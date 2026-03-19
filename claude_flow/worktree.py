from __future__ import annotations

import fcntl
import logging
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator, List, Optional, TypeVar

from .models import ManagedRepo
from .utils import can_skip_permissions

T = TypeVar("T")

if TYPE_CHECKING:
    from claude_flow.config import Config

logger = logging.getLogger(__name__)

# Default timeout for network operations (fetch/push) in seconds
NETWORK_TIMEOUT = 30


MERGE_LOCK_FILE = "merge.lock"


def _git_env() -> dict[str, str]:
    """Build environment dict for non-interactive git operations.

    Sets GIT_EDITOR and GIT_SEQUENCE_EDITOR to 'true' to prevent interactive
    editor prompts, and provides fallback author/committer identity.
    """
    env = os.environ.copy()
    env["GIT_EDITOR"] = "true"
    env["GIT_SEQUENCE_EDITOR"] = "true"
    env.setdefault("GIT_AUTHOR_NAME", "claude-flow")
    env.setdefault("GIT_AUTHOR_EMAIL", "claude-flow@localhost")
    env.setdefault("GIT_COMMITTER_NAME", "claude-flow")
    env.setdefault("GIT_COMMITTER_EMAIL", "claude-flow@localhost")
    return env


class WorktreeManager:
    def __init__(self, repo_root: Path, worktree_dir: Path, is_git: bool = True):
        self._repo = repo_root
        self._wt_dir = worktree_dir
        self._merge_lock_file = self._wt_dir / MERGE_LOCK_FILE
        self._is_git = is_git

    def _run(self, args: List[str], cwd: Path | None = None, check: bool = True,
             timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                args, cwd=cwd or self._repo,
                capture_output=True, text=True, check=check,
                timeout=timeout, env=_git_env(),
            )
        except subprocess.TimeoutExpired:
            cmd_str = " ".join(args)
            logger.warning(f"Command timed out after {timeout}s: {cmd_str}")
            # Return a failed CompletedProcess so callers with check=False still work
            return subprocess.CompletedProcess(args, returncode=124, stdout="", stderr=f"Timeout after {timeout}s")

    def _with_merge_lock(self, fn: Callable[[], T]) -> T:
        """在排他文件锁保护下执行 fn，防止多 Worker 同时 merge/rebase。

        使用 fcntl.flock 获取排他锁，与 TaskManager._with_lock 保持一致的模式。
        锁文件位于 worktree_dir/merge.lock。
        """
        self._merge_lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._merge_lock_file, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    # ------------------------------------------------------------------
    # Dirty worktree 保护
    # ------------------------------------------------------------------

    @contextmanager
    def _safe_checkout(self, branch: str) -> Iterator[None]:
        """保护主仓库中未提交的改动，防止 git checkout 失败。

        在 checkout 前 stash 脏文件，操作完成后自动 pop 恢复。
        解决多 Worker 并行时主仓库有未暂存修改导致 merge 失败的问题。
        """
        # 检测主仓库是否有脏文件（staged + unstaged + untracked）
        status = self._run(["git", "status", "--porcelain"], check=False)
        dirty = bool(status.stdout.strip())

        if dirty:
            stash_result = self._run(
                ["git", "stash", "push", "-u", "-m", f"cf-auto-stash-{branch}"],
                check=False,
            )
            stashed = (
                stash_result.returncode == 0
                and "No local changes" not in stash_result.stdout
            )
            if stashed:
                logger.debug(f"Stashed dirty working tree before checkout {branch}")
        else:
            stashed = False

        try:
            yield
        finally:
            if stashed:
                pop_result = self._run(["git", "stash", "pop"], check=False)
                if pop_result.returncode != 0:
                    logger.warning(
                        f"git stash pop failed after merge, "
                        f"stash preserved (run 'git stash pop' manually): "
                        f"{pop_result.stderr.strip()}"
                    )

    # ------------------------------------------------------------------
    # Symlink 共享文件
    # ------------------------------------------------------------------

    def _setup_symlinks(self, wt_path: Path, shared: List[str], forbidden: List[str]) -> None:
        """在 worktree 中为共享文件创建 symlink，指向主仓库对应文件。

        - shared 中列出的文件会被 symlink 到主仓库
        - forbidden 中列出的文件会被跳过
        - 主仓库中不存在的文件静默跳过，不报错
        - 同时在 worktree 中创建隔离的 data/ 目录
        """
        # 创建隔离的 data/ 目录
        data_dir = wt_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        for filename in shared:
            # 跳过 forbidden 列表中的文件
            if filename in forbidden:
                continue

            source = self._repo / filename
            target = wt_path / filename

            # 主仓库中不存在的文件静默跳过
            if not source.exists():
                continue

            # 如果目标已存在（可能是 worktree 拷贝的），先删除
            if target.exists() or target.is_symlink():
                target.unlink()

            # 确保目标文件的父目录存在
            target.parent.mkdir(parents=True, exist_ok=True)

            # 创建 symlink，指向主仓库的绝对路径
            target.symlink_to(source.resolve())

    # ------------------------------------------------------------------
    # Submodule 初始化
    # ------------------------------------------------------------------

    def _init_submodules(self, wt_path: Path, submodules: list[str],
                         task_id: str = "", sub_branches: dict[str, str] | None = None) -> None:
        """Initialize specified submodules in worktree and create named branches.

        Only initializes submodules listed in *submodules*, leaving others untouched.
        Leverages the main repo's .git/modules/ shared object store.

        After ``submodule update``, a named branch ``cf/{task_id}`` is created inside
        each submodule to avoid a detached HEAD.  If *sub_branches* maps a submodule
        path to a base ref, ``git fetch --all`` is run first and the new branch is
        based on that ref; otherwise the branch is based on the current HEAD.

        Uses ``-c protocol.file.allow=always`` to allow local file:// protocol clone
        (Git 2.38+ disables local file:// clone by default).
        """
        sub_branches = sub_branches or {}
        for sub_path in submodules:
            self._run(["git", "submodule", "init", sub_path], cwd=wt_path)
            self._run(
                ["git", "-c", "protocol.file.allow=always",
                 "submodule", "update", sub_path],
                cwd=wt_path,
            )
            # Create a named branch inside the submodule to avoid detached HEAD
            if task_id:
                sub_dir = wt_path / sub_path
                branch_name = f"cf/{task_id}"
                base = sub_branches.get(sub_path)
                if base:
                    # Fetch all remotes so the target branch ref is available
                    self._run(["git", "fetch", "--all"], cwd=sub_dir,
                              check=False, timeout=NETWORK_TIMEOUT)
                    self._run(
                        ["git", "checkout", "-b", branch_name, base],
                        cwd=sub_dir,
                    )
                else:
                    # Create branch based on current HEAD
                    self._run(
                        ["git", "checkout", "-b", branch_name],
                        cwd=sub_dir,
                    )

    # ------------------------------------------------------------------
    # 创建 worktree
    # ------------------------------------------------------------------

    def create(self, task_id: str, branch: str, config: Config = None,
               submodules: list[str] | None = None,
               sub_branches: dict[str, str] | None = None) -> Path:
        """Create a worktree and set up symlink-shared files.

        Non-git mode: returns the project root directly (no isolation).

        Args:
            task_id: Task identifier used for worktree directory and branch naming.
            branch: Git branch name to create (usually ``cf/{task_id}``).
            config: Optional config for symlink setup.
            submodules: List of submodule paths to initialize inside the worktree.
            sub_branches: Mapping of submodule path to base ref for the
                          ``cf/{task_id}`` branch created inside each submodule.
        """
        if not self._is_git:
            # Non-git mode: run directly in project root, no isolation
            return self._repo

        wt_path = self._wt_dir / task_id
        wt_path.parent.mkdir(parents=True, exist_ok=True)

        # Defensive cleanup: remove stale worktree/branch from a previous failed run
        if wt_path.exists():
            logger.warning(f"Worktree path {wt_path} already exists, removing stale worktree")
            self._run(["git", "worktree", "remove", str(wt_path), "--force"], check=False)
        # Check if branch already exists and delete it
        branch_check = self._run(["git", "branch", "--list", branch], check=False)
        if branch_check.stdout.strip():
            logger.warning(f"Branch {branch} already exists, deleting stale branch")
            self._run(["git", "branch", "-D", branch], check=False)

        self._run(["git", "worktree", "add", "-b", branch, str(wt_path)])

        # Set up symlinks if config is provided
        if config is not None:
            self._setup_symlinks(
                wt_path,
                shared=config.shared_symlinks,
                forbidden=config.forbidden_symlinks,
            )

        # Initialize specified submodules with named branches
        if submodules:
            self._init_submodules(wt_path, submodules, task_id=task_id,
                                  sub_branches=sub_branches)

        return wt_path

    # ------------------------------------------------------------------
    # Submodule merge and push
    # ------------------------------------------------------------------

    def merge_submodules(self, wt_path: Path, task: object) -> bool:
        """Merge the cf/{task.id} branch back to the target branch in each submodule.

        For submodules that have an entry in ``task.sub_branches``, the temporary
        branch is merged into the target via ``--no-ff`` and then deleted.
        Submodules without a ``sub_branches`` entry are skipped.

        Args:
            wt_path: Path to the worktree directory.
            task: Task object with ``id``, ``title``, ``submodules``, and
                  ``sub_branches`` attributes.

        Returns:
            True if all merges succeeded, False if any merge failed.
        """
        # Inherit user.email/user.name from the main worktree so merge commits
        # succeed even if the submodule has no local git config.
        user_email = self._run(
            ["git", "config", "user.email"], cwd=wt_path, check=False,
        ).stdout.strip()
        user_name = self._run(
            ["git", "config", "user.name"], cwd=wt_path, check=False,
        ).stdout.strip()

        for sub_path in task.submodules:
            sub_dir = wt_path / sub_path
            if not sub_dir.exists():
                continue
            target = task.sub_branches.get(sub_path)
            if not target:
                # No target branch specified -- skip internal merge
                continue
            branch_name = f"cf/{task.id}"
            try:
                self._run(["git", "checkout", target], cwd=sub_dir)
                # Build merge command with inherited user identity
                merge_cmd = ["git"]
                if user_email:
                    merge_cmd.extend(["-c", f"user.email={user_email}"])
                if user_name:
                    merge_cmd.extend(["-c", f"user.name={user_name}"])
                merge_cmd.extend([
                    "merge", "--no-ff", branch_name,
                    "-m", f"feat({task.id}): {task.title}",
                ])
                self._run(merge_cmd, cwd=sub_dir)
                # Clean up temporary branch
                self._run(["git", "branch", "-d", branch_name], cwd=sub_dir, check=False)
            except subprocess.CalledProcessError as e:
                logger.error(f"Submodule merge failed for {sub_path}: {e.stderr}")
                # Abort any in-progress merge
                self._run(["git", "merge", "--abort"], cwd=sub_dir, check=False)
                return False
        return True

    def push_submodules(self, wt_path: Path, task: object) -> None:
        """Push submodule changes to their respective remotes.

        Iterates over ``task.submodules`` and pushes the current branch of each
        submodule that has a configured remote.  Failures are logged but do not
        cause the overall operation to fail.

        Args:
            wt_path: Path to the worktree directory.
            task: Task object with ``submodules`` attribute.
        """
        for sub_path in task.submodules:
            sub_dir = wt_path / sub_path
            if not sub_dir.exists():
                continue
            if not self._has_remote(cwd=sub_dir):
                continue
            # Determine current branch
            result = self._run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=sub_dir, check=False,
            )
            branch = result.stdout.strip()
            if branch:
                push_result = self._run(
                    ["git", "push", "origin", branch],
                    cwd=sub_dir, check=False, timeout=NETWORK_TIMEOUT,
                )
                if push_result.returncode != 0:
                    logger.warning(f"Failed to push submodule {sub_path}: {push_result.stderr}")

    # ------------------------------------------------------------------
    # 移除 worktree
    # ------------------------------------------------------------------

    def remove(self, task_id: str, branch: str) -> None:
        if not self._is_git:
            return  # Non-git mode: nothing to remove
        wt_path = self._wt_dir / task_id
        self._run(["git", "worktree", "remove", str(wt_path), "--force"], check=False)
        self._run(["git", "branch", "-D", branch], check=False)

    # ------------------------------------------------------------------
    # 原有合并方法（向后兼容）
    # ------------------------------------------------------------------

    def merge(self, branch: str, main_branch: str, strategy: str = "--no-ff",
              config: Config = None,
              task_title: str = "", task_prompt: str = "") -> bool:
        if not self._is_git:
            return True  # Non-git mode: skip merge

        def _do() -> bool:
            with self._safe_checkout(branch):
                try:
                    self._run(["git", "checkout", main_branch])
                    self._run(["git", "merge", strategy, branch, "-m", f"merge {branch}"])
                    return True
                except subprocess.CalledProcessError:
                    # 尝试自动解决冲突
                    skip_ok = config is not None and can_skip_permissions(
                        getattr(config, "skip_permissions", False)
                    )
                    max_retries = getattr(config, "max_merge_retries", 3) if config else 3

                    if skip_ok:
                        for attempt in range(max_retries):
                            conflict_files = self._get_conflict_files(self._repo)
                            if not conflict_files:
                                break

                            logger.info(f"Merge conflict (attempt {attempt + 1}/{max_retries}), "
                                        f"files: {conflict_files}")

                            prompt = self._build_conflict_prompt(
                                conflict_files,
                                task_title=task_title,
                                task_prompt=task_prompt,
                                cwd=self._repo,
                            )
                            claude_cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions"]
                            claude_result = self._run(claude_cmd, check=False)

                            if claude_result.returncode != 0:
                                break

                            if self._has_conflict_markers(self._repo):
                                continue  # 重试

                            self._run(["git", "add", "-A"], check=False)

                            # 尝试完成 merge commit
                            commit_result = self._run(
                                ["git", "commit", "--no-edit"],
                                check=False,
                            )
                            if commit_result.returncode == 0:
                                return True

                    # Claude Code 兜底
                    if config is not None and getattr(config, "claude_merge_fallback", False):
                        logger.info(f"Attempting Claude Code merge fallback for {branch}")
                        self._run(["git", "merge", "--abort"], check=False)
                        self._run(["git", "checkout", main_branch], check=False)
                        fallback_ok = self._claude_code_merge_fallback(
                            branch, main_branch,
                            task_title=task_title, task_prompt=task_prompt,
                            timeout=getattr(config, "claude_merge_fallback_timeout", 300),
                            config=config,
                        )
                        if fallback_ok:
                            return True

                    # 解决失败，abort
                    self._run(["git", "merge", "--abort"], check=False)
                    self._run(["git", "checkout", main_branch], check=False)
                    return False
        return self._with_merge_lock(_do)

    # ------------------------------------------------------------------
    # Rebase 合并策略
    # ------------------------------------------------------------------

    def _has_remote(self, cwd: Path | None = None) -> bool:
        """Check whether a remote named 'origin' is configured.

        Args:
            cwd: Working directory for the git command.  Defaults to the repo root.
        """
        result = self._run(["git", "remote"], cwd=cwd, check=False)
        return "origin" in result.stdout.split()

    def _get_conflict_files(self, cwd: Path) -> List[str]:
        """获取当前冲突的文件列表。"""
        result = self._run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=cwd, check=False,
        )
        return [f for f in result.stdout.strip().splitlines() if f]

    def _has_conflict_markers(self, cwd: Path) -> bool:
        """检查工作区中是否还残留冲突标记。"""
        result = self._run(
            ["git", "diff", "--check"],
            cwd=cwd, check=False,
        )
        return result.returncode != 0

    def _build_conflict_prompt(self, conflict_files: List[str],
                               task_title: str = "", task_prompt: str = "",
                               cwd: Path | None = None) -> str:
        """构建包含任务上下文、冲突 diff 和提交历史的 prompt。

        与旧版仅包含文件名不同，新版包含每个冲突文件的完整 diff 输出
        和近期提交历史，给 Claude 充足的上下文来完成合并。
        """
        work_dir = cwd or self._repo

        parts = [
            "你正在解决一个 Git rebase 冲突。请仔细阅读以下上下文，完成冲突解决。",
        ]

        # 任务上下文（不截断，给完整信息）
        if task_title:
            parts.append(f"\n## 任务标题\n{task_title}")
        if task_prompt:
            parts.append(f"\n## 任务描述\n{task_prompt}")

        # 近期提交历史
        log_result = self._run(
            ["git", "log", "--oneline", "-10"],
            cwd=work_dir, check=False,
        )
        if log_result.stdout.strip():
            parts.append(f"\n## 近期提交历史\n```\n{log_result.stdout.strip()}\n```")

        # 冲突文件的 diff 详情
        parts.append("\n## 冲突文件详情")
        for f in conflict_files:
            parts.append(f"\n### {f}")
            diff_result = self._run(
                ["git", "diff", f],
                cwd=work_dir, check=False,
            )
            diff_text = diff_result.stdout.strip()
            # 限制单文件 diff 长度，防止 prompt 过大
            if len(diff_text) > 4000:
                diff_text = diff_text[:4000] + "\n... (diff truncated)"
            if diff_text:
                parts.append(f"```diff\n{diff_text}\n```")
            else:
                parts.append("(no diff output)")

        parts.append(
            "\n## 要求\n"
            "1. 读取每个冲突文件的完整内容\n"
            "2. 理解冲突双方的意图，结合任务描述判断应保留哪些改动\n"
            "3. 保留双方的有效改动，合理合并\n"
            "4. 删除所有冲突标记 (<<<<<<<, =======, >>>>>>>)\n"
            "5. 确保合并后的代码逻辑正确、语法正确、可运行\n"
            "6. 不要遗漏任何冲突文件"
        )
        return "\n".join(parts)

    def _claude_code_merge_fallback(
        self,
        branch: str,
        main_branch: str,
        task_title: str = "",
        task_prompt: str = "",
        timeout: int = 300,
        config: "Config | None" = None,
    ) -> bool:
        """Claude Code 全能力模式兜底合并。

        当所有常规合并策略失败后调用，让 Claude Code 自主读文件、执行命令、
        编辑代码完成合并。在主仓库 (_repo) 目录中执行。

        Returns:
            True if merge succeeded, False otherwise.
        """
        work_dir = self._repo

        # 收集诊断信息
        status_result = self._run(["git", "status"], cwd=work_dir, check=False)
        log_result = self._run(
            ["git", "log", "--oneline", "-5"], cwd=work_dir, check=False,
        )
        diff_stat = self._run(
            ["git", "diff", "--stat", f"{main_branch}..{branch}"],
            cwd=work_dir, check=False,
        )

        # 构建自主合并 prompt
        prompt_parts = [
            "你是一个 Git 合并专家。请自主完成以下分支的合并操作。",
            f"\n## 源分支: {branch}",
            f"## 目标分支: {main_branch}",
        ]
        if task_title:
            prompt_parts.append(f"\n## 任务标题\n{task_title}")
        if task_prompt:
            prompt_parts.append(f"\n## 任务描述\n{task_prompt}")

        prompt_parts.append(f"\n## 当前 git status\n```\n{status_result.stdout.strip()}\n```")
        if log_result.stdout.strip():
            prompt_parts.append(f"\n## 近期提交\n```\n{log_result.stdout.strip()}\n```")
        if diff_stat.stdout.strip():
            prompt_parts.append(f"\n## 分支差异概要\n```\n{diff_stat.stdout.strip()}\n```")

        prompt_parts.append(
            "\n## 操作步骤\n"
            f"1. 确认当前在目标分支上（如果不在，先 checkout）\n"
            f"2. 执行 `git merge {branch}`\n"
            "3. 如果有冲突，读取冲突文件内容，理解双方意图\n"
            "4. 编辑文件解决所有冲突，删除冲突标记 (<<<<<<<, =======, >>>>>>>)\n"
            "5. 确保合并后代码逻辑正确、语法正确\n"
            "6. `git add` 所有已解决的文件\n"
            "7. `git commit --no-edit` 完成合并\n"
            "\n## 约束\n"
            "- 保留双方的有效改动\n"
            "- 不要丢失任何功能代码\n"
            "- 确保没有残留的冲突标记\n"
            "- 操作完成后确保 `git status` 干净"
        )

        prompt = "\n".join(prompt_parts)

        # 构建 Claude 命令
        skip_ok = config is not None and can_skip_permissions(
            getattr(config, "skip_permissions", False)
        )
        if skip_ok:
            claude_cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions"]
        else:
            claude_cmd = [
                "claude", "-p", prompt,
                "--allowedTools",
                'Bash(git *)', 'Bash(cat *)', 'Read', 'Edit', 'Write', 'Grep', 'Glob',
            ]

        logger.info(f"Claude Code merge fallback: executing in {work_dir}")
        claude_result = self._run(
            claude_cmd, cwd=work_dir, check=False, timeout=timeout,
        )

        if claude_result.returncode != 0:
            logger.warning(
                f"Claude Code merge fallback failed (exit {claude_result.returncode}): "
                f"{claude_result.stderr.strip()[:200]}"
            )
            # 清理：abort 任何进行中的 merge/rebase
            self._run(["git", "merge", "--abort"], cwd=work_dir, check=False)
            self._run(["git", "rebase", "--abort"], cwd=work_dir, check=False)
            return False

        # 验证结果
        verify_status = self._run(["git", "status", "--porcelain"], cwd=work_dir, check=False)
        conflict_files = self._get_conflict_files(work_dir)
        has_markers = self._has_conflict_markers(work_dir)

        if conflict_files or has_markers:
            logger.warning(
                f"Claude Code merge fallback completed but conflicts remain: "
                f"files={conflict_files}, markers={has_markers}"
            )
            self._run(["git", "merge", "--abort"], cwd=work_dir, check=False)
            self._run(["git", "rebase", "--abort"], cwd=work_dir, check=False)
            return False

        # 如果还有未提交的改动（Claude 可能忘了 commit），尝试自动提交
        if verify_status.stdout.strip():
            self._run(["git", "add", "-A"], cwd=work_dir, check=False)
            commit_result = self._run(
                ["git", "commit", "--no-edit", "-m", f"merge {branch} (claude fallback)"],
                cwd=work_dir, check=False,
            )
            if commit_result.returncode != 0:
                logger.warning("Claude Code fallback: auto-commit after merge failed")
                self._run(["git", "merge", "--abort"], cwd=work_dir, check=False)
                return False

        logger.info(f"Claude Code merge fallback succeeded for {branch}")
        return True

    def rebase_and_merge(self, branch: str, main_branch: str, max_retries: int = 5,
                         config: Config = None,
                         task_title: str = "", task_prompt: str = "",
                         timeout: int = 0) -> bool:
        """使用 rebase 策略合并分支。

        流程：
        1. git fetch origin（如果有 remote）
        2. 在 worktree 中执行 git rebase origin/main（或 git rebase main）
        3. 成功后 checkout main，执行 git merge --ff-only（含重试降级）
        4. 冲突时使用 claude 解决冲突，然后 git rebase --continue
        5. 冲突解决后在 worktree 中执行 pre_merge_commands 验证
        6. 最多重试 max_retries 次
        7. 超时或全部失败则 git rebase --abort，返回 False
        """
        if not self._is_git:
            return True  # Non-git mode: skip rebase

        def _do() -> bool:
            start_time = time.time()
            has_remote = self._has_remote()

            # 确定 rebase 的目标
            rebase_target = f"origin/{main_branch}" if has_remote else main_branch

            # 找到该 branch 对应的 worktree 路径
            wt_path = self._find_worktree_path(branch)

            # 步骤 1: fetch（如果有 remote，设置超时防止网络挂起）
            if has_remote:
                self._run(["git", "fetch", "origin"], check=False, timeout=NETWORK_TIMEOUT)

            # 步骤 2: 在 worktree 中执行 rebase
            rebase_result = self._run(
                ["git", "rebase", rebase_target],
                cwd=wt_path, check=False,
            )

            if rebase_result.returncode == 0:
                # rebase 成功，执行 ff-only 合并（传入 wt_path 支持重试降级）
                return self._ff_merge(branch, main_branch, wt_path=wt_path,
                                      config=config, task_title=task_title,
                                      task_prompt=task_prompt)

            # 步骤 3: 冲突处理 — 最多重试 max_retries 次
            skip_ok = config is not None and can_skip_permissions(
                getattr(config, "skip_permissions", False)
            )

            for attempt in range(max_retries):
                # 超时保护：预留 20% 时间给清理操作
                if timeout > 0 and (time.time() - start_time) > timeout * 0.8:
                    logger.warning(f"Conflict resolution approaching timeout ({timeout}s), aborting")
                    break

                if not skip_ok:
                    logger.warning("Cannot auto-resolve conflicts: skip_permissions not available")
                    break

                # 获取冲突文件列表
                conflict_files = self._get_conflict_files(wt_path)
                if not conflict_files:
                    # 无冲突文件但 rebase 仍失败，尝试直接 continue
                    self._run(["git", "add", "-A"], cwd=wt_path, check=False)
                    continue_result = self._run(
                        ["git", "rebase", "--continue"],
                        cwd=wt_path, check=False,
                    )
                    if continue_result.returncode == 0:
                        return self._ff_merge(branch, main_branch, wt_path=wt_path,
                                              config=config, task_title=task_title,
                                              task_prompt=task_prompt)
                    break

                logger.info(f"Rebase conflict (attempt {attempt + 1}/{max_retries}), "
                            f"files: {conflict_files}")

                # 构建包含任务上下文和 diff 的冲突解决 prompt
                prompt = self._build_conflict_prompt(
                    conflict_files,
                    task_title=task_title,
                    task_prompt=task_prompt,
                    cwd=wt_path,
                )
                claude_cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions"]
                claude_result = self._run(
                    claude_cmd,
                    cwd=wt_path, check=False,
                )

                if claude_result.returncode != 0:
                    logger.warning(f"Claude conflict resolution failed (exit {claude_result.returncode})")
                    break

                # 验证冲突标记已清除
                if self._has_conflict_markers(wt_path):
                    logger.warning("Conflict markers still present after claude resolution")
                    continue  # 重试

                # 将所有文件标记为已解决
                self._run(["git", "add", "-A"], cwd=wt_path, check=False)

                # 尝试继续 rebase
                continue_result = self._run(
                    ["git", "rebase", "--continue"],
                    cwd=wt_path, check=False,
                )

                if continue_result.returncode == 0:
                    # rebase 成功，执行 ff-only 合并
                    return self._ff_merge(branch, main_branch, wt_path=wt_path,
                                          config=config, task_title=task_title,
                                          task_prompt=task_prompt)

            # Claude Code 兜底
            if config is not None and getattr(config, "claude_merge_fallback", False):
                logger.info(f"Attempting Claude Code rebase fallback for {branch}")
                self._run(["git", "rebase", "--abort"], cwd=wt_path, check=False)
                fallback_ok = self._claude_code_merge_fallback(
                    branch, main_branch,
                    task_title=task_title, task_prompt=task_prompt,
                    timeout=getattr(config, "claude_merge_fallback_timeout", 300),
                    config=config,
                )
                if fallback_ok:
                    return self._ff_merge(branch, main_branch, wt_path=wt_path,
                                          config=config, task_title=task_title,
                                          task_prompt=task_prompt)

            # 全部失败，abort rebase
            self._run(["git", "rebase", "--abort"], cwd=wt_path, check=False)
            return False

        return self._with_merge_lock(_do)

    def _find_worktree_path(self, branch: str) -> Path:
        """根据分支名找到对应的 worktree 路径。

        分支名格式通常为 cf/{task_id}，worktree 路径为 _wt_dir/{task_id}。
        如果找不到对应目录，回退到主仓库路径。
        """
        # 从分支名提取 task_id（分支格式: cf/task-xxxxxx）
        if branch.startswith("cf/"):
            task_id = branch[3:]
            wt_path = self._wt_dir / task_id
            if wt_path.exists():
                return wt_path

        # 回退：遍历 worktree 列表查找
        result = self._run(["git", "worktree", "list", "--porcelain"], check=False)
        path: Path | None = None
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                path = Path(line.split(" ", 1)[1])
            elif line.startswith("branch ") and line.endswith(f"/{branch}") and path is not None:
                return path

        # 最终回退到主仓库
        return self._repo

    def _ff_merge(self, branch: str, main_branch: str,
                  wt_path: Path | None = None,
                  config: "Config | None" = None,
                  task_title: str = "", task_prompt: str = "") -> bool:
        """checkout 主分支并执行 fast-forward 合并。

        如果 ff-only 失败（main 已有新提交），尝试在 worktree 中重新 rebase
        后再次 ff-only。若仍失败，降级到 --no-ff merge 保证不丢代码。

        使用 _safe_checkout 保护主仓库中未提交的脏文件，防止 checkout 失败。
        """
        with self._safe_checkout(branch):
            try:
                self._run(["git", "checkout", main_branch])
                self._run(["git", "merge", "--ff-only", branch])
                return True
            except subprocess.CalledProcessError:
                pass

            # ff-only 失败：main 在 rebase 期间被其他 worker 修改
            if wt_path is not None:
                logger.info(f"ff-only merge failed for {branch}, attempting re-rebase")
                # 重新 fetch + rebase
                has_remote = self._has_remote()
                rebase_target = f"origin/{main_branch}" if has_remote else main_branch
                if has_remote:
                    self._run(["git", "fetch", "origin"], check=False, timeout=NETWORK_TIMEOUT)
                re_rebase = self._run(
                    ["git", "rebase", rebase_target], cwd=wt_path, check=False,
                )
                if re_rebase.returncode == 0:
                    try:
                        self._run(["git", "checkout", main_branch])
                        self._run(["git", "merge", "--ff-only", branch])
                        return True
                    except subprocess.CalledProcessError:
                        pass
                else:
                    # re-rebase 也冲突，abort
                    self._run(["git", "rebase", "--abort"], cwd=wt_path, check=False)

            # 最终降级：--no-ff merge（生成 merge commit，但保证不丢代码）
            logger.warning(f"ff-only merge failed for {branch}, falling back to --no-ff")
            try:
                self._run(["git", "checkout", main_branch])
                self._run(["git", "merge", "--no-ff", branch, "-m", f"merge {branch}"])
                return True
            except subprocess.CalledProcessError:
                # Claude Code 兜底
                if config is not None and getattr(config, "claude_merge_fallback", False):
                    self._run(["git", "merge", "--abort"], check=False)
                    fallback_ok = self._claude_code_merge_fallback(
                        branch, main_branch,
                        task_title=task_title, task_prompt=task_prompt,
                        timeout=getattr(config, "claude_merge_fallback_timeout", 300),
                        config=config,
                    )
                    if fallback_ok:
                        return True
                self._run(["git", "merge", "--abort"], check=False)
                self._run(["git", "checkout", main_branch], check=False)
                return False

    # ------------------------------------------------------------------
    # 远程推送支持
    # ------------------------------------------------------------------

    def push(self, main_branch: str) -> bool:
        """将主分支推送到远程仓库。"""
        if not self._is_git:
            return False  # Non-git mode: cannot push
        result = self._run(["git", "push", "origin", main_branch], check=False, timeout=NETWORK_TIMEOUT)
        return result.returncode == 0

    # ------------------------------------------------------------------
    # 列表与清理
    # ------------------------------------------------------------------

    def list_active(self) -> List[str]:
        if not self._wt_dir.exists():
            return []
        return [d.name for d in self._wt_dir.iterdir() if d.is_dir()]

    def cleanup_all(self) -> int:
        if not self._is_git:
            return 0  # Non-git mode: nothing to clean up
        count = 0
        for task_id in self.list_active():
            branch = f"cf/{task_id}"
            self.remove(task_id, branch)
            count += 1
        return count


class MultiRepoWorktreeManager:
    """Manage worktrees across multiple independent git repositories.

    Used when the workspace is a non-git parent directory containing multiple
    independent git repos.  Each task gets a "composite" directory under
    ``.claude-flow/worktrees/{task_id}/`` with one git worktree per repo.
    """

    def __init__(self, workspace_root: Path, composite_dir: Path,
                 managed_repos: list[ManagedRepo]):
        self._workspace = workspace_root
        self._composite_dir = composite_dir  # .claude-flow/worktrees
        self._repos = {r.path: r for r in managed_repos}
        self._merge_lock_file = composite_dir / MERGE_LOCK_FILE

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, args: List[str], cwd: Path | None = None, check: bool = True,
             timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        """Run a subprocess command with standard git environment."""
        try:
            return subprocess.run(
                args, cwd=cwd or self._workspace,
                capture_output=True, text=True, check=check,
                timeout=timeout, env=_git_env(),
            )
        except subprocess.TimeoutExpired:
            cmd_str = " ".join(args)
            logger.warning(f"Command timed out after {timeout}s: {cmd_str}")
            return subprocess.CompletedProcess(args, returncode=124, stdout="", stderr=f"Timeout after {timeout}s")

    def _with_merge_lock(self, fn: Callable[[], T]) -> T:
        """Execute *fn* under an exclusive file lock to prevent concurrent merges."""
        self._merge_lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._merge_lock_file, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _repo_dir(self, repo_path: str) -> Path:
        """Absolute path to the original git repository."""
        return self._workspace / repo_path

    # ------------------------------------------------------------------
    # Composite worktree lifecycle
    # ------------------------------------------------------------------

    def create_composite(self, task_id: str, repo_branches: dict[str, str]) -> Path:
        """Create a composite working directory with one worktree per repo.

        Args:
            task_id: Task identifier (e.g. "task-a1b2c3").
            repo_branches: Mapping of repo path to the base branch for
                           the new ``cf/{task_id}`` worktree branch.

        Returns:
            Path to the composite directory.
        """
        composite_path = self._composite_dir / task_id
        composite_path.mkdir(parents=True, exist_ok=True)

        created_repos: list[str] = []  # Track successfully created worktrees
        try:
            for repo_path, base_branch in repo_branches.items():
                src_repo = self._repo_dir(repo_path)
                wt_dest = composite_path / repo_path
                branch_name = f"cf/{task_id}"

                self._run(
                    ["git", "-C", str(src_repo), "worktree", "add",
                     str(wt_dest), "-b", branch_name, base_branch],
                )
                created_repos.append(repo_path)
                logger.info(f"Created worktree for {repo_path} at {wt_dest} "
                            f"(branch {branch_name} from {base_branch})")
        except Exception:
            # Rollback: clean up already created worktrees
            for repo_path in created_repos:
                try:
                    wt_path = composite_path / repo_path
                    repo_dir = self._repo_dir(repo_path)
                    self._run(["git", "worktree", "remove", str(wt_path), "--force"],
                              cwd=repo_dir, check=False)
                    self._run(["git", "branch", "-D", f"cf/{task_id}"],
                              cwd=repo_dir, check=False)
                except Exception:
                    pass  # best effort cleanup
            # Clean up composite directory
            import shutil
            if composite_path.exists():
                shutil.rmtree(composite_path, ignore_errors=True)
            raise  # re-raise original exception

        return composite_path

    def commit_repos(self, task_id: str, composite_path: Path,
                     repos: list[str]) -> dict[str, bool]:
        """Stage and commit changes in each repo sub-directory.

        Args:
            task_id: Task identifier for the commit message.
            composite_path: Path to the composite directory.
            repos: List of repo paths to process.

        Returns:
            Mapping of repo path to whether it had changes that were committed.
        """
        results: dict[str, bool] = {}

        for repo_path in repos:
            sub_dir = composite_path / repo_path
            if not sub_dir.exists():
                logger.warning(f"Repo directory not found: {sub_dir}")
                results[repo_path] = False
                continue

            status = self._run(
                ["git", "status", "--porcelain"],
                cwd=sub_dir, check=False,
            )
            has_changes = bool(status.stdout.strip())

            if has_changes:
                self._run(["git", "add", "-A"], cwd=sub_dir, check=False)
                commit_result = self._run(
                    ["git", "commit", "-m", f"feat(cf/{task_id}): auto-commit changes"],
                    cwd=sub_dir, check=False,
                )
                if commit_result.returncode != 0:
                    logger.warning(f"Commit failed for {repo_path}: {commit_result.stderr}")
                    results[repo_path] = False
                else:
                    logger.info(f"Committed changes in {repo_path}")
                    results[repo_path] = True
            else:
                logger.debug(f"No changes in {repo_path}")
                results[repo_path] = False

        return results

    def merge_repos(self, task_id: str, repo_merge_targets: dict[str, str],
                    repo_configs: dict[str, ManagedRepo] | None = None) -> dict[str, bool]:
        """Merge task branches back into target branches in each original repo.

        Executed under a file lock to prevent concurrent merge operations.

        Args:
            task_id: Task identifier.
            repo_merge_targets: Mapping of repo path to target branch for merge.
            repo_configs: Optional per-repo config overrides.  Falls back to
                          the instance's ``_repos`` mapping.

        Returns:
            Mapping of repo path to merge success status.
        """
        def _do() -> dict[str, bool]:
            results: dict[str, bool] = {}
            configs = repo_configs or self._repos

            for repo_path, target_branch in repo_merge_targets.items():
                repo_dir = self._repo_dir(repo_path)
                branch_name = f"cf/{task_id}"
                repo_cfg = configs.get(repo_path)
                merge_mode = repo_cfg.merge_mode if repo_cfg else "rebase"

                try:
                    # Checkout the target branch in the original repo
                    self._run(["git", "checkout", target_branch], cwd=repo_dir)

                    if merge_mode == "rebase":
                        # Rebase the task branch onto target, then ff-only merge
                        composite_repo = self._composite_dir / task_id / repo_path
                        if composite_repo.exists():
                            rebase_result = self._run(
                                ["git", "rebase", target_branch],
                                cwd=composite_repo, check=False,
                            )
                            if rebase_result.returncode != 0:
                                logger.error(
                                    f"Rebase failed for {repo_path}: {rebase_result.stderr}")
                                self._run(
                                    ["git", "rebase", "--abort"],
                                    cwd=composite_repo, check=False,
                                )
                                results[repo_path] = False
                                continue

                        merge_result = self._run(
                            ["git", "merge", "--ff-only", branch_name],
                            cwd=repo_dir, check=False,
                        )
                        if merge_result.returncode != 0:
                            # Fallback to --no-ff if ff-only fails
                            logger.warning(
                                f"ff-only merge failed for {repo_path}, "
                                f"falling back to --no-ff")
                            merge_result = self._run(
                                ["git", "merge", "--no-ff", branch_name,
                                 "-m", f"feat(cf/{task_id}): merge {repo_path}"],
                                cwd=repo_dir, check=False,
                            )
                    else:
                        # Standard merge (--no-ff or custom strategy)
                        strategy = (repo_cfg.merge_strategy
                                    if repo_cfg else "--no-ff")
                        merge_result = self._run(
                            ["git", "merge", strategy, branch_name,
                             "-m", f"feat(cf/{task_id}): merge {repo_path}"],
                            cwd=repo_dir, check=False,
                        )

                    if merge_result.returncode != 0:
                        logger.error(
                            f"Merge failed for {repo_path}: {merge_result.stderr}")
                        self._run(
                            ["git", "merge", "--abort"],
                            cwd=repo_dir, check=False,
                        )
                        results[repo_path] = False
                    else:
                        # Clean up the task branch
                        self._run(
                            ["git", "branch", "-D", branch_name],
                            cwd=repo_dir, check=False,
                        )
                        logger.info(f"Merged {branch_name} into {target_branch} "
                                    f"for {repo_path}")
                        results[repo_path] = True

                except subprocess.CalledProcessError as e:
                    logger.error(f"Merge operation failed for {repo_path}: {e.stderr}")
                    self._run(
                        ["git", "merge", "--abort"],
                        cwd=repo_dir, check=False,
                    )
                    results[repo_path] = False

            return results

        return self._with_merge_lock(_do)

    def remove_composite(self, task_id: str, repos: list[str]) -> None:
        """Remove worktrees and clean up a composite directory.

        Args:
            task_id: Task identifier.
            repos: List of repo paths whose worktrees should be removed.
        """
        composite_path = self._composite_dir / task_id
        branch_name = f"cf/{task_id}"

        for repo_path in repos:
            src_repo = self._repo_dir(repo_path)
            wt_dest = composite_path / repo_path

            # Remove the git worktree
            self._run(
                ["git", "-C", str(src_repo), "worktree", "remove",
                 str(wt_dest), "--force"],
                check=False,
            )
            # Delete the task branch
            self._run(
                ["git", "-C", str(src_repo), "branch", "-D", branch_name],
                check=False,
            )
            logger.debug(f"Removed worktree and branch for {repo_path}")

        # Remove the composite directory if it still exists
        if composite_path.exists():
            import shutil
            shutil.rmtree(composite_path, ignore_errors=True)
            logger.info(f"Removed composite directory {composite_path}")

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def list_active(self) -> list[str]:
        """List task IDs that have active composite directories."""
        if not self._composite_dir.exists():
            return []
        return [d.name for d in self._composite_dir.iterdir() if d.is_dir()]

    def get_repo_branches(self, repo_path: str) -> list[str]:
        """Get the list of local branch names for a repository.

        Args:
            repo_path: Relative path of the repository from workspace root.

        Returns:
            List of branch names (without leading whitespace or ``*`` marker).
        """
        repo_dir = self._repo_dir(repo_path)
        result = self._run(
            ["git", "branch", "--format=%(refname:short)"],
            cwd=repo_dir, check=False,
        )
        if result.returncode != 0:
            return []
        return [b.strip() for b in result.stdout.strip().splitlines() if b.strip()]

    def get_repo_worktrees(self, repo_path: str) -> list[dict]:
        """Get the list of worktrees for a repository.

        Args:
            repo_path: Relative path of the repository from workspace root.

        Returns:
            List of dicts with keys ``path``, ``branch``, ``head``.
        """
        repo_dir = self._repo_dir(repo_path)
        result = self._run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_dir, check=False,
        )
        if result.returncode != 0:
            return []

        worktrees: list[dict] = []
        current: dict = {}

        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line.split(" ", 1)[1]}
            elif line.startswith("HEAD "):
                current["head"] = line.split(" ", 1)[1]
            elif line.startswith("branch "):
                ref = line.split(" ", 1)[1]
                # Strip refs/heads/ prefix
                if ref.startswith("refs/heads/"):
                    ref = ref[len("refs/heads/"):]
                current["branch"] = ref

        if current:
            worktrees.append(current)

        return worktrees

    def get_repo_status(self, repo_path: str) -> dict:
        """Get the current status of a repository.

        Args:
            repo_path: Relative path of the repository from workspace root.

        Returns:
            Dict with keys ``current_branch``, ``has_changes``, ``remote_url``.
        """
        repo_dir = self._repo_dir(repo_path)

        # Current branch
        branch_result = self._run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_dir, check=False,
        )
        current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""

        # Has changes
        status_result = self._run(
            ["git", "status", "--porcelain"],
            cwd=repo_dir, check=False,
        )
        has_changes = bool(status_result.stdout.strip())

        # Remote URL
        remote_result = self._run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_dir, check=False,
        )
        remote_url = remote_result.stdout.strip() if remote_result.returncode == 0 else ""

        return {
            "current_branch": current_branch,
            "has_changes": has_changes,
            "remote_url": remote_url,
        }

    # ------------------------------------------------------------------
    # Push
    # ------------------------------------------------------------------

    def push_repos(self, task_id: str, repos: list[str]) -> dict[str, bool]:
        """Push merged branches to remote for repos with auto_push enabled.

        Args:
            task_id: Task identifier (for logging).
            repos: List of repo paths to consider.

        Returns:
            Mapping of repo path to push success.  Repos without auto_push
            are not included.
        """
        results: dict[str, bool] = {}

        for repo_path in repos:
            repo_cfg = self._repos.get(repo_path)
            if not repo_cfg or not repo_cfg.auto_push:
                continue

            repo_dir = self._repo_dir(repo_path)

            # Check if remote exists
            remote_result = self._run(
                ["git", "remote"], cwd=repo_dir, check=False,
            )
            if "origin" not in remote_result.stdout.split():
                logger.debug(f"No remote 'origin' for {repo_path}, skipping push")
                results[repo_path] = False
                continue

            # Get current branch to push
            branch_result = self._run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=repo_dir, check=False,
            )
            branch = branch_result.stdout.strip()
            if not branch:
                results[repo_path] = False
                continue

            push_result = self._run(
                ["git", "push", "origin", branch],
                cwd=repo_dir, check=False, timeout=NETWORK_TIMEOUT,
            )
            if push_result.returncode != 0:
                logger.warning(f"Push failed for {repo_path}: {push_result.stderr}")
                results[repo_path] = False
            else:
                logger.info(f"Pushed {branch} for {repo_path}")
                results[repo_path] = True

        return results

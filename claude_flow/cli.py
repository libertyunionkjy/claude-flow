from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import click

from .chat import ChatManager
from .config import Config
from .models import TaskStatus, TaskType
from .planner import Planner
from .task_manager import TaskManager
from .worker import Worker
from .utils import is_git_repo
from .worktree import WorktreeManager


# -- Terminal helpers -------------------------------------------------------

def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences to prevent terminal state corruption."""
    return re.sub(r"\x1b\[[0-9;?]*[a-zA-Z~]|\x1b\].*?\x07|\x1b[^[\]]", "", text)


def _reset_terminal() -> None:
    """Reset terminal to canonical mode (cooked mode) for interactive input.

    Restores ICANON, ECHO, and ISIG flags so that Enter, Ctrl+C, etc. work
    correctly after a subprocess may have altered terminal settings.
    """
    if not sys.stdin.isatty():
        return
    try:
        import termios

        fd = sys.stdin.fileno()
        attrs = termios.tcgetattr(fd)
        # Restore local flags: canonical mode, echo, signal processing
        attrs[3] |= termios.ICANON | termios.ECHO | termios.ISIG
        # Restore input flags: CR-to-NL translation
        attrs[0] |= termios.ICRNL
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
    except (ImportError, termios.error, ValueError, OSError):
        pass


def _worker_entry(args: tuple) -> int:
    """Module-level worker entry point (must be picklable for multiprocessing)."""
    wid, root, worktree_dir, cfg_dict, daemon, is_git = args
    cfg = Config(**cfg_dict)
    tm = TaskManager(root)
    wt = WorktreeManager(root, root / worktree_dir, is_git=is_git)
    w = Worker(wid, root, tm, wt, cfg, is_git=is_git)
    if daemon:
        return w.run_daemon()
    return w.run_loop()


def _get_root() -> Path:
    env_root = os.environ.get("CF_PROJECT_ROOT")
    if env_root:
        return Path(env_root)
    # walk up to find .claude-flow or .git
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".claude-flow").exists() or (parent / ".git").exists():
            return parent
    return cwd


@click.group()
@click.pass_context
def main(ctx):
    """Claude Flow - Multi-instance Claude Code workflow manager."""
    ctx.ensure_object(dict)
    root = _get_root()
    ctx.obj["root"] = root
    ctx.obj["is_git"] = is_git_repo(root)


_SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
})


def _parse_gitmodules(root: Path) -> set[str]:
    """Parse .gitmodules and return set of existing submodule paths."""
    gitmodules = root / ".gitmodules"
    if not gitmodules.exists():
        return set()
    paths: set[str] = set()
    for line in gitmodules.read_text().splitlines():
        line = line.strip()
        if line.startswith("path"):
            _, _, value = line.partition("=")
            value = value.strip()
            if value:
                paths.add(value)
    return paths


def _git_current_branch(repo: Path) -> str:
    """Return the current branch name of a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo), capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _git_commit_count(repo: Path) -> int:
    """Return the total number of commits in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(repo), capture_output=True, text=True, check=True,
        )
        return int(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return 0


def _discover_git_repos(root: Path) -> list[dict]:
    """Recursively scan subdirectories and return info about all git repos found.

    Stops recursion when a ``.git`` directory is encountered (one repo per tree).
    Skips hidden directories and common non-code directories.
    Excludes directories that are already registered as submodules.
    """
    repos: list[dict] = []
    existing_submodules = _parse_gitmodules(root)

    def _walk(directory: Path, rel_prefix: str = ""):
        try:
            children = sorted(directory.iterdir())
        except PermissionError:
            return
        for child in children:
            if not child.is_dir():
                continue
            name = child.name
            if name.startswith(".") or name in _SKIP_DIRS:
                continue
            rel_path = name if not rel_prefix else f"{rel_prefix}/{name}"
            if (child / ".git").exists():
                # Found a git repo -- do not recurse into it
                if rel_path not in existing_submodules:
                    branch = _git_current_branch(child)
                    commit_count = _git_commit_count(child)
                    repos.append({
                        "path": rel_path,
                        "branch": branch,
                        "commits": commit_count,
                    })
            else:
                # Not a git repo -- continue recursion
                _walk(child, rel_path)

    _walk(root)
    return repos


def _interactive_select(repos: list[dict]) -> list[str]:
    """Display an interactive list and return selected relative paths."""
    click.echo(f"\nFound {len(repos)} git repositories:\n")
    max_path_len = max(len(r["path"]) for r in repos) + 2
    for i, r in enumerate(repos, 1):
        path_col = f"{r['path']}/".ljust(max_path_len)
        click.echo(f"  [{i}] {path_col} ({r['branch']}, "
                   f"{r['commits']} commits)")
    click.echo()
    raw = click.prompt(
        "Select repos to adopt as submodules (comma-separated, or 'all')",
        default="all",
    )
    if raw.strip().lower() == "all":
        return [r["path"] for r in repos]
    try:
        indices = [int(x.strip()) for x in raw.split(",")]
    except ValueError:
        click.echo("Invalid input, no repos selected.")
        return []
    return [repos[i - 1]["path"] for i in indices if 1 <= i <= len(repos)]


@main.command()
@click.option("--adopt", is_flag=True, default=False,
              help="Adopt existing git repos as submodules")
@click.option("--all", "adopt_all", is_flag=True, default=False,
              help="With --adopt: add all detected repos without prompting")
@click.argument("repos", nargs=-1)
@click.pass_context
def init(ctx, adopt, adopt_all, repos):
    """Initialize .claude-flow/ in the current project.

    With --adopt: scan for git repos in subdirectories and add them
    as submodules. Without arguments, shows an interactive picker.
    With explicit repo names, adds only those. With --all, adds
    everything found.
    """
    root = ctx.obj["root"]
    is_git = ctx.obj["is_git"]

    # Handle --adopt mode: set up git repo and add submodules
    if adopt:
        if not is_git:
            subprocess.run(["git", "init"], cwd=str(root), capture_output=True,
                           text=True, check=True)
            is_git = True
            ctx.obj["is_git"] = True

        if repos:
            selected = list(repos)
        elif adopt_all:
            discovered = _discover_git_repos(root)
            if not discovered:
                click.echo("No git repositories found in subdirectories.")
            selected = [r["path"] for r in discovered]
        else:
            discovered = _discover_git_repos(root)
            if not discovered:
                click.echo("No git repositories found in subdirectories.")
                selected = []
            else:
                selected = _interactive_select(discovered)

        if selected:
            click.echo(f"\nAdopting {len(selected)} repositories as submodules...")
            for rel_path in selected:
                result = subprocess.run(
                    ["git", "-c", "protocol.file.allow=always",
                     "submodule", "add", f"./{rel_path}", rel_path],
                    cwd=str(root), capture_output=True, text=True,
                )
                if result.returncode == 0:
                    click.echo(f"  + {rel_path}/ added as submodule")
                else:
                    stderr = result.stderr.strip()
                    click.echo(f"  ! {rel_path}/ failed: {stderr}")

    # Initialize .claude-flow/ directory structure
    cf_dir = root / ".claude-flow"
    for sub in ["logs", "plans", "worktrees", "chats"]:
        (cf_dir / sub).mkdir(parents=True, exist_ok=True)
    cfg = Config()
    cfg.save(root)
    # Add .claude-flow/worktrees, lock, log, chats to .gitignore (git repos only)
    if is_git:
        gitignore = root / ".gitignore"
        ignore_lines = [".claude-flow/worktrees/", ".claude-flow/tasks.lock", ".claude-flow/logs/", ".claude-flow/chats/"]
        existing = gitignore.read_text() if gitignore.exists() else ""
        to_add = [l for l in ignore_lines if l not in existing]
        if to_add:
            with open(gitignore, "a") as f:
                f.write("\n# claude-flow\n" + "\n".join(to_add) + "\n")
    mode_label = "git" if is_git else "non-git"
    click.echo(f"Initialized .claude-flow/ in {root} ({mode_label} mode)")


# -- Task commands ----------------------------------------------------------

@main.group()
def task():
    """Manage tasks."""
    pass


def _parse_sub_branches(raw: tuple[str, ...], cfg: Config) -> dict[str, str]:
    """Parse --sub-branch values and merge with config defaults.

    Each raw value has the format ``submodule_path:branch``.  Explicitly
    provided values take priority over ``config.default_sub_branches``.
    """
    merged: dict[str, str] = dict(cfg.default_sub_branches)
    for item in raw:
        if ":" not in item:
            raise click.BadParameter(
                f"Invalid format '{item}', expected 'submodule_path:branch'",
                param_hint="'--sub-branch'",
            )
        path, branch = item.split(":", 1)
        merged[path.strip()] = branch.strip()
    return merged


@task.command("add")
@click.argument("title")
@click.option("-p", "--prompt", default=None, help="Task prompt for Claude Code")
@click.option("-f", "--file", "filepath", default=None, type=click.Path(exists=True), help="Import tasks from file")
@click.option("-P", "--priority", default=0, type=int, help="Task priority (higher = more important)")
@click.option("-s", "--submodule", "submodules", multiple=True, help="Target submodule path (repeatable)")
@click.option("--sub-branch", "-sb", "sub_branches_raw", multiple=True,
              help="submodule_path:branch (e.g. libs/core:feature-auth)")
@click.option("--subagent/--no-subagent", default=None, help="Enable subagent mode for this task")
@click.pass_context
def task_add(ctx, title, prompt, filepath, priority, submodules, sub_branches_raw, subagent):
    """Add a new task."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    if filepath:
        added = tm.add_from_file(Path(filepath))
        click.echo(f"Added {len(added)} tasks")
        return
    if prompt is None:
        prompt = click.edit("# Enter the task prompt for Claude Code\n")
        if not prompt:
            click.echo("Aborted: no prompt provided")
            return
    cfg = Config.load(root)
    sub_branches = _parse_sub_branches(sub_branches_raw, cfg) if sub_branches_raw else dict(cfg.default_sub_branches)
    t = tm.add(title, prompt, priority=priority, submodules=list(submodules),
               use_subagent=subagent, sub_branches=sub_branches)
    click.echo(f"Added: {t.id} - {t.title} (priority: {priority})")


@task.command("mini")
@click.argument("prompt")
@click.option("-t", "--title", default=None, help="Task title (defaults to truncated prompt)")
@click.option("-P", "--priority", default=0, type=int, help="Task priority (higher = more important)")
@click.option("-s", "--submodule", "submodules", multiple=True, help="Target submodule path (repeatable)")
@click.option("--sub-branch", "-sb", "sub_branches_raw", multiple=True,
              help="submodule_path:branch (e.g. libs/core:feature-auth)")
@click.option("--run", "auto_run", is_flag=True, help="Immediately start a worker to execute")
@click.pass_context
def task_mini(ctx, prompt, title, priority, submodules, sub_branches_raw, auto_run):
    """Add a mini task (skips planning/approval, executes directly).

    Mini tasks are lightweight tasks that bypass the full planning cycle.
    They go directly to APPROVED status and can be executed immediately.
    Ideal for small requests, running scripts, or quick code changes.

    Examples:
        cf task mini "run pytest and fix any failures"
        cf task mini "update the version number to 2.0.0" --run
        cf task mini -t "Fix typo" "fix the typo in README.md line 42"
    """
    root = ctx.obj["root"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    if title is None:
        title = prompt[:60] + ("..." if len(prompt) > 60 else "")
    sub_branches = _parse_sub_branches(sub_branches_raw, cfg) if sub_branches_raw else dict(cfg.default_sub_branches)
    t = tm.add_mini(title, prompt, priority=priority, submodules=list(submodules),
                    sub_branches=sub_branches)
    click.echo(f"Mini task added: {t.id} - {t.title} [approved]")

    if auto_run:
        wt = WorktreeManager(root, root / cfg.worktree_dir)
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        worker = Worker(0, root, tm, wt, cfg)
        claimed = tm.claim_next(0)
        if claimed:
            click.echo(f"Executing mini task {claimed.id}...")
            success = worker.execute_task(claimed)
            if success:
                click.echo(f"Mini task {claimed.id} completed successfully")
            else:
                click.echo(f"Mini task {claimed.id} failed")
        else:
            click.echo("Failed to claim the task (it may have been picked up by another worker)")


@task.command("list")
@click.pass_context
def task_list(ctx):
    """List all tasks."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    tasks = tm.list_tasks()
    if not tasks:
        click.echo("No tasks")
        return
    # 按优先级降序排序显示
    tasks.sort(key=lambda t: t.priority, reverse=True)
    for t in tasks:
        status_icon = {"pending": "○", "planning": "⟳", "planned": "◉", "approved": "✓",
                       "running": "▶", "merging": "⇄", "done": "●", "failed": "✗"}
        icon = status_icon.get(t.status.value, "?")
        pri = f"P{t.priority}" if t.priority > 0 else ""
        tag = "[mini] " if t.is_mini else ""
        if t.use_subagent:
            tag += "[S] "
        click.echo(f"  {icon} {t.id}  {t.status.value:<10}  {pri:>3}  {tag}{t.title}")


@task.command("show")
@click.argument("task_id")
@click.pass_context
def task_show(ctx, task_id):
    """Show task details."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    t = tm.get(task_id)
    if not t:
        click.echo(f"Task {task_id} not found")
        return
    click.echo(f"ID:       {t.id}")
    click.echo(f"Title:    {t.title}")
    click.echo(f"Status:   {t.status.value}")
    click.echo(f"Priority: {t.priority}")
    click.echo(f"Branch:   {t.branch or '-'}")
    click.echo(f"Worker:   {t.worker_id or '-'}")
    if t.submodules:
        click.echo(f"Submodules: {', '.join(t.submodules)}")
    if t.sub_branches:
        for sp, br in t.sub_branches.items():
            click.echo(f"  {sp} -> {br}")
    click.echo(f"Created:  {t.created_at}")
    if t.progress:
        click.echo(f"Progress: {t.progress}")
    if t.error:
        click.echo(f"Error:    {t.error}")
    click.echo(f"\nPrompt:\n{t.prompt}")


@task.command("remove")
@click.argument("task_id")
@click.pass_context
def task_remove(ctx, task_id):
    """Remove a task and clean up associated worktree, branch, plan, log and chat files."""
    root = ctx.obj["root"]
    is_git = ctx.obj["is_git"]
    tm = TaskManager(root)
    removed = tm.remove(task_id)
    if not removed:
        click.echo(f"Task {task_id} not found")
        return

    click.echo(f"Removed {task_id}")

    # Clean up worktree and branch
    if is_git:
        branch = removed.branch or f"cf/{task_id}"
        cfg = Config.load(root)
        wt = WorktreeManager(root, root / cfg.worktree_dir, is_git=is_git)
        wt.remove(task_id, branch)
        click.echo(f"  Cleaned worktree and branch: {branch}")

    # Clean up plan file
    if removed.plan_file:
        plan_path = Path(removed.plan_file)
        if plan_path.exists():
            plan_path.unlink()
            click.echo(f"  Cleaned plan: {plan_path.name}")
    else:
        # Try default plan path
        plan_path = root / ".claude-flow" / "plans" / f"{task_id}.md"
        if plan_path.exists():
            plan_path.unlink()
            click.echo(f"  Cleaned plan: {plan_path.name}")

    # Clean up log files
    logs_dir = root / ".claude-flow" / "logs"
    for ext in (".log", ".json"):
        log_file = logs_dir / f"{task_id}{ext}"
        if log_file.exists():
            log_file.unlink()
            click.echo(f"  Cleaned log: {log_file.name}")

    # Clean up chat session
    chat_file = root / ".claude-flow" / "chats" / f"{task_id}.json"
    if chat_file.exists():
        chat_file.unlink()
        click.echo(f"  Cleaned chat: {chat_file.name}")


# -- Plan commands ----------------------------------------------------------

def _plan_foreground(root, cfg, tm, planner, tasks):
    """Run plan generation in foreground (blocking)."""
    for t in tasks:
        click.echo(f"Planning: {t.id} - {t.title}")
        click.echo(f"  Prompt: {t.prompt[:100]}{'...' if len(t.prompt) > 100 else ''}")
        tm.update_status(t.id, TaskStatus.PLANNING)
        click.echo(f"  [AI is generating plan...]")
        try:
            plan_file = planner.generate(t)
        except KeyboardInterrupt:
            tm.update_status(t.id, TaskStatus.PENDING)
            click.echo(f"\n  Interrupted, {t.id} rolled back to pending")
            raise SystemExit(130)
        if plan_file:
            tm.update_status(t.id, TaskStatus.PLANNED)
            click.echo(f"  [AI generation complete]")
            click.echo(f"  Plan saved to {plan_file}")
        else:
            tm.update_status(t.id, TaskStatus.FAILED, t.error)
            click.echo(f"  [AI generation failed]")
            click.echo(f"  Plan failed: {t.error}")


def _plan_background(root, cfg, tm, tasks):
    """Fork a detached background process for plan generation."""
    from datetime import datetime

    # Mark all tasks as PLANNING before forking
    for t in tasks:
        tm.update_status(t.id, TaskStatus.PLANNING)

    log_file = root / ".claude-flow" / "logs" / "plan-bg.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    pid = os.fork()
    if pid > 0:
        # Parent: report and return immediately
        click.echo(f"Started planning {len(tasks)} task(s) in background (PID: {pid})")
        for t in tasks:
            click.echo(f"  ⟳ {t.id} - {t.title}")
        click.echo(f"\nCheck progress:  cf plan status")
        click.echo(f"View log:        cf log plan-bg")
        return

    # -- Child process: detach from terminal and execute planning --
    try:
        os.setsid()

        # Redirect stdio to log file
        fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.close(fd)
        devnull_fd = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull_fd, 0)
        os.close(devnull_fd)

        # Re-create objects in child to avoid shared file descriptor issues
        tm_child = TaskManager(root)
        plans_dir = root / ".claude-flow" / "plans"
        planner = Planner(root, plans_dir, cfg, task_manager=tm_child)

        def _now():
            return datetime.now().replace(microsecond=0).isoformat()

        print(f"\n[{_now()}] Background planning started for {len(tasks)} task(s)",
              flush=True)

        for t in tasks:
            print(f"[{_now()}] Planning: {t.id} - {t.title}", flush=True)
            try:
                plan_file = planner.generate(t)
            except Exception as e:
                tm_child.update_status(t.id, TaskStatus.FAILED, str(e))
                print(f"[{_now()}] Failed: {t.id} - {e}", flush=True)
                continue
            if plan_file:
                tm_child.update_status(t.id, TaskStatus.PLANNED)
                print(f"[{_now()}] Done: {t.id} -> {plan_file}", flush=True)
            else:
                tm_child.update_status(t.id, TaskStatus.FAILED, t.error)
                print(f"[{_now()}] Failed: {t.id} - {t.error}", flush=True)

        print(f"[{_now()}] Background planning completed", flush=True)
    except Exception as e:
        try:
            print(f"Fatal error in background planning: {e}", flush=True)
        except Exception:
            pass
    finally:
        os._exit(0)


@main.group(invoke_without_command=True)
@click.option("-t", "--task-id", "task_id", default=None, help="Plan a specific task by ID")
@click.option("-F", "--foreground", is_flag=True, help="Run in foreground (blocking mode)")
@click.option("-i", "--interactive", is_flag=True, help="Use interactive chat mode")
@click.pass_context
def plan(ctx, task_id, foreground, interactive):
    """Generate plans for tasks.

    Use -t TASK_ID to plan a specific task.
    Otherwise, plan all pending tasks (auto mode only).
    Use --interactive to start a chat-based planning session.
    """
    if ctx.invoked_subcommand is not None:
        return
    root = ctx.obj["root"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    plans_dir = root / ".claude-flow" / "plans"
    planner = Planner(root, plans_dir, cfg, task_manager=tm)

    if interactive:
        # Interactive mode requires a specific task
        if not task_id:
            click.echo("Error: --interactive requires a task_id")
            return
        t = tm.get(task_id)
        if not t:
            click.echo(f"Task {task_id} not found")
            return
        if t.is_mini:
            click.echo(f"Task {task_id} is a mini task (no planning needed)")
            return
        chat_mgr = ChatManager(root, cfg)
        chat_mgr.create_session(task_id, mode="interactive")
        tm.update_status(task_id, TaskStatus.PLANNING)
        click.echo(f"Interactive planning session started for {task_id}")
        click.echo(f"Task prompt: {t.prompt[:100]}{'...' if len(t.prompt) > 100 else ''}")
        click.echo(f"\n[AI is analyzing the task...]\n")
        response = chat_mgr.send_initial_prompt(task_id, t.prompt)
        if response:
            click.echo(f"AI: {response}\n")
            click.echo(f"[Waiting for your input]")
        else:
            click.echo("Failed to get initial AI analysis")
        click.echo(f"\nUse 'cf plan chat {task_id}' to continue the conversation")
        click.echo(f"Use 'cf plan finalize {task_id}' to generate the plan document")
        return

    if task_id:
        t = tm.get(task_id)
        if t is None:
            click.echo(f"Task {task_id} not found")
            return
        if t.is_mini:
            click.echo(f"Task {task_id} is a mini task (no planning needed)")
            return
        tasks = [t]
    else:
        tasks = [t for t in tm.list_tasks(status=TaskStatus.PENDING) if not t.is_mini]

    if not tasks:
        click.echo("No pending tasks to plan")
        return

    if foreground:
        _plan_foreground(root, cfg, tm, planner, tasks)
    else:
        _plan_background(root, cfg, tm, tasks)


@plan.command("status")
@click.pass_context
def plan_status(ctx):
    """Check planning progress."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    planning = tm.list_tasks(status=TaskStatus.PLANNING)
    planned = tm.list_tasks(status=TaskStatus.PLANNED)

    if planning:
        click.echo("In progress:")
        for t in planning:
            click.echo(f"  ⟳ {t.id} - {t.title}")
        click.echo("  (If stuck, use 'cf reset <task_id>' to reset)")

    if planned:
        click.echo("Ready for review:")
        for t in planned:
            click.echo(f"  ◉ {t.id} - {t.title}")

    if not planning and not planned:
        click.echo("No plans in progress or ready for review")

    # Show recent log tail
    log_file = root / ".claude-flow" / "logs" / "plan-bg.log"
    if log_file.exists():
        lines = log_file.read_text().strip().splitlines()
        if lines:
            click.echo(f"\nRecent log:")
            for line in lines[-5:]:
                click.echo(f"  {line}")


@plan.command("review")
@click.pass_context
def plan_review(ctx):
    """Interactively review generated plans."""
    root = ctx.obj["root"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    plans_dir = root / ".claude-flow" / "plans"
    planner = Planner(root, plans_dir, cfg, task_manager=tm)

    tasks = tm.list_tasks(status=TaskStatus.PLANNED)
    if not tasks:
        click.echo("No plans to review")
        return

    try:
        for t in tasks:
            plan_path = Path(t.plan_file) if t.plan_file else plans_dir / f"{t.id}.md"
            if not plan_path.exists():
                click.echo(f"Plan file missing for {t.id}, skipping")
                continue

            click.echo(f"\n{'─' * 50}")
            click.echo(f"Task:   {t.id} - {t.title}")
            click.echo(f"{'─' * 50}")
            click.echo(_strip_ansi(planner.read_plan(plan_path)))
            click.echo(f"{'─' * 50}")

            _reset_terminal()
            action = click.prompt(
                "[a]pprove  [c]hat  [s]kip  [e]dit  [q]uit",
                type=str, default="s"
            )
            if action == "a":
                planner.approve(t)
                tm.update_status(t.id, TaskStatus.APPROVED)
                click.echo(f"  {t.id} approved")
            elif action == "c":
                # Start interactive chat for this task
                chat_mgr = ChatManager(root, cfg)
                session = chat_mgr.get_session(t.id)
                if not session:
                    chat_mgr.create_session(t.id, mode="interactive")
                tm.update_status(t.id, TaskStatus.PLANNING)
                click.echo(f"  Chat session started for {t.id}")
                click.echo(f"  Use 'cf plan chat {t.id} -m \"message\"' to continue")
            elif action == "e":
                editor = os.environ.get("EDITOR", "vi")
                subprocess.run([editor, str(plan_path)])
                planner.approve(t)
                tm.update_status(t.id, TaskStatus.APPROVED)
                click.echo(f"  {t.id} edited and approved")
            elif action == "q":
                break
    except (KeyboardInterrupt, click.Abort):
        click.echo("\nReview interrupted.")
        return


@plan.command("chat")
@click.argument("task_id")
@click.option("-m", "--message", default=None, help="Message to send to AI")
@click.pass_context
def plan_chat(ctx, task_id, message):
    """Send a chat message for interactive planning."""
    root = ctx.obj["root"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    chat_mgr = ChatManager(root, cfg)

    t = tm.get(task_id)
    if not t:
        click.echo(f"Task {task_id} not found")
        return

    session = chat_mgr.get_session(task_id)
    if not session:
        # Auto-create session from existing plan file if available
        if t.plan_file:
            plan_path = Path(t.plan_file)
            if plan_path.exists():
                plan_content = plan_path.read_text()
                session = chat_mgr.create_session_from_plan(task_id, plan_content)
                tm.update_status(task_id, TaskStatus.PLANNING)
                click.echo(f"Chat session created from existing plan for {task_id}")
        if not session:
            click.echo(f"No chat session for {task_id}. Start one with: cf plan {task_id} --interactive")
            return

    if session.status != "active":
        click.echo(f"Chat session for {task_id} is finalized")
        return

    if message:
        # Single message mode
        click.echo(f"[AI is generating response...]")
        response = chat_mgr.send_message(task_id, message, task_prompt=t.prompt)
        if response:
            click.echo(f"\nAI: {response}")
            click.echo(f"\n[Waiting for your input]")
        else:
            click.echo("Failed to get AI response")
    else:
        # Interactive REPL mode
        click.echo(f"Chat session for: {t.title} ({task_id})")
        click.echo(f"Type your messages. Enter empty line to quit.\n")

        # Show existing history or trigger first-round AI output
        if session.messages:
            for msg in session.messages:
                prefix = "You" if msg.role == "user" else "AI"
                click.echo(f"  {prefix}: {msg.content[:200]}{'...' if len(msg.content) > 200 else ''}\n")
        else:
            # No history yet: trigger initial AI analysis from the task prompt
            click.echo(f"  Task prompt: {t.prompt[:200]}{'...' if len(t.prompt) > 200 else ''}\n")
            click.echo(f"  [AI is analyzing your task...]")
            response = chat_mgr.send_initial_prompt(task_id, t.prompt)
            if response:
                click.echo(f"\n  AI: {response}\n")
            else:
                click.echo("  Failed to get initial AI analysis\n")

        click.echo(f"[Waiting for your input]\n")
        _reset_terminal()
        while True:
            try:
                user_input = click.prompt("You", default="", show_default=False)
            except (KeyboardInterrupt, click.Abort, EOFError):
                click.echo("\nChat ended.")
                break
            if not user_input.strip():
                break
            click.echo("  [AI is generating response...]")
            response = chat_mgr.send_message(task_id, user_input, task_prompt=t.prompt)
            if response:
                click.echo(f"\n  AI: {response}\n")
                click.echo(f"  [Waiting for your input]\n")
            else:
                click.echo("  Failed to get response\n")

        click.echo(f"Use 'cf plan finalize {task_id}' to generate the plan document")


@plan.command("finalize")
@click.argument("task_id")
@click.pass_context
def plan_finalize(ctx, task_id):
    """Generate a plan document from the chat session."""
    root = ctx.obj["root"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    plans_dir = root / ".claude-flow" / "plans"
    planner = Planner(root, plans_dir, cfg, task_manager=tm)
    chat_mgr = ChatManager(root, cfg)

    t = tm.get(task_id)
    if not t:
        click.echo(f"Task {task_id} not found")
        return

    session = chat_mgr.get_session(task_id)
    if not session:
        click.echo(f"No chat session for {task_id}")
        return

    if not session.messages:
        click.echo("Chat session has no messages")
        return

    click.echo(f"Generating plan from chat session ({len(session.messages)} messages)...")
    chat_mgr.finalize(task_id)
    tm.update_status(task_id, TaskStatus.PLANNING)

    try:
        plan_file = planner.generate_from_chat(t, session)
    except KeyboardInterrupt:
        tm.update_status(task_id, TaskStatus.PLANNING)
        click.echo(f"\n  Interrupted")
        return

    if plan_file:
        tm.update_status(task_id, TaskStatus.PLANNED)
        click.echo(f"  Plan saved to {plan_file}")
    else:
        tm.update_status(task_id, TaskStatus.FAILED, t.error)
        click.echo(f"  Plan generation failed: {t.error}")


@plan.command("approve")
@click.argument("task_id", required=False)
@click.option("--all", "approve_all", is_flag=True, help="Approve all planned tasks")
@click.pass_context
def plan_approve(ctx, task_id, approve_all):
    """Approve a plan or all plans."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    cfg = Config.load(root)
    planner = Planner(root, root / ".claude-flow" / "plans", cfg)

    if approve_all:
        tasks = tm.list_tasks(status=TaskStatus.PLANNED)
        for t in tasks:
            planner.approve(t)
            tm.update_status(t.id, TaskStatus.APPROVED)
            click.echo(f"  {t.id} approved")
    elif task_id:
        t = tm.get(task_id)
        if t and t.status == TaskStatus.PLANNED:
            planner.approve(t)
            tm.update_status(t.id, TaskStatus.APPROVED)
            click.echo(f"  {t.id} approved")
        else:
            click.echo(f"Task {task_id} not found or not in planned state")


# -- Run command ------------------------------------------------------------

@main.command()
@click.option("-n", "--num-workers", default=1, type=int, help="Number of parallel workers")
@click.option("-d", "--daemon", is_flag=True, help="Run in daemon mode (continuous polling)")
@click.argument("task_id", required=False)
@click.pass_context
def run(ctx, num_workers, daemon, task_id):
    """Start workers to execute approved tasks."""
    root = ctx.obj["root"]
    is_git = ctx.obj["is_git"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    wt = WorktreeManager(root, root / cfg.worktree_dir, is_git=is_git)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Non-git mode: enforce single worker (no worktree isolation)
    if not is_git and num_workers > 1:
        click.echo("Warning: non-git project, forcing single worker (no worktree isolation)")
        num_workers = 1

    if task_id:
        t = tm.get(task_id)
        if not t:
            click.echo(f"Task {task_id} not found")
            return
        if t.status != TaskStatus.APPROVED:
            tm.update_status(t.id, TaskStatus.APPROVED)
        worker = Worker(0, root, tm, wt, cfg, is_git=is_git)
        t = tm.claim_next(0)
        if t:
            worker.execute_task(t)
        return

    if num_workers == 1:
        worker = Worker(0, root, tm, wt, cfg, is_git=is_git)
        if daemon:
            click.echo("Starting daemon mode (Ctrl+C to stop)...")
            count = worker.run_daemon()
        else:
            count = worker.run_loop()
        click.echo(f"Completed {count} tasks")
    else:
        # Multi-worker: spawn subprocesses
        import multiprocessing
        from dataclasses import asdict

        worker_args = [
            (wid, root, cfg.worktree_dir, asdict(cfg), daemon, is_git)
            for wid in range(num_workers)
        ]
        with multiprocessing.Pool(num_workers) as pool:
            results = pool.map(_worker_entry, worker_args)
        total = sum(results)
        click.echo(f"Completed {total} tasks across {num_workers} workers")


# -- Watch command ----------------------------------------------------------

@main.command()
@click.option("--interval", default=2, type=int, help="Refresh interval in seconds")
@click.pass_context
def watch(ctx, interval):
    """Watch real-time worker activity (stream-json monitor)."""
    import json
    import time

    root = ctx.obj["root"]
    status_dir = root / ".claude-flow" / "worker-status"

    click.echo("Watching worker status (Ctrl+C to stop)...")
    try:
        while True:
            click.clear()
            click.echo("=== Claude Flow Worker Monitor ===\n")

            if not status_dir.exists():
                click.echo("No worker status data yet.")
            else:
                for status_file in sorted(status_dir.glob("worker-*.json")):
                    try:
                        data = json.loads(status_file.read_text())
                        wid = data.get("worker_id", "?")
                        tid = data.get("task_id", "-")
                        last = data.get("last_event", "-")
                        events = data.get("event_count", 0)
                        tools = data.get("tool_use_count", 0)
                        errors = data.get("error_count", 0)
                        updated = data.get("updated_at", "-")
                        click.echo(f"  Worker-{wid}  task={tid}  events={events}  tools={tools}  errors={errors}")
                        click.echo(f"    Last: {last}")
                        click.echo(f"    Updated: {updated}")
                        click.echo()
                    except (json.JSONDecodeError, KeyError):
                        continue

            # 同时显示任务状态概览
            tm = TaskManager(root)
            tasks = tm.list_tasks()
            counts = {}
            for t in tasks:
                counts[t.status.value] = counts.get(t.status.value, 0) + 1
            click.echo(f"--- Tasks: {len(tasks)} total ---")
            for s, c in sorted(counts.items()):
                click.echo(f"  {s}: {c}")

            time.sleep(interval)
    except KeyboardInterrupt:
        click.echo("\nStopped watching.")


# -- Web command ------------------------------------------------------------

@main.command()
@click.option("--port", default=None, type=int, help="Web server port (default from config)")
@click.pass_context
def web(ctx, port):
    """Start the web manager kanban interface."""
    root = ctx.obj["root"]
    cfg = Config.load(root)
    web_port = port or cfg.web_port

    try:
        from .web import create_app
    except ImportError:
        click.echo("Error: Flask is required for the web manager.")
        click.echo("Install it with: pip install flask")
        return

    app = create_app(root, cfg)
    click.echo(f"Starting web manager on http://0.0.0.0:{web_port}")
    app.run(host="0.0.0.0", port=web_port, debug=False)


# -- Status / Log / Clean / Reset / Retry -----------------------------------

@main.command()
@click.pass_context
def status(ctx):
    """Show task and worker status overview."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    tasks = tm.list_tasks()
    counts = {}
    for t in tasks:
        counts[t.status.value] = counts.get(t.status.value, 0) + 1
    click.echo(f"Total tasks: {len(tasks)}")
    for s, c in sorted(counts.items()):
        click.echo(f"  {s}: {c}")

    # 显示活跃 worker 状态
    cfg = Config.load(root)
    status_dir = root / ".claude-flow" / "worker-status"
    if status_dir.exists():
        import json
        click.echo(f"\nActive workers:")
        for sf in sorted(status_dir.glob("worker-*.json")):
            try:
                data = json.loads(sf.read_text())
                click.echo(f"  Worker-{data.get('worker_id', '?')}: "
                          f"task={data.get('task_id', '-')} "
                          f"events={data.get('event_count', 0)}")
            except (json.JSONDecodeError, KeyError):
                continue


@main.command()
@click.argument("task_id")
@click.option("--raw", is_flag=True, help="Show raw stream-json log instead of formatted output")
@click.pass_context
def log(ctx, task_id, raw):
    """View task execution log."""
    import json as _json

    root = ctx.obj["root"]
    logs_dir = root / ".claude-flow" / "logs"

    if raw:
        raw_file = logs_dir / f"{task_id}.log"
        if raw_file.exists():
            click.echo(raw_file.read_text())
        else:
            click.echo(f"No raw log for {task_id}")
        return

    # Prefer structured JSON log
    json_file = logs_dir / f"{task_id}.json"
    if json_file.exists():
        try:
            from .monitor import format_structured_log_for_cli
            log_data = _json.loads(json_file.read_text())
            click.echo(format_structured_log_for_cli(log_data))
            return
        except Exception:
            pass  # Fall through to raw log

    # Fallback to raw log
    raw_file = logs_dir / f"{task_id}.log"
    if raw_file.exists():
        click.echo(raw_file.read_text())
    else:
        click.echo(f"No log for {task_id}")


@main.command()
@click.pass_context
def clean(ctx):
    """Clean up worktrees and merged branches."""
    root = ctx.obj["root"]
    is_git = ctx.obj["is_git"]
    if not is_git:
        click.echo("Non-git project: no worktrees to clean")
        return
    cfg = Config.load(root)
    wt = WorktreeManager(root, root / cfg.worktree_dir, is_git=is_git)
    count = wt.cleanup_all()
    click.echo(f"Cleaned {count} worktrees")


@main.command()
@click.argument("task_id")
@click.pass_context
def reset(ctx, task_id):
    """Reset a failed task back to pending."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    t = tm.get(task_id)
    if not t:
        click.echo(f"Task {task_id} not found")
        return
    if t.status in (TaskStatus.FAILED, TaskStatus.NEEDS_INPUT):
        # Mini tasks reset to APPROVED (skip planning), normal tasks to PENDING
        target = TaskStatus.APPROVED if t.is_mini else TaskStatus.PENDING
        tm.update_status(task_id, target)
        click.echo(f"Reset {task_id} to {target.value}")
    elif t.status == TaskStatus.RUNNING:
        # Reset zombie running task (worker crashed without updating status)
        if t.is_mini:
            target = TaskStatus.APPROVED
        else:
            target = TaskStatus.PLANNED if t.plan_file else TaskStatus.PENDING
        tm.update_status(task_id, target)
        # Clean up orphaned worktree and branch (git repos only)
        is_git = ctx.obj["is_git"]
        if is_git:
            cfg = Config.load(root)
            wt = WorktreeManager(root, root / cfg.worktree_dir, is_git=is_git)
            wt.remove(task_id, t.branch)
        click.echo(f"Reset zombie running task {task_id} to {target.value}")
    else:
        click.echo(f"Task {task_id} is in {t.status.value} status, cannot reset")


@main.command()
@click.pass_context
def retry(ctx):
    """Retry all failed tasks."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    failed = tm.list_tasks(status=TaskStatus.FAILED)
    for t in failed:
        tm.update_status(t.id, TaskStatus.APPROVED)
        click.echo(f"  {t.id} -> approved")
    click.echo(f"Retrying {len(failed)} tasks")


# -- Respond command --------------------------------------------------------

@main.command()
@click.argument("task_id")
@click.option("-m", "--message", prompt="补充信息", help="提供给任务的补充信息")
@click.pass_context
def respond(ctx, task_id, message):
    """Respond to a task that needs input."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    t = tm.get(task_id)
    if not t:
        click.echo(f"Task {task_id} not found")
        return
    if t.status != TaskStatus.NEEDS_INPUT:
        click.echo(f"Task {task_id} is {t.status.value}, not needs_input")
        return
    if t.error:
        click.echo(f"\nClaude's question:\n{t.error}\n")
    updated = tm.respond(task_id, message)
    if updated:
        click.echo(f"Responded to {task_id}, status -> approved")
    else:
        click.echo(f"Failed to respond to {task_id}")


# -- Usage commands ---------------------------------------------------------

@main.group(invoke_without_command=True)
@click.option("--since", default=None, help="Start date (YYYY-MM-DD)")
@click.option("--until", default=None, help="End date (YYYY-MM-DD)")
@click.pass_context
def usage(ctx, since, until):
    """Show token usage statistics.

    Default: per-session/task usage report.
    Subcommands: daily, monthly, summary.
    """
    ctx.ensure_object(dict)
    ctx.obj["since"] = since
    ctx.obj["until"] = until

    if ctx.invoked_subcommand is not None:
        return

    from .usage import UsageManager, format_session_table

    root = ctx.obj["root"]
    cfg = Config.load(root)
    mgr = UsageManager(root, cfg)
    sessions = mgr.get_session_usage(since=since, until=until)
    click.echo(format_session_table(sessions))


@usage.command("daily")
@click.pass_context
def usage_daily(ctx):
    """Show daily aggregated usage (requires ccusage)."""
    from .usage import UsageManager, format_daily_table

    root = ctx.obj["root"]
    cfg = Config.load(root)
    mgr = UsageManager(root, cfg)
    data = mgr.get_daily_usage(
        since=ctx.obj.get("since"), until=ctx.obj.get("until"),
    )
    if data is None:
        click.echo("Daily report requires ccusage (npx ccusage@latest).")
        click.echo("Falling back to session usage from logs:")
        sessions = mgr.get_session_usage()
        from .usage import format_session_table
        click.echo(format_session_table(sessions))
        return
    click.echo(format_daily_table(data))


@usage.command("monthly")
@click.pass_context
def usage_monthly(ctx):
    """Show monthly aggregated usage (requires ccusage)."""
    from .usage import UsageManager, format_daily_table

    root = ctx.obj["root"]
    cfg = Config.load(root)
    mgr = UsageManager(root, cfg)
    data = mgr.get_monthly_usage(
        since=ctx.obj.get("since"), until=ctx.obj.get("until"),
    )
    if data is None:
        click.echo("Monthly report requires ccusage (npx ccusage@latest).")
        return
    # Reuse daily table format (same columns)
    click.echo(format_daily_table(data))


@usage.command("summary")
@click.pass_context
def usage_summary(ctx):
    """Show overall usage summary."""
    from .usage import UsageManager, format_summary

    root = ctx.obj["root"]
    cfg = Config.load(root)
    mgr = UsageManager(root, cfg)
    summary = mgr.get_summary(
        since=ctx.obj.get("since"), until=ctx.obj.get("until"),
    )
    click.echo(format_summary(summary))


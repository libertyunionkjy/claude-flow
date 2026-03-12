from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

CLAUDE_FLOW_DIR = ".claude-flow"
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "max_workers": 2,
    "main_branch": "main",
    "claude_args": [],
    "auto_merge": True,
    "merge_strategy": "--no-ff",
    "worktree_dir": ".claude-flow/worktrees",
    "skip_permissions": True,
    "plan_prompt_prefix": "请分析以下任务并输出实施计划，不要执行代码:",
    "task_prompt_prefix": "你的任务是（请直接实现，不要提问或等待确认，直接修改代码并完成任务）:",
    "task_timeout": 600,
    # Plan-phase tool restrictions (empty list = no restriction)
    "plan_allowed_tools": ["Read", "Glob", "Grep"],
    # Worktree symlink sharing
    "shared_symlinks": [],
    "forbidden_symlinks": ["PROGRESS.md"],
    # Merge strategy
    "merge_mode": "rebase",
    "max_merge_retries": 5,
    # Pre-merge testing
    "pre_merge_commands": [],
    "max_test_retries": 3,
    # Remote push
    "auto_push": False,
    # PROGRESS.md experience logging
    "enable_progress_log": True,
    "progress_file": "PROGRESS.md",
    # Worker port assignment
    "base_port": 5200,
    # Daemon mode
    "daemon_poll_interval": 10,
    # Web manager
    "web_port": 8080,
    # Subagent mode
    "use_subagent": False,
    # Submodule branch management
    "default_sub_branches": {},
    "auto_push_submodules": False,
}


@dataclass
class Config:
    max_workers: int = 2
    main_branch: str = "main"
    claude_args: List[str] = field(default_factory=list)
    auto_merge: bool = True
    merge_strategy: str = "--no-ff"
    worktree_dir: str = ".claude-flow/worktrees"
    skip_permissions: bool = True
    plan_prompt_prefix: str = "请分析以下任务并输出实施计划，不要执行代码:"
    task_prompt_prefix: str = "你的任务是（请直接实现，不要提问或等待确认，直接修改代码并完成任务）:"
    task_timeout: int = 600
    # Plan-phase tool restrictions (empty list = no restriction)
    plan_allowed_tools: List[str] = field(
        default_factory=lambda: ["Read", "Glob", "Grep"]
    )
    # Worktree symlink sharing
    shared_symlinks: List[str] = field(default_factory=list)
    forbidden_symlinks: List[str] = field(default_factory=lambda: ["PROGRESS.md"])
    # Merge strategy
    merge_mode: str = "rebase"
    max_merge_retries: int = 5
    # Pre-merge testing
    pre_merge_commands: List[str] = field(default_factory=list)
    max_test_retries: int = 3
    # Remote push
    auto_push: bool = False
    # PROGRESS.md experience logging
    enable_progress_log: bool = True
    progress_file: str = "PROGRESS.md"
    # Worker port assignment
    base_port: int = 5200
    # Daemon mode
    daemon_poll_interval: int = 10
    # Web manager
    web_port: int = 8080
    # Subagent mode
    use_subagent: bool = False
    # Submodule branch management
    default_sub_branches: dict[str, str] = field(default_factory=dict)
    auto_push_submodules: bool = False

    @classmethod
    def load(cls, project_root: Path) -> Config:
        config_file = project_root / CLAUDE_FLOW_DIR / CONFIG_FILE
        if not config_file.exists():
            return cls()
        data = json.loads(config_file.read_text())
        merged = {**DEFAULT_CONFIG, **data}
        return cls(**{k: v for k, v in merged.items() if k in cls.__dataclass_fields__})

    def save(self, project_root: Path) -> None:
        config_file = project_root / CLAUDE_FLOW_DIR / CONFIG_FILE
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))

    @staticmethod
    def claude_flow_dir(project_root: Path) -> Path:
        return project_root / CLAUDE_FLOW_DIR

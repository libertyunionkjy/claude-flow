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
    "task_prompt_prefix": "你的任务是:",
    "task_timeout": 600,
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
    task_prompt_prefix: str = "你的任务是:"
    task_timeout: int = 600

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

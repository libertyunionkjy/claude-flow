"""Integration test fixtures."""
from __future__ import annotations

import pytest
from pathlib import Path

from claude_flow.config import Config
from claude_flow.task_manager import TaskManager
from claude_flow.worktree import WorktreeManager
from claude_flow.planner import Planner
from claude_flow.chat import ChatManager


@pytest.fixture
def full_project(cf_project: Path):
    """Provide a fully initialized project with all managers."""
    config = Config.load(cf_project)
    tm = TaskManager(cf_project)
    plans_dir = cf_project / ".claude-flow" / "plans"
    wm = WorktreeManager(cf_project, cf_project / config.worktree_dir)
    planner = Planner(cf_project, plans_dir, config, task_manager=tm)
    chat_mgr = ChatManager(cf_project, config)
    return {
        "root": cf_project,
        "config": config,
        "tm": tm,
        "wm": wm,
        "planner": planner,
        "chat_mgr": chat_mgr,
        "plans_dir": plans_dir,
    }

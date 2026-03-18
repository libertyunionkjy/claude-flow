"""Tests for multi-repo workspace models, config, and task manager."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from claude_flow.config import Config
from claude_flow.models import ManagedRepo, ProjectMode, Task, TaskStatus
from claude_flow.task_manager import TaskManager


# ===================================================================
# ProjectMode enum
# ===================================================================

class TestProjectMode:
    def test_enum_values(self):
        assert ProjectMode.SINGLE_GIT.value == "single_git"
        assert ProjectMode.GIT_SUBMODULE.value == "git_submodule"
        assert ProjectMode.MULTI_REPO.value == "multi_repo"
        assert ProjectMode.NON_GIT.value == "non_git"

    def test_enum_from_value(self):
        assert ProjectMode("multi_repo") == ProjectMode.MULTI_REPO
        assert ProjectMode("single_git") == ProjectMode.SINGLE_GIT

    def test_enum_invalid_raises(self):
        with pytest.raises(ValueError):
            ProjectMode("invalid_mode")


# ===================================================================
# ManagedRepo dataclass
# ===================================================================

class TestManagedRepo:
    def test_to_dict(self):
        repo = ManagedRepo(path="frontend", alias="fe", main_branch="develop")
        d = repo.to_dict()
        assert d["path"] == "frontend"
        assert d["alias"] == "fe"
        assert d["main_branch"] == "develop"
        assert d["auto_merge"] is True
        assert d["merge_strategy"] == "--no-ff"
        assert d["merge_mode"] == "rebase"
        assert d["auto_push"] is False

    def test_from_dict(self):
        d = {
            "path": "backend",
            "alias": "be",
            "main_branch": "master",
            "auto_merge": False,
            "merge_strategy": "--ff-only",
            "merge_mode": "merge",
            "auto_push": True,
        }
        repo = ManagedRepo.from_dict(d)
        assert repo.path == "backend"
        assert repo.alias == "be"
        assert repo.main_branch == "master"
        assert repo.auto_merge is False
        assert repo.merge_strategy == "--ff-only"
        assert repo.merge_mode == "merge"
        assert repo.auto_push is True

    def test_from_dict_minimal(self):
        d = {"path": "my-project"}
        repo = ManagedRepo.from_dict(d)
        assert repo.path == "my-project"
        assert repo.alias == "my-project"  # default = dir name
        assert repo.main_branch == "main"
        assert repo.auto_merge is True

    def test_default_alias_is_dirname(self):
        repo = ManagedRepo(path="services/api-server")
        assert repo.alias == "api-server"

    def test_default_alias_trailing_slash(self):
        repo = ManagedRepo(path="services/api-server/")
        assert repo.alias == "api-server"

    def test_default_alias_simple_path(self):
        repo = ManagedRepo(path="myproject")
        assert repo.alias == "myproject"

    def test_explicit_alias_not_overridden(self):
        repo = ManagedRepo(path="services/api", alias="my-api")
        assert repo.alias == "my-api"

    def test_roundtrip_serialization(self):
        original = ManagedRepo(
            path="libs/core", alias="core", main_branch="develop",
            auto_merge=False, merge_strategy="--ff-only",
            merge_mode="merge", auto_push=True,
        )
        restored = ManagedRepo.from_dict(original.to_dict())
        assert restored.path == original.path
        assert restored.alias == original.alias
        assert restored.main_branch == original.main_branch
        assert restored.auto_merge == original.auto_merge
        assert restored.merge_strategy == original.merge_strategy
        assert restored.merge_mode == original.merge_mode
        assert restored.auto_push == original.auto_push

    def test_empty_path_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            ManagedRepo(path="")

    def test_whitespace_only_path_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            ManagedRepo(path="   ")

    def test_dotdot_path_raises(self):
        with pytest.raises(ValueError, match="cannot contain '..'"):
            ManagedRepo(path="../escape")

    def test_dotdot_middle_path_raises(self):
        with pytest.raises(ValueError, match="cannot contain '..'"):
            ManagedRepo(path="a/../b")

    def test_absolute_path_raises(self):
        with pytest.raises(ValueError, match="must be relative"):
            ManagedRepo(path="/etc/passwd")


# ===================================================================
# Task multi-repo fields
# ===================================================================

class TestTaskMultiRepoFields:
    def test_task_default_repos_empty(self):
        task = Task(title="Test", prompt="prompt")
        assert task.repos == []
        assert task.repo_base_branches == {}
        assert task.repo_merge_targets == {}

    def test_task_with_repos(self):
        task = Task(
            title="Test", prompt="prompt",
            repos=["project-a", "project-b"],
            repo_base_branches={"project-a": "main", "project-b": "develop"},
            repo_merge_targets={"project-a": "main", "project-b": "main"},
        )
        assert task.repos == ["project-a", "project-b"]
        assert task.repo_base_branches["project-b"] == "develop"
        assert task.repo_merge_targets["project-b"] == "main"

    def test_task_to_dict_includes_repos(self):
        task = Task(
            title="Test", prompt="prompt",
            repos=["frontend"],
            repo_base_branches={"frontend": "develop"},
        )
        d = task.to_dict()
        assert d["repos"] == ["frontend"]
        assert d["repo_base_branches"] == {"frontend": "develop"}
        assert d["repo_merge_targets"] == {}

    def test_task_from_dict_with_repos(self):
        d = {
            "id": "task-mr01",
            "title": "Multi-repo task",
            "prompt": "do something",
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "repos": ["project-a", "libs/core"],
            "repo_base_branches": {"project-a": "main", "libs/core": "develop"},
            "repo_merge_targets": {"project-a": "main"},
        }
        task = Task.from_dict(d)
        assert task.repos == ["project-a", "libs/core"]
        assert task.repo_base_branches["libs/core"] == "develop"
        assert task.repo_merge_targets["project-a"] == "main"

    def test_task_from_dict_without_repos_backward_compat(self):
        """Old tasks.json without repos fields should load with defaults."""
        d = {
            "id": "task-old01",
            "title": "Old Task",
            "prompt": "old prompt",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00",
        }
        task = Task.from_dict(d)
        assert task.repos == []
        assert task.repo_base_branches == {}
        assert task.repo_merge_targets == {}

    def test_task_roundtrip_with_repos(self):
        task = Task(
            title="Roundtrip", prompt="p",
            repos=["a", "b/c"],
            repo_base_branches={"a": "main", "b/c": "dev"},
            repo_merge_targets={"a": "release"},
        )
        restored = Task.from_dict(task.to_dict())
        assert restored.repos == ["a", "b/c"]
        assert restored.repo_base_branches == {"a": "main", "b/c": "dev"}
        assert restored.repo_merge_targets == {"a": "release"}


# ===================================================================
# Config multi-repo fields
# ===================================================================

class TestConfigMultiRepo:
    def test_config_default_values(self):
        cfg = Config()
        assert cfg.project_mode == "single_git"
        assert cfg.managed_repos == []

    def test_get_managed_repos_empty(self):
        cfg = Config()
        result = cfg.get_managed_repos()
        assert result == []

    def test_get_managed_repos(self):
        cfg = Config()
        cfg.managed_repos = [
            {"path": "project-a", "alias": "pa", "main_branch": "main"},
            {"path": "project-b", "alias": "pb", "main_branch": "develop"},
        ]
        repos = cfg.get_managed_repos()
        assert len(repos) == 2
        assert isinstance(repos[0], ManagedRepo)
        assert repos[0].path == "project-a"
        assert repos[1].main_branch == "develop"

    def test_get_repo_by_path_found(self):
        cfg = Config()
        cfg.managed_repos = [
            {"path": "project-a", "alias": "pa", "main_branch": "main"},
        ]
        repo = cfg.get_repo_by_path("project-a")
        assert repo is not None
        assert repo.alias == "pa"

    def test_get_repo_by_path_not_found(self):
        cfg = Config()
        cfg.managed_repos = [
            {"path": "project-a"},
        ]
        assert cfg.get_repo_by_path("nonexistent") is None

    def test_get_repo_by_alias_found(self):
        cfg = Config()
        cfg.managed_repos = [
            {"path": "services/api-server", "alias": "api"},
        ]
        repo = cfg.get_repo_by_alias("api")
        assert repo is not None
        assert repo.path == "services/api-server"

    def test_get_repo_by_alias_default_alias(self):
        """When no alias is set, from_dict uses dirname as default."""
        cfg = Config()
        cfg.managed_repos = [
            {"path": "services/api-server"},  # alias will default to "api-server"
        ]
        repo = cfg.get_repo_by_alias("api-server")
        assert repo is not None
        assert repo.path == "services/api-server"

    def test_get_repo_by_alias_not_found(self):
        cfg = Config()
        cfg.managed_repos = [
            {"path": "project-a", "alias": "pa"},
        ]
        assert cfg.get_repo_by_alias("nonexistent") is None

    def test_resolve_repo_by_path(self):
        cfg = Config()
        cfg.managed_repos = [
            {"path": "project-a", "alias": "pa"},
        ]
        repo = cfg.resolve_repo("project-a")
        assert repo is not None
        assert repo.alias == "pa"

    def test_resolve_repo_by_alias(self):
        cfg = Config()
        cfg.managed_repos = [
            {"path": "project-a", "alias": "pa"},
        ]
        repo = cfg.resolve_repo("pa")
        assert repo is not None
        assert repo.path == "project-a"

    def test_resolve_repo_not_found(self):
        cfg = Config()
        cfg.managed_repos = [
            {"path": "project-a", "alias": "pa"},
        ]
        assert cfg.resolve_repo("unknown") is None

    def test_config_save_load_roundtrip(self, tmp_path):
        """Save and load should preserve multi-repo fields."""
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)

        cfg = Config()
        cfg.project_mode = "multi_repo"
        cfg.managed_repos = [
            {"path": "project-a", "alias": "pa", "main_branch": "main"},
            {"path": "libs/core", "alias": "core", "main_branch": "develop"},
        ]
        cfg.save(tmp_path)

        loaded = Config.load(tmp_path)
        assert loaded.project_mode == "multi_repo"
        assert len(loaded.managed_repos) == 2
        repos = loaded.get_managed_repos()
        assert repos[0].path == "project-a"
        assert repos[1].alias == "core"
        assert repos[1].main_branch == "develop"


# ===================================================================
# TaskManager with repos
# ===================================================================

class TestTaskManagerMultiRepo:
    def test_add_with_repos(self, tmp_path):
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tm = TaskManager(tmp_path)
        task = tm.add(
            "Multi-repo task", "prompt",
            repos=["project-a", "project-b"],
            repo_base_branches={"project-a": "main"},
            repo_merge_targets={"project-b": "release"},
        )
        assert task.repos == ["project-a", "project-b"]
        assert task.repo_base_branches == {"project-a": "main"}
        assert task.repo_merge_targets == {"project-b": "release"}

        # Verify persistence
        loaded = tm.get(task.id)
        assert loaded.repos == ["project-a", "project-b"]
        assert loaded.repo_base_branches == {"project-a": "main"}

    def test_add_mini_with_repos(self, tmp_path):
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tm = TaskManager(tmp_path)
        task = tm.add_mini(
            "Mini multi-repo", "prompt",
            repos=["frontend"],
            repo_base_branches={"frontend": "develop"},
        )
        assert task.repos == ["frontend"]
        assert task.status == TaskStatus.APPROVED
        loaded = tm.get(task.id)
        assert loaded.repos == ["frontend"]

    def test_add_without_repos_default(self, tmp_path):
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tm = TaskManager(tmp_path)
        task = tm.add("Normal task", "prompt")
        assert task.repos == []
        assert task.repo_base_branches == {}
        assert task.repo_merge_targets == {}

    def test_list_tasks_with_repos(self, tmp_path):
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tm = TaskManager(tmp_path)
        tm.add("T1", "p1", repos=["project-a"])
        tm.add("T2", "p2", repos=["project-b"])
        tm.add("T3", "p3")

        tasks = tm.list_tasks()
        assert len(tasks) == 3
        repo_tasks = [t for t in tasks if t.repos]
        assert len(repo_tasks) == 2

    def test_backward_compat_old_tasks_json(self, tmp_path):
        """tasks.json without repos fields should load without error."""
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tasks_file = cf_dir / "tasks.json"
        old_task = {
            "id": "task-old01",
            "title": "Old Task",
            "prompt": "old prompt",
            "status": "pending",
            "task_type": "normal",
            "branch": None,
            "plan_file": None,
            "worker_id": None,
            "created_at": "2026-01-01T00:00:00",
            "started_at": None,
            "completed_at": None,
            "error": None,
            "priority": 0,
            "progress": None,
            "retry_count": 0,
            "plan_mode": None,
        }
        tasks_file.write_text(json.dumps([old_task]))

        tm = TaskManager(tmp_path)
        tasks = tm.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].repos == []
        assert tasks[0].repo_base_branches == {}
        assert tasks[0].repo_merge_targets == {}

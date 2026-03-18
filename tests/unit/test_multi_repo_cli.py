"""Tests for multi-repo CLI commands."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import click
import pytest
from click.testing import CliRunner

from claude_flow.cli import main, _parse_repo_params, _detect_default_branch
from claude_flow.config import Config
from claude_flow.models import ManagedRepo, ProjectMode
from claude_flow.task_manager import TaskManager


# ===================================================================
# _detect_default_branch
# ===================================================================

class TestDetectDefaultBranch:
    def test_detects_main(self, git_repo):
        branch = _detect_default_branch(git_repo)
        assert branch == "main"

    def test_detects_master(self, tmp_path):
        """If the repo uses 'master' instead of 'main'."""
        repo = tmp_path / "master_repo"
        subprocess.run(["git", "init", "-b", "master", str(repo)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"],
                       check=True, capture_output=True)
        (repo / "README.md").write_text("# test")
        subprocess.run(["git", "-C", str(repo), "add", "."],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"],
                       check=True, capture_output=True)

        branch = _detect_default_branch(repo)
        assert branch == "master"


# ===================================================================
# _parse_repo_params
# ===================================================================

class TestParseRepoParams:
    def _make_config(self) -> Config:
        cfg = Config()
        cfg.project_mode = ProjectMode.MULTI_REPO.value
        cfg.managed_repos = [
            {"path": "project-a", "alias": "pa", "main_branch": "main"},
            {"path": "project-b", "alias": "pb", "main_branch": "develop"},
            {"path": "libs/core", "alias": "core", "main_branch": "main"},
        ]
        return cfg

    def test_not_multi_repo_returns_empty(self):
        cfg = Config()  # default single_git
        repos, bases, targets = _parse_repo_params(cfg, ("pa",), (), (), False)
        assert repos == []
        assert bases == {}
        assert targets == {}

    def test_all_repos(self):
        cfg = self._make_config()
        repos, bases, targets = _parse_repo_params(cfg, (), (), (), True)
        assert repos == ["project-a", "project-b", "libs/core"]

    def test_specific_repos_by_alias(self):
        cfg = self._make_config()
        repos, _, _ = _parse_repo_params(cfg, ("pa", "core"), (), (), False)
        assert repos == ["project-a", "libs/core"]

    def test_specific_repos_by_path(self):
        cfg = self._make_config()
        repos, _, _ = _parse_repo_params(cfg, ("project-b",), (), (), False)
        assert repos == ["project-b"]

    def test_unknown_repo_raises(self):
        cfg = self._make_config()
        with pytest.raises(click.BadParameter, match="Unknown repo"):
            _parse_repo_params(cfg, ("nonexistent",), (), (), False)

    def test_repo_branch_parsing(self):
        cfg = self._make_config()
        repos, bases, _ = _parse_repo_params(
            cfg, ("pa",), ("pa:feature-x",), (), False,
        )
        assert bases["project-a"] == "feature-x"

    def test_repo_branch_default_fill(self):
        """Repos without explicit base branch get their config default."""
        cfg = self._make_config()
        repos, bases, _ = _parse_repo_params(
            cfg, ("pa", "pb"), (), (), False,
        )
        assert bases["project-a"] == "main"
        assert bases["project-b"] == "develop"

    def test_repo_branch_invalid_format(self):
        cfg = self._make_config()
        with pytest.raises(click.BadParameter, match="Invalid format"):
            _parse_repo_params(cfg, ("pa",), ("bad_format",), (), False)

    def test_repo_branch_unknown_repo(self):
        cfg = self._make_config()
        with pytest.raises(click.BadParameter, match="Unknown repo"):
            _parse_repo_params(cfg, ("pa",), ("unknown:main",), (), False)

    def test_repo_target_parsing(self):
        cfg = self._make_config()
        repos, _, targets = _parse_repo_params(
            cfg, ("pa",), (), ("pa:release",), False,
        )
        assert targets["project-a"] == "release"

    def test_repo_target_default_fill(self):
        """Repos without explicit target get base branch as default."""
        cfg = self._make_config()
        _, _, targets = _parse_repo_params(
            cfg, ("pa", "pb"), (), (), False,
        )
        assert targets["project-a"] == "main"
        assert targets["project-b"] == "develop"

    def test_repo_target_invalid_format(self):
        cfg = self._make_config()
        with pytest.raises(click.BadParameter, match="Invalid format"):
            _parse_repo_params(cfg, ("pa",), (), ("bad",), False)

    def test_repo_target_unknown_repo(self):
        cfg = self._make_config()
        with pytest.raises(click.BadParameter, match="Unknown repo"):
            _parse_repo_params(cfg, ("pa",), (), ("unknown:main",), False)

    def test_no_repos_no_all_repos_returns_empty(self):
        cfg = self._make_config()
        repos, bases, targets = _parse_repo_params(cfg, (), (), (), False)
        assert repos == []

    def test_empty_managed_repos_returns_empty(self):
        cfg = Config()
        cfg.project_mode = ProjectMode.MULTI_REPO.value
        cfg.managed_repos = []
        repos, _, _ = _parse_repo_params(cfg, ("something",), (), (), False)
        assert repos == []


# ===================================================================
# cf init --mode multi_repo
# ===================================================================

class TestInitMultiRepo:
    def test_init_multi_repo_with_repos(self, multi_repo_workspace):
        ws = multi_repo_workspace
        runner = CliRunner()

        with patch("claude_flow.cli._get_root", return_value=ws["workspace"]), \
             patch("claude_flow.cli.is_git_repo", return_value=False):
            result = runner.invoke(main, [
                "init", "--mode", "multi_repo",
                "--repo", "project-a", "--repo", "project-b",
            ])

        assert result.exit_code == 0, result.output
        assert "multi-repo" in result.output
        assert "project-a" in result.output
        assert "project-b" in result.output

        # Verify config was saved
        cfg = Config.load(ws["workspace"])
        assert cfg.project_mode == "multi_repo"
        assert len(cfg.managed_repos) == 2
        paths = [d["path"] for d in cfg.managed_repos]
        assert "project-a" in paths
        assert "project-b" in paths

    def test_init_multi_repo_skips_non_git(self, multi_repo_workspace):
        """Non-git subdirectories should be skipped with a warning."""
        ws = multi_repo_workspace
        # Create a non-git subdirectory
        (ws["workspace"] / "not-a-repo").mkdir()

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=ws["workspace"]), \
             patch("claude_flow.cli.is_git_repo", return_value=False):
            result = runner.invoke(main, [
                "init", "--mode", "multi_repo",
                "--repo", "project-a", "--repo", "not-a-repo",
            ])

        assert result.exit_code == 0
        assert "Warning" in result.output or "not a git repository" in result.output

        cfg = Config.load(ws["workspace"])
        # Only project-a should be managed
        paths = [d["path"] for d in cfg.managed_repos]
        assert "project-a" in paths
        assert "not-a-repo" not in paths


# ===================================================================
# cf task add with repo params
# ===================================================================

class TestTaskAddMultiRepo:
    def _setup_multi_repo_config(self, workspace: Path):
        cfg = Config()
        cfg.project_mode = ProjectMode.MULTI_REPO.value
        cfg.managed_repos = [
            ManagedRepo(path="project-a", alias="pa", main_branch="main").to_dict(),
            ManagedRepo(path="project-b", alias="pb", main_branch="develop").to_dict(),
        ]
        cfg.save(workspace)

    def test_task_add_with_repos(self, multi_repo_workspace):
        ws = multi_repo_workspace
        self._setup_multi_repo_config(ws["workspace"])

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=ws["workspace"]), \
             patch("claude_flow.cli.is_git_repo", return_value=False):
            result = runner.invoke(main, [
                "task", "add", "Multi-repo task", "-p", "do stuff",
                "-r", "project-a", "-r", "project-b",
            ])

        assert result.exit_code == 0, result.output
        assert "Added" in result.output

        tm = TaskManager(ws["workspace"])
        tasks = tm.list_tasks()
        assert len(tasks) == 1
        assert set(tasks[0].repos) == {"project-a", "project-b"}

    def test_task_add_with_all_repos(self, multi_repo_workspace):
        ws = multi_repo_workspace
        self._setup_multi_repo_config(ws["workspace"])

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=ws["workspace"]), \
             patch("claude_flow.cli.is_git_repo", return_value=False):
            result = runner.invoke(main, [
                "task", "add", "All repos task", "-p", "prompt",
                "--all-repos",
            ])

        assert result.exit_code == 0
        tm = TaskManager(ws["workspace"])
        tasks = tm.list_tasks()
        assert set(tasks[0].repos) == {"project-a", "project-b"}

    def test_task_add_with_repo_branch(self, multi_repo_workspace):
        ws = multi_repo_workspace
        self._setup_multi_repo_config(ws["workspace"])

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=ws["workspace"]), \
             patch("claude_flow.cli.is_git_repo", return_value=False):
            result = runner.invoke(main, [
                "task", "add", "Branch task", "-p", "prompt",
                "-r", "pa",
                "--repo-branch", "pa:feature-x",
            ])

        assert result.exit_code == 0
        tm = TaskManager(ws["workspace"])
        tasks = tm.list_tasks()
        assert tasks[0].repo_base_branches.get("project-a") == "feature-x"

    def test_task_add_with_repo_target(self, multi_repo_workspace):
        ws = multi_repo_workspace
        self._setup_multi_repo_config(ws["workspace"])

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=ws["workspace"]), \
             patch("claude_flow.cli.is_git_repo", return_value=False):
            result = runner.invoke(main, [
                "task", "add", "Target task", "-p", "prompt",
                "-r", "pa",
                "--repo-target", "pa:release",
            ])

        assert result.exit_code == 0
        tm = TaskManager(ws["workspace"])
        tasks = tm.list_tasks()
        assert tasks[0].repo_merge_targets.get("project-a") == "release"

    def test_task_add_resolves_alias(self, multi_repo_workspace):
        ws = multi_repo_workspace
        self._setup_multi_repo_config(ws["workspace"])

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=ws["workspace"]), \
             patch("claude_flow.cli.is_git_repo", return_value=False):
            result = runner.invoke(main, [
                "task", "add", "Alias task", "-p", "prompt",
                "-r", "pb",  # alias for project-b
            ])

        assert result.exit_code == 0
        tm = TaskManager(ws["workspace"])
        tasks = tm.list_tasks()
        assert tasks[0].repos == ["project-b"]


# ===================================================================
# cf task mini with repo params
# ===================================================================

class TestTaskMiniMultiRepo:
    def _setup_multi_repo_config(self, workspace: Path):
        cfg = Config()
        cfg.project_mode = ProjectMode.MULTI_REPO.value
        cfg.managed_repos = [
            ManagedRepo(path="project-a", alias="pa", main_branch="main").to_dict(),
        ]
        cfg.save(workspace)

    def test_task_mini_with_repos(self, multi_repo_workspace):
        ws = multi_repo_workspace
        self._setup_multi_repo_config(ws["workspace"])

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=ws["workspace"]), \
             patch("claude_flow.cli.is_git_repo", return_value=False):
            result = runner.invoke(main, [
                "task", "mini", "quick fix",
                "-r", "pa",
            ])

        assert result.exit_code == 0
        assert "Mini task added" in result.output

        tm = TaskManager(ws["workspace"])
        tasks = tm.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].repos == ["project-a"]
        assert tasks[0].is_mini


# ===================================================================
# cf task show with repos
# ===================================================================

class TestTaskShowMultiRepo:
    def test_task_show_displays_repos(self, multi_repo_workspace):
        ws = multi_repo_workspace
        cfg = Config()
        cfg.project_mode = ProjectMode.MULTI_REPO.value
        cfg.save(ws["workspace"])

        tm = TaskManager(ws["workspace"])
        task = tm.add(
            "Test", "prompt",
            repos=["project-a", "project-b"],
            repo_base_branches={"project-a": "main", "project-b": "develop"},
            repo_merge_targets={"project-a": "release"},
        )

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=ws["workspace"]), \
             patch("claude_flow.cli.is_git_repo", return_value=False):
            result = runner.invoke(main, ["task", "show", task.id])

        assert result.exit_code == 0
        assert "project-a" in result.output
        assert "project-b" in result.output
        assert "develop" in result.output
        assert "release" in result.output

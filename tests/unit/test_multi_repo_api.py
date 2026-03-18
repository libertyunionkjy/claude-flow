"""Tests for multi-repo REST API endpoints."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from claude_flow.chat import ChatManager
from claude_flow.config import Config
from claude_flow.models import ManagedRepo, ProjectMode, TaskStatus
from claude_flow.task_manager import TaskManager

try:
    from flask import Flask
    from claude_flow.web.api import api_bp
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

pytestmark = pytest.mark.skipif(not HAS_FLASK, reason="Flask not installed")


# ===================================================================
# Helpers
# ===================================================================

def _create_app(workspace: Path, project_mode: str = "multi_repo",
                managed_repos: list[dict] | None = None) -> tuple:
    """Create a Flask test app with multi-repo config."""
    cfg = Config()
    cfg.project_mode = project_mode
    cfg.managed_repos = managed_repos or []
    cf_dir = workspace / ".claude-flow"
    cf_dir.mkdir(parents=True, exist_ok=True)
    cfg.save(workspace)

    tm = TaskManager(workspace)

    app = Flask(__name__)
    app.register_blueprint(api_bp)
    app.config["TASK_MANAGER"] = tm
    app.config["CF_CONFIG"] = cfg
    app.config["PROJECT_ROOT"] = workspace
    app.config["IS_GIT"] = False
    app.config["CHAT_MANAGER"] = ChatManager(workspace, cfg)

    return app, tm, cfg


# ===================================================================
# GET /api/repos
# ===================================================================

class TestListRepos:
    def test_empty_list(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.get("/api/repos")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["data"] == []

    def test_with_repos(self, multi_repo_workspace):
        ws = multi_repo_workspace
        managed = [
            ManagedRepo(path="project-a", alias="pa").to_dict(),
            ManagedRepo(path="project-b", alias="pb").to_dict(),
        ]
        app, _, _ = _create_app(ws["workspace"], managed_repos=managed)

        with app.test_client() as client:
            resp = client.get("/api/repos")
            data = resp.get_json()
            assert data["ok"] is True
            assert len(data["data"]) == 2
            paths = [r["path"] for r in data["data"]]
            assert "project-a" in paths
            assert "project-b" in paths
            # Should include git status fields
            for entry in data["data"]:
                assert "exists" in entry
                assert "is_git" in entry
                assert entry["exists"] is True
                assert entry["is_git"] is True


# ===================================================================
# POST /api/repos
# ===================================================================

class TestAddRepo:
    def test_add_repo(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, cfg = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.post("/api/repos", json={
                "path": "project-a",
                "alias": "pa",
                "main_branch": "main",
            })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["ok"] is True
            assert data["data"]["path"] == "project-a"
            assert data["data"]["alias"] == "pa"

        # Verify saved to config
        loaded = Config.load(ws["workspace"])
        assert len(loaded.managed_repos) == 1

    def test_add_repo_empty_body(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.post("/api/repos", json={})
            assert resp.status_code == 400

    def test_add_repo_duplicate(self, multi_repo_workspace):
        ws = multi_repo_workspace
        managed = [ManagedRepo(path="project-a").to_dict()]
        app, _, _ = _create_app(ws["workspace"], managed_repos=managed)

        with app.test_client() as client:
            resp = client.post("/api/repos", json={"path": "project-a"})
            assert resp.status_code == 400
            assert "已在管理列表" in resp.get_json()["error"]

    def test_add_repo_nonexistent_path(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.post("/api/repos", json={"path": "does-not-exist"})
            assert resp.status_code == 400

    def test_add_repo_not_git(self, multi_repo_workspace):
        ws = multi_repo_workspace
        # Create a non-git directory
        (ws["workspace"] / "plain-dir").mkdir()
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.post("/api/repos", json={"path": "plain-dir"})
            assert resp.status_code == 400

    def test_add_repo_path_traversal(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.post("/api/repos", json={"path": "../etc/passwd"})
            assert resp.status_code == 400

    def test_add_repo_absolute_path(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.post("/api/repos", json={"path": "/tmp/repo"})
            assert resp.status_code == 400


# ===================================================================
# PATCH /api/repos/<path>
# ===================================================================

class TestUpdateRepo:
    def test_update_alias(self, multi_repo_workspace):
        ws = multi_repo_workspace
        managed = [ManagedRepo(path="project-a", alias="pa").to_dict()]
        app, _, _ = _create_app(ws["workspace"], managed_repos=managed)

        with app.test_client() as client:
            resp = client.patch("/api/repos/project-a", json={
                "alias": "new-alias",
            })
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["data"]["alias"] == "new-alias"

    def test_update_main_branch(self, multi_repo_workspace):
        ws = multi_repo_workspace
        managed = [ManagedRepo(path="project-a").to_dict()]
        app, _, _ = _create_app(ws["workspace"], managed_repos=managed)

        with app.test_client() as client:
            resp = client.patch("/api/repos/project-a", json={
                "main_branch": "develop",
            })
            assert resp.status_code == 200
            assert resp.get_json()["data"]["main_branch"] == "develop"

    def test_update_nonexistent_repo(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.patch("/api/repos/nonexistent", json={
                "alias": "x",
            })
            assert resp.status_code == 404


# ===================================================================
# DELETE /api/repos/<path>
# ===================================================================

class TestDeleteRepo:
    def test_delete_repo(self, multi_repo_workspace):
        ws = multi_repo_workspace
        managed = [
            ManagedRepo(path="project-a").to_dict(),
            ManagedRepo(path="project-b").to_dict(),
        ]
        app, _, _ = _create_app(ws["workspace"], managed_repos=managed)

        with app.test_client() as client:
            resp = client.delete("/api/repos/project-a")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["data"]["removed"] == "project-a"

        # Verify config updated
        loaded = Config.load(ws["workspace"])
        assert len(loaded.managed_repos) == 1
        assert loaded.managed_repos[0]["path"] == "project-b"

    def test_delete_nonexistent(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.delete("/api/repos/nonexistent")
            assert resp.status_code == 404


# ===================================================================
# GET /api/repos/<path>/branches
# ===================================================================

class TestRepoBranches:
    def test_get_branches(self, multi_repo_workspace):
        ws = multi_repo_workspace
        managed = [ManagedRepo(path="project-a").to_dict()]
        app, _, _ = _create_app(ws["workspace"], managed_repos=managed)

        with app.test_client() as client:
            resp = client.get("/api/repos/project-a/branches")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            branches = data["data"]
            assert "main" in branches
            assert "feature-x" in branches

    def test_nonexistent_repo(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.get("/api/repos/nonexistent/branches")
            assert resp.status_code == 404


# ===================================================================
# POST /api/repos/scan
# ===================================================================

class TestScanRepos:
    def test_scan_discovers_unmanaged(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.post("/api/repos/scan")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            discovered = data["data"]
            paths = [d["path"] for d in discovered]
            assert "project-a" in paths
            assert "project-b" in paths

    def test_scan_excludes_managed(self, multi_repo_workspace):
        ws = multi_repo_workspace
        managed = [ManagedRepo(path="project-a").to_dict()]
        app, _, _ = _create_app(ws["workspace"], managed_repos=managed)

        with app.test_client() as client:
            resp = client.post("/api/repos/scan")
            data = resp.get_json()
            paths = [d["path"] for d in data["data"]]
            assert "project-a" not in paths
            assert "project-b" in paths


# ===================================================================
# GET / POST /api/project-mode
# ===================================================================

class TestProjectModeEndpoints:
    def test_get_project_mode(self, multi_repo_workspace):
        ws = multi_repo_workspace
        managed = [ManagedRepo(path="project-a").to_dict()]
        app, _, _ = _create_app(ws["workspace"], managed_repos=managed)

        with app.test_client() as client:
            resp = client.get("/api/project-mode")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["data"]["mode"] == "multi_repo"
            assert len(data["data"]["managed_repos"]) == 1

    def test_set_project_mode(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.post("/api/project-mode", json={
                "mode": "single_git",
            })
            assert resp.status_code == 200
            assert resp.get_json()["data"]["mode"] == "single_git"

    def test_set_project_mode_with_repos(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.post("/api/project-mode", json={
                "mode": "multi_repo",
                "managed_repos": [
                    {"path": "project-a", "alias": "pa"},
                    {"path": "project-b"},
                ],
            })
            data = resp.get_json()
            assert data["ok"] is True
            assert len(data["data"]["managed_repos"]) == 2

    def test_set_invalid_mode(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.post("/api/project-mode", json={
                "mode": "invalid_mode",
            })
            assert resp.status_code == 400

    def test_set_mode_empty_body(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.post("/api/project-mode",
                               data="", content_type="application/json")
            assert resp.status_code == 400


# ===================================================================
# POST /api/tasks with repos (multi-repo mode)
# ===================================================================

class TestCreateTaskWithRepos:
    def test_create_task_with_repos(self, multi_repo_workspace):
        ws = multi_repo_workspace
        managed = [ManagedRepo(path="project-a").to_dict()]
        app, tm, _ = _create_app(ws["workspace"], managed_repos=managed)

        with app.test_client() as client:
            resp = client.post("/api/tasks", json={
                "title": "Multi-repo task",
                "prompt": "do stuff",
                "repos": ["project-a"],
                "repo_base_branches": {"project-a": "main"},
            })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["ok"] is True

        # Verify repos were saved
        task = tm.list_tasks()[0]
        assert task.repos == ["project-a"]

    def test_create_task_without_repos_in_multi_repo(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, tm, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.post("/api/tasks", json={
                "title": "Normal task",
                "prompt": "do stuff",
            })
            assert resp.status_code == 201

        task = tm.list_tasks()[0]
        assert task.repos == []


# ===================================================================
# GET /api/tasks/<id>/repo-status
# ===================================================================

class TestTaskRepoStatus:
    def test_task_without_repos(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, tm, _ = _create_app(ws["workspace"])
        task = tm.add("Test", "prompt")

        with app.test_client() as client:
            resp = client.get(f"/api/tasks/{task.id}/repo-status")
            assert resp.status_code == 400
            assert "没有关联仓库" in resp.get_json()["error"]

    def test_task_not_found(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.get("/api/tasks/nonexistent/repo-status")
            assert resp.status_code == 404


# ===================================================================
# GET /api/tasks/<id>/repo-diff/<repo_path>
# ===================================================================

class TestTaskRepoDiff:
    def test_task_not_found(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.get("/api/tasks/nonexistent/repo-diff/project-a")
            assert resp.status_code == 404

    def test_repo_not_in_task(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, tm, _ = _create_app(ws["workspace"])
        task = tm.add("Test", "prompt", repos=["project-a"])

        with app.test_client() as client:
            resp = client.get(f"/api/tasks/{task.id}/repo-diff/project-b")
            assert resp.status_code == 400
            assert "不在任务" in resp.get_json()["error"]

    def test_path_traversal_rejected(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, tm, _ = _create_app(ws["workspace"])
        task = tm.add("Test", "prompt", repos=["../etc"])

        with app.test_client() as client:
            resp = client.get(f"/api/tasks/{task.id}/repo-diff/../etc")
            assert resp.status_code == 400


# ===================================================================
# GET /api/repos/<path>/status
# ===================================================================

class TestRepoStatusEndpoint:
    def test_get_status(self, multi_repo_workspace):
        ws = multi_repo_workspace
        managed = [ManagedRepo(path="project-a").to_dict()]
        app, _, _ = _create_app(ws["workspace"], managed_repos=managed)

        with app.test_client() as client:
            resp = client.get("/api/repos/project-a/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["data"]["current_branch"] == "main"
            assert data["data"]["has_changes"] is False

    def test_nonexistent_repo(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.get("/api/repos/nonexistent/status")
            assert resp.status_code == 404


# ===================================================================
# GET /api/repos/<path>/worktrees
# ===================================================================

class TestRepoWorktreesEndpoint:
    def test_get_worktrees(self, multi_repo_workspace):
        ws = multi_repo_workspace
        managed = [ManagedRepo(path="project-a").to_dict()]
        app, _, _ = _create_app(ws["workspace"], managed_repos=managed)

        with app.test_client() as client:
            resp = client.get("/api/repos/project-a/worktrees")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert len(data["data"]) >= 1  # at least the main worktree

    def test_nonexistent_repo(self, multi_repo_workspace):
        ws = multi_repo_workspace
        app, _, _ = _create_app(ws["workspace"])

        with app.test_client() as client:
            resp = client.get("/api/repos/nonexistent/worktrees")
            assert resp.status_code == 404

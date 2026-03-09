"""E2E test fixtures.

Provides both mock and real claude CLI fixtures.
Smoke tests (real claude) require @pytest.mark.smoke marker.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from claude_flow.config import Config


@pytest.fixture
def real_claude_available():
    """Check if real claude CLI is available. Skip if not."""
    if not shutil.which("claude"):
        pytest.skip("claude CLI not available")


@pytest.fixture
def e2e_project(tmp_path: Path):
    """Create a fully isolated git repo for E2E testing."""
    repo = tmp_path / "e2e-project"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True
    )
    (repo / "README.md").write_text("# E2E Test Project\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo, check=True, capture_output=True
    )

    # Initialize .claude-flow
    cf_dir = repo / ".claude-flow"
    for sub in ["logs", "plans", "worktrees", "chats"]:
        (cf_dir / sub).mkdir(parents=True)
    Config().save(repo)

    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add claude-flow"],
        cwd=repo, check=True, capture_output=True
    )

    return repo

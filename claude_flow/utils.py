"""Shared utility functions for Claude Flow."""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def is_git_repo(path: Path) -> bool:
    """Check if the given path is inside a git repository.

    Walks up the directory tree looking for a .git directory or file
    (git worktrees use a .git file pointing to the main repo).
    Uses pure filesystem check to avoid subprocess dependency.
    """
    for parent in [path, *path.parents]:
        git_path = parent / ".git"
        if git_path.exists():
            return True
    return False


def is_running_as_root() -> bool:
    """Check if the current process is running with root/sudo privileges."""
    return os.geteuid() == 0


def can_skip_permissions(skip_permissions: bool) -> bool:
    """Determine whether --dangerously-skip-permissions can be used.

    Returns False if skip_permissions is disabled in config or if running
    as root/sudo (Claude CLI rejects this flag under root).
    """
    if not skip_permissions:
        return False
    if is_running_as_root():
        logger.warning(
            "Running as root/sudo — skipping --dangerously-skip-permissions "
            "(Claude CLI does not allow it under root privileges)"
        )
        return False
    return True

"""PTY Manager unit tests.

PTY fork operations are mocked since they require a real terminal environment.
Tests verify the session lifecycle management logic.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_flow.pty_manager import PtyManager, PtySession


class TestPtySession:
    def test_session_creation(self):
        """PtySession stores task_id, pid, fd."""
        s = PtySession(task_id="task-abc", pid=1234, fd=5, wt_path=Path("/tmp/wt"))
        assert s.task_id == "task-abc"
        assert s.pid == 1234
        assert s.fd == 5
        assert s.alive is True

    def test_session_mark_dead(self):
        """Session can be marked as dead."""
        s = PtySession(task_id="task-abc", pid=1234, fd=5, wt_path=Path("/tmp/wt"))
        s.alive = False
        assert s.alive is False


class TestPtyManager:
    def test_create_session(self, tmp_path):
        """create_session stores a PtySession and returns it."""
        mgr = PtyManager()
        wt_path = tmp_path / "worktree"
        wt_path.mkdir()

        with patch("claude_flow.pty_manager.pty.fork") as mock_fork, \
             patch("claude_flow.pty_manager.os.execvpe") as mock_exec:
            # Simulate parent process return from fork
            mock_fork.return_value = (1234, 5)  # (pid, fd)
            session = mgr.create_session("task-abc", wt_path, prompt="hello")

        assert session.task_id == "task-abc"
        assert session.pid == 1234
        assert session.fd == 5
        assert "task-abc" in mgr._sessions

    def test_get_session(self, tmp_path):
        """get_session returns existing session or None."""
        mgr = PtyManager()
        assert mgr.get_session("nonexistent") is None

        # Manually add a session
        s = PtySession(task_id="task-x", pid=99, fd=3, wt_path=tmp_path)
        mgr._sessions["task-x"] = s
        assert mgr.get_session("task-x") is s

    def test_remove_session(self, tmp_path):
        """remove_session kills process and removes from registry."""
        mgr = PtyManager()
        s = PtySession(task_id="task-y", pid=99, fd=3, wt_path=tmp_path)
        mgr._sessions["task-y"] = s

        with patch("claude_flow.pty_manager.os.kill") as mock_kill, \
             patch("claude_flow.pty_manager.os.close") as mock_close, \
             patch("claude_flow.pty_manager.os.waitpid") as mock_wait:
            mock_wait.return_value = (99, 0)
            mgr.remove_session("task-y")

        assert "task-y" not in mgr._sessions
        mock_kill.assert_called_once()
        mock_close.assert_called_once_with(3)

    def test_list_sessions(self, tmp_path):
        """list_sessions returns all task_ids."""
        mgr = PtyManager()
        mgr._sessions["a"] = PtySession(task_id="a", pid=1, fd=1, wt_path=tmp_path)
        mgr._sessions["b"] = PtySession(task_id="b", pid=2, fd=2, wt_path=tmp_path)
        assert set(mgr.list_sessions()) == {"a", "b"}

    def test_recover_marks_interrupted(self, tmp_path):
        """recover_sessions marks all existing sessions as dead."""
        mgr = PtyManager()
        s = PtySession(task_id="task-z", pid=99, fd=3, wt_path=tmp_path)
        mgr._sessions["task-z"] = s
        dead = mgr.recover_sessions()
        assert dead == ["task-z"]
        assert s.alive is False

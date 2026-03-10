"""PTY session lifecycle manager for mini task interactive terminals.

Each mini task gets a dedicated PTY running `claude` CLI in its worktree.
Sessions persist across browser tab close/reopen (reattachable).
Server restart marks sessions as dead (INTERRUPTED).
"""
from __future__ import annotations

import logging
import os
import pty
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PtySession:
    """Represents a single PTY session for a mini task."""
    task_id: str
    pid: int
    fd: int           # master fd for reading/writing
    wt_path: Path     # worktree directory
    alive: bool = True
    prompt: str = ""  # initial prompt sent to claude


class PtyManager:
    """Manages PTY sessions for mini tasks.

    Thread-safe via GIL for dict operations. Each session is a forked
    child process running `claude` in a worktree directory.
    """

    def __init__(self):
        self._sessions: Dict[str, PtySession] = {}

    def create_session(
        self,
        task_id: str,
        wt_path: Path,
        prompt: str = "",
        skip_permissions: bool = True,
    ) -> PtySession:
        """Fork a PTY child process running `claude` in the given worktree.

        Args:
            task_id: The mini task ID.
            wt_path: Path to the git worktree.
            prompt: Initial prompt to send after startup.
            skip_permissions: Whether to use --dangerously-skip-permissions.

        Returns:
            The created PtySession.
        """
        cmd = ["claude"]
        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        pid, fd = pty.fork()

        if pid == 0:
            # Child process: cd to worktree and exec claude
            os.chdir(str(wt_path))
            os.execvpe(cmd[0], cmd, os.environ)
            # execvpe never returns on success
        else:
            # Parent process: store session
            session = PtySession(
                task_id=task_id,
                pid=pid,
                fd=fd,
                wt_path=wt_path,
                prompt=prompt,
            )
            self._sessions[task_id] = session
            logger.info(f"PTY session created for {task_id}, pid={pid}")
            return session

    def get_session(self, task_id: str) -> Optional[PtySession]:
        """Get session by task_id, or None."""
        return self._sessions.get(task_id)

    def remove_session(self, task_id: str) -> None:
        """Kill the PTY process and remove the session."""
        session = self._sessions.pop(task_id, None)
        if session is None:
            return

        try:
            os.kill(session.pid, signal.SIGTERM)
            os.waitpid(session.pid, 0)
        except (OSError, ChildProcessError):
            pass

        try:
            os.close(session.fd)
        except OSError:
            pass

        session.alive = False
        logger.info(f"PTY session removed for {task_id}")

    def list_sessions(self) -> List[str]:
        """Return list of active session task_ids."""
        return list(self._sessions.keys())

    def recover_sessions(self) -> List[str]:
        """Mark all sessions as dead after server restart.

        Returns list of task_ids that were interrupted.
        The caller should update their status to INTERRUPTED in TaskManager.
        """
        interrupted = []
        for task_id, session in self._sessions.items():
            session.alive = False
            interrupted.append(task_id)
        return interrupted

    def read(self, task_id: str, size: int = 4096) -> Optional[bytes]:
        """Read from PTY master fd (non-blocking safe)."""
        session = self._sessions.get(task_id)
        if not session or not session.alive:
            return None
        try:
            return os.read(session.fd, size)
        except OSError:
            session.alive = False
            return None

    def write(self, task_id: str, data: bytes) -> bool:
        """Write to PTY master fd."""
        session = self._sessions.get(task_id)
        if not session or not session.alive:
            return False
        try:
            os.write(session.fd, data)
            return True
        except OSError:
            session.alive = False
            return False

    def resize(self, task_id: str, rows: int, cols: int) -> bool:
        """Resize the PTY terminal."""
        session = self._sessions.get(task_id)
        if not session or not session.alive:
            return False
        try:
            import struct
            import fcntl
            import termios
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(session.fd, termios.TIOCSWINSZ, winsize)
            return True
        except (OSError, ImportError):
            return False

    def is_alive(self, task_id: str) -> bool:
        """Check if PTY process is still running."""
        session = self._sessions.get(task_id)
        if not session or not session.alive:
            return False
        try:
            pid, status = os.waitpid(session.pid, os.WNOHANG)
            if pid != 0:
                session.alive = False
                return False
            return True
        except ChildProcessError:
            session.alive = False
            return False

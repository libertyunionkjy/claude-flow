"""ChatSession model and ChatManager for interactive plan creation."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .config import Config
from .utils import can_skip_permissions


@dataclass
class ChatMessage:
    """A single message in a chat session."""

    role: str  # "user" | "assistant"
    content: str
    timestamp: str = field(
        default_factory=lambda: datetime.now().replace(microsecond=0).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ChatMessage:
        return cls(
            role=d["role"],
            content=d["content"],
            timestamp=d.get("timestamp", ""),
        )


@dataclass
class ChatSession:
    """A conversation session tied to a task for interactive plan creation."""

    task_id: str
    mode: str = "interactive"  # "auto" | "interactive"
    status: str = "active"  # "active" | "finalized"
    messages: List[ChatMessage] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "mode": self.mode,
            "status": self.status,
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, d: dict) -> ChatSession:
        return cls(
            task_id=d["task_id"],
            mode=d.get("mode", "interactive"),
            status=d.get("status", "active"),
            messages=[ChatMessage.from_dict(m) for m in d.get("messages", [])],
        )


class ChatManager:
    """Manages chat sessions for interactive plan creation.

    Each session is persisted as a JSON file under .claude-flow/chats/.
    """

    def __init__(self, project_root: Path, config: Config):
        self._root = project_root
        self._config = config
        self._chats_dir = project_root / ".claude-flow" / "chats"

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    def _session_path(self, task_id: str) -> Path:
        return self._chats_dir / f"{task_id}.json"

    def _load_session(self, task_id: str) -> Optional[ChatSession]:
        path = self._session_path(task_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return ChatSession.from_dict(data)

    def _save_session(self, session: ChatSession) -> None:
        self._chats_dir.mkdir(parents=True, exist_ok=True)
        path = self._session_path(session.task_id)
        path.write_text(
            json.dumps(session.to_dict(), indent=2, ensure_ascii=False)
        )

    def create_session(
        self, task_id: str, mode: str = "interactive"
    ) -> ChatSession:
        """Create a new chat session for a task."""
        session = ChatSession(task_id=task_id, mode=mode)
        self._save_session(session)
        return session

    def get_session(self, task_id: str) -> Optional[ChatSession]:
        """Retrieve an existing chat session."""
        return self._load_session(task_id)

    def delete_session(self, task_id: str) -> bool:
        """Delete a chat session file."""
        path = self._session_path(task_id)
        if path.exists():
            path.unlink()
            return True
        return False

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    def add_message(
        self, task_id: str, role: str, content: str
    ) -> Optional[ChatSession]:
        """Append a message to the session without calling AI."""
        session = self._load_session(task_id)
        if not session:
            return None
        session.messages.append(ChatMessage(role=role, content=content))
        self._save_session(session)
        return session

    def send_message(
        self, task_id: str, content: str, task_prompt: str = ""
    ) -> Optional[str]:
        """Send a user message, call Claude, return the AI response.

        The full conversation history is included as context in each call.
        Returns the AI response text, or None if session not found.
        """
        session = self._load_session(task_id)
        if not session or session.status != "active":
            return None

        # Record user message
        session.messages.append(ChatMessage(role="user", content=content))
        self._save_session(session)

        # Build prompt and call Claude
        prompt = self._build_prompt(session, task_prompt)
        cmd = ["claude", "-p", prompt, "--print", "--output-format", "text"]
        if can_skip_permissions(self._config.skip_permissions):
            cmd.append("--dangerously-skip-permissions")

        try:
            result = subprocess.run(
                cmd,
                cwd=str(self._root),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self._config.task_timeout,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            error_msg = f"Chat error: {e}"
            session.messages.append(
                ChatMessage(role="assistant", content=error_msg)
            )
            self._save_session(session)
            return error_msg

        if result.returncode != 0:
            error_msg = f"Chat error: {result.stderr.strip()}"
            session.messages.append(
                ChatMessage(role="assistant", content=error_msg)
            )
            self._save_session(session)
            return error_msg

        ai_response = result.stdout.strip()
        session.messages.append(
            ChatMessage(role="assistant", content=ai_response)
        )
        self._save_session(session)
        return ai_response

    def finalize(self, task_id: str) -> Optional[ChatSession]:
        """Mark the session as finalized (plan generated)."""
        session = self._load_session(task_id)
        if not session:
            return None
        session.status = "finalized"
        self._save_session(session)
        return session

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_prompt(
        self, session: ChatSession, task_prompt: str = ""
    ) -> str:
        """Build a claude prompt from the full conversation history."""
        parts: list[str] = []

        if task_prompt:
            parts.append(f"## Task Description\n{task_prompt}")
            parts.append("")

        parts.append("## Conversation History")
        parts.append("")

        for msg in session.messages:
            prefix = "User" if msg.role == "user" else "Assistant"
            parts.append(f"**{prefix}**: {msg.content}")
            parts.append("")

        parts.append(
            "Please continue the conversation by responding to the "
            "user's latest message. Focus on helping refine the "
            "implementation plan for this task."
        )
        return "\n".join(parts)

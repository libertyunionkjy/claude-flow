"""ChatSession model and ChatManager for interactive plan creation."""
from __future__ import annotations

import json
import subprocess
import threading
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
    thinking: bool = False  # True when AI is generating a response
    messages: List[ChatMessage] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "mode": self.mode,
            "status": self.status,
            "thinking": self.thinking,
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, d: dict) -> ChatSession:
        return cls(
            task_id=d["task_id"],
            mode=d.get("mode", "interactive"),
            status=d.get("status", "active"),
            thinking=d.get("thinking", False),
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
        # Track active background threads by task_id
        self._active_threads: dict[str, threading.Thread] = {}
        # Track active subprocess handles by task_id (for abort support)
        self._active_processes: dict[str, subprocess.Popen] = {}
        # Recover stale thinking states from previous process
        self._recover_stale_sessions()

    # ------------------------------------------------------------------
    # Startup recovery
    # ------------------------------------------------------------------

    def _recover_stale_sessions(self) -> None:
        """Reset thinking=True on sessions left over from a previous process.

        When the server restarts, daemon threads are lost but the JSON
        files still have thinking=True. This scans all chat files on
        startup and resets them, appending an error message so the user
        knows the previous request was interrupted.
        """
        if not self._chats_dir.exists():
            return
        for path in self._chats_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                if data.get("thinking"):
                    session = ChatSession.from_dict(data)
                    session.thinking = False
                    session.messages.append(
                        ChatMessage(
                            role="assistant",
                            content="[System] AI response was interrupted "
                            "(server restarted). Please resend your message.",
                        )
                    )
                    self._save_session(session)
            except (json.JSONDecodeError, KeyError):
                continue

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
        """Retrieve an existing chat session.

        Also checks for dead background threads: if thinking=True but
        the tracked thread is no longer alive, resets the thinking flag
        and appends an error message.
        """
        session = self._load_session(task_id)
        if session and session.thinking:
            thread = self._active_threads.get(task_id)
            if thread is None or not thread.is_alive():
                # Thread died or was never tracked — recover
                session.thinking = False
                session.messages.append(
                    ChatMessage(
                        role="assistant",
                        content="[System] AI response generation failed "
                        "(background process lost). Please resend.",
                    )
                )
                self._save_session(session)
                self._active_threads.pop(task_id, None)
        return session

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

    def send_initial_prompt(
        self, task_id: str, task_prompt: str
    ) -> Optional[str]:
        """Send the initial task prompt to AI for the first round of output.

        This is called when starting a planning session (interactive or auto)
        to let AI analyze the task prompt and produce the first response,
        without requiring user input first.

        Returns the AI response text, or None if session not found.
        """
        session = self._load_session(task_id)
        if not session or session.status != "active":
            return None

        # Build initial analysis prompt
        prompt = self._build_initial_prompt(task_prompt)
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

    # ------------------------------------------------------------------
    # Async messaging (non-blocking, for web API)
    # ------------------------------------------------------------------

    def send_message_async(
        self, task_id: str, content: str, task_prompt: str = ""
    ) -> bool:
        """Send a user message and start AI response generation in background.

        Records the user message immediately and sets thinking=True.
        The AI response is generated in a background thread, which updates
        the session when complete and sets thinking=False.

        Returns True if the message was accepted, False otherwise.
        """
        session = self._load_session(task_id)
        if not session or session.status != "active":
            return False
        if session.thinking:
            return False  # Already processing a message

        # Record user message and set thinking flag
        session.messages.append(ChatMessage(role="user", content=content))
        session.thinking = True
        self._save_session(session)

        # Build prompt and start background thread
        prompt = self._build_prompt(session, task_prompt)
        cmd = self._build_cmd(prompt)

        thread = threading.Thread(
            target=self._async_claude_call,
            args=(task_id, cmd),
            daemon=True,
        )
        self._active_threads[task_id] = thread
        thread.start()
        return True

    def send_initial_prompt_async(
        self, task_id: str, task_prompt: str
    ) -> bool:
        """Start initial AI analysis in background (non-blocking).

        Sets thinking=True and generates the initial AI response in a
        background thread. Returns True if accepted.
        """
        session = self._load_session(task_id)
        if not session or session.status != "active":
            return False
        if session.thinking:
            return False

        session.thinking = True
        self._save_session(session)

        prompt = self._build_initial_prompt(task_prompt)
        cmd = self._build_cmd(prompt)

        thread = threading.Thread(
            target=self._async_claude_call,
            args=(task_id, cmd),
            daemon=True,
        )
        self._active_threads[task_id] = thread
        thread.start()
        return True

    def _build_cmd(self, prompt: str) -> list[str]:
        """Build the claude CLI command list."""
        cmd = ["claude", "-p", prompt, "--print", "--output-format", "text"]
        if can_skip_permissions(self._config.skip_permissions):
            cmd.append("--dangerously-skip-permissions")
        return cmd

    def _async_claude_call(self, task_id: str, cmd: list[str]) -> None:
        """Execute claude CLI in background and update session with result.

        This runs in a daemon thread. On completion (success or error),
        it appends the AI response and clears the thinking flag.
        Uses Popen to allow aborting the subprocess via abort_session().
        """
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self._root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._active_processes[task_id] = proc
            try:
                stdout, stderr = proc.communicate(
                    timeout=self._config.task_timeout
                )
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                ai_response = f"Chat error: timed out after {self._config.task_timeout}s"
            else:
                if proc.returncode != 0:
                    ai_response = f"Chat error: {stderr.strip()}"
                else:
                    ai_response = stdout.strip()
        except OSError as e:
            ai_response = f"Chat error: {e}"

        # Clean up process reference
        self._active_processes.pop(task_id, None)

        # Update session: append response and clear thinking flag
        session = self._load_session(task_id)
        if session:
            session.messages.append(
                ChatMessage(role="assistant", content=ai_response)
            )
            session.thinking = False
            self._save_session(session)
        # Clean up thread reference
        self._active_threads.pop(task_id, None)

    def abort_session(self, task_id: str) -> bool:
        """Abort an active chat session, killing any running subprocess.

        Used when deleting a task that is in planning state with an active
        chat or AI generation. Kills the subprocess, cleans up thread/process
        references, and removes the session file.

        Returns True if a session was found and cleaned up.
        """
        # Kill active subprocess if any
        proc = self._active_processes.pop(task_id, None)
        if proc and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

        # Clean up thread reference
        self._active_threads.pop(task_id, None)

        # Delete session file
        return self.delete_session(task_id)

    def finalize(self, task_id: str) -> Optional[ChatSession]:
        """Mark the session as finalized (plan generated)."""
        session = self._load_session(task_id)
        if not session:
            return None
        session.status = "finalized"
        session.thinking = False
        self._save_session(session)
        return session

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_initial_prompt(self, task_prompt: str) -> str:
        """Build the initial prompt for first-round AI analysis.

        Used when starting a planning session to let AI analyze the task
        requirements and produce initial thoughts/questions.
        """
        parts: list[str] = [
            "## Task Description",
            task_prompt,
            "",
            "Please analyze this task and provide:",
            "1. Your understanding of the requirements",
            "2. Key considerations and potential challenges",
            "3. Initial thoughts on the implementation approach",
            "4. Questions for clarification (if any)",
            "",
            "Focus on helping the user refine the implementation plan.",
        ]
        return "\n".join(parts)

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

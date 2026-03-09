"""Tests for ChatSession model and ChatManager."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_flow.chat import ChatManager, ChatMessage, ChatSession
from claude_flow.config import Config


@pytest.fixture
def chat_dir(tmp_path):
    """Create a project root with .claude-flow/chats/ directory."""
    cf_dir = tmp_path / ".claude-flow"
    cf_dir.mkdir()
    return tmp_path


@pytest.fixture
def chat_mgr(chat_dir):
    """Create a ChatManager with default config."""
    cfg = Config()
    return ChatManager(chat_dir, cfg)


# -- ChatMessage / ChatSession model tests -----------------------------------


class TestChatModels:
    def test_chat_message_serialization(self):
        msg = ChatMessage(role="user", content="Hello", timestamp="2026-03-09T10:00:00")
        d = msg.to_dict()
        assert d["role"] == "user"
        assert d["content"] == "Hello"
        restored = ChatMessage.from_dict(d)
        assert restored.role == "user"
        assert restored.content == "Hello"

    def test_chat_session_serialization(self):
        session = ChatSession(
            task_id="task-abc123",
            mode="interactive",
            status="active",
            messages=[
                ChatMessage(role="user", content="Hi"),
                ChatMessage(role="assistant", content="Hello!"),
            ],
        )
        d = session.to_dict()
        assert d["task_id"] == "task-abc123"
        assert len(d["messages"]) == 2

        restored = ChatSession.from_dict(d)
        assert restored.task_id == "task-abc123"
        assert restored.mode == "interactive"
        assert len(restored.messages) == 2
        assert restored.messages[0].role == "user"


# -- ChatManager tests -------------------------------------------------------


class TestChatManager:
    def test_create_session(self, chat_mgr):
        session = chat_mgr.create_session("task-001", mode="interactive")
        assert session.task_id == "task-001"
        assert session.mode == "interactive"
        assert session.status == "active"
        assert session.messages == []

    def test_get_session(self, chat_mgr):
        chat_mgr.create_session("task-002")
        session = chat_mgr.get_session("task-002")
        assert session is not None
        assert session.task_id == "task-002"

    def test_get_session_not_found(self, chat_mgr):
        assert chat_mgr.get_session("nonexistent") is None

    def test_delete_session(self, chat_mgr):
        chat_mgr.create_session("task-003")
        assert chat_mgr.delete_session("task-003") is True
        assert chat_mgr.get_session("task-003") is None

    def test_delete_session_not_found(self, chat_mgr):
        assert chat_mgr.delete_session("nonexistent") is False

    def test_add_message(self, chat_mgr):
        chat_mgr.create_session("task-004")
        session = chat_mgr.add_message("task-004", "user", "Hello")
        assert len(session.messages) == 1
        assert session.messages[0].role == "user"
        assert session.messages[0].content == "Hello"

    def test_add_message_no_session(self, chat_mgr):
        assert chat_mgr.add_message("nonexistent", "user", "Hi") is None

    def test_send_message(self, chat_mgr):
        chat_mgr.create_session("task-005")

        with patch("claude_flow.chat.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="AI says hello", stderr=""
            )
            response = chat_mgr.send_message("task-005", "Hello AI")

        assert response == "AI says hello"
        session = chat_mgr.get_session("task-005")
        assert len(session.messages) == 2
        assert session.messages[0].role == "user"
        assert session.messages[1].role == "assistant"
        assert session.messages[1].content == "AI says hello"

    def test_send_message_with_task_prompt(self, chat_mgr):
        chat_mgr.create_session("task-006")

        with patch("claude_flow.chat.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Got it", stderr=""
            )
            response = chat_mgr.send_message(
                "task-006", "How?", task_prompt="Fix the login bug"
            )
            # Verify the prompt includes the task description
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            prompt = cmd[2]  # claude -p <prompt>
            assert "Fix the login bug" in prompt

        assert response == "Got it"

    def test_send_message_error(self, chat_mgr):
        chat_mgr.create_session("task-007")

        with patch("claude_flow.chat.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Some error"
            )
            response = chat_mgr.send_message("task-007", "Hello")

        assert "Chat error" in response
        session = chat_mgr.get_session("task-007")
        assert len(session.messages) == 2  # user + error response

    def test_send_message_finalized_session(self, chat_mgr):
        chat_mgr.create_session("task-008")
        chat_mgr.finalize("task-008")

        response = chat_mgr.send_message("task-008", "Hello")
        assert response is None

    def test_finalize(self, chat_mgr):
        chat_mgr.create_session("task-009")
        chat_mgr.add_message("task-009", "user", "Plan this")
        session = chat_mgr.finalize("task-009")
        assert session.status == "finalized"

        # Verify persistence
        reloaded = chat_mgr.get_session("task-009")
        assert reloaded.status == "finalized"

    def test_finalize_not_found(self, chat_mgr):
        assert chat_mgr.finalize("nonexistent") is None

    def test_send_initial_prompt(self, chat_mgr):
        chat_mgr.create_session("task-011")

        with patch("claude_flow.chat.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Task analysis: looks good", stderr=""
            )
            response = chat_mgr.send_initial_prompt(
                "task-011", "Build a REST API for users"
            )

        assert response == "Task analysis: looks good"
        session = chat_mgr.get_session("task-011")
        # Only assistant message (no user message in initial prompt)
        assert len(session.messages) == 1
        assert session.messages[0].role == "assistant"
        assert session.messages[0].content == "Task analysis: looks good"

    def test_send_initial_prompt_includes_task_description(self, chat_mgr):
        chat_mgr.create_session("task-012")

        with patch("claude_flow.chat.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Analysis done", stderr=""
            )
            chat_mgr.send_initial_prompt("task-012", "Fix the login bug")
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            prompt = cmd[2]  # claude -p <prompt>
            assert "Fix the login bug" in prompt

    def test_send_initial_prompt_no_session(self, chat_mgr):
        response = chat_mgr.send_initial_prompt("nonexistent", "Some prompt")
        assert response is None

    def test_send_initial_prompt_finalized(self, chat_mgr):
        chat_mgr.create_session("task-013")
        chat_mgr.finalize("task-013")
        response = chat_mgr.send_initial_prompt("task-013", "Some prompt")
        assert response is None

    def test_send_initial_prompt_error(self, chat_mgr):
        chat_mgr.create_session("task-014")

        with patch("claude_flow.chat.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Some error"
            )
            response = chat_mgr.send_initial_prompt("task-014", "Do task")

        assert "Chat error" in response
        session = chat_mgr.get_session("task-014")
        assert len(session.messages) == 1
        assert session.messages[0].role == "assistant"

    def test_build_initial_prompt(self, chat_mgr):
        prompt = chat_mgr._build_initial_prompt("Build a REST API")
        assert "Build a REST API" in prompt
        assert "Task Description" in prompt
        assert "requirements" in prompt.lower()

    def test_build_prompt(self, chat_mgr):
        session = ChatSession(
            task_id="task-010",
            messages=[
                ChatMessage(role="user", content="Hello"),
                ChatMessage(role="assistant", content="Hi there"),
                ChatMessage(role="user", content="Plan this"),
            ],
        )
        prompt = chat_mgr._build_prompt(session, task_prompt="Build a REST API")
        assert "Build a REST API" in prompt
        assert "Hello" in prompt
        assert "Hi there" in prompt
        assert "Plan this" in prompt

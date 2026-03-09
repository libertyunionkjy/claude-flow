"""Tests for ChatSession model and ChatManager."""
from __future__ import annotations

import subprocess
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
        assert d["thinking"] is False

        restored = ChatSession.from_dict(d)
        assert restored.task_id == "task-abc123"
        assert restored.mode == "interactive"
        assert restored.thinking is False
        assert len(restored.messages) == 2
        assert restored.messages[0].role == "user"

    def test_chat_session_thinking_flag(self):
        session = ChatSession(task_id="task-t1", thinking=True)
        d = session.to_dict()
        assert d["thinking"] is True

        restored = ChatSession.from_dict(d)
        assert restored.thinking is True

    def test_chat_session_thinking_default_false(self):
        """thinking defaults to False when not present in dict (backward compat)."""
        d = {"task_id": "task-old", "mode": "interactive", "status": "active", "messages": []}
        restored = ChatSession.from_dict(d)
        assert restored.thinking is False


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

    def test_send_message_async(self, chat_mgr):
        """send_message_async records user message, sets thinking=True, returns True."""
        chat_mgr.create_session("task-async-1")

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("Async AI response", "")
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0

        with patch("claude_flow.chat.subprocess.Popen", return_value=mock_proc):
            accepted = chat_mgr.send_message_async("task-async-1", "Hello async")

            assert accepted is True
            # Wait for background thread to complete
            import time
            time.sleep(0.5)

        session = chat_mgr.get_session("task-async-1")
        assert len(session.messages) == 2  # user + assistant
        assert session.messages[0].role == "user"
        assert session.messages[0].content == "Hello async"
        assert session.messages[1].role == "assistant"
        assert session.messages[1].content == "Async AI response"
        assert session.thinking is False

    def test_send_message_async_rejects_when_thinking(self, chat_mgr):
        """send_message_async rejects if session is already thinking."""
        chat_mgr.create_session("task-async-2")

        # Manually set thinking=True
        session = chat_mgr.get_session("task-async-2")
        session.thinking = True
        chat_mgr._save_session(session)

        accepted = chat_mgr.send_message_async("task-async-2", "Another message")
        assert accepted is False

    def test_send_message_async_no_session(self, chat_mgr):
        """send_message_async returns False for nonexistent session."""
        accepted = chat_mgr.send_message_async("nonexistent", "Hello")
        assert accepted is False

    def test_send_initial_prompt_async(self, chat_mgr):
        """send_initial_prompt_async starts background AI analysis."""
        chat_mgr.create_session("task-async-3")

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("Initial analysis complete", "")
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0

        with patch("claude_flow.chat.subprocess.Popen", return_value=mock_proc):
            accepted = chat_mgr.send_initial_prompt_async(
                "task-async-3", "Build a REST API"
            )

            assert accepted is True
            import time
            time.sleep(0.5)  # Wait for background thread

        session = chat_mgr.get_session("task-async-3")
        assert len(session.messages) == 1
        assert session.messages[0].role == "assistant"
        assert session.messages[0].content == "Initial analysis complete"
        assert session.thinking is False

    def test_send_initial_prompt_async_rejects_when_thinking(self, chat_mgr):
        """send_initial_prompt_async rejects if already thinking."""
        chat_mgr.create_session("task-async-4")
        session = chat_mgr.get_session("task-async-4")
        session.thinking = True
        chat_mgr._save_session(session)

        accepted = chat_mgr.send_initial_prompt_async("task-async-4", "Do something")
        assert accepted is False

    def test_async_claude_call_error(self, chat_mgr):
        """_async_claude_call handles subprocess errors gracefully."""
        chat_mgr.create_session("task-async-5")

        with patch("claude_flow.chat.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Some error"
            )
            chat_mgr._async_claude_call(
                "task-async-5",
                ["claude", "-p", "test", "--print", "--output-format", "text"],
            )

        session = chat_mgr.get_session("task-async-5")
        assert len(session.messages) == 1
        assert "Chat error" in session.messages[0].content
        assert session.thinking is False

    def test_async_claude_call_timeout(self, chat_mgr):
        """_async_claude_call handles timeout gracefully."""
        chat_mgr.create_session("task-async-6")

        with patch("claude_flow.chat.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=600)
            chat_mgr._async_claude_call(
                "task-async-6",
                ["claude", "-p", "test", "--print", "--output-format", "text"],
            )

        session = chat_mgr.get_session("task-async-6")
        assert len(session.messages) == 1
        assert "Chat error" in session.messages[0].content
        assert session.thinking is False

    def test_finalize_clears_thinking(self, chat_mgr):
        """finalize should clear thinking flag."""
        chat_mgr.create_session("task-fin-1")
        session = chat_mgr.get_session("task-fin-1")
        session.thinking = True
        chat_mgr._save_session(session)

        finalized = chat_mgr.finalize("task-fin-1")
        assert finalized.status == "finalized"
        assert finalized.thinking is False

    def test_create_session_from_plan(self, chat_mgr):
        """create_session_from_plan injects plan content as assistant's first message."""
        plan_content = "# Implementation Plan\n\n1. Step one\n2. Step two"
        session = chat_mgr.create_session_from_plan("task-plan-1", plan_content)

        assert session.task_id == "task-plan-1"
        assert session.mode == "interactive"
        assert session.status == "active"
        assert len(session.messages) == 1
        assert session.messages[0].role == "assistant"
        assert session.messages[0].content == plan_content

        # Verify persistence
        reloaded = chat_mgr.get_session("task-plan-1")
        assert len(reloaded.messages) == 1
        assert reloaded.messages[0].content == plan_content

    def test_create_session_from_plan_preserves_chat_flow(self, chat_mgr):
        """After create_session_from_plan, subsequent messages include plan context."""
        plan_content = "Plan: build REST API"
        chat_mgr.create_session_from_plan("task-plan-2", plan_content)

        # Add a user message
        session = chat_mgr.add_message("task-plan-2", "user", "How about auth?")
        assert len(session.messages) == 2
        assert session.messages[0].role == "assistant"
        assert session.messages[0].content == plan_content
        assert session.messages[1].role == "user"

        # Build prompt should include both plan and user message
        prompt = chat_mgr._build_prompt(session, task_prompt="Build API")
        assert plan_content in prompt
        assert "How about auth?" in prompt

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

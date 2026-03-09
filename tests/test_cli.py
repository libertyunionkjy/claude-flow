from pathlib import Path
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from claude_flow.cli import main
from claude_flow.task_manager import TaskManager
from claude_flow.models import TaskStatus
from claude_flow.chat import ChatManager
from claude_flow.config import Config


class TestCLI:
    def test_init(self, git_repo):
        runner = CliRunner()
        result = runner.invoke(main, ["init"], catch_exceptions=False, env={"CF_PROJECT_ROOT": str(git_repo)})
        assert result.exit_code == 0
        assert (git_repo / ".claude-flow").is_dir()
        assert (git_repo / ".claude-flow" / "config.json").exists()

    def test_task_add(self, git_repo):
        (git_repo / ".claude-flow").mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main, ["task", "add", "-p", "Do something", "My Task"],
            catch_exceptions=False, env={"CF_PROJECT_ROOT": str(git_repo)},
        )
        assert result.exit_code == 0
        assert "My Task" in result.output

    def test_task_list_empty(self, git_repo):
        (git_repo / ".claude-flow").mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main, ["task", "list"],
            catch_exceptions=False, env={"CF_PROJECT_ROOT": str(git_repo)},
        )
        assert result.exit_code == 0

    def test_status(self, git_repo):
        (git_repo / ".claude-flow").mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main, ["status"],
            catch_exceptions=False, env={"CF_PROJECT_ROOT": str(git_repo)},
        )
        assert result.exit_code == 0


class TestPlanBackground:
    """Tests for background plan execution."""

    def _add_task(self, git_repo):
        """Add a pending task and return its ID."""
        (git_repo / ".claude-flow").mkdir(exist_ok=True)
        tm = TaskManager(git_repo)
        t = tm.add("Test Task", "Do something")
        return t.id

    def test_plan_defaults_to_background(self, git_repo):
        """cf plan (no flags) forks a background process."""
        task_id = self._add_task(git_repo)
        runner = CliRunner()

        with patch("os.fork", return_value=12345):
            result = runner.invoke(
                main, ["plan"],
                catch_exceptions=False,
                env={"CF_PROJECT_ROOT": str(git_repo)},
            )

        assert result.exit_code == 0
        assert "background" in result.output.lower()
        assert "12345" in result.output

        # Task should be set to PLANNING before fork returns
        tm = TaskManager(git_repo)
        t = tm.get(task_id)
        assert t.status == TaskStatus.PLANNING

    def test_plan_foreground_flag(self, git_repo):
        """cf plan -F runs in foreground (blocking)."""
        task_id = self._add_task(git_repo)
        runner = CliRunner()

        with patch("claude_flow.planner.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("# Plan\n1. Do X", "")
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc

            result = runner.invoke(
                main, ["plan", "-F"],
                catch_exceptions=False,
                env={"CF_PROJECT_ROOT": str(git_repo)},
            )

        assert result.exit_code == 0
        assert "Plan saved to" in result.output

        tm = TaskManager(git_repo)
        t = tm.get(task_id)
        assert t.status == TaskStatus.PLANNED

    def test_plan_foreground_with_specific_task(self, git_repo):
        """cf plan -F -t <task_id> plans a specific task in foreground."""
        task_id = self._add_task(git_repo)
        runner = CliRunner()

        with patch("claude_flow.planner.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("# Plan\nStep 1", "")
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc

            result = runner.invoke(
                main, ["plan", "-F", "-t", task_id],
                catch_exceptions=False,
                env={"CF_PROJECT_ROOT": str(git_repo)},
            )

        assert result.exit_code == 0
        assert "Plan saved to" in result.output

    def test_plan_no_pending_tasks(self, git_repo):
        """cf plan with no pending tasks shows message."""
        (git_repo / ".claude-flow").mkdir(exist_ok=True)
        runner = CliRunner()

        result = runner.invoke(
            main, ["plan"],
            catch_exceptions=False,
            env={"CF_PROJECT_ROOT": str(git_repo)},
        )

        assert result.exit_code == 0
        assert "No pending tasks" in result.output

    def test_plan_background_sets_planning_status(self, git_repo):
        """Background mode sets all tasks to PLANNING before forking."""
        (git_repo / ".claude-flow").mkdir(exist_ok=True)
        tm = TaskManager(git_repo)
        t1 = tm.add("Task 1", "Prompt 1")
        t2 = tm.add("Task 2", "Prompt 2")

        runner = CliRunner()
        with patch("os.fork", return_value=99999):
            result = runner.invoke(
                main, ["plan"],
                catch_exceptions=False,
                env={"CF_PROJECT_ROOT": str(git_repo)},
            )

        assert result.exit_code == 0

        tm2 = TaskManager(git_repo)
        assert tm2.get(t1.id).status == TaskStatus.PLANNING
        assert tm2.get(t2.id).status == TaskStatus.PLANNING


class TestPlanStatus:
    """Tests for cf plan status subcommand."""

    def test_plan_status_empty(self, git_repo):
        """plan status with no plans shows appropriate message."""
        (git_repo / ".claude-flow").mkdir(exist_ok=True)
        runner = CliRunner()

        result = runner.invoke(
            main, ["plan", "status"],
            catch_exceptions=False,
            env={"CF_PROJECT_ROOT": str(git_repo)},
        )

        assert result.exit_code == 0
        assert "No plans in progress" in result.output

    def test_plan_status_shows_planning(self, git_repo):
        """plan status shows tasks in PLANNING state."""
        (git_repo / ".claude-flow").mkdir(exist_ok=True)
        tm = TaskManager(git_repo)
        t = tm.add("Planning Task", "Prompt")
        tm.update_status(t.id, TaskStatus.PLANNING)

        runner = CliRunner()
        result = runner.invoke(
            main, ["plan", "status"],
            catch_exceptions=False,
            env={"CF_PROJECT_ROOT": str(git_repo)},
        )

        assert result.exit_code == 0
        assert "In progress" in result.output
        assert t.id in result.output

    def test_plan_status_shows_planned(self, git_repo):
        """plan status shows tasks in PLANNED state."""
        (git_repo / ".claude-flow").mkdir(exist_ok=True)
        tm = TaskManager(git_repo)
        t = tm.add("Planned Task", "Prompt")
        tm.update_status(t.id, TaskStatus.PLANNED)

        runner = CliRunner()
        result = runner.invoke(
            main, ["plan", "status"],
            catch_exceptions=False,
            env={"CF_PROJECT_ROOT": str(git_repo)},
        )

        assert result.exit_code == 0
        assert "Ready for review" in result.output
        assert t.id in result.output

    def test_plan_status_shows_log_tail(self, git_repo):
        """plan status shows recent log lines if log file exists."""
        (git_repo / ".claude-flow" / "logs").mkdir(parents=True, exist_ok=True)
        log_file = git_repo / ".claude-flow" / "logs" / "plan-bg.log"
        log_file.write_text("[2026-03-06T10:00:00] Background planning started\n"
                           "[2026-03-06T10:00:01] Planning: task-abc123\n")

        runner = CliRunner()
        result = runner.invoke(
            main, ["plan", "status"],
            catch_exceptions=False,
            env={"CF_PROJECT_ROOT": str(git_repo)},
        )

        assert result.exit_code == 0
        assert "Recent log" in result.output
        assert "Background planning started" in result.output


class TestPlanInteractive:
    """Tests for interactive plan mode with initial AI output."""

    def _add_task(self, git_repo, prompt="Do something complex"):
        """Add a pending task and return its ID."""
        (git_repo / ".claude-flow").mkdir(exist_ok=True)
        tm = TaskManager(git_repo)
        t = tm.add("Test Task", prompt)
        return t.id

    def test_interactive_plan_triggers_initial_prompt(self, git_repo):
        """cf plan -i -t <id> should trigger initial AI analysis."""
        task_id = self._add_task(git_repo, "Build a REST API for users")
        runner = CliRunner()

        with patch("claude_flow.chat.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="I'll analyze this task...", stderr=""
            )
            result = runner.invoke(
                main, ["plan", "-i", "-t", task_id],
                catch_exceptions=False,
                env={"CF_PROJECT_ROOT": str(git_repo)},
            )

        assert result.exit_code == 0
        assert "AI is analyzing" in result.output
        assert "I'll analyze this task..." in result.output
        assert "Waiting for your input" in result.output

        # Verify chat session has the initial AI message
        cfg = Config()
        chat_mgr = ChatManager(git_repo, cfg)
        session = chat_mgr.get_session(task_id)
        assert session is not None
        assert len(session.messages) == 1
        assert session.messages[0].role == "assistant"

    def test_interactive_plan_shows_task_prompt(self, git_repo):
        """cf plan -i -t <id> should display the task prompt."""
        task_id = self._add_task(git_repo, "Fix the authentication bug")
        runner = CliRunner()

        with patch("claude_flow.chat.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Analyzing...", stderr=""
            )
            result = runner.invoke(
                main, ["plan", "-i", "-t", task_id],
                catch_exceptions=False,
                env={"CF_PROJECT_ROOT": str(git_repo)},
            )

        assert result.exit_code == 0
        assert "Fix the authentication bug" in result.output

    def test_foreground_plan_shows_status_indicators(self, git_repo):
        """cf plan -F shows AI generation status indicators."""
        task_id = self._add_task(git_repo)
        runner = CliRunner()

        with patch("claude_flow.planner.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("# Plan\n1. Step one", "")
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc

            result = runner.invoke(
                main, ["plan", "-F"],
                catch_exceptions=False,
                env={"CF_PROJECT_ROOT": str(git_repo)},
            )

        assert result.exit_code == 0
        assert "AI is generating plan" in result.output
        assert "AI generation complete" in result.output

from pathlib import Path
from unittest.mock import patch, MagicMock
from claude_flow.planner import Planner
from claude_flow.models import Task, TaskStatus
from claude_flow.config import Config


class TestPlanner:
    def _make_planner(self, tmp_path: Path) -> Planner:
        plans_dir = tmp_path / ".claude-flow" / "plans"
        plans_dir.mkdir(parents=True)
        cfg = Config()
        return Planner(tmp_path, plans_dir, cfg)

    @patch("claude_flow.planner.subprocess.Popen")
    def test_generate_plan(self, mock_popen, tmp_path):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("# Plan\n1. Step one\n2. Step two", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        planner = self._make_planner(tmp_path)
        task = Task(title="Test", prompt="Implement feature X")
        plan_file = planner.generate(task)
        assert plan_file.exists()
        assert "Step one" in plan_file.read_text()
        assert task.status == TaskStatus.PLANNED
        assert task.plan_file == str(plan_file)

    @patch("claude_flow.planner.subprocess.Popen")
    def test_generate_plan_failure(self, mock_popen, tmp_path):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("", "error")
        mock_proc.returncode = 1
        mock_popen.return_value = mock_proc
        planner = self._make_planner(tmp_path)
        task = Task(title="Test", prompt="Bad task")
        plan_file = planner.generate(task)
        assert plan_file is None
        assert task.status == TaskStatus.FAILED

    def test_read_plan(self, tmp_path):
        planner = self._make_planner(tmp_path)
        plan_path = tmp_path / ".claude-flow" / "plans" / "task-001.md"
        plan_path.write_text("# My Plan\nDo stuff")
        content = planner.read_plan(plan_path)
        assert "My Plan" in content

    def test_approve(self, tmp_path):
        planner = self._make_planner(tmp_path)
        task = Task(title="Test", prompt="P")
        task.status = TaskStatus.PLANNED
        planner.approve(task)
        assert task.status == TaskStatus.APPROVED

    def test_reject_removed(self, tmp_path):
        """reject() method should no longer exist on Planner."""
        planner = self._make_planner(tmp_path)
        assert not hasattr(planner, 'reject')

    @patch("claude_flow.planner.subprocess.Popen")
    def test_generate_includes_allowed_tools(self, mock_popen, tmp_path):
        """generate() passes --allowedTools when plan_allowed_tools is set."""
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("# Plan", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        plans_dir = tmp_path / ".claude-flow" / "plans"
        plans_dir.mkdir(parents=True)
        cfg = Config(plan_allowed_tools=["Read", "Glob", "Grep"])
        planner = Planner(tmp_path, plans_dir, cfg)

        task = Task(title="Test", prompt="Analyze X")
        planner.generate(task)

        cmd = mock_popen.call_args[0][0]
        assert "--allowedTools" in cmd
        idx = cmd.index("--allowedTools")
        disallow_idx = cmd.index("--disallowedTools")
        assert cmd[idx + 1 : disallow_idx] == ["Read", "Glob", "Grep"]
        # Verify disallowed tools are always present
        disallowed = cmd[disallow_idx + 1 :]
        assert "Write" in disallowed
        assert "Edit" in disallowed
        assert "Bash" in disallowed

    @patch("claude_flow.planner.subprocess.Popen")
    def test_generate_no_restriction_when_empty(self, mock_popen, tmp_path):
        """generate() omits --allowedTools when plan_allowed_tools is empty."""
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("# Plan", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        plans_dir = tmp_path / ".claude-flow" / "plans"
        plans_dir.mkdir(parents=True)
        cfg = Config(plan_allowed_tools=[])
        planner = Planner(tmp_path, plans_dir, cfg)

        task = Task(title="Test", prompt="Analyze X")
        planner.generate(task)

        cmd = mock_popen.call_args[0][0]
        assert "--allowedTools" not in cmd
        # --disallowedTools should still be present
        assert "--disallowedTools" in cmd

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

    @patch("claude_flow.planner.can_skip_permissions", return_value=True)
    @patch("claude_flow.planner.subprocess.Popen")
    def test_generate_includes_allowed_tools(self, mock_popen, mock_can_skip, tmp_path):
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
        # --permission-mode should NOT be present when skip_permissions works
        assert "--permission-mode" not in cmd

    @patch("claude_flow.planner.can_skip_permissions", return_value=True)
    @patch("claude_flow.planner.subprocess.Popen")
    def test_generate_falls_back_to_default_tools_when_empty(
        self, mock_popen, mock_can_skip, tmp_path
    ):
        """generate() uses default read-only tools when plan_allowed_tools is empty."""
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
        # Fallback: always includes --allowedTools with defaults
        assert "--allowedTools" in cmd
        idx = cmd.index("--allowedTools")
        disallow_idx = cmd.index("--disallowedTools")
        assert cmd[idx + 1 : disallow_idx] == ["Read", "Glob", "Grep"]
        # --disallowedTools should still be present
        assert "--disallowedTools" in cmd

    @patch("claude_flow.planner.can_skip_permissions", return_value=False)
    @patch("claude_flow.planner.subprocess.Popen")
    def test_generate_uses_permission_mode_plan_when_no_skip(
        self, mock_popen, mock_can_skip, tmp_path
    ):
        """When --dangerously-skip-permissions is unavailable (e.g. root),
        falls back to --permission-mode plan for auto-authorizing read tools."""
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("# Plan", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        plans_dir = tmp_path / ".claude-flow" / "plans"
        plans_dir.mkdir(parents=True)
        cfg = Config(skip_permissions=True, plan_allowed_tools=["Read", "Glob", "Grep"])
        planner = Planner(tmp_path, plans_dir, cfg)

        task = Task(title="Test", prompt="Analyze X")
        planner.generate(task)

        cmd = mock_popen.call_args[0][0]
        assert "--dangerously-skip-permissions" not in cmd
        assert "--permission-mode" in cmd
        pm_idx = cmd.index("--permission-mode")
        assert cmd[pm_idx + 1] == "plan"
        # --allowedTools should still be present
        assert "--allowedTools" in cmd

    @patch("claude_flow.planner.can_skip_permissions", return_value=True)
    @patch("claude_flow.planner.subprocess.Popen")
    def test_generate_no_permission_mode_when_skip_available(
        self, mock_popen, mock_can_skip, tmp_path
    ):
        """When --dangerously-skip-permissions is available,
        --permission-mode should NOT be added."""
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("# Plan", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        plans_dir = tmp_path / ".claude-flow" / "plans"
        plans_dir.mkdir(parents=True)
        cfg = Config(skip_permissions=True)
        planner = Planner(tmp_path, plans_dir, cfg)

        task = Task(title="Test", prompt="Analyze X")
        planner.generate(task)

        cmd = mock_popen.call_args[0][0]
        assert "--dangerously-skip-permissions" in cmd
        assert "--permission-mode" not in cmd

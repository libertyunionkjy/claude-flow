import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from claude_flow.config import Config
from claude_flow.usage import (
    UsageManager,
    format_session_table,
    format_daily_table,
    format_summary,
    _format_number,
    _format_cost,
)


# -- Fixtures ---------------------------------------------------------------

@pytest.fixture
def usage_project(tmp_path: Path) -> Path:
    """Create a project with .claude-flow/logs/ structure."""
    logs_dir = tmp_path / ".claude-flow" / "logs"
    logs_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def usage_mgr(usage_project: Path) -> UsageManager:
    """Create a UsageManager with ccusage disabled (fallback mode)."""
    return UsageManager(usage_project, Config())


def _write_log(project: Path, task_id: str, result_data: dict) -> None:
    """Write a stream-json log file with a result event."""
    log_file = project / ".claude-flow" / "logs" / f"{task_id}.log"
    lines = [
        json.dumps({"type": "system", "message": "init"}),
        json.dumps({"type": "assistant", "message": "working..."}),
        json.dumps(result_data),
    ]
    log_file.write_text("\n".join(lines))


# -- UsageManager unit tests ------------------------------------------------

class TestUsageManager:

    def test_fallback_from_logs_empty(self, usage_mgr: UsageManager):
        """No logs -> empty result."""
        result = usage_mgr._fallback_from_logs()
        assert result == []

    def test_fallback_from_logs_single_task(self, usage_project: Path):
        """Parse a single task log with result event."""
        _write_log(usage_project, "task-abc123", {
            "type": "result",
            "result": "done",
            "total_cost_usd": 0.938,
            "usage": {
                "input_tokens": 3677,
                "output_tokens": 6376,
                "cache_creation_input_tokens": 58687,
                "cache_read_input_tokens": 785931,
            },
            "modelUsage": {
                "Claude-Opus-4.6": {
                    "inputTokens": 3677,
                    "outputTokens": 6376,
                }
            },
        })

        mgr = UsageManager(usage_project, Config())
        results = mgr._fallback_from_logs()

        assert len(results) == 1
        r = results[0]
        assert r["task_id"] == "task-abc123"
        assert r["input_tokens"] == 3677
        assert r["output_tokens"] == 6376
        assert r["cache_creation_input_tokens"] == 58687
        assert r["cache_read_input_tokens"] == 785931
        assert r["cost_usd"] == 0.938
        assert r["total_tokens"] == 3677 + 6376 + 58687 + 785931
        assert "Claude-Opus-4.6" in r["models"]
        assert r["source"] == "logs"

    def test_fallback_from_logs_multiple_tasks(self, usage_project: Path):
        """Parse multiple task logs."""
        _write_log(usage_project, "task-aaa111", {
            "type": "result",
            "total_cost_usd": 1.0,
            "usage": {"input_tokens": 100, "output_tokens": 200,
                      "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0},
            "modelUsage": {},
        })
        _write_log(usage_project, "task-bbb222", {
            "type": "result",
            "total_cost_usd": 2.0,
            "usage": {"input_tokens": 300, "output_tokens": 400,
                      "cache_creation_input_tokens": 50,
                      "cache_read_input_tokens": 100},
            "modelUsage": {},
        })

        mgr = UsageManager(usage_project, Config())
        results = mgr._fallback_from_logs()
        assert len(results) == 2

        task_ids = {r["task_id"] for r in results}
        assert task_ids == {"task-aaa111", "task-bbb222"}

    def test_fallback_ignores_non_result_lines(self, usage_project: Path):
        """Log files without result events produce no output."""
        log_file = usage_project / ".claude-flow" / "logs" / "task-ccc333.log"
        log_file.write_text(json.dumps({"type": "system", "message": "init"}) + "\n")

        mgr = UsageManager(usage_project, Config())
        results = mgr._fallback_from_logs()
        assert len(results) == 0

    def test_fallback_handles_malformed_json(self, usage_project: Path):
        """Malformed JSON lines are skipped gracefully."""
        log_file = usage_project / ".claude-flow" / "logs" / "task-ddd444.log"
        log_file.write_text("not valid json\n{broken\n")

        mgr = UsageManager(usage_project, Config())
        results = mgr._fallback_from_logs()
        assert len(results) == 0

    def test_build_task_session_map(self, usage_project: Path):
        """Build session -> task mapping from logs."""
        _write_log(usage_project, "task-eee555", {
            "type": "result",
            "session_id": "sess-123-456",
            "total_cost_usd": 0.5,
            "usage": {"input_tokens": 10, "output_tokens": 20,
                      "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0},
        })

        mgr = UsageManager(usage_project, Config())
        mapping = mgr._build_task_session_map()
        assert mapping == {"sess-123-456": "task-eee555"}

    def test_enrich_with_tasks_session_id(self, usage_project: Path):
        """Enrich sessions using session_id mapping."""
        _write_log(usage_project, "task-fff666", {
            "type": "result",
            "session_id": "sid-abc",
            "total_cost_usd": 1.0,
            "usage": {"input_tokens": 0, "output_tokens": 0,
                      "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0},
        })

        mgr = UsageManager(usage_project, Config())
        sessions = [{"sessionId": "sid-abc", "input_tokens": 100}]
        enriched = mgr._enrich_with_tasks(sessions)
        assert enriched[0]["task_id"] == "task-fff666"

    def test_enrich_with_tasks_project_path(self, usage_project: Path):
        """Enrich sessions by extracting task ID from project path."""
        mgr = UsageManager(usage_project, Config())
        sessions = [{
            "sessionId": "unknown",
            "projectPath": "/opt/proj/.claude-flow/worktrees/task-abc123",
        }]
        enriched = mgr._enrich_with_tasks(sessions)
        assert enriched[0]["task_id"] == "task-abc123"

    def test_enrich_with_tasks_no_match(self, usage_project: Path):
        """Sessions without matching task get task_id=None."""
        mgr = UsageManager(usage_project, Config())
        sessions = [{"sessionId": "unknown", "projectPath": "/some/other/path"}]
        enriched = mgr._enrich_with_tasks(sessions)
        assert enriched[0]["task_id"] is None

    def test_get_project_filter(self, usage_project: Path):
        """Project filter should be the string representation of project root."""
        mgr = UsageManager(usage_project, Config())
        assert mgr._get_project_filter() == str(usage_project)

    @patch("claude_flow.usage.shutil.which", return_value=None)
    def test_check_ccusage_no_npx(self, mock_which, usage_mgr: UsageManager):
        """ccusage check fails when npx is not available."""
        assert usage_mgr._check_ccusage() is False

    @patch("claude_flow.usage.subprocess.run")
    @patch("claude_flow.usage.shutil.which", return_value="/usr/bin/npx")
    def test_check_ccusage_available(self, mock_which, mock_run, usage_mgr: UsageManager):
        """ccusage check succeeds when npx and ccusage are available."""
        mock_run.return_value = MagicMock(returncode=0)
        assert usage_mgr._check_ccusage() is True

    @patch("claude_flow.usage.subprocess.run")
    def test_run_ccusage_success(self, mock_run, usage_mgr: UsageManager):
        """Successful ccusage invocation returns parsed JSON."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{"inputTokens": 100, "outputTokens": 200}]),
        )
        result = usage_mgr._run_ccusage("session")
        assert result == [{"inputTokens": 100, "outputTokens": 200}]

    @patch("claude_flow.usage.subprocess.run")
    @patch("claude_flow.usage.shutil.which", return_value="/usr/bin/npx")
    def test_run_ccusage_with_date_filters(self, mock_which, mock_run, usage_mgr: UsageManager):
        """ccusage invocation passes --since and --until flags."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([]),
        )
        usage_mgr._run_ccusage("daily", since="2026-03-01", until="2026-03-06")
        # Verify the command includes date flags
        call_args = mock_run.call_args[0][0]
        assert "--since" in call_args
        assert "2026-03-01" in call_args
        assert "--until" in call_args
        assert "2026-03-06" in call_args

    @patch("claude_flow.usage.subprocess.run")
    @patch("claude_flow.usage.shutil.which", return_value="/usr/bin/npx")
    def test_run_ccusage_failure(self, mock_which, mock_run, usage_mgr: UsageManager):
        """Failed ccusage invocation returns None."""
        mock_run.return_value = MagicMock(returncode=1, stderr="error msg")
        result = usage_mgr._run_ccusage("session")
        assert result is None

    def test_get_session_usage_fallback(self, usage_project: Path):
        """get_session_usage falls back to log parsing when ccusage unavailable."""
        _write_log(usage_project, "task-ggg777", {
            "type": "result",
            "total_cost_usd": 1.5,
            "usage": {"input_tokens": 500, "output_tokens": 300,
                      "cache_creation_input_tokens": 1000,
                      "cache_read_input_tokens": 2000},
            "modelUsage": {"Claude-Opus-4.6": {}},
        })

        mgr = UsageManager(usage_project, Config())
        with patch.object(mgr, "_check_ccusage", return_value=False):
            sessions = mgr.get_session_usage()

        assert len(sessions) == 1
        assert sessions[0]["task_id"] == "task-ggg777"
        assert sessions[0]["cost_usd"] == 1.5

    def test_get_summary(self, usage_project: Path):
        """get_summary aggregates across all sessions."""
        _write_log(usage_project, "task-hhh888", {
            "type": "result",
            "total_cost_usd": 1.0,
            "usage": {"input_tokens": 100, "output_tokens": 200,
                      "cache_creation_input_tokens": 300,
                      "cache_read_input_tokens": 400},
            "modelUsage": {},
        })
        _write_log(usage_project, "task-iii999", {
            "type": "result",
            "total_cost_usd": 2.0,
            "usage": {"input_tokens": 50, "output_tokens": 60,
                      "cache_creation_input_tokens": 70,
                      "cache_read_input_tokens": 80},
            "modelUsage": {},
        })

        mgr = UsageManager(usage_project, Config())
        with patch.object(mgr, "_check_ccusage", return_value=False):
            summary = mgr.get_summary()

        assert summary["session_count"] == 2
        assert summary["input_tokens"] == 150
        assert summary["output_tokens"] == 260
        assert summary["cache_creation_input_tokens"] == 370
        assert summary["cache_read_input_tokens"] == 480
        assert summary["total_tokens"] == 1260
        assert summary["total_cost_usd"] == 3.0

    @patch("claude_flow.usage.shutil.which", return_value=None)
    def test_get_daily_usage_no_ccusage(self, mock_which, usage_mgr: UsageManager):
        """get_daily_usage returns None when ccusage unavailable."""
        result = usage_mgr.get_daily_usage()
        assert result is None

    @patch("claude_flow.usage.shutil.which", return_value=None)
    def test_get_monthly_usage_no_ccusage(self, mock_which, usage_mgr: UsageManager):
        """get_monthly_usage returns None when ccusage unavailable."""
        result = usage_mgr.get_monthly_usage()
        assert result is None


# -- Formatting helpers tests -----------------------------------------------

class TestFormatHelpers:

    def test_format_number(self):
        assert _format_number(0) == "0"
        assert _format_number(1234) == "1,234"
        assert _format_number(1000000) == "1,000,000"

    def test_format_cost(self):
        assert _format_cost(None) == "n/a"
        assert _format_cost(0.0) == "$0.000"
        assert _format_cost(1.2345) == "$1.234"
        assert _format_cost(0.938) == "$0.938"

    def test_format_session_table_empty(self):
        result = format_session_table([])
        assert "No usage data" in result

    def test_format_session_table_with_data(self):
        sessions = [
            {
                "task_id": "task-abc123",
                "input_tokens": 3677,
                "output_tokens": 6376,
                "cache_creation_input_tokens": 58687,
                "cache_read_input_tokens": 785931,
                "cost_usd": 0.938,
                "models": ["Claude-Opus-4.6"],
            },
        ]
        result = format_session_table(sessions)
        assert "Claude Flow Usage Report" in result
        assert "task-abc123" in result
        assert "3,677" in result
        assert "6,376" in result
        assert "$0.938" in result
        assert "Total (1)" in result

    def test_format_daily_table_empty(self):
        result = format_daily_table([])
        assert "No daily usage" in result

    def test_format_daily_table_with_data(self):
        data = [
            {
                "date": "2026-03-05",
                "input_tokens": 8779,
                "output_tokens": 14620,
                "cache_creation_input_tokens": 89787,
                "cache_read_input_tokens": 1397931,
                "cost_usd": 2.169,
            },
        ]
        result = format_daily_table(data)
        assert "Daily Usage Report" in result
        assert "2026-03-05" in result
        assert "8,779" in result
        assert "$2.169" in result

    def test_format_summary(self):
        summary = {
            "session_count": 3,
            "input_tokens": 11670,
            "output_tokens": 19132,
            "cache_creation_input_tokens": 131987,
            "cache_read_input_tokens": 1817931,
            "total_tokens": 1980720,
            "total_cost_usd": 2.841,
        }
        result = format_summary(summary)
        assert "Usage Summary" in result
        assert "Sessions:      3" in result
        assert "11,670" in result
        assert "$2.841" in result


# -- CLI integration tests --------------------------------------------------

class TestUsageCLI:

    def test_usage_command_default(self, usage_project: Path):
        """cf usage shows session table from log fallback."""
        from click.testing import CliRunner
        from claude_flow.cli import main

        _write_log(usage_project, "task-cli111", {
            "type": "result",
            "total_cost_usd": 0.5,
            "usage": {"input_tokens": 100, "output_tokens": 200,
                      "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0},
            "modelUsage": {},
        })

        runner = CliRunner()
        with patch("claude_flow.usage.shutil.which", return_value=None):
            result = runner.invoke(
                main, ["usage"],
                catch_exceptions=False,
                env={"CF_PROJECT_ROOT": str(usage_project)},
            )

        assert result.exit_code == 0
        assert "task-cli111" in result.output
        assert "100" in result.output

    def test_usage_summary_command(self, usage_project: Path):
        """cf usage summary shows aggregated stats."""
        from click.testing import CliRunner
        from claude_flow.cli import main

        _write_log(usage_project, "task-cli222", {
            "type": "result",
            "total_cost_usd": 1.0,
            "usage": {"input_tokens": 500, "output_tokens": 300,
                      "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0},
            "modelUsage": {},
        })

        runner = CliRunner()
        with patch("claude_flow.usage.shutil.which", return_value=None):
            result = runner.invoke(
                main, ["usage", "summary"],
                catch_exceptions=False,
                env={"CF_PROJECT_ROOT": str(usage_project)},
            )

        assert result.exit_code == 0
        assert "Usage Summary" in result.output
        assert "Sessions:      1" in result.output

    def test_usage_daily_no_ccusage(self, usage_project: Path):
        """cf usage daily shows fallback message when ccusage unavailable."""
        from click.testing import CliRunner
        from claude_flow.cli import main

        runner = CliRunner()
        with patch("claude_flow.usage.shutil.which", return_value=None):
            result = runner.invoke(
                main, ["usage", "daily"],
                catch_exceptions=False,
                env={"CF_PROJECT_ROOT": str(usage_project)},
            )

        assert result.exit_code == 0
        assert "ccusage" in result.output

    def test_usage_monthly_no_ccusage(self, usage_project: Path):
        """cf usage monthly shows message when ccusage unavailable."""
        from click.testing import CliRunner
        from claude_flow.cli import main

        runner = CliRunner()
        with patch("claude_flow.usage.shutil.which", return_value=None):
            result = runner.invoke(
                main, ["usage", "monthly"],
                catch_exceptions=False,
                env={"CF_PROJECT_ROOT": str(usage_project)},
            )

        assert result.exit_code == 0
        assert "ccusage" in result.output

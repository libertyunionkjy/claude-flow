from pathlib import Path
from click.testing import CliRunner
from claude_flow.cli import main


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

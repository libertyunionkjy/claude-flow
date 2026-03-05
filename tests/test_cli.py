import subprocess
from pathlib import Path
from click.testing import CliRunner
from claude_flow.cli import main


def _init_git_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"], check=True, capture_output=True)
    (path / "README.md").write_text("# Test")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"], check=True, capture_output=True)
    return path


class TestCLI:
    def test_init(self, tmp_path):
        repo = _init_git_repo(tmp_path / "repo")
        runner = CliRunner()
        result = runner.invoke(main, ["init"], catch_exceptions=False, env={"CF_PROJECT_ROOT": str(repo)})
        assert result.exit_code == 0
        assert (repo / ".claude-flow").is_dir()
        assert (repo / ".claude-flow" / "config.json").exists()

    def test_task_add(self, tmp_path):
        repo = _init_git_repo(tmp_path / "repo")
        (repo / ".claude-flow").mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main, ["task", "add", "-p", "Do something", "My Task"],
            catch_exceptions=False, env={"CF_PROJECT_ROOT": str(repo)},
        )
        assert result.exit_code == 0
        assert "My Task" in result.output

    def test_task_list_empty(self, tmp_path):
        repo = _init_git_repo(tmp_path / "repo")
        (repo / ".claude-flow").mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main, ["task", "list"],
            catch_exceptions=False, env={"CF_PROJECT_ROOT": str(repo)},
        )
        assert result.exit_code == 0

    def test_status(self, tmp_path):
        repo = _init_git_repo(tmp_path / "repo")
        (repo / ".claude-flow").mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main, ["status"],
            catch_exceptions=False, env={"CF_PROJECT_ROOT": str(repo)},
        )
        assert result.exit_code == 0

import re
from pathlib import Path
from click.testing import CliRunner
from claude_flow.cli import main
from claude_flow.task_manager import TaskManager


def test_task_add_with_subagent_flag(tmp_path: Path):
    """cf task add --subagent should set use_subagent=True on the task."""
    runner = CliRunner()
    env = {"CF_PROJECT_ROOT": str(tmp_path)}
    # init
    (tmp_path / ".claude-flow").mkdir()
    runner.invoke(main, ["init"], env=env)

    result = runner.invoke(
        main,
        ["task", "add", "Test", "-p", "do it", "--subagent"],
        env=env,
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    match = re.search(r"(task-[a-f0-9]+)", result.output)
    assert match
    task_id = match.group(1)

    tm = TaskManager(tmp_path)
    task = tm.get(task_id)
    assert task.use_subagent is True


def test_task_add_with_no_subagent_flag(tmp_path: Path):
    """cf task add --no-subagent should set use_subagent=False."""
    runner = CliRunner()
    env = {"CF_PROJECT_ROOT": str(tmp_path)}
    (tmp_path / ".claude-flow").mkdir()
    runner.invoke(main, ["init"], env=env)

    result = runner.invoke(
        main,
        ["task", "add", "Test", "-p", "do it", "--no-subagent"],
        env=env,
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    match = re.search(r"(task-[a-f0-9]+)", result.output)
    task_id = match.group(1)

    tm = TaskManager(tmp_path)
    task = tm.get(task_id)
    assert task.use_subagent is False


def test_task_list_shows_subagent_marker(tmp_path: Path):
    """task list should show [S] for subagent-enabled tasks."""
    runner = CliRunner()
    env = {"CF_PROJECT_ROOT": str(tmp_path)}
    (tmp_path / ".claude-flow").mkdir()
    runner.invoke(main, ["init"], env=env)
    runner.invoke(main, ["task", "add", "WithSA", "-p", "prompt", "--subagent"], env=env)
    runner.invoke(main, ["task", "add", "Without", "-p", "prompt"], env=env)

    result = runner.invoke(main, ["task", "list"], env=env, catch_exceptions=False)
    assert result.exit_code == 0
    # Find the line for WithSA and check it has [S]
    for line in result.output.splitlines():
        if "WithSA" in line:
            assert "[S]" in line
        if "Without" in line:
            assert "[S]" not in line


def test_task_add_without_subagent_flag(tmp_path: Path):
    """Without flag, use_subagent should be None (inherit from config)."""
    runner = CliRunner()
    env = {"CF_PROJECT_ROOT": str(tmp_path)}
    (tmp_path / ".claude-flow").mkdir()
    runner.invoke(main, ["init"], env=env)

    result = runner.invoke(
        main,
        ["task", "add", "Test", "-p", "do it"],
        env=env,
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    match = re.search(r"(task-[a-f0-9]+)", result.output)
    task_id = match.group(1)

    tm = TaskManager(tmp_path)
    task = tm.get(task_id)
    assert task.use_subagent is None

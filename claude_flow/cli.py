from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import click

from .chat import ChatManager
from .config import Config
from .models import TaskStatus
from .planner import Planner
from .task_manager import TaskManager
from .worker import Worker
from .worktree import WorktreeManager


# -- Terminal helpers -------------------------------------------------------

def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences to prevent terminal state corruption."""
    return re.sub(r"\x1b\[[0-9;?]*[a-zA-Z~]|\x1b\].*?\x07|\x1b[^[\]]", "", text)


def _reset_terminal() -> None:
    """Reset terminal to canonical mode (cooked mode) for interactive input.

    Restores ICANON, ECHO, and ISIG flags so that Enter, Ctrl+C, etc. work
    correctly after a subprocess may have altered terminal settings.
    """
    if not sys.stdin.isatty():
        return
    try:
        import termios

        fd = sys.stdin.fileno()
        attrs = termios.tcgetattr(fd)
        # Restore local flags: canonical mode, echo, signal processing
        attrs[3] |= termios.ICANON | termios.ECHO | termios.ISIG
        # Restore input flags: CR-to-NL translation
        attrs[0] |= termios.ICRNL
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
    except (ImportError, termios.error, ValueError, OSError):
        pass


def _worker_entry(args: tuple) -> int:
    """Module-level worker entry point (must be picklable for multiprocessing)."""
    wid, root, worktree_dir, cfg_dict, daemon = args
    cfg = Config(**cfg_dict)
    tm = TaskManager(root)
    wt = WorktreeManager(root, root / worktree_dir)
    w = Worker(wid, root, tm, wt, cfg)
    if daemon:
        return w.run_daemon()
    return w.run_loop()


def _get_root() -> Path:
    env_root = os.environ.get("CF_PROJECT_ROOT")
    if env_root:
        return Path(env_root)
    # walk up to find .claude-flow or .git
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".claude-flow").exists() or (parent / ".git").exists():
            return parent
    return cwd


@click.group()
@click.pass_context
def main(ctx):
    """Claude Flow - Multi-instance Claude Code workflow manager."""
    ctx.ensure_object(dict)
    ctx.obj["root"] = _get_root()


@main.command()
@click.pass_context
def init(ctx):
    """Initialize .claude-flow/ in the current project."""
    root = ctx.obj["root"]
    cf_dir = root / ".claude-flow"
    for sub in ["logs", "plans", "worktrees", "chats"]:
        (cf_dir / sub).mkdir(parents=True, exist_ok=True)
    cfg = Config()
    cfg.save(root)
    # Add .claude-flow/worktrees, lock, log, chats to .gitignore
    gitignore = root / ".gitignore"
    ignore_lines = [".claude-flow/worktrees/", ".claude-flow/tasks.lock", ".claude-flow/logs/", ".claude-flow/chats/"]
    existing = gitignore.read_text() if gitignore.exists() else ""
    to_add = [l for l in ignore_lines if l not in existing]
    if to_add:
        with open(gitignore, "a") as f:
            f.write("\n# claude-flow\n" + "\n".join(to_add) + "\n")
    click.echo(f"Initialized .claude-flow/ in {root}")


# -- Task commands ----------------------------------------------------------

@main.group()
def task():
    """Manage tasks."""
    pass


@task.command("add")
@click.argument("title")
@click.option("-p", "--prompt", default=None, help="Task prompt for Claude Code")
@click.option("-f", "--file", "filepath", default=None, type=click.Path(exists=True), help="Import tasks from file")
@click.option("-P", "--priority", default=0, type=int, help="Task priority (higher = more important)")
@click.pass_context
def task_add(ctx, title, prompt, filepath, priority):
    """Add a new task."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    if filepath:
        added = tm.add_from_file(Path(filepath))
        click.echo(f"Added {len(added)} tasks")
        return
    if prompt is None:
        prompt = click.edit("# Enter the task prompt for Claude Code\n")
        if not prompt:
            click.echo("Aborted: no prompt provided")
            return
    t = tm.add(title, prompt, priority=priority)
    click.echo(f"Added: {t.id} - {t.title} (priority: {priority})")


@task.command("list")
@click.pass_context
def task_list(ctx):
    """List all tasks."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    tasks = tm.list_tasks()
    if not tasks:
        click.echo("No tasks")
        return
    # 按优先级降序排序显示
    tasks.sort(key=lambda t: t.priority, reverse=True)
    for t in tasks:
        status_icon = {"pending": "○", "planning": "⟳", "planned": "◉", "approved": "✓",
                       "running": "▶", "merging": "⇄", "done": "●", "failed": "✗"}
        icon = status_icon.get(t.status.value, "?")
        pri = f"P{t.priority}" if t.priority > 0 else ""
        click.echo(f"  {icon} {t.id}  {t.status.value:<10}  {pri:>3}  {t.title}")


@task.command("show")
@click.argument("task_id")
@click.pass_context
def task_show(ctx, task_id):
    """Show task details."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    t = tm.get(task_id)
    if not t:
        click.echo(f"Task {task_id} not found")
        return
    click.echo(f"ID:       {t.id}")
    click.echo(f"Title:    {t.title}")
    click.echo(f"Status:   {t.status.value}")
    click.echo(f"Priority: {t.priority}")
    click.echo(f"Branch:   {t.branch or '-'}")
    click.echo(f"Worker:   {t.worker_id or '-'}")
    click.echo(f"Created:  {t.created_at}")
    if t.progress:
        click.echo(f"Progress: {t.progress}")
    if t.error:
        click.echo(f"Error:    {t.error}")
    click.echo(f"\nPrompt:\n{t.prompt}")


@task.command("remove")
@click.argument("task_id")
@click.pass_context
def task_remove(ctx, task_id):
    """Remove a task."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    if tm.remove(task_id):
        click.echo(f"Removed {task_id}")
    else:
        click.echo(f"Task {task_id} not found")


# -- Plan commands ----------------------------------------------------------

def _plan_foreground(root, cfg, tm, planner, tasks):
    """Run plan generation in foreground (blocking)."""
    for t in tasks:
        click.echo(f"Planning: {t.id} - {t.title} ...")
        tm.update_status(t.id, TaskStatus.PLANNING)
        try:
            plan_file = planner.generate(t)
        except KeyboardInterrupt:
            tm.update_status(t.id, TaskStatus.PENDING)
            click.echo(f"\n  Interrupted, {t.id} rolled back to pending")
            raise SystemExit(130)
        if plan_file:
            tm.update_status(t.id, TaskStatus.PLANNED)
            click.echo(f"  Plan saved to {plan_file}")
        else:
            tm.update_status(t.id, TaskStatus.FAILED, t.error)
            click.echo(f"  Plan failed: {t.error}")


def _plan_background(root, cfg, tm, tasks):
    """Fork a detached background process for plan generation."""
    from datetime import datetime

    # Mark all tasks as PLANNING before forking
    for t in tasks:
        tm.update_status(t.id, TaskStatus.PLANNING)

    log_file = root / ".claude-flow" / "logs" / "plan-bg.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    pid = os.fork()
    if pid > 0:
        # Parent: report and return immediately
        click.echo(f"Started planning {len(tasks)} task(s) in background (PID: {pid})")
        for t in tasks:
            click.echo(f"  ⟳ {t.id} - {t.title}")
        click.echo(f"\nCheck progress:  cf plan status")
        click.echo(f"View log:        cf log plan-bg")
        return

    # -- Child process: detach from terminal and execute planning --
    try:
        os.setsid()

        # Redirect stdio to log file
        fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.close(fd)
        devnull_fd = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull_fd, 0)
        os.close(devnull_fd)

        # Re-create objects in child to avoid shared file descriptor issues
        tm_child = TaskManager(root)
        plans_dir = root / ".claude-flow" / "plans"
        planner = Planner(root, plans_dir, cfg, task_manager=tm_child)

        def _now():
            return datetime.now().replace(microsecond=0).isoformat()

        print(f"\n[{_now()}] Background planning started for {len(tasks)} task(s)",
              flush=True)

        for t in tasks:
            print(f"[{_now()}] Planning: {t.id} - {t.title}", flush=True)
            try:
                plan_file = planner.generate(t)
            except Exception as e:
                tm_child.update_status(t.id, TaskStatus.FAILED, str(e))
                print(f"[{_now()}] Failed: {t.id} - {e}", flush=True)
                continue
            if plan_file:
                tm_child.update_status(t.id, TaskStatus.PLANNED)
                print(f"[{_now()}] Done: {t.id} -> {plan_file}", flush=True)
            else:
                tm_child.update_status(t.id, TaskStatus.FAILED, t.error)
                print(f"[{_now()}] Failed: {t.id} - {t.error}", flush=True)

        print(f"[{_now()}] Background planning completed", flush=True)
    except Exception as e:
        try:
            print(f"Fatal error in background planning: {e}", flush=True)
        except Exception:
            pass
    finally:
        os._exit(0)


@main.group(invoke_without_command=True)
@click.option("-t", "--task-id", "task_id", default=None, help="Plan a specific task by ID")
@click.option("-F", "--foreground", is_flag=True, help="Run in foreground (blocking mode)")
@click.option("-i", "--interactive", is_flag=True, help="Use interactive chat mode")
@click.pass_context
def plan(ctx, task_id, foreground, interactive):
    """Generate plans for tasks.

    Use -t TASK_ID to plan a specific task.
    Otherwise, plan all pending tasks (auto mode only).
    Use --interactive to start a chat-based planning session.
    """
    if ctx.invoked_subcommand is not None:
        return
    root = ctx.obj["root"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    plans_dir = root / ".claude-flow" / "plans"
    planner = Planner(root, plans_dir, cfg, task_manager=tm)

    if interactive:
        # Interactive mode requires a specific task
        if not task_id:
            click.echo("Error: --interactive requires a task_id")
            return
        t = tm.get(task_id)
        if not t:
            click.echo(f"Task {task_id} not found")
            return
        chat_mgr = ChatManager(root, cfg)
        chat_mgr.create_session(task_id, mode="interactive")
        tm.update_status(task_id, TaskStatus.PLANNING)
        click.echo(f"Interactive planning session started for {task_id}")
        click.echo(f"Use 'cf plan chat {task_id} -m \"message\"' to send messages")
        click.echo(f"Use 'cf plan finalize {task_id}' to generate the plan document")
        return

    if task_id:
        tasks = [tm.get(task_id)]
        if tasks[0] is None:
            click.echo(f"Task {task_id} not found")
            return
    else:
        tasks = tm.list_tasks(status=TaskStatus.PENDING)

    if not tasks:
        click.echo("No pending tasks to plan")
        return

    if foreground:
        _plan_foreground(root, cfg, tm, planner, tasks)
    else:
        _plan_background(root, cfg, tm, tasks)


@plan.command("status")
@click.pass_context
def plan_status(ctx):
    """Check planning progress."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    planning = tm.list_tasks(status=TaskStatus.PLANNING)
    planned = tm.list_tasks(status=TaskStatus.PLANNED)

    if planning:
        click.echo("In progress:")
        for t in planning:
            click.echo(f"  ⟳ {t.id} - {t.title}")
        click.echo("  (If stuck, use 'cf reset <task_id>' to reset)")

    if planned:
        click.echo("Ready for review:")
        for t in planned:
            click.echo(f"  ◉ {t.id} - {t.title}")

    if not planning and not planned:
        click.echo("No plans in progress or ready for review")

    # Show recent log tail
    log_file = root / ".claude-flow" / "logs" / "plan-bg.log"
    if log_file.exists():
        lines = log_file.read_text().strip().splitlines()
        if lines:
            click.echo(f"\nRecent log:")
            for line in lines[-5:]:
                click.echo(f"  {line}")


@plan.command("review")
@click.pass_context
def plan_review(ctx):
    """Interactively review generated plans."""
    root = ctx.obj["root"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    plans_dir = root / ".claude-flow" / "plans"
    planner = Planner(root, plans_dir, cfg, task_manager=tm)

    tasks = tm.list_tasks(status=TaskStatus.PLANNED)
    if not tasks:
        click.echo("No plans to review")
        return

    try:
        for t in tasks:
            plan_path = Path(t.plan_file) if t.plan_file else plans_dir / f"{t.id}.md"
            if not plan_path.exists():
                click.echo(f"Plan file missing for {t.id}, skipping")
                continue

            click.echo(f"\n{'─' * 50}")
            click.echo(f"Task:   {t.id} - {t.title}")
            click.echo(f"{'─' * 50}")
            click.echo(_strip_ansi(planner.read_plan(plan_path)))
            click.echo(f"{'─' * 50}")

            _reset_terminal()
            action = click.prompt(
                "[a]pprove  [c]hat  [s]kip  [e]dit  [q]uit",
                type=str, default="s"
            )
            if action == "a":
                planner.approve(t)
                tm.update_status(t.id, TaskStatus.APPROVED)
                click.echo(f"  {t.id} approved")
            elif action == "c":
                # Start interactive chat for this task
                chat_mgr = ChatManager(root, cfg)
                session = chat_mgr.get_session(t.id)
                if not session:
                    chat_mgr.create_session(t.id, mode="interactive")
                tm.update_status(t.id, TaskStatus.PLANNING)
                click.echo(f"  Chat session started for {t.id}")
                click.echo(f"  Use 'cf plan chat {t.id} -m \"message\"' to continue")
            elif action == "e":
                editor = os.environ.get("EDITOR", "vi")
                subprocess.run([editor, str(plan_path)])
                planner.approve(t)
                tm.update_status(t.id, TaskStatus.APPROVED)
                click.echo(f"  {t.id} edited and approved")
            elif action == "q":
                break
    except (KeyboardInterrupt, click.Abort):
        click.echo("\nReview interrupted.")
        return


@plan.command("chat")
@click.argument("task_id")
@click.option("-m", "--message", default=None, help="Message to send to AI")
@click.pass_context
def plan_chat(ctx, task_id, message):
    """Send a chat message for interactive planning."""
    root = ctx.obj["root"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    chat_mgr = ChatManager(root, cfg)

    t = tm.get(task_id)
    if not t:
        click.echo(f"Task {task_id} not found")
        return

    session = chat_mgr.get_session(task_id)
    if not session:
        click.echo(f"No chat session for {task_id}. Start one with: cf plan {task_id} --interactive")
        return

    if session.status != "active":
        click.echo(f"Chat session for {task_id} is finalized")
        return

    if message:
        # Single message mode
        click.echo(f"Sending message to AI...")
        response = chat_mgr.send_message(task_id, message, task_prompt=t.prompt)
        if response:
            click.echo(f"\nAI: {response}")
        else:
            click.echo("Failed to get AI response")
    else:
        # Interactive REPL mode
        click.echo(f"Chat session for: {t.title} ({task_id})")
        click.echo(f"Type your messages. Enter empty line to quit.\n")

        # Show existing history
        if session.messages:
            for msg in session.messages:
                prefix = "You" if msg.role == "user" else "AI"
                click.echo(f"  {prefix}: {msg.content[:200]}{'...' if len(msg.content) > 200 else ''}\n")

        _reset_terminal()
        while True:
            try:
                user_input = click.prompt("You", default="", show_default=False)
            except (KeyboardInterrupt, click.Abort, EOFError):
                click.echo("\nChat ended.")
                break
            if not user_input.strip():
                break
            click.echo("  Thinking...")
            response = chat_mgr.send_message(task_id, user_input, task_prompt=t.prompt)
            if response:
                click.echo(f"\n  AI: {response}\n")
            else:
                click.echo("  Failed to get response\n")

        click.echo(f"Use 'cf plan finalize {task_id}' to generate the plan document")


@plan.command("finalize")
@click.argument("task_id")
@click.pass_context
def plan_finalize(ctx, task_id):
    """Generate a plan document from the chat session."""
    root = ctx.obj["root"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    plans_dir = root / ".claude-flow" / "plans"
    planner = Planner(root, plans_dir, cfg, task_manager=tm)
    chat_mgr = ChatManager(root, cfg)

    t = tm.get(task_id)
    if not t:
        click.echo(f"Task {task_id} not found")
        return

    session = chat_mgr.get_session(task_id)
    if not session:
        click.echo(f"No chat session for {task_id}")
        return

    if not session.messages:
        click.echo("Chat session has no messages")
        return

    click.echo(f"Generating plan from chat session ({len(session.messages)} messages)...")
    chat_mgr.finalize(task_id)
    tm.update_status(task_id, TaskStatus.PLANNING)

    try:
        plan_file = planner.generate_from_chat(t, session)
    except KeyboardInterrupt:
        tm.update_status(task_id, TaskStatus.PLANNING)
        click.echo(f"\n  Interrupted")
        return

    if plan_file:
        tm.update_status(task_id, TaskStatus.PLANNED)
        click.echo(f"  Plan saved to {plan_file}")
    else:
        tm.update_status(task_id, TaskStatus.FAILED, t.error)
        click.echo(f"  Plan generation failed: {t.error}")


@plan.command("approve")
@click.argument("task_id", required=False)
@click.option("--all", "approve_all", is_flag=True, help="Approve all planned tasks")
@click.pass_context
def plan_approve(ctx, task_id, approve_all):
    """Approve a plan or all plans."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    cfg = Config.load(root)
    planner = Planner(root, root / ".claude-flow" / "plans", cfg)

    if approve_all:
        tasks = tm.list_tasks(status=TaskStatus.PLANNED)
        for t in tasks:
            planner.approve(t)
            tm.update_status(t.id, TaskStatus.APPROVED)
            click.echo(f"  {t.id} approved")
    elif task_id:
        t = tm.get(task_id)
        if t and t.status == TaskStatus.PLANNED:
            planner.approve(t)
            tm.update_status(t.id, TaskStatus.APPROVED)
            click.echo(f"  {t.id} approved")
        else:
            click.echo(f"Task {task_id} not found or not in planned state")


# -- Run command ------------------------------------------------------------

@main.command()
@click.option("-n", "--num-workers", default=1, type=int, help="Number of parallel workers")
@click.option("-d", "--daemon", is_flag=True, help="Run in daemon mode (continuous polling)")
@click.argument("task_id", required=False)
@click.pass_context
def run(ctx, num_workers, daemon, task_id):
    """Start workers to execute approved tasks."""
    root = ctx.obj["root"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    wt = WorktreeManager(root, root / cfg.worktree_dir)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if task_id:
        t = tm.get(task_id)
        if not t:
            click.echo(f"Task {task_id} not found")
            return
        if t.status != TaskStatus.APPROVED:
            tm.update_status(t.id, TaskStatus.APPROVED)
        worker = Worker(0, root, tm, wt, cfg)
        t = tm.claim_next(0)
        if t:
            worker.execute_task(t)
        return

    if num_workers == 1:
        worker = Worker(0, root, tm, wt, cfg)
        if daemon:
            click.echo("Starting daemon mode (Ctrl+C to stop)...")
            count = worker.run_daemon()
        else:
            count = worker.run_loop()
        click.echo(f"Completed {count} tasks")
    else:
        # Multi-worker: spawn subprocesses
        import multiprocessing
        from dataclasses import asdict

        worker_args = [
            (wid, root, cfg.worktree_dir, asdict(cfg), daemon)
            for wid in range(num_workers)
        ]
        with multiprocessing.Pool(num_workers) as pool:
            results = pool.map(_worker_entry, worker_args)
        total = sum(results)
        click.echo(f"Completed {total} tasks across {num_workers} workers")


# -- Watch command ----------------------------------------------------------

@main.command()
@click.option("--interval", default=2, type=int, help="Refresh interval in seconds")
@click.pass_context
def watch(ctx, interval):
    """Watch real-time worker activity (stream-json monitor)."""
    import json
    import time

    root = ctx.obj["root"]
    status_dir = root / ".claude-flow" / "worker-status"

    click.echo("Watching worker status (Ctrl+C to stop)...")
    try:
        while True:
            click.clear()
            click.echo("=== Claude Flow Worker Monitor ===\n")

            if not status_dir.exists():
                click.echo("No worker status data yet.")
            else:
                for status_file in sorted(status_dir.glob("worker-*.json")):
                    try:
                        data = json.loads(status_file.read_text())
                        wid = data.get("worker_id", "?")
                        tid = data.get("task_id", "-")
                        last = data.get("last_event", "-")
                        events = data.get("event_count", 0)
                        tools = data.get("tool_use_count", 0)
                        errors = data.get("error_count", 0)
                        updated = data.get("updated_at", "-")
                        click.echo(f"  Worker-{wid}  task={tid}  events={events}  tools={tools}  errors={errors}")
                        click.echo(f"    Last: {last}")
                        click.echo(f"    Updated: {updated}")
                        click.echo()
                    except (json.JSONDecodeError, KeyError):
                        continue

            # 同时显示任务状态概览
            tm = TaskManager(root)
            tasks = tm.list_tasks()
            counts = {}
            for t in tasks:
                counts[t.status.value] = counts.get(t.status.value, 0) + 1
            click.echo(f"--- Tasks: {len(tasks)} total ---")
            for s, c in sorted(counts.items()):
                click.echo(f"  {s}: {c}")

            time.sleep(interval)
    except KeyboardInterrupt:
        click.echo("\nStopped watching.")


# -- Web command ------------------------------------------------------------

@main.command()
@click.option("--port", default=None, type=int, help="Web server port (default from config)")
@click.pass_context
def web(ctx, port):
    """Start the web manager kanban interface."""
    root = ctx.obj["root"]
    cfg = Config.load(root)
    web_port = port or cfg.web_port

    try:
        from .web import create_app
    except ImportError:
        click.echo("Error: Flask is required for the web manager.")
        click.echo("Install it with: pip install flask")
        return

    app = create_app(root, cfg)
    click.echo(f"Starting web manager on http://0.0.0.0:{web_port}")
    app.run(host="0.0.0.0", port=web_port, debug=False)


# -- Status / Log / Clean / Reset / Retry -----------------------------------

@main.command()
@click.pass_context
def status(ctx):
    """Show task and worker status overview."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    tasks = tm.list_tasks()
    counts = {}
    for t in tasks:
        counts[t.status.value] = counts.get(t.status.value, 0) + 1
    click.echo(f"Total tasks: {len(tasks)}")
    for s, c in sorted(counts.items()):
        click.echo(f"  {s}: {c}")

    # 显示活跃 worker 状态
    cfg = Config.load(root)
    status_dir = root / ".claude-flow" / "worker-status"
    if status_dir.exists():
        import json
        click.echo(f"\nActive workers:")
        for sf in sorted(status_dir.glob("worker-*.json")):
            try:
                data = json.loads(sf.read_text())
                click.echo(f"  Worker-{data.get('worker_id', '?')}: "
                          f"task={data.get('task_id', '-')} "
                          f"events={data.get('event_count', 0)}")
            except (json.JSONDecodeError, KeyError):
                continue


@main.command()
@click.argument("task_id")
@click.option("--raw", is_flag=True, help="Show raw stream-json log instead of formatted output")
@click.pass_context
def log(ctx, task_id, raw):
    """View task execution log."""
    import json as _json

    root = ctx.obj["root"]
    logs_dir = root / ".claude-flow" / "logs"

    if raw:
        raw_file = logs_dir / f"{task_id}.log"
        if raw_file.exists():
            click.echo(raw_file.read_text())
        else:
            click.echo(f"No raw log for {task_id}")
        return

    # Prefer structured JSON log
    json_file = logs_dir / f"{task_id}.json"
    if json_file.exists():
        try:
            from .monitor import format_structured_log_for_cli
            log_data = _json.loads(json_file.read_text())
            click.echo(format_structured_log_for_cli(log_data))
            return
        except Exception:
            pass  # Fall through to raw log

    # Fallback to raw log
    raw_file = logs_dir / f"{task_id}.log"
    if raw_file.exists():
        click.echo(raw_file.read_text())
    else:
        click.echo(f"No log for {task_id}")


@main.command()
@click.pass_context
def clean(ctx):
    """Clean up worktrees and merged branches."""
    root = ctx.obj["root"]
    cfg = Config.load(root)
    wt = WorktreeManager(root, root / cfg.worktree_dir)
    count = wt.cleanup_all()
    click.echo(f"Cleaned {count} worktrees")


@main.command()
@click.argument("task_id")
@click.pass_context
def reset(ctx, task_id):
    """Reset a failed task back to pending."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    t = tm.get(task_id)
    if not t:
        click.echo(f"Task {task_id} not found")
        return
    if t.status in (TaskStatus.FAILED, TaskStatus.NEEDS_INPUT):
        tm.update_status(task_id, TaskStatus.PENDING)
        click.echo(f"Reset {task_id} to pending")
    elif t.status == TaskStatus.RUNNING:
        # Reset zombie running task (worker crashed without updating status)
        target = TaskStatus.PLANNED if t.plan_file else TaskStatus.PENDING
        tm.update_status(task_id, target)
        # Clean up orphaned worktree and branch
        cfg = Config.load(root)
        wt = WorktreeManager(root, root / cfg.worktree_dir)
        wt.remove(task_id, t.branch)
        click.echo(f"Reset zombie running task {task_id} to {target.value}")
    else:
        click.echo(f"Task {task_id} is in {t.status.value} status, cannot reset")


@main.command()
@click.pass_context
def retry(ctx):
    """Retry all failed tasks."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    failed = tm.list_tasks(status=TaskStatus.FAILED)
    for t in failed:
        tm.update_status(t.id, TaskStatus.APPROVED)
        click.echo(f"  {t.id} -> approved")
    click.echo(f"Retrying {len(failed)} tasks")


# -- Respond command --------------------------------------------------------

@main.command()
@click.argument("task_id")
@click.option("-m", "--message", prompt="补充信息", help="提供给任务的补充信息")
@click.pass_context
def respond(ctx, task_id, message):
    """Respond to a task that needs input."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    t = tm.get(task_id)
    if not t:
        click.echo(f"Task {task_id} not found")
        return
    if t.status != TaskStatus.NEEDS_INPUT:
        click.echo(f"Task {task_id} is {t.status.value}, not needs_input")
        return
    if t.error:
        click.echo(f"\nClaude's question:\n{t.error}\n")
    updated = tm.respond(task_id, message)
    if updated:
        click.echo(f"Responded to {task_id}, status -> approved")
    else:
        click.echo(f"Failed to respond to {task_id}")


# -- Progress command -------------------------------------------------------

@main.command()
@click.pass_context
def progress(ctx):
    """Show PROGRESS.md experience log."""
    root = ctx.obj["root"]
    cfg = Config.load(root)
    progress_file = root / cfg.progress_file
    if progress_file.exists():
        click.echo(progress_file.read_text())
    else:
        click.echo("No progress log yet.")

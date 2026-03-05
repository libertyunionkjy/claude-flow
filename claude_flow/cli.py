from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import click

from .config import Config
from .models import TaskStatus
from .planner import Planner
from .task_manager import TaskManager
from .worker import Worker
from .worktree import WorktreeManager


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
    for sub in ["logs", "plans", "worktrees"]:
        (cf_dir / sub).mkdir(parents=True, exist_ok=True)
    cfg = Config()
    cfg.save(root)
    # Add .claude-flow/worktrees and lock/log files to .gitignore
    gitignore = root / ".gitignore"
    ignore_lines = [".claude-flow/worktrees/", ".claude-flow/tasks.lock", ".claude-flow/logs/"]
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
@click.pass_context
def task_add(ctx, title, prompt, filepath):
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
    t = tm.add(title, prompt)
    click.echo(f"Added: {t.id} - {t.title}")


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
    for t in tasks:
        status_icon = {"pending": "○", "planning": "⟳", "planned": "◉", "approved": "✓",
                       "running": "▶", "merging": "⇄", "done": "●", "failed": "✗"}
        icon = status_icon.get(t.status.value, "?")
        click.echo(f"  {icon} {t.id}  {t.status.value:<10}  {t.title}")


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
    click.echo(f"ID:      {t.id}")
    click.echo(f"Title:   {t.title}")
    click.echo(f"Status:  {t.status.value}")
    click.echo(f"Branch:  {t.branch or '-'}")
    click.echo(f"Worker:  {t.worker_id or '-'}")
    click.echo(f"Created: {t.created_at}")
    if t.error:
        click.echo(f"Error:   {t.error}")
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

@main.group(invoke_without_command=True)
@click.argument("task_id", required=False)
@click.pass_context
def plan(ctx, task_id):
    """Generate plans for pending tasks."""
    if ctx.invoked_subcommand is not None:
        return
    root = ctx.obj["root"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    plans_dir = root / ".claude-flow" / "plans"
    planner = Planner(root, plans_dir, cfg)

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

    for t in tasks:
        click.echo(f"Planning: {t.id} - {t.title} ...")
        plan_file = planner.generate(t)
        if plan_file:
            tm.update_status(t.id, TaskStatus.PLANNED)
            click.echo(f"  Plan saved to {plan_file}")
        else:
            tm.update_status(t.id, TaskStatus.FAILED, t.error)
            click.echo(f"  Plan failed: {t.error}")


@plan.command("review")
@click.pass_context
def plan_review(ctx):
    """Interactively review generated plans."""
    root = ctx.obj["root"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    plans_dir = root / ".claude-flow" / "plans"
    planner = Planner(root, plans_dir, cfg)

    tasks = tm.list_tasks(status=TaskStatus.PLANNED)
    if not tasks:
        click.echo("No plans to review")
        return

    for t in tasks:
        plan_path = Path(t.plan_file) if t.plan_file else plans_dir / f"{t.id}.md"
        if not plan_path.exists():
            click.echo(f"Plan file missing for {t.id}, skipping")
            continue

        click.echo(f"\n{'─' * 50}")
        click.echo(f"Task:   {t.id} - {t.title}")
        click.echo(f"{'─' * 50}")
        click.echo(planner.read_plan(plan_path))
        click.echo(f"{'─' * 50}")

        action = click.prompt("[a]pprove  [r]eject  [s]kip  [e]dit  [q]uit", type=str, default="s")
        if action == "a":
            planner.approve(t)
            tm.update_status(t.id, TaskStatus.APPROVED)
            click.echo(f"  {t.id} approved")
        elif action == "r":
            reason = click.prompt("Rejection reason", default="")
            planner.reject(t, reason)
            tm.update_status(t.id, TaskStatus.PENDING)
            click.echo(f"  {t.id} rejected, back to pending")
        elif action == "e":
            editor = os.environ.get("EDITOR", "vi")
            subprocess.run([editor, str(plan_path)])
            planner.approve(t)
            tm.update_status(t.id, TaskStatus.APPROVED)
            click.echo(f"  {t.id} edited and approved")
        elif action == "q":
            break


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
@click.argument("task_id", required=False)
@click.pass_context
def run(ctx, num_workers, task_id):
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
        count = worker.run_loop()
        click.echo(f"Completed {count} tasks")
    else:
        # Multi-worker: spawn subprocesses
        import multiprocessing

        def _worker_entry(wid):
            w = Worker(wid, root, tm, wt, cfg)
            return w.run_loop()

        with multiprocessing.Pool(num_workers) as pool:
            results = pool.map(_worker_entry, range(num_workers))
        total = sum(results)
        click.echo(f"Completed {total} tasks across {num_workers} workers")


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


@main.command()
@click.argument("task_id")
@click.pass_context
def log(ctx, task_id):
    """View task execution log."""
    root = ctx.obj["root"]
    log_file = root / ".claude-flow" / "logs" / f"{task_id}.log"
    if log_file.exists():
        click.echo(log_file.read_text())
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
    if t and t.status == TaskStatus.FAILED:
        tm.update_status(task_id, TaskStatus.PENDING)
        click.echo(f"Reset {task_id} to pending")
    else:
        click.echo(f"Task {task_id} not found or not failed")


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

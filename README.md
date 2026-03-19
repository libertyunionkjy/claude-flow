<p align="center">
  <img src="claude_flow/web/static/logo.svg" alt="Claude Flow Logo" width="128" height="128">
</p>

<h1 align="center">Claude Flow</h1>

<p align="center">Multi-instance Claude Code workflow manager. Manage multiple Claude Code instances for parallel development in any Git project.</p>

Inspired by [Yuanming Hu's Claude Code workflow](https://mp.weixin.qq.com/s/example), implementing a complete multi-Agent development pipeline from task queue, Git Worktree isolation to Web dashboard.

## Core Capabilities

- **Task Queue** -- Priority-based task list, workers automatically pick up tasks by priority
- **Mini Task** -- Lightweight tasks that bypass planning/approval, execute directly with `--run`
- **Git Worktree Parallelization** -- Each worker operates in an isolated worktree with symlink-shared files
- **Non-Git Support** -- Graceful degradation for non-Git projects (single worker, no isolation)
- **Plan Mode** -- Batch plan generation (background by default), multi-round chat feedback, unified review before execution
- **Daemon Mode** -- Ralph Loop continuously polls, automatically picks up the next task after completing one
- **Rebase Merge + Auto Conflict Resolution** -- Rebase strategy with Claude-powered automatic conflict resolution
- **Pre-merge Test Verification** -- Automatic test execution before merge, auto-fix and retry on failure
- **Needs Input** -- Worker detects when Claude needs clarification, pauses for human input
- **Token Usage Stats** -- Track token consumption per task/daily/monthly via ccusage integration
- **Stream JSON Real-time Monitoring** -- Parse worker output for real-time progress tracking
- **Web Manager Dashboard** -- Dark-themed dashboard with sidebar navigation, mobile support
- **Doctor Agent** -- Built-in diagnostic agent for Claude Code, auto-installed on `cf init`, helps troubleshoot task failures, orphaned worktrees, and other issues

## Installation

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install
pip install -e .

# Development mode (with test dependencies)
pip install -e ".[dev]"

# Web dashboard (optional)
pip install flask
```

> **Note:** Debian/Ubuntu systems need `python3-venv` first: `apt install python3-venv`

Activate the virtual environment before use: `source .venv/bin/activate`

Requirements: Python 3.10+, Git, [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code), Linux/macOS

## Quick Start

```bash
# 1. Initialize in your project
cd your-project
cf init

# 2. Add tasks (with priority)
cf task add -p "Implement user login API with JWT auth" -P 5 "User Login"
cf task add -p "Write unit tests covering all API endpoints" "API Tests"

# 3. Quick task (skip planning, execute directly)
cf task mini "run pytest and fix any failures" --run

# 4. Generate plans and review
cf plan              # Background plan generation (all pending tasks)
cf plan status       # Check generation progress
cf plan review       # Interactive review (multi-round feedback)

# 5. Execute
cf run               # Single worker
cf run -n 3          # 3 parallel workers
cf run --daemon      # Daemon mode, continuous polling

# 6. Monitor
cf watch             # Real-time worker status
cf web               # Start Web dashboard
cf status            # Task status overview
cf usage             # Token usage report
```

## Command Reference

| Command | Description |
|---------|-------------|
| `cf init` | Initialize `.claude-flow/` directory and install Doctor agent |
| `cf task add "title"` | Add task (`-p` prompt, `-f` batch import, `-P` priority) |
| `cf task mini "prompt"` | Add mini task (skip planning, `--run` to execute immediately) |
| `cf task list` | List all tasks (sorted by priority) |
| `cf task show <id>` | Show task details |
| `cf task remove <id>` | Remove a task |
| `cf plan [-t id]` | Generate plans (background by default, `-F` foreground) |
| `cf plan status` | Check plan generation progress |
| `cf plan review` | Interactive plan review (`[a]pprove / [c]hat / [e]dit / [s]kip`) |
| `cf plan chat <id>` | Interactive chat planning (REPL or `-m` single message) |
| `cf plan finalize <id>` | Generate plan document from chat session |
| `cf plan approve <id>` | Approve plan (`--all` to approve all) |
| `cf run [-n N] [-d]` | Start workers (`-n` parallel count, `-d` daemon mode) |
| `cf watch` | Real-time worker activity monitor |
| `cf web [--port 8080]` | Start Web dashboard |
| `cf status` | Task and worker status overview |
| `cf log <id>` | View task execution log (`--raw` for raw stream-json) |
| `cf respond <id>` | Provide input to a task in `needs_input` status |
| `cf usage` | Token usage report (per-session) |
| `cf usage daily` | Daily aggregated usage (requires ccusage) |
| `cf usage monthly` | Monthly aggregated usage (requires ccusage) |
| `cf usage summary` | Overall usage summary |
| `cf clean` | Clean up worktrees and merged branches |
| `cf reset <id>` | Reset failed/needs_input/zombie-running task |
| `cf retry` | Retry all failed tasks |

## Task Lifecycle

```
pending --> planning --> planned --> (review) --> approved --> running --> merging --> done
                                                                 \          \
                                                            needs_input    failed
```

Mini tasks skip the planning cycle:
```
(mini) approved --> running --> merging --> done
```

## Task Types

### Normal Task
Full lifecycle with planning, review, and execution. Best for complex features.

```bash
cf task add -p "Implement user auth with JWT" "User Auth"
cf plan
cf plan review
cf run
```

### Mini Task
Skips planning/approval, goes directly to `approved` status. Ideal for quick fixes, script execution, or simple changes.

```bash
# Add and auto-execute
cf task mini "fix the typo in README.md line 42" --run

# Add only (execute later with cf run)
cf task mini "update version to 2.0.0"
```

## Project Structure

```
claude_flow/
├── cli.py            # Click CLI entry point
├── config.py         # Config loading/saving (all configuration options)
├── models.py         # Task / TaskStatus / TaskType data models
├── task_manager.py   # Task CRUD + priority queue + file lock
├── worker.py         # Worker lifecycle (daemon mode, streaming, auto-commit)
├── worktree.py       # Git worktree ops (symlink, rebase, merge lock, push)
├── planner.py        # Plan mode (multi-round chat, plan split)
├── chat.py           # ChatSession model, ChatManager for interactive planning
├── monitor.py        # Stream JSON real-time parsing and monitoring
├── usage.py          # Token usage statistics (ccusage + log fallback)
├── utils.py          # Shared utilities
└── web/              # Web Manager dashboard
    ├── __init__.py
    ├── app.py        # Flask application factory
    ├── api.py        # REST API (20+ endpoints)
    └── templates/
        └── index.html  # Dark-themed dashboard UI
```

## Tests

```bash
pytest -v
```

## Troubleshooting

### Task failed

```bash
cf task show <id>              # Check the error message
cf log <id>                    # Full execution log
cf reset <id>                  # Reset to pending and retry
cf retry                       # Retry ALL failed tasks
```

### Task stuck in `running` (zombie)

No worker is actually running, but the task is still marked `running`.

```bash
cf reset <id>                  # Resets to pending, cleans up worktree
```

### Orphaned worktrees

Worktrees left behind after a crash or interrupted task.

```bash
cf clean                       # Remove worktrees for non-running tasks
git worktree list              # Verify cleanup
```

### Lock file stuck

`cf` commands hang because `tasks.lock` was not released after a crash.

```bash
ps aux | grep "cf "            # Check if any cf process is running
rm .claude-flow/tasks.lock     # Safe to remove if no cf process is active
```

### Merge conflict

Worker fails during the merge step.

```bash
cf log <id>                    # Check conflict details
cf reset <id>                  # Reset task
# The next run will create a fresh worktree and retry
```

### Mini Task interrupted

Server restart marks running Mini Tasks as `interrupted`.

```bash
cf task list                   # Find interrupted tasks
ls .claude-flow/worktrees/<id> # Worktree may still contain useful work
cf reset <id>                  # Reset to approved for re-execution
```

### Corrupted `tasks.json`

```bash
python3 -c "import json; json.load(open('.claude-flow/tasks.json'))"  # Validate
git log --oneline -5 -- .claude-flow/tasks.json                       # Check history
```

### Doctor Agent

For complex issues, use the built-in diagnostic agent. `cf init` automatically installs it at `.claude/agents/claude-flow-doctor.md`. Open Claude Code in your project and run:

```
/agent claude-flow-doctor task-a1b2c3 failed, help me diagnose
/agent claude-flow-doctor check for orphaned worktrees
/agent claude-flow-doctor tasks.json seems corrupted
```

The agent reads `.claude-flow/` data, logs, and worktree status to pinpoint root causes and suggest fixes.

## License

MIT

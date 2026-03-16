# Claude Flow Usage Guide

## Installation & Environment

### Prerequisites

- Python 3.10+
- Git (with worktree support)
- Claude Code CLI (installed and available via `claude` command)
- Linux or macOS (file locking depends on `fcntl`)

### Installation

```bash
git clone <repo-url> && cd claude-flow
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Web dashboard (optional)
pip install flask
```

Verify installation:

```bash
cf --help
```

---

## Initialization

Run in the target project root:

```bash
cd /path/to/your-project
cf init
```

This creates the following directory structure:

```
your-project/
└── .claude-flow/
    ├── config.json       # Configuration file
    ├── tasks.json        # Task queue (auto-generated)
    ├── tasks.lock        # File lock (auto-generated)
    ├── logs/             # Execution logs
    ├── plans/            # Generated plan files
    ├── chats/            # Chat session data
    └── worktrees/        # Worktrees (created during execution, cleaned after)
```

Temporary files are automatically added to `.gitignore`.

> **Non-Git projects:** `cf init` also works in non-Git directories. In this mode, worktree isolation is disabled and only single-worker execution is supported.

---

## Task Management

### Adding Tasks

```bash
# Specify prompt directly with -p
cf task add -p "Implement RESTful user registration API with email verification" "User Registration"

# With priority (higher number = higher priority)
cf task add -p "Urgent login bug fix" -P 10 "Urgent Fix"

# Without -p opens an editor to write the prompt
cf task add "Database Migration"
```

### Mini Tasks

Mini tasks bypass the full planning cycle (skip planning/approval, go directly to `approved` status). Ideal for quick fixes, script execution, or simple changes.

```bash
# Add a mini task
cf task mini "run pytest and fix any failures"

# Add with a custom title
cf task mini -t "Fix typo" "fix the typo in README.md line 42"

# Add and immediately execute
cf task mini "update the version number to 2.0.0" --run
```

### Batch Import

Prepare a file `tasks.txt`, each line in the format `title | prompt`:

```
User Login | Implement JWT login API supporting email and phone
User Registration | Implement registration with email verification
Password Reset | Implement password reset flow with email link
```

Import:

```bash
cf task add -f tasks.txt "batch import"
```

> Note: When using `-f`, the title argument is ignored; titles are read from the file.

### Viewing Tasks

```bash
# List view (sorted by priority descending)
cf task list
#   ○ task-a1b2c3  pending    P10  Urgent Fix
#   ○ task-d4e5f6  pending     P5  User Login
#   ✓ task-789abc  approved         User Registration
#   ▶ task-def012  running          [mini] Password Reset

# Detail view
cf task show task-a1b2c3
```

Status icons:

| Icon | Status | Description |
|------|--------|-------------|
| `○` | pending | Awaiting processing |
| `⟳` | planning | Plan being generated |
| `◉` | planned | Plan generated, awaiting review |
| `✓` | approved | Approved, awaiting execution |
| `▶` | running | Currently executing |
| `⇄` | merging | Being merged |
| `?` | needs_input | Claude needs clarification |
| `●` | done | Completed |
| `✗` | failed | Execution failed |

### Removing Tasks

```bash
cf task remove task-a1b2c3
```

---

## Plan Mode Workflow

Plan mode has Claude Code generate an implementation plan first, which is reviewed by a human before execution. Suitable for scenarios requiring quality control.

### Generating Plans

```bash
# Generate plans for all pending tasks (background by default)
cf plan

# Generate in foreground (blocking)
cf plan -F

# Generate for a specific task
cf plan -t task-a1b2c3

# Interactive chat-based planning
cf plan -t task-a1b2c3 --interactive
```

Plans are generated in background processes by default. Check progress with:

```bash
cf plan status
```

Plan files are saved in `.claude-flow/plans/task-xxx.md` using a structured format (YAML front matter + Markdown body).

### Interactive Chat Planning

Start a multi-round conversation to refine requirements before generating a plan:

```bash
# Start interactive planning session
cf plan -t task-a1b2c3 --interactive

# Continue the conversation (REPL mode)
cf plan chat task-a1b2c3

# Send a single message
cf plan chat task-a1b2c3 -m "Add error handling for edge cases"

# Generate plan document from chat history
cf plan finalize task-a1b2c3
```

### Reviewing Plans

**Interactive review:**

```bash
cf plan review
```

Each plan is displayed with action options:

| Key | Action |
|-----|--------|
| `a` | Approve, status becomes `approved` |
| `c` | Open chat to discuss the plan with AI |
| `s` | Skip current task |
| `e` | Open plan file in editor, auto-approve after editing |
| `q` | Exit review |

**Quick approve:**

```bash
# Approve a specific task
cf plan approve task-a1b2c3

# Approve all planned tasks
cf plan approve --all
```

### Plan Version History

Each time a plan is regenerated via chat feedback, the previous version is automatically saved as `task-xxx_v1.md`, `task-xxx_v2.md`, etc. The latest version is always at `task-xxx.md`.

---

## Executing Tasks

### Single Worker

```bash
# Auto-pick and execute all approved tasks (by priority)
cf run

# Execute a specific task
cf run task-a1b2c3
```

### Multi-Worker Parallel Execution

```bash
# 3 workers in parallel
cf run -n 3
```

> **Note:** Non-Git projects are limited to single worker mode (no worktree isolation).

### Daemon Mode

```bash
# Continuous polling, auto-pick next task (Ctrl+C to stop)
cf run --daemon

# Multi-worker daemon
cf run -n 3 --daemon
```

In daemon mode, workers check for new tasks every `daemon_poll_interval` seconds (default 10) when idle, until receiving SIGINT/SIGTERM.

### Worker Execution Flow

Each worker's complete execution flow:

1. Claim an `approved` task via file lock (by priority descending)
2. Create an isolated git worktree: `.claude-flow/worktrees/task-xxx/`
3. Set up symlinks for shared files (if `shared_symlinks` configured)
4. Run Claude Code in the worktree (port = `base_port + worker_id`)
5. Detect if Claude needs clarification → `needs_input` status (if no code changes produced)
6. **Repo contamination check** -- detect and rescue if Claude accidentally modified the main repo
7. **Auto-commit** uncommitted changes in worktree
8. **Pre-merge test verification** (if `pre_merge_commands` configured)
   - On failure, call Claude to fix, retry up to `max_test_retries` times
9. **Rebase merge** to main branch (with auto conflict resolution, up to `max_merge_retries` retries)
   - Merge lock prevents concurrent merges from multiple workers
10. **Remote push** (if `auto_push` configured)
11. Clean up worktree, mark task as `done`
13. Loop to pick up next task

---

## Responding to Needs Input

When Claude Code needs clarification during execution, the task enters `needs_input` status. Claude's question is stored in the task's error field.

```bash
# View what Claude is asking
cf task show task-a1b2c3

# Provide additional context
cf respond task-a1b2c3 -m "Use PostgreSQL, the database schema is in docs/schema.sql"
```

The task will be re-queued as `approved` with the supplementary information appended to the prompt.

---

## Monitoring

### Real-time Watch

```bash
# Watch worker activity (refreshes every 2 seconds)
cf watch

# Custom refresh interval
cf watch --interval 5
```

Displays each worker's current task, event count, tool call count, error count, and recent activity.

### Status Overview

```bash
cf status
# Total tasks: 5
#   approved: 1
#   done: 3
#   failed: 1
#
# Active workers:
#   Worker-0: task=task-a1b2c3 events=42
```

### Execution Logs

```bash
# View structured log (default)
cf log task-a1b2c3

# View raw stream-json log
cf log task-a1b2c3 --raw
```

---

## Token Usage Statistics

Track token consumption across tasks and sessions:

```bash
# Per-session (task) usage report
cf usage

# Daily aggregated report (requires ccusage)
cf usage daily

# Monthly aggregated report (requires ccusage)
cf usage monthly

# Overall summary
cf usage summary

# Filter by date range
cf usage --since 2026-03-01 --until 2026-03-10
cf usage daily --since 2026-03-01
```

Usage data sources:
1. **Primary**: `ccusage` CLI (via `npx ccusage@latest`) for full-featured reporting
2. **Fallback**: Parse stream-json logs from `.claude-flow/logs/` for basic per-task stats

---

## Web Manager Dashboard

Start the Web dashboard (requires Flask):

```bash
# Default port 8080
cf web

# Custom port
cf web --port 3000
```

### Dashboard Features

- **Overview Dashboard**: Status counts, active workers, recent activity, success rate, pipeline view
- **Sidebar Navigation**: Overview, All Tasks list, Workflow Guide
- **Task Management**: Create (normal/mini), edit priority, batch delete
- **Plan Workflow**: Auto Plan, Chat Plan (multi-round), View Plan, Approve
- **Chat Dialog**: Real-time AI conversation with thinking indicator and Finalize Plan action
- **Execution Control**: Run single task, Run All, daemon mode
- **Needs Input**: Inline respond form for tasks awaiting human input
- **Log Viewer**: Structured execution logs with tool calls, errors, and timeline
- **Auto-refresh**: Polls every 5 seconds for live updates
- **Dark Theme**: Eye-friendly design, responsive layout for mobile

### REST API

Web Manager provides the following API endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/tasks` | Task list (supports `?status=` filter) |
| `POST` | `/api/tasks` | Create task (body: `{title, prompt, priority, task_type}`) |
| `GET` | `/api/tasks/<id>` | Task details |
| `PATCH` | `/api/tasks/<id>` | Update task (status/priority) |
| `DELETE` | `/api/tasks/<id>` | Delete task (auto-stops running processes) |
| `POST` | `/api/tasks/batch-delete` | Batch delete (body: `{task_ids: [...]}`) |
| `POST` | `/api/tasks/<id>/approve` | Approve task |
| `POST` | `/api/tasks/<id>/plan` | Trigger plan generation (body: `{mode: "auto"\|"interactive"}`) |
| `GET` | `/api/tasks/<id>/plan` | Get plan content |
| `GET` | `/api/tasks/<id>/chat` | Get chat session history |
| `POST` | `/api/tasks/<id>/chat` | Send chat message (async, body: `{message}`) |
| `POST` | `/api/tasks/<id>/chat/finalize` | Generate plan from chat |
| `POST` | `/api/tasks/<id>/respond` | Respond to needs_input task (body: `{message}`) |
| `POST` | `/api/tasks/<id>/run` | Execute single task (async) |
| `POST` | `/api/tasks/<id>/reset` | Reset task status |
| `GET` | `/api/tasks/<id>/log` | Get execution log |
| `POST` | `/api/plan-all` | Batch plan all pending tasks |
| `POST` | `/api/approve-all` | Approve all planned tasks |
| `POST` | `/api/run` | Start workers (body: `{num_workers, daemon}`) |
| `POST` | `/api/retry-all` | Retry all failed tasks |
| `GET` | `/api/status` | Global status overview |
| `GET` | `/api/overview` | Dashboard overview data |
| `GET` | `/api/workers` | Worker status |
| `GET` | `/api/usage/summary` | Token usage summary |
| `GET` | `/api/usage/sessions` | Per-session usage |
| `GET` | `/api/usage/daily` | Daily usage report |
| `GET` | `/api/usage/monthly` | Monthly usage report |

Response format: `{"ok": true, "data": ...}` or `{"ok": false, "error": "..."}`.

---

## Troubleshooting

### Reset Failed Tasks

```bash
# Reset a single task to pending (or approved for mini tasks)
cf reset task-a1b2c3

# Reset a zombie running task (worker crashed without updating status)
cf reset task-a1b2c3  # Detects running status, cleans up worktree

# Retry all failed tasks (failed -> approved)
cf retry
```

### Clean Up Residuals

```bash
# Clean all worktrees and temporary branches
cf clean
```

---

## Configuration

Edit `.claude-flow/config.json`:

```jsonc
{
  // Basic settings
  "max_workers": 2,                // Max parallel workers
  "main_branch": "main",           // Main branch name
  "claude_args": [],                // Extra args passed to Claude Code
  "skip_permissions": true,         // Use --dangerously-skip-permissions
  "task_timeout": 600,              // Task timeout in seconds
  "plan_prompt_prefix": "...",      // Plan mode prompt prefix
  "task_prompt_prefix": "...",      // Execution mode prompt prefix

  // Plan phase tool restrictions
  "plan_allowed_tools": ["Read", "Glob", "Grep"],  // Tools allowed during planning (empty = no restriction)

  // Worktree symlink sharing
  "shared_symlinks": ["dev-tasks.json", "api-key.json"],  // Files to symlink
  "forbidden_symlinks": [],                                  // Files never symlinked

  // Merge strategy
  "auto_merge": true,              // Auto-merge after task completion
  "merge_mode": "rebase",          // Merge mode: rebase (default) or merge
  "merge_strategy": "--no-ff",     // Strategy for merge mode
  "max_merge_retries": 5,          // Max retries for rebase conflicts

  // Pre-merge testing
  "pre_merge_commands": ["pytest -v"],  // Test commands before merge
  "max_test_retries": 3,           // Max retries for test failures

  // Remote push
  "auto_push": false,              // Push to remote after merge

  // Worker port assignment
  "base_port": 5200,               // Port base (Worker-0 = 5200, Worker-1 = 5201, ...)

  // Daemon mode
  "daemon_poll_interval": 10,      // Poll interval when idle (seconds)

  // Web Manager
  "web_port": 8080                 // Web dashboard default port
}
```

### Common Configuration Scenarios

**Conservative mode** (disable auto-merge, manually inspect each task):

```json
{
  "auto_merge": false,
  "max_workers": 1,
  "auto_push": false
}
```

**High concurrency mode** (for many independent tasks):

```json
{
  "max_workers": 4,
  "auto_merge": true,
  "merge_mode": "rebase",
  "auto_push": true,
  "task_timeout": 1200,
  "daemon_poll_interval": 5
}
```

**Production mode with test verification**:

```json
{
  "max_workers": 2,
  "merge_mode": "rebase",
  "pre_merge_commands": ["pytest -v", "npm run lint"],
  "max_test_retries": 3,
  "max_merge_retries": 5,
  "auto_push": true,
  "shared_symlinks": [".env", "dev-tasks.json"]
}
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `CF_PROJECT_ROOT` | Override auto-detected project root |
| `EDITOR` | Editor for `cf plan review` edit mode (default `vi`) |
| `PORT` | Auto-set during execution, value = `base_port + worker_id` |
| `WORKER_ID` | Auto-set during execution, current worker number |

---

## Complete Usage Example

```bash
# Initialize
cd my-web-app
cf init

# Add tasks (with priority)
cf task add -p "Implement GET /api/users endpoint with pagination" -P 5 "User List API"
cf task add -p "Implement POST /api/users endpoint with input validation" -P 5 "Create User API"
cf task add -p "Write pytest tests for user API, cover normal and edge cases" -P 3 "User API Tests"

# Quick fix (mini task, immediate execution)
cf task mini "fix the import error in app.py line 12" --run

# Generate and review plans
cf plan                  # Background generation
cf plan status           # Check progress
cf plan review           # Use [a] approve, [c] chat, [e] edit

# Parallel execution (daemon mode)
cf run -n 2 --daemon

# Monitor in another terminal
cf watch                 # Real-time monitor
cf web                   # Or open Web dashboard

# Check results
cf status
cf log task-xxx
cf usage                 # Token usage report
# Handle special cases
cf respond task-xxx -m "Use PostgreSQL for the database"  # Answer Claude's question
cf retry                 # Retry failed tasks
cf run
```

---

## Notes

1. **Git repository requirement**: Project must be a Git repository with at least one commit (or use non-Git degraded mode)
2. **Merge conflicts**: In rebase mode, Claude automatically attempts to resolve conflicts (up to 5 retries); in merge mode, conflicted tasks are marked as failed
3. **Merge lock**: Multiple workers use a file-based merge lock to prevent concurrent merge operations
4. **Repo contamination detection**: Workers detect if Claude accidentally modified the main repo and automatically rescue changes to the worktree
5. **Claude Code**: Ensure the `claude` command is available and API key is configured
6. **File locking**: Uses `fcntl.flock`, only supports Linux/macOS
7. **Web Manager**: Requires Flask (`pip install flask`)
8. **Port assignment**: Each worker gets a dedicated port (base_port + worker_id), passed via the `PORT` environment variable
9. **Token usage**: Full reporting requires `ccusage` (`npx ccusage@latest`); basic per-task stats are available from logs without it

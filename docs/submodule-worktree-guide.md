# Working with Submodules

## Overview

claude-flow supports working across multiple Git submodules within a single
task. When a task involves changes spanning several repositories or modules,
claude-flow manages the worktree isolation, branch lifecycle, and merge flow
for each submodule automatically.

This is useful for:

- **Monorepo projects** with submodule dependencies (e.g. `libs/core`,
  `libs/ui`, `apps/server`)
- **Multi-repo development** where coordinated changes span several
  independent repositories
- **Feature branches** in submodules that need to be the base for new work

### Supported Scenarios

| Scenario | Description |
|----------|-------------|
| Existing submodule project | Your project already uses `git submodule` |
| Multi-repo orchestration | Multiple independent repos need coordinated changes |
| Branch-based submodule work | Submodules have feature branches you want to build on |

## Quick Start

### Scenario 1: Existing submodule project

Your project already has submodules configured:

```
my-monorepo/
  libs/core/           # submodule
  libs/api/            # submodule
  apps/web/            # submodule
```

```bash
cd my-monorepo
cf init

# Create a task that modifies specific submodules
cf task add "Fix shared types" -p "Update the TypeResult type in libs/core and update all callers in libs/api" \
  -s libs/core -s libs/api
```

### Scenario 2: Multi-repo development

You have several independent repositories that need coordinated changes:

```bash
mkdir workspace && cd workspace
git clone https://github.com/org/frontend.git
git clone https://github.com/org/backend.git
git clone https://github.com/org/shared-lib.git

# Initialize claude-flow, adopting repos as submodules
cf init --adopt

# Create a task spanning all repos
cf task add "Add user authentication" \
  -p "Implement OAuth2 login flow across frontend, backend, and shared-lib" \
  -s frontend -s backend -s shared-lib
```

### Scenario 3: Working on specific branches

Your submodules have feature branches you want to build upon:

```bash
cf task add "Extend auth module" \
  -p "Add two-factor authentication support" \
  -s libs/core -s libs/api \
  --sub-branch libs/core:feature-auth \
  --sub-branch libs/api:feature-auth
```

This tells claude-flow to base the work in `libs/core` on the `feature-auth`
branch instead of the default HEAD.

## How It Works

### Branch Lifecycle

When a task specifies submodules, claude-flow manages a temporary branch inside
each submodule to ensure commits are never left on a detached HEAD:

```
Step 1:  Create worktree for main repo
             main repo:  main --> cf/{task-id}

Step 2:  Initialize submodules in worktree

Step 3:  Create branch cf/{task-id} in each submodule
             If --sub-branch specified:
                 cf/{task-id} is based on that branch
             Otherwise:
                 cf/{task-id} is based on current HEAD

Step 4:  Claude Code works across all submodules

Step 5:  Auto-commit in each submodule, then update main repo
             submodule:  git add + commit on cf/{task-id}
             main repo:  git add + commit (updates submodule pointer)

Step 6:  Merge cf/{task-id} back to target branch in each submodule
             submodule:  git checkout {target} && git merge cf/{task-id}
             (only for submodules with --sub-branch specified)

Step 7:  Update main repo's submodule pointers

Step 8:  Rebase and merge main repo branch to main
             main repo:  git rebase main && git merge --ff-only

Step 9:  Clean up worktree and temporary branches
             git worktree remove
             git branch -d cf/{task-id}
```

### Visual Flow

```
Submodule:  HEAD ---- cf/task-xxx --[commit]-- merge --> target-branch
                                                          (e.g. feature-auth)

Main repo:  main ---- cf/task-xxx --[commit]-- rebase --> main
                                     ^
                                     |
                              (submodule pointer updated)
```

### Two-Step Commit

When a task modifies submodule files, claude-flow performs a two-step commit:

1. **Submodule commit**: For each submodule with changes, `git add` and
   `git commit` inside the submodule directory
2. **Main repo commit**: `git add` the updated submodule pointer in the
   main repo, then commit

This ensures the submodule pointer in the main repo always points to a
reachable commit.

### Existing Worktrees in Submodules

If a submodule already has worktrees managed by other tools (e.g. the user
has their own worktree setup for parallel development), claude-flow does not
interfere. It creates its own temporary branch with the `cf/` prefix, which
is independent of any existing worktree branches.

## Configuration

### Default Submodule Branches

To avoid repeating `--sub-branch` on every task, set defaults in the
project configuration:

```json
// .claude-flow/config.json
{
  "default_sub_branches": {
    "libs/core": "develop",
    "libs/api": "develop"
  }
}
```

**Priority order** (highest to lowest):
1. CLI `--sub-branch` flag on the task
2. Config file `default_sub_branches`
3. Submodule's current HEAD (no branch switching)

### Push Submodule Changes

By default, submodule commits are only made locally and are not pushed to
the submodule's remote. To enable automatic push after merge:

```json
// .claude-flow/config.json
{
  "auto_push_submodules": true
}
```

When enabled, after successfully merging a submodule's `cf/{task-id}` branch
back to its target branch, claude-flow will push the target branch to origin.

### All Configuration Options

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `default_sub_branches` | object | `{}` | Map of submodule path to default base branch |
| `auto_push_submodules` | boolean | `false` | Push submodule changes to remote after merge |

## Web UI

When the project has submodules, the task creation dialog in the Web UI
includes a **Submodules** section:

- **Checkboxes** to select which submodules to include in the task
- **Branch dropdown** for each selected submodule, populated from the
  submodule's available branches
- Branches are loaded from `GET /api/submodules?branches=true`

The task detail view also displays which submodules and branches are
associated with each task.

## CLI Reference

### Initialization

```
cf init --adopt [REPO1 REPO2 ...]
    Initialize a claude-flow project and optionally adopt existing git
    repositories as submodules.

    Without arguments: interactive picker showing all detected repos.
    With repo names: adopt only the specified repos.
    With --all: adopt all detected repos without prompting.
```

### Task Creation

```
cf task add TITLE -p PROMPT \
    -s SUBMODULE_PATH \
    --sub-branch SUBMODULE_PATH:BRANCH

    Create a task with submodule specifications.

    -s, --submodule PATH    Target submodule path (repeatable)
    --sub-branch PATH:BRANCH   Base branch for a submodule (repeatable)

    Examples:
        cf task add "Fix types" -p "..." -s libs/core
        cf task add "Auth" -p "..." -s libs/core --sub-branch libs/core:develop
```

```
cf task mini PROMPT \
    -s SUBMODULE_PATH \
    --sub-branch SUBMODULE_PATH:BRANCH

    Create a mini task (skips planning/approval) with submodule support.

    Examples:
        cf task mini "fix the typo" -s libs/core
        cf task mini "update deps" -s libs/core --sub-branch libs/core:hotfix --run
```

### Other Commands

All existing commands (`cf run`, `cf status`, `cf log`, `cf clean`,
`cf reset`, `cf retry`) work transparently with submodule-enabled tasks.
No additional flags are needed -- submodule handling is automatic based on
the task's configuration.

## Troubleshooting

### "fatal: not a git repository" in submodule

The submodule may not be initialized in the worktree. This typically happens
if the submodule path in `-s` does not match the actual path in
`.gitmodules`. Verify the path:

```bash
git submodule status
```

If needed, initialize manually:

```bash
git submodule update --init <path>
```

### Merge conflict in submodule

If a submodule merge fails due to conflicts, the task is marked as FAILED.
To investigate:

```bash
cf log <task-id>
```

The log shows which submodule had the conflict and the git error output.
To resolve manually:

1. Reset the task: `cf reset <task-id>`
2. Navigate to the submodule in the worktree
3. Resolve conflicts and commit
4. Retry: `cf retry`

### Branch already exists

If `cf/{task-id}` branch already exists in a submodule (e.g. from a
previously failed run), use `cf reset <task-id>` to clean up the worktree
and branches before retrying.

You can also manually delete the branch:

```bash
cd <submodule-path>
git branch -D cf/<task-id>
```

### Detached HEAD in submodule

Without the submodule worktree integration, `git submodule update` leaves
submodules in a detached HEAD state. This means commits made during task
execution become dangling (unreachable) after the worktree is removed.

The enhanced `_init_submodules` creates a named branch `cf/{task-id}` to
prevent this. If you see detached HEAD warnings, ensure you are using the
latest version of claude-flow.

### Push fails for submodule

If `auto_push_submodules` is enabled but push fails:

1. Check that the submodule has a remote configured:
   ```bash
   cd <submodule-path>
   git remote -v
   ```
2. Verify you have push access to the remote
3. Check if the remote is a bare repository (non-bare local repos reject
   pushes by default)

Submodules without a configured remote are silently skipped during push.

### cf init --adopt not finding repos

The `--adopt` scanner skips:
- Hidden directories (starting with `.`)
- Common non-code directories: `node_modules`, `__pycache__`, `.venv`,
  `venv`, `.tox`, `.mypy_cache`, `.pytest_cache`, `dist`, `build`
- Directories already registered as submodules

If your repo is inside one of these directories, move it or specify the
path explicitly:

```bash
cf init --adopt path/to/my-repo
```

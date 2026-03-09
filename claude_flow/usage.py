from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config

logger = logging.getLogger(__name__)


class UsageManager:
    """Token usage statistics manager.

    Collects and reports token usage data via two strategies:
    1. Primary: ccusage CLI (via npx) for full-featured reporting
    2. Fallback: parse stream-json logs from .claude-flow/logs/
    """

    # Class-level cache for ccusage availability check
    _ccusage_available: Optional[bool] = None
    _ccusage_checked_at: Optional[float] = None
    _CCUSAGE_CACHE_TTL = 300  # 5 minutes

    def __init__(self, project_root: Path, config: Optional[Config] = None):
        self._root = project_root
        self._config = config or Config()
        self._logs_dir = project_root / ".claude-flow" / "logs"

    # ------------------------------------------------------------------
    # ccusage availability
    # ------------------------------------------------------------------

    def _check_ccusage(self) -> bool:
        """Check if ccusage is available via npx (cached for 5 minutes)."""
        import time

        now = time.time()
        if (
            UsageManager._ccusage_available is not None
            and UsageManager._ccusage_checked_at is not None
            and now - UsageManager._ccusage_checked_at < UsageManager._CCUSAGE_CACHE_TTL
        ):
            return UsageManager._ccusage_available

        if not shutil.which("npx"):
            UsageManager._ccusage_available = False
            UsageManager._ccusage_checked_at = now
            return False
        try:
            result = subprocess.run(
                ["npx", "ccusage@latest", "--version"],
                capture_output=True, text=True, timeout=30,
            )
            UsageManager._ccusage_available = result.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            UsageManager._ccusage_available = False

        UsageManager._ccusage_checked_at = now
        return UsageManager._ccusage_available

    def _get_project_filter(self) -> str:
        """Generate --project filter based on project root path.

        Claude Code encodes the cwd path by replacing '/' with '-' as the
        directory name under ~/.claude/projects/. Using the full project root
        path matches both the main project and all its worktree sessions.
        """
        return str(self._root)

    # ------------------------------------------------------------------
    # ccusage invocation
    # ------------------------------------------------------------------

    def _run_ccusage(
        self, report_type: str, *, since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Run ccusage CLI and return parsed JSON output.

        Args:
            report_type: One of "session", "daily", "monthly".
            since: Optional start date (YYYY-MM-DD).
            until: Optional end date (YYYY-MM-DD).

        Returns:
            Parsed JSON list or None on failure.
        """
        cmd = ["npx", "ccusage@latest", report_type, "--json",
               "--project", self._get_project_filter()]
        if since:
            cmd.extend(["--since", since])
        if until:
            cmd.extend(["--until", until])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                logger.warning("ccusage failed: %s", result.stderr[:200])
                return None
            return self._parse_json_output(result.stdout)
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning("ccusage execution error: %s", e)
            return None

    def _parse_json_output(self, stdout: str) -> Optional[List[Dict[str, Any]]]:
        """Parse ccusage --json output into a list of dicts.

        Handles both direct arrays and wrapped structures like
        {"sessions": [...]} or {"daily": [...]}.
        """
        try:
            data = json.loads(stdout)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                # ccusage wraps results: {"sessions": [...], "totals": {...}}
                for key in ("sessions", "daily", "monthly", "data"):
                    if key in data and isinstance(data[key], list):
                        return data[key]
                # Single object (not a wrapper)
                return [data]
            return None
        except (json.JSONDecodeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Task-Session mapping
    # ------------------------------------------------------------------

    def _build_task_session_map(self) -> Dict[str, str]:
        """Build {session_id: task_id} mapping from stream-json logs.

        Scans .claude-flow/logs/task-*.log for result events containing
        a session_id field.
        """
        mapping: Dict[str, str] = {}
        if not self._logs_dir.exists():
            return mapping

        for log_file in self._logs_dir.glob("task-*.log"):
            task_id = log_file.stem
            try:
                lines = log_file.read_text(errors="replace").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "result" and obj.get("session_id"):
                        mapping[obj["session_id"]] = task_id
                        break
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
        return mapping

    @staticmethod
    def _normalize_session(session: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize ccusage field names to snake_case standard keys.

        ccusage uses camelCase (inputTokens, cacheCreationTokens, totalCost),
        while our internal format uses snake_case (input_tokens, cost_usd).
        This ensures a single consistent format downstream.
        """
        _get = session.get

        session.setdefault("input_tokens",
                           _get("inputTokens", 0))
        session.setdefault("output_tokens",
                           _get("outputTokens", 0))
        session.setdefault("cache_creation_input_tokens",
                           _get("cacheCreationInputTokens",
                                _get("cacheCreationTokens", 0)))
        session.setdefault("cache_read_input_tokens",
                           _get("cacheReadInputTokens",
                                _get("cacheReadTokens", 0)))
        session.setdefault("cost_usd",
                           _get("costUSD",
                                _get("totalCost",
                                     _get("total_cost_usd", 0))))
        session.setdefault("total_tokens",
                           _get("totalTokens", 0))
        session.setdefault("models",
                           _get("modelsUsed", []))
        return session

    def _enrich_with_tasks(
        self, sessions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Associate session data with task IDs and normalize field names.

        Uses two strategies for task mapping:
        1. session_id -> task_id mapping from log files
        2. session directory name containing task-{id} pattern
        """
        task_map = self._build_task_session_map()
        task_pattern = re.compile(r"task-[0-9a-f]{6}")

        for session in sessions:
            # Normalize field names first
            self._normalize_session(session)

            session_id = session.get("sessionId", session.get("session_id", ""))
            # Strategy 1: direct mapping
            if session_id in task_map:
                session["task_id"] = task_map[session_id]
                continue
            # Strategy 2: extract from session ID or project path
            # ccusage sessionId encodes the worktree path, e.g.
            # "-opt-shared-claude-flow--claude-flow-worktrees-task-c9c2fb"
            match = task_pattern.search(str(session_id))
            if match:
                session["task_id"] = match.group(0)
                continue
            project_path = session.get("projectPath", session.get("project", ""))
            match = task_pattern.search(str(project_path))
            if match:
                session["task_id"] = match.group(0)
            elif session_id == "subagents":
                session["task_id"] = "Subagent"
            else:
                # Main project direct usage (sessionId is encoded project path)
                session["task_id"] = "Direct (main)"
        return sessions

    # ------------------------------------------------------------------
    # Fallback: parse own logs
    # ------------------------------------------------------------------

    def _fallback_from_logs(self) -> List[Dict[str, Any]]:
        """Parse usage data from .claude-flow/logs/ when ccusage is unavailable.

        Extracts result events from stream-json log files to build
        per-task usage statistics.
        """
        results: List[Dict[str, Any]] = []
        if not self._logs_dir.exists():
            return results

        for log_file in self._logs_dir.glob("task-*.log"):
            task_id = log_file.stem
            try:
                lines = log_file.read_text(errors="replace").splitlines()
            except OSError:
                continue

            # Find the last result event
            for line in reversed(lines):
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                if obj.get("type") != "result":
                    continue

                usage = obj.get("usage", {})
                model_usage = obj.get("modelUsage", {})
                cost = obj.get("total_cost_usd", obj.get("cost_usd"))

                # Build model list
                models = list(model_usage.keys()) if model_usage else []

                entry: Dict[str, Any] = {
                    "task_id": task_id,
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_creation_input_tokens": usage.get(
                        "cache_creation_input_tokens", 0
                    ),
                    "cache_read_input_tokens": usage.get(
                        "cache_read_input_tokens", 0
                    ),
                    "cost_usd": cost,
                    "models": models,
                    "source": "logs",
                }

                # Calculate total tokens
                entry["total_tokens"] = (
                    entry["input_tokens"]
                    + entry["output_tokens"]
                    + entry["cache_creation_input_tokens"]
                    + entry["cache_read_input_tokens"]
                )

                results.append(entry)
                break  # only last result event per log

        return results

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_session_usage(
        self, *, since: Optional[str] = None, until: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get per-session (task) usage report.

        Tries ccusage first, falls back to log parsing.
        """
        if self._check_ccusage():
            data = self._run_ccusage("session", since=since, until=until)
            if data is not None:
                return self._enrich_with_tasks(data)

        return self._fallback_from_logs()

    def get_daily_usage(
        self, *, since: Optional[str] = None, until: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Get daily aggregated usage report (requires ccusage)."""
        if not self._check_ccusage():
            return None
        data = self._run_ccusage("daily", since=since, until=until)
        if data:
            for entry in data:
                self._normalize_session(entry)
        return data

    def get_monthly_usage(
        self, *, since: Optional[str] = None, until: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Get monthly aggregated usage report (requires ccusage)."""
        if not self._check_ccusage():
            return None
        data = self._run_ccusage("monthly", since=since, until=until)
        if data:
            for entry in data:
                self._normalize_session(entry)
        return data

    def get_summary(
        self, *, since: Optional[str] = None, until: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get aggregated summary across all sessions.

        Returns:
            Dict with total tokens, cost, and session count.
        """
        sessions = self.get_session_usage(since=since, until=until)

        total_input = 0
        total_output = 0
        total_cache_create = 0
        total_cache_read = 0
        total_cost = 0.0

        for s in sessions:
            total_input += s.get("input_tokens", s.get("inputTokens", 0)) or 0
            total_output += s.get("output_tokens", s.get("outputTokens", 0)) or 0
            total_cache_create += (
                s.get("cache_creation_input_tokens",
                      s.get("cacheCreationInputTokens", 0)) or 0
            )
            total_cache_read += (
                s.get("cache_read_input_tokens",
                      s.get("cacheReadInputTokens", 0)) or 0
            )
            cost = s.get("cost_usd", s.get("costUSD", s.get("total_cost_usd", 0)))
            total_cost += cost or 0.0

        total_tokens = (
            total_input + total_output + total_cache_create + total_cache_read
        )

        return {
            "session_count": len(sessions),
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_creation_input_tokens": total_cache_create,
            "cache_read_input_tokens": total_cache_read,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 4),
        }


# ------------------------------------------------------------------
# CLI formatting helpers
# ------------------------------------------------------------------

def _format_number(n: int) -> str:
    """Format an integer with thousands separators."""
    return f"{n:,}"


def _format_cost(cost: Optional[float]) -> str:
    """Format a cost value as USD."""
    if cost is None:
        return "n/a"
    return f"${cost:.3f}"


def format_session_table(sessions: List[Dict[str, Any]]) -> str:
    """Format session usage data as a CLI table."""
    if not sessions:
        return "No usage data available."

    # Normalize keys
    rows: List[Dict[str, Any]] = []
    for s in sessions:
        task_id = s.get("task_id") or s.get("sessionId", s.get("session_id", ""))[:12]
        input_t = s.get("input_tokens", s.get("inputTokens", 0)) or 0
        output_t = s.get("output_tokens", s.get("outputTokens", 0)) or 0
        cache_create = (
            s.get("cache_creation_input_tokens",
                  s.get("cacheCreationInputTokens", 0)) or 0
        )
        cache_read = (
            s.get("cache_read_input_tokens",
                  s.get("cacheReadInputTokens", 0)) or 0
        )
        total = input_t + output_t + cache_create + cache_read
        cost = s.get("cost_usd", s.get("costUSD", s.get("total_cost_usd")))
        models = s.get("models", [])
        model_str = ", ".join(models) if isinstance(models, list) else str(models or "")

        rows.append({
            "task": str(task_id or "-")[:14],
            "models": model_str[:20] if model_str else "-",
            "input": input_t,
            "output": output_t,
            "cache_create": cache_create,
            "cache_read": cache_read,
            "total": total,
            "cost": cost,
        })

    # Header
    header = (
        f"{'Task':<14}  {'Models':<20}  {'Input':>10}  {'Output':>10}  "
        f"{'Cache Cr.':>10}  {'Cache Rd.':>10}  {'Total':>12}  {'Cost':>10}"
    )
    sep = "-" * len(header)

    lines = [
        "Claude Flow Usage Report",
        "=" * 24,
        "",
        header,
        sep,
    ]

    total_input = 0
    total_output = 0
    total_cache_create = 0
    total_cache_read = 0
    total_tokens = 0
    total_cost = 0.0

    for r in rows:
        lines.append(
            f"{r['task']:<14}  {r['models']:<20}  "
            f"{_format_number(r['input']):>10}  {_format_number(r['output']):>10}  "
            f"{_format_number(r['cache_create']):>10}  {_format_number(r['cache_read']):>10}  "
            f"{_format_number(r['total']):>12}  {_format_cost(r['cost']):>10}"
        )
        total_input += r["input"]
        total_output += r["output"]
        total_cache_create += r["cache_create"]
        total_cache_read += r["cache_read"]
        total_tokens += r["total"]
        total_cost += r["cost"] or 0.0

    lines.append(sep)
    lines.append(
        f"{'Total (' + str(len(rows)) + ')':<14}  {'':<20}  "
        f"{_format_number(total_input):>10}  {_format_number(total_output):>10}  "
        f"{_format_number(total_cache_create):>10}  {_format_number(total_cache_read):>10}  "
        f"{_format_number(total_tokens):>12}  {_format_cost(total_cost):>10}"
    )

    return "\n".join(lines)


def format_daily_table(data: List[Dict[str, Any]]) -> str:
    """Format daily usage data as a CLI table."""
    if not data:
        return "No daily usage data available."

    header = (
        f"{'Date':<12}  {'Input':>10}  {'Output':>10}  "
        f"{'Cache Cr.':>10}  {'Cache Rd.':>10}  {'Total':>12}  {'Cost':>10}"
    )
    sep = "-" * len(header)

    lines = [
        "Daily Usage Report",
        "=" * 18,
        "",
        header,
        sep,
    ]

    total_input = 0
    total_output = 0
    total_cache_create = 0
    total_cache_read = 0
    total_tokens = 0
    total_cost = 0.0

    for d in data:
        date = d.get("date", d.get("day", "-"))
        input_t = d.get("input_tokens", d.get("inputTokens", 0)) or 0
        output_t = d.get("output_tokens", d.get("outputTokens", 0)) or 0
        cache_create = (
            d.get("cache_creation_input_tokens",
                  d.get("cacheCreationInputTokens", 0)) or 0
        )
        cache_read = (
            d.get("cache_read_input_tokens",
                  d.get("cacheReadInputTokens", 0)) or 0
        )
        total = input_t + output_t + cache_create + cache_read
        cost = d.get("cost_usd", d.get("costUSD", d.get("total_cost_usd")))

        lines.append(
            f"{str(date):<12}  "
            f"{_format_number(input_t):>10}  {_format_number(output_t):>10}  "
            f"{_format_number(cache_create):>10}  {_format_number(cache_read):>10}  "
            f"{_format_number(total):>12}  {_format_cost(cost):>10}"
        )
        total_input += input_t
        total_output += output_t
        total_cache_create += cache_create
        total_cache_read += cache_read
        total_tokens += total
        total_cost += cost or 0.0

    lines.append(sep)
    lines.append(
        f"{'Total':<12}  "
        f"{_format_number(total_input):>10}  {_format_number(total_output):>10}  "
        f"{_format_number(total_cache_create):>10}  {_format_number(total_cache_read):>10}  "
        f"{_format_number(total_tokens):>12}  {_format_cost(total_cost):>10}"
    )

    return "\n".join(lines)


def format_summary(summary: Dict[str, Any]) -> str:
    """Format summary data for CLI display."""
    lines = [
        "Usage Summary",
        "=" * 13,
        "",
        f"  Sessions:      {summary.get('session_count', 0)}",
        f"  Input tokens:  {_format_number(summary.get('input_tokens', 0))}",
        f"  Output tokens: {_format_number(summary.get('output_tokens', 0))}",
        f"  Cache create:  {_format_number(summary.get('cache_creation_input_tokens', 0))}",
        f"  Cache read:    {_format_number(summary.get('cache_read_input_tokens', 0))}",
        f"  Total tokens:  {_format_number(summary.get('total_tokens', 0))}",
        f"  Total cost:    {_format_cost(summary.get('total_cost_usd', 0))}",
    ]
    return "\n".join(lines)

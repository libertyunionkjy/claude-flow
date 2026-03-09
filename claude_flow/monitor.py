from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class StreamEvent:
    """解析后的流式事件数据类。"""

    event_type: str      # "tool_use", "text", "error", "result", "system"
    timestamp: str       # ISO 格式时间戳
    content: str         # 事件内容摘要
    raw: dict = field(default_factory=dict)  # 原始 JSON 数据

    def to_dict(self) -> dict:
        """序列化为可存储的 dict（不含 raw 以减小体积）。"""
        d: dict = {
            "type": self.event_type,
            "ts": self.timestamp,
            "content": self.content,
        }
        # tool_use / error 类型额外保留工具名
        if self.event_type in ("tool_use", "error"):
            tool = self.raw.get("tool") or self.raw.get("name", "")
            if tool:
                d["tool"] = tool
        # result 类型保留 cost
        if self.event_type == "result":
            cost = self.raw.get("cost_usd")
            if cost is not None:
                d["cost"] = cost
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StreamEvent":
        """从 dict 反序列化。"""
        return cls(
            event_type=d.get("type", "system"),
            timestamp=d.get("ts", ""),
            content=d.get("content", ""),
            raw=d,
        )


class StreamJsonParser:
    """解析 Claude Code stream-json 格式的输出。

    Claude Code 使用 --output-format stream-json 时，每行输出一个 JSON 对象，
    包含 type 字段标识事件类型。本类将其解析为统一的 StreamEvent 序列。
    """

    def __init__(self) -> None:
        self._events: List[StreamEvent] = []

    def parse_line(self, line: str) -> Optional[StreamEvent]:
        """解析单行 stream-json 输出，返回 StreamEvent 或 None。

        Claude Code stream-json 格式每行一个 JSON 对象，包含：
        - type: "assistant" (content 数组内含 text/tool_use)
        - type: "result", "system", "user" 等
        - 其他字段根据 type 不同而不同

        assistant 消息会被拆分为多个事件（text + tool_use）。
        无法解析的行静默跳过（返回 None）。
        """
        line = line.strip()
        if not line:
            return None

        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None

        # 必须是 dict 且包含 type 字段
        if not isinstance(data, dict) or "type" not in data:
            return None

        now = datetime.now().isoformat()
        event_type = data["type"]

        # assistant 消息：拆分 content 数组为独立事件
        if event_type == "assistant":
            events = self._parse_assistant_message(data, now)
            for ev in events:
                self._events.append(ev)
            return events[-1] if events else None

        elif event_type == "tool_use":
            tool_name = data.get("tool", data.get("name", "unknown"))
            tool_input = data.get("input", {})
            # 对常见工具提取关键参数作为摘要
            summary = self._summarize_tool_input(tool_name, tool_input)
            content = f"{tool_name}: {summary}" if summary else tool_name
            event = StreamEvent(
                event_type="tool_use",
                timestamp=now,
                content=content,
                raw=data,
            )

        elif event_type == "tool_result":
            tool_name = data.get("tool", "unknown")
            # tool_result 可能包含 error 标志
            is_error = data.get("is_error", False)
            if is_error:
                content = f"{tool_name}: ERROR"
                evt_type = "error"
            else:
                content = f"{tool_name}: ok"
                evt_type = "tool_use"
            event = StreamEvent(
                event_type=evt_type,
                timestamp=now,
                content=content,
                raw=data,
            )

        elif event_type == "result":
            cost = data.get("cost_usd")
            result_text = data.get("result", "")
            # 保留完整结果文本，由展示层决定截断
            summary = result_text if isinstance(result_text, str) else str(result_text)
            cost_str = f" (${cost:.4f})" if cost is not None else ""
            content = f"{summary}{cost_str}"
            event = StreamEvent(
                event_type="result",
                timestamp=now,
                content=content,
                raw=data,
            )

        elif event_type == "system":
            message = data.get("message", data.get("text", ""))
            content = str(message)[:200]
            event = StreamEvent(
                event_type="system",
                timestamp=now,
                content=content,
                raw=data,
            )

        else:
            # 未知类型也记录，归类为 system
            content = f"[{event_type}]"
            event = StreamEvent(
                event_type="system",
                timestamp=now,
                content=content,
                raw=data,
            )

        self._events.append(event)
        return event

    def get_events(self) -> List[StreamEvent]:
        """返回所有已解析的事件。"""
        return list(self._events)

    def get_summary(self) -> dict:
        """返回事件统计摘要：tool_use 次数、error 次数、总事件数等。"""
        total = len(self._events)
        tool_use_count = sum(1 for e in self._events if e.event_type == "tool_use")
        error_count = sum(1 for e in self._events if e.event_type == "error")
        text_count = sum(1 for e in self._events if e.event_type == "text")
        result_count = sum(1 for e in self._events if e.event_type == "result")
        system_count = sum(1 for e in self._events if e.event_type == "system")

        return {
            "total": total,
            "tool_use": tool_use_count,
            "error": error_count,
            "text": text_count,
            "result": result_count,
            "system": system_count,
        }

    def get_last_activity(self) -> Optional[str]:
        """返回最近活动的描述（用于状态显示）。"""
        if not self._events:
            return None
        last = self._events[-1]
        return f"{last.event_type}: {last.content}"

    def _parse_assistant_message(self, data: dict, timestamp: str) -> List[StreamEvent]:
        """将 assistant 消息拆分为独立的 text 和 tool_use 事件。"""
        message = data.get("message", {})
        if isinstance(message, str):
            if message.strip():
                return [StreamEvent("text", timestamp, message[:200], raw=data)]
            return []

        content_parts = message.get("content", [])
        if not isinstance(content_parts, list):
            if isinstance(content_parts, str) and content_parts.strip():
                return [StreamEvent("text", timestamp, content_parts[:200], raw=data)]
            return []

        events: List[StreamEvent] = []
        for part in content_parts:
            if not isinstance(part, dict):
                if isinstance(part, str) and part.strip():
                    events.append(StreamEvent("text", timestamp, part[:200], raw=data))
                continue

            part_type = part.get("type", "")

            if part_type == "text":
                text = part.get("text", "").strip()
                if text:
                    events.append(StreamEvent("text", timestamp, text[:200], raw=data))

            elif part_type == "tool_use":
                tool_name = part.get("name", "unknown")
                tool_input = part.get("input", {})
                summary = self._summarize_tool_input(tool_name, tool_input)
                content = f"{tool_name}: {summary}" if summary else tool_name
                # raw 保留 tool 名以便 to_dict() 提取
                tool_raw = {"tool": tool_name, "name": tool_name, "input": tool_input}
                events.append(StreamEvent("tool_use", timestamp, content, raw=tool_raw))

            elif part_type == "tool_result":
                tool_name = part.get("name", "unknown")
                is_error = part.get("is_error", False)
                if is_error:
                    events.append(StreamEvent("error", timestamp, f"{tool_name}: ERROR", raw={"tool": tool_name}))
                else:
                    events.append(StreamEvent("tool_use", timestamp, f"{tool_name}: ok", raw={"tool": tool_name}))

        return events

    @staticmethod
    def _extract_assistant_content(data: dict) -> str:
        """从 assistant 类型事件中提取文本内容（仅用于兼容）。"""
        message = data.get("message", {})
        if isinstance(message, str):
            return message[:200]
        content_parts = message.get("content", [])
        if isinstance(content_parts, list):
            texts = []
            for part in content_parts:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(part.get("text", ""))
                elif isinstance(part, str):
                    texts.append(part)
            combined = " ".join(texts)
            return combined[:200] if combined else ""
        if isinstance(content_parts, str):
            return content_parts[:200]
        return ""

    def to_structured_log(self, task_id: str) -> dict:
        """将解析结果导出为结构化日志 dict，用于 JSON 持久化。"""
        summary = self.get_summary()
        # 从最后一个 result 事件提取 cost
        cost = None
        for e in reversed(self._events):
            if e.event_type == "result" and e.raw.get("cost_usd") is not None:
                cost = e.raw["cost_usd"]
                break
        return {
            "task_id": task_id,
            "summary": summary,
            "cost_usd": cost,
            "events": [e.to_dict() for e in self._events],
        }

    @staticmethod
    def _summarize_tool_input(tool_name: str, tool_input: dict) -> str:
        """对常见工具的 input 提取关键参数作为简要摘要。"""
        if not isinstance(tool_input, dict):
            return ""
        # 文件操作类工具，提取文件路径
        file_path = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("filename")
        if file_path:
            return str(file_path)
        # 命令执行类工具，提取命令内容
        command = tool_input.get("command") or tool_input.get("cmd")
        if command:
            cmd_str = str(command)
            return cmd_str[:80]
        # 其他情况返回 input 的 key 列表
        if tool_input:
            keys = list(tool_input.keys())[:3]
            return ", ".join(keys)
        return ""


class TaskMonitor:
    """管理多个 worker 的实时监控状态。

    将每个 worker 的最新事件状态写入独立的 JSON 文件，
    供 manager 或 web 界面读取以展示实时进度。
    """

    def __init__(self, status_dir: Path) -> None:
        """初始化监控器。

        Args:
            status_dir: 存储实时状态文件的目录。
        """
        self._status_dir = status_dir
        self._status_dir.mkdir(parents=True, exist_ok=True)
        # 内存中维护各 worker 的事件计数
        self._counters: dict[int, dict] = {}

    def _worker_file(self, worker_id: int) -> Path:
        """返回指定 worker 的状态文件路径。"""
        return self._status_dir / f"worker-{worker_id}.json"

    def update_worker_status(
        self, worker_id: int, task_id: str, event: StreamEvent
    ) -> None:
        """更新指定 worker 的最新状态到状态文件。

        每次调用会累加事件计数并将最新状态写入 worker-{id}.json。
        """
        # 初始化或获取计数器
        if worker_id not in self._counters:
            self._counters[worker_id] = {
                "event_count": 0,
                "tool_use_count": 0,
                "error_count": 0,
            }
        counters = self._counters[worker_id]
        counters["event_count"] += 1
        if event.event_type == "tool_use":
            counters["tool_use_count"] += 1
        elif event.event_type == "error":
            counters["error_count"] += 1

        status = {
            "worker_id": worker_id,
            "task_id": task_id,
            "last_event": f"{event.event_type}: {event.content}",
            "event_count": counters["event_count"],
            "tool_use_count": counters["tool_use_count"],
            "error_count": counters["error_count"],
            "updated_at": datetime.now().isoformat(),
        }

        status_file = self._worker_file(worker_id)
        try:
            status_file.write_text(
                json.dumps(status, indent=2, ensure_ascii=False)
            )
        except OSError as e:
            logger.warning(f"无法写入 worker 状态文件 {status_file}: {e}")

    def get_all_status(self) -> dict:
        """获取所有 worker 的当前状态。

        Returns:
            以 worker_id 为键、状态 dict 为值的字典。
        """
        result: dict[int, dict] = {}
        for status_file in sorted(self._status_dir.glob("worker-*.json")):
            try:
                data = json.loads(status_file.read_text())
                worker_id = data.get("worker_id")
                if worker_id is not None:
                    result[worker_id] = data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"无法读取状态文件 {status_file}: {e}")
                continue
        return result

    def clear_worker(self, worker_id: int) -> None:
        """清除指定 worker 的状态。

        删除状态文件并清除内存中的计数器。
        """
        status_file = self._worker_file(worker_id)
        try:
            status_file.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"无法删除状态文件 {status_file}: {e}")
        self._counters.pop(worker_id, None)


def _filter_events(events: list[dict]) -> list[dict]:
    """过滤掉噪音事件：hook、空内容 system、user 类型。"""
    filtered = []
    for ev in events:
        ev_type = ev.get("type", "")
        content = ev.get("content", "")
        # Skip empty system events, hook events, and user-type events
        if ev_type == "system":
            if not content or "hook" in content.lower() or content.startswith("[user]"):
                continue
        filtered.append(ev)
    return filtered


def format_structured_log_for_cli(log_data: dict) -> str:
    """将结构化日志 dict 格式化为终端可读文本。

    Args:
        log_data: to_structured_log() 输出的 dict

    Returns:
        带 ANSI 颜色的格式化文本
    """
    task_id = log_data.get("task_id", "unknown")
    events = _filter_events(log_data.get("events", []))
    summary = log_data.get("summary", {})
    cost = log_data.get("cost_usd")

    # ANSI colors
    DIM = "\033[2m"
    RESET = "\033[0m"
    BOLD = "\033[1m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"

    # type -> (icon, color)
    TYPE_STYLE = {
        "text":     ("MSG", CYAN),
        "tool_use": ("USE", BLUE),
        "error":    ("ERR", RED),
        "result":   ("RES", GREEN),
        "system":   ("SYS", DIM),
    }

    lines: list[str] = []
    header = f"{BOLD}{task_id}{RESET}"
    lines.append(f"{DIM}{'=' * 60}{RESET}")
    lines.append(f"  {header}")
    lines.append(f"{DIM}{'=' * 60}{RESET}")

    # Detect if timestamps are meaningful (distinct values)
    ts_set = {ev.get("ts", "") for ev in events}
    has_real_timestamps = len(ts_set) > 1

    for idx, ev in enumerate(events, 1):
        ev_type = ev.get("type", "system")
        content = ev.get("content", "")
        tool = ev.get("tool", "")

        # Time column: use actual timestamp if distinct, else step number
        if has_real_timestamps:
            ts = ev.get("ts", "")
            ts_col = ts.split("T")[1][:8] if "T" in ts else ts
        else:
            ts_col = f"#{idx:<4}"

        icon, color = TYPE_STYLE.get(ev_type, ("???", DIM))

        if ev_type == "system":
            lines.append(f"  {DIM}{ts_col}  {icon}  {content[:80]}{RESET}")
        elif ev_type in ("tool_use", "error"):
            label = tool or content
            lines.append(f"  {DIM}{ts_col}{RESET}  {color}{icon}{RESET}  {BOLD}{label}{RESET}")
            # Show detail if content has more info beyond the tool name
            detail = content
            if tool and content.startswith(tool):
                detail = content[len(tool):].lstrip(": ")
            if detail:
                lines.append(f"           {DIM}{detail[:100]}{RESET}")
        elif ev_type == "text":
            preview = content[:120].replace("\n", " ")
            lines.append(f"  {DIM}{ts_col}{RESET}  {color}{icon}{RESET}  {preview}")
        elif ev_type == "result":
            cost_str = ""
            if ev.get("cost") is not None:
                cost_str = f"  {YELLOW}${ev['cost']:.4f}{RESET}"
            preview = content[:100].replace("\n", " ")
            lines.append(f"  {DIM}{ts_col}{RESET}  {color}{icon}{RESET}  {preview}{cost_str}")
        else:
            lines.append(f"  {DIM}{ts_col}{RESET}  {icon}  {content[:100]}")

    # footer
    tool_n = summary.get("tool_use", 0)
    err_n = summary.get("error", 0)
    cost_str = f"${cost:.4f}" if cost is not None else "n/a"
    footer_parts = [f"{tool_n} tools", f"{err_n} errors", cost_str]
    footer = " | ".join(footer_parts)
    lines.append(f"{DIM}{'=' * 60}{RESET}")
    lines.append(f"  {footer}")
    lines.append(f"{DIM}{'=' * 60}{RESET}")

    return "\n".join(lines)

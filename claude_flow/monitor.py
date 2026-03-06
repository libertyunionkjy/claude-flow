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
        - type: "assistant", "tool_use", "tool_result", "result", "system" 等
        - 其他字段根据 type 不同而不同

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

        # 根据不同 type 提取内容摘要
        if event_type == "assistant":
            content = self._extract_assistant_content(data)
            event = StreamEvent(
                event_type="text",
                timestamp=now,
                content=content,
                raw=data,
            )

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
            # 截取结果前 100 字符作为摘要
            summary = result_text[:100] if isinstance(result_text, str) else str(result_text)[:100]
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

    @staticmethod
    def _extract_assistant_content(data: dict) -> str:
        """从 assistant 类型事件中提取文本内容。"""
        message = data.get("message", {})
        if isinstance(message, str):
            return message[:200]
        # message 可能是包含 content 数组的对象
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

"""轨迹落盘（sink）。

- TraceSink：中立协议，emit(trace) 把一条已结束的 trace 写出去。
- JsonFileSink：按天建文件夹，一条 trace 一个缩进美化的 JSON 文件（中文不转义）。
                写盘用 asyncio.to_thread 卸载到线程池，绝不阻塞 agent 主循环。
- NullSink：关闭轨迹时使用，emit 直接丢弃。

所有落盘异常都被吞掉（只记日志），保证轨迹功能永不影响 Bot 正常运行。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Protocol

from ncatbot.utils import get_log

from agent_core.tracing.models import Trace

LOG = get_log(__name__)


class TraceSink(Protocol):
    """轨迹输出接口。"""

    async def emit(self, trace: Trace) -> None: ...


class NullSink:
    """空实现：轨迹关闭时使用。"""

    async def emit(self, trace: Trace) -> None:  # noqa: D401
        return None


def _sanitize(part: str) -> str:
    """把 context_id 里的 ':' 等字符换成文件名安全的形式。"""
    return part.replace(":", "-").replace("/", "-").replace("\\", "-")


class JsonFileSink:
    """按天文件夹 + 一 trace 一美化 JSON 文件。"""

    def __init__(self, base_dir: Path | str):
        self._base = Path(base_dir)

    async def emit(self, trace: Trace) -> None:
        try:
            await asyncio.to_thread(self._write, trace)
        except Exception as exc:  # noqa: BLE001
            LOG.error("轨迹落盘失败 trace_id=%s: %s", trace.trace_id, exc)

    def _write(self, trace: Trace) -> None:
        started = trace.started_at  # ISO，如 2026-07-03T03:06:12
        try:
            dt = datetime.fromisoformat(started)
        except ValueError:
            dt = datetime.now()

        day_dir = self._base / dt.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)

        short = trace.trace_id[:6]
        ctx = _sanitize(trace.context_id)
        suffix = "_sub" if trace.parent_trace_id else ""
        filename = f"{dt.strftime('%H%M%S')}_{ctx}_{short}{suffix}.json"

        payload = json.dumps(trace.to_dict(), ensure_ascii=False, indent=2)
        (day_dir / filename).write_text(payload, encoding="utf-8")

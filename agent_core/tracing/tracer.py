"""轨迹采集核心。

设计：用 contextvars 承载"当前 trace"，各层（LLM/工具 wrapper）直接从 contextvar
取当前 trace 往里挂 span，无需改动中立契约、无需层层传参。SubAgent 再次进入
run_trace 时读到父 trace，自动挂成子树。

调用关系：
- pipeline / subagent：进 controller.run 前用 trace_meta(context_id, trigger_type)
  设置本次 run 的元信息（sync 上下文管理器，仅设 contextvar）。
- AgentController.run：用 run_trace(req) 包住 backend.run，负责建/收 trace 并落盘。
- TracingLLMClient / TracingToolProvider：调用 record_llm_span / record_tool_span
  把 span 挂到当前 trace（无当前 trace 时静默 no-op）。

全部采集失败都被吞掉（记日志），保证不影响 Bot 主流程。
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from ncatbot.utils import get_log

from agent_core.tracing.models import LlmSpan, ToolSpan, Trace, _now_iso
from agent_core.tracing.sink import NullSink, TraceSink

if TYPE_CHECKING:
    from agent_core.backends.base import AgentRequest

LOG = get_log(__name__)

# ── 模块级状态（启动时 configure 一次） ──────────────────────────
_enabled: bool = False
_sink: TraceSink = NullSink()

# ── contextvar：当前 trace 与本次 run 的元信息 ───────────────────
_current_trace: ContextVar["Trace | None"] = ContextVar("current_trace", default=None)
_trace_meta: ContextVar["tuple[str, str] | None"] = ContextVar("trace_meta", default=None)


def configure(sink: TraceSink, *, enabled: bool) -> None:
    """启动时配置 sink 与开关。关闭时用 NullSink，运行时零采集。"""
    global _sink, _enabled
    _sink = sink
    _enabled = enabled


def is_enabled() -> bool:
    return _enabled


def current_trace() -> "Trace | None":
    return _current_trace.get()


@contextmanager
def trace_meta(context_id: str, trigger_type: str):
    """设置本次 run 的元信息（context_id / trigger_type），供 run_trace 读取。"""
    token = _trace_meta.set((context_id, trigger_type))
    try:
        yield
    finally:
        _trace_meta.reset(token)


def _serialize_content(user_content: Any) -> Any:
    """把中立 ContentPart 列表序列化为纯 JSON 结构。"""
    from agent_core.backends.base import ImagePart, TextPart

    if not isinstance(user_content, (list, tuple)):
        return str(user_content)
    out = []
    for p in user_content:
        if isinstance(p, TextPart):
            out.append({"type": "text", "text": p.text})
        elif isinstance(p, ImagePart):
            out.append({"type": "image", "url": p.url})
        else:
            out.append({"type": "unknown", "repr": str(p)})
    return out


@asynccontextmanager
async def run_trace(req: "AgentRequest"):
    """包住一次 agent run：建 trace → 交给 backend → finalize → 落盘。

    返回 trace（关闭时为 None）。调用方在 with 体内执行 backend.run，
    结束后可调用 finalize_trace 写入 stop_reason / iterations。
    """
    if not _enabled:
        yield None
        return

    try:
        meta = _trace_meta.get()
        context_id, trigger_type = meta if meta else ("unknown", "unknown")
        parent = _current_trace.get()
        trace = Trace.create(
            context_id=context_id,
            trigger_type=trigger_type,
            parent_trace_id=parent.trace_id if parent else None,
            system_prompt=req.system_prompt,
            user_content=_serialize_content(req.user_content),
            model=req.model,
        )
    except Exception as exc:  # noqa: BLE001
        LOG.error("轨迹创建失败，本次 run 不采集: %s", exc)
        yield None
        return

    token = _current_trace.set(trace)
    try:
        yield trace
    finally:
        _current_trace.reset(token)
        try:
            await _sink.emit(trace)
        except Exception as exc:  # noqa: BLE001
            LOG.error("轨迹 emit 失败 trace_id=%s: %s", trace.trace_id, exc)


def finalize_trace(trace: "Trace | None", *, stop_reason: str | None,
                   iterations: int, error: str | None = None) -> None:
    """把 RunResult 的结果写回 trace（no-op 当 trace 为 None）。"""
    if trace is None:
        return
    try:
        trace.finalize(stop_reason=stop_reason, iterations=iterations, error=error)
    except Exception as exc:  # noqa: BLE001
        LOG.error("轨迹 finalize 失败: %s", exc)

def record_llm_span(
    *,
    started_at: str,
    elapsed_ms: float | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    model: str | None,
    response_content: str,
    tool_calls: list[dict],
    reasoning_content: str | None = None,
) -> None:
    trace = _current_trace.get()
    if trace is None:
        return
    try:
        trace.add_span(LlmSpan(
            seq=trace.next_seq(),
            started_at=started_at,
            elapsed_ms=elapsed_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model=model,
            response_content=response_content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
        ))
    except Exception as exc:  # noqa: BLE001
        LOG.error("记录 llm span 失败: %s", exc)


def record_tool_span(
    *,
    name: str,
    started_at: str,
    elapsed_ms: float | None,
    arguments: Any,
    result: str,
    is_error: bool,
) -> None:
    trace = _current_trace.get()
    if trace is None:
        return
    try:
        trace.add_span(ToolSpan(
            seq=trace.next_seq(),
            name=name,
            started_at=started_at,
            elapsed_ms=elapsed_ms,
            arguments=arguments,
            result=result,
            is_error=is_error,
        ))
    except Exception as exc:  # noqa: BLE001
        LOG.error("记录 tool span 失败: %s", exc)

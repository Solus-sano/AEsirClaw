"""Agent 调用轨迹记录（数据回流）。

用法（启动接线处）：
    from agent_core import tracing
    tracing.configure(tracing.JsonFileSink("data/traces"), enabled=True)
    llm = tracing.TracingLLMClient(llm)                # 包装后交给 backend
    provider = tracing.TracingToolProvider(provider)   # 包装后交给 pipeline/subagent

调用链只需：
    - controller.run 用 tracing.run_trace(req) 包住 backend.run
    - pipeline / subagent 进 run 前用 tracing.trace_meta(context_id, trigger_type)
"""

from agent_core.tracing.models import LlmSpan, ToolSpan, Trace
from agent_core.tracing.sink import JsonFileSink, NullSink, TraceSink
from agent_core.tracing.tracer import (
    configure,
    current_trace,
    finalize_trace,
    is_enabled,
    record_llm_span,
    record_tool_span,
    run_trace,
    trace_meta,
)
from agent_core.tracing.wrappers import TracingLLMClient, TracingToolProvider

__all__ = [
    "Trace",
    "LlmSpan",
    "ToolSpan",
    "TraceSink",
    "JsonFileSink",
    "NullSink",
    "configure",
    "is_enabled",
    "current_trace",
    "run_trace",
    "trace_meta",
    "finalize_trace",
    "record_llm_span",
    "record_tool_span",
    "TracingLLMClient",
    "TracingToolProvider",
]

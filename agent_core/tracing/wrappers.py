"""透明装饰器：包装 LLMClient / ToolProvider，采集 span。

推理核心（NativeLoopBackend）完全不感知——它拿到的仍是 chat() / call() 接口。
只有轨迹开启时才在启动接线处套上这两个 wrapper，关闭时不套，运行时零开销。
未处理任何异常，采集失败在 tracer 内部已被吞掉；这里只负责"透传 + 记录"。
"""

from __future__ import annotations

import json
import time
from typing import Any

from agent_core.llm import ChatMessage, LLMClient, LLMResponse
from agent_core.tracing import tracer
from agent_core.tracing.models import _now_iso

from agent_core.tools.provider import ToolProvider, ToolSchema, ToolResult, tool_result_text


class TracingLLMClient:
    """包一个 LLMClient，chat() 调用后把本轮增量输出记入当前 trace。"""

    def __init__(self, inner: LLMClient):
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        # 未显式包装的属性（model / temperature / client 等）透传给内部实例
        return getattr(self._inner, name)

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        started_at = _now_iso()
        response = await self._inner.chat(messages, tools=tools, model=model)
        try:
            tool_calls = [
                {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                for tc in response.tool_calls
            ]
            tracer.record_llm_span(
                started_at=started_at,
                elapsed_ms=response.latency_ms,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                total_tokens=response.total_tokens,
                model=model or getattr(self._inner, "model", None),
                response_content=response.content or "",
                reasoning_content=response.reasoning_content,
                tool_calls=tool_calls,
            )
        except Exception:  # noqa: BLE001, S110
            pass
        return response


def _looks_like_error(result: str) -> bool:
    """best-effort：McpToolProvider 出错时返回 {"error": ...} 结构。"""
    s = result.lstrip()
    if not s.startswith("{"):
        return False
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(obj, dict) and "error" in obj


class TracingToolProvider:
    """包一个 ToolProvider，call() 调用后把工具入参/返回/耗时记入当前 trace。"""

    def __init__(self, inner: ToolProvider):
        self._inner = inner

    async def list_schemas(self) -> list[ToolSchema]:
        return await self._inner.list_schemas()

    async def call(self, name: str, args: dict) -> ToolResult:
        started_at = _now_iso()
        t0 = time.monotonic()
        result = await self._inner.call(name, args)
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        try:
            tracer.record_tool_span(
                name=name,
                started_at=started_at,
                elapsed_ms=elapsed_ms,
                arguments=args,
                result=tool_result_text(result),
                is_error=_looks_like_error(tool_result_text(result)),
            )
        except Exception:  # noqa: BLE001, S110
            pass
        return result

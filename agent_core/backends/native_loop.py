"""NativeLoopBackend：手写的 Tool Calling 循环。

从原 AgentController 迁移而来。改动点：
1. 工具 schema / 执行均经由 ToolProvider，不再直接依赖 FastMCP。
2. 同一轮的多个 tool_call 并行执行（asyncio.gather），结果按原顺序回填。
3. 循环结束时返回 RunResult（final_text 即最后一次无 tool_call 的 content）。

所有用户可见输出仍通过工具调用完成；主 agent 的 final_text 由调用方忽略，
subagent 的 final_text 即其报告。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agent_core.backends.base import (
    AgentRequest,
    ContentPart,
    ImagePart,
    RunResult,
    TextPart,
)
from agent_core.llm import ChatMessage, LLMClient
from ncatbot.utils import get_log

LOG = get_log(__name__)


def _truncate(s: str, max_len: int) -> str:
    return s if len(s) <= max_len else s[:max_len] + "..."


def _content_to_openai(parts: list[ContentPart]) -> str | list[dict]:
    """中立 ContentPart → OpenAI content（纯文本退化为 str，含图片用数组）。"""
    if not parts:
        return ""
    if len(parts) == 1 and isinstance(parts[0], TextPart):
        return parts[0].text
    out: list[dict] = []
    for p in parts:
        if isinstance(p, ImagePart):
            out.append({"type": "image_url", "image_url": {"url": p.url}})
        else:
            out.append({"type": "text", "text": p.text})
    return out


def _build_messages(req: AgentRequest) -> list[ChatMessage]:
    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=req.system_prompt),
        ChatMessage(role="user", content=_content_to_openai(req.user_content)),
    ]
    return messages


def _assistant_message(response: Any) -> ChatMessage:
    """从 LLM 响应构造 assistant 消息，优先保留原始字段。"""
    msg = response.raw_message if isinstance(response.raw_message, dict) else {}
    if not msg:
        msg = {"role": "assistant"}
        if response.content:
            msg["content"] = response.content
        if response.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in response.tool_calls
            ]
    return ChatMessage(role="assistant", content=msg)


def _tool_result_message(tool_call_id: str, result: str) -> ChatMessage:
    return ChatMessage(
        role="tool",
        content={"role": "tool", "tool_call_id": tool_call_id, "content": result},
    )


def _schemas_to_openai(schemas) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "parameters": s.parameters,
            },
        }
        for s in schemas
    ]


class NativeLoopBackend:
    """手写 Agent Loop。无状态，可被所有 context 共享。"""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def run(self, req: AgentRequest) -> RunResult:
        tools = _schemas_to_openai(await req.tools.list_schemas())
        messages = _build_messages(req)
        final_text = ""

        for i in range(req.max_iterations):
            response = await self.llm.chat(messages, tools=tools, model=req.model)

            if not response.tool_calls:
                final_text = response.content or ""
                if final_text:
                    LOG.info("[Agent Loop] 迭代 %d: 无 tool_call, 结束。content: %s",
                             i, _truncate(final_text, 200))
                else:
                    LOG.info("[Agent Loop] 迭代 %d: 无 tool_call, 无 content, 结束", i)
                return RunResult(final_text=final_text, stop_reason="completed", iterations=i + 1)

            messages.append(_assistant_message(response))
            LOG.info(f"\033[92m[Agent Loop] 迭代 {i}: messages: {messages} \n\n \033[0m")

            # 同一轮的多个 tool_call 并行执行，结果按原顺序回填
            results = await asyncio.gather(
                *[self._exec_tool(req, tc) for tc in response.tool_calls]
            )
            for tc, result_str in zip(response.tool_calls, results):
                messages.append(_tool_result_message(tc.id, result_str))

        LOG.warning("[Agent Loop] 达到最大迭代次数 %d，强制结束", req.max_iterations)
        return RunResult(final_text=final_text, stop_reason="max_iter", iterations=req.max_iterations)

    async def _exec_tool(self, req: AgentRequest, tc) -> str:
        tool_name = tc.function.name
        try:
            tool_args = json.loads(tc.function.arguments)
        except json.JSONDecodeError:
            tool_args = {}
            LOG.warning("[Agent Loop] tool_call 参数解析失败: %s", tc.function.arguments)

        LOG.info("[Agent Loop] 调用工具: %s(%s)", tool_name,
                 _truncate(json.dumps(tool_args, ensure_ascii=False), 200))
        try:
            result_str = await req.tools.call(tool_name, tool_args)
        except Exception as exc:  # noqa: BLE001
            result_str = json.dumps({"error": str(exc)}, ensure_ascii=False)
            LOG.error("[Agent Loop] 工具 %s 执行失败: %s", tool_name, exc)

        LOG.info("[Agent Loop] 工具 %s 返回: %s", tool_name, _truncate(result_str, 300))
        return result_str

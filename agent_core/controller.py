"""Agent Loop 引擎。接收已组装的 messages，执行 tool_call 循环。

所有用户可见输出通过工具调用完成，LLM 的 content 字段视为内部思考。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from agent_core.llm import ChatMessage, LLMClient
from ncatbot.utils import get_log

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

LOG = get_log(__name__)


def mcp_tools_to_openai_format(mcp_server: FastMCP) -> list[dict]:
    """将 FastMCP 注册的工具定义转为 OpenAI function calling 格式。"""
    tools: list[dict] = []
    for tool in mcp_server._tool_manager.list_tools():
        tools.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.parameters,
            },
        })
    return tools


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
    """构造 tool 结果消息。"""
    return ChatMessage(
        role="tool",
        content={"role": "tool", "tool_call_id": tool_call_id, "content": result},
    )


class AgentController:
    """Agent Loop 引擎。所有用户可见输出通过工具调用完成。"""

    def __init__(
        self,
        llm: LLMClient,
        mcp: FastMCP,
        *,
        max_iterations: int = 10,
    ):
        self.llm = llm
        self.mcp = mcp
        self.max_iterations = max_iterations
        self.tools = mcp_tools_to_openai_format(mcp)

    async def run(self, messages: list[ChatMessage]) -> None:
        """执行 Agent Loop。

        Args:
            messages: 已组装好的上下文（system prompt + 记忆 + 当前输入）
        """
        for i in range(self.max_iterations):
            response = await self.llm.chat(messages, tools=self.tools)
            LOG.info(f'\033[92mresponse: {response}\033[0m')

            if not response.tool_calls:
                if response.content:
                    LOG.info(
                        "[Agent Loop] 迭代 %d: 无 tool_call, content 视为内部思考: %s",
                        i, response.content[:200],
                    )
                else:
                    LOG.info("[Agent Loop] 迭代 %d: 无 tool_call, 无 content, 结束", i)
                break

            # 追加 assistant 消息（含 tool_calls）
            messages.append(_assistant_message(response))

            # 执行每个 tool_call 并追加结果
            for tc in response.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}
                    LOG.warning("[Agent Loop] tool_call 参数解析失败: %s", tc.function.arguments)

                LOG.info("[Agent Loop] 调用工具: %s(%s)", tool_name, _truncate(json.dumps(tool_args, ensure_ascii=False), 200))

                try:
                    result = await self.mcp._tool_manager.call_tool(tool_name, tool_args)
                    result_str = str(result) if result is not None else ""
                except Exception as exc:
                    result_str = json.dumps({"error": str(exc)}, ensure_ascii=False)
                    LOG.error("[Agent Loop] 工具 %s 执行失败: %s", tool_name, exc)

                LOG.info("[Agent Loop] 工具 %s 返回: %s", tool_name, _truncate(result_str, 300))
                messages.append(_tool_result_message(tc.id, result_str))
        else:
            LOG.warning("[Agent Loop] 达到最大迭代次数 %d，强制结束", self.max_iterations)


def _truncate(s: str, max_len: int) -> str:
    return s if len(s) <= max_len else s[:max_len] + "..."

"""LLM 客户端封装，支持 function calling 和调用日志。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence, Union

from openai import AsyncOpenAI
from ncatbot.utils import get_log

LOG = get_log(__name__)
LLM_LOG = get_log("llm_trace")


@dataclass
class ChatMessage:
    """轻量级消息结构。content 可以是纯字符串或 dict（用于 tool/assistant 消息）。"""

    role: str
    content: Union[str, Dict[str, Any], Iterable[Any]]


@dataclass
class ToolCallFunction:
    name: str
    arguments: str


@dataclass
class ToolCall:
    id: str
    function: ToolCallFunction


@dataclass
class LLMResponse:
    """LLM 返回结构。"""

    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw_message: Dict[str, Any] = field(default_factory=dict)


class LLMClient:
    """大模型客户端封装，读取 AppConfig 配置并发送聊天补全请求。"""

    def __init__(self, config):
        """初始化 LLM 客户端。

        Args:
            config: AppConfig 实例，或含 llm 子节的 dict。
        """
        if hasattr(config, "llm"):
            llm_cfg = config.llm
        elif isinstance(config, dict):
            llm_cfg = config.get("llm", config)
        else:
            llm_cfg = {}

        self.model: str = llm_cfg.get("model", "")
        if not self.model:
            raise ValueError("LLM 模型未配置")

        base_url = llm_cfg.get("model_base_url") or llm_cfg.get("base_url")
        api_key = llm_cfg.get("api_key")
        if not api_key:
            raise ValueError("LLM API Key 未配置")

        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def chat(
        self,
        messages: List[ChatMessage],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """调用聊天补全接口并返回统一响应结构。"""
        payload = self._build_payload(messages)
        kwargs: dict[str, Any] = {"model": self.model, "messages": payload}
        if tools:
            kwargs["tools"] = tools

        t0 = time.monotonic()
        completion = await self.client.chat.completions.create(**kwargs)
        elapsed_ms = (time.monotonic() - t0) * 1000

        choice = completion.choices[0].message
        content = self._normalize_content(choice.content)
        tool_calls = self._parse_tool_calls(getattr(choice, "tool_calls", None))
        raw_message = self._dump_raw_message(choice)

        # ── 专用日志 ────────────────────────────────────────
        usage = getattr(completion, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", "?") if usage else "?"
        completion_tokens = getattr(usage, "completion_tokens", "?") if usage else "?"
        tool_names = [tc.function.name for tc in tool_calls] if tool_calls else []

        LLM_LOG.info(
            "model=%s | prompt_tokens=%s | completion_tokens=%s | "
            "tool_calls=%s | elapsed=%.0fms | has_content=%s",
            self.model,
            prompt_tokens,
            completion_tokens,
            tool_names or "none",
            elapsed_ms,
            bool(content),
        )

        return LLMResponse(content=content, tool_calls=tool_calls, raw_message=raw_message)

    @staticmethod
    def _build_payload(messages: List[ChatMessage]) -> list[dict]:
        """将 ChatMessage 列表转为 API payload。"""
        payload = []
        for m in messages:
            if isinstance(m.content, dict):
                payload.append(m.content)
            else:
                payload.append({"role": m.role, "content": m.content})
        return payload

    @staticmethod
    def _parse_tool_calls(raw_tool_calls) -> List[ToolCall]:
        """解析 OpenAI 返回的 tool_calls。"""
        if not raw_tool_calls:
            return []
        result = []
        for tc in raw_tool_calls:
            result.append(ToolCall(
                id=tc.id,
                function=ToolCallFunction(
                    name=tc.function.name,
                    arguments=tc.function.arguments or "{}",
                ),
            ))
        return result

    @staticmethod
    def _dump_raw_message(message: Any) -> Dict[str, Any]:
        """保留 assistant 原始消息，避免 tool 循环丢失扩展字段。"""
        if isinstance(message, dict):
            return dict(message)
        if hasattr(message, "model_dump"):
            return message.model_dump(exclude_none=True)

        raw: Dict[str, Any] = {"role": "assistant"}
        content = getattr(message, "content", None)
        if content is not None:
            raw["content"] = content
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            raw["tool_calls"] = tool_calls
        return raw

    @staticmethod
    def _normalize_content(content: Any) -> str:
        """兼容字符串或分段内容的返回格式。"""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, Iterable):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    parts.append(part.get("text", ""))
                else:
                    parts.append(str(part))
            return "".join(parts)
        return str(content)

"""Backend 抽象层的中立契约。

AgentController 永远调用 AgentBackend，外部不感知底层是手写 loop 还是
未来的 Pi / Claude Code SDK。输入用语义化的 AgentRequest（system_prompt +
中立多模态 user_content + 工具集），而非 OpenAI 专属的 messages 数组。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent_core.tools.provider import ToolProvider


@dataclass
class TextPart:
    """中立的文本片段。"""

    text: str


@dataclass
class ImagePart:
    """中立的图片片段。url 为 http(s) 链接或 data: URL。"""

    url: str


ContentPart = TextPart | ImagePart


@dataclass
class Turn:
    """中立的会话轮次。为未来的 history / resume 预留，当前不使用。"""

    role: str  # "user" | "assistant"
    content: str


@dataclass
class AgentRequest:
    """一次 agent 运行的全部输入。"""

    system_prompt: str
    user_content: list[ContentPart]
    tools: "ToolProvider"
    max_iterations: int = 10
    # 预留：subagent 可用更便宜/快的模型。v1 不填（None = 用 backend 默认模型）。
    model: str | None = None
    # 预留：跨 run 的会话历史 / 可续接句柄。当前群聊私聊均 stateless 重渲染，不使用。
    history: list[Turn] | None = None
    session_id: str | None = None


@dataclass
class RunResult:
    """一次 agent 运行的结果。subagent 的报告即 final_text。"""

    final_text: str
    stop_reason: str  # "completed" | "max_iter" | "error"
    iterations: int = 0
    session_id: str | None = None  # 预留：backend 可回吐可续接句柄
    error: str | None = None


class AgentBackend(Protocol):
    """推理核心的抽象接口。NativeLoop / Pi / ClaudeCode 各自实现。"""

    async def run(self, req: AgentRequest) -> RunResult: ...

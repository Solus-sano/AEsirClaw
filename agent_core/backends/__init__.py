"""Backend 抽象与实现。"""

from agent_core.backends.base import (
    AgentBackend,
    AgentRequest,
    ContentPart,
    ImagePart,
    RunResult,
    TextPart,
    Turn,
)
from agent_core.backends.native_loop import NativeLoopBackend

__all__ = [
    "AgentBackend",
    "AgentRequest",
    "RunResult",
    "Turn",
    "ContentPart",
    "TextPart",
    "ImagePart",
    "NativeLoopBackend",
]

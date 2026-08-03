"""轨迹数据模型。

一次完整的 agent run（主 Agent 或 SubAgent）对应一个 Trace，内部按发生顺序
挂载若干 Span（LLM 调用 / 工具调用）。Trace 之间通过 parent_trace_id 形成树，
用于还原 SubAgent 的嵌套关系。

所有结构均可 to_dict() 序列化为纯 JSON，供 sink 落盘。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _new_id() -> str:
    return uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class LlmSpan:
    """一次 LLM 调用（事件日志式：只记录本轮新产生的输出）。

    完整的 request_messages 不逐轮快照，而是靠"初始输入(trace 顶层) + 按序回放
    各 span"来无损重建，避免长循环下的平方级膨胀。这里只存本轮 assistant 的
    增量输出（content + tool_calls）与观测指标。
    """

    seq: int
    started_at: str
    elapsed_ms: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    model: str | None
    response_content: str
    tool_calls: list[dict]
    reasoning_content: str | None = None
    type: str = "llm_call"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "seq": self.seq,
            "started_at": self.started_at,
            "elapsed_ms": self.elapsed_ms,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "response_content": self.response_content,
            "reasoning_content": self.reasoning_content,
            "tool_calls": self.tool_calls,
        }


@dataclass
class ToolSpan:
    """一次工具调用。full 粒度：保留完整入参与返回。"""

    seq: int
    name: str
    started_at: str
    elapsed_ms: float | None
    arguments: Any
    result: str
    is_error: bool
    type: str = "tool_call"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "seq": self.seq,
            "name": self.name,
            "started_at": self.started_at,
            "elapsed_ms": self.elapsed_ms,
            "arguments": self.arguments,
            "result": self.result,
            "is_error": self.is_error,
        }


@dataclass
class Trace:
    """一次完整 agent run 的轨迹。"""

    trace_id: str
    parent_trace_id: str | None
    context_id: str
    trigger_type: str
    model: str | None
    system_prompt: str
    user_content: Any
    started_at: str = field(default_factory=_now_iso)
    ended_at: str | None = None
    stop_reason: str | None = None
    iterations: int = 0
    error: str | None = None
    spans: list = field(default_factory=list)
    _t0: float = field(default_factory=time.monotonic, repr=False)
    _seq: int = field(default=0, repr=False)

    @classmethod
    def create(
        cls,
        *,
        context_id: str,
        trigger_type: str,
        parent_trace_id: str | None,
        system_prompt: str,
        user_content: Any,
        model: str | None,
    ) -> "Trace":
        return cls(
            trace_id=_new_id(),
            parent_trace_id=parent_trace_id,
            context_id=context_id,
            trigger_type=trigger_type,
            model=model,
            system_prompt=system_prompt,
            user_content=user_content,
        )

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def add_span(self, span: Any) -> None:
        self.spans.append(span)

    def finalize(self, *, stop_reason: str | None, iterations: int, error: str | None) -> None:
        self.ended_at = _now_iso()
        self.stop_reason = stop_reason
        self.iterations = iterations
        self.error = error

    @property
    def duration_ms(self) -> float:
        return (time.monotonic() - self._t0) * 1000

    def _token_totals(self) -> dict:
        prompt = completion = total = 0
        for s in self.spans:
            if isinstance(s, LlmSpan):
                prompt += s.prompt_tokens or 0
                completion += s.completion_tokens or 0
                total += s.total_tokens or 0
        return {"prompt": prompt, "completion": completion, "total": total}

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "parent_trace_id": self.parent_trace_id,
            "context_id": self.context_id,
            "trigger_type": self.trigger_type,
            "model": self.model,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": round(self.duration_ms, 1),
            "stop_reason": self.stop_reason,
            "iterations": self.iterations,
            "error": self.error,
            "tokens": self._token_totals(),
            "system_prompt": self.system_prompt,
            "user_content": self.user_content,
            "spans": [s.to_dict() for s in self.spans],
        }

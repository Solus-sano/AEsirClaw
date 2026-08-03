"""AgentController：稳定的 façade，外部永远调用它。

它本身不实现推理逻辑，只把 AgentRequest 委派给可插拔的 AgentBackend：

    AgentController              # 稳定 façade
      └── AgentBackend           # 抽象接口
            ├── NativeLoopBackend    # 现在手写的 loop
            ├── PiBackend            # 未来接 Pi Agents SDK
            └── ClaudeCodeBackend    # 未来接 Claude Code
"""

from __future__ import annotations

from agent_core import tracing
from agent_core.backends.base import AgentBackend, AgentRequest, RunResult


class AgentController:
    """Agent 运行入口。委派给具体 backend 执行。"""

    def __init__(self, backend: AgentBackend):
        self.backend = backend

    async def run(self, req: AgentRequest) -> RunResult:
        """执行一次 agent 运行，返回结果（subagent 报告即 RunResult.final_text）。

        用 tracing.run_trace 包住 backend.run：这是主 Agent 与 SubAgent 唯一的
        公共 choke point，能在此建立/收尾一条 trace 并落盘。轨迹关闭时 run_trace
        是 no-op，行为与包装前完全一致。
        """
        async with tracing.run_trace(req) as trace:
            result = await self.backend.run(req)
            tracing.finalize_trace(
                trace,
                stop_reason=result.stop_reason,
                iterations=result.iterations,
                error=result.error,
            )
            return result

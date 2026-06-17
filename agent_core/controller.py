"""AgentController：稳定的 façade，外部永远调用它。

它本身不实现推理逻辑，只把 AgentRequest 委派给可插拔的 AgentBackend：

    AgentController              # 稳定 façade
      └── AgentBackend           # 抽象接口
            ├── NativeLoopBackend    # 现在手写的 loop
            ├── PiBackend            # 未来接 Pi Agents SDK
            └── ClaudeCodeBackend    # 未来接 Claude Code
"""

from __future__ import annotations

from agent_core.backends.base import AgentBackend, AgentRequest, RunResult


class AgentController:
    """Agent 运行入口。委派给具体 backend 执行。"""

    def __init__(self, backend: AgentBackend):
        self.backend = backend

    async def run(self, req: AgentRequest) -> RunResult:
        """执行一次 agent 运行，返回结果（subagent 报告即 RunResult.final_text）。"""
        return await self.backend.run(req)

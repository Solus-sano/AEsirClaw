"""工具供给抽象层。

这是整个项目唯一接触 MCP（client/server 协议）的地方。Backend 只依赖
ToolProvider 接口，不直接碰 FastMCP / ClientSession，从而与具体工具实现解耦。

- McpToolProvider   ：包一个已连接的 in-memory MCP ClientSession。
- FilteredToolProvider：白名单视图，给 subagent 裁剪工具集（结构上防递归/防发错会话）。
- McpConnection     ：长生命周期的 in-memory server+client 连接（keeper-task 模式，
                      避免 anyio cancel-scope 跨任务退出问题）。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from ncatbot.utils import get_log

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

LOG = get_log(__name__)


@dataclass
class ToolSchema:
    """中立的工具描述。parameters 为 JSON Schema。"""

    name: str
    description: str
    parameters: dict


class ToolProvider(Protocol):
    """工具供给接口：列 schema + 执行。"""

    async def list_schemas(self) -> list[ToolSchema]: ...

    async def call(self, name: str, args: dict) -> str: ...


def _extract_text(result) -> str:
    """从 CallToolResult.content 抽取文本（拼接所有 text 块）。"""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts)


class McpToolProvider:
    """包一个已连接的 MCP ClientSession，对外暴露中立 ToolProvider 接口。"""

    def __init__(self, session: ClientSession):
        self._session = session

    async def list_schemas(self) -> list[ToolSchema]:
        resp = await self._session.list_tools()
        return [
            ToolSchema(
                name=t.name,
                description=t.description or "",
                parameters=t.inputSchema,
            )
            for t in resp.tools
        ]

    async def call(self, name: str, args: dict) -> str:
        result = await self._session.call_tool(name, args)
        text = _extract_text(result)
        if getattr(result, "isError", False):
            return json.dumps({"error": text or "工具执行失败"}, ensure_ascii=False)
        return text


class FilteredToolProvider:
    """白名单视图：只暴露 include 中的工具，并拒绝对其它工具的调用。

    用于 subagent —— 结构上拿不到 send_* / dispatch_subagent / 定时任务工具，
    天然防递归、防发错会话。白名单（而非黑名单）保证未来新增工具不会误泄漏。
    """

    def __init__(self, inner: ToolProvider, *, include: set[str]):
        self._inner = inner
        self._include = set(include)

    async def list_schemas(self) -> list[ToolSchema]:
        return [s for s in await self._inner.list_schemas() if s.name in self._include]

    async def call(self, name: str, args: dict) -> str:
        if name not in self._include:
            return json.dumps(
                {"error": f"工具 '{name}' 在当前 subagent 中不可用"},
                ensure_ascii=False,
            )
        return await self._inner.call(name, args)


class McpConnection:
    """持有一个 in-memory 连接的 MCP server+client session（长生命周期）。

    用后台 keeper task 持有 `async with`、靠 stop event 收尾：保证连接 ctx 的
    enter / exit 都发生在同一个 task 内，规避 anyio task-group 的 cancel-scope
    必须同任务退出的限制。
    """

    def __init__(self, mcp: "FastMCP"):
        self._mcp = mcp
        self._session: ClientSession | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._error: BaseException | None = None

    async def start(self) -> ClientSession:
        self._task = asyncio.create_task(self._serve())
        await self._ready.wait()
        if self._error is not None:
            raise self._error
        assert self._session is not None
        return self._session

    async def _serve(self) -> None:
        try:
            async with create_connected_server_and_client_session(self._mcp) as session:
                self._session = session
                self._ready.set()
                await self._stop.wait()
        except Exception as exc:  # noqa: BLE001
            self._error = exc
            LOG.error("MCP in-memory 连接异常: %s", exc)
            self._ready.set()

    async def aclose(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except Exception:  # noqa: BLE001
                pass

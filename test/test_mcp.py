import sys
import os
sys.path.append(os.path.abspath("./"))
import asyncio
from agent_core.tools.mcp_tools import create_mcp_server
from agent_core.tools.provider import McpConnection, McpToolProvider
from agent_core.pipeline import _list_skills


async def main():
    mcp = create_mcp_server(outputter=None, bot_api=None, memory=None, executor=None)
    conn = McpConnection(mcp)
    session = await conn.start()
    provider = McpToolProvider(session)
    for s in await provider.list_schemas():
        print(s.name, s.description, s.parameters)
    await conn.aclose()
    print(_list_skills())


asyncio.run(main())

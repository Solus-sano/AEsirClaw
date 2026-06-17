import os
import sys
sys.path.append(os.path.abspath("./"))
import asyncio
from agent_core.tools.mcp_tools import create_mcp_server
from agent_core.memory.short_term import ShortTermMemory
from agent_core.tools.docker_executor import LocalExecutor, DockerExecutor
from agent_core.tools.provider import McpConnection, McpToolProvider


class M:
    async def send_group(self, *a): pass
    async def send_private(self, *a): pass


class A:
    async def post_group_msg(self, **kw): pass
    async def post_group_file(self, **kw): pass
    async def get_group_msg_history(self, **kw): return []


async def test():
    mcp = create_mcp_server(outputter=M(), bot_api=A(), memory=ShortTermMemory(api=A()), executor=DockerExecutor())
    conn = McpConnection(mcp)
    session = await conn.start()
    provider = McpToolProvider(session)

    tools = await provider.list_schemas()
    et = [t for t in tools if t.name == 'execute_task'][0]
    print('参数:', list(et.parameters.get('properties', {}).keys()))
    print('描述前100字:', et.description[:100])

    r = await provider.call('execute_task', {'command': 'echo OK && date'})
    print('\033[92mecho、结果: \033[0m', r)
    r = await provider.call('execute_task', {'command': 'pwd'})
    print('\033[92m pwd 结果: \033[0m', r)
    r = await provider.call('execute_task', {'command': 'ls ./'})
    print('\033[92m ls ./ 结果: \033[0m', r)
    r = await provider.call('execute_task', {'command': 'ls /skills'})
    print('\033[92m ls /skills 结果: \033[0m', r)
    r = await provider.call('execute_task', {'command': 'python /skills/web/src/search.py "hacker news"'})
    print('\033[92msearch.py "hacker news" 结果: \033[0m', r)
    r = await provider.call('execute_task', {'command': 'python /skills/web/src/scrape.py "https://news.ycombinator.com"'})
    print('\033[92mscrape.py 结果: \033[0m', r)
    r = await provider.call('execute_task', {'command': 'find /workspace -name \\"*frp*\\" -type f 2>/dev/null | head -20'})
    print('\033[92mfind /workspace 结果: \033[0m', r)

    await conn.aclose()


asyncio.run(test())

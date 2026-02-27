import os
import sys
sys.path.append(os.path.abspath("./"))
import asyncio
from agent_core.tools.mcp_tools import create_mcp_server
from agent_core.tools.docker_executor import LocalExecutor, DockerExecutor
from agent_core.controller import mcp_tools_to_openai_format

class M:
    async def send_group(self, *a): pass
    async def send_private(self, *a): pass
class A:
    async def post_group_msg(self, **kw): pass
    async def post_group_file(self, **kw): pass
    async def get_group_msg_history(self, **kw): return []

mcp = create_mcp_server(outputter=M(), bot_api=A(), executor=DockerExecutor())
tools = mcp_tools_to_openai_format(mcp)

# 检查 execute_task 参数名已从 code 改为 command
et = [t for t in tools if t['function']['name'] == 'execute_task'][0]
params = et['function']['parameters']
print('参数:', list(params.get('properties', {}).keys()))
print('描述前100字:', et['function']['description'][:100])

async def test():
    # shell 命令
    r = await mcp._tool_manager.call_tool('execute_task', {'command': 'echo OK && date'})
    print('\033[92mecho、结果: \033[0m', r)
    r = await mcp._tool_manager.call_tool('execute_task', {'command': 'pwd'})
    print('\033[92m pwd 结果: \033[0m', r)
    r = await mcp._tool_manager.call_tool('execute_task', {'command': 'ls ./'})
    print('\033[92m ls ./ 结果: \033[0m', r)
    r = await mcp._tool_manager.call_tool('execute_task', {'command': 'ls /skills'})
    print('\033[92m ls ../ 结果: \033[0m', r)
    r = await mcp._tool_manager.call_tool('execute_task', {'command': 'cat /skills/INDEX.md'})
    print('\033[92mcat /skills/INDEX.md 结果: \033[0m', r)
    # r = await mcp._tool_manager.call_tool('execute_task', {'command': 'pip install pandas'})
    # print('\033[92mpip install pandas 结果: \033[0m', r)
    r = await mcp._tool_manager.call_tool('execute_task', {'command': 'python /skills/web/web_search/src/search.py "hacker news"'})
    print('\033[92mpython /skills/web/web_search/src/search.py "hacker news" 结果: \033[0m', r)
    r = await mcp._tool_manager.call_tool('execute_task', {'command': 'python /skills/web/web_scrape/src/scrape.py "https://news.ycombinator.com"'})
    print('\033[92mpython /skills/web/web_scrape/src/scrape.py "https://news.ycombinator.com/" 结果: \033[0m', r)
    r = await mcp._tool_manager.call_tool('execute_task', {'command': 'find /workspace -name \\"*frp*\\" -type f 2>/dev/null | head -20'})
    print('\033[92mls -la /workspace/ 结果: \033[0m', r)

asyncio.run(test())
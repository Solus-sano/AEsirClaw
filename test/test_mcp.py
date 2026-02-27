import sys
import os
sys.path.append(os.path.abspath("./"))
from agent_core.tools.mcp_tools import create_mcp_server
from agent_core.pipeline import _list_skills
mcp = create_mcp_server(outputter=None, bot_api=None, executor=None)

for tool in mcp._tool_manager.list_tools():
    print(tool.name, tool.description, tool.parameters)
    
print(_list_skills())
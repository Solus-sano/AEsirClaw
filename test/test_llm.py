import os
import sys
sys.path.append(os.path.abspath("./"))

from agent_core.llm import LLMClient,ChatMessage
import yaml
import asyncio

cfg_file = "config/bot.yaml"

cfg = yaml.load(open(cfg_file, "r"), Loader=yaml.FullLoader) or {}

llm = LLMClient(cfg)

messages = [ChatMessage(role="system", content="You are a helpful assistant."),
            ChatMessage(role="user", content="你是？")]


async def test():
    response = await llm.chat(messages)
    print(response)

if __name__ == "__main__":
    asyncio.run(test())
from ncatbot.plugin_system import NcatBotPlugin
from ncatbot.plugin_system import command_registry
from ncatbot.plugin_system import filter_registry
from ncatbot.core.event import BaseMessageEvent, PrivateMessageEvent

class HelloPlugin(NcatBotPlugin):
    name = "HelloPlugin"
    version = "1.0.0"

    async def on_load(self):
        # 可留空，保持轻量
        pass

    @command_registry.command("hello")
    async def hello_cmd(self, event: BaseMessageEvent):
        await event.reply("你好！我是插件 HelloPlugin。")

    @filter_registry.private_filter
    async def on_private_msg(self, event: PrivateMessageEvent):
        await event.reply("你发送了一条私聊消息！")
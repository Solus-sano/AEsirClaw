"""FastMCP 工具注册。所有 agent 可调用的工具定义在此。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import frontmatter
from mcp.server.fastmcp import FastMCP
from ncatbot.utils import get_log

if TYPE_CHECKING:
    from agent_core.output import MessageOutputter
    from agent_core.memory.short_term import ShortTermMemory
    from agent_core.tools.docker_executor import BaseExecutor
    from ncatbot.core.api import BotAPI
    
from agent_core.memory.short_term import MemoryMessage, _cq_processor

_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

LOG = get_log(__name__)


def create_mcp_server(
    *,
    outputter: MessageOutputter,
    bot_api: BotAPI,
    memory: ShortTermMemory,
    bot_name: str = "Bot",
    executor: BaseExecutor | None = None,
) -> FastMCP:
    """创建并返回已注册所有工具的 FastMCP 实例。

    工具 handler 通过闭包捕获外部依赖（outputter / bot_api / executor），
    避免全局状态。
    """
    mcp = FastMCP("AEsirClaw Agent Tools")

    def _record_bot_msg(context_id: str, content: str) -> None:
        """将 bot 自己发送的消息写入短期记忆。"""
        msg = MemoryMessage()
        msg.time = time.strftime("%Y-%m-%d %H:%M")
        msg.sender_name = bot_name
        msg.content = content
        msg.is_root = False
        memory.append(context_id, msg)

    # ─── 沙箱执行工具 ──────────────────────────────────────

    @mcp.tool()
    async def execute_task(command: str) -> str:
        """在沙箱中执行 shell 命令。支持任意命令：python、ls、wget、curl、ffmpeg、apt install 等。
        /skills/ 目录（只读）包含能力指南和预写脚本，/workspace/ 目录（可读写）是工作区。
        stdout 和 stderr 将作为返回值。

        常见用法:
        - 运行 Python: python -c "print(1+1)"
        - 运行脚本: python /skills/web/web_search/src/search.py "关键词"
        - 下载文件: wget -O /workspace/file.png "https://example.com/image.png"
        - 查看文件: ls /workspace/ 或 cat /skills/INDEX.md
        - 安装依赖: pip install pandas
        """
        if executor is None:
            return json.dumps({"ok": False, "error": "沙箱执行器未初始化"}, ensure_ascii=False)
        return await executor.execute(command)

    # ─── QQ 消息工具 ──────────────────────────────────────

    @mcp.tool()
    async def send_group_msg(group_id: int, messages: list[str]) -> str:
        """向 QQ 群发送消息。messages 为分段文本列表，内部自动模拟打字延迟逐条发送。"""
        await outputter.send_group(str(group_id), messages)
        ctx = f"group:{group_id}"
        for seg in messages:
            _record_bot_msg(ctx, seg)
        return f"已发送 {len(messages)} 条消息到群 {group_id}"

    @mcp.tool()
    async def send_private_msg(user_id: int, messages: list[str]) -> str:
        """向 QQ 用户发送私聊消息。messages 为分段文本列表，内部自动模拟打字延迟。"""
        await outputter.send_private(str(user_id), messages)
        ctx = f"private:{user_id}"
        for seg in messages:
            _record_bot_msg(ctx, seg)
        return f"已发送 {len(messages)} 条私聊消息给 {user_id}"

    @mcp.tool()
    async def send_group_media(
        group_id: int,
        media_type: str,
        path_or_url: str,
        text: str = None,
    ) -> str:
        """向 QQ 群发送媒体内容，如果要发送的内容包含不是文本的其他模态内容，则必须使用此工具发送。
        media_type: "image" | "file" | "video" 
        path_or_url: 图片URL 或 /workspace/ 下的本地文件路径
        text: 附带的文字说明（可选）
        """
        # 判断是否是url
        if not path_or_url.startswith("http"):
            path_or_url = os.path.join(os.path.abspath(_PROJECT_DIR), f".{path_or_url}")
        ctx = f"group:{group_id}"
        label = {"image": "[图片]", "file": "[文件]", "video": "[视频]"}.get(media_type, "")
        if media_type == "image":
            if text:
                await bot_api.post_group_msg(group_id=str(group_id), text=text, image=path_or_url)
            else:
                await bot_api.post_group_msg(group_id=str(group_id), image=path_or_url)
        elif media_type == "file":
            await bot_api.post_group_file(group_id=str(group_id), file=path_or_url)
        elif media_type == "video":
            await bot_api.post_group_msg(group_id=str(group_id), video=path_or_url)
        else:
            return f"不支持的媒体类型: {media_type}"

        content = f"{text} {label}" if text else label
        _record_bot_msg(ctx, content)
        return f"已发送{label}到群 {group_id}"
    
    @mcp.tool()
    async def send_private_media(
        user_id: int,
        media_type: str,
        path_or_url: str,
        text: str = None,
    ) -> str:
        """向 QQ 用户发送媒体内容，如果要发送的内容包含不是文本的其他模态内容，则必须使用此工具发送。
        注意先确认文件格式，必要时加上后缀
        media_type: "image" | "file" | "video"
        path_or_url: 图片URL 或 /workspace/ 下的本地文件路径
        text: 附带的文字说明（可选）
        """
        if not path_or_url.startswith("http"):
            path_or_url = os.path.join(os.path.abspath(_PROJECT_DIR), f".{path_or_url}")
        ctx = f"private:{user_id}"
        label = {"image": "[图片]", "file": "[文件]", "video": "[视频]"}.get(media_type, "")
        if media_type == "image":
            if text:
                await bot_api.post_private_msg(user_id=str(user_id), text=text, image=path_or_url)
            else:
                await bot_api.post_private_msg(user_id=str(user_id), image=path_or_url)
        elif media_type == "file":
            await bot_api.post_private_file(user_id=str(user_id), file=path_or_url)
        elif media_type == "video":
            await bot_api.post_private_msg(user_id=str(user_id), video=path_or_url)
        else:
            return f"不支持的媒体类型: {media_type}"

        content = f"{text} {label}" if text else label
        _record_bot_msg(ctx, content)
        return f"已发送{label}到用户 {user_id}"

    @mcp.tool()
    async def get_group_msg_history(group_id: int, count: int = 20) -> str:
        """
        如果需要获取更多的群聊历史，可以调用此工具获取群聊历史消息记录。
        group_id: 群聊 ID
        count: 获取的消息数量，默认 20（即最近20条消息）
        返回 JSON 格式的消息列表。
        """
        history = await bot_api.get_group_msg_history(
            group_id=str(group_id), count=count
        )
        messages = []
        for ev in history:
            messages.append({
                "sender": getattr(ev, "sender", {}).get("nickname", "unknown") if isinstance(getattr(ev, "sender", None), dict) else getattr(getattr(ev, "sender", None), "nickname", "unknown"),
                "content": _cq_processor.process(getattr(ev, "raw_message", "")),
                "time": getattr(ev, "time", 0),
            })
        return json.dumps(messages, ensure_ascii=False)
    
    @mcp.tool()
    async def send_forward_history(group_id: int, user_id: int, history: str) -> str:
        """
        向 QQ 群发送总结的历史信息，用转发格式。
        group_id: 群号
        user_id: 发送者 QQ 号（你的qq号，用于显示头像）
        history: 总结的历史信息文本，多段内容用双换行分隔，每段会作为转发消息中的一条
        """
        segments = [s.strip() for s in history.split("\n\n") if s.strip()]
        if not segments:
            segments = [history]

        messages = [
            {
                "type": "node",
                "data": {
                    "name": "聊天记录总结",
                    "uin": str(user_id),
                    "content": [{"type": "text", "data": {"text": seg}}],
                },
            }
            for seg in segments
        ]

        news = [s[:20] for s in segments[:4]]

        await bot_api.send_group_forward_msg(
            group_id=str(group_id),
            messages=messages,
            news=news,
            prompt="[聊天记录]",
            summary=f"查看{len(segments)}条转发消息",
            source="群聊的聊天记录",
        )
        return f"已发送转发消息到群 {group_id}"
    
    @mcp.tool()
    async def get_private_msg_history(user_id: int, count: int = 20) -> str:
        """
        如果需要获取更多的私聊历史，可以调用此工具获取私聊历史消息记录。
        user_id: 用户 ID
        count: 获取的消息数量，默认 20（即最近20条消息）
        返回 JSON 格式的消息列表。
        """
        history = await bot_api.get_friend_msg_history(user_id=str(user_id), count=count)
        messages = []
        for ev in history:
            messages.append({
                "sender": getattr(ev, "sender", {}).get("nickname", "unknown") if isinstance(getattr(ev, "sender", None), dict) else getattr(getattr(ev, "sender", None), "nickname", "unknown"),
                "content": _cq_processor.process(getattr(ev, "raw_message", "")),
                "time": getattr(ev, "time", 0),
            })
        return json.dumps(messages, ensure_ascii=False)

    # ─── 技能查询工具 ──────────────────────────────────────

    # @mcp.tool()
    # async def list_skills() -> str:
    #     """列出所有可用技能，返回 JSON 格式的技能摘要列表（name + description）。"""
    #     skills = []
    #     for skill_file in sorted(_SKILLS_DIR.glob("*/SKILL.md")):
    #         post = frontmatter.load(skill_file)
    #         skills.append({
    #             "name": post.metadata.get("name", skill_file.parent.name),
    #             "description": post.metadata.get("description", ""),
    #         })
    #     return json.dumps(skills, ensure_ascii=False)

    @mcp.tool()
    async def get_skill(name: str) -> str:
        """
        当需要使用某个技能时，先调用此工具获取技能文档来学习用法。
        name: 技能名称
        """
        for skill_file in _SKILLS_DIR.glob("*/SKILL.md"):
            post = frontmatter.load(skill_file)
            if post.metadata.get("name") == name:    
                return post.content
        return json.dumps({"ok": False, "error": f"未找到名为 '{name}' 的技能"}, ensure_ascii=False)



    return mcp

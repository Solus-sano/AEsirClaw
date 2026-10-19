"""FastMCP 工具注册。所有 agent 可调用的工具定义在此。"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import frontmatter
from mcp.server.fastmcp import FastMCP
from ncatbot.utils import get_log

if TYPE_CHECKING:
    from agent_core.output import MessageOutputter
    from agent_core.memory.short_term import ShortTermMemory
    from agent_core.scheduler import TaskScheduler
    from agent_core.subagent import SubAgentRunner
    from agent_core.tools.docker_executor import BaseExecutor
    from ncatbot.core.api import BotAPI
    
from agent_core.memory.short_term import MemoryMessage, _cq_processor

_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

LOG = get_log(__name__)


def _event_sender_nickname(ev: object) -> str:
    sender = getattr(ev, "sender", None)
    if isinstance(sender, dict):
        return sender.get("nickname", "unknown")
    return getattr(sender, "nickname", "unknown") if sender else "unknown"


def _events_to_messages(history: list) -> list[dict]:
    return [
        {
            "sender": _event_sender_nickname(ev),
            "content": _cq_processor.process(getattr(ev, "raw_message", "")),
            "time": getattr(ev, "time", 0),
        }
        for ev in history
    ]


def create_mcp_server(
    *,
    outputter: MessageOutputter,
    bot_api: BotAPI,
    memory: ShortTermMemory,
    bot_name: str = "Bot",
    bot_qq: str | int | None = None,
    executor: BaseExecutor | None = None,
    scheduler: TaskScheduler | None = None,
    context_id: str | None = None,
    subagent_runner: SubAgentRunner | None = None,
) -> FastMCP:
    """创建并返回已注册所有工具的 FastMCP 实例。

    工具 handler 通过闭包捕获外部依赖（outputter / bot_api / executor /
    scheduler / context_id），避免全局状态。

    scheduler 与 context_id 用于定时任务工具：context_id 决定任务绑定到
    哪个会话（在哪个群/私聊设置就在哪触发）。
    """
    mcp = FastMCP("AEsirClaw Agent Tools")

    def _record_bot_msg(context_id: str, content: str) -> None:
        """将 bot 自己发送的消息写入短期记忆。"""
        msg = MemoryMessage()
        msg.time = time.strftime("%Y-%m-%d %H:%M")
        msg.sender_name = bot_name
        msg.content = content
        memory.append(context_id, msg)

    # ─── 沙箱执行工具 ──────────────────────────────────────

    @mcp.tool()
    async def execute_task(command: str) -> str:
        """在沙箱执行 shell 命令，返回 stdout/stderr。/skills 只读，/workspace 可读写。"""
        if executor is None:
            return json.dumps({"ok": False, "error": "沙箱执行器未初始化"}, ensure_ascii=False)
        return await executor.execute(command)

    # ─── QQ 工具：统一使用 group:ID / private:ID 定位会话 ───

    def _resolve_target(target: str | None) -> tuple[str, str, str]:
        resolved = context_id if target is None else target
        kind, separator, identifier = (resolved or "").partition(":")
        if (kind not in {"group", "private"} or not separator
                or not identifier.isascii() or not identifier.isdecimal()
                or int(identifier) <= 0):
            raise ValueError("target 须为 group:正整数 或 private:正整数；省略时须有当前会话")
        identifier = str(int(identifier))
        return kind, identifier, f"{kind}:{identifier}"

    def _error(message: str) -> str:
        return json.dumps({"ok": False, "error": message}, ensure_ascii=False)

    @mcp.tool()
    async def send_message(
        messages: list[str], target: str | None = None, forward: bool = False,
    ) -> str:
        """发送文本。target 为 group:ID / private:ID，省略用当前会话。
        messages 各项逐条发送到qq中；forward=true 时将messages中的多条消息合为一个转发消息块发送到qq中。
        """
        try:
            kind, identifier, ctx = _resolve_target(target)
        except ValueError as exc:
            return _error(str(exc))
        segments = [message for message in messages if message.strip()]
        if not segments:
            return _error("消息内容不能为空")
        if forward:
            if bot_qq is None:
                return _error("合并转发所需的机器人 QQ 号未初始化")
            nodes = [
                {"type": "node", "data": {
                    "name": bot_name, "uin": str(bot_qq),
                    "content": [{"type": "text", "data": {"text": segment}}],
                }}
                for segment in segments
            ]
            send = getattr(bot_api, f"send_{kind}_forward_msg")
            await send(
                **{("group_id" if kind == "group" else "user_id"): identifier},
                messages=nodes, news=[s[:20] for s in segments[:4]],
                prompt="[合并转发]", summary=f"查看{len(segments)}条转发消息",
                source="机器人发送的合并转发消息",
            )
            _record_bot_msg(ctx, "[合并转发]\n" + "\n".join(segments))
        else:
            await getattr(outputter, f"send_{kind}")(identifier, segments)
            for segment in segments:
                _record_bot_msg(ctx, segment)
        return f"已发送 {len(segments)} 条{'合并转发' if forward else ''}消息到 {ctx}"

    @mcp.tool()
    async def send_media(
        kind: Literal["image", "file", "video", "record"],
        source: str, target: str | None = None, caption: str | None = None,
    ) -> str:
        """发送媒体。source 为 /workspace/ 文件路径或 URL（语音仅本地文件）。
        target 为 group:ID / private:ID，省略用当前会话；caption 为附言。
        """
        try:
            chat_kind, identifier, ctx = _resolve_target(target)
        except ValueError as exc:
            return _error(str(exc))
        is_url = source.startswith(("https://", "http://"))
        if kind == "record" and is_url:
            return _error("语音仅支持 /workspace/ 下的本地文件")
        if not is_url:
            workspace = (_PROJECT_DIR / "workspace").resolve()
            path = (_PROJECT_DIR / source.lstrip("/")).resolve()
            if not source.startswith("/workspace/") or not path.is_relative_to(workspace):
                return _error("本地文件须位于 /workspace/ 下")
            if not path.is_file():
                return _error(f"{'语音' if kind == 'record' else '媒体'}文件不存在: {source}")
            source = str(path)
        recipient = {("group_id" if chat_kind == "group" else "user_id"): identifier}
        label = {"image": "[图片]", "file": "[文件]", "video": "[视频]", "record": "[语音]"}[kind]
        if kind == "record":
            encoded = base64.b64encode(Path(source).read_bytes()).decode("utf-8")
            await getattr(bot_api, f"send_{chat_kind}_record")(**recipient, file=f"base64://{encoded}")
        elif kind == "file":
            await getattr(bot_api, f"post_{chat_kind}_file")(**recipient, file=source)
        else:
            kwargs = {kind: source}
            if caption:
                kwargs["text"] = caption
            await getattr(bot_api, f"post_{chat_kind}_msg")(**recipient, **kwargs)
        _record_bot_msg(ctx, f"{caption} {label}" if caption and kind in {"image", "video"} else label)
        # 文件和语音不能内嵌附言，成功发送后再发文本。
        if caption and kind in {"file", "record"}:
            await getattr(outputter, f"send_{chat_kind}")(identifier, [caption])
            _record_bot_msg(ctx, caption)
        return f"已发送{label}到 {ctx}"

    @mcp.tool()
    async def get_history(
        target: str | None = None, limit: int = 20, offset: int = 0,
    ) -> str:
        """读取历史，按旧到新返回。跳过最近 offset 条，再取最多 limit 条。
        target 为 group:ID / private:ID，省略用当前会话。
        """
        try:
            kind, identifier, _ = _resolve_target(target)
        except ValueError as exc:
            return _error(str(exc))
        if limit < 1 or offset < 0:
            return _error("limit 须大于 0，offset 须不小于 0")
        if kind == "group":
            history = await bot_api.get_group_msg_history(group_id=identifier, count=offset + limit)
        else:
            history = await bot_api.get_friend_msg_history(user_id=identifier, message_seq=0, count=offset + limit)
        end = max(0, len(history) - offset)
        return json.dumps(_events_to_messages(history[max(0, end - limit):end]), ensure_ascii=False)

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

    # ─── 定时任务工具 ──────────────────────────────────────

    @mcp.tool()
    async def add_scheduled_task(
        type: str,
        prompt: str,
        interval_seconds: float = None,
        time: str = None,
        delay_seconds: float = None,
    ) -> str:
        """为当前会话设置一个定时任务。到点后系统会自动唤醒你来完成它。

        type: 任务类型，必须是以下之一：
          - "once":     一次性任务，到点触发一次后自动删除。需配合 delay_seconds 使用。
          - "interval": 每隔固定时间重复触发。需配合 interval_seconds 使用。
          - "daily":    每天固定时刻触发。需配合 time 使用。
        prompt: 触发时给你自己的任务说明，写清到点时你要做什么（如"提醒大家喝水"）。
        interval_seconds: type=interval 时必填，重复间隔（秒）。如每小时填 3600。
        time: type=daily 时必填，每天触发时刻，格式 "HH:MM"（24小时制），如 "09:00"。
        delay_seconds: type=once 时必填，相对现在多少秒后触发。如10分钟后填 600。

        任务自动绑定到当前会话（在哪个群/私聊设置就在哪触发）。
        """
        if scheduler is None or context_id is None:
            return json.dumps({"ok": False, "error": "定时任务调度器未初始化"}, ensure_ascii=False)
        try:
            task = scheduler.add_task(
                context_id=context_id,
                type=type,
                prompt=prompt,
                interval_seconds=interval_seconds,
                time_str=time,
                delay_seconds=delay_seconds,
            )
        except ValueError as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        return json.dumps(
            {"ok": True, "task_id": task.id, "schedule": task.describe(), "prompt": task.prompt},
            ensure_ascii=False,
        )

    @mcp.tool()
    async def list_scheduled_tasks() -> str:
        """列出当前会话已设置的所有定时任务，返回 JSON 列表。"""
        if scheduler is None or context_id is None:
            return json.dumps({"ok": False, "error": "定时任务调度器未初始化"}, ensure_ascii=False)
        tasks = scheduler.list_tasks(context_id=context_id)
        return json.dumps(
            [
                {
                    "task_id": t.id,
                    "type": t.type,
                    "schedule": t.describe(),
                    "prompt": t.prompt,
                    "last_run": t.last_run,
                }
                for t in tasks
            ],
            ensure_ascii=False,
        )

    @mcp.tool()
    async def remove_scheduled_task(task_id: str) -> str:
        """删除当前会话的一个定时任务。

        task_id: 要删除的任务 ID（可先用 list_scheduled_tasks 查询）。
        """
        if scheduler is None or context_id is None:
            return json.dumps({"ok": False, "error": "定时任务调度器未初始化"}, ensure_ascii=False)
        ok = scheduler.remove_task(task_id, context_id=context_id)
        if ok:
            return json.dumps({"ok": True, "task_id": task_id}, ensure_ascii=False)
        return json.dumps(
            {"ok": False, "error": f"未找到属于当前会话的任务 '{task_id}'"},
            ensure_ascii=False,
        )

    # ─── SubAgent 工具 ──────────────────────────────────────

    if subagent_runner is not None:

        @mcp.tool()
        async def dispatch_subagent(task: str) -> str:
            """把一个独立子任务交给 subagent 处理，返回它的报告（你只会看到最终报告，看不到中间过程）。

            适合用于：
            - 超长历史分段总结：分别派发"总结某会话第 X-Y 条消息"，再汇总各份报告。
            - 需要大量读取 / 搜索调研、但你只需要结论的任务。

            subagent 在隔离的上下文中运行，拥有读取历史、沙箱执行、查技能等工具，
            但不能发消息、不能再派发 subagent。可在一轮内派发多个 dispatch_subagent，
            它们会并行执行。

            task: 对子任务的完整描述。务必写清数据指针（如"总结 group:123 的历史，limit=100、offset=100"），
                  以便 subagent 自行拉取数据，避免你把原始数据读进自己的上下文。
            """
            return await subagent_runner.run(task)

    return mcp

"""消息处理管线：触发判断 → 组装上下文 → 调用 Agent Loop。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from pathlib import Path

from agent_core.llm import ChatMessage
from agent_core.utils import inject_multimodal
from ncatbot.utils import get_log
import json
import frontmatter
from pathlib import Path

if TYPE_CHECKING:
    from agent_core.controller import AgentController
    from agent_core.memory.long_term import LongTermMemory
    from agent_core.memory.short_term import ShortTermMemory
    from agent_core.trigger import TriggerManager

LOG = get_log(__name__)

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
def _list_skills() -> str:
    """列出所有可用技能，返回 JSON 格式的技能摘要列表（name + description）。"""
    skills = []
    for skill_file in sorted(_SKILLS_DIR.glob("*/SKILL.md")):
        post = frontmatter.load(skill_file)
        skills.append({
            "name": post.metadata.get("name", skill_file.parent.name),
            "description": post.metadata.get("description", ""),
        })
    return json.dumps(skills, ensure_ascii=False)

def build_system_prompt(persona_str: str, skills_str: str) -> str:
    """根据人格配置生成 System Prompt。"""

    return f"""
## 【人格配置】
{persona_str}

【消息发送规则 — 必须严格遵守】
你不能通过直接输出文字来与用户交流。你的所有消息必须通过调用 send_group_msg 或 send_private_msg 工具发送。
如果你直接在回复中写了内容而没有调用发送工具，用户将看不到任何消息。
当你认为无需回复时，不调用任何发送工具即可（等价于保持沉默）。
调用 send_group_msg / send_private_msg 时，messages 参数是分段文本列表。
尽量只调用一次 send_group_msg / send_private_msg 工具，以免造成信息轰炸。

技能列表：
{skills_str}
"""


class MessagePipeline:
    """消息处理管线：触发判断 → 组装上下文 → 调用 Agent Loop。"""

    def __init__(
        self,
        controller: AgentController,
        memory: ShortTermMemory,
        trigger: TriggerManager,
        long_term_memory: LongTermMemory,
        persona: dict,
        *,
        context_short_term_messages: int = 100,
        extraction_threshold: int = 200,
    ):
        self.controller = controller
        self.memory = memory
        self.trigger = trigger
        self.long_term_memory = long_term_memory
        self.extraction_threshold = extraction_threshold
        self.context_short_term_messages = context_short_term_messages
        self.persona_str = json.dumps(persona, ensure_ascii=False)
        self.system_prompt = build_system_prompt(self.persona_str, _list_skills())

    async def handle(self, context_id: str, *, is_at_me: bool = False, message: str = "") -> None:
        """处理一次消息触发。

        Args:
            context_id: 上下文 ID（group:xxx 或 private:xxx）
            is_at_me: 是否 @ 了机器人
            message: 当前消息文本，用于触发判断
        """
        # 1. 检查长期记忆提取
        # if self.memory.should_extract(context_id, self.extraction_threshold):
        #     LOG.info("\033[92m提取长期记忆 [%s]\033[0m", context_id)
        #     self.long_term_memory.trigger_extract_async(context_id, self.memory)

        # 2. 触发判断
        trigger_result = self.trigger.check(
            context_id=context_id,
            message=message,
            is_at_me=is_at_me,
        )

        if not trigger_result.should_respond:
            LOG.debug("跳过响应 [%s]: %s", context_id, trigger_result.reason)
            return

        LOG.info("触发响应 [%s]: %s", context_id, trigger_result.reason)

        # 3. 组装 messages
        messages = self._build_context(context_id)
        messages = await inject_multimodal(messages)

        # 4. 调用 Agent Loop（消息发送在 loop 内通过工具完成）
        await self.controller.run(messages)

        # 5. 记录冷却时间
        self.trigger.record_response(context_id)

    def _build_context(self, context_id: str) -> list[ChatMessage]:
        """拼装 system prompt + 长期记忆 + 短期记忆。"""
        messages: list[ChatMessage] = []

        # System prompt
        messages.append(ChatMessage(role="system", content=self.system_prompt))

        # 注入上下文信息（group_id / user_id）
        context_info = self._build_context_info(context_id)
        if context_info:
            messages.append(ChatMessage(role="system", content=context_info))

        # 长期记忆
        # long_term_str = self.long_term_memory.get_summary_str(context_id)
        # if long_term_str:
        #     messages.append(ChatMessage(role="user", content=long_term_str))

        # 短期记忆
        mem_str = self.memory.get_recent_str(context_id, self.context_short_term_messages)
        messages.append(ChatMessage(
            role="user",
            content=f"以下为群聊或私聊屏幕上的聊天记录：\n{mem_str}",
        ))

        print(f"\033[92m[DEBUG] messages: {messages}\033[0m")
        return messages

    @staticmethod
    def _build_context_info(context_id: str) -> str:
        """根据 context_id 生成上下文信息，注入到 prompt。"""
        if context_id.startswith("group:"):
            group_id = context_id.removeprefix("group:")
            return f"【当前上下文】群聊，group_id={group_id}。"
        elif context_id.startswith("private:"):
            user_id = context_id.removeprefix("private:")
            return f"【当前上下文】私聊，user_id={user_id}。"
        return ""

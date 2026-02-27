from __future__ import annotations

import json
import re
import time
from pydantic import BaseModel
from typing import List, Optional

from agent_core.llm import LLMClient, ChatMessage
from ncatbot.utils import get_log

LOG = get_log(__name__)


class SummaryTopic(BaseModel):
    topic: str
    contributors: List[str]
    times: str
    detail: str


class SummaryData(BaseModel):
    """长期记忆的存储结构"""
    topics: List[SummaryTopic] = []
    last_update: str = ""


PROMPT = """
你是一个经验丰富的聊天记录总结专家，擅长从聊天记录中提取出有价值的信息。

你会获得一个群的最近聊天记录，以及先前的总结。

请根据聊天记录和先前的总结，将更新后的总结返回。

输出总结的格式为：

{
    "topics": [
        {
            "topic": "话题",
            "contributors": ["贡献者1", "贡献者2"],
            "times": "时间范围",
            "detail": "话题详情"
        }
    ]
}

生成总结内容时，你需要严格遵守以下准则：
- 每个话题的内容应该比较清晰，不能太笼统
- 明确主体与动作：在每条总结中点名具体群友（或群体）在何时做了什么，以及对话题产生的具体影响；避免与群友行为无关的泛化表述。
- 交代脉络与结论：尽量给出起因→经过→结果，并在首句给出清晰结论。
- 标注信息来源：引用观点或数据时，尽量指明发言人、时间或关键信息片段，提升可追溯性。

总结的流程：
- 分析先前的总结和聊天记录，将其重新聚合为 1~8 个主题。
- 直接输出 JSON 格式，包含 topics 数组，每项包含：
    - topic: 主题的一句话结论
    - times: 时间范围
    - contributors: 相关群友名称或 qq 号列表（去重）
    - detail: 100 字以内的简洁清晰描述，包含关键因果链与必要引用（可写"发言人@时间：内容片段"）
"""


class MemoryExtractor:
    """从短期记忆中提取长期记忆摘要"""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def extract(
        self, 
        recent_messages: str, 
        old_summary: Optional[SummaryData] = None
    ) -> SummaryData:
        """
        调用 LLM 提取摘要
        
        Args:
            recent_messages: 短期记忆的字符串形式
            old_summary: 先前的摘要（可为 None）
            
        Returns:
            新的 SummaryData
        """
        # 构建用户消息
        old_summary_str = "无" if old_summary is None else old_summary.model_dump_json(indent=2)
        
        user_content = f"""先前的总结：
{old_summary_str}

最近的聊天记录：
{recent_messages}

请输出更新后的总结（JSON 格式）："""

        messages = [
            ChatMessage(role="system", content=PROMPT),
            ChatMessage(role="user", content=user_content),
        ]

        response = await self.llm.chat(messages)
        
        # 解析 JSON
        return self._parse_response(response.content)

    def _parse_response(self, content: str) -> SummaryData:
        """解析 LLM 返回的 JSON"""
        try:
            # 尝试提取 JSON 块（处理可能的 markdown 包裹）
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', content)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = content.strip()
            
            data = json.loads(json_str)
            
            return SummaryData(
                topics=[SummaryTopic(**t) for t in data.get("topics", [])],
                last_update=time.strftime("%Y-%m-%d %H:%M:%S")
            )
        except Exception as e:
            LOG.error(f"解析 LLM 响应失败: {e}, content: {content[:200]}")
            # 返回空摘要而不是抛异常
            return SummaryData()

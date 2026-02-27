"""拟人化分段消息发送模块。"""

from __future__ import annotations

import asyncio
import random
from typing import List, Protocol


class MessageAPI(Protocol):
    """消息发送 API 接口协议"""
    async def post_group_msg(self, group_id: str, text: str) -> None: ...
    async def post_private_msg(self, user_id: str, text: str) -> None: ...


class MessageOutputter:
    """拟人化分段消息发送器。
    
    模拟真人打字行为，根据消息长度计算延迟，
    在多段消息之间加入随机延时。
    """

    def __init__(
        self,
        api: MessageAPI,
        typing_delay_per_char: float = 0.05,
        random_delay_range: tuple[float, float] = (0.5, 2.0),
        max_delay: float = 5.0,
    ):
        """初始化消息发送器。
        
        Args:
            api: 消息发送 API 实例
            typing_delay_per_char: 每字符延迟（秒）
            random_delay_range: 随机波动范围（秒）
            max_delay: 最大延迟上限（秒）
        """
        self.api = api
        self.typing_delay_per_char = typing_delay_per_char
        self.random_delay_range = random_delay_range
        self.max_delay = max_delay

    def calculate_delay(self, text: str) -> float:
        """计算拟人化延迟：基于字数 + 随机波动。
        
        Args:
            text: 待发送的文本
            
        Returns:
            计算出的延迟秒数
        """
        base = len(text) * self.typing_delay_per_char
        random_factor = random.uniform(*self.random_delay_range)
        return min(base + random_factor, self.max_delay)

    async def send_group(self, group_id: str, segments: List[str]) -> None:
        """分段发送群消息。
        
        Args:
            group_id: 群号
            segments: 消息段列表
        """
        for i, seg in enumerate(segments):
            if i > 0:
                delay = self.calculate_delay(seg)
                await asyncio.sleep(delay)
            await self.api.post_group_msg(group_id=group_id, text=seg)

    async def send_private(self, user_id: str, segments: List[str]) -> None:
        """分段发送私聊消息。
        
        Args:
            user_id: 用户 QQ 号
            segments: 消息段列表
        """
        for i, seg in enumerate(segments):
            if i > 0:
                delay = self.calculate_delay(seg)
                await asyncio.sleep(delay)
            await self.api.post_private_msg(user_id=user_id, text=seg)


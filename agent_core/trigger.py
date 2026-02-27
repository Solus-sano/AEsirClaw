"""触发器模块：决定是否需要调用 LLM 响应消息。

冷却时间按 context_id（group:xxx 或 private:xxx）独立计算。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from ncatbot.utils import get_log

LOG = get_log(__name__)


class TriggerType(Enum):
    """触发类型枚举"""
    FORCED = "forced"       # 强制触发：@、私聊
    KEYWORD = "keyword"     # 关键词触发
    SMART = "smart"         # 智能触发（预留）
    NONE = "none"           # 不触发


@dataclass
class TriggerResult:
    """触发检查结果"""
    should_respond: bool
    trigger_type: TriggerType
    reason: str = ""


class TriggerManager:
    """触发器管理器：决定是否需要调用 LLM
    
    冷却时间按 context_id（group:xxx 或 private:xxx）独立计算
    """
    
    def __init__(
        self,
        *,
        bot_qq: str = "",
        keywords: list[str] | None = None,
        group_cooldown_seconds: float = 30.0,
        private_cooldown_seconds: float = 1.0,
    ):
        """初始化触发器管理器
        
        Args:
            bot_qq: 机器人的 QQ 号
            keywords: 关键词列表，命中时触发响应
            group_cooldown_seconds: 群聊冷却时间（秒）
            private_cooldown_seconds: 私聊冷却时间（秒）
        """
        self.bot_qq = bot_qq
        self.keywords = [kw.lower() for kw in (keywords or [])]
        self.group_cooldown_seconds = group_cooldown_seconds
        self.private_cooldown_seconds = private_cooldown_seconds
        
        # 冷却时间追踪：context_id -> last_response_time
        self._cooldowns: dict[str, float] = {}
    
    def check(
        self,
        context_id: str,
        message: str,
        *,
        is_at_me: bool = False,
    ) -> TriggerResult:
        """检查是否应该触发响应
        
        Args:
            context_id: 上下文ID，格式为 "group:xxx" 或 "private:xxx"
            message: 消息内容
            is_at_me: 是否 @ 了机器人
            
        Returns:
            TriggerResult: 触发检查结果
        """
        is_private = context_id.startswith("private:")
        
        # 1. 强制触发：私聊
        if is_private:
            return TriggerResult(True, TriggerType.FORCED, "私聊消息")
        
        # 2. 强制触发：@ 机器人
        if is_at_me:
            return TriggerResult(True, TriggerType.FORCED, "被@提及")
        
        # 3. 关键词触发
        message_lower = message.lower()
        for kw in self.keywords:
            if kw in message_lower:
                return TriggerResult(True, TriggerType.KEYWORD, f"命中关键词: {kw}")
        
        # 4. 冷却检查（用于未来的智能触发）
        if self._is_in_cooldown(context_id):
            remaining = self.get_cooldown_remaining(context_id)
            return TriggerResult(False, TriggerType.NONE, f"冷却中 (剩余 {remaining:.1f}s)")
        else:
            self.record_response(context_id)
            return TriggerResult(True, TriggerType.NONE, "冷却结束")
        
        # 5. TODO: 智能触发（Phase 3，需轻量模型判断）
        
        return TriggerResult(False, TriggerType.NONE, "无触发条件")
    
    def record_response(self, context_id: str):
        """记录响应时间，用于冷却计算
        
        Args:
            context_id: 上下文ID
        """
        self._cooldowns[context_id] = time.time()
        LOG.debug("记录响应时间 [%s]", context_id)
    
    def _get_cooldown_seconds(self, context_id: str) -> float:
        """根据上下文类型获取对应的冷却时间"""
        if context_id.startswith("private:"):
            return self.private_cooldown_seconds
        return self.group_cooldown_seconds
    
    def _is_in_cooldown(self, context_id: str) -> bool:
        """检查指定上下文是否在冷却中
        
        Args:
            context_id: 上下文ID
            
        Returns:
            bool: 是否在冷却中
        """
        last_time = self._cooldowns.get(context_id, 0)
        cooldown = self._get_cooldown_seconds(context_id)
        return (time.time() - last_time) < cooldown
    
    def get_cooldown_remaining(self, context_id: str) -> float:
        """获取剩余冷却时间（秒），用于调试
        
        Args:
            context_id: 上下文ID
            
        Returns:
            float: 剩余冷却时间（秒），最小为 0
        """
        last_time = self._cooldowns.get(context_id, 0)
        cooldown = self._get_cooldown_seconds(context_id)
        remaining = cooldown - (time.time() - last_time)
        return max(0, remaining)


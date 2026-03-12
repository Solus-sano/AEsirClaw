from __future__ import annotations

from collections import deque
from typing import Callable, Dict, Deque, List

import re
import time

from ncatbot.core.event import (
    BaseMessageEvent
)
from ncatbot.utils import get_log
from ncatbot.core.api import BotAPI

LOG = get_log(__name__)

# ---------------------------------------------------------------------------
# CQ 码处理器
# ---------------------------------------------------------------------------

CQHandler = Callable[[Dict[str, str]], str]
"""CQ handler 签名：接收解析后的参数字典，返回替换文本。"""


class CQProcessor:
    """CQ 码处理器 — 将 raw CQ 码转换为 LLM 友好的格式。

    支持按类型注册自定义处理函数，方便后续扩展
    （如图片带 URL、reply 带消息 ID 等）。

    优先级：注册的 handler > 默认标签 > fallback ``[类型名]``。
    """

    DEFAULT_LABELS: Dict[str, str] = {
        "image":   "[图片]",
        "file":    "[文件]",
        "face":    "[表情]",
        "mface":   "[表情包]",
        "record":  "[语音]",
        "video":   "[视频]",
        "reply":   "",
        "forward": "[转发消息]",
        "json":    "[卡片消息]",
        "xml":     "[卡片消息]",
        "share":   "[链接分享]",
        "music":   "[音乐分享]",
        "poke":    "[戳一戳]",
    }

    _CQ_PATTERN = re.compile(r'\[CQ:(\w+)([^\]]*)\]')

    def __init__(self) -> None:
        self._handlers: Dict[str, CQHandler] = {}

    def register(self, cq_type: str, handler: CQHandler) -> None:
        """注册 / 覆盖某个 CQ 类型的处理函数。

        handler 签名: ``(params: Dict[str, str]) -> str``
        params 示例: ``{"file": "abc.jpg", "url": "https://..."}``
        """
        self._handlers[cq_type] = handler

    def process(self, text: str) -> str:
        """处理文本中所有 CQ 码，返回清洗后的文本。"""
        return self._CQ_PATTERN.sub(self._replace, text)

    # -- internal ----------------------------------------------------------

    def _replace(self, match: re.Match) -> str:
        cq_type = match.group(1)
        raw_params = match.group(2)  # ",key=val,key2=val2" 或 ""

        # 优先级 1：注册的自定义 handler（能拿到完整参数）
        if cq_type in self._handlers:
            params = self._parse_params(raw_params)
            return self._handlers[cq_type](params)

        # 优先级 2：默认标签（不需要参数）
        if cq_type in self.DEFAULT_LABELS:
            return self.DEFAULT_LABELS[cq_type]

        # 优先级 3：未知类型 fallback
        return f"[{cq_type}]"

    @staticmethod
    def _parse_params(raw: str) -> Dict[str, str]:
        """解析 CQ 参数字符串。

        输入: ``",file=abc.jpg,url=https://xx.com/img?s=1"``
        输出: ``{"file": "abc.jpg", "url": "https://xx.com/img?s=1"}``

        CQ 码规范中特殊字符会被转义，此处一并做反转义。
        """
        params: Dict[str, str] = {}
        if not raw:
            return params
        for part in raw.lstrip(",").split(","):
            key, sep, value = part.partition("=")
            if sep:
                # 反转义 CQ 码规范中的特殊字符
                value = (
                    value
                    .replace("&#44;", ",")
                    .replace("&#91;", "[")
                    .replace("&#93;", "]")
                    .replace("&amp;", "&")
                )
                params[key] = value
        return params


# 模块级默认处理器实例
_cq_processor = CQProcessor()


def _handle_image(params: Dict[str, str]) -> str:
    """保留图片 URL，供后续多模态处理使用。"""
    url = params.get("url", "")
    if url:
        return f"[IMG:{url}]"
    return "[图片]"

def _handle_file(params: Dict[str, str]) -> str:
    """保留文件 URL，供后续多模态处理使用。"""
    url = params.get("url", "")
    if url:
        return f"[FILE:{url}]"
    return "[文件]"

_cq_processor.register("image", _handle_image)
_cq_processor.register("file", _handle_file)


# ---------------------------------------------------------------------------
# MemoryMessage
# ---------------------------------------------------------------------------

class MemoryMessage:
    time: str = ""   # yyyy-mm-dd HH:MM
    sender_name: str = ""
    content: str = ""
    is_root: bool = False
    
    def to_str(self, userid_nickname_map: Dict[str, str], *, is_group: bool = False) -> str:
        sender_name = f"[ROOT]{self.sender_name}" if is_group and self.is_root else self.sender_name
        raw_str = f"[t={self.time}] [ID={sender_name}] [msg={self.content}]"
        # 第一步：at → @昵称（需要昵称映射，优先处理）
        for userid, nickname in userid_nickname_map.items():
            raw_str = raw_str.replace(f"[CQ:at,qq={userid}]", f"@{nickname}")
        # 第二步：清洗剩余 CQ 码
        raw_str = _cq_processor.process(raw_str)
        return raw_str

    @classmethod
    def from_event(cls, event: BaseMessageEvent, root_qq: str = "") -> "MemoryMessage":
        message = cls()
        message.time = time.strftime("%Y-%m-%d %H:%M", time.localtime(event.time))
        message.sender_name = event.sender.nickname
        message.content = event.raw_message
        user_id = str(getattr(event, "user_id", "")).strip()
        message.is_root = bool(root_qq) and user_id == root_qq
        return message

class ShortTermMemory:
    """基于 deque 的短期记忆，按 context_id 维护会话。"""

    def __init__(self, api: BotAPI, max_size: int = 200, root_qq: str = ""):
        self.api = api
        self.queues: Dict[str, Deque[MemoryMessage]] = {}
        self.max_size = max_size
        self.counters: Dict[str, int] = {}
        self.group_userid_nickname_map: Dict[str, Dict[str, str]] = {}
        self.root_qq = str(root_qq).strip()
        
    async def get_userid_nickname_map(self, group_id: str) -> Dict[str, str]:
        """获取用户 ID 和昵称映射"""
        GroupMemResponse = await self.api.get_group_member_list(group_id=group_id)
        for item in GroupMemResponse.members:
            if group_id not in self.group_userid_nickname_map:
                self.group_userid_nickname_map[group_id] = {}
            nickname = item.nickname if item.card == "" else item.card
            self.group_userid_nickname_map[group_id][str(item.user_id)] = nickname #! 使用card而不是nickname
        

    def append(self, context_id: str, message: MemoryMessage) -> None:
        """写入一条记忆并增加计数。"""
        if context_id not in self.queues:
            self.queues[context_id] = deque(maxlen=self.max_size)
        self.queues[context_id].append(message)
        self.counters[context_id] = self.counters.get(context_id, 0) + 1

    def append_from_event(self, context_id: str, event: BaseMessageEvent) -> None:
        memory_message = MemoryMessage.from_event(event, root_qq=self.root_qq)
        self.append(context_id, memory_message)
    
    def get_recent(self, context_id: str, n: int | None = None) -> List[MemoryMessage]:
        """读取最近 n 条记忆。"""
        if n is None:
            return list(self.queues.get(context_id, []))
        return list(self.queues.get(context_id, []))[-n:]
    
    def get_recent_str(self, context_id: str, n: int | None = None) -> str:
        # import ipdb; ipdb.set_trace()
        if context_id.startswith("group:"):
            group_id = context_id.replace("group:", "")
            nickname_map = self.group_userid_nickname_map.get(group_id, {})
            return "\n".join([message.to_str(nickname_map, is_group=True) for message in self.get_recent(context_id, n=n)])
        else:
            return "\n".join([message.to_str({}, is_group=False) for message in self.get_recent(context_id, n=n)])

    def should_extract(self, context_id: str, threshold: int = 200) -> bool:
        """达到阈值后可触发摘要提取。"""
        # import ipdb; ipdb.set_trace()
        return self.counters.get(context_id, 0) >= threshold

    def reset_counter(self, context_id: str) -> None:
        """提取后重置计数。"""
        self.counters[context_id] = 0

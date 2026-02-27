from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Dict, Optional, Set

from agent_core.llm import LLMClient
from agent_core.memory.extractor import MemoryExtractor, SummaryData
from agent_core.memory.short_term import ShortTermMemory
from ncatbot.utils import get_log

LOG = get_log(__name__)


class LongTermMemory:
    """长期记忆管理，基于文件存储"""

    def __init__(
        self, 
        llm: LLMClient, 
        storage_dir: Path | str = "memory/summaries"
    ):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.extractor = MemoryExtractor(llm)
        
        # 内存缓存，避免频繁读文件
        self._cache: Dict[str, SummaryData] = {}
        
        # 记录正在提取的 context，防止重复触发
        self._extracting: Set[str] = set()

    def _get_file_path(self, context_id: str) -> Path:
        """获取存储文件路径，context_id 格式: group:123 或 private:456"""
        safe_name = context_id.replace(":", "_")
        return self.storage_dir / f"{safe_name}.json"

    def get_summary(self, context_id: str) -> Optional[SummaryData]:
        """获取长期记忆摘要"""
        # 优先从缓存读取
        if context_id in self._cache:
            return self._cache[context_id]
        
        # 从文件读取
        file_path = self._get_file_path(context_id)
        if file_path.exists():
            try:
                with file_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                summary = SummaryData(**data)
                self._cache[context_id] = summary
                return summary
            except Exception as e:
                LOG.error(f"读取长期记忆失败 [{context_id}]: {e}")
        
        return None

    def get_summary_str(self, context_id: str) -> str:
        """获取长期记忆的字符串形式，用于注入 LLM context"""
        summary = self.get_summary(context_id)
        if summary is None or not summary.topics:
            return ""
        
        lines = ["<群聊历史话题摘要>"]
        for i, topic in enumerate(summary.topics, 1):
            contributors_str = "、".join(topic.contributors)
            lines.append(f"{i}. {topic.topic}")
            lines.append(f"   时间: {topic.times}")
            lines.append(f"   参与者: {contributors_str}")
            lines.append(f"   详情: {topic.detail}")
            lines.append("")
        lines.append("</群聊历史话题摘要>")
        
        return "\n".join(lines)

    def _save_summary(self, context_id: str, summary: SummaryData) -> None:
        """保存摘要到文件"""
        file_path = self._get_file_path(context_id)
        os.makedirs(file_path.parent, exist_ok=True)
        with file_path.open("w", encoding="utf-8") as f:
            json.dump(summary.model_dump(), f, ensure_ascii=False, indent=2)
        self._cache[context_id] = summary
        LOG.info(f"长期记忆已保存 [{context_id}]: {len(summary.topics)} 个话题")

    async def extract_and_save(
        self, 
        context_id: str, 
        short_term: ShortTermMemory
    ) -> None:
        """从短期记忆提取并保存到长期记忆（异步执行）"""
        # 防止重复触发
        if context_id in self._extracting:
            LOG.debug(f"正在提取中，跳过 [{context_id}]")
            return
        
        self._extracting.add(context_id)
        try:
            # 获取短期记忆
            recent_messages = short_term.get_recent_str(context_id, n=200)
            if not recent_messages:
                return
            
            # 获取旧摘要
            old_summary = self.get_summary(context_id)
            
            LOG.info(f"开始提取长期记忆 [{context_id}]...")
            
            # LLM 提取
            new_summary = await self.extractor.extract(recent_messages, old_summary)
            
            # 保存
            self._save_summary(context_id, new_summary)
            
            # 重置短期记忆计数器
            short_term.reset_counter(context_id)
            
        except Exception as e:
            LOG.error(f"提取长期记忆失败 [{context_id}]: {e}")
        finally:
            self._extracting.discard(context_id)

    def trigger_extract_async(
        self, 
        context_id: str, 
        short_term: ShortTermMemory
    ) -> None:
        """异步触发提取（不阻塞当前流程）"""
        asyncio.create_task(self.extract_and_save(context_id, short_term))


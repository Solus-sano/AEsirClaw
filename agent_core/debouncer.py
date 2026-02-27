"""防抖调度器。控制连续消息的处理时机，每个 context 串行执行。"""

from __future__ import annotations

import asyncio
from typing import Coroutine

from ncatbot.utils import get_log

LOG = get_log(__name__)


class Debouncer:
    """防抖调度器。

    收到消息后不立即处理，而是等待 *delay* 秒。
    如果在等待期间有新消息到达，旧的候选任务会被覆盖。
    同一 context 下保证串行：正在执行时新消息排队等候。
    """

    def __init__(self, delay: float = 5.0):
        self.delay = delay
        self._candidate: dict[str, Coroutine] = {}
        self._loops: dict[str, asyncio.Task] = {}
        self._is_processing: dict[str, bool] = {}

    def schedule(self, context_id: str, coro: Coroutine) -> None:
        """调度一个候选任务。

        如果 context 正在处理中，新任务排队等候（覆盖旧候选）。
        否则启动新的处理循环。
        """
        self._candidate[context_id] = coro

        if self._is_processing.get(context_id, False):
            LOG.debug("[%s] 正在处理中，候选任务已缓存", context_id)
            return

        need_new_loop = True
        if context_id in self._loops:
            if not self._loops[context_id].done():
                need_new_loop = False

        if need_new_loop:
            self._loops[context_id] = asyncio.create_task(
                self._process_loop(context_id)
            )

    async def _process_loop(self, context_id: str) -> None:
        """持续消费候选任务，直到没有新任务为止。"""
        self._is_processing[context_id] = True
        try:
            while True:
                await asyncio.sleep(self.delay)
                coro = self._candidate.pop(context_id, None)
                if coro is None:
                    break
                try:
                    await coro
                except Exception:
                    LOG.exception("[%s] 防抖任务执行异常", context_id)
        finally:
            self._is_processing[context_id] = False

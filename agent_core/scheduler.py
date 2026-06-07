"""定时任务调度器。

将「定时触发」纳入 Agent 的主动行为：到点后调用注入的触发回调，
让对应 context 重新跑一次 Agent Loop。

任务以 JSON 文件持久化（默认 data/schedules.json），方便手动查看与编辑。
调度循环挂在主进程的 asyncio event loop 上，与 QQ 事件共享同一循环，
因此触发回调可以直接访问 pipeline / bot_api 等运行时依赖。

支持三种任务类型（统一用 next_run unix 时间戳表示下次触发时刻）：
- once:     一次性任务，触发后自动删除。
- interval: 每隔 interval_seconds 秒重复。
- daily:    每天固定 HH:MM 触发。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable, Optional

from ncatbot.utils import get_log

LOG = get_log(__name__)

TriggerCallback = Callable[[str, str], Awaitable[None]]
"""触发回调签名：async (context_id, prompt) -> None。"""

VALID_TYPES = ("once", "interval", "daily")


@dataclass
class ScheduledTask:
    """一个定时任务。"""

    context_id: str           # group:xxx 或 private:xxx，决定在哪触发
    type: str                 # once | interval | daily
    prompt: str               # 触发时喂给 Agent 的任务说明
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    next_run: float = 0.0     # 下次触发的 unix 时间戳
    interval_seconds: Optional[float] = None  # type=interval 时有效
    time: Optional[str] = None                # type=daily 时有效，格式 "HH:MM"
    enabled: bool = True
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M"))
    last_run: Optional[str] = None

    def describe(self) -> str:
        """生成人类可读的调度说明，用于回显给用户。"""
        if self.type == "interval":
            return f"每隔 {self.interval_seconds:.0f} 秒"
        if self.type == "daily":
            return f"每天 {self.time}"
        nxt = datetime.fromtimestamp(self.next_run).strftime("%Y-%m-%d %H:%M")
        return f"一次性，于 {nxt}"


class TaskScheduler:
    """定时任务调度器：JSON 持久化 + asyncio 常驻扫描循环。"""

    def __init__(
        self,
        storage_path: Path | str,
        *,
        scan_interval: float = 60.0,
    ):
        self.storage_path = Path(storage_path)
        self.scan_interval = scan_interval
        self._tasks: dict[str, ScheduledTask] = {}
        self._callback: Optional[TriggerCallback] = None
        self._loop_task: Optional[asyncio.Task] = None
        self._load()

    # ── 生命周期 ──────────────────────────────────────────────

    def set_callback(self, callback: TriggerCallback) -> None:
        """注入触发回调。必须在 start() 前调用。"""
        self._callback = callback

    def start(self) -> None:
        """启动常驻扫描循环（幂等）。"""
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._loop_task = asyncio.create_task(self._scan_loop())
        LOG.info("TaskScheduler 已启动，加载任务 %d 个", len(self._tasks))

    def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            self._loop_task = None

    # ── 任务增删改查（供 MCP 工具调用）────────────────────────

    def add_task(
        self,
        *,
        context_id: str,
        type: str,
        prompt: str,
        interval_seconds: Optional[float] = None,
        time_str: Optional[str] = None,
        delay_seconds: Optional[float] = None,
    ) -> ScheduledTask:
        """新增一个任务。

        Args:
            context_id: 任务触发的目标会话（group:xxx / private:xxx）。
            type: once | interval | daily。
            prompt: 触发时喂给 Agent 的任务说明。
            interval_seconds: type=interval 必填，重复间隔（秒）。
            time_str: type=daily 必填，每天触发时刻 "HH:MM"。
            delay_seconds: type=once 时，相对当前时间多少秒后触发。

        Raises:
            ValueError: 参数非法时抛出，调用方负责转成给 Agent 的错误信息。
        """
        if type not in VALID_TYPES:
            raise ValueError(f"不支持的任务类型 '{type}'，应为 {VALID_TYPES} 之一")
        if not prompt or not prompt.strip():
            raise ValueError("prompt 不能为空")

        task = ScheduledTask(
            context_id=context_id,
            type=type,
            prompt=prompt.strip(),
            interval_seconds=interval_seconds,
            time=time_str,
        )
        task.next_run = self._compute_next_run(task, delay_seconds=delay_seconds)
        self._tasks[task.id] = task
        self._save()
        LOG.info("[Scheduler] 新增任务 %s (%s) @ %s", task.id, task.describe(), context_id)
        return task

    def remove_task(self, task_id: str, *, context_id: Optional[str] = None) -> bool:
        """删除任务。若提供 context_id，则只允许删除属于该会话的任务。"""
        task = self._tasks.get(task_id)
        if task is None:
            return False
        if context_id is not None and task.context_id != context_id:
            return False
        del self._tasks[task_id]
        self._save()
        LOG.info("[Scheduler] 删除任务 %s", task_id)
        return True

    def list_tasks(self, *, context_id: Optional[str] = None) -> list[ScheduledTask]:
        """列出任务。若提供 context_id，只返回属于该会话的任务。"""
        tasks = list(self._tasks.values())
        if context_id is not None:
            tasks = [t for t in tasks if t.context_id == context_id]
        return sorted(tasks, key=lambda t: t.next_run)

    # ── 调度核心 ──────────────────────────────────────────────

    async def _scan_loop(self) -> None:
        """常驻循环：周期性扫描到期任务并触发。"""
        while True:
            try:
                await asyncio.sleep(self.scan_interval)
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                LOG.exception("[Scheduler] 扫描循环异常")

    async def _tick(self) -> None:
        now = time.time()
        # 快照避免触发回调内修改字典导致 RuntimeError
        due = [t for t in self._tasks.values() if t.enabled and t.next_run <= now]
        if not due:
            return

        for task in due:
            await self._fire(task, now)

    async def _fire(self, task: ScheduledTask, now: float) -> None:
        """触发单个任务，并按类型重算 next_run 或删除。"""
        if self._callback is None:
            LOG.warning("[Scheduler] 触发回调未设置，跳过任务 %s", task.id)
            return

        LOG.info("[Scheduler] 触发任务 %s @ %s", task.id, task.context_id)
        try:
            await self._callback(task.context_id, task.prompt)
        except Exception:
            LOG.exception("[Scheduler] 任务 %s 触发回调异常", task.id)

        task.last_run = time.strftime("%Y-%m-%d %H:%M")

        if task.type == "once":
            self._tasks.pop(task.id, None)
        else:
            # interval / daily：基于「当前时刻」重算下次，避免停机堆积补偿
            task.next_run = self._compute_next_run(task, reference=now)
        self._save()

    # ── 时间计算 ──────────────────────────────────────────────

    def _compute_next_run(
        self,
        task: ScheduledTask,
        *,
        reference: Optional[float] = None,
        delay_seconds: Optional[float] = None,
    ) -> float:
        """根据任务类型计算下次触发的 unix 时间戳。"""
        ref = reference if reference is not None else time.time()

        if task.type == "once":
            return ref + float(delay_seconds if delay_seconds is not None else 0.0)

        if task.type == "interval":
            if not task.interval_seconds or task.interval_seconds <= 0:
                raise ValueError("interval 任务必须提供正的 interval_seconds")
            return ref + float(task.interval_seconds)

        if task.type == "daily":
            return self._next_daily_timestamp(task.time, ref)

        raise ValueError(f"不支持的任务类型 '{task.type}'")

    @staticmethod
    def _next_daily_timestamp(time_str: Optional[str], ref: float) -> float:
        """计算下一个 HH:MM 对应的 unix 时间戳（基于本地时区）。"""
        if not time_str:
            raise ValueError("daily 任务必须提供 time（格式 HH:MM）")
        try:
            hour, minute = (int(x) for x in time_str.split(":"))
        except (ValueError, AttributeError):
            raise ValueError(f"非法的时间格式 '{time_str}'，应为 HH:MM")
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"非法的时间 '{time_str}'，时应在 0-23、分应在 0-59")

        ref_dt = datetime.fromtimestamp(ref)
        candidate = ref_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate.timestamp() <= ref:
            candidate += timedelta(days=1)
        return candidate.timestamp()

    # ── 持久化 ────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            LOG.error("[Scheduler] 加载任务文件失败: %s", exc)
            return
        if not isinstance(raw, list):
            LOG.warning("[Scheduler] 任务文件格式异常（非列表），忽略")
            return

        now = time.time()
        valid_fields = ScheduledTask.__dataclass_fields__.keys()
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                filtered = {k: v for k, v in item.items() if k in valid_fields}
                task = ScheduledTask(**filtered)
                # 重复任务若 next_run 已过期（停机期间错过），重算到未来
                if task.type in ("interval", "daily") and task.next_run <= now:
                    task.next_run = self._compute_next_run(task, reference=now)
                self._tasks[task.id] = task
            except (TypeError, ValueError) as exc:
                LOG.warning("[Scheduler] 跳过非法任务条目 %s: %s", item, exc)

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = [asdict(t) for t in self._tasks.values()]
            self.storage_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            LOG.error("[Scheduler] 保存任务文件失败: %s", exc)

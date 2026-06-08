"""TaskScheduler 回归测试。

覆盖定时任务调度器的核心逻辑（纯逻辑，无需 QQ / 网络 / Docker 依赖）：
- 三类任务（once / interval / daily）的 next_run 计算
- 按 context_id 过滤的增删改查与跨会话删除保护
- 参数校验
- JSON 持久化往返
- 启动时对齐过期重复任务（不补发）、once 过期补发一次后删除
- 调度循环实际触发

直接运行：
    uv run python test/test_scheduler.py
"""

import asyncio
import json
import os
import sys
import tempfile
import time

sys.path.append(os.path.abspath("./"))

from agent_core.scheduler import ScheduledTask, TaskScheduler


def _new_scheduler(scan_interval: float = 0.2) -> tuple[TaskScheduler, str]:
    """构造一个使用临时文件的调度器。"""
    tmp = tempfile.mktemp(suffix=".json")
    return TaskScheduler(tmp, scan_interval=scan_interval), tmp


def test_add_and_compute_next_run():
    """三类任务的 next_run 计算 + describe。"""
    s, tmp = _new_scheduler()
    try:
        t_iv = s.add_task(context_id="group:1", type="interval", prompt="报时", interval_seconds=3600)
        assert t_iv.next_run > time.time(), "interval next_run 应在未来"
        assert t_iv.describe() == "每隔 3600 秒"

        t_daily = s.add_task(context_id="private:2", type="daily", prompt="早安", time_str="09:00")
        assert t_daily.next_run > time.time(), "daily next_run 应在未来"
        assert t_daily.describe() == "每天 09:00"

        t_once = s.add_task(context_id="group:1", type="once", prompt="提醒", delay_seconds=600)
        assert abs(t_once.next_run - (time.time() + 600)) < 5, "once 应在 ~600s 后"

        print("[PASS] test_add_and_compute_next_run")
    finally:
        os.path.exists(tmp) and os.remove(tmp)


def test_list_filter_and_remove_protection():
    """按 context 过滤、跨会话删除保护。"""
    s, tmp = _new_scheduler()
    try:
        t1 = s.add_task(context_id="group:1", type="interval", prompt="a", interval_seconds=60)
        s.add_task(context_id="group:1", type="interval", prompt="b", interval_seconds=60)
        s.add_task(context_id="private:2", type="interval", prompt="c", interval_seconds=60)

        assert len(s.list_tasks(context_id="group:1")) == 2
        assert len(s.list_tasks(context_id="private:2")) == 1
        assert len(s.list_tasks()) == 3

        # 跨会话删除应被拒绝
        assert s.remove_task(t1.id, context_id="private:2") is False, "应阻止跨会话删除"
        assert s.remove_task(t1.id, context_id="group:1") is True
        assert s.remove_task("not_exist") is False

        print("[PASS] test_list_filter_and_remove_protection")
    finally:
        os.path.exists(tmp) and os.remove(tmp)


def test_param_validation():
    """非法参数应抛出 ValueError。"""
    s, tmp = _new_scheduler()
    try:
        for kwargs, label in [
            (dict(context_id="g", type="interval", prompt="x"), "interval 缺 interval_seconds"),
            (dict(context_id="g", type="daily", prompt="x", time_str="25:99"), "非法时间"),
            (dict(context_id="g", type="daily", prompt="x"), "daily 缺 time"),
            (dict(context_id="g", type="weekly", prompt="x"), "未知类型"),
            (dict(context_id="g", type="once", prompt="  "), "空 prompt"),
        ]:
            try:
                s.add_task(**kwargs)
                raise AssertionError(f"应抛 ValueError: {label}")
            except ValueError:
                pass
        print("[PASS] test_param_validation")
    finally:
        os.path.exists(tmp) and os.remove(tmp)


def test_persistence_roundtrip():
    """任务落盘后重新加载应一致。"""
    s, tmp = _new_scheduler()
    try:
        s.add_task(context_id="group:1", type="interval", prompt="a", interval_seconds=60)
        s.add_task(context_id="private:2", type="daily", prompt="b", time_str="09:00")

        data = json.loads(open(tmp, encoding="utf-8").read())
        assert len(data) == 2, "应持久化 2 条"

        s2 = TaskScheduler(tmp)
        assert len(s2.list_tasks()) == 2, "重新加载应得 2 条"
        print("[PASS] test_persistence_roundtrip")
    finally:
        os.path.exists(tmp) and os.remove(tmp)


def test_startup_align_and_fire():
    """启动时：过期 daily/interval 对齐到未来（不补发）；过期 once 补发一次后删除。

    这是对「定时任务不触发」bug 修复的核心回归保护。
    """
    now = time.time()
    tasks = [
        {"context_id": "private:1", "type": "daily", "prompt": "HN新闻", "id": "aa",
         "next_run": now - 3600, "time": "08:00", "interval_seconds": None,
         "enabled": True, "created_at": "x", "last_run": None},
        {"context_id": "private:1", "type": "interval", "prompt": "报时", "id": "bb",
         "next_run": now - 30, "interval_seconds": 3600, "time": None,
         "enabled": True, "created_at": "x", "last_run": None},
        {"context_id": "private:1", "type": "once", "prompt": "提醒", "id": "cc",
         "next_run": now - 10, "interval_seconds": None, "time": None,
         "enabled": True, "created_at": "x", "last_run": None},
    ]
    tmp = tempfile.mktemp(suffix=".json")
    open(tmp, "w", encoding="utf-8").write(json.dumps(tasks, ensure_ascii=False))

    s = TaskScheduler(tmp, scan_interval=0.2)
    fired: list[tuple[str, str]] = []

    async def cb(ctx: str, prompt: str) -> None:
        fired.append((ctx, prompt[:6]))

    s.set_callback(cb)

    async def run() -> None:
        s.start()
        by_id = {t.id: t for t in s.list_tasks()}
        assert by_id["aa"].next_run > time.time(), "过期 daily 应被对齐到未来"
        assert by_id["bb"].next_run > time.time(), "过期 interval 应被对齐到未来"
        assert by_id["cc"].next_run <= time.time(), "once 不对齐，保留过期值"
        await asyncio.sleep(0.5)  # 让扫描循环跑若干轮
        s.stop()

    try:
        asyncio.run(run())
        assert ("private:1", "提醒") in fired, f"过期 once 应被补发一次: {fired}"
        assert ("private:1", "HN新闻") not in fired, "已对齐的 daily 不应在启动瞬间触发"
        assert ("private:1", "报时") not in fired, "已对齐的 interval 不应在启动瞬间触发"
        assert s.remove_task("cc") is False, "once 触发后应已自动删除"
        print("[PASS] test_startup_align_and_fire")
    finally:
        os.path.exists(tmp) and os.remove(tmp)


def test_interval_reschedules_after_fire():
    """interval 任务触发后应重算 next_run 到未来，且不被删除。"""
    now = time.time()
    tasks = [
        {"context_id": "group:9", "type": "interval", "prompt": "tick", "id": "iv",
         "next_run": now - 1, "interval_seconds": 3600, "time": None,
         "enabled": True, "created_at": "x", "last_run": None},
    ]
    tmp = tempfile.mktemp(suffix=".json")
    open(tmp, "w", encoding="utf-8").write(json.dumps(tasks, ensure_ascii=False))

    fired: list[str] = []

    async def cb(ctx: str, prompt: str) -> None:
        fired.append(ctx)

    # scan_interval 很小，但 start() 会先把过期 interval 对齐到未来，
    # 因此这里手动构造「过期且不对齐」的触发：直接调用内部 _fire。
    s = TaskScheduler(tmp, scan_interval=999)
    s.set_callback(cb)

    async def run() -> None:
        task = s.list_tasks()[0]
        await s._fire(task, time.time())

    try:
        asyncio.run(run())
        assert fired == ["group:9"], f"interval 应触发一次: {fired}"
        survivor = s.list_tasks()
        assert len(survivor) == 1, "interval 触发后不应被删除"
        assert survivor[0].next_run > time.time(), "interval 触发后 next_run 应推到未来"
        assert survivor[0].last_run is not None, "应记录 last_run"
        print("[PASS] test_interval_reschedules_after_fire")
    finally:
        os.path.exists(tmp) and os.remove(tmp)


def run_all() -> None:
    test_add_and_compute_next_run()
    test_list_filter_and_remove_protection()
    test_param_validation()
    test_persistence_roundtrip()
    test_startup_align_and_fire()
    test_interval_reschedules_after_fire()
    print("\nALL SCHEDULER TESTS PASSED")


if __name__ == "__main__":
    run_all()

"""防抖与群聊 @ 路由回归。

运行：python -m unittest discover -s test -p test_debouncer.py
"""
import asyncio
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agent_core.debouncer import Debouncer
from agent_core.trigger import TriggerManager
from agent_core.pipeline import MessagePipeline
from plugins.agent_router.agent_router import AgentRouterPlugin


class DebouncerTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_message_resets_delay_and_closes_replaced_candidate(self):
        debouncer = Debouncer(delay=0.08)
        calls = []

        async def handle(value):
            calls.append((value, asyncio.get_running_loop().time()))

        old = handle("old")
        debouncer.schedule("group:1", old)
        await asyncio.sleep(0.04)
        last_arrival = asyncio.get_running_loop().time()
        debouncer.schedule("group:1", handle("new"))
        self.assertEqual(inspect.getcoroutinestate(old), inspect.CORO_CLOSED)
        await debouncer._loops["group:1"]
        self.assertEqual([value for value, _ in calls], ["new"])
        self.assertGreaterEqual(calls[0][1] - last_arrival, debouncer.delay)
        self.assertFalse(debouncer._loops)

    async def test_messages_during_processing_are_serial_and_merged(self):
        debouncer = Debouncer(delay=0.01)
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def first():
            calls.append("first start")
            started.set()
            await release.wait()
            calls.append("first end")

        async def followup(value):
            calls.append(value)

        debouncer.schedule("group:1", first())
        await asyncio.wait_for(started.wait(), 1)
        debouncer.schedule("group:1", followup("discarded"))
        debouncer.schedule("group:1", followup("latest"))
        await asyncio.sleep(0.02)
        self.assertEqual(calls, ["first start"])
        release.set()
        await debouncer._loops["group:1"]
        self.assertEqual(calls, ["first start", "first end", "latest"])

    async def test_cancellation_closes_pending_candidate(self):
        debouncer = Debouncer(delay=1)

        async def handle():
            pass

        debouncer.schedule("group:1", handle())
        task = debouncer._loops["group:1"]
        await asyncio.sleep(0)
        pending = handle()
        debouncer.schedule("group:1", pending)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(inspect.getcoroutinestate(pending), inspect.CORO_CLOSED)
        self.assertFalse(debouncer._candidate)

    async def test_batch_bypasses_cooldown_without_changing_latest_message_identity(self):
        router = AgentRouterPlugin.__new__(AgentRouterPlugin)
        router.group_profile_map = {}
        router.bot_qq = "123"
        router.memory = Mock()
        router.debouncer = Debouncer(delay=0.01)
        router._pending_cooldown_bypass = set()
        trigger = TriggerManager(group_cooldown_seconds=72000)
        results = []
        handled = []

        async def safe_handle(context_id, **kwargs):
            handled.append(kwargs)
            results.append(trigger.check(context_id, **kwargs))
            trigger.record_response(context_id)

        router._safe_handle = safe_handle

        def event(text, mentions=()):
            return SimpleNamespace(
                group_id=1, user_id="456", self_id="123", raw_message=text,
                message=SimpleNamespace(filter_at=lambda: [
                    SimpleNamespace(qq=qq) for qq in mentions
                ]),
            )

        # 连续两批 @，均处于冷却期；机器人是第二个 @ 且 ID 为整数。
        trigger.record_response("group:1")
        for _ in range(2):
            await router.on_group_msg(event("先说一句"))
            await router.on_group_msg(event("@bot", (999, 123)))
            await router.debouncer._loops["group:1"]
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.should_respond for result in results))
        self.assertTrue(all(result.reason == "被@提及" for result in results))
        # 普通消息覆盖候选，但本批仍绕过冷却，且保留真实消息身份。
        mention = event("@bot", (123,))
        followup = event("普通消息")
        await router.on_group_msg(mention)
        await router.on_group_msg(followup)
        await router.debouncer._loops["group:1"]
        self.assertEqual(len(results), 3)
        self.assertTrue(results[-1].should_respond)
        self.assertFalse(handled[-1]["is_at_me"])
        self.assertTrue(handled[-1]["bypass_cooldown"])
        self.assertEqual(handled[-1]["message"], "普通消息")
        router.memory.append_from_event.assert_any_call("group:1", mention)
        router.memory.append_from_event.assert_any_call("group:1", followup)

        # 无 @ 的下一批恢复冷却，不泄漏上批资格。
        await router.on_group_msg(event("下一批普通消息"))
        await router.debouncer._loops["group:1"]
        self.assertFalse(results[-1].should_respond)
        self.assertFalse(handled[-1]["bypass_cooldown"])

    async def test_mention_during_processing_belongs_to_next_batch(self):
        router = AgentRouterPlugin.__new__(AgentRouterPlugin)
        router._pending_cooldown_bypass = {"group:1"}
        router.debouncer = Debouncer(delay=0.01)
        trigger = TriggerManager(group_cooldown_seconds=72000)
        started, release = asyncio.Event(), asyncio.Event()
        results = []

        async def safe_handle(context_id, **kwargs):
            results.append(trigger.check(context_id, **kwargs))
            if len(results) == 1:
                started.set()
                await release.wait()
            trigger.record_response(context_id)

        router._safe_handle = safe_handle
        router.debouncer.schedule("group:1", router._handle_debounced_group(
            "group:1", is_at_me=False, message="第一批补充",
        ))
        await asyncio.wait_for(started.wait(), 1)
        self.assertNotIn("group:1", router._pending_cooldown_bypass)
        # 本轮执行中收到新的 @，随后又被普通消息覆盖。
        router._pending_cooldown_bypass.add("group:1")
        router.debouncer.schedule("group:1", router._handle_debounced_group(
            "group:1", is_at_me=True, message="@bot",
        ))
        router.debouncer.schedule("group:1", router._handle_debounced_group(
            "group:1", is_at_me=False, message="第二批补充",
        ))
        # 其他群不共享绕过冷却的资格。
        trigger.record_response("group:2")
        await router._handle_debounced_group("group:2", is_at_me=False, message="普通消息")
        self.assertFalse(results[-1].should_respond)
        release.set()
        await router.debouncer._loops["group:1"]
        self.assertEqual(len(results), 3)
        self.assertTrue(results[-1].should_respond)
        self.assertFalse(router._pending_cooldown_bypass)

    async def test_pipeline_forwards_batch_flag_to_trigger(self):
        pipeline = MessagePipeline.__new__(MessagePipeline)
        pipeline.trigger = Mock()
        pipeline.trigger.check.return_value.should_respond = False
        await pipeline.handle(
            "group:1", message="补充", is_at_me=False, bypass_cooldown=True,
        )
        pipeline.trigger.check.assert_called_once_with(
            context_id="group:1", message="补充", is_at_me=False,
            is_scheduled=False, bypass_cooldown=True,
        )

    def test_mention_identity_alone_does_not_bypass_cooldown(self):
        trigger = TriggerManager(group_cooldown_seconds=72000)
        trigger.record_response("group:1")
        result = trigger.check("group:1", "@bot", is_at_me=True)
        self.assertFalse(result.should_respond)
        self.assertIn("冷却中", result.reason)


if __name__ == "__main__":
    unittest.main()

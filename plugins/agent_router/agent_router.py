"""QQ 事件路由插件 — 薄层，只做事件转换、记忆写入和防抖调度。"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

import yaml

from ncatbot.core.event import GroupMessageEvent, PrivateMessageEvent
from ncatbot.plugin_system import NcatBotPlugin, filter_registry
from ncatbot.utils import get_log
from ncatbot.utils.assets.literals import OFFICIAL_STARTUP_EVENT

from agent_core.config import AppConfig
from agent_core.controller import AgentController
from agent_core.debouncer import Debouncer
from agent_core.llm import LLMClient
from agent_core.memory.long_term import LongTermMemory
from agent_core.memory.short_term import ShortTermMemory
from agent_core.output import MessageOutputter
from agent_core.pipeline import MessagePipeline
from agent_core.trigger import TriggerManager
from agent_core.tools import create_mcp_server
from agent_core.tools.docker_executor import DockerExecutor

LOG = get_log(__name__)

DEFAULT_GROUP_WHITELIST: list[str] = []


class AgentRouterPlugin(NcatBotPlugin):
    """将 QQ 消息路由到 AEsirClaw Agent 的插件入口。"""

    name = "AgentRouter"
    version = "0.2.1"

    async def on_load(self):
        self.cfg = AppConfig()

        bot_cfg = self.cfg.bot
        memory_cfg = self.cfg.memory
        llm_cfg = self.cfg.llm

        self.group_whitelist = bot_cfg.get("group_whitelist", DEFAULT_GROUP_WHITELIST)
        self.private_whitelist = bot_cfg.get("private_whitelist", [])
        self.bot_qq = bot_cfg.get("bot_qq", "")
        self.init_short_term_messages = int(memory_cfg.get("init_short_term_messages", 50))

        # ── 核心组件初始化 ───────────────────────────────────

        self.memory = ShortTermMemory(
            api=self.api,
            max_size=int(memory_cfg.get("short_term_queue_size", 200)),
        )

        self.llm = LLMClient(self.cfg)
        self.long_term_memory = LongTermMemory(
            self.llm,
            storage_dir=memory_cfg.get("summaries_dir", "memory/summaries"),
        )
        self.max_iterations = int(llm_cfg.get("max_iterations", 10))
        self.context_short_term_messages = int(memory_cfg.get("context_short_term_messages", 100))
        self.extraction_threshold = int(memory_cfg.get("extraction_threshold", 200))
        self.executor = DockerExecutor()

        self._load_routing_config()
        self._pipeline_cache: dict[str, MessagePipeline] = {}

        self.debouncer = Debouncer(delay=5.0)

        self.register_handler(OFFICIAL_STARTUP_EVENT, self._on_bot_ready)

        LOG.info("AgentRouterPlugin loaded with model=%s", llm_cfg.get("model"))

    # ── Startup ──────────────────────────────────────────────

    async def _on_bot_ready(self, event):
        """Bot 连接成功后加载历史消息。"""
        LOG.info("Bot 连接成功，开始加载历史消息...")
        await self._load_recent_messages()
        LOG.info("历史消息加载完成。")

    async def _load_recent_messages(self) -> None:
        count = self.init_short_term_messages
        if count <= 0:
            return
        for group_id in self.group_whitelist:
            history = await self.api.get_group_msg_history(
                group_id=group_id,
                count=count,
            )
            await self.memory.get_userid_nickname_map(group_id)
            for ev in history:
                self.memory.append_from_event(f"group:{group_id}", ev)

        for user_id in self.private_whitelist:
            history = await self.api.get_friend_msg_history(
                user_id=user_id,
                message_seq=0,
                count=count,
            )
            for ev in history:
                self.memory.append_from_event(f"private:{user_id}", ev)

    # ── 群聊消息 ─────────────────────────────────────────────

    @filter_registry.group_filter
    async def on_group_msg(self, event: GroupMessageEvent):
        if not self._is_group_allowed(event):
            return

        context_id = f"group:{event.group_id}"

        # bot 回声跳过（MCP 发送工具已写入记忆）
        if self._check_self(event):
            return

        self.memory.append_from_event(context_id, event)

        text = (getattr(event, "raw_message", "") or "").strip()
        if not text:
            return

        is_at_me = self._check_at_me(event)

        # @ 强制触发：跳过防抖
        if is_at_me:
            await self._safe_handle(context_id, is_at_me=True, message=text)
            return

        # 普通消息：走防抖
        self.debouncer.schedule(
            context_id,
            self._safe_handle(context_id, message=text),
        )

    # ── 私聊消息 ─────────────────────────────────────────────

    @filter_registry.private_filter
    async def on_private_msg(self, event: PrivateMessageEvent):
        if not self._is_private_allowed(event):
            return

        context_id = f"private:{event.user_id}"

        # bot 回声跳过（MCP 发送工具已写入记忆）
        if self._check_self(event):
            return

        self.memory.append_from_event(context_id, event)

        text = (getattr(event, "raw_message", "") or "").strip()
        if not text:
            return

        self.debouncer.schedule(
            context_id,
            self._safe_handle(context_id, message=text),
        )

    # ── 内部方法 ─────────────────────────────────────────────

    async def _safe_handle(self, context_id: str, **kwargs) -> None:
        try:
            pipeline = self._get_pipeline(context_id)
            await pipeline.handle(context_id, **kwargs)
        except Exception as exc:
            LOG.error("处理消息失败 [%s]: %s\n%s", context_id, exc, traceback.format_exc())

    def _load_routing_config(self) -> None:
        raw_bot = getattr(self.cfg, "_bot", {})
        routing_cfg = raw_bot.get("routing", {})
        defaults_cfg = routing_cfg.get("defaults", {})

        self.default_persona_file = str(defaults_cfg.get("persona_file", "personal_xlpj.yaml"))
        trigger_cfg = defaults_cfg.get("trigger", {})
        output_cfg = defaults_cfg.get("output", {})
        self.default_trigger_cfg = trigger_cfg if isinstance(trigger_cfg, dict) else {}
        self.default_output_cfg = output_cfg if isinstance(output_cfg, dict) else {}

        groups = routing_cfg.get("groups", {})
        users = routing_cfg.get("users", {})
        self.group_overrides = groups if isinstance(groups, dict) else {}
        self.user_overrides = users if isinstance(users, dict) else {}

    @staticmethod
    def _merge_dict(base: dict | None, override: dict | None) -> dict:
        merged = dict(base or {})
        if isinstance(override, dict):
            merged.update(override)
        return merged

    @staticmethod
    def _to_delay_range(value: Any) -> tuple[float, float]:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return float(value[0]), float(value[1])
        return (0.5, 2.0)

    def _resolve_context_config(self, context_id: str) -> dict[str, Any]:
        override: dict[str, Any] = {}
        if context_id.startswith("group:"):
            group_id = context_id.removeprefix("group:")
            cand = self.group_overrides.get(group_id, {})
            if isinstance(cand, dict):
                override = cand
        elif context_id.startswith("private:"):
            user_id = context_id.removeprefix("private:")
            cand = self.user_overrides.get(user_id, {})
            if isinstance(cand, dict):
                override = cand

        return {
            "persona_file": str(override.get("persona_file", self.default_persona_file)),
            "trigger": self._merge_dict(self.default_trigger_cfg, override.get("trigger")),
            "output": self._merge_dict(self.default_output_cfg, override.get("output")),
        }

    def _load_persona_file(self, persona_file: str) -> dict:
        path = self.cfg.config_dir / persona_file
        if not path.exists():
            LOG.warning("人格文件不存在，使用空人格: %s", persona_file)
            persona = {}
        else:
            persona = self._read_yaml(path)
        persona["QQ_ID"] = self.bot_qq
        return persona

    @staticmethod
    def _read_yaml(path: Path) -> dict:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _get_pipeline(self, context_id: str) -> MessagePipeline:
        cached = self._pipeline_cache.get(context_id)
        if cached is not None:
            return cached

        resolved = self._resolve_context_config(context_id)
        persona = self._load_persona_file(resolved["persona_file"])
        output_cfg = resolved["output"]
        trigger_cfg = resolved["trigger"]

        outputter = MessageOutputter(
            api=self.api,
            typing_delay_per_char=float(output_cfg.get("typing_delay_per_char", 0.05)),
            random_delay_range=self._to_delay_range(output_cfg.get("random_delay_range", [0.5, 2.0])),
            max_delay=float(output_cfg.get("max_delay", 5.0)),
        )

        bot_name = persona.get("core", {}).get("name", "Bot")
        mcp = create_mcp_server(
            outputter=outputter,
            bot_api=self.api,
            memory=self.memory,
            bot_name=bot_name,
            executor=self.executor,
        )

        controller = AgentController(
            llm=self.llm,
            mcp=mcp,
            max_iterations=self.max_iterations,
        )

        trigger = TriggerManager(
            bot_qq=self.bot_qq,
            keywords=trigger_cfg.get("keywords", []),
            group_cooldown_seconds=float(trigger_cfg.get("group_cooldown_seconds", 30.0)),
            private_cooldown_seconds=float(trigger_cfg.get("private_cooldown_seconds", 10.0)),
        )

        pipeline = MessagePipeline(
            controller=controller,
            memory=self.memory,
            trigger=trigger,
            long_term_memory=self.long_term_memory,
            persona=persona,
            context_short_term_messages=self.context_short_term_messages,
            extraction_threshold=self.extraction_threshold,
        )
        self._pipeline_cache[context_id] = pipeline
        LOG.info("上下文配置已生效 [%s]: persona=%s", context_id, resolved["persona_file"])
        return pipeline

    def _is_group_allowed(self, event: GroupMessageEvent) -> bool:
        group_id = getattr(event, "group_id", None)
        if group_id is None:
            return False
        whitelist = {str(g) for g in self.group_whitelist}
        return not whitelist or str(group_id) in whitelist

    def _is_private_allowed(self, event: PrivateMessageEvent) -> bool:
        user_id = getattr(event, "user_id", None)
        if user_id is None:
            return False
        
        whitelist = {str(u) for u in self.private_whitelist}
        return not whitelist or str(user_id) in whitelist

    def _check_self(self, event) -> bool:
        return event.self_id == event.user_id

    def _check_at_me(self, event) -> bool:
        at_lst = event.message.filter_at()
        if not at_lst:
            return False
        return at_lst[0].qq == self.bot_qq

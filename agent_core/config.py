"""统一配置中心。启动时加载一次，各模块通过属性获取子配置。"""

from __future__ import annotations

from pathlib import Path

import yaml


class AppConfig:
    """统一配置中心，加载 bot.yaml 和 persona YAML。

    各模块通过属性获取子配置节，避免各处重复读取 YAML 文件。
    """

    def __init__(self, config_dir: Path | str | None = None):
        if config_dir is None:
            config_dir = Path(__file__).resolve().parents[1] / "config"
        self._config_dir = Path(config_dir)
        self._bot = self._load("bot.yaml")
        self._persona: dict | None = None

    def load_persona(self, filename: str = "personal_OPCI.yaml") -> None:
        """加载人格配置文件。可多次调用以切换人格。"""
        self._persona = self._load(filename)

    # ── 子配置属性 ──────────────────────────────────────────

    @property
    def llm(self) -> dict:
        return self._bot.get("llm", {})

    @property
    def memory(self) -> dict:
        return self._bot.get("memory", {})

    @property
    def trigger(self) -> dict:
        return self._bot.get("trigger", {})

    @property
    def output(self) -> dict:
        return self._bot.get("output", {})

    @property
    def docker(self) -> dict:
        return self._bot.get("docker", {})

    @property
    def bot(self) -> dict:
        return self._bot.get("bot_config", {})

    @property
    def persona(self) -> dict:
        if self._persona is None:
            raise RuntimeError("Persona config not loaded. Call load_persona() first.")
        return self._persona

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    # ── 内部方法 ────────────────────────────────────────────

    def _load(self, filename: str) -> dict:
        path = self._config_dir / filename
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

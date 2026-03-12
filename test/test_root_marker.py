import unittest
import sys
import types

# Stub ncatbot dependency for isolated unit testing.
if "ncatbot" not in sys.modules:
    ncatbot = types.ModuleType("ncatbot")
    ncatbot_core = types.ModuleType("ncatbot.core")
    ncatbot_core_event = types.ModuleType("ncatbot.core.event")
    ncatbot_core_api = types.ModuleType("ncatbot.core.api")
    ncatbot_utils = types.ModuleType("ncatbot.utils")

    class _BaseMessageEvent:
        pass

    class _BotAPI:
        pass

    def _get_log(_name):
        class _Logger:
            def info(self, *args, **kwargs):
                return None
            def debug(self, *args, **kwargs):
                return None
            def warning(self, *args, **kwargs):
                return None
            def error(self, *args, **kwargs):
                return None
        return _Logger()

    ncatbot_core_event.BaseMessageEvent = _BaseMessageEvent
    ncatbot_core_api.BotAPI = _BotAPI
    ncatbot_utils.get_log = _get_log

    sys.modules["ncatbot"] = ncatbot
    sys.modules["ncatbot.core"] = ncatbot_core
    sys.modules["ncatbot.core.event"] = ncatbot_core_event
    sys.modules["ncatbot.core.api"] = ncatbot_core_api
    sys.modules["ncatbot.utils"] = ncatbot_utils

from agent_core.memory.short_term import ShortTermMemory


class _DummyAPI:
    pass


class _Sender:
    def __init__(self, nickname: str):
        self.nickname = nickname


class _Event:
    def __init__(self, *, user_id: str, nickname: str, raw_message: str, ts: int = 0):
        self.user_id = user_id
        self.sender = _Sender(nickname)
        self.raw_message = raw_message
        self.time = ts


class RootMarkerTests(unittest.TestCase):
    def test_group_message_from_root_has_marker(self):
        mem = ShortTermMemory(api=_DummyAPI(), root_qq="10001")
        mem.group_userid_nickname_map["123"] = {}
        ev = _Event(user_id="10001", nickname="管理员", raw_message="hello")
        mem.append_from_event("group:123", ev)

        text = mem.get_recent_str("group:123")
        self.assertIn("[ROOT]", text)

    def test_group_message_from_non_root_has_no_marker(self):
        mem = ShortTermMemory(api=_DummyAPI(), root_qq="10001")
        mem.group_userid_nickname_map["123"] = {}
        ev = _Event(user_id="20002", nickname="群友", raw_message="hello")
        mem.append_from_event("group:123", ev)

        text = mem.get_recent_str("group:123")
        self.assertNotIn("[ROOT]", text)

    def test_private_message_from_root_has_no_marker(self):
        mem = ShortTermMemory(api=_DummyAPI(), root_qq="10001")
        ev = _Event(user_id="10001", nickname="管理员", raw_message="hi")
        mem.append_from_event("private:10001", ev)

        text = mem.get_recent_str("private:10001")
        self.assertNotIn("[ROOT]", text)

    def test_empty_root_config_does_not_crash_and_no_marker(self):
        mem = ShortTermMemory(api=_DummyAPI(), root_qq="")
        mem.group_userid_nickname_map["123"] = {}
        ev = _Event(user_id="10001", nickname="管理员", raw_message="hello")
        mem.append_from_event("group:123", ev)

        text = mem.get_recent_str("group:123")
        self.assertNotIn("[ROOT]", text)


if __name__ == "__main__":
    unittest.main()

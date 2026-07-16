"""record 语音技能回归测试。

覆盖两块新功能，全部离线（不连真实 TTS 服务 / 不走网络 / 不依赖 Docker）：

1. TTS 合成脚本 skills/record/src/tts.py
   - 正常合成：请求 URL / 方法 / payload 正确，音频落盘，stdout 输出 JSON
   - 自定义情绪权重透传
   - 参数校验（空文本、越界权重）以退出码 1 结束
   - TTS 服务报错（HTTPError / 连不通）以退出码 1 结束

2. MCP 工具 send_group_record / send_private_record
   - 本地文件被 base64 编码（base64:// 前缀）后交给 bot_api
   - 短期记忆写入 [语音]
   - 文件不存在时返回 ok=False，且不调用 bot_api

直接运行：
    uv run python test/test_record.py
"""

import asyncio
import base64
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import urllib.error

sys.path.append(os.path.abspath("./"))

from agent_core.memory.short_term import ShortTermMemory
from agent_core.tools.mcp_tools import create_mcp_server
from agent_core.tools.provider import McpConnection, McpToolProvider

_PROJECT_ROOT = os.path.abspath("./")
_TTS_PATH = os.path.join(_PROJECT_ROOT, "skills", "record", "src", "tts.py")


# ─── 加载被测脚本为模块 ──────────────────────────────────────

def _load_tts_module():
    spec = importlib.util.spec_from_file_location("record_tts", _TTS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResp:
    """模拟 urlopen 返回的响应上下文管理器。"""

    def __init__(self, data: bytes):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._data


@contextlib.contextmanager
def _patched_urlopen(module, *, audio: bytes = b"RIFFxxxxFAKEWAV", raises=None):
    """临时替换 tts 脚本使用的 urlopen，并记录收到的请求。"""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["data"] = req.data
        captured["timeout"] = timeout
        if raises is not None:
            raise raises
        return _FakeResp(audio)

    original = module.urllib.request.urlopen
    module.urllib.request.urlopen = fake_urlopen
    try:
        yield captured
    finally:
        module.urllib.request.urlopen = original


def _run_tts(module, argv: list[str]) -> str:
    """以给定 argv 运行 tts.main()，返回其 stdout。"""
    old_argv = sys.argv
    sys.argv = ["tts.py", *argv]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            module.main()
    finally:
        sys.argv = old_argv
    return buf.getvalue()


# ─── 1. TTS 脚本测试 ──────────────────────────────────────

def test_tts_synthesize_ok():
    """正常合成：URL/方法/payload 正确，音频落盘，stdout 为成功 JSON。"""
    tts = _load_tts_module()
    audio = b"RIFF____fake_wav_bytes"
    out = tempfile.mktemp(suffix=".wav")
    try:
        with _patched_urlopen(tts, audio=audio) as cap:
            stdout = _run_tts(tts, ["你好呀", "--output", out])

        assert cap["url"] == "http://192.168.1.10:13172/tts", f"URL 错误: {cap['url']}"
        assert cap["method"] == "POST", "应为 POST"
        payload = json.loads(cap["data"])
        assert payload["text"] == "你好呀"
        assert payload["weights"] == [0, 0, 0, 0, 0, 0, 0, 1], "默认应为 calm=1"

        assert os.path.exists(out), "音频文件应已写入"
        assert open(out, "rb").read() == audio, "落盘内容应与响应一致"

        result = json.loads(stdout)
        assert result["ok"] is True
        assert result["output"] == out
        assert result["bytes"] == len(audio)
        print("[PASS] test_tts_synthesize_ok")
    finally:
        os.path.exists(out) and os.remove(out)


def test_tts_custom_weights():
    """自定义情绪权重应原样透传到请求体。"""
    tts = _load_tts_module()
    out = tempfile.mktemp(suffix=".wav")
    try:
        with _patched_urlopen(tts) as cap:
            _run_tts(
                tts,
                ["太好啦", "--weights", "0.8", "0", "0", "0", "0", "0", "0.2", "0",
                 "--output", out],
            )
        payload = json.loads(cap["data"])
        assert payload["weights"] == [0.8, 0, 0, 0, 0, 0, 0.2, 0], payload["weights"]
        print("[PASS] test_tts_custom_weights")
    finally:
        os.path.exists(out) and os.remove(out)


def test_tts_empty_text_exits():
    """空文本应以退出码 1 结束，且不发起请求。"""
    tts = _load_tts_module()
    called = {"hit": False}

    def should_not_call(req, timeout=None):
        called["hit"] = True

    tts.urllib.request.urlopen = should_not_call
    try:
        _run_tts(tts, ["   "])
        raise AssertionError("空文本应触发 SystemExit")
    except SystemExit as exc:
        assert exc.code == 1, f"退出码应为 1, 实际 {exc.code}"
        assert called["hit"] is False, "校验失败时不应发起请求"
    print("[PASS] test_tts_empty_text_exits")


def test_tts_bad_weights_exits():
    """越界权重（>1）应以退出码 1 结束。"""
    tts = _load_tts_module()
    with _patched_urlopen(tts):
        try:
            _run_tts(tts, ["hi", "--weights", "1.5", "0", "0", "0", "0", "0", "0", "0"])
            raise AssertionError("越界权重应触发 SystemExit")
        except SystemExit as exc:
            assert exc.code == 1, f"退出码应为 1, 实际 {exc.code}"
    print("[PASS] test_tts_bad_weights_exits")


def test_tts_http_error_exits():
    """TTS 服务返回 5xx 时应以退出码 1 结束。"""
    tts = _load_tts_module()
    out = tempfile.mktemp(suffix=".wav")
    err = urllib.error.HTTPError(
        "http://192.168.1.10:13172/tts", 500, "Server Error", {},
        io.BytesIO(b"model inference failed"),
    )
    try:
        with _patched_urlopen(tts, raises=err):
            try:
                _run_tts(tts, ["hi", "--output", out])
                raise AssertionError("HTTPError 应触发 SystemExit")
            except SystemExit as exc:
                assert exc.code == 1, f"退出码应为 1, 实际 {exc.code}"
        assert not os.path.exists(out), "合成失败不应写出音频文件"
        print("[PASS] test_tts_http_error_exits")
    finally:
        os.path.exists(out) and os.remove(out)


def test_tts_connection_error_exits():
    """连不通 TTS 服务时应以退出码 1 结束。"""
    tts = _load_tts_module()
    out = tempfile.mktemp(suffix=".wav")
    err = urllib.error.URLError("Connection refused")
    try:
        with _patched_urlopen(tts, raises=err):
            try:
                _run_tts(tts, ["hi", "--output", out])
                raise AssertionError("URLError 应触发 SystemExit")
            except SystemExit as exc:
                assert exc.code == 1, f"退出码应为 1, 实际 {exc.code}"
        print("[PASS] test_tts_connection_error_exits")
    finally:
        os.path.exists(out) and os.remove(out)


# ─── 2. MCP 工具测试 ──────────────────────────────────────

class _FakeOutputter:
    async def send_group(self, *a):
        pass

    async def send_private(self, *a):
        pass


class _FakeBotApi:
    """记录 send_*_record 调用参数的假 BotAPI。"""

    def __init__(self):
        self.calls: list[tuple] = []

    async def send_group_record(self, group_id, file):
        self.calls.append(("group", group_id, file))
        return "msg_1"

    async def send_private_record(self, user_id, file):
        self.calls.append(("private", user_id, file))
        return "msg_2"


@contextlib.contextmanager
def _workspace_file(name: str, data: bytes):
    """在项目 workspace 下放置一个临时文件，产出 tool 用的 /workspace 路径。"""
    ws = os.path.join(_PROJECT_ROOT, "workspace")
    os.makedirs(ws, exist_ok=True)
    abs_path = os.path.join(ws, name)
    with open(abs_path, "wb") as f:
        f.write(data)
    try:
        yield f"/workspace/{name}"
    finally:
        os.path.exists(abs_path) and os.remove(abs_path)


async def _make_provider(bot_api, memory):
    mcp = create_mcp_server(outputter=_FakeOutputter(), bot_api=bot_api, memory=memory)
    conn = McpConnection(mcp)
    session = await conn.start()
    return McpToolProvider(session), conn


def _decode_base64_file(file_arg: str) -> bytes:
    assert file_arg.startswith("base64://"), f"应为 base64:// 前缀: {file_arg[:20]}"
    return base64.b64decode(file_arg[len("base64://"):])


async def _t_send_group_record():
    audio = b"group-voice-bytes-\x00\x01\x02"
    bot_api = _FakeBotApi()
    memory = ShortTermMemory(api=bot_api)
    provider, conn = await _make_provider(bot_api, memory)
    try:
        with _workspace_file(".test_group_record.wav", audio) as path:
            ret = await provider.call(
                "send_group_record", {"group_id": 123, "path": path}
            )
        assert "已发送语音到群 123" in ret, ret
        assert len(bot_api.calls) == 1, bot_api.calls
        kind, gid, file_arg = bot_api.calls[0]
        assert kind == "group" and gid == "123", (kind, gid)
        assert _decode_base64_file(file_arg) == audio, "base64 解码后应还原原始音频"
        recent = memory.get_recent("group:123")
        assert recent and recent[-1].content == "[语音]", "应写入 [语音] 记忆"
    finally:
        await conn.aclose()
    print("[PASS] test_send_group_record")


async def _t_send_private_record():
    audio = b"private-voice-bytes-\xff\xfe"
    bot_api = _FakeBotApi()
    memory = ShortTermMemory(api=bot_api)
    provider, conn = await _make_provider(bot_api, memory)
    try:
        with _workspace_file(".test_private_record.wav", audio) as path:
            ret = await provider.call(
                "send_private_record", {"user_id": 456, "path": path}
            )
        assert "已发送语音给用户 456" in ret, ret
        assert len(bot_api.calls) == 1, bot_api.calls
        kind, uid, file_arg = bot_api.calls[0]
        assert kind == "private" and uid == "456", (kind, uid)
        assert _decode_base64_file(file_arg) == audio
        recent = memory.get_recent("private:456")
        assert recent and recent[-1].content == "[语音]"
    finally:
        await conn.aclose()
    print("[PASS] test_send_private_record")


async def _t_send_record_missing_file():
    bot_api = _FakeBotApi()
    memory = ShortTermMemory(api=bot_api)
    provider, conn = await _make_provider(bot_api, memory)
    try:
        ret = await provider.call(
            "send_group_record",
            {"group_id": 1, "path": "/workspace/__not_exist__.wav"},
        )
        data = json.loads(ret)
        assert data["ok"] is False, ret
        assert "语音文件不存在" in data["error"], ret
        assert bot_api.calls == [], "文件不存在时不应调用 bot_api"
    finally:
        await conn.aclose()
    print("[PASS] test_send_record_missing_file")


def test_mcp_record_tools():
    async def run():
        await _t_send_group_record()
        await _t_send_private_record()
        await _t_send_record_missing_file()

    asyncio.run(run())


# ─── 运行入口 ──────────────────────────────────────

def run_all() -> None:
    test_tts_synthesize_ok()
    test_tts_custom_weights()
    test_tts_empty_text_exits()
    test_tts_bad_weights_exits()
    test_tts_http_error_exits()
    test_tts_connection_error_exits()
    test_mcp_record_tools()
    print("\nALL RECORD TESTS PASSED")


if __name__ == "__main__":
    run_all()

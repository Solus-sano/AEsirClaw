"""离线表情库与 MCP 集成测试：.venv/bin/python test/test_stickers.py。"""
import asyncio
import io
import json
import sys
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from aiohttp import web
from agent_core.stickers import StickerStore
from agent_core.memory.short_term import ShortTermMemory
from agent_core.tools.mcp_tools import create_mcp_server
from agent_core.tools.provider import McpConnection, McpToolProvider


def picture(number):
    out = io.BytesIO()
    Image.new("RGB", (2, 2), (number, 0, 0)).save(out, format="PNG")
    return out.getvalue()


def concurrent_save(workspace, number):
    return StickerStore(Path(workspace)).save(picture(number), "same.png", str(number))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = StickerStore(Path(self.tmp.name))

    def test_lru_duplicate_collision_and_failure(self):
        for i in range(20):
            self.store.save(picture(i), f"{i}.png", str(i))
        self.store.mark_sent(self.store.snapshot("0.png")[0])
        result = self.store.save(picture(1), "other.png", "new caption")
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["sticker"]["id"], "1.png")
        self.assertEqual(result["sticker"]["caption"], "1")
        self.assertEqual(result["sticker"]["use_count"], 0)
        before = self.store.index.read_bytes()
        with patch.object(self.store, "_commit", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.store.save(picture(21), "new.png", "new")
        self.assertEqual(before, self.store.index.read_bytes())
        self.assertFalse((self.store.images / "new.png").exists())
        result = self.store.save(picture(22), "0.png", "new")
        self.assertEqual(result["sticker"]["id"], "0_2.png")
        self.assertEqual(result["evicted_id"], "2.png")
        self.assertFalse((self.store.images / "2.png").exists())
        self.assertEqual(len(self.store.list()), 20)
        before = self.store.index.read_bytes()
        self.store.list()
        self.store.snapshot("0.png")
        self.assertEqual(before, self.store.index.read_bytes())
        with self.assertRaises(ValueError):
            self.store.save(b"not image", "bad.png", "bad")
        self.assertEqual(before, self.store.index.read_bytes())

    def test_lru_ignores_lifetime_frequency(self):
        self.store.save(picture(0), "popular.png", "old popular sticker")
        for _ in range(10):
            self.store.mark_sent(self.store.snapshot("popular.png")[0])
        for i in range(1, 20):
            self.store.save(picture(i), f"{i}.png", str(i))
        result = self.store.save(picture(20), "new.png", "new")
        self.assertEqual(result["evicted_id"], "popular.png")
        self.assertEqual(self.store.list()[0]["id"], "new.png")

    def test_animation_and_corrupt_index(self):
        out = io.BytesIO()
        a, b = Image.new("RGB", (2, 2), "red"), Image.new("RGB", (2, 2), "blue")
        a.save(out, format="GIF", save_all=True, append_images=[b], duration=100, loop=0)
        self.store.save(out.getvalue(), "../cat.gif", "cat")
        self.assertEqual(self.store.snapshot("cat.gif")[1], out.getvalue())
        with Image.open(self.store.images / "cat.gif") as image:
            self.assertEqual(image.n_frames, 2)
        self.store.index.write_text('{"stickers": "bad"}')
        with self.assertRaisesRegex(ValueError, "索引损坏"):
            self.store.save(picture(0), "x.png", "x")
        self.assertEqual(self.store.index.read_text(), '{"stickers": "bad"}')

    def test_multiprocess_and_stale_send(self):
        with ProcessPoolExecutor(max_workers=4) as pool:
            list(pool.map(concurrent_save, [self.tmp.name] * 24, range(24)))
        rows = self.store.list()
        self.assertEqual(len(rows), 20)
        self.assertEqual(len({r["id"] for r in rows}), 20)
        self.assertEqual(len(list(self.store.images.iterdir())), 20)
        old = self.store.snapshot(rows[-1]["id"])[0]
        self.store.save(picture(100), "new.png", "new")
        self.assertFalse(self.store.mark_sent(old))
        self.store.save(picture(101), old["id"], "replacement")
        self.assertFalse(self.store.mark_sent(old))


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "cat.png").write_bytes(picture(1))
            api = SimpleNamespace(send_group_msg=AsyncMock(return_value="123"),
                                  send_private_msg=AsyncMock(return_value="456"))
            memory = ShortTermMemory(api)
            with patch("agent_core.tools.mcp_tools._PROJECT_DIR", root):
                server = create_mcp_server(outputter=SimpleNamespace(), bot_api=api,
                                           memory=memory, context_id="group:123")
            conn = McpConnection(server)
            provider = McpToolProvider(await conn.start())
            try:
                saved = json.loads(await provider.call("save_sticker", {
                    "source": "/workspace/cat.png", "caption": "猫猫无语"}))
                self.assertTrue(saved["ok"])
                rows = json.loads(await provider.call("list_stickers", {}))
                self.assertEqual(rows[0]["id"], "cat.png")
                self.assertEqual(rows[0]["path"], "/workspace/stickers/images/cat.png")
                for target, send, key in [("group:123", api.send_group_msg, "group_id"),
                                           ("private:456", api.send_private_msg, "user_id")]:
                    await provider.call("send_media", {"kind": "sticker", "source": "cat.png",
                                                       "target": target, "caption": "hi"})
                    args = send.await_args.kwargs
                    self.assertEqual(args[key], target.split(":")[1])
                    self.assertEqual(args["message"][1]["data"]["sub_type"], 1)
                    self.assertTrue(args["message"][1]["data"]["file"].startswith("base64://"))
                    self.assertIn("猫猫无语", memory.get_recent(target)[-1].content)
                self.assertEqual(StickerStore(workspace).list()[0]["use_count"], 2)
                api.send_group_msg.return_value = ""
                result = json.loads(await provider.call("send_media", {"kind": "sticker", "source": "cat.png"}))
                self.assertFalse(result["ok"])
                api.send_group_msg.side_effect = RuntimeError("send failed")
                await provider.call("send_media", {"kind": "sticker", "source": "cat.png"})
                self.assertEqual(StickerStore(workspace).list()[0]["use_count"], 2)
                result = json.loads(await provider.call("save_sticker", {"source": "../outside", "caption": "x"}))
                self.assertFalse(result["ok"])
                result = json.loads(await provider.call("send_media", {"kind": "sticker", "source": "missing.png"}))
                self.assertFalse(result["ok"])
            finally:
                await conn.aclose()

    async def test_url_source(self):
        async def serve(request):
            return web.Response(body=picture(5), content_type="image/png")
        app = web.Application()
        app.router.add_get('/{name}', serve)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            with tempfile.TemporaryDirectory() as directory:
                store = StickerStore(Path(directory))
                data, name = await store.read_source(f'http://127.0.0.1:{port}/cat.png?token=x', None)
                self.assertEqual(name, 'cat.png')
                self.assertEqual(data, picture(5))
                with self.assertRaises(ValueError):
                    await store.read_source(f'http://127.0.0.1:{port}/download', None)
                _, name = await store.read_source(f'http://127.0.0.1:{port}/download', 'cat.png')
                self.assertEqual(name, 'cat.png')
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    unittest.main()

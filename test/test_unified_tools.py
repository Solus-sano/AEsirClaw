"""统一 MCP 工具的离线回归测试：python test/test_unified_tools.py。"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_core.memory.short_term import ShortTermMemory
from agent_core.subagent import DEFAULT_SUBAGENT_TOOLS
from agent_core.tools.mcp_tools import create_mcp_server
from agent_core.tools.provider import McpConnection, McpToolProvider, FilteredToolProvider


async def run_checks(root):
    names = ["send_group_forward_msg", "send_private_forward_msg", "post_group_msg",
             "post_private_msg", "post_group_file", "post_private_file",
             "get_group_msg_history", "get_friend_msg_history"]
    api = SimpleNamespace(**{name: AsyncMock() for name in names})
    output = SimpleNamespace(send_group=AsyncMock(), send_private=AsyncMock())
    memory = ShortTermMemory(api=api)
    server = create_mcp_server(outputter=output, bot_api=api, memory=memory,
                              context_id="group:123", bot_qq="999", bot_name="测试机器人",
                              subagent_runner=SimpleNamespace(run=AsyncMock()))
    conn = McpConnection(server)
    provider = McpToolProvider(await conn.start())
    try:
        schemas = await provider.list_schemas()
        assert {s.name for s in schemas} == {
            "send_message", "send_media", "get_history", "execute_task", "get_skill", "read_image",
            "add_scheduled_task", "list_scheduled_tasks", "remove_scheduled_task", "dispatch_subagent",
        }
        filtered = FilteredToolProvider(provider, include=DEFAULT_SUBAGENT_TOOLS)
        assert {s.name for s in await filtered.list_schemas()} == {"execute_task", "get_history", "get_skill", "read_image"}
        assert "error" in json.loads(await filtered.call("send_message", {"messages": ["x"]}))

        await provider.call("send_message", {"messages": ["hello", "world"]})
        output.send_group.assert_awaited_once_with("123", ["hello", "world"])
        assert [m.content for m in memory.get_recent("group:123")][-2:] == ["hello", "world"]
        await provider.call("send_message", {"messages": ["hi"], "target": "private:456"})
        output.send_private.assert_awaited_once_with("456", ["hi"])
        for target, method, key in [("group:123", api.send_group_forward_msg, "group_id"),
                                    ("private:456", api.send_private_forward_msg, "user_id")]:
            await provider.call("send_message", {"messages": ["node1", "node2"], "forward": True, "target": target})
            kwargs = method.await_args.kwargs
            assert kwargs[key] == target.split(":")[1]
            assert kwargs["messages"][0]["data"]["uin"] == "999"
            assert kwargs["messages"][0]["data"]["name"] == "测试机器人"
            assert "node1\nnode2" in memory.get_recent(target)[-1].content
        for args in [{"messages": [" "]}, {"messages": ["x"], "target": "group:0"},
                     {"messages": ["x"], "target": ""}]:
            assert json.loads(await provider.call("send_message", args))["ok"] is False
        assert output.send_group.await_count == 1

        history = [SimpleNamespace(sender={"nickname": "A"}, raw_message=str(i), time=i) for i in range(10)]
        api.get_group_msg_history.return_value = history
        api.get_friend_msg_history.return_value = history
        for target in ["group:123", "private:456"]:
            result = json.loads(await provider.call("get_history", {"target": target, "limit": 3, "offset": 2}))
            assert result == {"ok": True, "path": "/workspace/tmp_history.jsonl", "count": 3, "target": target}
            rows = [json.loads(line) for line in (root / "workspace/tmp_history.jsonl").read_text().splitlines()]
            assert [m["content"] for m in rows] == ["5", "6", "7"]
            result = json.loads(await provider.call("get_history", {"target": target, "limit": 3, "offset": 20}))
            assert result["count"] == 0
            assert (root / "workspace/tmp_history.jsonl").read_bytes() == b""
        result = json.loads(await provider.call("get_history", {"limit": 2}))
        assert result["count"] == 2 and result["target"] == "group:123"
        rows = [json.loads(line) for line in (root / "workspace/tmp_history.jsonl").read_text().splitlines()]
        assert [m["content"] for m in rows] == ["8", "9"]

        history[-1].raw_message = "你好\n第二行"
        for output_path in ["stats/history.jsonl", "/workspace/stats/history.jsonl"]:
            result = json.loads(await provider.call("get_history", {"limit": 1, "output_path": output_path}))
            assert result["path"] == "/workspace/stats/history.jsonl" and result["count"] == 1
            lines = (root / "workspace/stats/history.jsonl").read_text().splitlines()
            assert len(lines) == 1
            assert json.loads(lines[0]) == {"sender": "A", "content": "你好\n第二行", "time": 9}
            assert "你好" not in json.dumps(result, ensure_ascii=False)
        (root / "outside.jsonl").write_text("preserve")
        (root / "workspace/link.jsonl").symlink_to(root / "outside.jsonl")
        before_calls = api.get_group_msg_history.await_count
        for output_path in ["../outside.jsonl", "/workspace/../outside.jsonl", "/workspace/link.jsonl", "/workspace/stats", ""]:
            result = json.loads(await provider.call("get_history", {"output_path": output_path}))
            assert result["ok"] is False
        assert api.get_group_msg_history.await_count == before_calls
        assert (root / "outside.jsonl").read_text() == "preserve"
        original = (root / "workspace/tmp_history.jsonl").read_bytes()
        with patch("agent_core.tools.mcp_tools.os.replace", side_effect=OSError("test write failure")):
            result = json.loads(await provider.call("get_history", {}))
            assert result["ok"] is False
        assert (root / "workspace/tmp_history.jsonl").read_bytes() == original
        assert not list((root / "workspace").glob(".history-*.tmp"))
        for args in [{"limit": 0}, {"offset": -1}]:
            assert json.loads(await provider.call("get_history", args))["ok"] is False

        for target, method in [("group:123", api.post_group_msg), ("private:456", api.post_private_msg)]:
            for kind in ["image", "video"]:
                await provider.call("send_media", {"kind": kind, "source": "https://example.com/media", "target": target, "caption": "说明"})
                assert method.await_args.kwargs[kind] == "https://example.com/media"
                assert method.await_args.kwargs["text"] == "说明"
        await provider.call("send_media", {"kind": "file", "source": "https://example.com/file", "caption": "附件"})
        api.post_group_file.assert_awaited_once()
        output.send_group.assert_awaited_with("123", ["附件"])
        for source in ["/workspace/../config.yaml", "/workspace/missing.file"]:
            assert json.loads(await provider.call("send_media", {"kind": "file", "source": source}))["ok"] is False
        assert "error" in json.loads(await provider.call("send_media", {"kind": "bad", "source": "x"}))
        assert json.loads(await provider.call("send_media", {"kind": "record", "source": "https://example.com/audio"}))["ok"] is False
    finally:
        await conn.aclose()
    print("ALL UNIFIED TOOL TESTS PASSED")


async def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with patch("agent_core.tools.mcp_tools._PROJECT_DIR", root):
            await run_checks(root)


if __name__ == "__main__":
    asyncio.run(main())

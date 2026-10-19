"""图片工具到模型请求的离线回归：python test/test_read_image.py。"""
import asyncio
import base64
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from agent_core.backends.base import AgentRequest, ImagePart, TextPart
from agent_core.backends.native_loop import NativeLoopBackend
from agent_core.llm import LLMClient, LLMResponse, ToolCall, ToolCallFunction
from agent_core.tools.mcp_tools import create_mcp_server
from agent_core.tools.provider import McpConnection, McpToolProvider
from agent_core.tracing.wrappers import TracingToolProvider


async def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        workspace = root / 'workspace'
        workspace.mkdir()
        image_path = workspace / 'test.dat'
        Image.new('RGB', (32, 24), 'red').save(image_path, format='JPEG')
        (workspace / 'bad.png').write_text('not an image')
        (root / 'outside.png').write_bytes(image_path.read_bytes())
        (workspace / 'link.png').symlink_to(root / 'outside.png')
        with patch('agent_core.tools.mcp_tools._PROJECT_DIR', root):
            server = create_mcp_server(outputter=None, bot_api=None, memory=None)
            conn = McpConnection(server)
            provider = McpToolProvider(await conn.start())
            try:
                for path in ['/workspace/test.dat', 'test.dat', str(image_path)]:
                    result = await provider.call('read_image', {'path': path})
                    assert isinstance(result, list) and isinstance(result[0], ImagePart)
                    assert result[0].url.startswith('data:image/png;base64,')
                    with Image.open(io.BytesIO(base64.b64decode(result[0].url.split(',', 1)[1]))) as image:
                        assert image.size == (32, 24) and image.format == 'PNG'
                        assert image.getpixel((0, 0))[0] > 250
                for path in ['/workspace/missing.png', '/workspace/bad.png', '/workspace/../outside.png', '/workspace/link.png']:
                    result = await provider.call('read_image', {'path': path})
                    assert isinstance(result, str) and 'error' in json.loads(result), result

                class FakeLLM:
                    calls = 0
                    async def chat(self, messages, **kwargs):
                        self.calls += 1
                        if self.calls == 1:
                            return LLMResponse(content='', tool_calls=[
                                ToolCall('img1', ToolCallFunction('read_image', json.dumps({'path': '/workspace/test.dat'}))),
                                ToolCall('text1', ToolCallFunction('get_history', '{}')),
                                ToolCall('img2', ToolCallFunction('read_image', json.dumps({'path': 'test.dat'}))),
                            ])
                        payload = LLMClient._build_payload(messages)
                        assert [m['role'] for m in payload] == ['system', 'user', 'assistant', 'tool', 'tool', 'tool', 'user']
                        assert [m['tool_call_id'] for m in payload[3:6]] == ['img1', 'text1', 'img2']
                        assert all('base64' not in m['content'] for m in payload[3:6])
                        pictures = [p for p in payload[-1]['content'] if p['type'] == 'image_url']
                        assert len(pictures) == 2
                        assert all(p['image_url']['url'].startswith('data:image/png;base64,') for p in pictures)
                        return LLMResponse(content='看到了图片')

                with patch('agent_core.tracing.wrappers.tracer.record_tool_span') as trace:
                    llm = FakeLLM()
                    result = await NativeLoopBackend(llm).run(AgentRequest(
                        system_prompt='test', user_content=[TextPart('查看图片')],
                        tools=TracingToolProvider(provider), max_iterations=2,
                    ))
                    assert result.final_text == '看到了图片' and llm.calls == 2
                    assert trace.call_count == 3
                    assert all('base64' not in c.kwargs['result'] for c in trace.call_args_list)
            finally:
                await conn.aclose()
    print('ALL IMAGE TOOL TESTS PASSED')


if __name__ == '__main__':
    asyncio.run(main())

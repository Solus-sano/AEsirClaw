from __future__ import annotations

import asyncio
import base64
import io
import re
from typing import List

import aiohttp
from PIL import Image

from agent_core.llm import ChatMessage
from ncatbot.utils import get_log

LOG = get_log(__name__)

# ---------------------------------------------------------------------------
# 多模态图片处理
# ---------------------------------------------------------------------------

_IMG_PATTERN = re.compile(r'\[IMG:(https?://[^\]]+)\]')


async def _download_and_resize(url: str, max_side: int = 224) -> str | None:
    """下载图片 → resize（长边不超过 max_side） → 返回 data URL，失败返回 None。"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    LOG.warning("图片下载失败: HTTP %s -> %s", resp.status, url)
                    return None
                data = await resp.read()

        img = Image.open(io.BytesIO(data))

        # 动图（GIF / APNG）：取中间帧
        if getattr(img, "is_animated", False):
            mid = img.n_frames // 2
            img.seek(mid)

        # 统一转 RGB（GIF 的 P 模式、RGBA 等均需转换）
        img = img.convert("RGB")

        w, h = img.size
        if max(w, h) > max_side:
            ratio = max_side / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

        img.save(f"test.png")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/png;base64,{b64}"
    except Exception as e:
        LOG.warning("图片处理失败: %s -> %s", url, e)
        return None


async def inject_multimodal(messages: List[ChatMessage]) -> List[ChatMessage]:
    """扫描消息列表，将含 [IMG:url] 标记的消息转换为多模态格式。

    按原始位置交替插入 text / image_url，保留图文顺序；
    下载失败的图片降级为文本 [图片]；
    若全部图片均失败，整条消息退化为纯文本字符串。
    """
    result: List[ChatMessage] = []
    for msg in messages:
        # 跳过非文本 / 不含图片标记的消息
        if not isinstance(msg.content, str) or not _IMG_PATTERN.search(msg.content):
            result.append(msg)
            continue

        # re.split 带捕获组 → 交替产生 [文本, url, 文本, url, 文本]
        parts = _IMG_PATTERN.split(msg.content)
        # parts[0], parts[2], ... 是文本片段
        # parts[1], parts[3], ... 是图片 URL

        # 收集所有 URL，并发下载
        urls = parts[1::2]
        data_urls = await asyncio.gather(*[_download_and_resize(u) for u in urls])

        # 按原始顺序交替构建 content_parts
        content_parts: List[dict] = []
        img_idx = 0
        for i, part in enumerate(parts):
            if i % 2 == 0:
                # 文本片段（可能为空字符串，跳过）
                if part:
                    content_parts.append({"type": "text", "text": part})
            else:
                # 图片片段
                content_parts.append(
                    {"type": "text", "text": f"{part}"}
                )
                if data_urls[img_idx] is not None:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": data_urls[img_idx]},
                    })
                else:
                    content_parts.append({"type": "text", "text": "[图片]"})
                img_idx += 1

        # 全部图片都失败 → 退化为纯文本
        has_image = any(p["type"] == "image_url" for p in content_parts)
        if not has_image:
            clean_text = _IMG_PATTERN.sub("[图片]", msg.content)
            result.append(ChatMessage(role=msg.role, content=clean_text))
        else:
            result.append(ChatMessage(role=msg.role, content=content_parts))

    return result

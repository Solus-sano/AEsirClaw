from __future__ import annotations

import asyncio
import base64
import io
import re
from typing import List

import aiohttp
from PIL import Image

from agent_core.backends.base import ContentPart, ImagePart, TextPart
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

        # img.save(f"test.png")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/png;base64,{b64}"
    except Exception as e:
        LOG.warning("图片处理失败: %s -> %s", url, e)
        return None


async def build_multimodal_content(text: str) -> List[ContentPart]:
    """把一段含 [IMG:url] 标记的文本构造为中立的多模态 ContentPart 列表。

    按原始位置交替产出 TextPart / ImagePart，保留图文顺序；
    下载失败的图片降级为文本 [图片]；
    若不含图片或全部图片均失败，退化为单个 TextPart。

    返回中立结构，由各 backend 自行渲染为其 wire 格式（OpenAI / SDK 等）。
    """
    if not _IMG_PATTERN.search(text):
        return [TextPart(text=text)]

    # re.split 带捕获组 → 交替产生 [文本, url, 文本, url, 文本]
    parts = _IMG_PATTERN.split(text)
    urls = parts[1::2]
    data_urls = await asyncio.gather(*[_download_and_resize(u) for u in urls])

    content_parts: List[ContentPart] = []
    img_idx = 0
    for i, part in enumerate(parts):
        if i % 2 == 0:
            if part:
                content_parts.append(TextPart(text=part))
        else:
            # 保留原始 URL 文本片段，再附图片
            content_parts.append(TextPart(text=f"{part}"))
            if data_urls[img_idx] is not None:
                content_parts.append(ImagePart(url=data_urls[img_idx]))
            else:
                content_parts.append(TextPart(text="[图片]"))
            img_idx += 1

    has_image = any(isinstance(p, ImagePart) for p in content_parts)
    if not has_image:
        return [TextPart(text=_IMG_PATTERN.sub("[图片]", text))]
    return content_parts

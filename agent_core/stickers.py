"""共享表情库：原文件保存、SHA-256 去重、跨进程锁和 LRU 淘汰。"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import io
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

import aiohttp
from PIL import Image

MAX_BYTES = 20 * 1024 * 1024


class StickerStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.root = self.workspace / "stickers"
        self.images = self.root / "images"
        self.index = self.root / "index.json"

    @contextmanager
    def _locked(self):
        self.images.mkdir(parents=True, exist_ok=True)
        with (self.root / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _load(self) -> list[dict]:
        if not self.index.exists():
            return []

        def require(condition):
            if not condition:
                raise ValueError("invalid sticker index")

        try:
            document = json.loads(self.index.read_text(encoding="utf-8"))
            rows = document["stickers"]
            require(document["version"] == 1 and isinstance(rows, list) and len(rows) <= 20)
            ids, hashes = set(), set()
            for row in rows:
                require(isinstance(row["id"], str) and self._filename(row["id"]) == row["id"])
                require(row["id"] not in ids and row["sha256"] not in hashes)
                require(re.fullmatch(r"[0-9a-f]{64}", row["sha256"]))
                require(row["path"] == f"images/{row['id']}")
                require(isinstance(row["caption"], str))
                require(isinstance(row["use_count"], int) and row["use_count"] >= 0)
                for key in ("created_at", "last_used_at"):
                    require(isinstance(row[key], int) and row[key] > 0)
                ids.add(row["id"])
                hashes.add(row["sha256"])
            return rows
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("表情索引损坏，请修复 index.json 后重试") from exc

    @staticmethod
    def _atomic_write(path: Path, content: bytes):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, prefix=".sticker-") as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _commit(self, rows):
        self._atomic_write(self.index, json.dumps(
            {"version": 1, "stickers": rows}, ensure_ascii=False, indent=2,
        ).encode("utf-8"))

    @staticmethod
    def _filename(value: str) -> str:
        name = value.replace("\\", "/").rsplit("/", 1)[-1]
        name = re.sub(r'[\x00-\x1f\x7f<>:"|?*]', "_", name).strip().strip(".")
        if not name or len(name.encode("utf-8")) > 220:
            raise ValueError("请提供有效的 filename（不超过 220 字节）")
        return name

    async def read_source(self, source: str, filename: str | None) -> tuple[bytes, str]:
        if source.startswith(("http://", "https://")):
            name = self._filename(filename if filename is not None else unquote(urlsplit(source).path).rsplit("/", 1)[-1])
            if filename is None and not Path(name).suffix:
                raise ValueError("URL 未提供有效原文件名，请指定 filename")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get(source) as response:
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            raise ValueError("表情文件超过 20 MiB")
            return bytes(data), name
        path = Path(source)
        if source.startswith("/workspace/"):
            path = self.workspace / source.removeprefix("/workspace/")
        elif not path.is_absolute():
            path = self.workspace / path
        path = path.resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError("表情源文件须位于 /workspace/ 下")
        name = self._filename(filename if filename is not None else path.name)
        # 有界读取；由调用者在线程中执行本地 I/O。
        def read():
            with path.open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("表情文件超过 20 MiB")
            return data
        return await asyncio.to_thread(read), name

    def save(self, data: bytes, filename: str, caption: str) -> dict:
        name = self._filename(filename)
        caption = caption.strip()
        if not caption or len(caption) > 200:
            raise ValueError("caption 须为 1–200 字的简短描述")
        if not data or len(data) > MAX_BYTES:
            raise ValueError("表情文件为空或超过 20 MiB")
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 25_000_000:
                    raise ValueError("表情超过 2500 万像素")
                image.verify()
        except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
            raise ValueError("文件不是有效图片或图片尺寸过大") from exc
        digest = hashlib.sha256(data).hexdigest()
        with self._locked():
            rows = self._load()
            now = max(time.time_ns(), max((r["last_used_at"] for r in rows), default=0) + 1)
            for row in rows:
                if row["sha256"] == digest:
                    # 再次主动收藏刷新 LRU，但不覆盖描述或增加发送次数。
                    self._atomic_write(self.images / row["id"], data)
                    row["last_used_at"] = now
                    self._commit(rows)
                    return {"ok": True, "duplicate": True, "sticker": row, "evicted_id": None}
            base = Path(name)
            used = {r["id"] for r in rows}
            suffix = 2
            while name in used or (self.images / name).exists():
                name = f"{base.stem}_{suffix}{base.suffix}"
                suffix += 1
            victim = min(rows, key=self._lru_key) if len(rows) == 20 else None
            row = dict(id=name, path=f"images/{name}", sha256=digest, caption=caption,
                       use_count=0, created_at=now, last_used_at=now)
            self._atomic_write(self.images / name, data)
            try:
                self._commit([r for r in rows if r is not victim] + [row])
            except Exception:
                (self.images / name).unlink(missing_ok=True)
                raise
            if victim:
                # 索引已提交；清理失败留作孤立文件，不应把成功保存报告为失败。
                try:
                    (self.images / victim["id"]).unlink(missing_ok=True)
                except OSError:
                    pass
            return {"ok": True, "duplicate": False, "sticker": row,
                    "evicted_id": victim["id"] if victim else None}

    @staticmethod
    def _lru_key(row):
        return row["last_used_at"], row["created_at"], row["id"]

    def list(self) -> list[dict]:
        with self._locked():
            return sorted(self._load(), key=self._lru_key, reverse=True)

    def snapshot(self, sticker_id: str) -> tuple[dict, bytes]:
        with self._locked():
            for row in self._load():
                if row["id"] == sticker_id:
                    data = (self.images / row["id"]).read_bytes()
                    if hashlib.sha256(data).hexdigest() != row["sha256"]:
                        raise ValueError("表情文件内容已变更，请重新保存")
                    return row, data
        raise ValueError(f"表情不存在或已淘汰: {sticker_id}；请调用 list_stickers")

    def mark_sent(self, snapshot: dict) -> bool:
        with self._locked():
            rows = self._load()
            for row in rows:
                # 发送期间可能淘汰并重新使用同名文件，不能记到另一条记录上。
                if all(row[k] == snapshot[k] for k in ("id", "sha256", "created_at")):
                    row["use_count"] += 1
                    row["last_used_at"] = max(time.time_ns(), max(r["last_used_at"] for r in rows) + 1)
                    self._commit(rows)
                    return True
            return False

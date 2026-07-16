#!/usr/bin/env python3
"""调用局域网 IndexTTS2 服务合成语音，保存为 wav 文件。

仅依赖标准库，可直接在沙箱中运行。TTS 服务地址写死为局域网内网地址。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

TTS_URL = "http://192.168.1.10:13172"

WEIGHT_ORDER = [
    "happy",
    "angry",
    "sad",
    "afraid",
    "disgusted",
    "melancholic",
    "surprised",
    "calm",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="IndexTTS2 语音合成（合成后保存为 wav，再用 send_*_record 工具发送）"
    )
    parser.add_argument("text", help="待合成文本，不能为空")
    parser.add_argument(
        "--weights",
        "-w",
        type=float,
        nargs=8,
        metavar="W",
        default=[0, 0, 0, 0, 0, 0, 0, 1],
        help="8 维情绪权重，顺序: %s，每项 [0,1]，默认 calm=1" % " ".join(WEIGHT_ORDER),
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="输出 wav 路径，默认 /workspace/tts_<时间戳>.wav",
    )
    parser.add_argument("--timeout", type=int, default=120, help="请求超时秒数，默认 120")
    args = parser.parse_args()

    text = args.text.strip()
    if not text:
        print("错误: text 不能为空", file=sys.stderr)
        sys.exit(1)
    if any(w < 0 or w > 1 for w in args.weights):
        print("错误: weights 每项需在 [0,1] 之间", file=sys.stderr)
        sys.exit(1)

    output = args.output or f"/workspace/tts_{int(time.time())}.wav"
    out_dir = os.path.dirname(output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    payload = json.dumps({"text": text, "weights": args.weights}).encode("utf-8")
    req = urllib.request.Request(
        f"{TTS_URL}/tts",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        print(f"错误: TTS 服务返回 {exc.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"错误: 无法连接 TTS 服务 {TTS_URL}: {exc.reason}", file=sys.stderr)
        sys.exit(1)

    with open(output, "wb") as f:
        f.write(audio)

    print(
        json.dumps(
            {"ok": True, "output": output, "bytes": len(audio)}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()

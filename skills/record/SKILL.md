---
name: "record"
description: "把文本合成为语音并发送到 QQ 群/私聊（调用局域网 IndexTTS2 服务）"
---

# Skill Description
当需要用语音（而非文字）说话时使用此技能。流程分两步：先在沙箱中调用 TTS 服务把文本合成为 wav，存到 `/workspace`；再用 `send_group_record` / `send_private_record` 工具把该文件发送出去。

TTS 服务部署在局域网另一台机器（`192.168.1.10:13172`），沙箱以 host 网络运行，可直接访问。

## Constraints
- 只能合成中文/常规文本，`text` 不能为空。
- 合成较慢（首字到出声可能数秒到数十秒），请设置足够的超时。
- 输出为 `.wav`，NapCat 一般会自动转码；若发送失败，可用 ffmpeg 转成 amr 再发。
- 情绪权重为 8 维数组，每项 `[0,1]`，顺序固定：`[happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]`。服务按最高权重对应的情绪目录选取参考音色。

## Usage

### 1. 合成语音
默认平静情绪（calm=1），输出到 `/workspace/tts_<时间戳>.wav`：
```bash
python /skills/record/src/tts.py "你好呀，今天过得怎么样？"
```

指定输出路径：
```bash
python /skills/record/src/tts.py "你好呀" --output /workspace/hello.wav
```

指定情绪权重（例：开心为主，略带惊讶）：
```bash
python /skills/record/src/tts.py "太好啦！" --weights 0.8 0 0 0 0 0 0.2 0
```

脚本成功时在 stdout 打印 JSON，如 `{"ok": true, "output": "/workspace/hello.wav", "bytes": 123456}`，其中 `output` 即为下一步要发送的文件路径。

### 2. 发送语音
拿到 `output` 路径后，调用对应工具（这两个是 MCP 工具，不是命令行）：
- 群聊：`send_group_record(group_id, path="/workspace/hello.wav")`
- 私聊：`send_private_record(user_id, path="/workspace/hello.wav")`

### 健康检查（可选，排查连不通时用）
```bash
curl http://192.168.1.10:13172/health
```

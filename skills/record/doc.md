# IndexTTS2 API Server 使用说明

`api_server.py` 是一个基于 FastAPI 的独立 HTTP 服务，用于把 IndexTTS2 以 API 形式暴露给局域网或集群中的其他机器。

## 功能

- `POST /tts`：传入待合成文本与 8 维情绪权重，返回 `audio/wav` 音频。
- `GET /health`：查看模型加载状态与各情绪目录可用参考音频数量。

## 启动服务

在项目根目录执行：

```bash
uv run api_server.py \
  --model-dir /home/aesir/aesir/models/IndexTTS-2 \
  --dataset-dir /home/aesir/aesir/datasets/firefly_audio_example \
  --host 0.0.0.0 --port 13172 --fp16
```

如需禁用 FP16（例如无 GPU 时）：

```bash
uv run api_server.py \
  --model-dir /home/aesir/aesir/models/IndexTTS-2 \
  --dataset-dir /home/aesir/aesir/datasets/firefly_audio_example \
  --host 0.0.0.0 --port 13172 --no-fp16
```

建议使用 `tmux` 或 `screen` 常驻运行。

## 参数说明

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--host` | `0.0.0.0` | 监听地址 |
| `--port` | `13172` | 监听端口 |
| `--model-dir` | `/home/aesir/aesir/models/IndexTTS-2` | IndexTTS2 模型目录 |
| `--dataset-dir` | `/home/aesir/aesir/datasets/firefly_audio_example` | 情绪参考音频根目录，需包含 `happy/angry/sad/afraid/disgusted/melancholic/surprised/calm` 子目录 |
| `--fp16` / `--no-fp16` | `--fp16` | 是否使用 FP16 推理 |
| `--deepspeed` | 关闭 | 使用 DeepSpeed 加速 |
| `--cuda-kernel` | 关闭 | 使用 BigVGAN CUDA kernel |
| `--accel` | 关闭 | 使用 GPT2 flash-attn 加速 |
| `--torch-compile` | 关闭 | 使用 torch.compile 优化 s2mel |
| `--workers` | `1` | uvicorn worker 数量，建议保持 1 |

## API 接口

### 健康检查

```bash
curl http://localhost:13172/health
```

返回示例：

```json
{
  "status": "ok",
  "model_loaded": true,
  "dataset_dir": "/home/aesir/aesir/datasets/firefly_audio_example",
  "emotion_counts": {
    "happy": 6,
    "angry": 0,
    "sad": 0,
    "afraid": 3,
    "disgusted": 0,
    "melancholic": 7,
    "surprised": 0,
    "calm": 12
  }
}
```

### 合成语音

```bash
curl -X POST http://localhost:13172/tts \
  -H "Content-Type: application/json" \
  -d '{
    "text": "大家好，这是 IndexTTS2 的 API 测试。",
    "weights": [0.8, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.2]
  }' \
  -o output.wav
```

`weights` 顺序固定为：

```
[happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]
```

服务会根据最高权重去对应情绪目录下随机选取一个 `.wav` 作为音色参考音频；如果该目录为空，则自动回退到 `calm`。传入的 `weights` 同时会作为情绪向量传给模型。

## 请求/响应

### 请求体

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `text` | `string` | 是 | 待合成文本，不能为空 |
| `weights` | `array[float]` | 是 | 8 维情绪权重，每项在 `[0, 1]` 之间 |

### 响应

成功时返回 `audio/wav` 二进制音频，HTTP 状态码 `200`。

常见错误：

| HTTP 状态码 | 说明 |
|---|---|
| `400` | 请求参数校验失败，如 `text` 为空、`weights` 长度不为 8 或越界 |
| `422` | Pydantic 校验错误 |
| `500` | 模型推理失败 |
| `503` | 模型尚未加载完成 |

## 注意事项

- 模型在启动时加载，首次启动需要等待一段时间。
- IndexTTS2 内部有参考音频缓存，当前实现使用 `asyncio.Lock` 串行处理 `/tts` 请求，避免并发状态冲突。
- 情绪权重在传入模型前会经过与 WebUI 一致的 `normalize_emo_vec(apply_bias=True)` 归一化。
- 临时生成的音频文件会在返回前读入内存并删除，不会保留在磁盘上。
---
name: "image_tools"
description: "图片信息查看、缩放、裁剪、加文字水印"
---

# Skill Description
处理图片文件：查看信息、缩放、裁剪、添加文字水印。

## Usage

### 查看图片信息
```bash
python /skills/media/src/image_tool.py info input.png
```

### 缩放图片
```bash
python /skills/media/src/image_tool.py resize input.png --width 200 --height 200 --output /workspace/out.png
```

### 裁剪图片
```bash
python /skills/media/src/image_tool.py crop input.png --left 10 --top 10 --right 190 --bottom 190 --output /workspace/out.png
```

### 添加文字水印
```bash
python /skills/media/src/image_tool.py watermark input.png --text "Hello" --output /workspace/out.png
```

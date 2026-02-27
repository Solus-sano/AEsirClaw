"""图片处理 CLI。子命令: info, resize, crop, watermark。"""

import argparse
import sys

from PIL import Image, ImageDraw, ImageFont


def cmd_info(args):
    img = Image.open(args.input)
    print(f"size: {img.size}, mode: {img.mode}, format: {img.format}")


def cmd_resize(args):
    img = Image.open(args.input)
    img = img.resize((args.width, args.height))
    out = args.output or args.input
    img.save(out)
    print(f"已保存到 {out}")


def cmd_crop(args):
    img = Image.open(args.input)
    img = img.crop((args.left, args.top, args.right, args.bottom))
    out = args.output or args.input
    img.save(out)
    print(f"已保存到 {out}")


def cmd_watermark(args):
    img = Image.open(args.input).convert("RGBA")
    txt_layer = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(txt_layer)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", args.size)
    except OSError:
        font = ImageFont.load_default()
    draw.text((10, 10), args.text, fill=(255, 255, 255, 180), font=font)
    result = Image.alpha_composite(img, txt_layer)
    out = args.output or args.input
    result.save(out)
    print(f"已保存到 {out}")


def main():
    parser = argparse.ArgumentParser(description="图片处理工具")
    sub = parser.add_subparsers(dest="command", required=True)

    # info
    p_info = sub.add_parser("info")
    p_info.add_argument("input")

    # resize
    p_resize = sub.add_parser("resize")
    p_resize.add_argument("input")
    p_resize.add_argument("--width", type=int, required=True)
    p_resize.add_argument("--height", type=int, required=True)
    p_resize.add_argument("--output", "-o")

    # crop
    p_crop = sub.add_parser("crop")
    p_crop.add_argument("input")
    p_crop.add_argument("--left", type=int, required=True)
    p_crop.add_argument("--top", type=int, required=True)
    p_crop.add_argument("--right", type=int, required=True)
    p_crop.add_argument("--bottom", type=int, required=True)
    p_crop.add_argument("--output", "-o")

    # watermark
    p_wm = sub.add_parser("watermark")
    p_wm.add_argument("input")
    p_wm.add_argument("--text", required=True)
    p_wm.add_argument("--size", type=int, default=24)
    p_wm.add_argument("--output", "-o")

    args = parser.parse_args()
    {"info": cmd_info, "resize": cmd_resize, "crop": cmd_crop, "watermark": cmd_watermark}[args.command](args)


if __name__ == "__main__":
    main()

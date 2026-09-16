#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_icon.py — 把 icon_design 的图形渲染成多尺寸 .ico（桌面快捷方式用）

配色与几何参数全部来自 icon_design.py，与大盘 favicon 同源，不会各画各的。

为什么不用 AI 生图：favicon / 快捷方式图标主要在 16–48px 显示，生成式位图缩到
那么小会糊成一团；印章这种「几何 + 单字」图形手绘反而更清晰。

用法：
    python src/tools/make_icon.py            # 写 assets/kaoyan.ico + 预览图
    python src/tools/make_icon.py --svg      # 只打印 favicon 的 SVG
"""

import argparse
import io
import os
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from icon_design import (  # noqa: E402  —— 必须在 sys.path 之后导入
    GLYPH, GLYPH_CHAR, INSET, MO, R_IN, R_OUT, STROKE, XUAN, ZHUSHA,
    rgb, svg_markup,
)

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(SRC_DIR))          # 考研/
OUT_DIR = os.path.join(ROOT, "assets")
ICO_PATH = os.path.join(OUT_DIR, "kaoyan.ico")
PREVIEW_PATH = os.path.join(OUT_DIR, "kaoyan_preview.png")

# Pillow 要 (r,g,b)，icon_design 里配色以十六进制保存
MO = rgb(MO)
ZHUSHA = rgb(ZHUSHA)
XUAN = rgb(XUAN)

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
SS = 8                     # 超采样倍数：画大再缩，边缘才干净

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simhei.ttf",    # 黑体：笔画等粗，小尺寸最经得起缩
    r"C:\Windows\Fonts\msyhbd.ttc",    # 雅黑粗
    r"C:\Windows\Fonts\msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
]


def load_font(px):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, px)
            except OSError:
                continue
    raise SystemExit("找不到可用的中文字体，无法渲染「%s」字" % GLYPH_CHAR)


def render(size, glyph=None):
    """画一张 size×size 的图标（RGBA）。"""
    glyph = glyph or GLYPH_CHAR
    s = size * SS
    u = s / 64.0
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=R_OUT * u, fill=MO)
    d.rounded_rectangle(
        [INSET * u, INSET * u, s - INSET * u, s - INSET * u],
        radius=R_IN * u, outline=ZHUSHA, width=max(1, round(STROKE * u)),
    )
    # 字单独画一层，按墨迹包围盒居中——不同字体的基线与字面框差很多，
    # 用 anchor 对齐会各偏各的；量出实际墨迹再摆才稳。
    tmp = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((s // 2, s // 2), glyph,
                             font=load_font(int(GLYPH * u)), fill=XUAN, anchor="mm")
    bb = tmp.getbbox()
    if bb:
        g = tmp.crop(bb)
        img.alpha_composite(g, ((s - g.width) // 2, (s - g.height) // 2))
    return img.resize((size, size), Image.LANCZOS)


def write_ico(path, imgs):
    """手写 ICO 容器：每个尺寸直接塞一份 PNG，避免 Pillow 自己重采样毁掉小尺寸。

    ICONDIR(6B) + ICONDIRENTRY(16B × N) + 各尺寸 PNG 数据
    """
    entries, blobs, offset = [], [], 6 + 16 * len(imgs)
    for im in imgs:
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        # 宽高字段各 1 字节，256 要写成 0
        w = im.width if im.width < 256 else 0
        h = im.height if im.height < 256 else 0
        entries.append(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset))
        blobs.append(data)
        offset += len(data)
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(imgs)))
        for e in entries:
            f.write(e)
        for b in blobs:
            f.write(b)


def write_preview(imgs):
    """对照图：浅底 / 深底各一排。小尺寸在浅色任务栏会不会糊、深色会不会消失，
    看这一张就知道。"""
    pad, gap, label = 24, 18, 22
    cell = max(i.width for i in imgs)
    w = pad * 2 + sum(i.width for i in imgs) + gap * (len(imgs) - 1)
    h = pad * 2 + cell * 2 + label * 2 + gap
    canvas = Image.new("RGB", (w, h), (246, 245, 242))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, pad + cell + label + gap // 2, w, h], fill=MO)

    for base in (pad + label, pad * 2 + cell + label * 2 + gap // 2):
        x = pad
        for im in imgs:
            canvas.paste(im, (x, base + (cell - im.height) // 2), im)
            x += im.width + gap
    d.text((pad, pad - 4), "浅色背景", font=load_font(15), fill=(60, 60, 60))
    d.text((pad, pad + cell + label + gap // 2 - 4), "深色背景",
           font=load_font(15), fill=XUAN)
    canvas.save(PREVIEW_PATH)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--svg", action="store_true", help="只打印 favicon 的 SVG，不写文件")
    ap.add_argument("--glyph", default=GLYPH_CHAR, help="印章用的字")
    args = ap.parse_args()

    if args.svg:
        sys.stdout.buffer.write((svg_markup() + "\n").encode("ascii"))
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    imgs = [render(s, args.glyph) for s in SIZES]
    write_ico(ICO_PATH, imgs)
    write_preview(imgs)
    print("ICO  : %s (%d 字节, %d 个尺寸)" % (ICO_PATH, os.path.getsize(ICO_PATH), len(SIZES)))
    print("预览 : %s" % PREVIEW_PATH)
    print("尺寸 : %s" % ", ".join(str(s) for s in SIZES))
    return 0


if __name__ == "__main__":
    sys.exit(main())

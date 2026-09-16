#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
icon_design.py — 考研大盘图标的唯一定义（纯标准库，不依赖 Pillow）

设计：新中式印章——青墨底 + 朱砂细边 + 月白「研」字，配色与大盘 :root 同源。

被两处共用，避免位图和矢量各画各的：
    tools/make_icon.py    → 渲染成多尺寸 .ico（桌面快捷方式用）
    generate_dashboard.py → 内联成 favicon 的 data URI（浏览器标签页用）

几何参数是在 16/24/32/48 四档渲染图里比出来的，不是拍脑袋定的：
粗框（4/34）在 48px 端庄，但 16px 时 1px 的框会把字挤成一团；
现在的细框 + 大字在最小和最大两端都站得住。
"""

import base64

# --- 配色（与 generate_dashboard.py 的 :root 一致）---
MO = "#151A1A"        # 青墨
ZHUSHA = "#B84A42"    # 朱砂
XUAN = "#E5E9E7"      # 月白

# --- 设计尺寸 64×64 下的几何参数 ---
R_OUT = 13.0          # 外圆角
INSET = 5.0           # 内边框距边
R_IN = 9.0            # 内框圆角
STROKE = 2.5          # 内框线宽
GLYPH = 38.0          # 字面高
GLYPH_CHAR = "研"


def rgb(hex_color):
    """'#151A1A' → (21, 26, 26)，给 Pillow 用"""
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def svg_markup():
    """图标的 SVG。纯 ASCII 输出——「研」写成 &#30740; 字符引用，
    这样后面 base64 进 data URI 时不用操心编码声明。"""
    inner = 64.0 - 2 * INSET
    body = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<rect width="64" height="64" rx="{r_out:g}" fill="{mo}"/>'
        '<rect x="{inset:g}" y="{inset:g}" width="{inner:g}" height="{inner:g}" '
        'rx="{r_in:g}" fill="none" stroke="{zhusha}" stroke-width="{stroke:g}"/>'
        '<text x="32" y="32" text-anchor="middle" dominant-baseline="central" '
        'font-family="SimHei,Microsoft YaHei,PingFang SC,sans-serif" '
        'font-size="{glyph:g}" fill="{xuan}">{char}</text>'
        '</svg>'
    ).format(
        r_out=R_OUT, mo=MO, inset=INSET, inner=inner, r_in=R_IN,
        zhusha=ZHUSHA, stroke=STROKE, glyph=GLYPH, xuan=XUAN,
        char=GLYPH_CHAR.encode("ascii", "xmlcharrefreplace").decode("ascii"),
    )
    return body.encode("ascii").decode("ascii")


def favicon_href():
    """可直接塞进 <link rel="icon" href="..."> 的 data URI"""
    b64 = base64.b64encode(svg_markup().encode("ascii")).decode("ascii")
    return "data:image/svg+xml;base64," + b64


if __name__ == "__main__":
    import sys
    sys.stdout.buffer.write((svg_markup() + "\n").encode("ascii"))

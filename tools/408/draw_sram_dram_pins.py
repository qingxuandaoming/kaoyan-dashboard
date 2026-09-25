# -*- coding: utf-8 -*-
"""SRAM vs DRAM 引脚对比图（相同容量 64K×8）→ CO/assets/CO_SRAM与DRAM引脚对比_64Kx8.png"""
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch

ROOT = Path(r"E:\NPEE\408")
OUT = ROOT / "CO" / "assets"
OUT.mkdir(parents=True, exist_ok=True)

FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")
if FONT_PATH.exists():
    font_manager.fontManager.addfont(str(FONT_PATH))
    plt.rcParams["font.family"] = "Microsoft YaHei"
plt.rcParams["axes.unicode_minus"] = False

COLORS = {
    "ink": "#1F2937",
    "muted": "#6B7280",
    "blue": "#2563EB",
    "blue_light": "#DBEAFE",
    "green": "#059669",
    "green_light": "#D1FAE5",
    "amber": "#D97706",
    "amber_light": "#FEF3C7",
    "border": "#CBD5E1",
    "gray_light": "#F3F4F6",
}

fig, ax = plt.subplots(figsize=(13, 9), dpi=220)
ax.set_xlim(0, 1)
ax.set_ylim(0, 1)
ax.axis("off")
ax.text(0.5, 0.975, "SRAM vs DRAM 引脚对比（同为 64K×8 位，地址共 16 位）",
        ha="center", va="top", fontsize=17, weight="bold", color=COLORS["ink"])

TOP, BOT = 0.86, 0.12  # 芯片引脚排布范围


def chip(cx, w, name, subtitle, left_pins, right_pins, body_fc, foot):
    x0, x1 = cx - w / 2, cx + w / 2
    body = FancyBboxPatch((x0, BOT), w, TOP - BOT,
                          boxstyle="round,pad=0.004,rounding_size=0.01",
                          facecolor=body_fc, edgecolor=COLORS["border"], lw=1.6)
    ax.add_patch(body)
    ax.text(cx, TOP + 0.045, name, ha="center", fontsize=14, weight="bold", color=COLORS["ink"])
    ax.text(cx, TOP + 0.02, subtitle, ha="center", fontsize=9.5, color=COLORS["muted"])

    def pins(side_xs, plist, fc):
        n = len(plist)
        for i, label in enumerate(plist):
            y = TOP - (i + 0.5) * (TOP - BOT) / n
            px0, px1 = side_xs
            ax.plot([min(px0, px1), max(px0, px1)], [y, y],
                    color=COLORS["ink"], lw=1.4,
                    solid_capstyle="butt", zorder=3)
            ax.add_patch(FancyBboxPatch(
                (min(px0, px1) - 0.048 if px0 < px1 else px1 + 0.002, y - 0.011),
                0.046, 0.022,
                boxstyle="round,pad=0.002,rounding_size=0.004",
                facecolor=fc, edgecolor=COLORS["border"], lw=0.8, zorder=4))
            tx = (min(px0, px1) - 0.025) if px0 < px1 else (max(px0, px1) + 0.025)
            ax.text(tx, y, label, ha="center", va="center", fontsize=8.2,
                    color=COLORS["ink"], zorder=5)

    pins((x0 - 0.055, x0), left_pins, COLORS["gray_light"])
    pins((x1, x1 + 0.055), right_pins, COLORS["gray_light"])
    ax.text(cx, BOT - 0.028, foot, ha="center", fontsize=10.5, color=COLORS["ink"],
            weight="bold")


# ---------- SRAM：左=地址16根；右=数据8 + 控制3 + 电源2 ----------
chip(0.27, 0.30, "SRAM（静态 RAM）", "全地址一次送入，无需刷新",
     [f"A{i}" if i < 16 else "" for i in range(16)],
     [f"D{i}" for i in range(7, -1, -1)][::-1]
     + ["/CS", "/OE", "/WE", "VDD", "VSS"],
     COLORS["blue_light"],
     "地址引脚 16 根（A0~A15）｜总引脚 16+8+3+2 = 29")

# SRAM 芯片体内注释
ax.text(0.27, 0.52, "16 位地址一次全部送入\n内部译码器直接选单元\n无需刷新、无需锁存",
        ha="center", fontsize=9, color=COLORS["blue"], weight="bold")

# ---------- DRAM：左=地址仅8根；右=数据8 + 控制3 + 电源2 ----------
chip(0.75, 0.30, "DRAM（动态 RAM）", "行/列地址分两次复用同一组引脚",
     [f"A{i}" for i in range(8)],
     [f"D{i}" for i in range(7, -1, -1)][::-1]
     + ["/RAS", "/CAS", "/WE", "VDD", "VSS"],
     COLORS["green_light"],
     "地址引脚 16÷2 = 8 根（向上取整）｜总引脚 8+8+3+2 = 21")

# 地址复用示意（写在 DRAM 芯片体内空白处）
ax.text(0.755, 0.66, "第1步：送行地址 A0~A7\n由 /RAS 下降沿锁存", ha="center",
        fontsize=9, color=COLORS["green"], weight="bold")
ax.text(0.755, 0.565, "第2步：再送列地址 A0~A7\n由 /CAS 下降沿锁存", ha="center",
        fontsize=9, color=COLORS["green"], weight="bold")
ax.text(0.755, 0.475, "同一组 8 根引脚\n16 位地址分两次送", ha="center",
        fontsize=9, color=COLORS["amber"], weight="bold")
ax.text(0.755, 0.375, "刷新不占引脚：RAS-only 刷新周期\n由外部刷新电路提供行地址",
        ha="center", fontsize=8.5, color=COLORS["muted"])

# 底部结论
ax.text(0.5, 0.055,
        "秒判：DRAM 地址引脚 = 地址位数/2 向上取整（行列复用）；SRAM = 全部地址位数。容量 1M 位时差距更大：20 根 → 10 根",
        ha="center", fontsize=10.5, weight="bold", color=COLORS["ink"])
ax.text(0.5, 0.02,
        "真题原型：4164（64K×1 DRAM，16 引脚仅 8 根地址线）｜6264（8K×8 SRAM，28 引脚 13 根地址线）",
        ha="center", fontsize=9, color=COLORS["muted"])

path = OUT / "CO_SRAM与DRAM引脚对比_64Kx8.png"
plt.savefig(path, dpi=220, bbox_inches="tight", pad_inches=0.15, facecolor="white")
print(path)

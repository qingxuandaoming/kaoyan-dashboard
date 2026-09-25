# -*- coding: utf-8 -*-
"""SRAM 内部寻址通路图：16 根地址引脚 → 行/列译码器 → 256×256 矩阵交叉点
→ CO/assets/CO_SRAM地址译码与存储矩阵.png"""
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, Rectangle, FancyArrowPatch

ROOT = Path(r"E:\NPEE\408")
OUT = ROOT / "CO" / "assets"
OUT.mkdir(parents=True, exist_ok=True)

FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")
if FONT_PATH.exists():
    font_manager.fontManager.addfont(str(FONT_PATH))
    plt.rcParams["font.family"] = "Microsoft YaHei"
plt.rcParams["axes.unicode_minus"] = False

C = {
    "ink": "#1F2937", "muted": "#6B7280",
    "blue": "#2563EB", "blue_light": "#DBEAFE",
    "green": "#059669", "green_light": "#D1FAE5",
    "amber": "#D97706", "amber_light": "#FEF3C7",
    "red": "#DC2626", "red_light": "#FEE2E2",
    "border": "#CBD5E1", "gray_light": "#F3F4F6",
}

fig, ax = plt.subplots(figsize=(13.5, 9), dpi=220)
ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
ax.text(0.5, 0.975, "SRAM（64K×8）内部寻址：16 根地址引脚如何选中一个单元",
        ha="center", va="top", fontsize=17, weight="bold", color=C["ink"])


def box(x, y, w, h, fc, ec=C["border"], lw=1.4, r=0.012, z=3):
    p = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0.003,rounding_size={r}",
                       facecolor=fc, edgecolor=ec, lw=lw, zorder=z)
    ax.add_patch(p)


def arrow(p0, p1, color=C["ink"], lw=1.8, style="-|>"):
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle=style, mutation_scale=14,
                                 color=color, lw=lw, zorder=4))

# ---------- 1. 地址引脚 ----------
box(0.025, 0.30, 0.10, 0.56, C["gray_light"])
ax.text(0.075, 0.885, "16 根地址引脚", ha="center", fontsize=11, weight="bold", color=C["ink"])
for i in range(16):
    y = 0.815 - i * 0.0335
    ax.plot([0.035, 0.055], [y, y], color=C["ink"], lw=1.6, zorder=4)
    ax.text(0.062, y, f"A{i}", fontsize=6.8, va="center", color=C["ink"])
ax.text(0.075, 0.265, "每根只送 1 位（高=1/低=0）\n16 根合起来 = 一个 16 位二进制数\n是【组合编码】2^16 种，不是线交叉",
        ha="center", va="top", fontsize=8.2, color=C["muted"])

# ---------- 2. 拆分 ----------
arrow((0.125, 0.72), (0.20, 0.72), C["blue"])
arrow((0.125, 0.44), (0.20, 0.44), C["green"])
ax.text(0.163, 0.755, "高 8 位\nA15~A8", ha="center", fontsize=8.5, color=C["blue"], weight="bold")
ax.text(0.163, 0.475, "低 8 位\nA7~A0", ha="center", fontsize=8.5, color=C["green"], weight="bold")

# ---------- 3. 两个译码器 ----------
box(0.205, 0.60, 0.135, 0.24, C["blue_light"], ec=C["blue"])
ax.text(0.2725, 0.745, "行译码器", ha="center", fontsize=11, weight="bold", color=C["blue"])
ax.text(0.2725, 0.695, "8 → 256", ha="center", fontsize=9, color=C["ink"])
ax.text(0.2725, 0.655, "把 8 位编码翻译成\n1 根行线有效", ha="center", fontsize=7.8, color=C["muted"])

box(0.205, 0.32, 0.135, 0.24, C["green_light"], ec=C["green"])
ax.text(0.2725, 0.465, "列译码器", ha="center", fontsize=11, weight="bold", color=C["green"])
ax.text(0.2725, 0.415, "8 → 256", ha="center", fontsize=9, color=C["ink"])
ax.text(0.2725, 0.375, "把 8 位编码翻译成\n1 根列线有效", ha="center", fontsize=7.8, color=C["muted"])

# ---------- 4. 存储矩阵 ----------
MX0, MY0, MX1, MY1 = 0.44, 0.26, 0.86, 0.86
box(MX0 - 0.005, MY0 - 0.005, MX1 - MX0 + 0.01, MY1 - MY0 + 0.01, "white", ec=C["border"], z=1)
ax.text((MX0 + MX1) / 2, MY1 + 0.022, "存储矩阵（示意，画 10×10 代 256×256）",
        ha="center", fontsize=10.5, weight="bold", color=C["ink"])

n = 10
xs = [MX0 + 0.02 + i * (MX1 - MX0 - 0.04) / (n - 1) for i in range(n)]
ys = [MY0 + 0.03 + i * (MY1 - MY0 - 0.06) / (n - 1) for i in range(n)]
sel_row, sel_col = 3, 6  # 选中第3行、第6列（示意）

for i, y in enumerate(ys):
    hl = (i == sel_row)
    ax.plot([MX0, xs[-1] + 0.012], [y, y],
            color=C["blue"] if hl else C["border"],
            lw=2.2 if hl else 0.9, zorder=2)
for i, x in enumerate(xs):
    hl = (i == sel_col)
    ax.plot([x, x], [MY0, ys[-1] + 0.012],
            color=C["green"] if hl else C["border"],
            lw=2.2 if hl else 0.9, zorder=2)
# 交叉点高亮
ax.add_patch(Rectangle((xs[sel_col] - 0.014, ys[sel_row] - 0.016), 0.028, 0.032,
                       facecolor=C["amber_light"], edgecolor=C["amber"], lw=2, zorder=5))
arrow((0.34, 0.72), (MX0 - 0.005, ys[sel_row]), C["blue"])
arrow((0.34, 0.44), (xs[sel_col], MY0 - 0.005), C["green"])
ax.text(xs[sel_col] + 0.028, ys[sel_row] + 0.045,
        "行线 × 列线\n交叉点 = 唯一单元\n（SRAM 单元 = 1 个触发器）",
        fontsize=9, color=C["amber"], weight="bold", ha="left")

ax.text(MX0 - 0.008, ys[sel_row], "选中行", fontsize=8, color=C["blue"], ha="right", va="center")
ax.text(xs[sel_col], MY0 - 0.028, "选中列", fontsize=8, color=C["green"], ha="center")

# ---------- 5. 数据输出 ----------
arrow((xs[sel_col] + 0.016, ys[sel_row] - 0.05), (0.90, 0.155), C["red"])
box(0.895, 0.115, 0.09, 0.075, C["red_light"], ec=C["red"])
ax.text(0.94, 0.1525, "D0~D7\n读出/写入 1 字节", ha="center", va="center",
        fontsize=8, color=C["red"], weight="bold")
ax.text(0.875, 0.085, "×8 芯片：8 个这样的矩阵并排（位面），\n共用行/列译码，一次选中同一交叉位的 8 个触发器",
        ha="right", fontsize=8, color=C["muted"])

# ---------- 6. 底部结论 ----------
ax.text(0.5, 0.205,
        "为什么要二维矩阵？一维直译需 65536 根选择线；二维只需 256+256 = 512 根",
        ha="center", fontsize=10, weight="bold", color=C["ink"])
ax.text(0.5, 0.045,
        "DRAM 复用原理由此显然：内部本来就是「行 8 位 + 列 8 位」两半独立 → 同一组 8 根引脚分两次送（/RAS 锁行、/CAS 锁列）\n"
        "SRAM 用 16 根引脚一次送完，省掉锁存时序 —— 引脚数差异 = 速度 与 封装成本 的取舍",
        ha="center", fontsize=9.5, color=C["muted"])

path = OUT / "CO_SRAM地址译码与存储矩阵.png"
plt.savefig(path, dpi=220, bbox_inches="tight", pad_inches=0.15, facecolor="white")
print("saved:", path)

# -*- coding: utf-8 -*-
"""森林转二叉树 示例配图：转换前(3棵树) vs 转换后(1棵二叉树)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.executable).parent.parent.parent))
from daimon_runtime import setup_plot
setup_plot()
import matplotlib.pyplot as plt
from matplotlib.patches import Circle

# 莫兰迪配色
C_NODE = "#E8B4B8"   # 根结点
C_LEAF = "#A8C3A0"   # 普通结点
C_LEFT = "#8FA6C9"   # 左孩子边（孩子关系）
C_RIGHT = "#D98E73"  # 右孩子边（兄弟关系）
C_EDGE = "#9A9A9A"

def draw_node(ax, x, y, label, color, r=0.28):
    ax.add_patch(Circle((x, y), r, facecolor=color, edgecolor="#5A5A5A", lw=1.2, zorder=3))
    ax.text(x, y, label, ha="center", va="center", fontsize=13, weight="bold", zorder=4)

def draw_edge(ax, p, q, color, lw=1.6, ls="-"):
    ax.plot([p[0], q[0]], [p[1], q[1]], color=color, lw=lw, ls=ls, zorder=1)

fig, axes = plt.subplots(1, 2, figsize=(11, 4.6))

# ---------- 左图：转换前的森林（3 棵树） ----------
ax = axes[0]
ax.set_title("转换前：森林（3 棵互不相交的树）", fontsize=13, weight="bold")
# 树1: A -> D E F
for p, q in [((0, 2), (-1, 1)), ((0, 2), (0, 1)), ((0, 2), (1, 1))]:
    draw_edge(ax, p, q, C_EDGE)
draw_node(ax, 0, 2, "A", C_NODE)
draw_node(ax, -1, 1, "D", C_LEAF); draw_node(ax, 0, 1, "E", C_LEAF); draw_node(ax, 1, 1, "F", C_LEAF)
# 树2: B -> G
draw_edge(ax, (2.6, 2), (2.6, 1), C_EDGE)
draw_node(ax, 2.6, 2, "B", C_NODE); draw_node(ax, 2.6, 1, "G", C_LEAF)
# 树3: C -> H I
draw_edge(ax, (5.2, 2), (4.6, 1), C_EDGE); draw_edge(ax, (5.2, 2), (5.8, 1), C_EDGE)
draw_node(ax, 5.2, 2, "C", C_NODE); draw_node(ax, 4.6, 1, "H", C_LEAF); draw_node(ax, 5.8, 1, "I", C_LEAF)
# 标注根互为兄弟
ax.annotate("3 个根互为兄弟\n(A→B→C)", xy=(2.6, 2.45), fontsize=11, ha="center", color=C_RIGHT, weight="bold")
ax.set_xlim(-1.6, 6.6); ax.set_ylim(0.3, 2.9); ax.set_aspect("equal"); ax.axis("off")

# ---------- 右图：转换后的二叉树 ----------
ax = axes[1]
ax.set_title("转换后：二叉树（左孩子右兄弟）", fontsize=13, weight="bold")
pos = {"A": (0, 4), "D": (-1.5, 3), "B": (1.5, 3), "E": (-1.0, 2), "G": (1.0, 2), "C": (2.3, 2), "F": (-0.5, 1), "H": (1.9, 1), "I": (2.6, 0)}
left_edges = [("A", "D"), ("B", "G"), ("C", "H")]                # 孩子关系
right_edges = [("A", "B"), ("B", "C"), ("D", "E"), ("E", "F"), ("H", "I")]  # 兄弟关系
for u, v in left_edges:
    draw_edge(ax, pos[u], pos[v], C_LEFT, lw=2.0)
for u, v in right_edges:
    draw_edge(ax, pos[u], pos[v], C_RIGHT, lw=2.0)
for n, (x, y) in pos.items():
    draw_node(ax, x, y, n, C_NODE if n in "ABC" else C_LEAF)
ax.set_xlim(-2.2, 3.2); ax.set_ylim(-0.6, 4.6); ax.set_aspect("equal"); ax.axis("off")
# 图例
ax.plot([0.7, 1.0], [-0.35, -0.35], color=C_LEFT, lw=2)
ax.text(1.1, -0.35, "左孩子 = 第一个孩子", fontsize=10, va="center", color=C_LEFT)
ax.plot([0.7, 1.0], [-0.6, -0.6], color=C_RIGHT, lw=2)
ax.text(1.1, -0.6, "右孩子 = 兄弟（含根链）", fontsize=10, va="center", color=C_RIGHT)

fig.tight_layout()
out = Path(r"C:\Users\92534\Desktop\考研\408\DS\assets\DS_森林转二叉树_示例.png")
fig.savefig(out, dpi=150, bbox_inches="tight")
print("saved:", out)

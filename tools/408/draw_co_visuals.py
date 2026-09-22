from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle


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
    "red": "#DC2626",
    "red_light": "#FEE2E2",
    "gray_light": "#F3F4F6",
    "border": "#CBD5E1",
}


def setup(width, height, title):
    fig, ax = plt.subplots(figsize=(width, height), dpi=220)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.text(
        0.5,
        0.965,
        title,
        ha="center",
        va="top",
        fontsize=18,
        weight="bold",
        color=COLORS["ink"],
    )
    return fig, ax


def label_pill(ax, x, y, w, h, text, fc, ec, fs=9):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.008,rounding_size=0.012",
        facecolor=fc,
        edgecolor=ec,
        linewidth=1.1,
    )
    ax.add_patch(patch)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, weight="bold", color=COLORS["ink"])


def box(ax, x, y, w, h, title, subtitle="", fc="#FFFFFF", ec=None, lw=1.3, fs=11):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.012,rounding_size=0.016",
        facecolor=fc,
        edgecolor=ec or COLORS["border"],
        linewidth=lw,
    )
    ax.add_patch(patch)
    ax.text(x + w / 2, y + h * 0.61, title, ha="center", va="center", fontsize=fs, weight="bold", color=COLORS["ink"])
    if subtitle:
        ax.text(x + w / 2, y + h * 0.31, subtitle, ha="center", va="center", fontsize=fs - 2, color=COLORS["muted"])
    return patch


def arrow(ax, start, end, color=None, lw=1.5, text=None, text_offset=(0, 0)):
    arr = FancyArrowPatch(
        start,
        end,
        arrowstyle="-|>",
        mutation_scale=12,
        linewidth=lw,
        color=color or COLORS["muted"],
        shrinkA=4,
        shrinkB=4,
    )
    ax.add_patch(arr)
    if text:
        mx = (start[0] + end[0]) / 2 + text_offset[0]
        my = (start[1] + end[1]) / 2 + text_offset[1]
        ax.text(mx, my, text, ha="center", va="center", fontsize=9, color=color or COLORS["muted"])


def save(fig, name):
    fig.savefig(OUT / name, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def draw_boot_flow():
    fig, ax = setup(9.2, 11, "CO 第1章：开机启动流程（OS 最终加载到 RAM）")
    steps = [
        ("1 通电", "CPU 复位"),
        ("2 固定地址取指", "从 ROM/Flash 中的 BIOS/UEFI 入口开始"),
        ("3 执行 BIOS/UEFI", "固件负责最小化硬件初始化"),
        ("4 POST 自检", "检测 CPU、内存、外设等"),
        ("5 查找可引导设备", "硬盘、U 盘、网络启动等"),
        ("6 Boot Sector → RAM", "读取引导扇区或 EFI 程序"),
        ("7 Bootloader → RAM", "如 GRUB，继续加载 OS 内核"),
        ("8 OS Kernel → RAM", "内核被装入可读写内存"),
        ("9 跳转内核入口", "控制权交给 OS"),
        ("10 OS 初始化", "中断、内存管理、进程调度等"),
    ]
    locations = [
        ("CPU", COLORS["gray_light"], COLORS["border"]),
        ("ROM/Flash", COLORS["red_light"], COLORS["red"]),
        ("ROM/Flash", COLORS["red_light"], COLORS["red"]),
        ("ROM/Flash", COLORS["red_light"], COLORS["red"]),
        ("ROM/Flash", COLORS["red_light"], COLORS["red"]),
        ("RAM", COLORS["green_light"], COLORS["green"]),
        ("RAM", COLORS["green_light"], COLORS["green"]),
        ("RAM", COLORS["amber_light"], COLORS["amber"]),
        ("RAM", COLORS["green_light"], COLORS["green"]),
        ("RAM", COLORS["green_light"], COLORS["green"]),
    ]
    ax.text(0.12, 0.895, "执行位置", ha="center", va="center", fontsize=11, weight="bold", color=COLORS["ink"])
    ax.text(0.48, 0.895, "启动动作", ha="center", va="center", fontsize=11, weight="bold", color=COLORS["ink"])
    x, w, h = 0.25, 0.67, 0.06
    y0, gap = 0.84, 0.077
    for i, (title, sub) in enumerate(steps):
        y = y0 - i * gap
        fc = COLORS["blue_light"] if i < 5 else COLORS["green_light"]
        ec = COLORS["blue"] if i < 5 else COLORS["green"]
        if i == 7:
            fc, ec = COLORS["amber_light"], COLORS["amber"]
        loc_text, loc_fc, loc_ec = locations[i]
        label_pill(ax, 0.055, y + 0.012, 0.16, 0.035, loc_text, loc_fc, loc_ec)
        arrow(ax, (0.215, y + h / 2), (x, y + h / 2), color=loc_ec, lw=1.1)
        box(ax, x, y, w, h, title, sub, fc=fc, ec=ec)
        if i < len(steps) - 1:
            arrow(ax, (0.5, y), (0.5, y - gap + h), color=COLORS["muted"])

    ax.text(0.50, 0.075, "记忆：ROM/Flash 存 BIOS/UEFI；Bootloader 和 OS 内核会被加载到 RAM 中运行。", ha="center", fontsize=11, color=COLORS["ink"])
    save(fig, "CO_开机启动流程_OS加载到RAM.png")


def draw_cache_lookup():
    fig, ax = setup(10, 6, "CO 第3章：Cache 与主存不是统一编址")
    box(ax, 0.06, 0.60, 0.18, 0.13, "CPU", "发出主存地址", fc=COLORS["blue_light"], ec=COLORS["blue"], fs=12)
    box(ax, 0.34, 0.60, 0.24, 0.13, "Cache 控制器", "硬件映射、查找、替换", fc=COLORS["amber_light"], ec=COLORS["amber"], fs=12)
    box(ax, 0.70, 0.70, 0.20, 0.13, "Cache", "SRAM，高速副本", fc=COLORS["green_light"], ec=COLORS["green"], fs=12)
    box(ax, 0.70, 0.40, 0.20, 0.13, "主存", "DRAM，完整地址空间", fc=COLORS["gray_light"], ec=COLORS["border"], fs=12)
    arrow(ax, (0.24, 0.665), (0.34, 0.665), color=COLORS["blue"], text="主存地址", text_offset=(0, 0.055))
    arrow(ax, (0.58, 0.665), (0.70, 0.765), color=COLORS["green"], text="命中", text_offset=(-0.005, 0.055))
    arrow(ax, (0.58, 0.635), (0.70, 0.465), color=COLORS["red"], text="未命中", text_offset=(-0.005, -0.06))
    arrow(ax, (0.80, 0.53), (0.80, 0.70), color=COLORS["amber"], text="调入块", text_offset=(0.06, 0))

    ax.text(0.12, 0.33, "关键点", fontsize=13, weight="bold", color=COLORS["ink"])
    notes = [
        "Cache 是独立的高速存储器，不占用主存地址空间。",
        "CPU 发出的始终是主存地址，Cache 对程序员透明。",
        "Cache 中保存的是主存活跃数据块的副本。",
    ]
    for i, note in enumerate(notes):
        ax.text(0.12, 0.27 - i * 0.06, f"{i + 1}. {note}", fontsize=11, color=COLORS["ink"])
    save(fig, "CO_Cache与主存编址关系_访问流程.png")


def draw_cache_hardware():
    fig, ax = setup(10, 7.2, "CO 第3章：Cache 功能全部由硬件实现")
    steps = [
        ("CPU 发出主存地址", "软件无需参与"),
        ("地址划分", "Tag | 组号/行号 | 块内偏移"),
        ("Tag 比较器并行比较", "判断 Cache 是否命中"),
        ("命中：直接读写 Cache", "纳秒级数据通路"),
        ("未命中：访问主存并调入", "必要时执行替换"),
        ("写策略控制", "写直达 / 写回，写分配 / 非写分配"),
    ]
    coords = [(0.08, 0.70), (0.38, 0.70), (0.68, 0.70), (0.20, 0.42), (0.58, 0.42), (0.38, 0.20)]
    sizes = [(0.22, 0.12), (0.22, 0.12), (0.24, 0.12), (0.25, 0.12), (0.25, 0.12), (0.24, 0.12)]
    for i, ((title, sub), (x, y), (w, h)) in enumerate(zip(steps, coords, sizes)):
        fc = COLORS["blue_light"] if i == 0 else COLORS["amber_light"] if i in (1, 2, 5) else COLORS["green_light"]
        ec = COLORS["blue"] if i == 0 else COLORS["amber"] if i in (1, 2, 5) else COLORS["green"]
        box(ax, x, y, w, h, title, sub, fc=fc, ec=ec, fs=11)
    arrow(ax, (0.30, 0.76), (0.38, 0.76), color=COLORS["muted"])
    arrow(ax, (0.60, 0.76), (0.68, 0.76), color=COLORS["muted"])
    arrow(ax, (0.80, 0.70), (0.34, 0.54), color=COLORS["green"], text="命中", text_offset=(-0.03, 0.03))
    arrow(ax, (0.80, 0.70), (0.70, 0.54), color=COLORS["red"], text="未命中", text_offset=(0.03, -0.01))
    arrow(ax, (0.32, 0.42), (0.46, 0.32), color=COLORS["amber"])
    arrow(ax, (0.70, 0.42), (0.54, 0.32), color=COLORS["amber"])

    ax.text(0.5, 0.09, "原因：Cache 目标是弥补 CPU 与主存速度差距，若由软件介入，开销会超过缓存收益。", ha="center", fontsize=11, color=COLORS["ink"])
    save(fig, "CO_Cache硬件实现流程.png")


def draw_address_split():
    fig, ax = setup(10, 4.8, "CO 第3章：32 位主存地址划分（2021 直接映射例）")
    x0, y, h = 0.08, 0.56, 0.18
    widths = [17 / 32 * 0.84, 10 / 32 * 0.84, 5 / 32 * 0.84]
    labels = [("Tag（标记）", "17 位"), ("行号 / 索引", "10 位"), ("块内偏移", "5 位")]
    fills = [COLORS["blue_light"], COLORS["amber_light"], COLORS["green_light"]]
    edges = [COLORS["blue"], COLORS["amber"], COLORS["green"]]
    x = x0
    for (title, bits), w, fc, ec in zip(labels, widths, fills, edges):
        ax.add_patch(Rectangle((x, y), w, h, facecolor=fc, edgecolor=ec, linewidth=1.5))
        ax.text(x + w / 2, y + h * 0.62, title, ha="center", va="center", fontsize=13, weight="bold", color=COLORS["ink"])
        ax.text(x + w / 2, y + h * 0.34, bits, ha="center", va="center", fontsize=12, color=COLORS["ink"])
        arrow(ax, (x + w / 2, y), (x + w / 2, y - 0.055), color=ec, lw=1.2)
        x += w
    desc_rows = [
        (0.09, "Tag：区分映射到同一 Cache 行的不同主存块，必须随数据存入 Cache 行。", COLORS["blue"]),
        (0.09, "行号 / 索引：直接定位 1024 个 Cache 行中的一行，位置本身就是行号。", COLORS["amber"]),
        (0.09, "块内偏移：在 32B 数据块中选择具体字节，只作为读写选择信号。", COLORS["green"]),
    ]
    for i, (x_text, text, color) in enumerate(desc_rows):
        ax.text(x_text, 0.43 - i * 0.065, text, ha="left", va="top", fontsize=10.5, color=COLORS["ink"])
        ax.add_patch(Rectangle((x_text - 0.025, 0.423 - i * 0.065), 0.014, 0.014, facecolor=color, edgecolor=color))
    ax.text(0.08, 0.80, "CPU 发出的完整主存地址：32 位", fontsize=12, weight="bold", color=COLORS["ink"])
    ax.text(0.08, 0.21, "计算：块大小 32B = 2^5 → 偏移 5 位；Cache 1024 行 = 2^10 → 行号 10 位；Tag = 32 - 10 - 5 = 17 位。", fontsize=11, color=COLORS["ink"])
    ax.text(0.08, 0.12, "结论：Cache 行中存 Tag + 数据块 + 控制位，不存行号和偏移。", fontsize=11, color=COLORS["ink"])
    save(fig, "CO_Cache地址划分_32位直接映射.png")


def draw_replacement_unit():
    fig, ax = setup(10, 5.6, "CO 第3章：Cache 替换以块 / 行为单位")
    box(ax, 0.06, 0.58, 0.20, 0.14, "主存", "按块划分", fc=COLORS["gray_light"], ec=COLORS["border"], fs=12)
    box(ax, 0.40, 0.58, 0.20, 0.14, "Cache", "按行存放、按块替换", fc=COLORS["green_light"], ec=COLORS["green"], fs=12)
    box(ax, 0.74, 0.58, 0.20, 0.14, "CPU", "按字 / 字节读写", fc=COLORS["blue_light"], ec=COLORS["blue"], fs=12)
    arrow(ax, (0.26, 0.65), (0.40, 0.65), color=COLORS["green"])
    arrow(ax, (0.60, 0.65), (0.74, 0.65), color=COLORS["blue"])
    ax.text(0.33, 0.745, "未命中：整块调入", ha="center", va="center", fontsize=10, weight="bold", color=COLORS["green"])
    ax.text(0.67, 0.745, "命中：只取所需字 / 字节", ha="center", va="center", fontsize=10, weight="bold", color=COLORS["blue"])

    items = [
        ("1 发生未命中", "需要的主存块不在 Cache 中"),
        ("2 选中牺牲行", "LRU / FIFO / 随机等替换算法"),
        ("3 脏块先写回", "写回策略且脏位为 1 时需要"),
        ("4 新块整体替换", "替换整个 Cache 行"),
    ]
    for i, (title, sub) in enumerate(items):
        x = 0.08 + i * 0.23
        box(ax, x, 0.24, 0.18, 0.13, title, sub, fc=COLORS["amber_light"], ec=COLORS["amber"], fs=10)
        if i < len(items) - 1:
            arrow(ax, (x + 0.18, 0.305), (x + 0.23, 0.305), color=COLORS["muted"])
    ax.text(0.5, 0.12, "空间局部性：访问某个地址后，相邻数据很可能很快被访问，所以未命中时按块搬运。", ha="center", fontsize=11, color=COLORS["ink"])
    save(fig, "CO_Cache替换单位_块与行.png")


if __name__ == "__main__":
    draw_boot_flow()
    draw_cache_lookup()
    draw_cache_hardware()
    draw_address_split()
    draw_replacement_unit()

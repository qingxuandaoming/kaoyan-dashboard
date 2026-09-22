"""笔记条目 ID → 知识图谱科目前缀。

408 / 数学 / 英语的条目 ID 形如 408-DS-001、MATH-GS-004、ENG-READ-002，
取前两段即图谱前缀，无需转换。

政治是三套编号混用，必须查表：

    马原    MY-001     → POL-MY
    毛中特  MZT-001    → POL-MZ
    史纲    SG-003     → POL-SG
    习思想  XSX-001    → POL-XX
    思修    思修-001   → POL-SX
    专题    ZT-001     → （跨科目方法论，不归属任何图谱科目）

历史坑（2026-09-17 修）：
  * gap_analysis.py 原先没有这张表，把 "MY-001" 整体当作前缀（"MY-001" != "POL-MY"），
    政治笔记永远匹配不上图谱，政治「笔记覆盖」恒为 0，全靠 progress overlay 硬撑到 100%。
  * generate_dashboard.py 有表但把 XSX 错写成 POL-SX（思想道德与法治），
    7 篇习思想笔记被挂到思修名下，并在第 11/13 章报越界。
  * 思修笔记用中文前缀「思修-001」，两张表里都没有。

两个脚本各存一份映射是这两个 bug 能长期存活的原因，所以抽到这里共用，
与 english_coverage.py 同一模式——那也正因为「两边口径打架」才被抽出来的。
"""

import re

# 政治短编号 → 图谱科目前缀
NOTE_PREFIX_ALIAS = {
    "MY":  "POL-MY",   # 马原
    "MZT": "POL-MZ",   # 毛中特
    "SG":  "POL-SG",   # 史纲
    "XSX": "POL-XX",   # 习思想
    "思修": "POL-SX",   # 思修（该科笔记用的是中文前缀，不是拼音缩写）
}

# 英语：既有笔记 ID 沿用 ENG-TRN（用户笔记规范里 ENG-TRN = 翻译练习），
# 但 ENG-TRN 这个考点前缀已随「翻译与完形」拆分取消，翻译现为 ENG-TRAN，
# 故把笔记侧的前缀桥接过去。完形侧没有历史笔记。
EN_PREFIX_ALIAS = {"ENG-TRN": "ENG-TRAN"}

# 跨科目方法论专题：内容本就是跨章的横向汇总，强行归到某一格反而是错的，
# 因此解析为空前缀，调用方按「不计入任何章节」处理。
#   ZT = Politics/专题/ 下的方法论专题
#   QT = 政治强化.md 里的优题库错题知识点（散记，无章节归属，二刷中持续补充）
CROSS_SUBJECT_PREFIXES = {"ZT", "QT"}


def note_entry_prefix(eid) -> str:
    """笔记条目 ID → 知识图谱科目前缀；无法归属时返回空串。

    '408-DS-001' → '408-DS'
    'MATH-GS-04' → 'MATH-GS'
    'MY-001'     → 'POL-MY'
    '思修-001'    → 'POL-SX'
    'ZT-001'     → ''        （跨科目专题）
    """
    parts = str(eid or "").split("-")
    if not parts or not parts[0]:
        return ""
    root = parts[0]
    if root in NOTE_PREFIX_ALIAS:
        return NOTE_PREFIX_ALIAS[root]
    if root in CROSS_SUBJECT_PREFIXES:
        return ""
    if len(parts) >= 2:
        two = "-".join(parts[:2])
        return EN_PREFIX_ALIAS.get(two, two)
    return root


def is_cross_subject(eid) -> bool:
    """是否跨科目专题笔记（这类条目不参与章节级覆盖统计）。"""
    root = str(eid or "").split("-")[0]
    return root in CROSS_SUBJECT_PREFIXES


# ---------------------------------------------------------------------------
# 笔记文件名里的「单元号」：第N章 还是 第N讲
# ---------------------------------------------------------------------------
# 2026-09-22：**数学高数**的笔记从「按教材章（8 章）」迁移到「按张宇强化 18 讲的讲」，
# 文件名随之从 `第N章_标题.md` 变成 `第N讲_标题.md`。
# ⚠️ 线代（6 章）与概率论（6 章）**保持按章不动** —— 用户明确只要高数拆讲。
# 所以两套名字会长期共存（不只是迁移中途），三处消费方（generate_dashboard 的章节
# 权重、daily_brief 的载体匹配、generate_pdf 的章节扫描）统一用这里的一个正则，
# 别再各写一份 `第(\d+)章`——过去正是因为「政治映射表存了两份」才出现长期静默 bug
# （见本文件顶部历史坑）。
NOTE_UNIT_RE = re.compile(r"^第?\s*0*(\d+)\s*[章讲]")


def note_unit_no(name: str):
    """从笔记文件名里取单元号（章或讲），取不到返回 None。

    '第3章_一元函数积分学.md' → 3
    '第9讲_一元函数积分学的计算.md' → 9
    '公式速查.md' → None
    """
    m = NOTE_UNIT_RE.match(str(name or ""))
    return int(m.group(1)) if m else None


def topic_note_chapter(topic) -> int | None:
    """图谱考点用来对齐「笔记单元号」的那个号（章号或讲次）。

    408 / 政治 / 英语：考点的 chapter 就是教材章号，笔记也照它编号，直接可用。
    数学：考点按张宇的「讲」建（高数18 / 线代9 / 概率9）。

    2026-09-17 引入时的背景是「图谱按讲建、笔记按章写，一章对多讲」，
    所以数学考点额外带一个 note_chapter 指到所属的笔记章号。

    **2026-09-22 起「高数」的笔记已迁移为按讲拆分**（`第N章_标题.md` →
    `第N讲_标题.md`，8 章 → 18 讲），高数的 chapter 与笔记单元号从此是同一个号，
    `MATH-GS-*` 上的 note_chapter 键已删除 → 本函数对高数自动回退到 chapter。

    ⚠️ **线代（MATH-XD）与概率论（MATH-GL）的笔记仍按教材章，没有迁移**
    （用户 2026-09-22 明确「只改高数，线代和概率不动」）→ 这两科的 note_chapter
    必须保留，而且**讲号与章号是交叉的**：线代讲5「线性方程组」的笔记在
    `第4章_线性方程组.md`、讲6「向量组」在 `第3章_向量组与线性相关性.md`。
    删掉它们的 note_chapter 会让线代的笔记覆盖与每日任务载体全部错配。

    显式写 `"note_chapter": null` 表示「这个考点没有对应笔记」，
    不匹配任何文件、一直算缺口；省略该键则回退到 chapter。
    """
    if "note_chapter" in topic:
        return topic.get("note_chapter")
    return topic.get("chapter")

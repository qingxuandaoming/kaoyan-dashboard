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

# 政治短编号 → 图谱科目前缀
NOTE_PREFIX_ALIAS = {
    "MY":  "POL-MY",   # 马原
    "MZT": "POL-MZ",   # 毛中特
    "SG":  "POL-SG",   # 史纲
    "XSX": "POL-XX",   # 习思想
    "思修": "POL-SX",   # 思修（该科笔记用的是中文前缀，不是拼音缩写）
}

# 跨科目方法论专题：内容本就是跨章的横向汇总，强行归到某一格反而是错的，
# 因此解析为空前缀，调用方按「不计入任何章节」处理。
CROSS_SUBJECT_PREFIXES = {"ZT"}


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
        return "-".join(parts[:2])
    return root


def is_cross_subject(eid) -> bool:
    """是否跨科目专题笔记（这类条目不参与章节级覆盖统计）。"""
    root = str(eid or "").split("-")[0]
    return root in CROSS_SUBJECT_PREFIXES


def topic_note_chapter(topic) -> int | None:
    """图谱考点用来对齐「笔记章号」的那个章号。

    408 / 政治 / 英语：考点的 chapter 就是教材章号，笔记也照它编号，直接可用。
    数学：考点按张宇的「讲」建（高数18 / 线代9 / 概率9），但笔记是按「章」写的
    （高数8 / 线代6 / 概率6），一章对应多讲。所以数学考点额外带一个 note_chapter
    指向所属的笔记章号；匹配笔记时用它，展示时仍用 chapter（讲次）。

    显式写 `"note_chapter": null` 表示「这个考点没有对应笔记」（如张宇第17讲
    多元函数积分学的预备知识——空间解析几何，用户没有这一章的笔记），
    它不会匹配任何笔记、一直显示为缺口；省略该键则回退到 chapter。

    2026-09-17 引入。此前四处各写各的 `topic.get("chapter")`，数学改讲次后会
    全部失配——与政治那次「三个脚本各存一份映射表」是同一类坑。
    """
    if "note_chapter" in topic:
        return topic.get("note_chapter")
    return topic.get("chapter")

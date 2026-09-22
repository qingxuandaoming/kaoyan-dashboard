#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrate_math_lectures.py —— 高数笔记「按教材章」→「按张宇强化18讲」迁移器。

为什么要有它
------------
图谱 `math_graph.json` 的 MATH-GS-01..18 一直是张宇 18 讲的讲次，笔记却按教材章写
（高数 8 章），两套编号靠 `note_chapter` 桥接（一章对多讲）——大盘、差距分析、
每日任务三处都得先过桥才能对上，「对照不通顺」就出在这里。本脚本把高数笔记拆成
18 个讲文件，让高数的讲号与图谱考点号合一，桥随即可以拆掉。

⚠️ **只动高数**。线代（6 章）与概率论（6 章）保持按章不动（用户 2026-09-22 明确），
它们的 note_chapter 必须保留（线代讲5→第4章、讲6→第3章，是交叉的）。

映射的单一事实源是 `src/math_lectures.json`，本脚本不含任何硬编码的章节对应关系。

安全设计
--------
* **内容守恒断言**：源章文件除「H1 标题行」「末尾双链导航块」「被改写的 preamble」外，
  每一行必须且只能落进一个讲文件；对不上就整体中止，一个文件都不写。
* **默认 dry-run**：只打印拆分计划与守恒核对，`--apply` 才落盘。
* **不覆盖陌生文件**：目标讲文件已存在且没有本脚本的迁移标记 → 报错停手。
* 源章文件默认保留（`--delete-old` 才删），方便逐讲对照检查。
* 统一 LF、无 BOM 写回（与 .gitattributes 的 `* text=auto eol=lf` 一致）。

用法
----
    python tools/migrate_math_lectures.py                # dry-run：打印计划 + 守恒核对
    python tools/migrate_math_lectures.py --apply        # 落盘（保留旧章文件）
    python tools/migrate_math_lectures.py --apply --delete-old
"""
import argparse
import io
import json
import os
import re
import sys
from collections import OrderedDict

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
ROOT = os.path.dirname(SRC)
SPEC = os.path.join(SRC, "math_lectures.json")

HEAD_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
NAV_RE = re.compile(r"^#{2,3}\s*🔗?\s*双链导航\s*$")
SEC_NO_RE = re.compile(r"^(\d+(?:\.\d+)*[a-z]?)\s")
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\s]+")
MIGRATE_MARK = "<!-- migrated-by: tools/migrate_math_lectures.py -->"


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def read_lines(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n").split("\n")


def norm_title(t):
    return EMOJI_RE.sub("", str(t or "")).strip()


def sec_no(title):
    """「3.10 函数方程与定积分」→「3.10」。取不到返回 None。

    ⚠️ 必须按「完整编号 token」比对，不能用前缀 startswith：
    "3.10 …".startswith("3.1") 为真，会把 3.10~3.16 全部错配到 3.1 那一讲。
    """
    m = SEC_NO_RE.match(norm_title(title))
    return m.group(1) if m else None


class Block(object):
    """一个标题块。

    ⚠️ 块是**嵌套**的：`## 3.1` 的范围覆盖它下面所有 `### 3.1.x`。
    所以搬运时只能用 `own_*`（标题行 + 到第一个子标题之前的正文），
    子标题由它自己的块负责——否则父块与子块会被重复搬进讲文件
    （第一版就踩了：第1章 1021 行算出 2010 行）。
    """

    def __init__(self, idx, level, title, start, end, parent):
        self.idx = idx
        self.level = level
        self.title = title
        self.start = start          # 含标题行
        self.end = end              # 不含；覆盖全部子孙
        self.own_end = end          # 不含；只到第一个子标题
        self.parent = parent        # Block or None
        self.lecture = None         # 归属讲号
        self.explicit = False

    @property
    def nlines(self):
        return self.own_end - self.start

    def ancestors(self):
        out, p = [], self.parent
        while p is not None:
            out.append(p)
            p = p.parent
        return out


def parse_blocks(lines):
    """返回 (h1_idx, preamble_range, blocks, nav_range)。

    preamble = H1 之后、第一个 level>=2 标题之前的文字。
    nav      = 末尾「双链导航」块（含标题行到文件尾）。
    """
    h1 = None
    heads = []
    for i, ln in enumerate(lines):
        m = HEAD_RE.match(ln)
        if not m:
            continue
        lvl = len(m.group(1))
        if lvl == 1 and h1 is None:
            h1 = i
            continue
        heads.append((i, lvl, m.group(2)))

    # 双链导航块：从其标题行到文件尾
    nav = None
    for k, (i, lvl, t) in enumerate(heads):
        if NAV_RE.match(lines[i]):
            nav = (i, len(lines))
            heads = heads[:k]
            break

    blocks = []
    stack = []
    for i, lvl, title in heads:
        while stack and stack[-1].level >= lvl:
            stack.pop()
        parent = stack[-1] if stack else None
        blocks.append(Block(len(blocks), lvl, title, i, len(lines), parent))
        stack.append(blocks[-1])
    # 定 end：下一个「层级 <= 自身」的标题行
    for b in blocks:
        for c in blocks:
            if c.start > b.start and c.level <= b.level:
                b.end = c.start
                break
        if nav and b.end > nav[0]:
            b.end = nav[0]
    # 定 own_end：第一个子标题行（没有子标题就等于 end）
    for b in blocks:
        kids = [c.start for c in blocks if c.parent is b]
        b.own_end = min(kids) if kids else b.end

    first = min((b.start for b in blocks), default=nav[0] if nav else len(lines))
    pre_start = (h1 + 1) if h1 is not None else 0
    return h1, (pre_start, first), blocks, nav


# ---------------------------------------------------------------------------
# 归属判定
# ---------------------------------------------------------------------------
def build_assign_map(spec_lectures, source_files):
    """{源文件名: [(matcher, 讲号, 原文)]}，matcher = ("no","3.10") 或 ("sub","递推数列证不等式")。

    同时给出每个源文件的 default 讲：有 `mode:"default"` 就用它；
    整章按标题全拆的（如第2章）没有 default 声明，退回**该章的第一个讲**，
    这样任何漏配的块也有归宿，且会被守恒核对暴露出来（不会静默丢内容）。
    """
    amap = {f: [] for f in source_files}
    defaults = {}
    first_no = {}
    for lec in spec_lectures:
        for s in lec.get("sources") or []:
            fn = s["file"]
            amap.setdefault(fn, [])
            first_no[fn] = min(first_no.get(fn, 10**9), lec["no"])
            if s.get("mode") == "default":
                if fn in defaults:
                    raise SystemExit("[映射错误] %s 被两个讲同时声明为 default" % fn)
                defaults[fn] = lec["no"]
            for h in s.get("headings") or []:
                n = sec_no(h)
                amap[fn].append((("no", n) if n else ("sub", norm_title(h)), lec["no"], h))
    for fn in source_files:
        if fn not in first_no:
            raise SystemExit("[映射错误] 源文件 %s 没有任何讲声明引用它" % fn)
        defaults.setdefault(fn, first_no[fn])
    return amap, defaults


def match_title(entry, title):
    kind, val = entry[0]
    if kind == "no":
        return sec_no(title) == val
    return val in norm_title(title)


def resolve(blocks, amap, default_no, fname):
    """给每个块定讲号：自己显式 > 最近的显式祖先 > default。"""
    entries = amap[fname]
    problems = []
    for b in blocks:
        hits = [e for e in entries if match_title(e, b.title)]
        if len(hits) > 1:
            lec_set = {h[1] for h in hits}
            if len(lec_set) > 1:
                problems.append("标题「%s」同时命中多个讲：%s" % (b.title, sorted(lec_set)))
        if hits:
            b.lecture, b.explicit = hits[0][1], True
    for b in blocks:
        if b.lecture is None:
            for a in b.ancestors():
                if a.lecture is not None:
                    b.lecture = a.lecture
                    break
        if b.lecture is None:
            b.lecture = default_no
    return problems


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------
def header(lec, src_chapter_no, src_chapter_name, extra=None):
    origin = ("原笔记：第%d章 %s" % (src_chapter_no, src_chapter_name)) if src_chapter_no else ""
    lines = ["# 第%d讲 %s" % (lec["no"], lec["name"]), "",
             "> 张宇《高数18讲》第%d讲 ｜ 图谱考点 %s%s" % (
                 lec["no"], lec["topic_id"], (" ｜ " + origin) if origin else ""),
             "> 本文件由 tools/migrate_math_lectures.py 从「按教材章」的笔记拆分而来，拆分依据见 src/math_lectures.json。"]
    if extra:
        lines.extend(extra)
    lines += [MIGRATE_MARK, "", "---", ""]
    return lines


def provenance(block, src_no, src_name):
    """块的祖先标题没跟过来时，补一行出处，避免脱离上下文读不懂。"""
    missing = [a.title for a in reversed(block.ancestors()) if a.lecture != block.lecture]
    if not missing:
        return []
    return ["> 📎 出自原第%d章《%s》的「%s」" % (src_no, src_name, "」→「".join(missing)), ""]


def nav_for(lec, spec, siblings, src_nav_lines):
    """重新生成双链导航：上/下一讲 + 同源兄弟讲 + 源导航里的跨文件链接（去重）。"""
    by_no = {l["no"]: l for l in spec["lectures"]}
    out = ["## 🔗 双链导航", ""]
    if lec["no"] - 1 in by_no:
        p = by_no[lec["no"] - 1]
        out.append("- 上一讲：[第%d讲 %s](./%s)" % (p["no"], p["name"], p["file"]))
    if lec["no"] + 1 in by_no:
        n = by_no[lec["no"] + 1]
        out.append("- 下一讲：[第%d讲 %s](./%s)" % (n["no"], n["name"], n["file"]))
    if len(siblings) > 1:
        others = [s for s in siblings if s["no"] != lec["no"]]
        out.append("- 同一章拆出的其他讲：%s" % "、".join(
            "[第%d讲](./%s)" % (s["no"], s["file"]) for s in others))
    # 源导航里的链接：丢掉「上/下/前/后一章」这类章级链接（已被讲级链接取代）
    drop = re.compile(r"(上一章|下一章|前一章|后一章|前置知识|返回)")
    seen = set()
    for ln in src_nav_lines:
        s = ln.strip()
        if not s.startswith("-"):
            continue
        if drop.search(s.split("]")[0]):
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    out.append("- 返回中控：[高数 notes.md](./高数%20notes.md)")
    out.append("- 返回总览：[数一知识体系总览.md](../数一知识体系总览.md)")
    out.append("")
    return out


STUB_BODY = """## ⚠️ 本讲暂无笔记条目

图谱考点 {tid} 的子考点是「{subs}」，但现有高数笔记里**从来没有整理过这一讲的内容**。

过去它被 `note_chapter: 2` 混进「第2章 一元函数微分学」，所以差距分析一直显示
「已覆盖」——那是**假覆盖**（笔记里翻不到任何相关变化率或物理极值的条目）。
迁移后本讲不再挂 note_chapter，缺口会如实显示出来。

### 现在能用的相邻材料

- 物理建模的「列方程」环节：[第15讲 微分方程](./第15讲_微分方程.md) 的 6.13 应用题（建模）路由
- 积分侧的物理应用（做功/压力/质心）：[第12讲 一元函数积分学的应用三——物理应用](./第12讲_一元函数积分学的应用三_物理应用.md)
- 极值的判定工具：[第5讲 几何应用](./第5讲_一元函数微分学的应用一_几何应用.md)

### 要补什么

- 相关变化率：$\dfrac{{dy}}{{dt}}=\dfrac{{dy}}{{dx}}\cdot\dfrac{{dx}}{{dt}}$，两个变量都随时间变时的联立求导
- 物理极值：把物理量写成单变量函数后求驻点，注意**端点与定义域**（物理量常要求非负）

> 补完后请用 `python src/tools/notes_entry.py --root Math --json -` 入账（chapter 填「第7讲」），
> 缺口才会闭合。**不要为了让覆盖率好看而往索引里塞空条目。**
"""

HUB_BODY = """## 正文在专题里（不在本文件）

这一讲（向量代数、空间平面与直线、曲面与曲线方程、场论初步）的内容早就写好了，
只是一直躺在专题目录、没有章节归属，所以图谱里 `note_chapter` 是 `null`，
差距分析常年把 MATH-GS-17 报成缺口。本文件只做**讲级入口**，正文不复制（守单一事实源）。

- [空间解析几何专题](../专题/空间曲面体绘图/空间解析几何专题.md) —— 曲面与曲线方程、空间图形识别与绘图
- [专题/多元函数积分学](../专题/多元函数积分学.md) —— 重积分/曲线曲面积分的计算路由
- 场论初步（散度、旋度）：[第18讲 多元函数积分学](./第18讲_多元函数积分学.md) 的 8.C.6「为什么出现旋度/散度」

### 与下游的关系

第18讲（曲线积分与曲面积分）默认你已经会：空间平面的点法式、空间直线的对称式、
曲面方程与投影、向量积与混合积。这些都在上面那份专题里。
"""


def main():
    ap = argparse.ArgumentParser(description="高数笔记 章→讲 迁移器")
    ap.add_argument("--apply", action="store_true", help="真正写盘（默认 dry-run）")
    ap.add_argument("--delete-old", action="store_true", help="迁移后删除源章文件")
    ap.add_argument("--force", action="store_true",
                    help="跳过「源文件刚被改过」的闸门（有并行笔记会话在写时用）")
    ap.add_argument("--out", help="把计划写到这个文件（便于逐条核对）")
    args = ap.parse_args()

    spec = json.loads(open(SPEC, encoding="utf-8").read())
    gs_dir = os.path.join(ROOT, *spec["subject"]["dir"].split("/"))

    # 并发写入闸门：源章文件在最近 QUIET_SECONDS 内被改过，说明很可能有另一个
    # 笔记整理会话正在写（notes_entry.py 会同时改 章节笔记 + 中控 + notes_index.json）。
    # 这时落盘会把两边的改动劈开：我拆出讲文件、它继续往旧章文件追加 → 内容静默分叉。
    # 2026-09-22 实测撞到过（第4章/错题归档/中控/notes_index 在 23:15~23:16 被改）。
    QUIET_SECONDS = 180
    if args.apply and not args.force:
        import time
        hot = []
        for fn in sorted({s["file"] for l in spec["lectures"] for s in (l.get("sources") or [])}):
            p = os.path.join(gs_dir, fn)
            if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < QUIET_SECONDS:
                hot.append("%s（%d 秒前改过）" % (fn, int(time.time() - os.path.getmtime(p))))
        if hot:
            print("[中止] 源文件刚被改过，可能有并行的笔记整理会话在写：\n   " + "\n   ".join(hot))
            print("\n等它写完再跑；确认没有并行会话就加 --force。dry-run 不受此闸门限制。")
            return 1

    # 源章文件清单（从 lectures 的 sources 收集）
    source_files = OrderedDict()
    for lec in spec["lectures"]:
        for s in lec.get("sources") or []:
            source_files.setdefault(s["file"], None)
    amap, defaults = build_assign_map(spec["lectures"], list(source_files))

    report = []
    outputs = {}          # 讲号 -> 行列表
    consumed = {}         # 源文件 -> {行号: 次数}（次数 >1 即重叠，必须暴露）
    problems = []

    def mark(fn, rng):
        d = consumed.setdefault(fn, {})
        for i in rng:
            d[i] = d.get(i, 0) + 1

    for fn in source_files:
        path = os.path.join(gs_dir, fn)
        if not os.path.exists(path):
            problems.append("源文件不存在：%s" % fn)
            continue
        lines = read_lines(path)
        m = re.match(r"^第(\d+)章_(.+)\.md$", fn)
        ch_no, ch_name = (int(m.group(1)), m.group(2)) if m else (None, fn)

        h1, pre, blocks, nav = parse_blocks(lines)
        probs = resolve(blocks, amap, defaults[fn], fn)
        problems.extend(probs)

        # 同源兄弟讲（同一个源章拆出来的讲）
        siblings = [l for l in spec["lectures"]
                    if any(s["file"] == fn for s in (l.get("sources") or []))]
        first_lec = min(l["no"] for l in siblings)

        nav_lines = lines[nav[0]:nav[1]] if nav else []
        report.append("=" * 78)
        report.append("源：第%d章 %s（%d 行，%d 个标题块）→ %s" % (
            ch_no, ch_name, len(lines), len(blocks),
            "、".join("第%d讲" % l["no"] for l in siblings)))

        # preamble 归第一个讲
        pre_lines = lines[pre[0]:pre[1]]
        pre_body = [re.sub(r"本章", "原第%d章" % ch_no, x) for x in pre_lines]
        mark(fn, range(pre[0], pre[1]))
        if h1 is not None:
            mark(fn, [h1])
        if nav:
            mark(fn, range(nav[0], nav[1]))

        # 按讲分组（保持文档顺序）；只搬「块自身」，子标题由它自己的块负责
        groups = OrderedDict()
        for b in blocks:
            groups.setdefault(b.lecture, []).append(b)
            mark(fn, range(b.start, b.own_end))

        for lec in siblings:
            no = lec["no"]
            body = header(lec, ch_no, ch_name)
            if no == first_lec:
                body += [x for x in pre_body if x.strip() != "---"] + ["---", ""]
            for b in groups.get(no, []):
                body += provenance(b, ch_no, ch_name)
                body += lines[b.start:b.own_end]
                if body and body[-1].strip() != "":
                    body.append("")
            report.append("   第%-2d讲 %-46s %2d 块 / %4d 行" % (
                no, lec["file"], len(groups.get(no, [])),
                sum(b.nlines for b in groups.get(no, []))))
            body += nav_for(lec, spec, siblings, nav_lines)
            outputs[no] = body

    # 无源的讲：stub / hub / authored
    for lec in spec["lectures"]:
        if lec.get("sources"):
            continue
        # authored = 正文已经由人手写或从别处并入（第7讲物理应用、第17讲并入空间解析几何专题）。
        # 重跑本脚本**绝不能**把它们打回 stub/hub 模板，直接跳过并说明。
        if lec.get("authored"):
            report.append("=" * 78)
            report.append("第%-2d讲 %-46s 跳过（authored：正文已人工建立/并入，不由本脚本生成）"
                          % (lec["no"], lec["file"]))
            continue
        body = header(lec, None, None)
        if lec.get("kind") == "stub":
            g = json.loads(open(os.path.join(SRC, "knowledge_graph", "math_graph.json"),
                                encoding="utf-8").read())
            subs = "、"
            for sub in g["subs"]["高等数学"]["topics"]:
                if sub["id"] == lec["topic_id"]:
                    subs = "、".join(s["name"] for s in sub.get("subtopics", []))
            body += STUB_BODY.format(tid=lec["topic_id"], subs=subs).split("\n")
        elif lec.get("kind") == "hub":
            body += HUB_BODY.split("\n")
        else:
            problems.append("第%d讲 既没有 sources 也没有 kind（stub/hub）" % lec["no"])
            continue
        body += nav_for(lec, spec, [lec], [])
        outputs[lec["no"]] = body
        report.append("=" * 78)
        report.append("第%-2d讲 %-46s 新建（%s，无源章内容）" % (
            lec["no"], lec["file"], lec.get("kind")))

    # ---------------- 内容守恒断言 ----------------
    report.append("")
    report.append("=" * 78)
    report.append("内容守恒核对")
    conserve_ok = True
    for fn in source_files:
        path = os.path.join(gs_dir, fn)
        if not os.path.exists(path):
            continue
        lines = read_lines(path)
        cnt = consumed.get(fn, {})
        missed = [i for i in range(len(lines)) if not cnt.get(i)]
        dup = [i for i in range(len(lines)) if cnt.get(i, 0) > 1]
        # 允许「未被任何块覆盖」的只有：文件尾空白行
        real_missed = [i for i in missed if lines[i].strip() != ""]
        report.append("   %-34s 共 %4d 行 ｜ 未归属 %d（非空 %d）｜ 重复归属 %d" % (
            fn, len(lines), len(missed), len(real_missed), len(dup)))
        if real_missed:
            conserve_ok = False
            for i in real_missed[:5]:
                report.append("        [漏] L%d| %s" % (i + 1, lines[i][:100]))
        if dup:
            conserve_ok = False
            for i in dup[:5]:
                report.append("        [重] L%d ×%d| %s" % (i + 1, cnt[i], lines[i][:100]))

    # 反向核对：讲文件里的正文行数之和 >= 源文件非导航非H1行数
    total_out = sum(len(v) for v in outputs.values())
    report.append("   18 个讲文件合计 %d 行（含新生成的头部与导航）" % total_out)

    if problems:
        report.append("")
        report.append("⚠️ 问题 %d 条：" % len(problems))
        report += ["   - " + p for p in problems]

    report.append("")
    report.append("守恒断言：%s ｜ 问题：%d ｜ 模式：%s" % (
        "通过" if conserve_ok else "未通过", len(problems),
        "APPLY" if args.apply else "DRY-RUN"))

    text = "\n".join(report)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")

    if not conserve_ok or problems:
        print("\n[中止] 守恒断言未通过或映射有问题，未写任何文件。")
        return 1
    if not args.apply:
        print("\n[dry-run] 未写盘。确认无误后加 --apply。")
        return 0

    # ---------------- 落盘 ----------------
    by_no = {l["no"]: l for l in spec["lectures"]}
    written = []
    for no in sorted(outputs):
        lec = by_no[no]
        p = os.path.join(gs_dir, lec["file"])
        if os.path.exists(p):
            old = open(p, encoding="utf-8").read()
            if MIGRATE_MARK not in old:
                print("[中止] %s 已存在且不是本脚本生成的，拒绝覆盖。" % lec["file"])
                return 1
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(outputs[no]).rstrip("\n") + "\n")
        written.append(lec["file"])
    print("\n[apply] 写入 %d 个讲文件。" % len(written))

    if args.delete_old:
        for fn in source_files:
            p = os.path.join(gs_dir, fn)
            if os.path.exists(p):
                os.remove(p)
                print("   删除源章文件：%s" % fn)
    else:
        print("   源章文件保留（对照检查用）。确认无误后再 --delete-old 或手工删。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

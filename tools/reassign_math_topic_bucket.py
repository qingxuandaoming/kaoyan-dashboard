#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reassign_math_topic_bucket.py —— 把「专题」桶里已不再指向任何专题的条目改归到讲。

背景：2026-09-23 两个专题被并入讲笔记后删除（`专题/无穷级数.md` → 第16讲 7.7；
`专题/空间曲面体绘图/空间解析几何专题.md` → 第17讲 整篇）。加上用户更早删掉的
`专题/常微分方程.md`，`chapter: "专题"` 的 8 条高数条目现在**链接全部指向讲文件**，
再留在「专题」桶里就是错的（那个桶的语义是"正文落在 专题/ 下"）。

做法是**定点改归**，不重跑 migrate_math_hub 的全量解析：
全量重解析会把 178 条重新打分，chapter 已是「第N讲」的条目会被当成"无章号"
而在 18 个讲里重新猜一遍，制造无谓的 churn。这里只动判据明确的条目：

  判据：chapter == "专题"，且 links 里**没有任何**路径落在现存的 `专题/` 目录下，
        且能解析出唯一的讲（多讲时取 links[0]，即 hub 主目标）。

同步改两处并保持一致：
  1. `Math/notes_index.json` 的 `chapter`
  2. `Math/高数/高数 notes.md` 的整理记录索引：把该行从「专题」段搬到目标讲段，
     所有段计数重算，顶部总数不变

默认 dry-run，`--apply` 才写盘；写完自检「顶部总数 == 各段之和 == 实际行数」。
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
ROOT = os.environ.get("NOTES_ROOT", r"E:\NPEE")   # 笔记库根（2026-09-25 起与代码根分离，可用环境变量 NOTES_ROOT 覆盖）
MATH = os.path.join(ROOT, "Math")
GS = os.path.join(MATH, "高数")
IDX = os.path.join(MATH, "notes_index.json")
HUB = os.path.join(GS, "高数 notes.md")

SEC_RE = re.compile(r"^#{2,4}\s*(.+?)（(\d+)\s*条）\s*$")
LINE_RE = re.compile(r"^- \*\*(\d{4}-\d{2}-\d{2})\*\* (.*?)\s*$")
TOTAL_RE = re.compile(r"(>\s*共\s*)(\d+)(\s*条已整理记录)")
KEEP = ("专题", "预备知识")
ORDER = ["预备知识"] + ["第%d讲" % n for n in range(1, 19)] + ["专题"]


def read(p):
    with open(p, encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def write(p, t):
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(t)


def norm_link(l):
    if isinstance(l, dict):
        return l.get("text", "") or "", l.get("path", "") or ""
    s = str(l)
    if "|" in s:
        t, _, p = s.partition("|")
        return t.strip(), p.strip()
    return s, s


def resolve_path(p):
    q = str(p or "").lstrip("./")
    if not q:
        return None
    for base in (GS, MATH):
        c = os.path.normpath(os.path.join(base, q))
        if os.path.exists(c):
            return c
    return None


def target_lecture(ent):
    """返回 (讲号, 依据) 或 (None, 原因)。"""
    links = [norm_link(l) for l in (ent.get("links") or [])]
    resolved = [(t, resolve_path(p)) for t, p in links]
    live = [r for _, r in resolved if r]
    if any("专题" in r.replace("\\", "/") for r in live):
        return None, "链接仍指向现存专题，保留在专题桶"
    lecs = []
    for r in live:
        m = re.match(r"^第(\d+)讲_", os.path.basename(r))
        if m:
            n = int(m.group(1))
            if n not in lecs:
                lecs.append(n)
    if not lecs:
        return None, "链接里没有讲文件（可能只指工具文件），不动"
    if len(lecs) == 1:
        return lecs[0], "唯一讲"
    return lecs[0], "多讲(%s)，取 links[0] 主目标" % lecs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    idx = json.loads(read(IDX))
    ents = idx["subjects"]["高数"]["entries"]
    moves = {}          # id -> (讲号, 依据)
    for e in ents:
        # ⚠️ 只动「专题」桶。「预备知识」是公式速查里 基础公式（预备知识） 那一段的
        # 合法非编号桶（四种命题/韦达/三角/反三角/椭圆双曲线），与专题存不存在无关，
        # 不能因为某条恰好也链到某个讲就把它挪走。
        if e.get("chapter") != "专题":
            continue
        no, why = target_lecture(e)
        print("   %-12s chapter=%-6s → %s ｜ %s" % (
            e.get("id"), e.get("chapter"),
            ("第%d讲" % no) if no else "（不动）", why))
        if no:
            moves[e["id"]] = no
    print()
    print("需改归：%d 条" % len(moves))

    # ---------- 中控行搬迁 ----------
    hub = read(HUB)
    lines = hub.split("\n")
    i_idx = next(i for i, l in enumerate(lines) if l.strip() == "## 整理记录索引")
    header = lines[:i_idx]

    sections = OrderedDict()
    cur = None
    idx_preamble = []          # 「## 整理记录索引」标题 + 其下说明行（含「共 N 条」）
    for l in lines[i_idx:]:
        ms = SEC_RE.match(l)
        if ms:
            cur = ms.group(1).strip()
            sections.setdefault(cur, [])
            continue
        if cur is None:
            # ⚠️ 这一段必须**原样保留**：里面有 `## 整理记录索引` 标题与「> 共 N 条…」说明行。
            # 第一版直接 continue 丢掉，结果 notes_entry --check 读不到顶部总数（自检报 顶部=None）。
            idx_preamble.append(l)
            continue
        if LINE_RE.match(l):
            sections[cur].append(l)

    by_title = {}
    for e in ents:
        by_title.setdefault(e.get("title", "").strip(), e.get("id"))

    moved, orphan = 0, []
    moved_ids = set()
    for sec in list(sections):
        keep = []
        for l in sections[sec]:
            title = LINE_RE.match(l).group(2).split(" → [")[0].strip()
            eid = by_title.get(title)
            if eid in moves and sec != ("第%d讲" % moves[eid]):
                sections.setdefault("第%d讲" % moves[eid], []).append(l)
                moved += 1
                moved_ids.add(eid)
            else:
                keep.append(l)
        sections[sec] = keep
    for eid, no in moves.items():
        if not any(("第%d讲" % no) == s for s in sections):
            orphan.append(eid)
    print("中控行搬迁：%d 行%s" % (moved, "" if not orphan else "，⚠️ 目标段缺失：%s" % orphan))
    # 中控「专题」段的行数与索引的专题条目数**本来就对不上**（既有漂移，27 行 vs 8 条），
    # 所以有些条目在中控里找不到同名行。如实报出来，不假装全搬了。
    nomatch = [e for e in moves if e not in moved_ids]
    if nomatch:
        print("   ⚠️ 索引改归了、但中控里没有同名行可搬（既有漂移）：%s" % nomatch)

    if not args.apply:
        print("\n[dry-run] 未写盘。")
        return 0

    # ---------- 写 index ----------
    n = 0
    for e in ents:
        if e.get("id") in moves:
            e["chapter"] = "第%d讲" % moves[e["id"]]
            n += 1
    write(IDX, json.dumps(idx, ensure_ascii=False, indent=2) + "\n")
    json.loads(read(IDX))
    print("[apply] notes_index.json 改归 %d 条。" % n)

    # ---------- 写中控 ----------
    out = list(header)
    total = sum(len(v) for v in sections.values())
    out += idx_preamble
    for sec in ORDER:
        if sec not in sections:
            continue
        rows = sections[sec]
        out += ["### %s（%d 条）" % (sec, len(rows)), ""] + rows + [""]
    for sec in sections:
        if sec in ORDER:
            continue
        rows = sections[sec]
        out += ["### %s（%d 条）" % (sec, len(rows)), ""] + rows + [""]
    text = "\n".join(out).rstrip("\n") + "\n"
    text = TOTAL_RE.sub(lambda m: "%s%d%s" % (m.group(1), total, m.group(3)), text, count=1)
    write(HUB, text)

    txt = read(HUB)
    declared = sum(int(m.group(2)) for m in (SEC_RE.match(l) for l in txt.split("\n")) if m)
    actual = sum(1 for l in txt.split("\n") if LINE_RE.match(l))
    mt = TOTAL_RE.search(txt)
    top = int(mt.group(2)) if mt else None
    ok = top == declared == actual == total
    print("[apply] 中控已重排。自检：顶部=%s 各段之和=%d 实际行数=%d 预期=%d → %s"
          % (top, declared, actual, total, "OK" if ok else "✗ 漂移"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

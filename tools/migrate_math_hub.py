#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrate_math_hub.py —— 高数「章 → 讲」迁移的第 5 步：索引与中控。

做两件事：

1. `Math/notes_index.json`：高数条目的 `chapter` 从「第N章」改成「第N讲」。
   `专题` / `预备知识` 两个非编号桶原样保留（它们本来就不属于任何讲）。
2. `Math/高数/高数 notes.md`（中控）：章节笔记表 8 行 → 18 行、相关专题的「对应章节」
   列换成讲、整理记录索引按讲重排并重算计数、顶部总数与日期区间同步。

条目 → 讲 怎么定（关键：**先框定范围，再在范围内打分**）
------------------------------------------------------
条目的 `chapter` 已经告诉我们它属于哪一章，而一章对应哪几个讲是确定的
（第3章 → 第8–12讲）。所以候选集最多 5 个讲，不会全库乱猜：

  P1 链接路径已是某个讲文件，且链接文字的锚点能在**那个讲**的标题里找到 → 确认
  P2 锚点在**同章别的讲**的标题里找到（路径只是章首讲兜底）→ 用锚点命中的讲
  P3 链接路径是讲文件、锚点匹配不上 → 用路径里的讲
  P4 在本章的候选讲里，用**正文内容**给条目标题打分（IDF 加权：只在少数讲出现的词
     才有区分力），最高分显著领先 → 用它
  P5 打分也分不出来 → 退回章首讲，并在报告里单列，人工过一眼

⚠️ 为什么不按 title 精确对齐中控行：实测中控行标题与 notes_index 的 title 有 85/169
   对不上（中控那份往往更长、带补注）。所以**中控行独立解析**（用它自己的链接路径 +
   自己所在的章），不依赖与条目一一对应。计数由行本身重算，自检天然成立。
"""
import argparse
import io
import json
import math
import os
import re
import sys
from collections import OrderedDict, Counter

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
SPEC = os.path.join(SRC, "math_lectures.json")
IDX = os.path.join(MATH, "notes_index.json")
HUB = os.path.join(GS, "高数 notes.md")

KEEP_BUCKETS = ("专题", "预备知识")
HEAD_RE = re.compile(r"^(#{2,6})\s+(.*?)\s*$")
LINE_RE = re.compile(r"^- \*\*(\d{4}-\d{2}-\d{2})\*\* (.*?)\s*$")
SEC_RE = re.compile(r"^#{2,4}\s*(.+?)（(\d+)\s*条）\s*$")
TOTAL_RE = re.compile(r"(>\s*共\s*)(\d+)(\s*条已整理记录)")
RANGE_RE = re.compile(r"（(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})）")
LINK_RE = re.compile(r"\[([^\]\n]*)\]\(([^)\n]*)\)")


def read(p):
    with open(p, encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def write(p, t):
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(t)


def norm(s):
    s = re.sub(r"\$[^$]*\$", " ", str(s or ""))
    s = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", s)
    return s.lower()


def tokens(s):
    """中日韩文字取二元组，字母数字取整词。LaTeX 先剥掉。"""
    s = re.sub(r"\$[^$]*\$", " ", str(s or ""))
    out = []
    for w in re.findall(r"[A-Za-z0-9]+", s):
        if len(w) > 1:
            out.append(w.lower())
    for run in re.findall(r"[\u4e00-\u9fff]+", s):
        if len(run) == 1:
            out.append(run)
        else:
            out += [run[i:i + 2] for i in range(len(run) - 1)]
    return out


def norm_link(l):
    if isinstance(l, dict):
        return l.get("text", "") or "", l.get("path", "") or ""
    s = str(l)
    if "|" in s:
        t, _, p = s.partition("|")
        return t.strip(), p.strip()
    return s, s


class Resolver(object):
    def __init__(self, spec):
        self.lec_by_no = {l["no"]: l for l in spec["lectures"]}
        self.file2no = {l["file"]: l["no"] for l in spec["lectures"]}
        self.ch2lec = {}
        for l in spec["lectures"]:
            for s in l.get("sources") or []:
                m = re.match(r"^第(\d+)章_", s["file"])
                if m:
                    self.ch2lec.setdefault(int(m.group(1)), []).append(l["no"])
        self.ch2lec = {k: sorted(v) for k, v in self.ch2lec.items()}
        self.text, self.heads = {}, {}
        for no, l in self.lec_by_no.items():
            p = os.path.join(GS, l["file"])
            t = read(p) if os.path.exists(p) else ""
            self.text[no] = t
            self.heads[no] = [norm(m.group(2)) for m in
                              (HEAD_RE.match(x) for x in t.split("\n")) if m]
        # IDF：一个词在几个讲里出现过
        df = Counter()
        tokset = {no: set(tokens(t)) for no, t in self.text.items()}
        for s in tokset.values():
            df.update(s)
        self.N = max(1, len(tokset))
        self.idf = {w: math.log(1.0 + self.N / (1 + c)) for w, c in df.items()}
        self.tokset = tokset

    def chapter_of(self, s):
        m = re.search(r"第(\d+)章", str(s or ""))
        return int(m.group(1)) if m else None

    def candidates(self, ch):
        return self.ch2lec.get(ch, [])

    def find_heading(self, needle, restrict=None):
        n = norm(re.sub(r"^\d+(\.\d+)*\s*", "", needle or ""))
        if len(n) < 4:
            return None
        hits = set()
        for no, hs in self.heads.items():
            if restrict and no not in restrict:
                continue
            for h in hs:
                if h and (n in h or h in n):
                    hits.add(no)
        return hits.pop() if len(hits) == 1 else None

    def score(self, query, cands):
        """IDF 加权的内容打分，返回 [(讲号, 分)] 降序。"""
        q = set(tokens(query))
        if not q:
            return []
        out = []
        for no in cands:
            ts = self.tokset.get(no, set())
            s = sum(self.idf.get(w, 0.0) for w in q if w in ts)
            out.append((no, s))
        out.sort(key=lambda x: -x[1])
        return out

    def resolve_entry(self, ent):
        ch_field = ent.get("chapter", "")
        if ch_field in KEEP_BUCKETS:
            return ch_field, "非编号桶", None
        ch = self.chapter_of(ch_field)
        cands = self.candidates(ch) if ch else list(self.lec_by_no)
        links = [norm_link(l) for l in (ent.get("links") or [])]
        path_no, anchor = None, ""
        for text, path in links:
            base = os.path.basename(path.split("#")[0])
            if base in self.file2no:
                path_no = self.file2no[base]
            if not anchor:
                anchor = text.split(">", 1)[1].strip() if ">" in text else text
        if anchor:
            hit = self.find_heading(anchor, restrict=set(cands) or None)
            if hit is not None:
                if path_no is not None and hit != path_no:
                    return hit, "P2 锚点标题命中", ch
                return hit, "P1 路径+锚点双确认", ch
        if path_no is not None and path_no in (cands or [path_no]):
            return path_no, "P3 链接路径", ch
        q = "%s %s" % (ent.get("title", ""), anchor)
        sc = self.score(q, cands)
        if sc and sc[0][1] > 0 and (len(sc) == 1 or sc[0][1] >= sc[1][1] * 1.25 + 0.5):
            return sc[0][0], "P4 正文打分", ch
        if cands:
            return cands[0], "P5 退回章首讲", ch
        return ch_field, "P5 无法归属", ch

    def resolve_hub_line(self, body, cur_section):
        """中控行独立解析：自己的链接路径优先，其次在本章候选里打分。"""
        if cur_section in KEEP_BUCKETS:
            return cur_section, "非编号桶"
        m = LINK_RE.search(body)
        if m:
            base = os.path.basename(m.group(2).split("#")[0])
            if base in self.file2no:
                return self.file2no[base], "行内链接路径"
        ch = self.chapter_of(cur_section)
        cands = self.candidates(ch) if ch else list(self.lec_by_no)
        title = body.split(" → [")[0].strip()
        anchor = ""
        if m and ">" in m.group(1):
            anchor = m.group(1).split(">", 1)[1].strip()
        hit = self.find_heading(anchor or title, restrict=set(cands) or None)
        if hit is not None:
            return hit, "行内锚点标题"
        sc = self.score("%s %s" % (title, anchor), cands)
        if sc and sc[0][1] > 0 and (len(sc) == 1 or sc[0][1] >= sc[1][1] * 1.25 + 0.5):
            return sc[0][0], "行内正文打分"
        if cands:
            return cands[0], "退回章首讲"
        return cur_section, "无法归属"


def main():
    ap = argparse.ArgumentParser(description="高数 notes_index + 中控 的 章→讲 迁移")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", help="把报告写到这个文件")
    args = ap.parse_args()

    spec = json.loads(read(SPEC))
    R = Resolver(spec)
    idx = json.loads(read(IDX))
    ents = idx["subjects"]["高数"]["entries"]

    plan, methods = {}, Counter()
    for e in ents:
        no, why, ch = R.resolve_entry(e)
        plan[e["id"]] = (no, why, ch)
        methods[why.split(" ")[0]] += 1

    rep = ["== 条目 → 讲 解析（高数 %d 条）==" % len(ents), ""]
    for k, v in methods.most_common():
        rep.append("   %-4s %3d 条" % (k, v))

    dist = Counter(plan[e["id"]][0] for e in ents)
    order = ["预备知识"] + list(range(1, 19)) + ["专题"]
    rep += ["", "== notes_index 新分组分布 =="]
    for k in order:
        if dist.get(k):
            lab = ("第%d讲 %s" % (k, R.lec_by_no[k]["name"])) if isinstance(k, int) else k
            rep.append("   %-46s %3d 条" % (lab, dist[k]))
    extra = {k: v for k, v in dist.items() if k not in order}
    if extra:
        rep.append("   ⚠️ 计划外的桶：%s" % extra)
    rep.append("   合计 %d 条" % sum(dist.values()))

    fb = [(e["id"], e.get("title", ""), plan[e["id"]][0]) for e in ents
          if plan[e["id"]][1].startswith("P5")]
    rep += ["", "== P5 兜底（%d 条，建议抽查）==" % len(fb)]
    for i, t, no in fb[:40]:
        rep.append("   %-12s → 第%-3s讲 %s" % (i, no, t[:74]))

    # ---- 中控行解析 ----
    hub = read(HUB)
    lines = hub.split("\n")
    cur, marks = None, {}
    for i, l in enumerate(lines):
        ms = SEC_RE.match(l)
        if ms:
            cur = ms.group(1).strip()
        elif LINE_RE.match(l):
            marks[i] = (cur, LINE_RE.match(l).group(1), LINE_RE.match(l).group(2))
    hmethods = Counter()
    hdist = Counter()
    for i, (sec, dt, body) in marks.items():
        no, why = R.resolve_hub_line(body, sec or "")
        marks[i] = (sec, dt, body, no)
        hmethods[why] += 1
        hdist[no] += 1
    rep += ["", "== 中控索引行（%d 行）==" % len(marks), ""]
    for k, v in hmethods.most_common():
        rep.append("   %-16s %3d 行" % (k, v))
    rep.append("")
    for k in order:
        if hdist.get(k):
            lab = ("第%d讲" % k) if isinstance(k, int) else k
            rep.append("   %-14s %3d 行   （索引 %3d 条）" % (lab, hdist[k], dist.get(k, 0)))

    text = "\n".join(rep)
    print(text)
    if args.out:
        write(args.out, text + "\n")
    if not args.apply:
        print("\n[dry-run] 未写盘。")
        return 0

    # ================= 写 notes_index.json =================
    for e in ents:
        no = plan[e["id"]][0]
        e["chapter"] = ("第%d讲" % no) if isinstance(no, int) else no
    write(IDX, json.dumps(idx, ensure_ascii=False, indent=2) + "\n")
    print("\n[apply] notes_index.json：高数 %d 条 chapter 已改为讲。" % len(ents))

    # ================= 重建中控 =================
    i_idx = next(i for i, l in enumerate(lines) if l.strip() == "## 整理记录索引")
    i_chap = next((i for i, l in enumerate(lines) if l.strip() == "## 章节笔记"), None)
    i_tool = next((i for i, l in enumerate(lines) if l.strip() == "## 工具与跨科"), None)
    i_topic = next((i for i, l in enumerate(lines) if l.strip() == "## 相关专题"), None)

    grouped = OrderedDict()
    for i in sorted(marks):
        grouped.setdefault(marks[i][3], []).append(lines[i])

    new = list(lines[:i_chap]) if i_chap is not None else list(lines[:i_idx])
    new += ["## 章节笔记", "",
            "> 高数笔记已按**张宇《高数18讲》**拆分（2026-09-22）：一个讲一个文件，",
            "> 讲号与知识图谱 `MATH-GS-01..18` 一一对应，与大盘「差距分析 / 每日任务」的考点号对齐。",
            "> 线代与概率论仍按教材章（`第N章_…md`），它们的图谱考点靠 `note_chapter` 换算。", "",
            "| 讲 | 笔记 | 图谱考点 | 公式 | 陷阱 | 错题 |",
            "|----|------|---------|------|------|------|"]
    for no in sorted(R.lec_by_no):
        l = R.lec_by_no[no]
        new.append("| 第%d讲 | [%s](./%s) | %s | [公式](./公式速查.md) | [陷阱](./计算陷阱.md) | [错题](./错题归档.md) |"
                   % (no, l["name"], l["file"], l["topic_id"]))
    new.append("")
    if i_tool is not None:
        end = i_topic if (i_topic is not None and i_topic > i_tool) else i_idx
        new += lines[i_tool:end]
    if i_topic is not None and i_topic < i_idx:
        for l in lines[i_topic:i_idx]:
            for n in sorted(R.ch2lec, reverse=True):
                lecs = R.ch2lec[n]
                lab = "第%d讲" % lecs[0] if len(lecs) == 1 else "第%d–%d讲" % (lecs[0], lecs[-1])
                l = re.sub(r"第%d章(?![\u4e00-\u9fffA-Za-z0-9_]*\.md)" % n, lab, l)
            new.append(l)
    dates = [marks[i][1] for i in marks]
    new += ["## 整理记录索引", "",
            "> 共 %d 条已整理记录（%s ~ %s），按**张宇18讲的讲次**分组；" % (
                len(marks), min(dates), max(dates)),
            "> 首组「预备知识」与末组「专题」是非编号桶（正文分别落在 `公式速查.md` 与 `专题/`）。", ""]
    for g in order:
        rows = grouped.get(g)
        # 讲 1..18 一律出段，**包括 0 条的**：中控因此成为一张完整的 18 讲清单，
        # 哪一讲还没归档过条目一眼可见（也让 notes_entry.py 之后能直接往任何讲追加）。
        if isinstance(g, int) and not rows:
            rows = []
        elif not rows:
            continue
        lab = ("第%d讲" % g) if isinstance(g, int) else g
        new += ["### %s（%d 条）" % (lab, len(rows)), ""] + rows + [""]
    for g in [k for k in grouped if k not in order]:
        rows = grouped[g]
        new += ["### %s（%d 条）" % (g, len(rows)), ""] + rows + [""]

    write(HUB, "\n".join(new).rstrip("\n") + "\n")

    txt = read(HUB)
    declared = sum(int(m.group(2)) for m in (SEC_RE.match(l) for l in txt.split("\n")) if m)
    actual = sum(1 for l in txt.split("\n") if LINE_RE.match(l))
    mt = TOTAL_RE.search(txt)
    top = int(mt.group(2)) if mt else None
    ok = (top == declared == actual)
    print("[apply] 中控已重建。自检：顶部总数=%s 各段之和=%d 实际行数=%d → %s" % (
        top, declared, actual, "OK" if ok else "✗ 漂移"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

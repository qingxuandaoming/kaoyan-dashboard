#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rewrite_math_chapter_links.py —— 高数「章 → 讲」迁移后的引用改写器。

配套 `migrate_math_lectures.py`：拆分只负责搬正文，所有**指向旧章文件的引用**由本脚本
统一改写。映射同样只读 `src/math_lectures.json`，不含硬编码对应关系。

⚠️ 链接是**一个整体**：`[文字](路径)` 的文字与路径必须同时改、且指向同一个讲。
第一版把两者分开处理（路径统一指向「章首讲」、文字按节号精确换算），结果出现
「文字写第6讲、路径却指第3讲」的错链——点击会打开错的文件。现在按链接单元统一解析。

节号解析（从严）
--------------
只有**确实存在于映射表里的节号**才用来定位讲。像「张宇例 10.6」「1000题 14.12」这种
书内编号会被 `\d+\.\d+` 匹配上，但不在表里，就退回「章首讲」并把文字写成讲区间
（`第8–12讲`），不假装精确。

四条规则
--------
A. **链接单元**：`[文字](路径)`，路径含高数旧章文件名 → 按节号定位讲，文字与路径一起改。
B. **裸文件名**：不在链接里的 `第N章_xxx.md` → 章首讲文件名。
C. **分组标题**：`高数/公式速查.md`、`高数/错题归档.md` 的 `## 第N章 X` → `## 第A–B讲 X`。
   线代/概率的同名标题**不动**。
D. **散文提及**：只认「高数/第N章」前缀式，与紧跟高数章名的写法。

按完整文件名匹配，所以线代 `第5章_特征值与特征向量.md`、概率 `第2章_随机变量及其分布.md`
绝不会被误伤。一律跳过：迁移生成的头部与出处行、代码块内、stub/hub 讲文件的新写正文。

默认 dry-run，`--apply` 才写盘。
"""
import argparse
import io
import json
import os
import re
import sys

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
ROOT = os.environ.get("NOTES_ROOT", r"E:\NPEE")   # 笔记库根（2026-09-25 起与代码根分离，可用环境变量 NOTES_ROOT 覆盖）
SPEC = os.path.join(SRC, "math_lectures.json")

GEN_LINE = (re.compile(r"^#\s+第\d+讲\s"), re.compile(r"^>\s*张宇《高数18讲》"),
            re.compile(r"^>\s*本文件由 tools/"), re.compile(r"^<!--\s*migrated-by:"),
            re.compile(r"^>\s*📎\s*出自原第\d+章"))
LINK_RE = re.compile(r"\[([^\]\n]*)\]\(([^)\n]*)\)")
SEC_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def load_map():
    spec = json.loads(read(SPEC))
    file_map, ch2lec, ch_name, sec2lec = {}, {}, {}, {}
    for lec in spec["lectures"]:
        for s in lec.get("sources") or []:
            fn = s["file"]
            m = re.match(r"^第(\d+)章_(.+)\.md$", fn)
            if not m:
                continue
            n = int(m.group(1))
            ch_name[n] = m.group(2)
            ch2lec.setdefault(n, []).append(lec["no"])
            file_map.setdefault(fn, n)
            for h in s.get("headings") or []:
                sm = re.match(r"^(\d+(?:\.\d+)*[a-z]?)\s", h.strip())
                if sm:
                    sec2lec.setdefault(n, {})[sm.group(1)] = lec["no"]
    ch2lec = {n: sorted(v) for n, v in ch2lec.items()}
    primary = {n: v[0] for n, v in ch2lec.items()}
    lec_by_no = {l["no"]: l for l in spec["lectures"]}
    return {
        "spec": spec,
        "file_map": {fn: lec_by_no[primary[n]]["file"] for fn, n in file_map.items()},
        "file_ch": {fn: n for fn, n in file_map.items()},
        "ch2lec": ch2lec,
        "primary": primary,
        "sec2lec": sec2lec,
        "ch_name": ch_name,
        "lec_by_no": lec_by_no,
    }


def label(n, ch2lec):
    lecs = ch2lec.get(n) or []
    if not lecs:
        return "第%d章" % n
    return "第%d讲" % lecs[0] if len(lecs) == 1 else "第%d–%d讲" % (lecs[0], lecs[-1])


class Rewriter(object):
    def __init__(self, M):
        self.M = M
        self.n_link = 0
        self.n_bare = 0
        self.n_head = 0
        self.n_prose = 0
        self.detail = []

    # ---- 节号 → 讲（只认表里真有的节号）----
    def resolve(self, text, n):
        """返回 (讲号, 是否按节号精确定位)。"""
        table = self.M["sec2lec"].get(n, {})
        for cand in SEC_RE.findall(text or ""):
            if cand in table:
                return table[cand], True
            parent = ".".join(cand.split(".")[:2])
            if parent in table:
                return table[parent], True
            top = cand.split(".")[0]
            # 「3.1.10」的父节写作「3.1」；也允许只写章内一级号（如 2.7 → 讲6）
            for k, v in table.items():
                if k.split(".")[0] == top and "." not in k:
                    return v, True
        return self.M["primary"].get(n), False

    # ---- A：链接单元 ----
    def links(self, line):
        M = self.M

        def fix(m):
            inner, target = m.group(1), m.group(2)
            hit = next((fn for fn in M["file_map"] if fn in target), None)
            if not hit:
                return m.group(0)
            n = M["file_ch"][hit]
            lec, precise = self.resolve(inner, n)
            if lec is None:
                return m.group(0)
            new_target = target.replace(hit, M["lec_by_no"][lec]["file"])
            new_inner = re.sub(r"第%d章" % n,
                               ("第%d讲" % lec) if precise else label(n, M["ch2lec"]),
                               inner)
            self.n_link += 1
            if precise:
                self.detail.append("   [节号] %s → 第%d讲 ｜ %s" % (hit, lec, inner[:70]))
            return "[%s](%s)" % (new_inner, new_target)

        return LINK_RE.sub(fix, line)

    # ---- B：裸文件名 ----
    def bare(self, line):
        for old, new in self.M["file_map"].items():
            if old in line:
                self.n_bare += line.count(old)
                line = line.replace(old, new)
        return line

    # ---- C：分组标题 ----
    def heads(self, line):
        def fix(m):
            hashes, n, rest = m.group(1), int(m.group(2)), m.group(3)
            if n not in self.M["ch2lec"]:
                return m.group(0)
            self.n_head += 1
            return "%s %s%s" % (hashes, label(n, self.M["ch2lec"]), rest)
        return re.sub(r"^(#{2,4})\s*第(\d+)章([^\n]*)$", fix, line)

    # ---- D：散文提及 ----
    def prose(self, line):
        def fix(m):
            n = int(m.group(2))
            if n not in self.M["ch2lec"]:
                return m.group(0)
            self.n_prose += 1
            return "%s%s" % (m.group(1), label(n, self.M["ch2lec"]))
        line = re.sub(r"(高数\s*/\s*)第(\d+)章", fix, line)
        for n, nm in self.M["ch_name"].items():
            if n not in self.M["ch2lec"]:
                continue

            def fix2(m, n=n):
                self.n_prose += 1
                return label(n, self.M["ch2lec"])
            line = re.sub(r"第%d章(?=\s*%s)" % (n, re.escape(nm[:4])), fix2, line)
        return line

    # ---- 字符串形态的链接（notes_index.json 的 links 里混着 dict 与裸字符串）----
    def fix_str(self, s):
        """`"text|path"` 或直接一个带锚点的路径。

        ⚠️ 实测 notes_index.json 的 links 里高数 243 个 dict + 9 个裸字符串、线代 63 + 5。
        第一版只处理 dict，结果 5 条字符串链接漏改（都指向 第3章_一元函数积分学.md），
        直到审计才被抓出来。字符串里也可能带节号（`…第3章_….md#3.1.3`），一样按节号定位讲。
        """
        hit = next((fn for fn in self.M["file_map"] if fn in s), None)
        if not hit:
            return s
        n = self.M["file_ch"][hit]
        lec, precise = self.resolve(s, n)
        if lec is None:
            return self.bare(s)
        out = s.replace(hit, self.M["lec_by_no"][lec]["file"])
        out = re.sub(r"第%d章" % n,
                     ("第%d讲" % lec) if precise else label(n, self.M["ch2lec"]), out)
        self.n_link += 1
        if precise:
            self.detail.append("   [节号·索引串] %s → 第%d讲 ｜ %s" % (hit, lec, s[:64]))
        return out


def process_json(obj, rw):
    """notes_index.json 专用：链接的 text 与 path 是**分离字段**，必须一起改。

    条目形如 `{"text": "第3章_一元函数积分学.md > 3.9", "path": "./第3章_一元函数积分学.md"}`。
    按 text 里的节号定位讲（与 markdown 链接同一套解析），再同时改写 text 与 path。
    ⚠️ links 里还混着**裸字符串**形态，一并走 rw.fix_str()（第一版漏了这一支）。
    """
    if isinstance(obj, dict):
        path = obj.get("path")
        if isinstance(path, str):
            hit = next((fn for fn in rw.M["file_map"] if fn in path), None)
            if hit:
                n = rw.M["file_ch"][hit]
                text = obj.get("text") if isinstance(obj.get("text"), str) else ""
                lec, precise = rw.resolve(text or path, n)
                if lec is not None:
                    newfn = rw.M["lec_by_no"][lec]["file"]
                    obj["path"] = path.replace(hit, newfn)
                    if isinstance(obj.get("text"), str):
                        t = obj["text"].replace(hit, newfn)
                        obj["text"] = re.sub(
                            r"第%d章" % n,
                            ("第%d讲" % lec) if precise else label(n, rw.M["ch2lec"]), t)
                    rw.n_link += 1
                    if precise:
                        rw.detail.append("   [节号·索引] %s → 第%d讲 ｜ %s" % (hit, lec, text[:60]))
        for k, v in list(obj.items()):
            if k == "path":
                continue
            if isinstance(v, str):
                obj[k] = rw.fix_str(v)
            else:
                process_json(v, rw)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                obj[i] = rw.fix_str(v)
            else:
                process_json(v, rw)
    return obj


def process_text(text, rw, rules):
    out, infence = [], False
    for ln in text.split("\n"):
        if ln.strip().startswith("```"):
            infence = not infence
            out.append(ln)
            continue
        if infence or any(p.match(ln) for p in GEN_LINE):
            out.append(ln)
            continue
        new = ln
        if "A" in rules:
            new = rw.links(new)
        if "B" in rules:
            new = rw.bare(new)
        if "C" in rules:
            new = rw.heads(new)
        if "D" in rules:
            new = rw.prose(new)
        out.append(new)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="高数 章→讲 引用改写器")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", help="把报告写到这个文件")
    ap.add_argument("--show-detail", action="store_true", help="打印按节号精确定位的明细")
    args = ap.parse_args()

    M = load_map()
    print("== 文件名映射（8 个高数旧章 → 章首讲）==")
    for old in sorted(M["file_map"], key=lambda x: M["file_ch"][x]):
        n = M["file_ch"][old]
        print("   %-34s → %-46s %s" % (old, M["file_map"][old], label(n, M["ch2lec"])))
    print()
    print("== 节号 → 讲（链接文字里带这些节号时精确定位）==")

    def _key(kv):
        parts = []
        for x in kv[0].split("."):
            m = re.match(r"^(\d+)([a-z]*)$", x)
            parts.append((int(m.group(1)), m.group(2)) if m else (999, x))
        return parts
    for n in sorted(M["sec2lec"]):
        items = sorted(M["sec2lec"][n].items(), key=_key)
        print("   第%d章：%s" % (n, "  ".join("%s→讲%d" % (k, v) for k, v in items)))
    print()

    math = os.path.join(ROOT, "Math")
    generated = {l["file"] for l in M["lec_by_no"].values() if l.get("kind") in ("stub", "hub")}
    targets = []
    for dirpath, dirnames, filenames in os.walk(math):
        dirnames[:] = [d for d in dirnames if d not in {"assets", "PDF", ".qoder", ".git"}]
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            sub = rel.split("/")[1] if rel.startswith("Math/") else ""
            if fn in generated:
                rules = "AB"                    # 我新写的说明文字，别改它的历史叙述
            elif sub == "高数":
                rules = "ABD" + ("C" if fn in ("公式速查.md", "错题归档.md") else "")
                if fn == "高数 notes.md":
                    rules = "AB"                # 中控的表格与分组由 migrate_math_hub.py 重建
            elif sub in ("专题", "跨科综合"):
                rules = "ABD"
            else:
                rules = "AB"                    # 线代/概率：只改指向高数的链接
            targets.append((p, rel, rules))
    # 索引与每日回顾：JSON，没有 markdown 链接语法，走各自的通道
    targets.append((os.path.join(math, "notes_index.json"), "Math/notes_index.json", "J"))
    targets.append((os.path.join(SRC, "morning_review.json"), "src/morning_review.json", "B"))

    rows, changed, detail = [], 0, []
    for p, rel, rules in targets:
        if not os.path.exists(p):
            continue
        old = read(p)
        rw = Rewriter(M)
        if rules == "J":
            data = json.loads(old)
            process_json(data, rw)
            new = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        elif rel.endswith(".json"):
            new = rw.bare(old)
        else:
            new = process_text(old, rw, rules)
        n = rw.n_link + rw.n_bare + rw.n_head + rw.n_prose
        if new != old:
            changed += 1
            rows.append("   %-52s 链接%-4d 裸名%-4d 标题%-3d 散文%-3d  [%s]" % (
                rel, rw.n_link, rw.n_bare, rw.n_head, rw.n_prose, rules))
            detail += rw.detail
            if args.apply:
                write(p, new)
                if rel.endswith(".json"):
                    json.loads(read(p))     # 写坏了立刻炸，不留半成品

    print("== 改动文件（%d 个）==" % changed)
    print("\n".join(rows) if rows else "   （无）")
    if args.show_detail and detail:
        print()
        print("== 按节号精确定位的链接（%d 条）==" % len(detail))
        print("\n".join(detail[:60]))
    print()
    print("模式：%s" % ("APPLY" if args.apply else "DRY-RUN"))
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(rows) + "\n\n" + "\n".join(detail) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

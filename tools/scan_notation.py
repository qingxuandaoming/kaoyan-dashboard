#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scan_notation.py — 扫描题库里"写了但渲染不出来"的数学记号。

只读不改。判定标准：出现在 $...$ 之外的 _下标 / ^上标 写法
（_(x)、_D、_j、^(n) 等）—— richText() 只认 $...$ 与带 \命令的裸 LaTeX，
这些写法会原样显示成 `F_(x)`、`∬_D`。

用法: python tools/scan_notation.py
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = Path(__file__).resolve().parent.parent / "question_bank.db"

SUB = re.compile(r"_(\([^)]*\)|\{[^}]*\}|[A-Za-z0-9]{1,3})(?![_\w])")
SUP = re.compile(r"\^(\([^)]*\)|\{[^}]*\}|[A-Za-z0-9]{1,3})")
LATEX = re.compile(r"\$\$.*?\$\$|\$[^$]*?\$", re.S)


def fields(ct):
    for k in ("stem", "explanation", "answer", "cloze"):
        if isinstance(ct.get(k), str):
            yield k, ct[k]
    for k in ("options", "traps"):
        for x in ct.get(k) or []:
            if isinstance(x, str):
                yield k, x


def main():
    conn = sqlite3.connect(DB)
    hits = []
    for qid, raw in conn.execute("SELECT id, content FROM questions"):
        try:
            ct = json.loads(raw)
        except Exception:
            continue
        for key, val in fields(ct):
            # 填空线 ____ 与已包裹的公式不算问题
            masked = re.sub(r"_{2,}", lambda m: " " * len(m.group(0)), val)
            masked = LATEX.sub(lambda m: " " * len(m.group(0)), masked)
            for pat, kind in ((SUB, "下标"), (SUP, "上标")):
                for m in pat.finditer(masked):
                    ctx = val[max(0, m.start() - 16):m.start() + 20].replace("\n", " ")
                    hits.append((qid, key, kind, m.group(0), ctx))

    by_card = {}
    for h in hits:
        by_card.setdefault(h[0], []).append(h)
    print(f"命中 {len(hits)} 处，涉及 {len(by_card)} 张卡\n")

    def shape(tok):
        if tok.startswith("_("):
            return "_(...) 括号式下标"
        if tok.startswith("^("):
            return "^(...) 括号式上标"
        return ("_单词式下标" if tok[0] == "_" else "^单词式上标")

    tally = {}
    for h in hits:
        tally[shape(h[3])] = tally.get(shape(h[3]), 0) + 1
    print("按写法分类：")
    for k, v in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {k:22} {v}")
    print()
    for qid, hs in by_card.items():
        print(f"{qid}")
        for _, key, kind, tok, ctx in hs[:6]:
            print(f"   [{key}] {kind} {tok!r}  …{ctx}…")
    conn.close()
    return len(by_card)


if __name__ == "__main__":
    main()

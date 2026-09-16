# -*- coding: utf-8 -*-
"""demote_h1_20260803.py —— 遗留汇总文件：保留首个 h1，其余 h1 降为 h2。"""
import io
import pathlib
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = pathlib.Path(r"C:\Users\92534\Desktop\考研")
files = [
    r"408\DS\数据结构\数据结构Markdown版.md",
    r"English\grammar\考研英语语法笔记.md",
    r"English\word&phrase\固定搭配&短语&俚语\词组积累.md",
    r"Politics\史纲\考研政治·中国近现代史纲要知识点整理.md",
    r"Politics\史纲\考研政治核心知识点整理（对话考点汇总）.md",
]
FENCE = re.compile(r"^(`{3,}|~{3,})")
for rel in files:
    p = ROOT / rel
    lines = p.read_text(encoding="utf-8").split("\n")
    in_fence, fence_ch, h1_seen, n = False, "", False, 0
    for i, l in enumerate(lines):
        m = FENCE.match(l)
        if m:
            if not in_fence:
                in_fence, fence_ch = True, m.group(1)[0]
            elif m.group(1)[0] == fence_ch:
                in_fence = False
            continue
        if in_fence:
            continue
        if l.startswith("# "):
            if h1_seen:
                lines[i] = "## " + l[2:]
                n += 1
            else:
                h1_seen = True
    if n:
        p.write_text("\n".join(lines), encoding="utf-8")
        print(f"[OK] {rel}: 降级 {n} 个 h1")
    else:
        print(f"[SKIP] {rel}")

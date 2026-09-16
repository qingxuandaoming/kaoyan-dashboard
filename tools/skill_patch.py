#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""skill_patch.py — 精确修改工作区外的 skill 文件（SKILL.md 等）。

专用编辑工具只能改工作区内文件，而 skill 位于 %USERPROFILE%\.qoderworkcn\skills，
故用本脚本做带校验的字符串替换。

用法：
    python src/tools/skill_patch.py <spec.py>

spec.py 格式（用 raw string 原生写 Windows 路径，免去转义）：
    EDITS = [
        {
            "file": r"C:/Users/92534/.qoderworkcn/skills/xxx/SKILL.md",
            "replacements": [
                {"old": r"原文", "new": r"新文", "all": False},
            ],
        },
    ]
    DELETES = [r"C:/.../some.bak"]

行为：
- 每条替换校验 old 的出现次数：0 次=FAIL(跳过)；>1 次且 all=false=FAIL(歧义)；否则替换。
- 读取用 utf-8 / utf-8-sig 回退；写回 utf-8（无 BOM）。
- 任一替换失败则退出码非 0，并打印明细。
"""
import io
import json
import os
import sys

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def read_text(path):
    for enc in ("utf-8", "utf-8-sig"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="gbk") as fh:
        return fh.read()


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main():
    if len(sys.argv) != 2:
        print("usage: python skill_patch.py <spec.py>")
        return 2
    ns = {}
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        exec(fh.read(), ns)
    edits = ns.get("EDITS", [])
    deletes = ns.get("DELETES", [])

    failures = 0
    applied = 0

    for edit in edits:
        path = edit["file"]
        if not os.path.exists(path):
            print(f"[MISS] file not found: {path}")
            failures += 1
            continue
        text = read_text(path)
        changed = False
        for rep in edit["replacements"]:
            old = rep["old"]
            new = rep["new"]
            do_all = rep.get("all", False)
            cnt = text.count(old)
            if cnt == 0:
                print(f"[FAIL] not found in {os.path.basename(path)}: {old[:50]!r}")
                failures += 1
                continue
            if cnt > 1 and not do_all:
                print(f"[FAIL] ambiguous ({cnt}x) in {os.path.basename(path)}: {old[:50]!r}")
                failures += 1
                continue
            if do_all:
                text = text.replace(old, new)
            else:
                text = text.replace(old, new, 1)
            applied += 1
            changed = True
            print(f"[OK]   {os.path.basename(path)}: replaced {cnt if do_all else 1}x  {old[:40]!r}")
        if changed:
            write_text(path, text)
            print(f"[SAVE] {path}")

    for dpath in deletes:
        if os.path.exists(dpath):
            os.remove(dpath)
            print(f"[DEL]  {dpath}")
        else:
            print(f"[SKIP] not found: {dpath}")

    print("-" * 50)
    print(f"applied={applied}  failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

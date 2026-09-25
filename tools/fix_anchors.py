#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_anchors.py —— 修复笔记中的失效锚点链接。

处理两类：
1. Pandoc 风格 `{#自定义id}`：Obsidian 不支持 → 从标题移除该标记，把引用 id 的链接改指向标题实际锚点
2. 链接锚点与标题文字不匹配 → 归一化模糊匹配到最佳标题后改写

用法：
  python tools/fix_anchors.py                # 干跑预览全部笔记，不改文件
  python tools/fix_anchors.py --apply        # 实际写入
  python tools/fix_anchors.py 文件A.md 文件B.md [--apply]   # 只处理指定文件（相对 ROOT）
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths as _paths   # 路径单一事实源
ROOT = Path(_paths.NOTES_ROOT).resolve()
EXCLUDE_DIRS = {"tools", "PDF", ".git", "assets"}
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CUSTOM_ID_RE = re.compile(r"\s*\{#([^}]+)\}\s*$")
ANCHOR_LINK_RE = re.compile(r"(\[[^\]]+\]\()#([^)]+)(\))")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


def iter_targets():
    """默认自动扫描 ROOT 下全部 .md，排除 tools/PDF/.git/assets 路径。"""
    for p in sorted(ROOT.rglob("*.md")):
        rel = p.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        yield p


def slugify(t: str) -> str:
    t = t.strip().lower()
    t = re.sub(r"[^\w一-鿿\- ]", "", t)
    return re.sub(r"-+", "-", t.replace(" ", "-"))


def norm(t: str) -> str:
    return "".join(re.findall(r"[a-z0-9一-鿿]", t.lower()))


def process(path: Path, apply: bool):
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    in_code, fence_ch, flags = False, "", []
    for ln in lines:
        fm = FENCE_RE.match(ln)
        if fm and not in_code:
            in_code, fence_ch = True, fm.group(1)[0]
        elif fm and in_code and fm.group(1)[0] == fence_ch:
            in_code = False
        flags.append(in_code)

    headings = []       # (line_idx, clean_text, slug)
    custom_ids = {}     # custom_id -> slug
    new_lines = list(lines)
    for i, ln in enumerate(lines):
        if flags[i]:
            continue
        m = HEADING_RE.match(ln)
        if not m:
            continue
        raw = m.group(2)
        cid = CUSTOM_ID_RE.search(raw)
        clean = CUSTOM_ID_RE.sub("", raw).strip().rstrip("#").strip()
        slug = slugify(clean)
        headings.append((i, clean, slug))
        if cid:
            custom_ids[cid.group(1)] = slug
            new_lines[i] = f"{m.group(1)} {clean}"

    slugs = {s for _, _, s in headings}
    fixed, unresolved = [], []
    for i, ln in enumerate(lines):
        if flags[i]:
            continue
        def repl(m):
            anchor = m.group(2)
            if anchor in slugs or anchor in {s.lower() for s in slugs}:
                return m.group(0)
            # 1) 自定义 id 命中
            if anchor in custom_ids:
                fixed.append((f"L{i+1}", anchor, custom_ids[anchor], "自定义id"))
                return m.group(1) + "#" + custom_ids[anchor] + m.group(3)
            # 2) 模糊匹配标题
            na = norm(anchor)
            cands = [(s, t) for _, t, s in headings
                     if na and (na in norm(t) or norm(t) in na)]
            if len(cands) == 1:
                fixed.append((f"L{i+1}", anchor, cands[0][0], "模糊匹配"))
                return m.group(1) + "#" + cands[0][0] + m.group(3)
            unresolved.append((f"L{i+1}", anchor, len(cands)))
            return m.group(0)
        new_lines[i] = ANCHOR_LINK_RE.sub(repl, new_lines[i])

        # 矫正历史误写：`](有效slug)` → `](#有效slug)`（早期版本丢 # 的补救）
        if slugs:
            pat = re.compile(r"\]\((?!#)(" + "|".join(
                re.escape(s) for s in sorted(slugs, key=len, reverse=True)) + r")\)")
            new_lines[i], n = pat.subn(r"](#\1)", new_lines[i])
            if n:
                fixed.append((f"L{i+1}", "（丢失#的链接）", f"恢复 {n} 处", "补救"))

    print(f"\n=== {path.relative_to(ROOT)}")
    for loc, old, new, how in fixed:
        print(f"  [修复-{how}] {loc} #{old}  →  #{new}")
    for loc, anchor, ncand in unresolved:
        print(f"  [未解决] {loc} #{anchor}（候选标题 {ncand} 个，需人工判断）")
    if apply and (fixed or new_lines != lines):
        path.write_text("\n".join(new_lines), encoding="utf-8")
        print("  → 已写入")
    return len(fixed), len(unresolved)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    apply = "--apply" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--apply"]
    if args:
        targets = [ROOT / a for a in args]
    else:
        targets = list(iter_targets())
    tf = tu = 0
    for p in targets:
        if not p.exists():
            print(f"⚠ 文件不存在，跳过：{p}")
            continue
        f, u = process(p, apply)
        tf += f
        tu += u
    print(f"\n合计：修复 {tf}，未解决 {tu}{'（已写入）' if apply else '（干跑，未改文件）'}")


if __name__ == "__main__":
    main()

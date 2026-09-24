#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_links.py —— 修复笔记中失效的文件链接与图片路径。

扫描 ROOT 下全部 .md（排除 tools/PDF/.git/assets），找出失效的文件链接
（markdown 链接和图片，target 非 http/https/mailto/data，unquote 后不存在），
跳过纯锚点链接（#xxx，交给 fix_anchors.py）。

解析顺序：
  a) 旧绝对路径：target 含 "考研/" → 取其后的部分（如 408/CO/x.md），
     相对当前科目根（去掉第一段科目目录）解析。
  b) 同名文件搜索：unquote 后的 basename 在全科目文件索引中找唯一匹配。
  c) 都失败 → 未解决清单；basename 多候选 → 多候选清单，不自动改。

改写保留 #anchor 后缀；相对路径 POSIX 风格，空格编码为 %20。

用法：
  python tools/fix_links.py            # 干跑预览，不改文件
  python tools/fix_links.py --apply    # 实际写入
"""
import os
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(os.environ.get("NOTES_ROOT", r"E:\NPEE")).resolve()   # 笔记库根（2026-09-25 起与代码根分离，可用环境变量 NOTES_ROOT 覆盖）
EXCLUDE_DIRS = {"tools", "PDF", ".git", "assets"}
IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


def iter_files():
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        yield p


def build_index():
    """basename -> [Path]，覆盖全部文件（含 assets 图片，靠它解析图片链接）。"""
    idx = {}
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if any(part in {"tools", "PDF", ".git"} for part in rel.parts):
            continue
        if p.is_file():
            idx.setdefault(p.name.lower(), []).append(p)
    return idx


def split_code(lines):
    in_fence, fence_ch, flags = False, "", []
    for line in lines:
        fm = FENCE_RE.match(line)
        if fm and not in_fence:
            in_fence, fence_ch = True, fm.group(1)[0]
            flags.append(True)
            continue
        if fm and in_fence and fm.group(1)[0] == fence_ch:
            in_fence = False
            flags.append(True)
            continue
        flags.append(in_fence)
    return flags


def encode_rel(rel: str) -> str:
    # Obsidian 风格：仅空格编码为 %20，中文等保持原样
    return rel.replace(os.sep, "/").replace(" ", "%20")


def relpath(src: Path, dst: Path) -> str:
    return os.path.relpath(dst, src.parent).replace(os.sep, "/")


def resolve(path: Path, target: str, index):
    """返回 (新绝对路径 or None, 状态)。状态: ok / multi / fail"""
    dec = urllib.parse.unquote(target).replace("\\", "/")
    # a) 旧绝对路径：含 考研/ 部分
    if "考研/" in dec:
        tail = dec.split("考研/", 1)[1]
        parts = tail.split("/")
        # 去掉第一段科目目录名（408/English/Politics/Math），相对当前科目根
        if len(parts) > 1:
            cand = ROOT.joinpath(*parts[1:])
            if cand.exists():
                return cand, "ok"
    # b) 同名文件搜索
    base = dec.rstrip("/").split("/")[-1]
    hits = index.get(base.lower(), [])
    if len(hits) == 1:
        return hits[0], "ok"
    if len(hits) > 1:
        return hits, "multi"
    return None, "fail"


def process(path: Path, index, apply: bool):
    text = path.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    in_code = split_code(lines)
    changes, multi, fail = [], [], []
    new_lines = list(lines)
    rel_src = path.relative_to(ROOT)

    for i, line in enumerate(lines):
        if in_code[i]:
            continue
        for rx in (IMG_RE, LINK_RE):
            def repl(m, _i=i, _rx=rx):
                raw = m.group(1)
                if raw.startswith(("http://", "https://", "mailto:", "data:", "#")):
                    return m.group(0)
                target, sep, anchor = raw.partition("#")
                if not target:
                    return m.group(0)
                if (path.parent / urllib.parse.unquote(target)).exists():
                    return m.group(0)  # 有效，不动
                hit, status = resolve(path, target, index)
                if status == "ok":
                    new = encode_rel(relpath(path, hit)) + (sep + anchor if sep else "")
                    changes.append((rel_src, _i + 1, raw, new))
                    return m.group(0).replace(raw, new, 1)
                if status == "multi":
                    multi.append((rel_src, _i + 1, raw,
                                  [h.relative_to(ROOT).as_posix() for h in hit]))
                else:
                    fail.append((rel_src, _i + 1, raw))
                return m.group(0)
            new_lines[i] = rx.sub(repl, new_lines[i])

    if apply and changes:
        path.write_text(newline.join(new_lines), encoding="utf-8", newline="")
    return changes, multi, fail


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    apply = "--apply" in sys.argv
    index = build_index()
    all_changes, all_multi, all_fail = [], [], []
    for p in iter_files():
        if p.suffix.lower() != ".md" or not p.is_file():
            continue
        c, m, f = process(p, index, apply)
        all_changes += c
        all_multi += m
        all_fail += f

    print("—— 修复映射 ——")
    for src, ln, old, new in all_changes:
        print(f"{src}:L{ln}  {old}  →  {new}")
    print(f"\n—— 多候选（未自动改，需人工）共 {len(all_multi)} 条 ——")
    for src, ln, old, cands in all_multi:
        print(f"{src}:L{ln}  `{old}` 候选：{' | '.join(cands)}")
    print(f"\n—— 未解决 共 {len(all_fail)} 条 ——")
    for src, ln, old in all_fail:
        print(f"{src}:L{ln}  `{old}`")
    print(f"\n合计：可修复 {len(all_changes)}，多候选 {len(all_multi)}，"
          f"未解决 {len(all_fail)}{'（已写入）' if apply else '（干跑，未改文件）'}")


if __name__ == "__main__":
    main()

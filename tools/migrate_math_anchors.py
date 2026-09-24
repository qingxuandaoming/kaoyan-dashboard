#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrate_math_anchors.py —— 高数「章 → 讲」迁移的第 4b 步：修跨讲锚点。

拆分前 `](#33-定积分几何应用…)` 是**同文件**跳转；拆分后 3.3 节搬去了第11讲，
这个锚点就变成了死链（audit_notes 报「锚点无效」）。本脚本把这类锚点改写成
跨文件形式 `](./第11讲_….md#33-…)`。

slug 规则直接复用 `audit_notes.slugify`（它与渲染真值 md2pdf._slugify 一致），
不自己另写一份——两边规则不一致正是「体检说坏、页面却能跳」这类幽灵 bug 的来源。

顺带修一个既有错路径：`高数 notes.md` 里的 `./高数/错题归档.md`（中控本身就在 高数/ 下，
正确写法是 `./错题归档.md`），迁移前就是坏的。

只在**唯一命中**时才改写；slug 在多个讲里都存在（歧义）或全库都找不到 → 只报告不动手。
默认 dry-run，`--apply` 才写盘。
"""
import argparse
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
ROOT = os.environ.get("NOTES_ROOT", r"E:\NPEE")   # 笔记库根（2026-09-25 起与代码根分离，可用环境变量 NOTES_ROOT 覆盖）
GS = os.path.join(ROOT, "Math", "高数")

sys.path.insert(0, HERE)
import audit_notes as AN        # noqa: E402  复用 slugify / split_code / HEAD_RE

ANCHOR_RE = re.compile(r"\]\(#([^)\s]+)\)")
BAD_PATH = ("./高数/错题归档.md", "./错题归档.md")


def read(p):
    with open(p, encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def write(p, t):
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(t)


def slugs_of(path):
    lines = read(path).split("\n")
    infence = AN.split_code(lines)
    out = set()
    for i, ln in enumerate(lines):
        if infence[i]:
            continue
        m = AN.HEADING_RE.match(ln)
        if m:
            out.add(AN.slugify(m.group(2).strip()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    files = sorted(f for f in os.listdir(GS) if re.match(r"^第\d+讲_.+\.md$", f))
    # slug 索引要覆盖**所有**被扫描的文件，否则 公式速查.md 检索目录里的同文件锚点
    # 会被误报成「全库找不到」（第一版就漏了工具文件，93 条假警报）。
    extra = [f for f in ("高数 notes.md", "公式速查.md", "计算陷阱.md", "错题归档.md")
             if os.path.exists(os.path.join(GS, f))]
    own = {f: slugs_of(os.path.join(GS, f)) for f in files + extra}
    # slug → 哪些文件里有（跨讲改写只在目标是「别的讲」时才需要）
    where = {}
    for f, ss in own.items():
        for s in ss:
            where.setdefault(s, []).append(f)

    print("扫描 %d 个讲文件 + %d 个工具文件，标题 slug 共 %d 个（去重 %d）" % (
        len(files), len(extra), sum(len(v) for v in own.values()), len(where)))

    fixed, ambiguous, missing, pathfix = [], [], [], 0
    for f in files + extra:
        p = os.path.join(GS, f)
        if not os.path.exists(p):
            continue
        text = read(p)
        lines = text.split("\n")
        infence = AN.split_code(lines)
        changed = False
        for i, ln in enumerate(lines):
            if infence[i]:
                continue
            if BAD_PATH[0] in ln:
                pathfix += ln.count(BAD_PATH[0])
                lines[i] = ln.replace(BAD_PATH[0], BAD_PATH[1])
                changed = True
                ln = lines[i]

            def rep(m, f=f, i=i):
                nonlocal changed
                slug = m.group(1)
                if slug in own.get(f, ()):
                    return m.group(0)                      # 本文件内有效，不动
                hits = [h for h in (where.get(slug) or []) if h != f]
                if len(hits) == 1:
                    changed = True
                    fixed.append("%s:L%d → ./%s#%s" % (f, i + 1, hits[0], slug[:44]))
                    return "](./%s#%s)" % (hits[0], slug)
                if len(hits) > 1:
                    ambiguous.append("%s:L%d #%s 命中 %d 个文件：%s" % (
                        f, i + 1, slug[:40], len(hits), [h[:16] for h in hits]))
                else:
                    missing.append("%s:L%d #%s（全库找不到该标题）" % (f, i + 1, slug[:52]))
                return m.group(0)

            lines[i] = ANCHOR_RE.sub(rep, ln)
        if changed and args.apply:
            write(p, "\n".join(lines))

    print()
    print("== 改写的跨讲锚点（%d）==" % len(fixed))
    print("\n".join("   " + x for x in fixed) if fixed else "   （无）")
    print()
    print("== 既有错路径 ./高数/错题归档.md 修正：%d 处 ==" % pathfix)
    print()
    print("== 歧义（slug 在多个讲里都有，不动手）：%d ==" % len(ambiguous))
    print("\n".join("   " + x for x in ambiguous[:20]) if ambiguous else "   （无）")
    print()
    print("== 全库找不到的锚点（迁移前就是坏的，不动手）：%d ==" % len(missing))
    print("\n".join("   " + x for x in missing[:20]) if missing else "   （无）")
    print()
    print("模式：%s" % ("APPLY" if args.apply else "DRY-RUN"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

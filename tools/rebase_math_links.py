#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rebase_math_links.py —— 正文搬家后，把相对链接按新位置重定位。

两件事：

1. **重定位（rebase）**：`第17讲` 的正文原来在 `专题/空间曲面体绘图/`（两层深），
   `第16讲` 并入的部分原来在 `专题/`（一层深）。搬进 `高数/` 后，正文里那些
   `../../高数/第13讲….md`、`../高数/错题归档.md` 就指错地方了。
   规则：链接**从新位置解析不到**、但**从原位置能解析到** → 改写成从新位置出发的相对路径。
   已经能解析的一律不动（比如迁移时已改好的 `./assets/空间解析几何/figures/x.png`）。

2. **已删专题的兄弟相对引用**：`专题/不等式技巧与题型路由.md` 里写的是 `./无穷级数.md`
   （同目录兄弟），我的引用改写器只认 `../专题/无穷级数.md` 这类带前缀的写法，漏了它。
   按 basename 认出已删专题 → 指到并入后的讲文件。

默认 dry-run，`--apply` 才写盘；逐条打印改动，解析不到的单独报出来（不猜）。
"""
import argparse
import io
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
MATH = os.path.join(ROOT, "Math")
GS = os.path.join(MATH, "高数")

LINK_RE = re.compile(r"(!?)\[[^\]\n]*\]\(([^)\s]+)\)")

# 搬过家的文件 → 它正文的原目录
REBASE = {
    os.path.join(GS, "第17讲_多元函数积分学的预备知识.md"):
        os.path.join(MATH, "专题", "空间曲面体绘图"),
    os.path.join(GS, "第16讲_无穷级数.md"):
        os.path.join(MATH, "专题"),
}

# 已删除的专题：basename → (新文件绝对路径, 说明)
DELETED = {
    "无穷级数.md": (os.path.join(GS, "第16讲_无穷级数.md"), "并入第16讲 7.7"),
    "空间解析几何专题.md": (os.path.join(GS, "第17讲_多元函数积分学的预备知识.md"), "整篇并入第17讲"),
}


def resolves(base_dir, target):
    if target.startswith(("http://", "https://", "#", "mailto:")):
        return None
    path_only = target.split("#")[0].replace("%20", " ")
    if not path_only:
        return None
    p = os.path.normpath(os.path.join(base_dir, path_only))
    return p if os.path.exists(p) else None


def rel_from(new_dir, abs_target):
    r = os.path.relpath(abs_target, new_dir).replace("\\", "/")
    if not r.startswith("."):
        r = "./" + r
    return r


def fix_file(path, apply, report):
    new_dir = os.path.dirname(path)
    old_dir = REBASE.get(path)
    with open(path, encoding="utf-8", newline="") as fh:
        text = fh.read().replace("\r\n", "\n")
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    n = 0

    def rep(m):
        nonlocal n
        bang, target = m.group(1), m.group(2)
        anchor = ""
        core = target
        if "#" in target and not target.startswith("#"):
            core, _, a = target.partition("#")
            anchor = "#" + a
        if target.startswith(("http://", "https://", "mailto:")) or target.startswith("#"):
            return m.group(0)
        if resolves(new_dir, core):
            return m.group(0)                     # 本来就通，不动
        # ① 从原位置能解析 → 重定位
        if old_dir:
            abs_t = resolves(old_dir, core)
            if abs_t:
                n += 1
                newt = rel_from(new_dir, abs_t) + anchor
                report.append("   [rebase] %s ｜ %s → %s" % (rel, target[:60], newt))
                return m.group(0)[:m.group(0).index("(") + 1] + newt + ")"
        # ② basename 命中已删专题 → 指到并入后的讲
        base = os.path.basename(core)
        if base in DELETED:
            dst, why = DELETED[base]
            if os.path.exists(dst):
                n += 1
                report.append("   [已删专题] %s ｜ %s → %s（%s）" % (rel, target[:56], rel_from(new_dir, dst) + anchor, why))
                return m.group(0)[:m.group(0).index("(") + 1] + rel_from(new_dir, dst) + anchor + ")"
        report.append("   [仍不通] %s ｜ %s" % (rel, target[:80]))
        return m.group(0)

    new_text = LINK_RE.sub(rep, text)
    if new_text != text and apply:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new_text)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    targets = list(REBASE)
    for dp, dn, fns in os.walk(MATH):
        dn[:] = [d for d in dn if d not in {"assets", "PDF", ".qoder", ".git", "0讲义资料", "真题", "题库"}]
        for f in sorted(fns):
            if f.endswith(".md"):
                p = os.path.join(dp, f)
                if p not in targets:
                    targets.append(p)

    report, total = [], 0
    for p in targets:
        if os.path.exists(p):
            total += fix_file(p, args.apply, report)
    print("\n".join(report) if report else "   （无改动）")
    print()
    print("改动 %d 处 ｜ 模式：%s" % (total, "APPLY" if args.apply else "DRY-RUN"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

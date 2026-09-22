# -*- coding: utf-8 -*-
"""verify_lecture_split.py —— 独立复验「章 → 讲」拆分没有丢字、没有串讲。

不复用 migrate_math_lectures.py 的任何内部账本：分别从**源章文件**与**18 个讲文件**
抽取正文行，做多重集（multiset）比对。

两侧都要剥掉「非正文」的部分：
  源章文件：H1 标题行、末尾双链导航块
  讲文件  ：生成的头部（H1 / 张宇讲次说明 / 迁移标记）、末尾生成的双链导航块、
            自动插入的「📎 出自原第N章」出处行

另外做两处归一化，避免把「刻意改写」误判成「内容不一致」：
  * `原第N章` → `本章`（preamble 搬运时改写过）
  * 纯 `---` 分隔行不计入（生成头部/导航会增删）
"""
import io
import json
import os
import re
import sys
from collections import Counter

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
ROOT = os.path.dirname(SRC)
SPEC = os.path.join(SRC, "math_lectures.json")

NAV_RE = re.compile(r"^#{2,3}\s*🔗?\s*双链导航\s*$")
GEN_HEAD = (
    re.compile(r"^#\s+第\d+讲\s"),
    re.compile(r"^>\s*张宇《高数18讲》"),
    re.compile(r"^>\s*本文件由 tools/migrate_math_lectures\.py"),
    re.compile(r"^<!--\s*migrated-by:"),
    re.compile(r"^>\s*📎\s*出自原第\d+章"),
)
ORIGIN_RE = re.compile(r"原第(\d+)章")


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n").split("\n")


def cut_nav(lines):
    for i, ln in enumerate(lines):
        if NAV_RE.match(ln):
            return lines[:i]
    return lines


def norm(ln):
    return ORIGIN_RE.sub(lambda m: "本章", ln).strip()


def body_multiset(lines, drop_h1=False, drop_gen=False):
    out = Counter()
    for ln in lines:
        s = ln.strip()
        if not s or s == "---":
            continue
        if drop_h1 and s.startswith("# "):
            continue
        if drop_gen and any(p.match(ln) for p in GEN_HEAD):
            continue
        out[norm(ln)] += 1
    return out


def main():
    spec = json.loads(open(SPEC, encoding="utf-8").read())
    gs = os.path.join(ROOT, *spec["subject"]["dir"].split("/"))

    # 源章 → 派生出的讲
    src2lec = {}
    for lec in spec["lectures"]:
        for s in lec.get("sources") or []:
            src2lec.setdefault(s["file"], []).append(lec)

    fails, notes = [], []
    total_src = Counter()
    total_lec = Counter()
    compared = 0            # 真正做过逐章比对的源章数
    skipped = []            # 源章已删除（迁移完成后就是这种状态）

    for fn, lecs in sorted(src2lec.items()):
        sp = os.path.join(gs, fn)
        if not os.path.exists(sp):
            skipped.append(fn)
            notes.append("—  源章已删除，无法逐行比对：%s" % fn)
            continue
        compared += 1
        src = body_multiset(cut_nav(read(sp)), drop_h1=True)
        got = Counter()
        for lec in sorted(lecs, key=lambda x: x["no"]):
            lp = os.path.join(gs, lec["file"])
            if not os.path.exists(lp):
                fails.append("第%d讲 文件缺失：%s" % (lec["no"], lec["file"]))
                continue
            got += body_multiset(cut_nav(read(lp)), drop_gen=True)
        total_src += src
        total_lec += got

        miss = src - got       # 源里有、讲里没有 → 丢内容
        extra = got - src      # 讲里有、源里没有 → 串讲或凭空多出来
        tag = "OK " if not miss and not extra else "✗"
        notes.append("%s 第%s章 %s ｜ 源 %d 行 / 讲 %d 行 ｜ 丢 %d ｜ 多 %d" % (
            tag, re.match(r"^第(\d+)章", fn).group(1), fn,
            sum(src.values()), sum(got.values()), sum(miss.values()), sum(extra.values())))
        for ln, c in list(miss.items())[:6]:
            fails.append("  [丢] %s ×%d ｜ %s" % (fn, c, ln[:110]))
            notes.append("      [丢] ×%d ｜ %s" % (c, ln[:110]))
        for ln, c in list(extra.items())[:6]:
            fails.append("  [多] %s ×%d ｜ %s" % (fn, c, ln[:110]))
            notes.append("      [多] ×%d ｜ %s" % (c, ln[:110]))

    print("== 逐章比对 ==")
    print("\n".join(notes))
    print()
    print("== 汇总 ==")
    print("   源章正文行合计 %d ｜ 讲文件正文行合计 %d" % (
        sum(total_src.values()), sum(total_lec.values())))
    print("   全库丢失 %d 行 ｜ 全库多出 %d 行" % (
        sum((total_src - total_lec).values()), sum((total_lec - total_src).values())))
    print()
    if fails:
        print("✗ 复验未通过，%d 条问题：" % len(fails))
        print("\n".join(fails[:40]))
        return 1
    if compared == 0:
        # ⚠️ 迁移完成后源章文件已删，这里没有可比对的对象 —— **不能**打印"复验通过"，
        # 那是空断言（0 行 vs 0 行当然相等）。守恒证据只在迁移当时有效：
        # 2026-09-22/23 实测 8 章正文 5636 行 → 18 讲 5636 行，0 丢失 0 串讲 0 重复。
        print("○ 无可比对对象：%d 个源章文件都已删除（迁移已完成）。" % len(skipped))
        print("  本脚本只在「源章与讲文件并存」时才有意义，用于落盘前的守恒核对。")
        print("  历史证据：迁移当时 8 章正文 5636 行 → 18 讲 5636 行，0 丢失 / 0 串讲 / 0 重复。")
        print("  现在要验笔记完整性，请跑 tools/audit_notes.py（断链/锚点）与 tools/e2e_lecture_split.py。")
        return 0
    print("✓ 复验通过：%d 个源章的正文与派生讲文件逐行一致（无丢失、无串讲）。" % compared)
    return 0


if __name__ == "__main__":
    sys.exit(main())

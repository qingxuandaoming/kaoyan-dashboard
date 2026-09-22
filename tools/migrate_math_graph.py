#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrate_math_graph.py —— 高数「章 → 讲」迁移的第 6 步：拆掉图谱里的 note_chapter 桥。

`math_graph.json` 的考点 `chapter` 一直是张宇讲次；过去笔记按教材章写，所以每个数学考点
额外带一个 `note_chapter` 指到笔记章号，`note_prefix.topic_note_chapter()` 靠它对齐。

高数笔记已按讲拆分 → **高数的 chapter 就是笔记单元号**，桥多余，删掉 `MATH-GS-*` 的
`note_chapter`（函数会自动回退到 chapter）。

⚠️ **线代（MATH-XD）与概率论（MATH-GL）的 note_chapter 必须原样保留**：它们的笔记仍按
教材章，而且讲号与章号是交叉的（线代讲5「线性方程组」在第4章、讲6「向量组」在第3章）。
删掉会让这两科的笔记覆盖与每日任务载体全部错配。

第17讲（多元函数积分学的预备知识）过去是 `note_chapter: null`（永不匹配、恒为缺口），
因为它的正文一直躺在 专题/空间曲面体绘图/空间解析几何专题.md。现在建了讲级入口文件，
所以这个 null 也一并删掉，让它按 chapter=17 正常参与匹配。

用法：python tools/migrate_math_graph.py [--apply]
"""
import argparse
import io
import json
import os
import sys

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
GRAPH = os.path.join(SRC, "knowledge_graph", "math_graph.json")
KEEP_PREFIX = ("MATH-XD", "MATH-GL")     # 这两科的桥不能拆


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    with open(GRAPH, encoding="utf-8", newline="") as fh:
        raw = fh.read()
    g = json.loads(raw)

    removed, kept, already = [], [], 0
    for sub_key, sub in g.get("subs", {}).items():
        for t in sub.get("topics", []):
            tid = t.get("id", "")
            if "note_chapter" not in t:
                already += 1
                continue
            if tid.startswith(KEEP_PREFIX):
                kept.append("%-12s note_chapter=%s（第%s讲 → 第%s章）" % (
                    tid, t["note_chapter"], t.get("chapter"), t["note_chapter"]))
                continue
            removed.append("%-12s note_chapter=%-5s chapter=%s  %s" % (
                tid, t["note_chapter"], t.get("chapter"), t.get("name")))
            if args.apply:
                del t["note_chapter"]

    print("== 删除 note_chapter（高数 MATH-GS，%d 个）==" % len(removed))
    print("\n".join("   " + x for x in removed))
    print()
    print("== 保留 note_chapter（线代/概率，%d 个）==" % len(kept))
    print("\n".join("   " + x for x in kept))
    print()
    print("本来就没有该键的考点：%d" % already)

    if not args.apply:
        print("\n[dry-run] 未写盘。")
        return 0

    with open(GRAPH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(g, ensure_ascii=False, indent=2) + "\n")
    json.loads(open(GRAPH, encoding="utf-8").read())      # 写坏了立刻炸
    print("\n[apply] math_graph.json 已更新并通过 JSON 合法性校验。")

    # 复核：线代/概率的桥还在
    g2 = json.loads(open(GRAPH, encoding="utf-8").read())
    n_gs = sum(1 for s in g2["subs"].values() for t in s["topics"]
               if t["id"].startswith("MATH-GS") and "note_chapter" in t)
    n_xd = sum(1 for s in g2["subs"].values() for t in s["topics"]
               if t["id"].startswith(("MATH-XD", "MATH-GL")) and "note_chapter" in t)
    print("复核：MATH-GS 残留 note_chapter = %d（应为 0）｜ 线代+概率 保留 = %d（应为 18）"
          % (n_gs, n_xd))
    return 0 if (n_gs == 0 and n_xd == 18) else 1


if __name__ == "__main__":
    sys.exit(main())

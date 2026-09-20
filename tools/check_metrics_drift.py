#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_metrics_drift.py — 口径自检：把 metrics_spec.json 与源码里的实际取值对拍。

为什么要有它
------------
2026-09-20 用户原话：「不如就一套标准、一套流程…不用这样每次再发现问题，再重新去排查。」
口径散落在十来个脚本里，靠人发现不一致是不可持续的。本脚本把「注册表声明的值」
与「源码里真正在用的值」逐条比对，不一致就报错并指出行号。

三类检查
--------
1. **锚点对拍**：metrics_spec.json 里每个 metric 的 checks 给出 (文件, 正则, 期望值)，
   实际取到的值与之不符 → DRIFT。
2. **路径存在性**：kind="path_prefix" 的检查会把「笔记目录规则表」里的目录前缀
   拿到磁盘上验证，不存在 → MISSING-PATH（正是 translation&write 那个 bug）。
3. **交叉一致**：可改项的 range 必须在 serve.js 的 RANGES 里与 spec 一致（否则设置页
   的输入范围与服务端校验会打架）。

用法
----
    python tools/check_metrics_drift.py           # 只报告，退出码非 0 表示有漂移
    python tools/check_metrics_drift.py --fix     # 顺带修掉可安全修的目标
"""

import argparse
import io
import json
import os
import re
import sys

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
ROOT = os.path.dirname(SRC)
SPEC_PATH = os.path.join(SRC, "metrics_spec.json")

MONTHS = None


def load_spec():
    with open(SPEC_PATH, encoding="utf-8") as f:
        return json.load(f)


def read_lines(rel):
    p = os.path.join(SRC, rel)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


# ---------------------------------------------------------------------------
# 取值
# ---------------------------------------------------------------------------
def extract(lines, ch):
    """按 check 定义从源码里取实际值；取不到返回 (None, None)。"""
    rx = re.compile(ch["regex"])
    for i, line in enumerate(lines, 1):
        m = rx.search(line)
        if not m:
            continue
        kind = ch.get("kind", "int")
        cap = ch.get("capture")
        if kind == "iso_date":
            g = m.groups()
            return "%04d-%02d-%02d" % (int(g[0]), int(g[1]), int(g[2])), i
        if cap is None:
            return m.group(0), i
        raw = m.group(cap)
        if kind == "int":
            return int(raw), i
        if kind == "float":
            return float(raw), i
        return raw, i
    return None, None


def expected_value(metric, ch):
    """这条 check 期望的值（支持 at 键取子字段）。"""
    v = metric.get("value", metric.get("default"))
    at = ch.get("at")
    if at == "range_hi":
        rng = metric.get("range") or []
        return rng[1] if len(rng) == 2 else None
    if at and isinstance(v, dict):
        return v.get(at)
    return v


def same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-9
    return str(a) == str(b)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="口径漂移自检")
    ap.add_argument("--fix", action="store_true", help="顺带修掉可安全修的目标（目前仅目录名）")
    args = ap.parse_args()

    spec = load_spec()
    drifts, missing, oks, skipped = [], [], [], []

    for m in spec.get("metrics", []):
        mid = m.get("id")
        for ch in m.get("checks", []) or []:
            if ch.get("kind") == "skip":
                skipped.append((mid, ch["file"]))
                continue
            rel = ch["file"]
            lines = read_lines(rel)
            if lines is None:
                drifts.append((mid, rel, 0, "文件不存在", "-", "-"))
                continue

            # ① 路径存在性（不比对值，只看磁盘有没有这个目录/glob 前缀）
            if ch.get("kind") == "path_prefix":
                for i, line in enumerate(lines, 1):
                    mm = re.search(ch["regex"], line)
                    if not mm:
                        continue
                    prefix = mm.group(ch.get("capture", 1))
                    head = prefix.split("*")[0].rstrip("/")
                    target = os.path.join(ROOT, "English", head)
                    if head and not os.path.exists(target):
                        missing.append((mid, rel, i, prefix, head, ""))
                    else:
                        oks.append((mid, rel, i, prefix, "路径存在"))
                continue

            actual, ln = extract(lines, ch)
            want = expected_value(m, ch)
            if actual is None:
                drifts.append((mid, rel, 0, "源码里找不到锚点", want, None))
            elif same(actual, want):
                oks.append((mid, rel, ln, actual, want))
            else:
                drifts.append((mid, rel, ln, actual, want))

    # ② 可改项的 range 与 serve.js RANGES 交叉一致（range 由 spec 声明，这里动态对拍）
    # ⚠️ 要把整个文件读成一段：RANGES 现在写成多行（每行一个键），逐行找 RANGES 声明
    #    那一行是找不到键的。用「const RANGES = { … }」整段匹配。
    serve_src = "\n".join(read_lines("serve.js") or [])
    ranges_block = ""
    mblock = re.search(r"const RANGES = \{(.*?)\};", serve_src, re.DOTALL)
    if mblock:
        ranges_block = mblock.group(1)
    for m in spec.get("metrics", []):
        if not (m.get("editable") and m.get("key") and m.get("range")):
            continue
        mm = re.search(re.escape(m["key"]) + r":\s*\[(\d+),\s*(\d+)\]", ranges_block)
        if not mm:
            drifts.append((m["id"], "serve.js", 0, "RANGES 里没有这个键（设置页改不了它）",
                           list(m["range"]), ""))
        else:
            lo, hi = int(mm.group(1)), int(mm.group(2))
            if not same(lo, m["range"][0]) or not same(hi, m["range"][1]):
                drifts.append((m["id"], "serve.js", 0, [lo, hi], list(m["range"]), ""))
            else:
                oks.append((m["id"], "serve.js", 0, m["range"], "与 RANGES 一致"))

    # ---- 报告 ----
    print("=" * 76)
    print("口径漂移自检 —— metrics_spec.json  vs  源码实际取值")
    print("=" * 76)
    print("  注册表条目：%d（runtime 可改 %d / fixed %d / convention %d）" % (
        len(spec.get("metrics", [])),
        sum(1 for x in spec["metrics"] if x.get("scope") == "runtime"),
        sum(1 for x in spec["metrics"] if x.get("scope") == "fixed"),
        sum(1 for x in spec["metrics"] if x.get("scope") == "convention")))
    print("  锚点检查：%d 通过 / %d 漂移 / %d 路径缺失" % (len(oks), len(drifts), len(missing)))

    if oks:
        print("\n--- 通过 ---")
        for mid, rel, ln, a, b in oks:
            print("  ✓ %-28s %s%s  = %s" % (mid, rel, (":%d" % ln) if ln else "", a))

    if missing:
        print("\n--- ⚠️ 路径缺失（目录改名后失配，相关统计会静默变 0）---")
        for mid, rel, ln, prefix, head, _ in missing:
            print("  ✗ %-28s %s:%d" % (mid, rel, ln))
            print("      规则写的：%s" % prefix)
            print("      磁盘查过：%s" % os.path.join(ROOT, "English", head))
            near = os.path.join(ROOT, "English", os.path.dirname(head))
            if os.path.isdir(near):
                cands = [d for d in os.listdir(near) if not d.startswith(".")]
                print("      该层实际有：" + ", ".join(cands))

    if drifts:
        print("\n--- ⚠️ 漂移（注册表与源码不一致）---")
        for row in drifts:
            mid, rel, ln, a, b = row[0], row[1], row[2], row[3], row[4]
            loc = "%s:%d" % (rel, ln) if ln else rel
            print("  ✗ %-28s %s" % (mid, loc))
            print("      源码实际：%s" % a)
            print("      注册表说：%s" % b)

    bad = len(drifts) + len(missing)
    print("\n" + "=" * 76)
    print("结论：%s" % ("口径一致 ✅" if bad == 0 else "发现 %d 处不一致 ⚠️（见上）" % bad))
    print("=" * 76)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

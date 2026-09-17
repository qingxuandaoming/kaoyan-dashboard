#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""fix_ascii_math_cards.py —— 修掉「渲染层无法安全处理」的 ASCII 公式写法。

【这一层修什么】2026-09-17 给 mdInline 加了 ASCII 上下标兜底（_(x) / _i / ^{n} / ^n
→ <sub>/<sup>），题库与笔记里 129 处这类写法**显示**已经正常，不必改数据。

只有一类渲染层**故意不碰**：指数后面还粘着字母的，如 2^32B。
    · 它到底是 2³² B（字节）还是 2^(32B)？纯靠字符串分不出来；
    · 猜错了比不改更糟 —— 所以 asciiMath 直接放行，交给这一层用「人写死的改写表」修。
这类写法全库只剩 1 张卡 4 处（Q-TGT-A31CA44C）。改成 Unicode 上标（2³²B）：
与题库里既有的 O(n²)、(−1)ⁿ、2ⁿ⁻¹ 一致，且飞书周报、Anki 导出这些**纯文本出口**
也照样好看（TeX 会在那些出口露源码，见 tex_plain.py 的说明）。

【安全约束】只改 questions.content，不碰 cards 表 FSRS 状态；
每条改写都断言"原文能在库里找到"，找不到就报错退出，绝不静默漏改。
先备份：backups/question_bank.db.bak_notation_<日期>。

用法:
    python tools/fix_ascii_math_cards.py --dry-run
    python tools/fix_ascii_math_cards.py
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = Path(__file__).resolve().parent.parent / "question_bank.db"

# qid -> {字段: [(旧, 新), ...]}    字段是 str 或 list[str]（options / traps）
REWRITE = {
    "Q-TGT-A31CA44C": {
        "explanation": [
            ("2^32B", "2³²B"),      # 主存 4GB
            ("2^6B", "2⁶B"),        # 块大小 64B（别先匹配 2^6 再撞上 2^6B）
            ("2^20B", "2²⁰B"),      # Cache 1MB
        ],
        "traps": [
            ("2^30B", "2³⁰B"),      # 「把 4GB 当成 2^30B」这个典型错法
        ],
    },
}

# 渲染层不再插手的形态：^ 后面是「数字开头 + 尾巴带字母」的串
LEFTOVER = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{1,3}\^[0-9]+[A-Za-z][A-Za-z0-9]*")


def apply_pairs(value, pairs, missing):
    """把 pairs 里的旧写法换成新写法；没命中的记进 missing"""
    for old, new in pairs:
        if old not in value:
            missing.append(old)
            continue
        value = value.replace(old, new)
    return value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    changed = 0
    missing = []
    after_blobs = {}

    for qid, patch in REWRITE.items():
        row = conn.execute("SELECT content FROM questions WHERE id = ?", (qid,)).fetchone()
        if not row:
            missing.append(qid)
            continue
        ct = json.loads(row["content"])
        before = json.dumps(ct, ensure_ascii=False)
        for field, pairs in patch.items():
            if isinstance(ct.get(field), list):
                ct[field] = [apply_pairs(str(x), pairs, missing) for x in ct[field]]
            elif isinstance(ct.get(field), str):
                ct[field] = apply_pairs(ct[field], pairs, missing)
            else:
                missing.append(f"{qid}.{field}")
        after = json.dumps(ct, ensure_ascii=False)
        if before == after:
            print(f"  [无变化] {qid}")
            continue
        after_blobs[qid] = after
        if not args.dry_run:
            conn.execute("UPDATE questions SET content = ? WHERE id = ?", (after, qid))
        print(f"  [改] {qid}：{', '.join(patch.keys())}")
        changed += 1

    if missing:
        conn.close()
        print(f"\n❌ 改写表里的旧写法在库里找不到（表要更新）：{missing}")
        sys.exit(1)

    if not args.dry_run:
        conn.commit()

    # 复核：这几张卡不能再残留「^数字+字母」的写法
    bad = [(qid, m.group(0)) for qid, blob in after_blobs.items()
           for m in LEFTOVER.finditer(blob)]
    conn.close()

    print(f"\n完成：改写 {changed} 张。" + ("（dry-run，未写库）" if args.dry_run else ""))
    if bad:
        print(f"⚠️ 仍残留歧义写法：{bad}")
        sys.exit(1)
    print("✅ 复核通过：改写表覆盖的卡片已无「^数字+字母」残留")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
check_card_quality.py — 闪卡选项质量审计

出题容易犯的三类错误，脚本按 ERROR / WARN 两档报出来：

  ERROR
    E1 选项内嵌字母前缀（"A. xxx"）—— 大盘渲染层已经画了 A/B/C/D 徽章，
       会显示成 "Ⓐ A. xxx"；且字母与序号不一致时更会误导。
    E2 选择题选项数 ≠ 4
    E3 answer 下标越界 / 类型不对
    E4 选项重复（去装饰性标点后完全相同）

  WARN
    W1 格式泄露：一部分选项带 "词缀-(释义)" 标签、另一部分不带
       —— 这就是 2026-09-13 用户反馈的"不看词义、只看格式就能选对"。
    W2 选项长度失衡：最长 / 最短 > 2.5 倍，答案往往是最长那条。
    W3 缺 explanation 或 traps。

用法:
    python tools/check_card_quality.py               # 全库审计
    python tools/check_card_quality.py --prefix ENG  # 只看英语
    python tools/check_card_quality.py --quiet       # 只打印汇总
退出码：存在 ERROR 时为 1，否则 0。
"""

import argparse
import io
import json
import re
import sqlite3
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paths as _paths   # 路径单一事实源
BASE_DIR = Path(_paths.NOTES_ROOT)
DB_PATH = Path(__file__).resolve().parent.parent / "question_bank.db"   # 代码根（2026-09-25 起与笔记库分离）

# 判定规则与出题侧的闸门共用 tools/card_quality.py（单一事实来源，避免口径漂移）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from card_quality import (  # noqa: E402
    AFFIX_LABEL_RE as AFFIX_LABEL,
    LEN_RATIO_LIMIT,
    LETTER_PREFIX_RE as LETTER_PREFIX,
    SHORT_OPTION_CHARS,
    normalize_option as norm,
)


def audit_one(qid, qtype, content):
    """返回 (errors, warnings)，元素为字符串。"""
    errors, warns = [], []
    ct = content if isinstance(content, dict) else {}
    opts = ct.get("options")

    if qtype == "choice":
        if not isinstance(opts, list) or len(opts) != 4:
            errors.append(f"E2 选择题选项数 = {len(opts) if isinstance(opts, list) else '无'}，应为 4")
        ans = ct.get("answer")
        if isinstance(ans, bool) or not isinstance(ans, int) or (isinstance(opts, list) and not 0 <= ans < len(opts)):
            errors.append(f"E3 answer={ans!r} 非法")

    if isinstance(opts, list) and opts:
        # E1 字母前缀
        for i, o in enumerate(opts):
            m = LETTER_PREFIX.match(str(o or ""))
            if m:
                want = chr(65 + i)
                got = m.group(1).upper()
                extra = "" if got == want else f"（字母 {got} 与序号 {want} 不一致，渲染后会张冠李戴）"
                errors.append(f"E1 选项{i} 内嵌字母前缀 “{got}. ”{extra}")

        # E4 选项重复（去装饰性标点后完全相同）
        ns = [norm(o) for o in opts]
        for i in range(len(ns)):
            for j in range(i + 1, len(ns)):
                a, b = ns[i], ns[j]
                if not a or not b:
                    continue
                # 只判完全相同：数学选项里一个因子之差就是不同答案（如 x·e^(x²) 与 2x·e^(x²)）
                if a == b:
                    errors.append(f"E4 选项{i} 与选项{j} 完全相同：{str(opts[i])[:24]}")

        # W1 格式泄露：带词缀标签的选项与裸选项混排
        labeled = [i for i, o in enumerate(opts) if AFFIX_LABEL.match(str(o or ""))]
        if labeled and len(labeled) < len(opts):
            bare = [i for i in range(len(opts)) if i not in labeled]
            hit = "（唯一裸项就是答案，格式直接泄露）" if len(bare) == 1 else ""
            warns.append(f"W1 选项形态混排：{len(labeled)} 项带“词缀-(释义)”标签、{len(bare)} 项为裸释义{hit}")

        # W2 长度失衡
        lens = [len(str(o)) for o in opts]
        if (len(lens) == len(opts) and min(lens) > 0 and max(lens) > SHORT_OPTION_CHARS
                and max(lens) / min(lens) > LEN_RATIO_LIMIT):
            longest = lens.index(max(lens))
            warns.append(f"W2 选项长度失衡 {min(lens)}–{max(lens)} 字（最长项为选项{longest}，易成为答案线索）")

    # W3 解析与陷阱
    if not str(ct.get("explanation") or "").strip():
        warns.append("W3 缺 explanation")
    if qtype == "choice" and not [t for t in (ct.get("traps") or []) if str(t).strip()]:
        warns.append("W3 缺 traps 易错点")

    return errors, warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="", help="只审计 topic_id 以该前缀开头的题")
    ap.add_argument("--quiet", action="store_true", help="只打印汇总")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, topic_id, type, content FROM questions ORDER BY topic_id, id"
    ).fetchall()
    conn.close()

    n_err = n_warn = n_scanned = 0
    err_qids, warn_qids = set(), set()
    by_topic = {}
    for qid, topic_id, qtype, raw in rows:
        if args.prefix and not (topic_id or "").startswith(args.prefix):
            continue
        try:
            ct = json.loads(raw)
        except Exception:
            print(f"[{qid}] content 不是合法 JSON")
            n_err += 1
            continue
        n_scanned += 1
        e, w = audit_one(qid, qtype, ct)
        if e or w:
            by_topic.setdefault(topic_id or "（未归类）", []).append((qid, e, w))
            if e:
                err_qids.add(qid)
            if w:
                warn_qids.add(qid)
            n_err += len(e)
            n_warn += len(w)

    if not args.quiet:
        for topic in sorted(by_topic):
            print(f"\n=== {topic} ===")
            for qid, e, w in by_topic[topic]:
                for msg in e:
                    print(f"  ❌ {qid}  {msg}")
                for msg in w:
                    print(f"  ⚠️  {qid}  {msg}")

    print(f"\n{'=' * 56}")
    print(f"审计 {n_scanned} 题：ERROR {n_err} 条（{len(err_qids)} 题）、WARN {n_warn} 条（{len(warn_qids)} 题）")
    print(f"{'=' * 56}")
    sys.exit(1 if err_qids else 0)


if __name__ == "__main__":
    main()

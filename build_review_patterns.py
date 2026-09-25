#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_review_patterns.py — 从错题复盘会话聚合「错因画像」

为什么需要它（解决用户担心的两个问题）：
  · token 浪费：复盘会话会越攒越多，谁都不该去全量读 errors.json。
    本脚本一次聚合出 _patterns.json（每科只留 TOP-N），
    大盘、AI 上下文、出卡脚本都只读这一份小的。
  · 死数据：每条错因都带 score（次数 × 时间衰减 × 是否闭环）与消费方标记。
    已出卡且答对的会自动降权沉底，不再反复提示；长期零命中的会被挤出 TOP-N，
    但原始会话文件保留在磁盘上（用户资料不删）。

用法：
  python build_review_patterns.py            # 生成/覆盖 Review/_patterns.json
  python build_review_patterns.py --top 12   # 每科保留多少条错因
  python build_review_patterns.py --json     # 只打到 stdout，不落盘
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import paths as _paths   # 路径单一事实源
SRC = Path(_paths.SRC_DIR)
ROOT = Path(_paths.NOTES_ROOT)
REVIEW = ROOT / "Review"
OUT = REVIEW / "_patterns.json"

SUBJECTS = ["408", "数学一", "政治", "英语一"]
HALF_LIFE_DAYS = 30.0     # 错因权重半衰期：30 天前的错，权重减半
RESOLVED_FACTOR = 0.35    # 已闭环（出卡并答对/已确认掌握）的折扣
MAX_AGE_KEEP = 180        # 超过这么多天且已闭环的，不再进 TOP 榜（数据仍留在文件里）


def _setup_io():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def parse_day(s):
    """兼容 2026-09-15 与 2026-09-15T10:00:00 两种写法。"""
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def load_errors():
    """遍历 Review/<科目>/sessions/<sid>/errors.json，返回扁平行。"""
    rows = []
    for subject in SUBJECTS:
        sdir = REVIEW / subject / "sessions"
        if not sdir.is_dir():
            continue
        for sess in sorted(sdir.iterdir()):
            ef = sess / "errors.json"
            mf = sess / "meta.json"
            if not ef.is_file():
                continue
            try:
                bag = json.loads(ef.read_text(encoding="utf-8"))
                meta = json.loads(mf.read_text(encoding="utf-8")) if mf.is_file() else {}
            except Exception as exc:
                print(f"WARN: 跳过 {sess.name}: {exc}", file=sys.stderr)
                continue
            day = parse_day(meta.get("date") or bag.get("date")) or parse_day(sess.name[:11])
            for e in (bag.get("errors") or []):
                rows.append({
                    "subject": subject,
                    "sid": sess.name,
                    "date": day,
                    "cause": (e.get("cause") or "未归类").strip()[:120],
                    "cause_kind": (e.get("cause_kind") or "concept").strip()[:40],
                    "topic_hint": (e.get("topic_hint") or "").strip()[:200],
                    "topic_id": (e.get("topic_id") or "").strip()[:60],
                    "severity": max(1, min(5, int(e.get("severity") or 3))),
                    "resolved": bool(e.get("resolved")),
                    "title": (e.get("title") or "").strip()[:200],
                })
    return rows


def card_coverage(db, hints):
    """
    这些错因涉及的考点，库里有没有卡、练得怎么样。
    用 topics.name 模糊匹配 topic_hint；匹配不上就当作「尚无卡」，
    宁可漏判闭环也不误判——漏判只是多提醒一次，误判会让真弱点消失。
    """
    cov = {}
    if not hints:
        return cov
    for hint in hints:
        key = (hint or "").strip()
        if len(key) < 2:
            continue
        like = "%" + key[:24] + "%"
        row = db.execute(
            """
            SELECT COUNT(DISTINCT c.id) AS cards,
                   COALESCE(AVG(rl.rating), 0) AS avg_rating,
                   COUNT(rl.id) AS answered
              FROM topics t
              LEFT JOIN questions q ON q.topic_id = t.id
              LEFT JOIN cards c ON c.question_id = q.id AND COALESCE(c.suspended,0) = 0
              LEFT JOIN review_log rl ON rl.question_id = q.id
             WHERE t.name LIKE ?
            """,
            (like,),
        ).fetchone()
        if row:
            cov[hint] = {
                "cards": row["cards"] or 0,
                "answered": row["answered"] or 0,
                "avg_rating": round(float(row["avg_rating"] or 0), 2),
            }
    return cov


def build(top_n):
    today = date.today()
    rows = load_errors()

    db = None
    db_path = SRC / "question_bank.db"
    if db_path.is_file():
        try:
            db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
        except Exception as exc:
            print(f"WARN: 题库只读打开失败，闭环判定降级：{exc}", file=sys.stderr)

    subjects = {}
    try:
        for subject in SUBJECTS:
            mine = [r for r in rows if r["subject"] == subject]
            if not mine:
                subjects[subject] = {"total_errors": 0, "open": 0, "top_causes": []}
                continue

            grouped = defaultdict(list)
            for r in mine:
                grouped[r["cause"]].append(r)

            hints = sorted({r["topic_hint"] for r in mine if r["topic_hint"]})
            cov = card_coverage(db, hints) if db else {}

            items = []
            for cause, items_rows in grouped.items():
                n = len(items_rows)
                score = 0.0
                last_seen = None
                kinds = defaultdict(int)
                topic_ids = set()
                all_resolved = True
                for r in items_rows:
                    age = (today - r["date"]).days if r["date"] else 999
                    decay = 0.5 ** (max(0, age) / HALF_LIFE_DAYS)
                    factor = RESOLVED_FACTOR if r["resolved"] else 1.0
                    score += (r["severity"] / 3.0) * decay * factor
                    if r["date"] and (last_seen is None or r["date"] > last_seen):
                        last_seen = r["date"]
                    kinds[r["cause_kind"]] += 1
                    if r["topic_id"]:
                        topic_ids.add(r["topic_id"])
                    if not r["resolved"]:
                        all_resolved = False

                # 闭环判定：错因涉及的考点全部有卡且平均评分 >=3
                hints_here = [r["topic_hint"] for r in items_rows if r["topic_hint"]]
                covered = [cov.get(h, {}) for h in hints_here] if hints_here else []
                has_cards = bool(covered) and all((c or {}).get("cards", 0) > 0 for c in covered)
                practised_ok = bool(covered) and all(
                    (c or {}).get("answered", 0) > 0 and (c or {}).get("avg_rating", 0) >= 3
                    for c in covered
                )
                resolved = all_resolved and has_cards and practised_ok

                age_last = (today - last_seen).days if last_seen else 999
                # 太老且已闭环的，不再占榜单名额
                if resolved and age_last > MAX_AGE_KEEP:
                    continue

                items.append({
                    "cause": cause,
                    "cause_kind": max(kinds.items(), key=lambda kv: kv[1])[0] if kinds else "concept",
                    "count": n,
                    "score": round(score, 3),
                    "topics": sorted(topic_ids)[:6],
                    # 去重：同一考点反复错会重复出现，报告里看着像 bug
                    "topic_hints": sorted({h for h in hints_here if h})[:4],
                    "has_cards": has_cards,
                    "resolved": resolved,
                    "last_seen": last_seen.isoformat() if last_seen else None,
                    "sample": next((r["title"] for r in items_rows if r["title"]), ""),
                    "sessions": sorted({r["sid"] for r in items_rows})[:5],
                })

            items.sort(key=lambda x: (-x["score"], x["cause"]))
            subjects[subject] = {
                "total_errors": len(mine),
                "open": sum(1 for r in mine if not r["resolved"]),
                "distinct_causes": len(grouped),
                "top_causes": items[:top_n],
            }
    finally:
        if db:
            db.close()

    # 给 weekly_flashcard_report.py / 出卡任务直接用的建议：
    # 高频 + 尚无卡 + 未闭环 → 最该出卡
    suggestions = []
    for subject, blk in subjects.items():
        for c in blk["top_causes"]:
            if not c["has_cards"] and not c["resolved"] and c["count"] >= 2:
                suggestions.append({
                    "subject": subject, "cause": c["cause"], "count": c["count"],
                    "score": c["score"], "topics": c["topics"],
                    "topic_hints": c["topic_hints"],
                })
    suggestions.sort(key=lambda x: -x["score"])

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "half_life_days": HALF_LIFE_DAYS,
        "resolved_factor": RESOLVED_FACTOR,
        "note": "派生文件，可安全重跑；消费方：大盘复盘/学习区上下文、weekly_flashcard_report.py、每日任务生成。",
        "totals": {
            "errors": len(rows),
            "open": sum(b["open"] for b in subjects.values()),
        },
        "subjects": subjects,
        "card_suggestions": suggestions[:20],
    }


def main():
    _setup_io()
    ap = argparse.ArgumentParser(description="聚合错题复盘会话，生成错因画像")
    ap.add_argument("--top", type=int, default=10, help="每科保留多少条错因（默认 10）")
    ap.add_argument("--json", action="store_true", help="打到 stdout，不落盘")
    args = ap.parse_args()

    if not REVIEW.is_dir():
        print(f"复盘目录不存在：{REVIEW}", file=sys.stderr)
        return 1

    patterns = build(args.top)

    if args.json:
        print(json.dumps(patterns, ensure_ascii=False, indent=2))
        return 0

    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(patterns, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, OUT)

    print(f"错因画像已写入：{OUT}")
    print(f"  错题 {patterns['totals']['errors']} 条，未闭环 {patterns['totals']['open']} 条")
    for s, blk in patterns["subjects"].items():
        tc = blk["top_causes"]
        print(f"  {s}：错因 {blk['distinct_causes'] if 'distinct_causes' in blk else 0} 类"
              f"，错题 {blk['total_errors']} 条，未闭环 {blk['open']} 条"
              + (f"，TOP：{tc[0]['cause']}（{tc[0]['count']}次）" if tc else ""))
    print(f"  建议出卡：{len(patterns['card_suggestions'])} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())

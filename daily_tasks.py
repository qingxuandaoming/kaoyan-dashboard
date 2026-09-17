#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
daily_tasks.py — 每日任务的 agent 侧命令行（2026-09-14）

为什么走 CLI 直连 SQLite 而不是调 HTTP：定时任务在 00:00 触发，那时 serve.js
可能压根没在运行。表 daily_tasks 是唯一真相源，Node 与 Python 各自实现同规则校验。

四个子命令：

    context  把「决定今天布置什么」所需的全部数据吐成 JSON（含用户近期自加/删除）
    write    把生成好的任务写进库（幂等，--replace 只清理自己布置且未完成未删的）
    seed     直接把 daily_planner 的目标清单写成任务（无 LLM 时的回退/链路自检）
    list     人工查看某天的任务

典型用法（定时任务唤起 agent 后）：

    cd C:/Users/92534/Desktop/考研/src
    python daily_tasks.py context --date today          # 1. 读数据
    #   → 据此合成今天的任务，写到 /tmp/tasks.json
    python daily_tasks.py write --date today --tasks-file /tmp/tasks.json --replace
"""

import argparse
import hashlib
import io
import json
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

if sys.platform == "win32":
    # 就地改编码，**不要**再套一层 TextIOWrapper：被换掉的那层 wrapper 没人引用，
    # GC 回收时会顺手 close 掉底层 buffer；此时本模块若又 import 了 daily_planner
    # （它照样包一层），后者的写入就会炸 "I/O operation on closed file"。
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DB_PATH") or (BASE_DIR / "question_bank.db"))

SUBJECTS = ["408", "政治", "数学一", "英语一"]
TEXT_MAX = 200


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------

def resolve_date(arg: str) -> str:
    """today / tomorrow / yesterday / YYYY-MM-DD → YYYY-MM-DD（本地日期）。

    ⚠️ 一律用 date.today()，不要用 utcnow()——UTC+8 下凌晨会差一天。
    """
    a = (arg or "today").strip().lower()
    today = date.today()
    if a in ("today", "今天", ""):
        return today.isoformat()
    if a in ("tomorrow", "明天"):
        return (today + timedelta(days=1)).isoformat()
    if a in ("yesterday", "昨天"):
        return (today - timedelta(days=1)).isoformat()
    try:
        return date.fromisoformat(a).isoformat()
    except ValueError:
        raise SystemExit(f"日期格式无法识别：{arg}（应为 today/tomorrow/yesterday 或 YYYY-MM-DD）")


def connect():
    if not DB_PATH.exists():
        raise SystemExit(f"数据库不存在：{DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def norm_text(t: str) -> str:
    """归一化任务文本，用于 ext_key 去重（空格差异不该产生重复任务）。"""
    return " ".join(str(t or "").split())


def ext_key(d: str, text: str) -> str:
    return hashlib.sha1(f"{d}|{norm_text(text)}".encode("utf-8")).hexdigest()


def validate(tasks):
    """与 serve.js 的 /api/tasks/add 保持同一套规则，两边都要拦。"""
    out = []
    for t in tasks:
        if isinstance(t, str):
            t = {"text": t}
        text = str(t.get("text") or "").strip()
        if not text:
            raise SystemExit("任务文本不能为空")
        if len(text) > TEXT_MAX:
            raise SystemExit(f"任务文本超过 {TEXT_MAX} 字：{text[:30]}…")
        subj = t.get("subject")
        subj = str(subj).strip() if subj else None
        if subj and subj not in SUBJECTS:
            raise SystemExit(f"科目只能是 {'/'.join(SUBJECTS)}，收到：{subj}")
        out.append({"text": text, "subject": subj or None})
    return out


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------

def recent_actions(conn, days):
    """近 N 天用户的自加与删除记录 —— agent 据此调整后续布置的核心信号。"""
    since = (date.today() - timedelta(days=days)).isoformat()
    added = [dict(r) for r in conn.execute(
        "SELECT text, subject, task_date, created_at FROM daily_tasks "
        "WHERE source = 'user' AND deleted = 0 AND task_date >= ? "
        "ORDER BY task_date DESC, id DESC", (since,))]
    deleted = [dict(r) for r in conn.execute(
        "SELECT text, source, subject, task_date, deleted_at FROM daily_tasks "
        "WHERE deleted = 1 AND task_date >= ? "
        "ORDER BY task_date DESC, id DESC", (since,))]
    return added, deleted


def review_by_subject(conn):
    """近 1 天 / 7 天各科练习量。窗口一律 localtime——review_date 存的是本地时间。

    四科**固定都出现**，没练的补 0：某科整条消失会让「这科没练」和「这科不存在」
    看起来一样，而 agent 恰恰要靠这个判断该给哪科补量。
    """
    out = {}
    for label, days in (("1d", 1), ("7d", 7)):
        rows = conn.execute(
            "SELECT COALESCE(t.subject,'') AS s, COUNT(*) AS n FROM review_log rl "
            "JOIN questions q ON q.id = rl.question_id "
            "LEFT JOIN topics t ON t.id = q.topic_id "
            "WHERE rl.review_date >= datetime('now','localtime', ?) "
            "GROUP BY s", (f"-{days} days",)).fetchall()
        got = {r["s"]: r["n"] for r in rows}
        out[label] = {s: got.get(s, 0) for s in SUBJECTS}
    return out


def planner_goals(d: str):
    """复用 daily_planner 生成的目标清单（不重造轮子）。

    daily_planner 会加载图谱/索引/题库，略慢；失败时返回空列表而不影响其它字段。
    """
    try:
        import daily_planner as dp
        md = dp.generate_plan_markdown(
            date.fromisoformat(d),
            dp.load_knowledge_graphs(),
            dp.load_notes_index(),
            dp.load_question_bank(),
            progress_data=dp.load_progress(),
        )
    except Exception as e:                                     # noqa: BLE001
        print(f"  WARN: daily_planner 生成失败（{e}），plan_goals 为空", file=sys.stderr)
        return []
    import re
    return [m.group(1).strip() for m in re.finditer(r"^- \[ \] (.+)$", md, re.M)]


def review_hot_causes(per_subject=3):
    """
    读错题复盘的错因画像（Review/_patterns.json）。

    为什么每日任务要看它：布置任务时最容易写成「复习数学」这种空话，
    而复盘沉淀出来的错因是**已经证明会丢分**的具体点，直接可执行。
    只读一个派生小文件（不遍历会话目录），文件不存在就返回空、不影响主流程。
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Review", "_patterns.json")
    if not os.path.isfile(path):
        return {}
    try:
        with io.open(path, encoding="utf-8") as f:
            p = json.load(f)
    except Exception:
        return {}
    out = {}
    for subject, blk in (p.get("subjects") or {}).items():
        rows = [c for c in (blk.get("top_causes") or []) if not c.get("resolved")]
        if not rows:
            continue
        out[subject] = [{
            "cause": c.get("cause"), "count": c.get("count"), "score": c.get("score"),
            "has_cards": c.get("has_cards"), "last_seen": c.get("last_seen"),
            "topics": (c.get("topic_hints") or [])[:2],
        } for c in rows[:per_subject]]
    return out


def card_reports():
    """待修的问题卡（闪卡练习里标记的「题目本身错了」）。

    为什么每日任务一定要带上它：用户在练习时一键标记，**出口只有这里**——
    agent 看到这份清单才会去改题/驳回/删卡（`node src/card_reports.js list|fix|dismiss`）。
    取数复用 daily_planner.load_card_reports，免得两处口径漂移。
    """
    try:
        import daily_planner as dp
        return dp.load_card_reports()
    except Exception as e:                                     # noqa: BLE001
        print(f"  WARN: 读取问题卡标记失败（{e}）", file=sys.stderr)
        return []


def cmd_context(args):
    d = resolve_date(args.date)
    conn = connect()
    tasks = [dict(r) for r in conn.execute(
        "SELECT id, text, source, subject, done, done_at, created_at FROM daily_tasks "
        "WHERE task_date = ? AND deleted = 0 ORDER BY source, id", (d,))]
    added, deleted = recent_actions(conn, args.days)
    rev = review_by_subject(conn)
    conn.close()

    y = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
    conn = connect()
    y_tasks = [dict(r) for r in conn.execute(
        "SELECT text, source, done FROM daily_tasks "
        "WHERE task_date = ? AND deleted = 0 ORDER BY id", (y,))]
    conn.close()
    y_done = [t["text"] for t in y_tasks if t["done"]]

    payload = {
        "date": d,
        "yesterday": {
            "date": y,
            "total": len(y_tasks),
            "done": len(y_done),
            "rate": round(len(y_done) / len(y_tasks) * 100) if y_tasks else 0,
            "done_texts": y_done,
        },
        "agent_tasks": [t for t in tasks if t["source"] == "agent"],
        "user_tasks": [t for t in tasks if t["source"] == "user"],
        "user_added_recent": added,
        "user_deleted_recent": deleted,
        "review_by_subject": rev,
        # 复盘错因：优先据此排「针对性重做/重刷」类任务，比泛泛的"复习某科"有用
        "review_hot_causes": review_hot_causes(),
        # 待修的问题卡：练习时标记的「题目本身错了」，修它的是 agent 而不是用户。
        # 复核命令：node src/card_reports.js list | show <id> | fix <id> --patch-file p.json
        #          | dismiss <id> --note … | delete <id> --note …
        "card_reports": card_reports(),
        "plan_goals": [] if args.no_plan else planner_goals(d),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# write / seed / list
# ---------------------------------------------------------------------------

def cmd_write(args):
    d = resolve_date(args.date)
    with open(args.tasks_file, "r", encoding="utf-8") as f:
        raw = json.load(f)
    tasks = validate(raw.get("tasks", raw) if isinstance(raw, dict) else raw)

    conn = connect()
    inserted = skipped = 0
    for t in tasks:
        cur = conn.execute(
            "INSERT OR IGNORE INTO daily_tasks (task_date, text, source, subject, ext_key) "
            "VALUES (?, ?, 'agent', ?, ?)",
            (d, t["text"], t["subject"], ext_key(d, t["text"])),
        )
        if cur.rowcount:
            inserted += 1
        else:
            skipped += 1

    replaced = 0
    if args.replace:
        keep = {ext_key(d, t["text"]) for t in tasks}
        # 只清理「自己布置的、未完成的、未被删的」——用户已完成的和用户已删的
        # 都要留着：前者是成果，后者是他明确拒绝过的，硬删会让 agent 反复重提。
        rows = conn.execute(
            "SELECT id, ext_key FROM daily_tasks "
            "WHERE task_date = ? AND source = 'agent' AND deleted = 0 AND done = 0", (d,)
        ).fetchall()
        for r in rows:
            if r["ext_key"] not in keep:
                conn.execute(
                    "UPDATE daily_tasks SET deleted = 1, "
                    "deleted_at = datetime('now','localtime'), "
                    "updated_at = datetime('now','localtime') WHERE id = ?", (r["id"],))
                replaced += 1
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "date": d, "inserted": inserted,
                      "skipped": skipped, "replaced": replaced}, ensure_ascii=False))
    return 0


def cmd_seed(args):
    d = resolve_date(args.date)
    goals = planner_goals(d)
    if not goals:
        print("daily_planner 没有产出目标，未写入任何任务", file=sys.stderr)
        return 1
    conn = connect()
    inserted = 0
    for g in goals:
        cur = conn.execute(
            "INSERT OR IGNORE INTO daily_tasks (task_date, text, source, ext_key) "
            "VALUES (?, ?, 'agent', ?)", (d, g, ext_key(d, g)))
        inserted += cur.rowcount
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "date": d, "goals": len(goals), "inserted": inserted},
                     ensure_ascii=False))
    return 0


def cmd_list(args):
    d = resolve_date(args.date)
    conn = connect()
    sql = "SELECT id, text, source, subject, done, deleted FROM daily_tasks WHERE task_date = ?"
    if not args.include_deleted:
        sql += " AND deleted = 0"
    sql += " ORDER BY deleted, done, id"
    rows = conn.execute(sql, (d,)).fetchall()
    conn.close()
    if not rows:
        print(f"{d} 没有任务")
        return 0
    print(f"{d} 共 {len(rows)} 条：")
    for r in rows:
        mark = "[x]" if r["done"] else "[ ]"
        tag = "我加的" if r["source"] == "user" else "AI  "
        dead = " (已删)" if r["deleted"] else ""
        subj = f" [{r['subject']}]" if r["subject"] else ""
        print(f"  {mark} #{r['id']} {tag}{subj} {r['text']}{dead}")
    return 0


def cmd_explain(args):
    """周报取数：近 N 天的错题解析记录，按线索聚合。

    用户明确说过这件事**不要每天做**——解析在答题当时就生成好了，记录留着，
    一周汇总一次即可。所以这里是给周定时任务用的取数口，不做任何生成。
    """
    conn = connect()
    rows = conn.execute(
        "SELECT thread_id, question_id, subject, topic_id, chosen, correct, role, content, created_at "
        "FROM explain_log WHERE created_at >= datetime('now','localtime', ?) "
        "ORDER BY thread_id, id", (f"-{args.days} days",)).fetchall()
    # 全部答错且记下了错选的题。数据源是 review_log 而不是 explain_log——
    # 后者只覆盖「生成过 AI 解析」的题，只看它会让周报漏掉一半错题。
    wrong = [dict(r) for r in conn.execute(
        "SELECT rl.question_id, rl.chosen, rl.rating, rl.review_date, "
        "       COALESCE(t.subject, '') AS subject, q.topic_id, "
        "       COALESCE(t.name, '') AS topic_name "
        "FROM review_log rl JOIN questions q ON q.id = rl.question_id "
        "LEFT JOIN topics t ON t.id = q.topic_id "
        "WHERE rl.rating <= 2 AND rl.chosen IS NOT NULL "
        "  AND rl.review_date >= datetime('now','localtime', ?) "
        "ORDER BY rl.review_date DESC", (f"-{args.days} days",))]
    conn.close()

    # 错题按考点聚合——反复错的考点才是要补的地方
    wrong_by_topic = {}
    for w in wrong:
        k = w["topic_id"] or "(未知考点)"
        wrong_by_topic[k] = wrong_by_topic.get(k, 0) + 1

    threads = {}
    for r in rows:
        t = threads.setdefault(r["thread_id"], {
            "thread_id": r["thread_id"], "question_id": r["question_id"],
            "subject": r["subject"], "topic_id": r["topic_id"],
            "chosen": r["chosen"], "correct": r["correct"],
            "created_at": r["created_at"], "turns": [],
        })
        t["turns"].append({"role": r["role"], "content": r["content"]})

    by_subject, by_topic = {}, {}
    for t in threads.values():
        s = t["subject"] or "(未分类)"
        by_subject[s] = by_subject.get(s, 0) + 1
        if t["topic_id"]:
            by_topic[t["topic_id"]] = by_topic.get(t["topic_id"], 0) + 1

    out_threads = []
    for t in threads.values():
        if args.full:
            out_threads.append(t)
            continue
        first = next((x["content"] for x in t["turns"] if x["role"] == "assistant"), "")
        out_threads.append({
            "thread_id": t["thread_id"], "question_id": t["question_id"],
            "subject": t["subject"], "topic_id": t["topic_id"],
            "chosen": t["chosen"], "correct": t["correct"],
            "created_at": t["created_at"], "turn_count": len(t["turns"]),
            "first_reply": first[:args.snippet],
        })

    print(json.dumps({
        "days": args.days,
        "thread_count": len(threads),
        "by_subject": by_subject,
        "by_topic": dict(sorted(by_topic.items(), key=lambda kv: -kv[1])),
        # 本周全部错题（含没生成过解析的），错选是最有信息量的字段
        "wrong_count": len(wrong),
        "wrong_by_topic": dict(sorted(wrong_by_topic.items(), key=lambda kv: -kv[1])),
        "wrong_answers": wrong,
        "threads": out_threads,
    }, ensure_ascii=False, indent=2))
    return 0


def main():
    p = argparse.ArgumentParser(description="每日任务的 agent 侧命令行")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("context", help="输出制定今日任务所需的全部数据（JSON）")
    c.add_argument("--date", default="today")
    c.add_argument("--days", type=int, default=7, help="回看用户自加/删除的天数")
    c.add_argument("--no-plan", action="store_true", help="跳过 daily_planner（更快）")
    c.set_defaults(func=cmd_context)

    w = sub.add_parser("write", help="写入 agent 生成的任务")
    w.add_argument("--date", default="today")
    w.add_argument("--tasks-file", required=True)
    w.add_argument("--replace", action="store_true",
                   help="把该日自己布置的、未完成未删的旧任务软删除")
    w.set_defaults(func=cmd_write)

    s = sub.add_parser("seed", help="用 daily_planner 的目标清单直接生成任务")
    s.add_argument("--date", default="tomorrow")
    s.set_defaults(func=cmd_seed)

    l = sub.add_parser("list", help="查看某天的任务")
    l.add_argument("--date", default="today")
    l.add_argument("--include-deleted", action="store_true")
    l.set_defaults(func=cmd_list)

    e = sub.add_parser("explain", help="周报取数：近 N 天的错题解析记录")
    e.add_argument("--days", type=int, default=7)
    e.add_argument("--full", action="store_true", help="带上完整对话（默认只给首轮摘要）")
    e.add_argument("--snippet", type=int, default=300, help="首轮解析截断长度")
    e.set_defaults(func=cmd_explain)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

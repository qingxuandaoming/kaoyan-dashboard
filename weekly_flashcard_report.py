#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weekly_flashcard_report.py — 每周闪卡补充的输入报告（只读）

用途：每周定时任务触发时，先跑这个脚本产出「本周该补什么卡」的结构化报告，
      再由 AI 依据报告撰写卡片、走 import_politics_cards.py 或
      generate_targeted_cards.py 的入库路径。

设计原则：**只读、不写库、不调 LLM**。报告里给的是「素材与缺口」，
出题判断留给读报告的一方。

素材来源（复用现有采集器，避免重复实现）：
  src/generate_targeted_cards.py 的 collect_recent_notes /
  collect_weak_topics / collect_uncovered_weighty / match_topic_id

用法：
    python src/weekly_flashcard_report.py              # 生成 markdown 报告
    python src/weekly_flashcard_report.py --json       # 机器可读
    python src/weekly_flashcard_report.py --days 7     # 调整"近日"窗口
"""

import argparse
import importlib.util
import io
import json
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict

SRC_DIR = os.path.dirname(os.path.abspath(__file__))

# 持有原 stdio 引用，防止被 GC 后关掉底层 buffer（见 _setup_io 注释）
_STDIO_REFS = None


def _setup_io():
    """确保 UTF-8 输出。

    ⚠️ 必须在 load_collectors() **之后**调用：generate_targeted_cards.py 在
    import 时也会重设 sys.stdout，两次包装会各自持有同一个 buffer，先被回收的
    那个 wrapper 会把 buffer 关掉，于是报 "I/O operation on closed file"。
    这里先持有旧引用，再按需包装。
    """
    global _STDIO_REFS
    if sys.platform != "win32":
        return
    if (sys.stdout.encoding or "").lower().replace("-", "") == "utf8":
        return
    _STDIO_REFS = (sys.stdout, sys.stderr)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

DB_PATH = os.path.join(SRC_DIR, "question_bank.db")
GTC_PATH = os.path.join(SRC_DIR, "generate_targeted_cards.py")


def load_collectors():
    """复用 generate_targeted_cards.py 的采集器（避免两套实现漂移）"""
    spec = importlib.util.spec_from_file_location("gtc", GTC_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def q(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def _tex_to_plain(s):
    """把 LaTeX 降级成纯文本。

    报告是纯文本出口（→ 飞书），数学卡 2026-09-13 起改用了 $...$ LaTeX，
    不降级就会在报告里露出 \\dfrac / \\int 这种源码。
    取不到转换器时退回"去掉 $ 定界符"，至少不出乱码。
    """
    try:
        import importlib.util as _ilu
        from pathlib import Path as _P
        _p = _P(__file__).resolve().parent / "tools" / "tex_plain.py"
        _spec = _ilu.spec_from_file_location("tex_plain", _p)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        return _mod.tex_to_plain(s)
    except Exception:
        return str(s or "").replace("$", "")


def _stem_of(raw, limit=70):
    """questions.content 是 JSON 字符串，取出题干用于报告展示（LaTeX 先降级成纯文本）"""
    if not raw:
        return ""
    try:
        d = json.loads(raw)
        s = d.get("stem") or d.get("question") or ""
    except Exception:
        s = str(raw)
    s = _tex_to_plain(s)
    s = " ".join(s.split())
    return s[:limit]


def build_report(conn, gtc, days):
    cutoff = f"-{days} days"
    r = {}

    # ---- 1. 各科目卡量 ----  ----
    r["by_subject"] = [
        {"subject": s or "(无考点)", "cards": n}
        for s, n in q(conn, """
            SELECT COALESCE(t.subject,'') AS s, COUNT(*) FROM cards c
              JOIN questions q ON c.question_id = q.id
              LEFT JOIN topics t ON q.topic_id = t.id
             GROUP BY s ORDER BY 2 DESC""")
    ]

    # ---- 2. 状态与成熟度 ----
    r["state"] = {}
    for st, n in q(conn, "SELECT state, COUNT(*) FROM cards GROUP BY state"):
        r["state"][{0: "新卡", 1: "学习中", 2: "复习", 3: "再学习"}.get(st, str(st))] = n

    # ---- 3. 空白高频考点（exam_weight>=2 且无卡）----
    r["uncovered_high_weight"] = [
        {"topic_id": i, "name": n, "subject": s, "weight": w}
        for i, n, s, w in q(conn, """
            SELECT t.id, t.name, t.subject, t.exam_weight FROM topics t
             WHERE t.exam_weight >= 2
               AND t.id NOT IN (SELECT DISTINCT topic_id FROM questions WHERE topic_id IS NOT NULL)
             ORDER BY t.exam_weight DESC, t.subject, t.id""")
    ]

    # ---- 4. 薄弱卡（按正确率从低到高，不是按错误次数）----
    # 错误次数只会选出练得最多的考点；正确率才区分「不会」和「练得多」。
    # 注意 cards 不能和 review_log 一起 JOIN——那样是笛卡尔积，COUNT/SUM 全会翻倍，
    # 所以卡数走子查询。
    weak = []
    for tid, name, subj, total, correct, ncards in q(conn, """
            SELECT t.id, t.name, t.subject,
                   COUNT(rl.id) AS total,
                   SUM(CASE WHEN rl.rating >= 3 THEN 1 ELSE 0 END) AS correct,
                   (SELECT COUNT(*) FROM cards c JOIN questions q2 ON c.question_id = q2.id
                     WHERE q2.topic_id = t.id) AS ncards
              FROM topics t
              JOIN questions  qq ON qq.topic_id = t.id
              JOIN review_log rl ON rl.question_id = qq.id
             GROUP BY t.id"""):
        total = int(total or 0)
        correct = int(correct or 0)
        if total <= 0 or correct >= total:
            continue          # 没练过、或一次没错的考点不算薄弱
        weak.append({"topic_id": tid, "name": name, "subject": subj,
                     "total": total, "correct": correct,
                     "accuracy": round(correct / total * 100),
                     "cards": int(ncards or 0)})
    weak.sort(key=lambda w: (w["accuracy"], -w["total"]))
    r["weak_topics"] = weak[:25]

    # ---- 5. 水蛭卡 ----
    r["leeches"] = [
        {"card_id": cid, "subject": s, "topic": tn, "lapses": lp, "stem": (st or "")[:60]}
        for cid, s, tn, lp, st in q(conn, """
            SELECT c.id, COALESCE(t.subject,''), COALESCE(t.name,''), c.lapses, q.content
              FROM cards c JOIN questions q ON c.question_id=q.id
              LEFT JOIN topics t ON q.topic_id=t.id
             WHERE COALESCE(c.leech,0)=1 ORDER BY c.lapses DESC LIMIT 20""")
    ]

    # ---- 6. 近日笔记（复用现有采集器）----
    try:
        recent = gtc.collect_recent_notes()
    except Exception as e:
        recent = []
        r["recent_notes_error"] = str(e)
    r["recent_notes"] = [
        {"file": x.get("file"), "prefix": x.get("prefix"),
         "days_ago": round((time.time() - x.get("mtime", 0)) / 86400, 1),
         "bytes": len(x.get("excerpt") or "")}
        for x in recent
    ]

    # ---- 7. 上次补充后新增的卡（避免重复出题）----
    r["recently_added"] = [
        {"source": s, "n": n} for s, n in q(conn, """
            SELECT COALESCE(source,'(无)'), COUNT(*) FROM questions
             WHERE created_at >= datetime('now', ?) GROUP BY source ORDER BY 2 DESC""", (cutoff,))
    ]
    r["recently_added_samples"] = [
        {"id": i, "subject": s, "topic": tn, "stem": _stem_of(c)}
        for i, s, tn, c in q(conn, """
            SELECT q.id, COALESCE(t.subject,''), COALESCE(t.name,''), q.content
              FROM questions q LEFT JOIN topics t ON q.topic_id=t.id
             WHERE q.created_at >= datetime('now', ?) ORDER BY q.created_at DESC LIMIT 15""", (cutoff,))
    ]

    # ---- 8. 政治源文件覆盖（政治走 politics_*.json，单独统计）----
    pol_src = os.path.join(SRC_DIR, "flashcards", "source")
    r["politics_sources"] = []
    if os.path.isdir(pol_src):
        for fn in sorted(os.listdir(pol_src)):
            if not (fn.startswith("politics_") and fn.endswith(".json")):
                continue
            try:
                with io.open(os.path.join(pol_src, fn), encoding="utf-8") as f:
                    d = json.load(f)
                r["politics_sources"].append({
                    "file": fn, "module": (d.get("meta") or {}).get("module"),
                    "cards": len(d.get("cards") or []),
                    "topics": len({c.get("topic_id") for c in (d.get("cards") or []) if c.get("topic_id")}),
                })
            except Exception:
                r["politics_sources"].append({"file": fn, "error": "解析失败"})

    # ---- 9. 复习量趋势（近 7 天）----
    r["review_trend"] = [
        {"date": d, "reviews": n, "accuracy": round(ok / n, 3) if n else None}
        for d, n, ok in q(conn, """
            SELECT date(review_date), COUNT(*), SUM(CASE WHEN rating>=3 THEN 1 ELSE 0 END)
              FROM review_log WHERE review_date >= datetime('now','localtime','-7 days')
             GROUP BY 1 ORDER BY 1""")
    ]

    # ---- 10. 复盘沉淀的高频错因（读 Review/_patterns.json）----
    # 这是错题复盘页攒出来的热数据：学生真实栽过、且还没出卡的点。
    # 补卡时它比「权重高但没错过」更该优先——那是已经证明会丢分的地方。
    # 只读一个小的派生文件，不去遍历会话目录，避免报告随数据量线性变慢。
    pat_path = os.path.join(os.environ.get("NOTES_ROOT", r"E:\NPEE"), "Review", "_patterns.json")
    r["review_patterns"] = {"generated_at": None, "suggestions": []}
    if os.path.isfile(pat_path):
        try:
            with io.open(pat_path, encoding="utf-8") as f:
                p = json.load(f)
            r["review_patterns"]["generated_at"] = p.get("generated_at")
            r["review_patterns"]["totals"] = p.get("totals")
            r["review_patterns"]["suggestions"] = (p.get("card_suggestions") or [])[:15]
        except Exception as exc:
            r["review_patterns"]["error"] = str(exc)

    return r


def render_markdown(r, days):
    L = []
    L.append(f"# 每周闪卡补充报告（近 {days} 天窗口）")
    L.append("")
    L.append("> 本报告只读，不改库。请据此决定本周补哪些卡，")
    L.append("> 并用对应导入器入库（政治走 politics_*.json，其余走 generate_targeted_cards.py）。")
    L.append("")

    L.append("## 1. 各科目卡量")
    for x in r["by_subject"]:
        L.append(f"- {x['subject']}：{x['cards']} 张")
    if r.get("state"):
        L.append("")
        L.append("状态分布：" + " · ".join(f"{k} {v}" for k, v in r["state"].items()))
    L.append("")

    L.append(f"## 2. 空白高频考点（权重≥2 且无卡）—— 共 {len(r['uncovered_high_weight'])} 个")
    if not r["uncovered_high_weight"]:
        L.append("无。所有高频考点均已有卡。")
    for x in r["uncovered_high_weight"][:40]:
        L.append(f"- [{x['subject']}] **{x['name']}**（{x['topic_id']}，权重 {x['weight']}）")
    L.append("")

    L.append(f"## 3. 薄弱考点（最该出强化卡，按正确率低→高）—— 共 {len(r['weak_topics'])} 个")
    if not r["weak_topics"]:
        L.append("无。")
    for x in r["weak_topics"][:15]:
        L.append(f"- [{x['subject']}] **{x['name']}**：正确率 {x['accuracy']}%"
                 f"（{x['correct']}/{x['total']} 题），现有 {x['cards']} 张卡")
    L.append("")

    if r["leeches"]:
        L.append(f"## 4. 水蛭卡 —— 共 {len(r['leeches'])} 张")
        for x in r["leeches"][:10]:
            L.append(f"- [{x['subject']}] {x['stem']}…（遗忘 {x['lapses']} 次）")
        L.append("")

    L.append(f"## 5. 近日修改的笔记 —— 共 {len(r['recent_notes'])} 篇")
    for x in r["recent_notes"]:
        L.append(f"- `{x['file']}`（{x['days_ago']} 天前，{x['bytes']} 字符）")
    L.append("")

    L.append(f"## 6. 近 {days} 天已新增的卡（避免重复出题）")
    if not r["recently_added"]:
        L.append("无。")
    for x in r["recently_added"]:
        L.append(f"- {x['source']}：{x['n']} 张")
    if r["recently_added_samples"]:
        L.append("")
        L.append("抽样（题干前 70 字）：")
        for x in r["recently_added_samples"]:
            L.append(f"  - [{x['subject']}] {x['stem']}…")
    L.append("")

    if r["politics_sources"]:
        L.append("## 7. 政治源文件现状")
        for x in r["politics_sources"]:
            if "error" in x:
                L.append(f"- `{x['file']}`：{x['error']}")
            else:
                L.append(f"- `{x['file']}`（{x['module']}）：{x['cards']} 张，覆盖 {x['topics']} 个考点")
        L.append("")

    if r["review_trend"]:
        L.append("## 8. 近 7 天复习量")
        L.append("")
        L.append("| 日期 | 复习量 | 正确率 |")
        L.append("|------|-------|-------|")
        for x in r["review_trend"]:
            acc = "—" if x["accuracy"] is None else f"{round(x['accuracy']*100)}%"
            L.append(f"| {x['date']} | {x['reviews']} | {acc} |")
        L.append("")

    rp = r.get("review_patterns") or {}
    L.append("## 9. 错题复盘沉淀的高频错因（优先补卡）")
    L.append("")
    if rp.get("error"):
        L.append(f"- ⚠ 画像读取失败：{rp['error']}")
    elif not rp.get("suggestions"):
        L.append("- （暂无：复盘页还没积累，或这些错因都已出卡并闭环）")
    else:
        L.append(f"> 画像生成于 {rp.get('generated_at')}"
                 + (f"，错题 {rp['totals']['errors']} 条、未闭环 {rp['totals']['open']} 条"
                    if rp.get("totals") else ""))
        L.append("> 这些是学生**真实反复栽过、且尚无对应卡**的点，权重含时间衰减，越近越重。")
        L.append("")
        L.append("| 科目 | 错因 | 次数 | 权重 | 涉及考点 |")
        L.append("|------|------|------|------|---------|")
        for s in rp["suggestions"]:
            hints = "、".join((s.get("topic_hints") or [])[:2]) or "—"
            L.append(f"| {s['subject']} | {s['cause']} | {s['count']} | {s['score']} | {hints} |")
    L.append("")

    L.append("---")
    L.append("")
    L.append("## 建议动作")
    L.append(f"1. **最优先**：给「第 9 节」复盘错因补卡——那是已经证明会丢分的地方（{len(rp.get('suggestions') or [])} 条待补）")
    L.append(f"2. 再给「第 2 节」的 {len(r['uncovered_high_weight'])} 个空白高频考点补卡（每点 1–2 张）")
    L.append("3. 给「第 3 节」的薄弱考点出强化卡，考查易混与易错细节")
    L.append("4. 若「第 5 节」有新笔记，盘活其中尚未出卡的知识点")
    L.append("5. 出卡前先看「第 6 节」，避免与近期新增重复（导入器另有 ext_key 幂等兜底）")
    L.append("6. 补完卡后把对应错题在复盘页勾成「已闭环」，画像会自动降权沉底，不再重复提醒")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="「近日」窗口天数")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非 markdown")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    gtc = load_collectors()   # 先加载（它会重设 stdout），再设置编码
    _setup_io()
    r = build_report(conn, gtc, args.days)
    conn.close()

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print(render_markdown(r, args.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrate_math_tasks.py —— 高数「章→讲」迁移的第 6b 步：修题库里任务文本的死路径。

`daily_tasks.text` 是每日任务的正文，里面会写「整理进 Math/高数/第6章_微分方程.md」这类
**可点击的载体路径**。章文件已删，这些路径变成死链；实测 57 条任务里有 5 条中招，
其中 4 条还是未完成状态（用户在大盘上仍看得见、点了会 404）。

只改 1:1 映射的三个章（第5→14讲、第6→15讲、第7→16讲），路径唯一确定、无歧义。
一对多的章（第1/2/3章）**不动**——那种任务文本里的路径本来就指不到某一讲，
留原样比猜一个讲更安全（这类任务实测为 0 条，脚本会报出来）。

⚠️ 只碰 `daily_tasks.text`。`explain_log` 是与 AI 的对话历史（日志，不是待办），
`card_reports.note` 是报卡时的问题描述，都不改。

用法：python tools/migrate_math_tasks.py [--apply]
"""
import argparse
import io
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
DB = os.environ.get("DB_PATH") or os.path.join(SRC, "question_bank.db")

# 1:1 的章 → 讲（来自 src/math_lectures.json 的 default 映射）
ONE2ONE = {
    "第4章_多元函数微分学.md": "第13讲_多元函数微分学.md",
    "第5章_多元函数积分学.md": "第14讲_二重积分.md",
    "第6章_微分方程.md": "第15讲_微分方程.md",
    "第7章_无穷级数.md": "第16讲_无穷级数.md",
    "第8章_曲线积分与曲面积分.md": "第18讲_多元函数积分学.md",
}
# 一对多：不敢猜，只报告
AMBIG = {
    "第1章_函数极限与连续.md": "第1讲 或 第2讲",
    "第2章_一元函数微分学.md": "第3–6讲",
    "第3章_一元函数积分学.md": "第8–12讲",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("[migrate_math_tasks] 找不到题库：%s" % DB)

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id, task_date, text, done, deleted FROM daily_tasks").fetchall()
    print("daily_tasks 共 %d 条" % len(rows))

    fix, amb = [], []
    for r in rows:
        t = r["text"] or ""
        new = t
        hits = []
        for old, newfn in ONE2ONE.items():
            if old in new:
                hits.append("%s → %s" % (old, newfn))
                new = new.replace(old, newfn)
        found_amb = [o for o in AMBIG if o in new]
        if hits:
            fix.append((r["id"], r["task_date"], r["done"], r["deleted"], hits, new))
        if found_amb:
            amb.append((r["id"], r["task_date"], found_amb))

    print()
    print("== 可无歧义修复（%d 条）==" % len(fix))
    for i, d, done, dele, hits, _ in fix:
        print("   id=%-4s %s done=%s deleted=%s ｜ %s" % (i, d, done, dele, "; ".join(hits)))
    print()
    print("== 一对多、不敢猜（%d 条，保持原样）==" % len(amb))
    for i, d, f in amb:
        print("   id=%-4s %s ｜ %s → %s" % (i, d, f[0], AMBIG[f[0]]))

    if not args.apply:
        print("\n[dry-run] 未写库。")
        con.close()
        return 0
    if not fix:
        print("\n无需修改。")
        con.close()
        return 0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(SRC, "backups", "question_bank.db.bak_task_paths_%s" % ts)
    os.makedirs(os.path.dirname(bak), exist_ok=True)
    con.close()
    shutil.copy2(DB, bak)
    print("\n已备份题库 → %s" % os.path.basename(bak))

    con = sqlite3.connect(DB)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for i, d, done, dele, hits, new in fix:
        con.execute("UPDATE daily_tasks SET text=?, updated_at=? WHERE id=?", (new, now, i))
        n += con.total_changes and 1 or 1
    con.commit()
    left = con.execute("SELECT COUNT(*) FROM daily_tasks WHERE text LIKE '%高数/第_章_%'").fetchone()[0]
    con.close()
    print("[apply] 改写 %d 条任务文本。剩余含「高数/第N章/」的任务：%d" % (len(fix), left))
    return 0


if __name__ == "__main__":
    sys.exit(main())

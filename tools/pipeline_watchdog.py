#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pipeline_watchdog.py — 「该跑的是不是跑了」的每日看门狗（只读、不花 API、约 1 秒）

为什么要有它：2026-10-04 复盘发现四条定时任务里三条已死，而**没人发现**——每日任务连超 3 次
被熔断暂停、早间回顾 cron 整个消失，数据侧一连 7 天没动静（daily_tasks 停在 09-27、
morning_review 停在 09-30），直到用户说「还是不行」才被翻出来。每周体检的粒度救不了这个：
七天里它会错六天。所以需要一条**每天**都跑、成本近似为零、只在"没跑成"时才出声的检查。

它跑在 02:00「每日任务生成」那条 cron 的第一步（同一个 agent，零额外 API 成本），
也可以随时手动跑：

    python tools\\pipeline_watchdog.py            # 打印状态；exit 1 = 有 FAIL
    python tools\\pipeline_watchdog.py --json logs\\watchdog.json

判据刻意用「昨天」而不是「今天」为界：02:00 跑的时候，今天的任务/回顾本来就还没生成
（任务由这条 cron 自己写、回顾 07:30 才写），拿今天当界会天天误报。
"""

import argparse
import io
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))
import paths  # noqa: E402

NOTES = Path(paths.NOTES_ROOT)
DB = SRC / "question_bank.db"

FAIL, WARN, INFO = [], [], []


def newest_md(root: Path):
    if not root.is_dir():
        return None
    newest = None
    for dp, dn, fn in os.walk(root):
        dn[:] = [x for x in dn if x not in (".obsidian", ".trash", ".git", "assets", "PDF",
                                            "0参考资料", "node_modules", "uploads")]
        for f in fn:
            if f.endswith(".md"):
                m = os.path.getmtime(os.path.join(dp, f))
                if newest is None or m > newest:
                    newest = m
    return newest


def main() -> int:
    ap = argparse.ArgumentParser(description="「该跑的是不是跑了」每日看门狗")
    ap.add_argument("--json", default="", help="把结论落成 JSON")
    ap.add_argument("--stale-days", type=int, default=3, help="归因/练习新旧容忍天数")
    args = ap.parse_args()

    today = date.today()
    yest = today - timedelta(days=1)
    print(f"数据链路看门狗 · {today} {datetime.now():%H:%M}")

    # ---- 1. 每日任务：02:00 那条 cron 每天写当天任务；昨天之前都没有 = 它没跑成 ----
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    last_task = (con.execute("SELECT MAX(task_date) d FROM daily_tasks").fetchone()["d"] or "")
    n_today = con.execute("SELECT COUNT(*) n FROM daily_tasks WHERE task_date=? AND deleted=0",
                          (today.isoformat(),)).fetchone()["n"]
    if last_task and date.fromisoformat(last_task) >= yest:
        print(f"  ✓ 每日任务：最新 {last_task}（今天 {n_today} 条）")
    else:
        FAIL.append(f"每日任务没在跑：库里最新是 {last_task or '（空）'}，"
                    "预期每天 02:00 会写当天任务 → 查 cron 是否被熔断暂停/超时")

    # ---- 2. 早间回顾：07:30 那条 cron 每天写当天 ----
    mr = SRC / "morning_review.json"
    if mr.exists():
        j = json.load(io.open(mr, encoding="utf-8"))
        ks = sorted(j.get("days") or {})
        last_mr = ks[-1] if ks else ""
        if last_mr and date.fromisoformat(last_mr) >= yest:
            print(f"  ✓ 早间回顾：最新 {last_mr}")
        else:
            FAIL.append(f"早间回顾没在跑：morning_review.json 最新是 {last_mr or '（空）'}，"
                        "预期每天 07:30 写当天 → 查 cron 在不在（2026-10-01~10-04 它消失过）")
    else:
        FAIL.append("morning_review.json 不存在")

    # ---- 3. 归因新鲜度：离线旁路，没有任何东西自动跑它 ----
    try:
        spec = json.load(io.open(SRC / "attribution_spec.json", encoding="utf-8"))
        runs = {r["domain"]: dict(r) for r in con.execute("SELECT * FROM note_attr_runs")}
        stale = []
        newest_all = None
        for d in spec.get("domains") or []:
            nm = newest_md(NOTES / d["corpus_root"])
            run = runs.get(d["id"])
            if nm is None or not run or not run.get("finished_at"):
                continue
            if newest_all is None or nm > newest_all:
                newest_all = nm
            gap = (datetime.fromtimestamp(nm) - datetime.fromisoformat(str(run["finished_at"]))).days
            if gap >= args.stale_days:
                stale.append(f"{d['id']} {gap} 天")
        if stale:
            WARN.append("归因过期：" + "、".join(stale)
                        + " → `node tools\\ai_attribute_notes.js --all`（增量，实测 46 秒，只问变了的段落）")
        else:
            print(f"  ✓ 归因：最新笔记 {datetime.fromtimestamp(newest_all):%m-%d %H:%M}"
                  if newest_all else "  ? 归因：无数据")
    except Exception as e:
        WARN.append(f"归因新鲜度没查成：{e}")

    # ---- 4. 大盘产物是否吸收了最新索引 ----
    idxs = [NOTES / s / "notes_index.json" for s in ("Math", "408", "Politics", "English")]
    idxs = [p for p in idxs if p.exists()]
    if idxs:
        newest_idx = max(os.path.getmtime(p) for p in idxs)
        behind = []
        for name in ("dashboard.html", "dashboard_data.json"):
            p = SRC / name
            if not p.exists():
                FAIL.append(f"{name} 不存在（管道没跑过？）")
            elif newest_idx > os.path.getmtime(p) + 5:
                behind.append(name)
        if behind:
            WARN.append("、".join(behind) + " 比笔记索引旧（管道没吸收最新索引，"
                         "跑 run_pipeline.py --no-plan）")
        else:
            print("  ✓ 大盘产物不比索引旧")

    # ---- 5. 服务 / 待修卡 / 练习情况（信息项，不算错）----
    try:
        port = io.open(SRC / ".serve_port").read().strip()
    except Exception:
        port = ""
    if port:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
                up = r.status == 200
        except Exception:
            up = False
        INFO.append(f"大盘服务{'在跑' if up else '没在跑'}（{port}）")
    open_reports = con.execute("SELECT COUNT(*) n FROM card_reports WHERE status='open'").fetchone()["n"]
    if open_reports:
        INFO.append(f"待修闪卡 {open_reports} 张（修卡是每日任务 cron 的活）")
    last_rev = con.execute("SELECT MAX(review_date) d FROM review_log").fetchone()["d"] or ""
    if last_rev:
        gap = (today - date.fromisoformat(last_rev[:10])).days
        if gap >= args.stale_days:
            INFO.append(f"闪卡已 {gap} 天没有新作答（最后 {last_rev[:10]}）——他最近可能在纸上做题")
    con.close()

    for i in INFO:
        print("  · " + i)

    print("-" * 52)
    if FAIL:
        for f in FAIL:
            print("  FAIL  " + f)
        print(f"结论：❌ {len(FAIL)} 项该跑没跑")
    else:
        print("结论：✅ 该跑的都跑了")
    for w in WARN:
        print("  WARN  " + w)

    if args.json:
        p = Path(args.json)
        if not p.is_absolute():
            p = SRC / p
        p.parent.mkdir(parents=True, exist_ok=True)
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"date": today.isoformat(), "fail": FAIL, "warn": WARN, "info": INFO},
                      f, ensure_ascii=False, indent=2)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

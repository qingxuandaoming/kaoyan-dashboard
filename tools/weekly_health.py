#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
weekly_health.py — 考研数据链路的「一次跑完、只报异常」体检（每周体检定时任务用）

为什么要有它：这套系统的历史 bug 几乎都是**静默失败**（目录改名、路径写错、前缀对不上
→ 统计恒为 0 却毫不报错，缺口列表长期骗人）。但「每周把四条命令跑一遍、再从上千行输出里
找告警」这件事本身也一直失败——2026-10-04 复盘时发现每周体检定时任务的输出是
`Task timed out after 12 minute(s)`：让 agent 现跑现读，它会把预算花在探索上，而不是判断上。

于是把**机械部分**（跑命令、抓告警、对水位、查完整性台账）固化成这一个脚本：
它只输出「异常 + 建议动作」，agent 拿到的是几十行结论，剩下的判断（要不要改代码、
要不要补材料）才是它的活。

跑的东西：
  1. tools/check_metrics_drift.py   口径注册表 ↔ 源码锚点 + 路径分离闸门 + NOTES_ROOT 体检
  2. run_pipeline.py --no-plan      英语索引 → 笔记索引 → 覆盖率 → 错因画像 → 笔记归因口径 → 大盘
  3. tools/audit_notes.py           各科笔记体检（断链 / 无效锚点 / 表格 / 公式闭合）
  4. 库与文件水位                   笔记索引 vs 大盘产物、归因完整性台账、数据源新鲜度、待修卡

退出码：0 = 无异常；1 = 有 FAIL（异常清单在最后）。
"""

import argparse
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
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

PY = sys.executable
NOTES = Path(paths.NOTES_ROOT)
DB = SRC / "question_bank.db"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

FAIL, WARN, INFO = [], [], []


def run(cmd, cwd=SRC, timeout=900):
    t = time.time()
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, encoding="utf-8",
                           errors="replace", env=ENV, timeout=timeout)
        return r.returncode, (r.stdout or ""), (r.stderr or ""), time.time() - t
    except subprocess.TimeoutExpired:
        return 124, "", f"超时（>{timeout}s）", time.time() - t


def interesting(text, pats, limit=12):
    """从一大堆输出里只留值得人看的行（告警/错误），去重后限量。"""
    keep, seen = [], set()
    rx = re.compile("|".join(pats))
    for ln in text.splitlines():
        s = ln.strip()
        if not s or not rx.search(s):
            continue
        key = re.sub(r"\d+", "#", s)          # 同型告警只留一条
        if key in seen:
            continue
        seen.add(key)
        keep.append(s)
        if len(keep) >= limit:
            break
    return keep


# ---------------------------------------------------------------------------
# 1. 口径与路径闸门
# ---------------------------------------------------------------------------

def step_drift():
    rc, so, se, dt = run([PY, str(SRC / "tools" / "check_metrics_drift.py")])
    print(f"[1] check_metrics_drift  {dt:.1f}s  exit={rc}")
    if rc != 0:
        FAIL.append("口径/路径闸门未通过（check_metrics_drift）")
        for ln in interesting(so + se, [r"FAIL", r"漂移", r"缺失", r"WARN", r"Traceback"], 15):
            print("    " + ln)
    else:
        tail = [l for l in so.splitlines() if "一致" in l or "✅" in l]
        print("    " + (tail[-1].strip() if tail else "通过"))
    return rc == 0


# ---------------------------------------------------------------------------
# 2. 管道
# ---------------------------------------------------------------------------

def step_pipeline(skip=False):
    if skip:
        print("[2] run_pipeline  跳过（--no-pipeline）")
        return True
    rc, so, se, dt = run([PY, str(SRC / "run_pipeline.py"), "--no-plan"])
    print(f"[2] run_pipeline --no-plan  {dt:.1f}s  exit={rc}")
    if rc != 0:
        FAIL.append("管道非零退出（run_pipeline）")
        for ln in (so + se).splitlines()[-12:]:
            print("    " + ln.strip())
        return False
    warns = interesting(so, [r"告警", r"WARN", r"⚠", r"未能落到图谱", r"超出图谱范围"])
    if warns:
        WARN.extend(warns)
        print(f"    管道输出告警 {len(warns)} 条：")
        for w in warns:
            print("      " + w)
    else:
        print("    无告警")
    # 覆盖率总览（最后几行 Summary 里的 TOTAL）
    tot = [l.strip() for l in so.splitlines() if "TOTAL" in l]
    if tot:
        print("    " + tot[-1])
    return True


# ---------------------------------------------------------------------------
# 2b. 归因增量（离线层自己不会跑，必须有人踢它）
# ---------------------------------------------------------------------------

def step_attribution(skip=False):
    """把「笔记→考点」的 AI 归因按增量刷新一遍。

    为什么要放进体检：归因是**离线旁路**（不在 run_pipeline 里），没有任何东西会自动跑它。
    2026-10-04 实测：408/数学的笔记已比归因新 6 天，判定条目都还在、闸门全绿，但大盘的
    覆盖与缺口一直是 6 天前正文的结论——这跟当年"笔记监控不全面"是同一类病（静默过期）。
    增量很便宜：内容指纹缓存，只问新写/改过的段落（803 文件里 168 段，46 秒）。
    """
    if skip:
        print("[2b] 归因增量  跳过（--no-attribution）")
        return
    js = SRC / "tools" / "ai_attribute_notes.js"
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    if not os.path.exists(node) or not js.exists():
        WARN.append("找不到 node 或 ai_attribute_notes.js，归因没刷新（下面的新鲜度检查会照实报）")
        print("[2b] 归因增量  跳过（没有 node）")
        return
    rc, so, se, dt = run([node, str(js), "--all"], timeout=1800)
    last = [l.strip() for l in so.splitlines() if "命中缓存" in l or "本次判定" in l or "编造" in l]
    print(f"[2b] 归因增量（node tools/ai_attribute_notes.js --all）  {dt:.0f}s  exit={rc}")
    for l in last[-1:]:
        print("    " + l)
    if rc != 0:
        WARN.append(f"归因增量非零退出（exit={rc}）：{(se or so).strip().splitlines()[-1][:160] if (se or so).strip() else ''}")
    for ln in interesting(so, [r"失败", r"编造", r"WARN", r"错误"], 6):
        if "0 个" not in ln and "失败 0" not in ln:
            WARN.append("归因：" + ln)


# ---------------------------------------------------------------------------
# 3. 笔记体检
# ---------------------------------------------------------------------------

def step_audit(subjects):
    print("[3] audit_notes（各科笔记体检）")
    tot = 0
    for name in subjects:
        root = NOTES / name
        if not root.is_dir():
            INFO.append(f"笔记目录不存在，跳过体检：{root}")
            continue
        rc, so, se, dt = run([PY, str(SRC / "tools" / "audit_notes.py")], cwd=root, timeout=300)
        m = re.search(r"真问题共\s*(\d+)\s*处", so)
        n = int(m.group(1)) if m else -1
        m2 = re.search(r"风格提示共\s*(\d+)\s*处", so)
        style = int(m2.group(1)) if m2 else -1
        print(f"    {name}: {dt:.1f}s exit={rc} 真问题={n if n >= 0 else '?'} 风格提示={style if style >= 0 else '?'}")
        if rc != 0 and n < 0:
            FAIL.append(f"{name} 笔记体检没跑出结论（audit_notes exit={rc}）")
            continue
        if n > 0:
            tot += n
            print(f"      ⚠ {name} 有 {n} 处真问题，明细：")
            for ln in interesting(so, [r"^⚠", r"断链", r"无效锚点", r"表格", r"未闭合"], 6):
                print("        " + ln)
    if tot:
        WARN.append(f"笔记体检真问题共 {tot} 处（分布见上；这些是笔记自身的内容问题，"
                    "不要代改——用户有并行笔记会话，列文件与处数给他即可）")
    return tot


# ---------------------------------------------------------------------------
# 4. 水位与台账
# ---------------------------------------------------------------------------

def mtime(p: Path):
    return datetime.fromtimestamp(p.stat().st_mtime) if p.exists() else None


def step_watermarks(subjects):
    print("[4] 水位与台账")
    # 4a 索引 vs 产物：索引比产物新说明管道没吸收这次笔记改动
    idx = [NOTES / s / "notes_index.json" for s in subjects]
    idx = [p for p in idx if p.exists()]
    prod = SRC / "dashboard_data.json"
    yaml_i = SRC / "笔记索引.yaml"
    newest_idx = max((mtime(p) for p in idx), default=None)
    prod_t = mtime(prod)
    yaml_t = mtime(yaml_i)
    if newest_idx and prod_t and newest_idx > prod_t:
        FAIL.append(f"笔记索引（{newest_idx:%m-%d %H:%M}）比大盘产物（{prod_t:%m-%d %H:%M}）新——管道没吃进去")
    if newest_idx and yaml_t and newest_idx > yaml_t:
        FAIL.append(f"笔记索引（{newest_idx:%m-%d %H:%M}）比 src/笔记索引.yaml（{yaml_t:%m-%d %H:%M}）新")
    print(f"    笔记索引最新 {newest_idx:%m-%d %H:%M} | 笔记索引.yaml {yaml_t:%m-%d %H:%M} | dashboard_data {prod_t:%m-%d %H:%M}"
          if (newest_idx and yaml_t and prod_t) else "    （有产物缺失，跳过水位对拍）")

    # 4b 归因完整性台账：半截数据比没有数据更危险（note_attr_runs 是闸门）
    spec_pv, spec = "", {}
    try:
        spec = json.load(io.open(SRC / "attribution_spec.json", encoding="utf-8"))
        spec_pv = spec.get("prompt_version", "")
    except Exception as e:
        FAIL.append(f"读不到 attribution_spec.json：{e}")
    if DB.exists():
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute("SELECT * FROM note_attr_runs").fetchall()
        except sqlite3.OperationalError:
            rows, FAIL[:] = [], FAIL + ["note_attr_runs 表不存在——归因层没跑过或库是旧的"]
        bad = []
        for r in rows:
            if r["units_pending"]:
                bad.append(f"{r['domain']} 还有 {r['units_pending']} 段没判（半截，大盘会回退规则口径）")
            if spec_pv and r["prompt_ver"] != spec_pv:
                bad.append(f"{r['domain']} 的归因是 {r['prompt_ver']}，spec 已是 {spec_pv}——判定语义变了要重跑")
        if bad:
            FAIL.extend(bad)
            for b in bad:
                print("    ⚠ " + b)
        elif rows:
            print(f"    归因台账 OK：{len(rows)} 个域全部 units_pending=0 且 prompt_ver={spec_pv}")

        # 4b-2 归因**新鲜度**：只查「跑没跑完」是不够的——2026-10-04 发现 408/数学的笔记比
        # 归因新 6 天，闸门却是绿的：判定条目还在，只是判的是 6 天前的正文，大盘的覆盖/缺口
        # 一直是旧结论。**半截数据危险，过期数据同样是骗人的。**
        if spec.get("domains"):
            newest_all, stale = None, []
            for d in spec["domains"]:
                root = NOTES / d["corpus_root"]
                if not root.is_dir():
                    continue
                newest = None
                for dp, dn, fn in os.walk(root):
                    dn[:] = [x for x in dn if x not in (".obsidian", ".trash", ".git", "assets",
                                                        "PDF", "0参考资料", "node_modules", "uploads")]
                    for f in fn:
                        if f.endswith(".md"):
                            m = os.path.getmtime(os.path.join(dp, f))
                            if newest is None or m > newest:
                                newest = m
                run = next((r for r in rows if r["domain"] == d["id"]), None)
                if not run or not run["finished_at"] or newest is None:
                    continue
                gap = (datetime.fromtimestamp(newest) - datetime.fromisoformat(str(run["finished_at"]))).days
                if newest_all is None or newest > newest_all:
                    newest_all = newest
                if gap >= 1:
                    stale.append(f"{d['id']} 笔记比归因新 {gap} 天")
            if stale:
                WARN.append("归因已过期（大盘覆盖/缺口是旧正文的结论）：" + "；".join(stale)
                            + "　→ 跑 `node tools\\ai_attribute_notes.js --all`（增量，只问变了的文件；"
                              "2026-10-04 实测 803 文件 / 168 段新判定 / 46 秒）")
                for s in stale:
                    print("    ⚠ " + s)

        # 4c 数据源新鲜度（信息项：说清「最近有没有在用」）
        def last(q):
            try:
                v = con.execute(q).fetchone()
                return (v[0] if v else "") or ""
            except Exception:
                return "?"
        fres = {
            "闪卡作答 review_log": last("SELECT MAX(review_date) FROM review_log"),
            "AI 提问 explain_log": last("SELECT MAX(created_at) FROM explain_log"),
            "每日任务 daily_tasks": last("SELECT MAX(task_date) FROM daily_tasks"),
            "打卡 mr_checkin": last("SELECT MAX(check_date) FROM mr_checkin"),
        }
        for k, v in fres.items():
            d = str(v)[:10]
            print(f"    {k}: {d or '（空）'}")
            if d and d != "?":
                try:
                    gap = (date.today() - date.fromisoformat(d)).days
                    if gap >= 3:
                        INFO.append(f"{k} 已 {gap} 天没有新数据（最后一次 {d}）")
                except Exception:
                    pass
        try:
            open_reports = con.execute("SELECT COUNT(*) FROM card_reports WHERE status='open'").fetchone()[0]
            cards = con.execute("SELECT COUNT(*), SUM(suspended) FROM cards").fetchone()
            print(f"    待修闪卡 {open_reports} 张 | 卡片 {cards[0]} 张（下架 {cards[1] or 0}）")
            if open_reports:
                INFO.append(f"闪卡修复队列还有 {open_reports} 张（cron 每日任务负责处理，若长期不清说明那条链没跑）")
        except Exception:
            pass
        con.close()

    mrp = SRC / "morning_review.json"
    if mrp.exists():
        try:
            j = json.load(io.open(mrp, encoding="utf-8"))
            last_day = sorted(j.get("days") or {})[-1]
            gap = (date.today() - date.fromisoformat(last_day)).days
            print(f"    早间回顾最后一天 {last_day}（{gap} 天前）")
            if gap >= 3:
                INFO.append(f"早间回顾 {gap} 天没更新（最后 {last_day}）——检查 morning-review 定时任务")
        except Exception as e:
            FAIL.append(f"morning_review.json 读不动：{e}")


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="考研数据链路每周体检（一次跑完、只报异常）")
    ap.add_argument("--no-pipeline", action="store_true", help="跳过 run_pipeline（只查不改）")
    ap.add_argument("--no-attribution", action="store_true", help="跳过归因增量（不花 API 额度）")
    ap.add_argument("--subjects", default="Math,408,Politics,English", help="体检哪些笔记科目根")
    ap.add_argument("--json", default="", help="把结论落成 JSON（供别的脚本消费）")
    args = ap.parse_args()
    subjects = [s.strip() for s in args.subjects.split(",") if s.strip()]

    t0 = time.time()
    print(f"考研数据链路体检 · {date.today():%Y-%m-%d %H:%M}｜代码根 {SRC}｜笔记根 {NOTES}")
    print("-" * 64)
    step_drift()
    step_attribution(args.no_attribution)
    step_pipeline(args.no_pipeline)
    step_audit(subjects)
    step_watermarks(subjects)
    print("-" * 64)

    if FAIL:
        print(f"结论：❌ 有 {len(FAIL)} 项异常")
        for f in FAIL:
            print("  FAIL  " + f)
    else:
        print("结论：✅ 数据链路无异常")
    if WARN:
        print(f"需要注意（{len(WARN)}）：")
        for w in WARN:
            print("  WARN  " + w)
    if INFO:
        print(f"信息项（{len(INFO)}）：")
        for i in INFO:
            print("  INFO  " + i)
    print(f"用时 {time.time() - t0:.1f}s")

    if args.json:
        p = Path(args.json)
        if not p.is_absolute():
            p = SRC / p
        p.parent.mkdir(parents=True, exist_ok=True)
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"date": date.today().isoformat(), "fail": FAIL, "warn": WARN, "info": INFO},
                      f, ensure_ascii=False, indent=2)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

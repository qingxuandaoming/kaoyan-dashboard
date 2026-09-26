#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_pipeline.py —— 考研学习数据一键流水线（本地大盘版）。

顺序执行四个有数据依赖的脚本，任一失败不中断后续（记录错误继续），
最后汇总各步状态与关键输出。

流程：
    tools/gen_english_index.py  英语 md → English/notes_index.json（必须先于 build_index）
    build_index.py      各科 notes_index.json → src/笔记索引.yaml
    gap_analysis.py     知识图谱 + 索引 + progress.json → 覆盖率/缺口
    daily_planner.py    生成明日学习计划 schedule/daily/plan_YYYY-MM-DD.md
    generate_dashboard.py  生成本地大盘 src/dashboard.html

用法：
    python src/run_pipeline.py            # 全量跑
    python src/run_pipeline.py --no-plan  # 跳过 daily_planner（只刷新索引与大盘）
    python src/run_pipeline.py --if-stale # 大盘不陈旧就直接跳过（启动器用，省时间）

--if-stale 的新鲜度判据：dashboard.html 的 mtime 晚于全部输入才跳过。
输入 = 本流水线各步脚本 + 配置/图谱/各科 notes_index.json + 英语 md +
morning_review.json；题库另按「水位」对拍（作答日志 max rowid，构建时记进
dashboard_data.json 的 db_watermark）——WAL 下 db 的 mtime 会被 checkpoint
无端刷新，不可靠。笔记正文不在内：大盘只消费索引，正文改动经 notes_entry
落索引后自然会触发。
"""
import io
import json
import os
import subprocess
import sys
from datetime import date, datetime

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import paths  # 路径单一事实源

SRC = paths.SRC_DIR
ROOT = paths.NOTES_ROOT
PROGRESS = os.path.join(SRC, "progress.json")
DASHBOARD = os.path.join(SRC, "dashboard.html")


def check_progress_freshness():
    """progress.json 超过 3 天未更新时提醒（计划会失真）。"""
    try:
        with open(PROGRESS, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        updated = data.get("updated", "")
        d = datetime.strptime(updated, "%Y-%m-%d").date()
        gap_days = (date.today() - d).days
        if gap_days > 3:
            print(f"  [警告] progress.json 已 {gap_days} 天未更新（updated={updated}）。")
            print("         请先运行 kaoyan-progress-sync 或晚间回顾 Step 0 同步真实进度，")
            print("         否则计划与大盘数据会失真。")
        else:
            print(f"  [OK] progress.json 新鲜（updated={updated}，{gap_days} 天前）")
    except Exception as e:
        print(f"  [警告] 无法解析 progress.json：{e}")


def run_step(name, args):
    print("\n" + "=" * 60)
    print(f"[STEP] {name}")
    print("=" * 60)
    try:
        proc = subprocess.run(
            [sys.executable] + args,
            cwd=SRC,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        if proc.stdout:
            print(proc.stdout.strip()[-4000:])
        if proc.returncode != 0:
            print(f"[FAIL] {name} 退出码 {proc.returncode}")
            if proc.stderr:
                print(proc.stderr.strip()[-2000:])
            return False
        print(f"[OK] {name}")
        return True
    except subprocess.TimeoutExpired:
        print(f"[FAIL] {name} 超时（>600s）")
        return False
    except Exception as e:
        print(f"[FAIL] {name} 异常：{e}")
        return False


def build_steps(skip_plan):
    steps = [
        # 英语笔记是普通 md，没有 note-meta:entry 块，必须先扫成 notes_index.json，
        # 否则 build_index.py 读不到英语，大盘英语 15 格会重新变回全 0。
        ("tools/gen_english_index.py（英语笔记索引）",
         [os.path.join(SRC, "tools", "gen_english_index.py")]),
        ("build_index.py（重建笔记索引）", [os.path.join(SRC, "build_index.py")]),
        ("gap_analysis.py（覆盖率与缺口）", [os.path.join(SRC, "gap_analysis.py")]),
        # 错题复盘的错因画像：大盘「复/学/首页」与每周出卡报告都读这一份派生结果。
        # 放主管道里，保证每次刷新大盘时画像同步更新（脚本只读 Review 下的 errors.json，毫秒级）。
        ("build_review_patterns.py（错因画像）", [os.path.join(SRC, "build_review_patterns.py")]),
    ]
    if not skip_plan:
        steps.append(("daily_planner.py（明日计划）", [os.path.join(SRC, "daily_planner.py")]))
    steps.append(("generate_dashboard.py（本地大盘）", [os.path.join(SRC, "generate_dashboard.py")]))
    return steps


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return None


def collect_inputs(steps, skip_plan):
    """--if-stale 的输入清单：任何一个比 dashboard.html 新就算陈旧。"""
    inputs = [os.path.abspath(__file__)]
    inputs += [args[0] for _, args in steps]
    inputs += [os.path.join(SRC, n) for n in (
        "subjects.json", "metrics_spec.json", "math_lectures.json",
        "paths.json", "morning_review.json",
    )]
    if not skip_plan:
        inputs.append(os.path.join(SRC, "progress.json"))
    kg = os.path.join(SRC, "knowledge_graph")
    if os.path.isdir(kg):
        inputs += [os.path.join(kg, n) for n in os.listdir(kg) if n.endswith(".json")]
    try:
        for subj in os.listdir(ROOT):
            idx = os.path.join(ROOT, subj, "notes_index.json")
            if os.path.isfile(idx):
                inputs.append(idx)
        # 英语索引由 md 现扫，英语正文改动要能触发
        eng = os.path.join(ROOT, "English")
        if os.path.isdir(eng):
            for dirpath, _dirnames, filenames in os.walk(eng):
                for fn in filenames:
                    if fn.endswith(".md"):
                        inputs.append(os.path.join(dirpath, fn))
    except OSError:
        pass
    # 题库不用 mtime 判（WAL checkpoint 会无作答也刷新 mtime），改用水位对拍，见 check_stale
    return inputs


def _db_watermark_now():
    """当前题库作答水位；与 generate_dashboard.py 构建时写进
    dashboard_data.json 的 db_watermark 是同一份 SQL 口径。"""
    import sqlite3
    db = os.path.join(SRC, "question_bank.db")
    if not os.path.exists(db):
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        wm = str(conn.execute(
            "SELECT (SELECT MAX(rowid) FROM review_log) || ':' ||"
            " (SELECT MAX(rowid) FROM cards) || ':' ||"
            " (SELECT MAX(rowid) FROM explain_log) || ':' ||"
            " (SELECT MAX(rowid) FROM daily_tasks)"
        ).fetchone()[0])
        conn.close()
        return wm
    except Exception:
        return None


def check_stale(steps, skip_plan):
    """返回 (stale:bool, reason:str)。dashboard.html 不存在 = 陈旧。"""
    dash_mt = _mtime(DASHBOARD)
    if dash_mt is None:
        return True, "dashboard.html 还不存在"
    newest, newest_mt = None, dash_mt
    for p in collect_inputs(steps, skip_plan):
        mt = _mtime(p)
        if mt is not None and mt > newest_mt:
            newest, newest_mt = p, mt
    if newest is not None:
        try:
            rel = os.path.relpath(newest, SRC)
        except ValueError:
            rel = newest
        dt = datetime.fromtimestamp(newest_mt).strftime("%m-%d %H:%M")
        return True, f"{rel} 比大盘新（{dt}）"
    # 文件都没变，再对题库水位：有新作答才重跑（mtime 在 WAL 下不可靠）
    wm_now = _db_watermark_now()
    if wm_now is not None:
        wm_built = None
        try:
            with open(os.path.join(SRC, "dashboard_data.json"), encoding="utf-8") as fh:
                wm_built = json.load(fh).get("db_watermark")
        except Exception:
            wm_built = None
        if wm_built is None:
            return True, "dashboard_data.json 缺 db_watermark（旧版构建）"
        if wm_now != wm_built:
            return True, f"题库有新作答数据（水位 {wm_built} → {wm_now}）"
    return False, ""


def main():
    argv = sys.argv[1:]
    skip_plan = "--no-plan" in argv
    if_stale = "--if-stale" in argv
    print("考研学习数据流水线（本地大盘）")
    print(f"代码根：{SRC}\n笔记库根：{ROOT}")

    steps = build_steps(skip_plan)
    if if_stale:
        stale, reason = check_stale(steps, skip_plan)
        if not stale:
            dt = datetime.fromtimestamp(os.path.getmtime(DASHBOARD)).strftime("%Y-%m-%d %H:%M")
            print(f"[跳过] 大盘已是最新（上次构建 {dt}；笔记/配置/题库都没再变）。")
            print("       要强制重建：run_pipeline.py（不带 --if-stale）或启动器加 rebuild 参数。")
            return 0
        print(f"[陈旧] {reason} → 跑全量流水线")
    check_progress_freshness()

    results = []
    for name, args in steps:
        results.append((name, run_step(name, args)))

    print("\n" + "=" * 60)
    print("流水线汇总")
    print("=" * 60)
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'} {name}")
    if os.path.exists(DASHBOARD):
        print(f"\n本地大盘：{DASHBOARD}")
        print("（浏览器直接打开，无需飞书）")
    n_fail = sum(1 for _, ok in results if not ok)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())

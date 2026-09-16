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

SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)
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


def main():
    skip_plan = "--no-plan" in sys.argv[1:]
    print("考研学习数据流水线（本地大盘）")
    print(f"根目录：{ROOT}")
    check_progress_freshness()

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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""review_scheduler.py —— 间隔复习调度器

扫描所有笔记中的 <!-- review: PX, YYYY-MM-DD --> 标记，
根据复习间隔规则计算下次复习日期，输出"今日待复习"清单。

用法：
  python tools/review_scheduler.py              # 输出今日待复习
  python tools/review_scheduler.py --days 3     # 输出未来 3 天内到期的复习
  python tools/review_scheduler.py --all        # 输出所有复习标记（含未到期）

复习间隔规则：
  P1（高频+曾错）：3天 → 7天 → 14天 → 30天 → 毕业
  P2（中频+模糊）：7天 → 14天 → 30天 → 毕业
  P3（低频+已掌握）：14天 → 30天 → 毕业
"""

import argparse
import io
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

# Force UTF-8 on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 笔记根目录：单一事实源 paths.NOTES_ROOT（env NOTES_ROOT > src/paths.json > 默认）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths as _paths
ROOT = Path(_paths.NOTES_ROOT).resolve()

# 复习间隔（天数）
INTERVALS = {
    "P1": [3, 7, 14, 30],
    "P2": [7, 14, 30],
    "P3": [14, 30],
}

# 匹配 <!-- review: P1, 2026-07-15 --> 格式
REVIEW_RE = re.compile(
    r"<!--\s*review:\s*(P[123])\s*,\s*(\d{4}-\d{2}-\d{2})\s*-->"
)

# 匹配标题（用于定位知识点）
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

EXCLUDE_DIRS = {"tools", "PDF", ".git", "assets", "真题", "0讲义资料"}


def parse_date(s: str) -> date:
    """解析 YYYY-MM-DD 格式日期。"""
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def compute_next_review(priority: str, last_date: date, review_count: int) -> date | None:
    """根据优先级和已复习次数计算下次复习日期。返回 None 表示已毕业。"""
    intervals = INTERVALS.get(priority, [14, 30])
    if review_count >= len(intervals):
        return None  # 已完成所有复习轮次，毕业
    return last_date + timedelta(days=intervals[review_count])


def scan_file(path: Path):
    """扫描单个文件，返回 [(priority, last_date, review_count, heading, rel_path)]。"""
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    rel = path.relative_to(ROOT)

    # 收集同一知识点的所有 review 标记
    # 策略：找到 review 标记后，向上搜索最近的标题作为知识点名
    entries = []
    current_heading = "(文件顶部)"

    for i, line in enumerate(lines):
        hm = HEADING_RE.match(line)
        if hm:
            current_heading = hm.group(2).strip()
            continue

        for m in REVIEW_RE.finditer(line):
            priority = m.group(1)
            last_date = parse_date(m.group(2))
            entries.append((priority, last_date, current_heading, str(rel), i + 1))

    return entries


def iter_notes():
    """遍历所有 .md 笔记文件。"""
    for p in sorted(ROOT.rglob("*.md")):
        rel = p.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        yield p


def main():
    ap = argparse.ArgumentParser(description="间隔复习调度器")
    ap.add_argument("--days", type=int, default=0,
                    help="输出未来 N 天内到期的复习（默认 0 = 仅今日）")
    ap.add_argument("--all", action="store_true",
                    help="输出所有复习标记（含未到期）")
    args = ap.parse_args()

    today = date.today()
    deadline = today + timedelta(days=args.days)

    # 收集所有 review 标记
    all_marks = []  # (priority, last_date, heading, rel_path, line_no)
    for p in iter_notes():
        all_marks.extend(scan_file(p))

    if not all_marks:
        print("📭 未找到任何复习标记（<!-- review: PX, YYYY-MM-DD -->）。")
        print("   在章节笔记或错题归档中添加标记后，本工具即可调度复习。")
        return

    # 按知识点聚合（同文件同标题的多个标记视为同一知识点的多次复习记录）
    grouped = {}  # (rel_path, heading) -> [(priority, date, line_no)]
    for priority, dt, heading, rel, ln in all_marks:
        key = (rel, heading)
        grouped.setdefault(key, []).append((priority, dt, ln))

    # 计算每个知识点的下次复习日期
    due_items = []  # (next_date, priority, heading, rel, review_count)
    graduated = 0

    for (rel, heading), marks in grouped.items():
        # 取最高优先级和最新日期
        priority = min(m[0] for m in marks)  # P1 < P2 < P3 字符串排序正好
        latest_date = max(m[1] for m in marks)
        review_count = len(marks)  # 标记数 ≈ 已复习次数

        next_date = compute_next_review(priority, latest_date, review_count)
        if next_date is None:
            graduated += 1
            continue

        if args.all or next_date <= deadline:
            due_items.append((next_date, priority, heading, rel, review_count))

    # 排序：到期日 → 优先级
    due_items.sort(key=lambda x: (x[0], x[1]))

    # 输出
    print(f"📅 间隔复习调度 — {today.isoformat()}")
    print(f"   扫描范围：{ROOT}")
    print(f"   复习标记总数：{len(all_marks)} | 知识点：{len(grouped)} | 已毕业：{graduated}")
    print()

    if not due_items:
        if args.days == 0:
            print("✅ 今日无待复习项目。")
        else:
            print(f"✅ 未来 {args.days} 天内无待复习项目。")
        return

    # 分组输出
    overdue = [x for x in due_items if x[0] < today]
    today_items = [x for x in due_items if x[0] == today]
    upcoming = [x for x in due_items if x[0] > today]

    if overdue:
        print(f"🔴 已逾期（{len(overdue)} 项）：")
        for nd, pri, head, rel, cnt in overdue:
            days_late = (today - nd).days
            print(f"   [{pri}] {head}")
            print(f"       📄 {rel} | 逾期 {days_late} 天 | 已复习 {cnt} 次")
        print()

    if today_items:
        print(f"🟡 今日到期（{len(today_items)} 项）：")
        for nd, pri, head, rel, cnt in today_items:
            print(f"   [{pri}] {head}")
            print(f"       📄 {rel} | 已复习 {cnt} 次")
        print()

    if upcoming:
        print(f"🟢 即将到期（{len(upcoming)} 项，{args.days} 天内）：")
        for nd, pri, head, rel, cnt in upcoming:
            days_left = (nd - today).days
            print(f"   [{pri}] {head}")
            print(f"       📄 {rel} | {days_left} 天后 | 已复习 {cnt} 次")
        print()

    total_due = len(overdue) + len(today_items)
    print(f"📊 合计：逾期 {len(overdue)} + 今日 {len(today_items)} + 即将 {len(upcoming)} = {len(due_items)} 项待复习")


if __name__ == "__main__":
    main()

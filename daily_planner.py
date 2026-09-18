#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
daily_planner.py -- Adaptive daily study plan generator for 考研 2026

Generates a markdown daily plan at C:/Users/92534/Desktop/考研/schedule/daily/plan_YYYY-MM-DD.md
based on knowledge graphs, notes index, question bank, and FSRS card data.

Usage:
    python daily_planner.py                    # Generate today's plan
    python daily_planner.py --date 2026-07-15  # Generate specific date
    python daily_planner.py --hours 8          # Override available hours
    python daily_planner.py --preview          # Preview next 7 days (summary)
    python daily_planner.py --review           # Evening review mode
"""

import argparse
import io
import json
import math
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta

# 政治笔记短编号（MY-001 / 思修-001）与图谱前缀（POL-MY）的映射，三处共用一份
from note_prefix import note_entry_prefix, topic_note_chapter

# ---------------------------------------------------------------------------
# Force UTF-8 on Windows to avoid GBK encoding errors
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    # 就地改编码，**不要**再套一层 TextIOWrapper：被换掉的那层 wrapper 没人引用，
    # GC 回收时会顺手 close 掉底层 buffer，调用方（如 daily_tasks.py）的 stdout
    # 会因此失效，报 "I/O operation on closed file"。
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
EXAM_DATE = date(2026, 12, 19)
KNOWLEDGE_GRAPH_DIR = "C:/Users/92534/Desktop/考研/src/knowledge_graph"
NOTES_INDEX_PATH = "C:/Users/92534/Desktop/考研/src/笔记索引.yaml"
QUESTION_BANK_PATH = "C:/Users/92534/Desktop/考研/src/question_bank.db"
DAILY_PLAN_DIR = "C:/Users/92534/Desktop/考研/schedule/daily"
SYNC_STATE_PATH = "C:/Users/92534/Desktop/考研/src/sync_state.json"
PROGRESS_PATH = "C:/Users/92534/Desktop/考研/src/progress.json"
FLASHCARD_SYNC_PATH = "C:/Users/92534/Desktop/考研/src/flashcard_session_export.json"

# 2026-09-19 起科目映射由 subjects.json 派生（subjects_conf.py），
# 与 serve.js / generate_dashboard.py / gap_analysis.py 同口径，改学科只动配置文件。
import subjects_conf  # noqa: E402

GRAPH_FILES = {k: os.path.join(KNOWLEDGE_GRAPH_DIR, v)
               for k, v in subjects_conf.graph_files().items()}

# Display names used in output
SUBJECT_DISPLAY = subjects_conf.plan_display()

# Map notes-index subject labels -> graph subject keys
NOTES_SUBJECT_MAP = subjects_conf.notes_subject_map()

# Map graph subject keys -> progress.json subject keys
PROGRESS_SUBJECT_MAP = subjects_conf.progress_map()

# Exam weight per subject (total score)
SUBJECT_TOTAL_SCORE = subjects_conf.total_scores()

# Maximum allocation ratio per subject (prevents over-concentration)
SUBJECT_MAX_RATIO = subjects_conf.subject_max_ratio()

# ---------------------------------------------------------------------------
# Phase Definitions (Periodization)
# ---------------------------------------------------------------------------
PHASES = {
    "基础期": {"start": date(2026, 7, 11), "end": date(2026, 9, 19), "hours": (8, 10), "new_review_ratio": (80, 20)},
    "强化期": {"start": date(2026, 9, 20), "end": date(2026, 11, 19), "hours": (10, 12), "new_review_ratio": (50, 50)},
    "冲刺期": {"start": date(2026, 11, 20), "end": date(2026, 12, 12), "hours": (10, 12), "new_review_ratio": (20, 80)},
    "减量期": {"start": date(2026, 12, 13), "end": date(2026, 12, 18), "hours": (8, 3), "new_review_ratio": (10, 90)},
}

# ---------------------------------------------------------------------------
# YAML loading (with fallback)
# ---------------------------------------------------------------------------
try:
    import yaml as _yaml

    def load_yaml(path):
        with open(path, "r", encoding="utf-8") as fh:
            return _yaml.safe_load(fh)

except ImportError:
    import re

    def load_yaml(path):
        """Bare-bones YAML parser for the structure emitted by build_index.py."""
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        # Try JSON first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Minimal line-by-line parser
        result = {}
        stack = [(result, -1)]  # (dict, indent_level)
        current_list_key = None
        for raw_line in text.splitlines():
            stripped = raw_line.rstrip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(raw_line) - len(raw_line.lstrip())
            # Pop stack to find parent
            while len(stack) > 1 and indent <= stack[-1][1]:
                stack.pop()
            parent = stack[-1][0]
            line = stripped.lstrip("- ")
            if ":" in line:
                key_part, _, val_part = line.partition(":")
                key = key_part.strip().strip("'\"")
                val = val_part.strip().strip("'\"")
                if val == "" or val is None:
                    # Nested dict
                    new_dict = {}
                    if isinstance(parent, dict):
                        parent[key] = new_dict
                    stack.append((new_dict, indent))
                    current_list_key = key
                else:
                    # Scalar
                    try:
                        val = int(val)
                    except ValueError:
                        try:
                            val = float(val)
                        except ValueError:
                            pass
                    if isinstance(parent, dict):
                        parent[key] = val
            elif stripped.lstrip().startswith("- "):
                item_text = stripped.lstrip()[2:]
                if ":" in item_text:
                    # List item that is a dict
                    item_dict = {}
                    k, _, v = item_text.partition(":")
                    k = k.strip().strip("'\"")
                    v = v.strip().strip("'\"")
                    try:
                        v = int(v)
                    except ValueError:
                        try:
                            v = float(v)
                        except ValueError:
                            pass
                    item_dict[k] = v
                    # Find the list parent
                    p = stack[-1][0]
                    if isinstance(p, dict) and current_list_key and current_list_key in p:
                        lst = p[current_list_key]
                        if not isinstance(lst, list):
                            lst = []
                            p[current_list_key] = lst
                        lst.append(item_dict)
                    stack.append((item_dict, indent))
        return result


# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------

def load_knowledge_graphs():
    """Load all 4 knowledge graph JSON files. Returns {subject: graph_data}."""
    graphs = {}
    for subject, filepath in GRAPH_FILES.items():
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    graphs[subject] = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"  [警告] 无法加载知识图谱 {filepath}: {e}")
        else:
            print(f"  [提示] 知识图谱文件不存在: {filepath}")
    return graphs


def load_notes_index():
    """Load notes index YAML. Returns dict or empty dict on failure."""
    if not os.path.exists(NOTES_INDEX_PATH):
        print(f"  [提示] 笔记索引不存在: {NOTES_INDEX_PATH}")
        return {}
    try:
        return load_yaml(NOTES_INDEX_PATH)
    except Exception as e:
        print(f"  [警告] 无法解析笔记索引: {e}")
        return {}


def load_question_bank():
    """Load card and topic data from question_bank.db. Returns structured dict."""
    if not os.path.exists(QUESTION_BANK_PATH):
        print(f"  [提示] 题库不存在: {QUESTION_BANK_PATH}")
        return {}
    try:
        conn = sqlite3.connect(QUESTION_BANK_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # Get all topics with their subjects
        c.execute("SELECT id, subject, name, chapter, exam_weight, difficulty FROM topics")
        topics_by_subject = {}
        for row in c.fetchall():
            subj = row["subject"]
            if subj not in topics_by_subject:
                topics_by_subject[subj] = []
            topics_by_subject[subj].append(dict(row))

        # Get card stats per subject
        c.execute("""
            SELECT t.subject,
                   COUNT(*) as total,
                   SUM(CASE WHEN q.times_correct > 0 THEN 1 ELSE 0 END) as with_correct,
                   SUM(q.times_asked) as total_asked,
                   SUM(q.times_correct) as total_correct
            FROM cards c
            JOIN questions q ON c.question_id = q.id
            JOIN topics t ON q.topic_id = t.id
            GROUP BY t.subject
        """)
        card_stats = {}
        for row in c.fetchall():
            card_stats[row["subject"]] = {
                "total": row["total"],
                "total_asked": row["total_asked"] or 0,
                "total_correct": row["total_correct"] or 0,
            }

        # Get due cards by subject for a given date
        c.execute("""
            SELECT t.subject, c.due_date, c.state, c.id as card_id
            FROM cards c
            JOIN questions q ON c.question_id = q.id
            JOIN topics t ON q.topic_id = t.id
        """)
        all_cards = {}
        for row in c.fetchall():
            subj = row["subject"]
            if subj not in all_cards:
                all_cards[subj] = []
            all_cards[subj].append({
                "card_id": row["card_id"],
                "due_date": row["due_date"],
                "state": row["state"],
            })

        # Get review log entries (for evening review mode)
        c.execute("""
            SELECT rl.card_id, rl.rating, rl.review_date,
                   q.topic_id, t.subject
            FROM review_log rl
            JOIN questions q ON rl.question_id = q.id
            JOIN topics t ON q.topic_id = t.id
        """)
        review_logs = [dict(r) for r in c.fetchall()]

        conn.close()
        return {
            "topics_by_subject": topics_by_subject,
            "card_stats": card_stats,
            "all_cards": all_cards,
            "review_logs": review_logs,
        }
    except Exception as e:
        print(f"  [警告] 无法连接题库: {e}")
        return {}


# 「这道题有问题」标记的原因标签。
# ⚠️ 与服务端 src/card_reports.js 的 KINDS 同源，**改一边要改另一边**：
#    跨语言没法共享常量（和 due_date/due_at 的处理一样，那边注释也写着这句）。
CARD_REPORT_KINDS = {
    "multi_correct": "多个选项都对",
    "answer_wrong": "答案有误",
    "stem_wrong": "题干有误",
    "unclear": "表述不清",
    "dup": "与别的卡重复",
    "other": "其它",
}


def load_card_reports(limit=50):
    """待修的「这道题有问题」标记 —— 也就是 agent 每日任务里要处理的那批活。

    用户在闪卡练习里一键标记（题目本身错了：多个选项都对 / 答案有误 …），
    复核与修复落在每日任务里。取数入口就是这里：
      · 网页上同一份数据：GET /api/flashcards/reports
      · 命令行（改题/驳回/删卡）：node src/card_reports.js list | fix | dismiss
    单独读一次而不是并进 load_question_bank：那是「统计口径」用的，这是「待办的活」，
    两件事分开——库里还没这张表时也不该影响掌握度计算。
    """
    if not os.path.exists(QUESTION_BANK_PATH):
        return []
    try:
        conn = sqlite3.connect(QUESTION_BANK_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT r.id, r.card_id, r.kind, r.note, r.chosen, r.correct, r.created_at,"
            "       q.content, q.type, t.name AS topic_name, COALESCE(t.subject, '') AS subject"
            "  FROM card_reports r"
            "  LEFT JOIN cards c ON c.id = r.card_id"
            "  LEFT JOIN questions q ON q.id = COALESCE(r.question_id, c.question_id)"
            "  LEFT JOIN topics t ON t.id = q.topic_id"
            " WHERE r.status = 'open' ORDER BY r.id DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
    except sqlite3.Error:
        return []          # 老库还没建这张表：当成「没有待修」，不报错、不影响计划生成

    out = []
    for r in rows:
        stem = ""
        try:
            stem = str((json.loads(r["content"]) or {}).get("stem") or "")
        except Exception:
            stem = ""
        out.append({
            "id": r["id"], "card_id": r["card_id"], "kind": r["kind"],
            "kind_label": CARD_REPORT_KINDS.get(r["kind"], "其它"),
            "note": r["note"] or "", "chosen": r["chosen"] or "", "correct": r["correct"] or "",
            "subject": r["subject"] or "", "topic_name": r["topic_name"] or "",
            "type": r["type"] or "", "created_at": r["created_at"] or "",
            "stem": re.sub(r"\s+", " ", stem)[:80],
        })
    return out


def load_sync_state():
    """Load sync_state.json if it exists."""
    if os.path.exists(SYNC_STATE_PATH):
        try:
            with open(SYNC_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def load_progress():
    """Load user's actual learning progress from progress.json.
    Returns the parsed JSON dict, or None if the file does not exist.
    """
    if os.path.exists(PROGRESS_PATH):
        try:
            with open(PROGRESS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [警告] 无法加载 progress.json: {e}")
    return None


# ---------------------------------------------------------------------------
# Topic & Mastery Analysis
# ---------------------------------------------------------------------------

def get_all_topics(graphs):
    """Extract flat list of all topics from knowledge graphs.
    Returns list of dicts with subject, id, name, chapter, exam_weight, difficulty,
    prerequisites, subtopics, sub_key.
    """
    all_topics = []
    for subject, graph in graphs.items():
        subs = graph.get("subs", {})
        for sub_key, sub_data in subs.items():
            topics = sub_data.get("topics", [])
            for topic in topics:
                all_topics.append({
                    "subject": subject,
                    "sub_key": sub_key,
                    "sub_name": sub_data.get("name", sub_key),
                    "id": topic.get("id", ""),
                    "name": topic.get("name", ""),
                    "chapter": topic.get("chapter", 0),
                    "exam_weight": topic.get("exam_weight", 1),
                    "exam_points": topic.get("exam_points", ""),
                    "difficulty": topic.get("difficulty", 0.5),
                    "prerequisites": topic.get("prerequisites", []),
                    "subtopics": topic.get("subtopics", []),
                })
    return all_topics


def _get_main_topic_ids_for_subject(subject, graphs):
    """Collect the set of main-level topic IDs for a subject from the graph."""
    graph = graphs.get(subject, {})
    ids = set()
    for sub_key, sub_data in graph.get("subs", {}).items():
        for topic in sub_data.get("topics", []):
            tid = topic.get("id", "")
            if tid:
                ids.add(tid)
    return ids


def _get_covered_main_topic_ids(subject, notes_index, graphs):
    """Determine which main topic IDs have at least one note entry.

    口径与 gap_analysis.py 统一：笔记按「图谱前缀 + 章节号」落到考点上。

    原先的写法是拿笔记 ID 去 startswith 考点 ID，只在不巧的巧合下成立——
    '408-DS-001' 确实以 '408-DS-01' 开头，所以 408/数学/英语看着像是对的；
    但政治是 MY-001 / POL-MY-01 两套编号，永远匹配不上，覆盖率恒为 0，
    「政治笔记没挂上图谱」的直接原因（2026-09-17 修）。
    """
    graph = graphs.get(subject, {})

    reverse_map = {v: k for k, v in NOTES_SUBJECT_MAP.items()}
    notes_subj_label = reverse_map.get(subject, subject)

    entries = notes_index.get("entries", [])
    prefix_chapters = {}   # "POL-MY" -> {1, 2, 3}
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_subj = entry.get("subject", "")
            if entry_subj != notes_subj_label and entry_subj != subject:
                continue
            pfx = note_entry_prefix(entry.get("id", ""))
            if not pfx:
                # 跨科目方法论专题（ZT-*）：不归属任何图谱考点
                continue
            m = re.search(r"\d+", str(entry.get("chapter", "")))
            if m:
                prefix_chapters.setdefault(pfx, set()).add(int(m.group()))

    covered = set()
    for sub_key, sub_data in graph.get("subs", {}).items():
        for topic in sub_data.get("topics", []):
            tid = topic.get("id", "")
            if not tid:
                continue
            pfx = "-".join(tid.split("-")[:2])
            if topic_note_chapter(topic) in prefix_chapters.get(pfx, set()):
                covered.add(tid)
    return covered


def count_topics_for_subject(subject, graphs):
    """Count total main-level topics for a subject (not subtopics)."""
    return len(_get_main_topic_ids_for_subject(subject, graphs))


def count_covered_topics_for_subject(subject, notes_index, graphs):
    """Count how many main-level topics have at least one note."""
    return len(_get_covered_main_topic_ids(subject, notes_index, graphs))


def estimate_mastery(subject, graphs, notes_index, qb_data, progress_data=None):
    """Estimate 0-1 mastery for a subject.

    When progress.json provides data for the subject:
      - progress_mastery = overall_progress from progress.json (一轮学习进度)
      - note_mastery     = note coverage + card accuracy (笔记整理深度)
      - final = 0.7 * progress_mastery + 0.3 * note_mastery

    When progress.json is unavailable:
      - Falls back to original formula: 0.4 * note_coverage + 0.6 * card_accuracy
    """
    # --- Note-based mastery (original logic) ---
    topic_count = count_topics_for_subject(subject, graphs)
    covered = count_covered_topics_for_subject(subject, notes_index, graphs)
    note_coverage = covered / max(topic_count, 1)

    card_stats = qb_data.get("card_stats", {})
    stats = card_stats.get(subject, {})
    total_asked = stats.get("total_asked", 0)
    total_correct = stats.get("total_correct", 0)
    card_accuracy = total_correct / max(total_asked, 1) if total_asked > 0 else 0

    note_mastery = 0.4 * note_coverage + 0.6 * card_accuracy

    # --- Progress-based mastery from progress.json ---
    if progress_data and "subjects" in progress_data:
        prog_key = PROGRESS_SUBJECT_MAP.get(subject, subject)
        prog_subject = progress_data["subjects"].get(prog_key, {})
        prog_overall = prog_subject.get("overall_progress", None)

        if prog_overall is not None:
            # Blend: 70% progress.json (一轮进度), 30% note depth
            mastery = 0.7 * prog_overall + 0.3 * note_mastery
            return min(mastery, 1.0)

    # Fallback: no progress.json data
    return min(note_mastery, 1.0)


def get_coverage_percent(subject, graphs, notes_index):
    """Get notes coverage percentage for display."""
    topic_count = count_topics_for_subject(subject, graphs)
    covered = count_covered_topics_for_subject(subject, notes_index, graphs)
    if topic_count == 0:
        return 0.0
    return min(covered / topic_count * 100, 100.0)


# ---------------------------------------------------------------------------
# Phase & Taper
# ---------------------------------------------------------------------------

def get_current_phase(target_date):
    """Return (phase_name, phase_data) for the given date."""
    for name, phase in PHASES.items():
        if phase["start"] <= target_date <= phase["end"]:
            return name, phase
    # Before start
    if target_date < PHASES["基础期"]["start"]:
        return "预备期", PHASES["基础期"]
    # After end but before exam
    if target_date < EXAM_DATE:
        return "减量期", PHASES["减量期"]
    return "考试日", PHASES["减量期"]


def taper_hours(days_to_exam):
    """Exponential decay model for the final week."""
    if days_to_exam <= 1:
        return 2.0
    elif days_to_exam <= 7:
        return 3 + 7 * math.exp(-(7 - days_to_exam) / 3)
    else:
        return 10.0


def get_available_hours(target_date, phase_name, phase_data, override_hours=None):
    """Determine available study hours for a date."""
    if override_hours is not None:
        return float(override_hours)

    days_to_exam = (EXAM_DATE - target_date).days
    if days_to_exam <= 7:
        return round(taper_hours(days_to_exam), 1)

    low, high = phase_data["hours"]
    # Linear interpolation within phase for the hours range
    phase_start = phase_data["start"]
    phase_end = phase_data["end"]
    total_days = max((phase_end - phase_start).days, 1)
    elapsed = (target_date - phase_start).days
    progress = min(elapsed / total_days, 1.0)

    if low <= high:
        hours = low + (high - low) * progress
    else:
        # Decreasing (e.g., taper period 8->3)
        hours = low - (low - high) * progress
    return round(hours, 1)


# ---------------------------------------------------------------------------
# Priority Algorithm
# ---------------------------------------------------------------------------

def compute_priority(topic, days_to_exam, mastery, weight, is_srs_due, days_since_review):
    """Compute priority score (0-100) for a topic."""
    urgency = 100 * (1 - days_to_exam / 180) ** 1.5 if days_to_exam <= 180 else 0
    remaining_value = weight * (1 - mastery)
    importance = min(remaining_value / 10 * 100, 100)
    recency = min(days_since_review / 14, 1.0) * 20
    srs_boost = 25 if is_srs_due else 0
    difficulty_bonus = topic.get("difficulty", 0.5) * 10
    score = urgency * 0.25 + importance * 0.35 + recency + srs_boost + difficulty_bonus
    return min(score, 100)


# ---------------------------------------------------------------------------
# Time Allocation
# ---------------------------------------------------------------------------

def allocate_hours(subjects_data, total_hours=10):
    """Allocate hours across subjects based on gap and weight.
    subjects_data: list of dicts with name, target_mastery, current_mastery, exam_weight
    Returns dict {subject_name: hours}
    """
    raw = {}
    for subj in subjects_data:
        gap = subj["target_mastery"] - subj["current_mastery"]
        raw[subj["name"]] = subj["exam_weight"] * max(gap, 0.1)
    total_raw = sum(raw.values())
    if total_raw == 0:
        # Equal distribution
        equal = total_hours / len(subjects_data) if subjects_data else 0
        return {s["name"]: round(equal, 1) for s in subjects_data}

    hours = {}
    for name, r in raw.items():
        h = total_hours * r / total_raw
        hours[name] = max(1.0, min(h, total_hours * SUBJECT_MAX_RATIO.get(name, 0.4)))
    actual_total = sum(hours.values())
    if actual_total == 0:
        return {s["name"]: round(total_hours / len(subjects_data), 1) for s in subjects_data}
    return {k: round(v / actual_total * total_hours, 1) for k, v in hours.items()}


# ---------------------------------------------------------------------------
# Card Analysis
# ---------------------------------------------------------------------------

def get_due_cards(qb_data, target_date):
    """Get due cards by subject for a given date.
    Returns {subject: {"due": count, "new": count, "review": count}}
    """
    date_str = target_date.isoformat()
    result = {}
    all_cards = qb_data.get("all_cards", {})
    for subject, cards in all_cards.items():
        due_count = 0
        new_count = 0
        review_count = 0
        for card in cards:
            if card["due_date"] <= date_str:
                due_count += 1
                if card["state"] == 0:
                    new_count += 1
                else:
                    review_count += 1
        result[subject] = {"due": due_count, "new": new_count, "review": review_count}
    return result


# ---------------------------------------------------------------------------
# Topic Selection for Today
# ---------------------------------------------------------------------------

def select_topics_for_subject(subject, graphs, notes_index, qb_data,
                               days_to_exam, hours_allocated, phase_name,
                               target_date=None, progress_data=None):
    """Select priority topics for a subject today.
    Returns list of (topic_dict, action_str, reason_str)
    """
    if target_date is None:
        target_date = date.today()
    graph = graphs.get(subject, {})
    all_topics = []
    for sub_key, sub_data in graph.get("subs", {}).items():
        for topic in sub_data.get("topics", []):
            topic["_sub_key"] = sub_key
            topic["_sub_name"] = sub_data.get("name", sub_key)
            all_topics.append(topic)

    # Get covered topic IDs from notes index using prefix-based matching
    covered_ids = _get_covered_main_topic_ids(subject, notes_index, graphs)

    # Get due card topic IDs
    date_str = target_date.isoformat()
    due_topic_ids = set()
    all_cards = qb_data.get("all_cards", {})
    subj_cards = all_cards.get(subject, [])
    # Map question_id prefix to topic_id
    for card in subj_cards:
        if card["due_date"] <= date_str:
            # card_id like C-408-CN-01-0001, question_id like Q-408-CN-01-0001
            # topic_id like 408-CN-01
            cid = card.get("card_id", "")
            # Extract topic part: remove C- prefix and last -NNNN suffix
            parts = cid.split("-")
            if len(parts) >= 4:
                # Reconstruct topic_id: e.g. 408-CN-01
                topic_parts = parts[1:-1]  # skip C and the last number
                if len(topic_parts) >= 3:
                    tid = "-".join(topic_parts[:3])
                    due_topic_ids.add(tid)

    # Score each topic
    scored = []
    mastery = estimate_mastery(subject, graphs, notes_index, qb_data, progress_data)
    for topic in all_topics:
        tid = topic.get("id", "")
        is_covered = tid in covered_ids
        is_srs_due = tid in due_topic_ids
        # days_since_review: estimate from coverage
        days_since_review = 0 if is_covered else 30

        priority = compute_priority(
            topic, days_to_exam, mastery,
            topic.get("exam_weight", 1), is_srs_due, days_since_review
        )

        if is_covered:
            action = "复习"
            reason_parts = []
            if is_srs_due:
                reason_parts.append("闪卡到期")
            reason_parts.append(f"已学待巩固")
        else:
            action = "新学"
            reason_parts = []
            weight = topic.get("exam_weight", 1)
            pts = topic.get("exam_points", "")
            reason_parts.append(f"权重 {weight} 分" if weight else "基础章节")
            if pts:
                reason_parts.append(f"预估{pts}")

            # Check prerequisites
            prereqs = topic.get("prerequisites", [])
            prereqs_met = all(p in covered_ids for p in prereqs) if prereqs else True
            if prereqs_met:
                reason_parts.append("前置已满足")
            else:
                reason_parts.append("需先补前置")
                priority -= 10

        reason = "，".join(reason_parts)
        scored.append((topic, priority, action, reason))

    # Sort by priority descending
    scored.sort(key=lambda x: x[1], reverse=True)

    # Select top topics based on hours (roughly 1 topic per 1.5 hours)
    n_topics = max(1, min(int(hours_allocated / 1.2) + 1, 5))
    selected = []
    for topic, priority, action, reason in scored[:n_topics]:
        sub_key = topic.get("_sub_key", "")
        selected.append((topic, action, reason, sub_key))
    return selected


# ---------------------------------------------------------------------------
# Gap Analysis Summary
# ---------------------------------------------------------------------------

def get_gap_summary(subject, graphs, notes_index):
    """Get the highest-priority gap for a subject. Returns (coverage_str, gap_str)."""
    coverage = get_coverage_percent(subject, graphs, notes_index)
    coverage_str = f"{coverage:.1f}%"

    # Find highest-weight uncovered topic using prefix-based coverage
    covered_main_ids = _get_covered_main_topic_ids(subject, notes_index, graphs)

    graph = graphs.get(subject, {})
    best_gap = None
    best_weight = 0

    for sub_key, sub_data in graph.get("subs", {}).items():
        for topic in sub_data.get("topics", []):
            tid = topic.get("id", "")
            if tid not in covered_main_ids:
                w = topic.get("exam_weight", 1)
                if w > best_weight:
                    best_weight = w
                    pts = topic.get("exam_points", "")
                    best_gap = f"{topic.get('name', tid)}（{pts}）"

    if best_gap:
        gap_str = best_gap
    elif coverage < 100:
        gap_str = "部分章节待深化"
    else:
        gap_str = "全面覆盖，以复习为主"

    return coverage_str, gap_str


# ---------------------------------------------------------------------------
# Daily Goals Generation
# ---------------------------------------------------------------------------

def generate_goals(subject_selections, due_cards, phase_name, card_reports=None):
    """Generate checklist items for today's goals."""
    goals = []
    for subject, selections in subject_selections.items():
        display = SUBJECT_DISPLAY.get(subject, subject)
        for topic, action, reason, sub_key in selections:
            name = topic.get("name", "")
            if action == "新学":
                goals.append(f"完成 {sub_key} {name} 知识图谱学习")
            else:
                goals.append(f"复习 {sub_key} {name} 重点内容")

    # Flashcard goal
    total_due = sum(v.get("due", 0) for v in due_cards.values())
    if total_due > 0:
        goals.append("闪卡复习全部完成")

    # 待修的问题卡：这是**agent 的活**，写进今日目标它才会流转到每日任务/飞书任务里
    # （daily_tasks.py 的 planner_goals 就是抠这份 `- [ ]` 清单）。
    reports = card_reports or []
    if reports:
        goals.append(f"修复 {len(reports)} 张被标记有问题的闪卡（node src/card_reports.js list）")

    return goals


# ---------------------------------------------------------------------------
# Markdown Generation
# ---------------------------------------------------------------------------

def generate_plan_markdown(target_date, graphs, notes_index, qb_data,
                            override_hours=None, progress_data=None):
    """Generate the full daily plan markdown string."""
    days_to_exam = (EXAM_DATE - target_date).days
    day_number = (target_date - PHASES["基础期"]["start"]).days + 1
    total_days = (EXAM_DATE - PHASES["基础期"]["start"]).days + 1

    phase_name, phase_data = get_current_phase(target_date)
    available_hours = get_available_hours(
        target_date, phase_name, phase_data, override_hours
    )

    # Mastery per subject
    subjects_data = []
    subject_masteries = {}
    for subject in GRAPH_FILES:
        mastery = estimate_mastery(subject, graphs, notes_index, qb_data, progress_data)
        subject_masteries[subject] = mastery
        subjects_data.append({
            "name": subject,
            "target_mastery": 1.0,
            "current_mastery": mastery,
            "exam_weight": SUBJECT_TOTAL_SCORE.get(subject, 100),
        })

    # Allocate hours
    hour_allocation = allocate_hours(subjects_data, available_hours)

    # Due cards
    due_cards = get_due_cards(qb_data, target_date)

    # Select topics per subject
    subject_selections = {}
    for subject in GRAPH_FILES:
        hours_for_subj = hour_allocation.get(subject, 1.0)
        selections = select_topics_for_subject(
            subject, graphs, notes_index, qb_data,
            days_to_exam, hours_for_subj, phase_name,
            target_date=target_date, progress_data=progress_data
        )
        subject_selections[subject] = selections

    # New/review ratio from phase
    new_ratio, review_ratio = phase_data.get("new_review_ratio", (50, 50))

    # --- Build markdown ---
    lines = []
    date_str = target_date.strftime("%Y-%m-%d")
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_names[target_date.weekday()]

    lines.append(f"# {date_str} {weekday} 学习计划（Day {day_number}/{total_days}）")
    lines.append("")
    lines.append("## 倒计时")
    lines.append(f"- 距考试 **{days_to_exam} 天**，当前阶段：{phase_name}")
    lines.append(f"- 今日可用时间：{available_hours:.0f} 小时")
    if phase_name == "减量期" or days_to_exam <= 7:
        lines.append(f"- ⚠️ 考前减量期，以回顾巩固为主")
    lines.append(f"- 新学/复习比例：{new_ratio}% / {review_ratio}%")
    lines.append("")

    # Time allocation table
    lines.append("## 时间分配")
    lines.append("| 科目 | 时长 | 重点章节 | 依据 |")
    lines.append("|------|------|---------|------|")

    for subject in GRAPH_FILES:
        display = SUBJECT_DISPLAY.get(subject, subject)
        hours = hour_allocation.get(subject, 0)
        selections = subject_selections.get(subject, [])
        if selections:
            topic, action, reason, sub_key = selections[0]
            chapter_info = f"{sub_key}-{topic.get('chapter', ''):02d} {topic.get('name', '')}（{action}）"
            # 政治月度资料建议
            if subject == "政治":
                month = target_date.month
                if month in (7, 8):
                    reason = reason + "（建议衔接肖秀荣1000题）"
                elif month in (9, 10):
                    reason = reason + "（建议跟腿姐技巧班+1000题二刷）"
                elif month == 11:
                    reason = reason + "（建议做肖八选择题）"
                elif month == 12:
                    reason = reason + "（建议背诵肖四大题）"
            lines.append(f"| {display} | {hours:.1f}h | {chapter_info} | {reason} |")
        else:
            reason_text = "按缺口自行安排"
            # 政治月度资料建议（无选题时）
            if subject == "政治":
                month = target_date.month
                if month in (7, 8):
                    reason_text = reason_text + "（建议衔接肖秀荣1000题）"
                elif month in (9, 10):
                    reason_text = reason_text + "（建议跟腿姐技巧班+1000题二刷）"
                elif month == 11:
                    reason_text = reason_text + "（建议做肖八选择题）"
                elif month == 12:
                    reason_text = reason_text + "（建议背诵肖四大题）"
            lines.append(f"| {display} | {hours:.1f}h | 自由复习 | {reason_text} |")

    lines.append("")

    # Flashcard section
    lines.append("## 闪卡复习")
    total_due = 0
    total_new = 0
    card_parts_due = []
    card_parts_new = []
    for subject in GRAPH_FILES:
        display = SUBJECT_DISPLAY.get(subject, subject)
        info = due_cards.get(subject, {"due": 0, "new": 0, "review": 0})
        if info["due"] > 0:
            card_parts_due.append(f"{display} x {info['due']}")
            total_due += info["due"]
        if info["new"] > 0:
            # Apply new card quota based on phase ratio
            new_quota = max(1, int(info["new"] * new_ratio / 100))
            card_parts_new.append(f"{display} x {new_quota}")
            total_new += new_quota

    if card_parts_due:
        lines.append(f"- 到期卡片：{' + '.join(card_parts_due)} = **{total_due} 张**")
    else:
        lines.append("- 到期卡片：暂无到期卡片")
    if card_parts_new:
        lines.append(f"- 新卡配额：{' + '.join(card_parts_new)} = **{total_new} 张**")
    else:
        lines.append("- 新卡配额：暂无新卡")

    est_min = int((total_due + total_new) * 1.2)
    est_max = int((total_due + total_new) * 2.0)
    if total_due + total_new > 0:
        lines.append(f"- 预计耗时：{est_min}-{est_max} 分钟")

    # 待修的问题卡（用户在练习里标记的「题目本身错了」）：这是 **agent 的活**，
    # 不是他的——所以既要在这里写清有几张（免得他以为标记丢了），
    # 也要进下面的今日目标，才会流转到每日任务与飞书任务清单里。
    card_reports = load_card_reports()
    if card_reports:
        by_kind = {}
        for r in card_reports:
            by_kind[r["kind_label"]] = by_kind.get(r["kind_label"], 0) + 1
        lines.append(f"- ⚠️ 待修的问题卡：**{len(card_reports)} 张**（"
                     + " + ".join(f"{k} x {v}" for k, v in by_kind.items()) + "）")
        lines.append("- 复核入口：`node src/card_reports.js list`（改题 / 驳回 / 删卡都在那儿；"
                     "网页上筛「⚑ 待修」也能直接复看）")
    lines.append("")

    # Goals
    lines.append("## 今日目标")
    goals = generate_goals(subject_selections, due_cards, phase_name, card_reports)
    if goals:
        for goal in goals:
            lines.append(f"- [ ] {goal}")
    else:
        lines.append("- [ ] 按计划自由复习")
    lines.append("")

    # 专项任务（7-8月词汇突击，数据驱动：vocab_gap>0 才安排新学）
    has_special_task = False
    if date(2026, 7, 11) <= target_date <= date(2026, 8, 31):
        english_hours = hour_allocation.get("英语一", 0)
        if english_hours >= 1.5:
            eng_prog = (progress_data or {}).get("subjects", {}).get("英语", {})
            vocab_gap = eng_prog.get("vocab_gap", 0)
            vplan = eng_prog.get("vocab_daily_plan", {})
            new_n = vplan.get("new_per_day", 50)
            rev_n = vplan.get("review_per_day", 100)
            lines.append("## 专项任务")
            if vocab_gap > 0 and new_n > 0:
                lines.append(f"- [ ] 词汇突击：今日新学{new_n}词 + 复习{rev_n}词（目标8月底补齐{vocab_gap}词缺口）")
            else:
                lines.append(f"- [ ] 词汇巩固：单词一轮已完成，今日复习{rev_n}词（不再新学）")
            lines.append("")
            has_special_task = True

    # 真题训练提醒
    if phase_name == "强化期":
        lines.append("## 真题训练提醒")
        lines.append("- 强化期建议每周至少2天做真题套卷（限时完成）")
        lines.append("- 数学真题限时3小时，408真题限时150分钟")
        lines.append("")
    elif phase_name == "冲刺期":
        lines.append("## 真题训练提醒")
        lines.append("- 冲刺期以真题为核心，建议每日至少做1科真题（套卷或分类）")
        lines.append("- 重做错题+经典题，不再刷新题")
        lines.append("")

    # Gap analysis summary
    lines.append("## 差距分析摘要")
    lines.append("| 科目 | 覆盖率 | 最高优先缺口 |")
    lines.append("|------|--------|-------------|")
    for subject in GRAPH_FILES:
        display = SUBJECT_DISPLAY.get(subject, subject)
        cov, gap = get_gap_summary(subject, graphs, notes_index)
        lines.append(f"| {display} | {cov} | {gap} |")
    lines.append("")

    # Mastery overview
    lines.append("## 掌握度概览")
    for subject in GRAPH_FILES:
        display = SUBJECT_DISPLAY.get(subject, subject)
        mastery = subject_masteries.get(subject, 0)
        bar_len = int(mastery * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        lines.append(f"- {display}：{bar} {mastery*100:.1f}%")

        # Show progress.json detail if available
        if progress_data and "subjects" in progress_data:
            prog_key = PROGRESS_SUBJECT_MAP.get(subject, subject)
            prog_subject = progress_data["subjects"].get(prog_key, {})
            if prog_subject:
                phase = prog_subject.get("phase", "")
                resource = prog_subject.get("resource", "")
                overall = prog_subject.get("overall_progress", None)
                detail_parts = []
                if phase:
                    detail_parts.append(f"阶段: {phase}")
                if resource:
                    detail_parts.append(f"资料: {resource}")
                if overall is not None:
                    detail_parts.append(f"一轮进度: {overall*100:.0f}%")
                if detail_parts:
                    lines.append(f"  - {'  |  '.join(detail_parts)}")

                # Show sub-progress for subjects with subs
                subs = prog_subject.get("subs", {})
                if subs:
                    sub_parts = []
                    for sk, sv in subs.items():
                        if isinstance(sv, dict):
                            p = sv.get("progress", 0)
                            st = sv.get("status", "")
                            sub_parts.append(f"{sk} {p*100:.0f}%({st})")
                    if sub_parts:
                        lines.append(f"  - 分项: {' | '.join(sub_parts)}")

                # Show course/exercise progress for politics
                course = prog_subject.get("course", {})
                exercises = prog_subject.get("exercises", {})
                if course:
                    c_parts = [f"{k} {'✅' if v >= 1.0 else f'{v*100:.0f}%'}" for k, v in course.items()]
                    lines.append(f"  - 课程: {' '.join(c_parts)}")
                if exercises:
                    e_parts = [f"{k} {'✅' if v >= 1.0 else f'{v*100:.0f}%'}" for k, v in exercises.items() if v > 0]
                    not_started = [k for k, v in exercises.items() if v == 0]
                    if e_parts:
                        lines.append(f"  - 刷题: {' '.join(e_parts)}")
                    if not_started:
                        lines.append(f"  - 刷题未开始: {' '.join(not_started)}")

                # Show English-specific fields
                vocab = prog_subject.get("vocabulary_size", None)
                phrases = prog_subject.get("phrases_learned", None)
                word_prog = prog_subject.get("word_progress", None)
                if vocab or phrases or word_prog:
                    eng_parts = []
                    if vocab:
                        eng_parts.append(f"词汇量{vocab}")
                    if phrases:
                        eng_parts.append(f"短语{phrases}+")
                    if word_prog:
                        eng_parts.append(f"单词: {word_prog}")
                    lines.append(f"  - {' | '.join(eng_parts)}")
    lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*由 daily_planner.py 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Preview Mode
# ---------------------------------------------------------------------------

def generate_preview(graphs, notes_index, qb_data, start_date, days=7):
    """Generate a 7-day preview summary."""
    lines = []
    lines.append(f"# 未来 {days} 天学习预览")
    lines.append(f"起始日期：{start_date.strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append("| 日期 | 阶段 | 倒计时 | 可用时间 | 到期闪卡 |")
    lines.append("|------|------|--------|---------|---------|")

    for i in range(days):
        d = start_date + timedelta(days=i)
        days_to_exam = (EXAM_DATE - d).days
        phase_name, phase_data = get_current_phase(d)
        hours = get_available_hours(d, phase_name, phase_data)
        due = get_due_cards(qb_data, d)
        total_due = sum(v.get("due", 0) for v in due.values())
        weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
        wd = weekday_names[d.weekday()]
        lines.append(
            f"| {d.strftime('%m-%d')} 周{wd} | {phase_name} | "
            f"{days_to_exam}天 | {hours:.0f}h | {total_due}张 |"
        )
    lines.append("")
    return "\n".join(lines)


def load_flashcard_sync():
    """Load flashcard review data synced from the browser app.
    Returns list of review dicts compatible with evening_review, or empty list.
    """
    if not os.path.exists(FLASHCARD_SYNC_PATH):
        return []
    try:
        with open(FLASHCARD_SYNC_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        reviews = data.get("reviews", [])
        # Map topic_id prefix to subject display name
        result = []
        for r in reviews:
            topic_id = r.get("topic_id", "")
            # Extract subject from topic_id (e.g. "408-DS-04" -> "408", "MATH-01" -> "数学", etc.)
            subject = "未知"
            if topic_id.startswith("408"):
                subject = "408"
            elif topic_id.startswith("MATH"):
                subject = "数学一"
            elif topic_id.startswith("POL"):
                subject = "政治"
            elif topic_id.startswith("ENG"):
                subject = "英语一"
            result.append({
                "question_id": r.get("question_id", ""),
                "rating": r.get("rating", 0),
                "answer_correct": r.get("answer_correct", False),
                "review_date": r.get("timestamp", ""),
                "subject": subject,
                "topic_id": topic_id,
            })
        return result
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [警告] 无法加载闪卡同步数据: {e}")
        return []


# ---------------------------------------------------------------------------
# Evening Review Mode
# ---------------------------------------------------------------------------

def _daily_task_completion(date_str):
    """从 daily_tasks 表读当日完成情况 → (done, total, pct)；无表或无任务时 None。

    与上面那份 markdown 计数**口径不同，刻意并存**：
      * markdown 计数 = 用户有没有手工编辑过计划文件（生成端只写 `- [ ]`，所以恒 0%）
      * 这里        = 用户在大盘「活动」页勾选的真实完成状态
    合并成一个数字会让两边都失真，所以分开列、各自标明来源。
    """
    try:
        conn = sqlite3.connect(str(QUESTION_BANK_PATH))
        row = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(done), 0) AS done "
            "FROM daily_tasks WHERE task_date = ? AND deleted = 0", (date_str,)
        ).fetchone()
        conn.close()
    except Exception:                                          # noqa: BLE001
        return None                       # 迁移还没跑过时静默降级
    total, done = (row or (0, 0))
    if not total:
        return None
    return (int(done), int(total), done / total * 100)


def evening_review(target_date, graphs, notes_index, qb_data):
    """Generate evening review summary for the day."""
    date_str = target_date.strftime("%Y-%m-%d")
    lines = []
    lines.append(f"# {date_str} 晚间回顾")
    lines.append("")

    # Read today's plan
    plan_path = os.path.join(DAILY_PLAN_DIR, f"plan_{date_str}.md")
    if os.path.exists(plan_path):
        lines.append("## 今日计划回顾")
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_content = f.read()
        # Count checkboxes
        total_checks = plan_content.count("- [ ]") + plan_content.count("- [x]")
        done_checks = plan_content.count("- [x]")
        lines.append(f"- 计划目标数：{total_checks}")
        lines.append(f"- 已完成：{done_checks}")
        if total_checks > 0:
            lines.append(f"- 完成率：{done_checks/total_checks*100:.0f}%")
        lines.append("")
        # ⚠️ 上面这个完成率**恒为 0%**：生成端只写 `- [ ]`，从不写 `- [x]`，
        #    所以它反映的其实是"用户有没有手工编辑过计划文件"。
    else:
        lines.append("*今日未生成学习计划*")
        lines.append("")

    # 大盘任务的完成度**无论有没有 markdown 计划文件都要报**——它才是真实来源
    # （用户在「活动」页勾选，落在 daily_tasks 表）。两个数字并存、各自标明来源，
    # 合并成一个会让两边都失真。
    db_rate = _daily_task_completion(date_str)
    if db_rate is not None:
        lines.append(f"- 大盘任务完成度：{db_rate[0]}/{db_rate[1]}"
                     f"（{db_rate[2]:.0f}%，来自「活动」页勾选）")
        lines.append("")

    # Check review_log for today (SQLite), fallback to flashcard sync JSON
    review_logs = qb_data.get("review_logs", [])
    today_logs = [r for r in review_logs if r.get("review_date", "").startswith(date_str)]

    # Fallback: read from flashcard_session_export.json if SQLite has no logs
    if not today_logs:
        sync_logs = load_flashcard_sync()
        today_logs = [r for r in sync_logs if r.get("review_date", "").startswith(date_str)]

    lines.append("## 闪卡复习统计")
    if today_logs:
        by_subject = {}
        for log in today_logs:
            subj = log.get("subject", "未知")
            if subj not in by_subject:
                by_subject[subj] = {"count": 0, "correct": 0, "ratings": []}
            by_subject[subj]["count"] += 1
            by_subject[subj]["ratings"].append(log.get("rating", 0))
            # Handle both SQLite format (rating >= 3) and sync JSON (answer_correct bool)
            is_correct = log.get("answer_correct", None)
            if is_correct is None:
                is_correct = log.get("rating", 0) >= 3
            if is_correct:
                by_subject[subj]["correct"] += 1

        lines.append("| 科目 | 复习张数 | 正确率 |")
        lines.append("|------|---------|--------|")
        for subj, data in by_subject.items():
            display = SUBJECT_DISPLAY.get(subj, subj)
            rate = data["correct"] / max(data["count"], 1) * 100
            lines.append(f"| {display} | {data['count']} | {rate:.0f}% |")
    else:
        lines.append("- 今日无闪卡复习记录")
    lines.append("")

    # Tomorrow's preview
    tomorrow = target_date + timedelta(days=1)
    days_to_exam = (EXAM_DATE - tomorrow).days
    phase_name, phase_data = get_current_phase(tomorrow)
    tomorrow_hours = get_available_hours(tomorrow, phase_name, phase_data)
    lines.append("## 明日预览")
    lines.append(f"- 日期：{tomorrow.strftime('%Y-%m-%d')}")
    lines.append(f"- 阶段：{phase_name}")
    lines.append(f"- 倒计时：{days_to_exam} 天")
    lines.append(f"- 预计可用时间：{tomorrow_hours:.0f} 小时")

    due_tomorrow = get_due_cards(qb_data, tomorrow)
    total_due_tmr = sum(v.get("due", 0) for v in due_tomorrow.values())
    lines.append(f"- 预计到期闪卡：{total_due_tmr} 张")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="考研自适应每日学习计划生成器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python daily_planner.py                    生成今天的计划
  python daily_planner.py --date 2026-07-15  生成指定日期的计划
  python daily_planner.py --hours 8          覆盖今日可用时间
  python daily_planner.py --preview          预览未来7天
  python daily_planner.py --review           晚间回顾模式
        """,
    )
    parser.add_argument("--date", type=str, default=None,
                        help="指定日期 (YYYY-MM-DD)，默认为今天")
    parser.add_argument("--hours", type=float, default=None,
                        help="覆盖可用学习时间（小时）")
    parser.add_argument("--preview", action="store_true",
                        help="预览未来7天（仅摘要）")
    parser.add_argument("--review", action="store_true",
                        help="晚间回顾模式")

    args = parser.parse_args()

    # Determine target date
    if args.date:
        date_str = args.date.strip().lower()
        if date_str == "tomorrow":
            target_date = date.today() + timedelta(days=1)
        elif date_str == "yesterday":
            target_date = date.today() - timedelta(days=1)
        else:
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                print(f"错误：日期格式不正确 '{args.date}'，请使用 YYYY-MM-DD 格式，或 tomorrow/yesterday。")
                sys.exit(1)
    else:
        target_date = date.today()

    # Ensure output directory exists
    os.makedirs(DAILY_PLAN_DIR, exist_ok=True)

    # Load data
    print(f"正在加载数据...")
    graphs = load_knowledge_graphs()
    print(f"  知识图谱：已加载 {len(graphs)} 个科目")

    notes_index = load_notes_index()
    entries_count = len(notes_index.get("entries", []))
    print(f"  笔记索引：{entries_count} 条记录")

    qb_data = load_question_bank()
    topics_count = sum(len(v) for v in qb_data.get("topics_by_subject", {}).values())
    cards_count = sum(len(v) for v in qb_data.get("all_cards", {}).values())
    print(f"  题库数据：{topics_count} 个知识点，{cards_count} 张闪卡")

    sync_state = load_sync_state()
    if sync_state:
        print(f"  同步状态：上次同步 {sync_state.get('last_sync', '未知')}")

    progress_data = load_progress()
    if progress_data:
        updated = progress_data.get("updated", "未知")
        print(f"  学习进度：已加载 progress.json (更新于 {updated})")
        for subj_key, subj_data in progress_data.get("subjects", {}).items():
            overall = subj_data.get("overall_progress", 0)
            phase = subj_data.get("phase", "")
            print(f"    {subj_key}: {overall*100:.0f}% ({phase})")
    else:
        print(f"  学习进度：progress.json 不存在，使用笔记覆盖率估算")

    # 待修的问题卡（闪卡练习里标记的「题目本身错了」）：这是 agent 的活，
    # 每次生成计划都报一句，别让它们沉在库里没人管。
    _reports = load_card_reports()
    if _reports:
        print(f"  ⚠️ 待修的问题卡：{len(_reports)} 张"
              f"（node src/card_reports.js list → 改题/驳回/删卡）")

    days_to_exam = (EXAM_DATE - target_date).days
    phase_name, phase_data = get_current_phase(target_date)

    print(f"\n目标日期：{target_date.strftime('%Y-%m-%d')}")
    print(f"距考试：{days_to_exam} 天 | 阶段：{phase_name}")
    print("-" * 50)

    if args.preview:
        # Preview mode
        output = generate_preview(graphs, notes_index, qb_data, target_date, days=7)
        print(output)
        # Also write to file
        preview_path = os.path.join(DAILY_PLAN_DIR, f"preview_{target_date.strftime('%Y-%m-%d')}.md")
        with open(preview_path, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"\n预览已保存到：{preview_path}")
        return

    if args.review:
        # Evening review mode
        output = evening_review(target_date, graphs, notes_index, qb_data)
        print(output)
        # Save review
        review_path = os.path.join(DAILY_PLAN_DIR, f"review_{target_date.strftime('%Y-%m-%d')}.md")
        with open(review_path, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"\n回顾已保存到：{review_path}")
        return

    # Normal plan generation
    output = generate_plan_markdown(target_date, graphs, notes_index, qb_data,
                                     override_hours=args.hours, progress_data=progress_data)

    # Write output file
    plan_filename = f"plan_{target_date.strftime('%Y-%m-%d')}.md"
    plan_path = os.path.join(DAILY_PLAN_DIR, plan_filename)
    with open(plan_path, "w", encoding="utf-8") as f:
        f.write(output)

    # Print summary to stdout
    print(output)
    print(f"\n{'='*50}")
    print(f"计划已生成：{plan_path}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()

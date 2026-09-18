#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gap_analysis.py — 考研知识图谱差距分析

Loads the 4 knowledge graph JSON files + 笔记索引.yaml, matches notes to
topics, identifies gaps, and ranks them by study priority.

Usage:
    python gap_analysis.py                  # full analysis
    python gap_analysis.py --subject 408    # one subject only
"""

import argparse
import io
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

# 政治笔记用 MY-001 / 思修-001 这类短编号，与图谱前缀 POL-MY 不是一套体系。
# 本文件原先没有这张映射表，把 "MY-001" 整体当前缀，导致政治笔记永远匹配不上图谱，
# 「笔记覆盖」恒为 0（2026-09-17 修，表与 generate_dashboard.py 共用）。
from note_prefix import note_entry_prefix, is_cross_subject, topic_note_chapter

# Force UTF-8 on Windows stdout to avoid GBK encoding errors with CJK output
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_KAOYAN = r"C:\Users\92534\Desktop\考研"
GRAPH_DIR  = os.path.join(_KAOYAN, r"src\knowledge_graph")
INDEX_PATH = os.path.join(_KAOYAN, "src", "笔记索引.yaml")
PROGRESS_PATH = os.path.join(_KAOYAN, r"src\progress.json")
# 英语题型判定要读闪卡答题记录（最客观的「练过」证据），所以这里也要连库
DB_PATH = os.path.join(_KAOYAN, "src", "question_bank.db")

# Map graph subject keys -> progress.json subject keys
# 2026-09-19 起科目映射由 subjects.json 派生（subjects_conf.py），
# 与 serve.js / generate_dashboard.py 同口径，改学科只动配置文件。
import subjects_conf  # noqa: E402
PROGRESS_SUBJECT_MAP = subjects_conf.progress_map()          # graph_key -> progress_key
GRAPH_FILES = subjects_conf.graph_files()                    # graph_key -> 图谱文件名

# Map graph subject -> human label used in the section header
SUBJECT_HEADERS = subjects_conf.graph_headers()              # graph_key -> "名称 (总分 N)"

# Map short sub keys (used in 408 graph) to display names
# 子科缩略显示名（「高数」「马原」）与 key 都在 subjects.json 的 subs 里。
SUB_DISPLAY = subjects_conf.sub_display()

# ---------------------------------------------------------------------------
# YAML loading (with fallback)
# ---------------------------------------------------------------------------

try:
    import yaml as _yaml

    def load_yaml(path):
        with open(path, "r", encoding="utf-8") as fh:
            return _yaml.safe_load(fh)

except ImportError:
    # Minimal fallback: only handles the subset we actually emit
    def load_yaml(path):
        """
        Bare-bones YAML loader — sufficient for the structure produced by
        build_index.py. Falls back to json when the file is valid JSON, and
        uses a line-by-line parser otherwise.
        """
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        # Try JSON first (in case someone converted it)
        try:
            return json.loads(text)
        except Exception:
            pass
        # Simple line-based parser for the subset we produce
        return _simple_yaml_parse(text)

    def _simple_yaml_parse(text):
        """
        Very simple YAML-subset parser: handles scalars, lists of dicts,
        and nested dicts up to ~4 levels. Enough for 笔记索引.yaml.
        """
        import ast

        lines = text.splitlines()
        root = {}
        stack = [(root, -1)]  # (container, indent)

        i = 0
        while i < len(lines):
            line = lines[i]
            i += 1
            stripped = line.rstrip()
            if not stripped or stripped.startswith("#"):
                continue

            indent = len(line) - len(line.lstrip())

            # Pop stack to correct level
            while len(stack) > 1 and indent <= stack[-1][1]:
                stack.pop()

            parent, _ = stack[-1]

            # List item
            if stripped.lstrip().startswith("- "):
                item_text = stripped.lstrip()[2:].strip()
                if ":" in item_text:
                    # Dict item in list
                    key, _, val = item_text.partition(":")
                    key = key.strip()
                    val = val.strip()
                    item_dict = {key: _yaml_val(val)}
                    if isinstance(parent, list):
                        parent.append(item_dict)
                    elif isinstance(parent, dict):
                        # Find last key that holds a list
                        pass
                    stack.append((item_dict, indent + 2))
                else:
                    if isinstance(parent, list):
                        parent.append(_yaml_val(item_text))
                continue

            # Key: value
            if ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip().strip("'\"")
                val = val.strip()
                if val:
                    if isinstance(parent, dict):
                        parent[key] = _yaml_val(val)
                    elif isinstance(parent, list):
                        pass
                else:
                    # Look ahead to decide if it's a list or dict
                    next_indent = None
                    j = i
                    while j < len(lines):
                        nxt = lines[j].rstrip()
                        if nxt and not nxt.strip().startswith("#"):
                            next_indent = len(lines[j]) - len(lines[j].lstrip())
                            break
                        j += 1
                    if next_indent is not None and lines[j].lstrip().startswith("- "):
                        child = []
                    else:
                        child = {}
                    if isinstance(parent, dict):
                        parent[key] = child
                    stack.append((child, indent))

        return root

    def _yaml_val(s):
        if not s:
            return ""
        s = s.strip()
        if s in ("true", "True"):
            return True
        if s in ("false", "False"):
            return False
        if s in ("null", "~", "None"):
            return None
        if s.startswith("'") and s.endswith("'"):
            return s[1:-1].replace("''", "'")
        if s.startswith('"') and s.endswith('"'):
            return s[1:-1]
        if s.startswith("[") and s.endswith("]"):
            inner = s[1:-1]
            if not inner.strip():
                return []
            return [_yaml_val(x.strip()) for x in inner.split(",")]
        try:
            return int(s)
        except ValueError:
            pass
        try:
            return float(s)
        except ValueError:
            pass
        return s

# ---------------------------------------------------------------------------
# Graph / Index loading
# ---------------------------------------------------------------------------

def load_json(path):
    """Load a JSON file, returning None on error."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        print(f"  [WARNING] Graph file not found: {path}")
        return None
    except Exception as exc:
        print(f"  [WARNING] Cannot load graph ({exc}): {path}")
        return None


def get_topic_prefix(topic_id):
    """
    Return the sub-level prefix from a topic ID.
    '408-DS-01' -> '408-DS'
    'MATH-GS-01' -> 'MATH-GS'
    'POL-MY-01' -> 'POL-MY'
    """
    parts = topic_id.split("-")
    if len(parts) >= 2:
        return "-".join(parts[:2])
    return topic_id


def load_progress():
    """Load user's actual learning progress from progress.json."""
    if os.path.exists(PROGRESS_PATH):
        try:
            with open(PROGRESS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [WARNING] Cannot load progress.json ({e}): {PROGRESS_PATH}")
    return None


def get_note_prefix(note_id):
    """
    Return the sub-level prefix from a note entry ID.
    '408-DS-001' -> '408-DS'
    'MATH-GS-001' -> 'MATH-GS'
    'MY-001' -> 'POL-MY'      （政治短编号需查表换算）
    '思修-001' -> 'POL-SX'
    'ZT-001' -> ''            （跨科目专题，不计入任何科目）
    """
    return note_entry_prefix(note_id)


def extract_chapter_number(chapter_str):
    """
    Extract the main chapter number from strings like '第2章', '第4.7章', '第12章'.
    Returns int or None.
    """
    m = re.search(r"(\d+)", str(chapter_str))
    return int(m.group(1)) if m else None

# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def build_note_lookup(entries):
    """
    Build lookup structures from note entries:
      - prefix_chapters: { prefix: { chapter_int: count } }
      - prefix_total:    { prefix: total_count }
    """
    prefix_chapters = {}  # e.g. {'408-DS': {1: 3, 2: 5, ...}}
    prefix_total = {}

    for entry in entries:
        nid = entry.get("id", "")
        if not nid:
            continue
        prefix = get_note_prefix(nid)
        if not prefix:
            # 跨科目方法论专题（ZT-*）：横向汇总，无对应图谱科目，不参与覆盖率
            continue
        ch = extract_chapter_number(entry.get("chapter", ""))

        if prefix not in prefix_total:
            prefix_total[prefix] = 0
            prefix_chapters[prefix] = {}
        prefix_total[prefix] += 1
        if ch is not None:
            prefix_chapters[prefix][ch] = prefix_chapters[prefix].get(ch, 0) + 1

    return prefix_chapters, prefix_total


def analyze_graph(graph, note_prefix_chapters, note_prefix_total):
    """
    For one knowledge graph, compute per-topic coverage.
    Returns list of topic_result dicts.
    """
    subject = graph["subject"]
    results = []

    for sub_key, sub_data in graph.get("subs", {}).items():
        topics = sub_data.get("topics", [])
        # Determine the prefix for this sub's topics (use first topic's id)
        sub_prefix = None
        if topics:
            sub_prefix = get_topic_prefix(topics[0]["id"])

        for topic in topics:
            tid = topic["id"]
            t_prefix = get_topic_prefix(tid)
            ch = topic.get("chapter")            # 展示用（数学是张宇讲次）
            match_ch = topic_note_chapter(topic)  # 匹配笔记用（数学是笔记章号）

            # Count matching notes
            note_count = 0
            covered = False
            if t_prefix in note_prefix_chapters:
                ch_counts = note_prefix_chapters[t_prefix]
                if match_ch is not None and match_ch in ch_counts:
                    note_count = ch_counts[match_ch]
                    covered = True

            # Also count total notes for this prefix (regardless of chapter)
            total_for_prefix = note_prefix_total.get(t_prefix, 0)

            results.append({
                "id": tid,
                "name": topic["name"],
                "sub_key": sub_key,
                "sub_name": sub_data.get("name", sub_key),
                "chapter": ch,
                "exam_weight": topic.get("exam_weight", 0),
                "exam_points": topic.get("exam_points", "?"),
                "difficulty": topic.get("difficulty", 0.5),
                "prerequisites": topic.get("prerequisites", []),
                "exam_frequency": topic.get("exam_frequency", 0),
                "covered": covered,
                "note_count": note_count,
                "total_for_prefix": total_for_prefix,
            })

    return results


def compute_gap_priority(topic_result, covered_ids):
    """
    priority = exam_weight * prereq_multiplier * freq_boost
    - prereq_multiplier: 1.0 if all prerequisites covered else 0.5
    - freq_boost: 1.0 + min(exam_frequency * 0.1, 0.5)  (up to +50% for high-frequency topics)
    """
    prereqs = topic_result["prerequisites"]
    if not prereqs:
        prereq_met = True
    else:
        prereq_met = all(p in covered_ids for p in prereqs)

    weight = topic_result["exam_weight"]
    multiplier = 1.0 if prereq_met else 0.5

    # 真题频率加权：高频考点优先覆盖
    exam_freq = topic_result.get("exam_frequency", 0)
    freq_boost = 1.0 + min(exam_freq * 0.1, 0.5)  # 每出现1次真题加10%，最多加50%

    priority = weight * multiplier * freq_boost
    return priority, prereq_met


# ---------------------------------------------------------------------------
# Progress-based coverage overlay
# ---------------------------------------------------------------------------

# Map graph sub code -> progress.json subjects.politics.course/exercises key
_POLITICS_CODE_TO_KEY = {
    "POL-MY": "马原",
    "POL-MZ": "毛中特",
    "POL-SG": "史纲",
    "POL-SX": "思修",
    "POL-XX": "习思想",
}


def _sub_code(sub_prefix):
    """Return 'POL-MY' from topic id 'POL-MY-01'."""
    return "-".join(sub_prefix.split("-")[:2]) if "-" in sub_prefix else sub_prefix


def apply_politics_overlay(topic_results, progress_data):
    """
    For 政治: use progress.json course + exercise completion to mark
    topics as covered when notes.md files are missing.

    Rule: a topic is "covered" if course completion for that sub >= 0.9.
    """
    subjects = progress_data.get("subjects", {})
    politics = subjects.get("政治", {})
    course_progress = politics.get("course", {})
    exercise_progress = politics.get("exercises", {})

    COURSE_THRESHOLD = 0.9

    newly_covered = 0
    progress_meta = {}  # {sub_name: {'course': x, 'exercise': y, 'newly': n}}

    for t in topic_results:
        code = _sub_code(t["id"])
        prog_key = _POLITICS_CODE_TO_KEY.get(code)
        if not prog_key:
            continue

        course_val = course_progress.get(prog_key, 0)
        exercise_val = exercise_progress.get(prog_key, 0)

        if course_val >= COURSE_THRESHOLD and not t["covered"]:
            t["covered"] = True
            t["coverage_source"] = "progress"
            newly_covered += 1
            sub_name = t["sub_name"]
            if sub_name not in progress_meta:
                progress_meta[sub_name] = {
                    "course": course_val,
                    "exercise": exercise_val,
                    "newly": 0,
                }
            progress_meta[sub_name]["newly"] += 1

    return newly_covered, progress_meta


# 英语覆盖探测的告警收集器。以前所有探测都是 except: pass，
# 目录改名后统计恒为 0 却毫无提示，缺口列表因此长期错误。现在统一收集并打印。
ENGLISH_PROBE_WARN = []


def _first_existing_dir(cands, label):
    """按候选顺序返回第一个存在的目录；全都不存在就记一条告警。"""
    for p in cands:
        if os.path.isdir(p):
            return p
    ENGLISH_PROBE_WARN.append(
        f"{label}未找到（试过：{[os.path.basename(c) for c in cands]}）——相关考点会被误判为缺口")
    return cands[0]


def _english_review_counts():
    """
    各题型（ENG-READ-02 这一级）的闪卡数与答题记录，作为「练过」的客观证据。

    卡挂在 subtopic（ENG-READ-02-01）上，所以按 ID 前三段归并到题型级。
    读不到就返回空、少一路证据，不影响其它判定。
    """
    out = {}
    try:
        # file: URI 里的反斜杠在 Windows 上不可靠，统一换成正斜杠
        c = sqlite3.connect(f"file:{DB_PATH.replace(os.sep, '/')}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        rows = c.execute("""
            SELECT t.id AS tid,
                   COUNT(DISTINCT ca.id) AS cards,
                   COUNT(rl.id) AS reviews,
                   SUM(CASE WHEN rl.rating >= 3 THEN 1 ELSE 0 END) AS ok
              FROM topics t
              LEFT JOIN questions q ON q.topic_id = t.id
              LEFT JOIN cards ca ON ca.question_id = q.id AND COALESCE(ca.suspended,0) = 0
              LEFT JOIN review_log rl ON rl.question_id = q.id
             WHERE t.subject = '英语一'
             GROUP BY t.id
        """)
        for r in rows:
            stem = "-".join(str(r["tid"]).split("-")[:3])
            agg = out.setdefault(stem, [0, 0, 0])
            agg[0] += r["cards"] or 0
            agg[1] += r["reviews"] or 0
            agg[2] += r["ok"] or 0
        c.close()
    except Exception as exc:
        ENGLISH_PROBE_WARN.append(f"闪卡答题记录读不到（{exc}），题型判定少一路证据")
    return out


def apply_english_overlay(topic_results, progress_data):
    """
    For 英语一: use a multi-dimensional approach combining data from
    multiple sources to determine per-topic coverage.

    Data sources:
      - progress.json: vocabulary_size, phrases_learned, overall_progress
      - vocab_database.json: word count, distinction count
      - Grammar notes: 考研英语语法笔记.md, 长难句笔记.md
      - 外刊 articles: reading volume
      - Writing records: essay correction .md files
      - Translation files: translation practice .md files
    """
    english_data = progress_data.get("subjects", {}).get("英语", {})

    # --- 1. Vocabulary data ---
    vocab_size = english_data.get("vocabulary_size", 0)
    vocab_target = english_data.get("vocab_target", 5500)
    phrases_learned = english_data.get("phrases_learned", 0)

    vocab_db_path = os.path.join(
        _KAOYAN, r"English\word&phrase\vocab-graph\data\vocab_database.json"
    )
    word_count = 0
    distinction_count = 0
    try:
        with open(vocab_db_path, "r", encoding="utf-8") as f:
            vocab_db = json.load(f)
        if isinstance(vocab_db, dict):
            word_count = len(vocab_db.get("words", []))
            distinction_count = len(vocab_db.get("distinctions", []))
    except Exception as exc:
        ENGLISH_PROBE_WARN.append(f"词汇库读不到（{exc}），辨析数按 0 计")

    # --- 2. Grammar data ---
    grammar_path = os.path.join(
        _KAOYAN, r"English\grammar\考研英语语法笔记.md"
    )
    grammar_exists = os.path.exists(grammar_path)
    if not grammar_exists:
        ENGLISH_PROBE_WARN.append(f"语法笔记不存在：{grammar_path}")

    long_sentence_path = os.path.join(
        _KAOYAN, r"English\grammar\长难句笔记.md"
    )
    long_sentence_count = 0
    try:
        with open(long_sentence_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Count note-meta:entry blocks first
        long_sentence_count = content.count("<!-- note-meta:entry")
        # Fallback: count '## YYYY-MM-DD' date headings
        if long_sentence_count == 0:
            long_sentence_count = len(
                re.findall(r"^## \d{4}-\d{2}-\d{2}", content, re.MULTILINE)
            )
    except Exception as exc:
        ENGLISH_PROBE_WARN.append(f"长难句笔记读不到（{exc}）")

    # --- 3~5. 按「题型」判定覆盖：交给共享模块 ---
    # 判定实现放在 english_coverage.py，gap_analysis 与 generate_dashboard 共用同一份。
    # 之前两边各算各的：报告说「写作 3/3 已覆盖」，大盘却仍把小作文/大作文列进真缺口——
    # 同一件事两套口径必然长期打架，所以这里只留一次调用。
    from english_coverage import english_type_coverage, english_review_counts
    cov, metrics, warns = english_type_coverage(
        _KAOYAN, progress_data, english_review_counts(DB_PATH))
    ENGLISH_PROBE_WARN.extend(warns)

    # coverage_info 沿用原结构：{子科: {"metric": 摘要, "details": {topic_id: bool}}}
    # key 是考点 ID 的第二段；value 必须与图谱的 sub 名逐字一致，否则覆盖结果会被丢掉。
    # 「翻译与完形」拆成两个题型后，TRN 换成 CLOZE/TRAN（2026-09-17）。
    SUB_OF = {"VOC": "词汇", "GRAM": "语法与长难句", "READ": "阅读理解",
              "WRITE": "写作", "CLOZE": "完形填空", "TRAN": "翻译"}
    coverage_info = {s: {"metric": metrics.get(s, ""), "details": {}}
                     for s in SUB_OF.values()}
    for tid, (ok, _why) in cov.items():
        seg = tid.split("-")[1] if "-" in tid else ""
        sub = SUB_OF.get(seg)
        if sub:
            coverage_info[sub]["details"][tid] = bool(ok)


    # Apply overlay
    newly_covered = 0
    for t in topic_results:
        if t["covered"]:
            continue
        sub_name = t["sub_name"]
        info = coverage_info.get(sub_name)
        if info and info["details"].get(t["id"], False):
            t["covered"] = True
            t["coverage_source"] = "progress"
            newly_covered += 1

    return newly_covered, coverage_info


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_subject_section(subject, graph, topic_results, note_prefix_total):
    """Build the printed output for one subject section."""
    lines = []
    header = SUBJECT_HEADERS.get(subject, f"{subject} (总分 {graph.get('total_score', '?')})")
    lines.append(f"--- {header} ---")

    # Coverage summary per sub
    sub_keys = list(graph.get("subs", {}).keys())
    coverage_parts = []
    all_topic_count = 0
    covered_topic_count = 0

    for sk in sub_keys:
        sub_topics = [t for t in topic_results if t["sub_key"] == sk]
        total_ch = len(sub_topics)
        covered_ch = sum(1 for t in sub_topics if t["covered"])
        all_topic_count += total_ch
        covered_topic_count += covered_ch
        display = SUB_DISPLAY.get(sk, sk)
        coverage_parts.append(f"{display} {covered_ch}/{total_ch}")

    if all_topic_count == 0:
        lines.append("  Coverage: No topics in graph")
    elif covered_topic_count == 0:
        # Check if there are notes at all
        total_notes = sum(note_prefix_total.values())
        if subject == "政治":
            lines.append("  Coverage: All 0% (no notes yet)")
        elif subject == "英语一":
            lines.append(f"  Coverage: Partial ({total_notes} notes but not mapped to graph yet)")
        else:
            lines.append(f"  Coverage: {' | '.join(coverage_parts)}")
    else:
        lines.append(f"  Coverage: {' | '.join(coverage_parts)}")

    # Gaps (uncovered topics)
    gaps = [t for t in topic_results if not t["covered"]]
    covered_ids = {t["id"] for t in topic_results if t["covered"]}

    # Compute priorities
    gap_priorities = []
    for g in gaps:
        pri, prereq_met = compute_gap_priority(g, covered_ids)
        gap_priorities.append((g, pri, prereq_met))

    gap_priorities.sort(key=lambda x: -x[1])

    if gap_priorities:
        lines.append("")
        lines.append("  Top Priority Gaps:")
        for rank, (g, pri, prereq_met) in enumerate(gap_priorities[:20], start=1):
            prereq_str = "prerequisites met" if prereq_met else "prerequisites NOT met"
            lines.append(
                f"    {rank:2d}. [{g['id']}] {g['name']} "
                f"({g['exam_points']}, difficulty {g['difficulty']}) "
                f"— {prereq_str} — PRIORITY: {pri:.1f}"
            )
    else:
        lines.append("")
        lines.append("  All topics are covered! Great job!")

    lines.append("")
    return "\n".join(lines)


def format_per_sub_gaps(subject, graph, topic_results):
    """Per-sub gap list (supplementary detail)."""
    lines = []
    lines.append(f"  Per-sub gaps for {subject}:")
    covered_ids = {t["id"] for t in topic_results if t["covered"]}

    for sub_key, sub_data in graph.get("subs", {}).items():
        sub_topics = [t for t in topic_results if t["sub_key"] == sub_key]
        gaps = [t for t in sub_topics if not t["covered"]]
        display = SUB_DISPLAY.get(sub_key, sub_data.get("name", sub_key))
        if not gaps:
            lines.append(f"    {display}: fully covered")
        else:
            lines.append(f"    {display} ({len(gaps)} gaps):")
            for g in sorted(gaps, key=lambda x: -x["exam_weight"]):
                pri, prereq_met = compute_gap_priority(g, covered_ids)
                flag = "[+]" if prereq_met else "[-]"
                lines.append(
                    f"      {flag} [{g['id']}] Ch{g['chapter']} {g['name']}  "
                    f"weight={g['exam_weight']}  pri={pri:.1f}"
                )
    lines.append("")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="考研知识图谱差距分析")
    parser.add_argument(
        "--subject", "-s",
        choices=["408", "数学一", "政治", "英语一"],
        default=None,
        help="分析指定科目 (default: all)",
    )
    args = parser.parse_args()

    print()
    print("=== 考研知识图谱差距分析 ===")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # --- Load graphs ---
    graphs = {}
    for subj, fname in GRAPH_FILES.items():
        if args.subject and subj != args.subject:
            continue
        path = os.path.join(GRAPH_DIR, fname)
        g = load_json(path)
        if g:
            graphs[subj] = g

    if not graphs:
        print("  [ERROR] No knowledge graphs could be loaded. Check GRAPH_DIR.")
        sys.exit(1)

    # --- Load index ---
    entries = []
    note_prefix_chapters = {}
    note_prefix_total = {}

    if os.path.exists(INDEX_PATH):
        try:
            index = load_yaml(INDEX_PATH)
            if index and "entries" in index:
                entries = index["entries"]
                print(f"  Loaded {len(entries)} note entries from {INDEX_PATH}")
            else:
                print(f"  [WARNING] Index file has no 'entries' key: {INDEX_PATH}")
        except Exception as exc:
            print(f"  [WARNING] Cannot load index ({exc}): {INDEX_PATH}")
    else:
        print(f"  [WARNING] Index file not found: {INDEX_PATH}")
        print("             Run build_index.py first to generate it.")

    # Build note lookup
    note_prefix_chapters, note_prefix_total = build_note_lookup(entries)

    if note_prefix_total:
        print("  Note prefixes found:")
        for prefix, cnt in sorted(note_prefix_total.items()):
            chs = sorted(note_prefix_chapters.get(prefix, {}).keys())
            ch_str = ", ".join(str(c) for c in chs) if chs else "none"
            print(f"    {prefix}: {cnt} notes  (chapters: {ch_str})")

    # --- Load progress.json ---
    progress_data = load_progress()
    if progress_data:
        updated = progress_data.get("updated", "unknown")
        print(f"\n  Progress data loaded from progress.json (updated: {updated})")
        print("  Method:")
        for subj_label, method in progress_data.get("method", {}).items():
            print(f"    {subj_label}: {method}")
    else:
        print("\n  [INFO] progress.json not found — using note coverage only.")
    print()

    # --- Analyze each graph (with progress overlays) ---
    analysis_results = {}  # subj -> (graph, topic_results, progress_meta)

    for subj in ["408", "数学一", "政治", "英语一"]:
        if subj not in graphs:
            continue
        graph = graphs[subj]
        topic_results = analyze_graph(graph, note_prefix_chapters, note_prefix_total)

        # Apply progress-based overlay for politics and english
        progress_meta = None
        if subj == "政治" and progress_data:
            _, progress_meta = apply_politics_overlay(topic_results, progress_data)
        elif subj == "英语一" and progress_data:
            _, progress_meta = apply_english_overlay(topic_results, progress_data)

        analysis_results[subj] = (graph, topic_results, progress_meta)

    # 探测告警：宁可吵一句，也不要静默把「已经整理过」的考点报成缺口。
    # 这次英语写作/翻译长期显示 0/3、0/4，就是因为目录改名后 except 把异常吞了。
    if ENGLISH_PROBE_WARN:
        print("\n⚠️  英语覆盖探测告警")
        seen = set()
        for w in ENGLISH_PROBE_WARN:
            if w in seen:
                continue
            seen.add(w)
            print(f"   · {w}")
        print("   （缺口判定可能偏保守，请核对路径或文件命名）")

    # --- Print per-subject sections ---
    for subj in ["408", "数学一", "政治", "英语一"]:
        if subj not in analysis_results:
            continue
        graph, topic_results, progress_meta = analysis_results[subj]

        section = format_subject_section(subj, graph, topic_results, note_prefix_total)
        print(section)

        # Print progress-based coverage info for politics
        if subj == "政治" and progress_meta:
            print("  [progress.json 覆盖层]")
            for sub_name, meta in progress_meta.items():
                c = meta["course"]
                e = meta["exercise"]
                print(f"    {sub_name}: 课程{c:.0%} 刷题{e:.0%} → {meta['newly']}个章节已覆盖")
            print()

        # Print multi-dimensional coverage info for english
        if subj == "英语一" and progress_meta:
            print("  [多维统计口径]")
            for sub_name, info in progress_meta.items():
                covered_count = sum(1 for v in info["details"].values() if v)
                total_count = len(info["details"])
                print(f"    {sub_name}: {info['metric']}  ({covered_count}/{total_count})")
            print()

        # Per-sub detail
        gaps_exist = any(not t["covered"] for t in topic_results)
        if gaps_exist:
            detail = format_per_sub_gaps(subj, graph, topic_results)
            print(detail)

    # --- Overall summary ---
    print("=" * 60)
    print("  Overall Summary")
    print("=" * 60)

    total_topics = 0
    total_covered = 0
    for subj in ["408", "数学一", "政治", "英语一"]:
        if subj not in analysis_results:
            continue
        _graph, topic_results, _meta = analysis_results[subj]
        n = len(topic_results)
        c = sum(1 for t in topic_results if t["covered"])
        total_topics += n
        total_covered += c
        pct = round(c / n * 100, 1) if n > 0 else 0
        # Show coverage source breakdown
        note_covered = sum(1 for t in topic_results
                          if t["covered"] and t.get("coverage_source") != "progress")
        prog_covered = sum(1 for t in topic_results
                          if t.get("coverage_source") == "progress")
        source_note = ""
        if prog_covered > 0:
            source_note = f"  (笔记{note_covered} + 进度{prog_covered})"
        print(f"  {subj:6s}: {c:3d}/{n:3d} topics covered ({pct}%){source_note}")

    if total_topics > 0:
        overall_pct = round(total_covered / total_topics * 100, 1)
        print(f"  {'TOTAL':6s}: {total_covered:3d}/{total_topics:3d} topics covered ({overall_pct}%)")
    print()

    # Suggest next steps
    print("  Recommendations:")
    # Find highest priority gap across all subjects
    best_gap = None
    best_pri = -1
    for subj in ["408", "数学一", "政治", "英语一"]:
        if subj not in analysis_results:
            continue
        _graph, topic_results, _meta = analysis_results[subj]
        covered_ids = {t["id"] for t in topic_results if t["covered"]}
        for t in topic_results:
            if not t["covered"]:
                pri, _ = compute_gap_priority(t, covered_ids)
                if pri > best_pri:
                    best_pri = pri
                    best_gap = (subj, t)

    if best_gap:
        subj, g = best_gap
        print(f"  -> Next best topic to study: [{g['id']}] {g['name']} ({subj})")
        print(f"     Priority: {best_pri:.1f}  |  Exam weight: {g['exam_points']}")
    else:
        print("  -> All topics covered — keep reviewing!")
    print()
    print("  Done.")


if __name__ == "__main__":
    main()

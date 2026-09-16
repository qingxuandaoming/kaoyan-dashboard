#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_index.py — Parse 12 draft note files ("草稿箱") and generate 笔记索引.yaml.

Usage:
    python build_index.py

Idempotent: safe to re-run at any time. Missing files are skipped with a warning.
"""

import os
import re
import sys
import io
from datetime import datetime
from collections import defaultdict

# Force UTF-8 on Windows stdout to avoid GBK encoding errors with CJK output
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_BASE = r"C:\Users\92534\Desktop\考研"

DRAFT_FILES = {
    "高数": os.path.join(_BASE, r"Math\高数\高数 notes.md"),
    "线代": os.path.join(_BASE, r"Math\线代\线代 notes.md"),
    "概率": os.path.join(_BASE, r"Math\概率论\概率 notes.md"),
    "DS":   os.path.join(_BASE, r"408\DS\DS notes.md"),
    "CO":   os.path.join(_BASE, r"408\CO\CO notes.md"),
    "OS":   os.path.join(_BASE, r"408\OS\OS notes.md"),
    "CN":   os.path.join(_BASE, r"408\CN\CN notes.md"),
    "马原": os.path.join(_BASE, r"Politics\马原\notes.md"),
    "史纲": os.path.join(_BASE, r"Politics\史纲\notes.md"),
    "毛中特": os.path.join(_BASE, r"Politics\毛中特\notes.md"),
    "思修": os.path.join(_BASE, r"Politics\思修\notes.md"),
    "习思想": os.path.join(_BASE, r"Politics\习思想\notes.md"),
}

OUTPUT_PATH = os.path.join(_BASE, "src", "笔记索引.yaml")
JSON_INDEX_PATH = os.path.join(_BASE, r"Math\notes_index.json")
JSON_INDEX_408_PATH = os.path.join(_BASE, r"408\notes_index.json")
JSON_INDEX_POL_PATH = os.path.join(_BASE, r"Politics\notes_index.json")
JSON_INDEX_RE_PATH = os.path.join(_BASE, r"Re-examination\notes_index.json")
# 英语笔记是普通 Markdown（无 note-meta 块），由 tools/gen_english_index.py 扫成 JSON
JSON_INDEX_ENG_PATH = os.path.join(_BASE, r"English\notes_index.json")

# ---------------------------------------------------------------------------
# YAML helpers — try PyYAML, fall back to manual emission
# ---------------------------------------------------------------------------

try:
    import yaml

    def write_yaml(data, path):
        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump(
                data, fh,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
                width=120,
            )

except ImportError:
    def write_yaml(data, path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_manual_yaml(data))

    def _manual_yaml(data, indent=0):
        lines = []
        prefix = "  " * indent
        if isinstance(data, dict):
            for key, val in data.items():
                safe_key = _yaml_safe_key(key)
                if isinstance(val, dict):
                    lines.append(f"{prefix}{safe_key}:")
                    lines.append(_manual_yaml(val, indent + 1))
                elif isinstance(val, list):
                    if not val:
                        lines.append(f"{prefix}{safe_key}: []")
                    elif all(isinstance(v, (str, int, float)) for v in val):
                        items = ", ".join(_yaml_scalar(v) for v in val)
                        lines.append(f"{prefix}{safe_key}: [{items}]")
                    else:
                        lines.append(f"{prefix}{safe_key}:")
                        for item in val:
                            if isinstance(item, dict):
                                first = True
                                for k2, v2 in item.items():
                                    sk2 = _yaml_safe_key(k2)
                                    if first:
                                        lines.append(f"{prefix}- {sk2}: {_yaml_scalar(v2)}")
                                        first = False
                                    else:
                                        lines.append(f"{prefix}  {sk2}: {_yaml_scalar(v2)}")
                            else:
                                lines.append(f"{prefix}- {_yaml_scalar(item)}")
                else:
                    lines.append(f"{prefix}{safe_key}: {_yaml_scalar(val)}")
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    first = True
                    for k, v in item.items():
                        sk = _yaml_safe_key(k)
                        if first:
                            lines.append(f"{prefix}- {sk}: {_yaml_scalar(v)}")
                            first = False
                        else:
                            lines.append(f"{prefix}  {sk}: {_yaml_scalar(v)}")
                else:
                    lines.append(f"{prefix}- {_yaml_scalar(item)}")
        else:
            lines.append(f"{prefix}{_yaml_scalar(data)}")
        return "\n".join(lines)

    def _yaml_safe_key(key):
        s = str(key)
        if any(c in s for c in ":#{}[]&*?|>!%@`") or s.startswith(("-", " ")):
            return f"'{s}'"
        return s

    def _yaml_scalar(val):
        if val is None:
            return "null"
        if isinstance(val, bool):
            return "true" if val else "false"
        if isinstance(val, (int, float)):
            return str(val)
        s = str(val)
        if not s:
            return "''"
        # Quote strings that might be misinterpreted
        needs_quote = (
            any(c in s for c in ":#{}[]&*?|>!%@`,'\"")
            or s.startswith(("-", " "))
            or s in ("true", "false", "yes", "no", "null", "True", "False")
            or re.match(r"^\d{4}-\d{2}-\d{2}$", s)
            or re.match(r"^\d+\.?\d*$", s)
        )
        if needs_quote:
            escaped = s.replace("'", "''")
            return f"'{escaped}'"
        return s

# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def read_file(path):
    """Read a file with UTF-8 BOM fallback. Returns content string or None."""
    for enc in ("utf-8", "utf-8-sig"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except UnicodeDecodeError:
            continue
        except FileNotFoundError:
            print(f"  [WARNING] File not found, skipping: {path}")
            return None
        except OSError as exc:
            print(f"  [WARNING] Cannot read file ({exc}), skipping: {path}")
            return None
    # Last resort
    try:
        with open(path, "r", encoding="gbk") as fh:
            return fh.read()
    except Exception as exc:
        print(f"  [WARNING] Cannot decode file ({exc}), skipping: {path}")
        return None

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# Matches multiline HTML comment blocks:  <!-- note-meta:entry  ... -->
ENTRY_RE = re.compile(
    r"<!--\s*note-meta:entry\s*\n(.*?)-->",
    re.DOTALL,
)

FILE_META_RE = re.compile(
    r"<!--\s*note-meta:file\s*\n(.*?)-->",
    re.DOTALL,
)


def parse_kv_block(block):
    """Parse 'key: value' lines into a dict."""
    result = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        result[key] = value
    return result


def parse_tags(raw):
    """Parse tags from '[a, b, c]' or 'a,b,c' format into a list of strings."""
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    if not raw.strip():
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def clean_markdown(text):
    """Strip Markdown formatting to produce plain text suitable for summaries."""
    # Remove HTML comments
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # Remove images
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    # Convert links to their display text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove inline code backticks
    text = text.replace("`", "")
    # Remove display math blocks ($$...$$)
    text = re.sub(r"\$\$.*?\$\$", "[公式]", text, flags=re.DOTALL)
    # Remove inline math ($...$)
    text = re.sub(r"\$[^$\n]+\$", "[公式]", text)
    # Remove heading markers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove bold/italic markers
    text = re.sub(r"\*{1,3}", "", text)
    text = re.sub(r"_{1,3}", "", text)
    # Remove horizontal rules
    text = re.sub(r"^-{3,}\s*$", "", text, flags=re.MULTILINE)
    # Collapse table separator rows (|---|---|)
    text = re.sub(r"^\|[\s\-:|]+\|$", "", text, flags=re.MULTILINE)
    # Convert table cell separators to commas within rows
    text = re.sub(r"\s*\|\s*", ", ", text)
    # Strip leading/trailing commas left from table conversion
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"^,\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*,\s*$", "", text, flags=re.MULTILINE)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_summary(raw_body, max_len=500):
    """Extract a plain-text summary (first ~max_len chars) from a note body."""
    plain = clean_markdown(raw_body)
    # Drop the heading line (### date — title) if present
    lines = plain.split("\n")
    if lines and lines[0].startswith("### "):
        lines = lines[1:]
    plain = "\n".join(lines).strip()
    if len(plain) <= max_len:
        return plain
    # Truncate at a sentence boundary if possible
    truncated = plain[:max_len]
    last_period = max(truncated.rfind("。"), truncated.rfind("\n"))
    if last_period > max_len // 2:
        truncated = truncated[:last_period + 1]
    return truncated.rstrip() + "…"


def parse_entries(content, source_file=""):
    """Extract all note-meta:entry blocks and their body content from a file.

    For each entry, captures:
      - metadata from the <!-- note-meta:entry ... --> block
      - body text between the closing --> and the next entry's <!-- (or EOF)
      - a plain-text summary (first ~500 chars of body)
      - the relative source file path

    Returns list of dicts.
    """
    matches = list(ENTRY_RE.finditer(content))
    entries = []
    for i, match in enumerate(matches):
        block = match.group(1)
        kv = parse_kv_block(block)
        if not kv.get("id"):
            continue

        # --- Extract body content ---
        body_start = match.end()                        # right after "-->"
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        raw_body = content[body_start:body_end].strip()
        # Trim trailing "---" separator
        raw_body = re.sub(r"\n---\s*$", "", raw_body).strip()

        tags_raw = kv.get("tags", "")
        tags = parse_tags(tags_raw)
        entry = {
            "id":          kv.get("id", ""),
            "date":        kv.get("date", ""),
            "title":       kv.get("title", ""),
            "subject":     kv.get("subject", ""),
            "sub":         kv.get("sub", ""),
            "chapter":     kv.get("chapter", ""),
            "level":       kv.get("level", ""),
            "status":      kv.get("status", ""),
            "tags":        tags,
            "content":     raw_body,
            "summary":     extract_summary(raw_body),
            "source_file": source_file,
        }
        entries.append(entry)
    return entries

# ---------------------------------------------------------------------------
# Stats computation
# ---------------------------------------------------------------------------

def compute_stats(all_entries):
    """Build the stats, timeline, and sorted entries sections."""

    # -- Subject-level accumulators --
    subject_stats = {}
    # We'll dynamically discover subs per subject
    subject_subs = defaultdict(lambda: defaultdict(int))  # subject -> sub -> count

    for entry in all_entries:
        subj = entry["subject"]
        if subj not in subject_stats:
            subject_stats[subj] = {
                "total": 0,
                "organized": 0,
                "L1": 0, "L2": 0, "L3": 0,
            }
        st = subject_stats[subj]
        st["total"] += 1
        if entry["status"] == "已整理":
            st["organized"] += 1
        level = entry.get("level", "")
        if level in ("L1", "L2", "L3"):
            st[level] += 1
        sub = entry.get("sub", "")
        if sub:
            subject_subs[subj][sub] += 1

    # Build final stats dict with coverage + subs
    stats = {}
    for subj, st in subject_stats.items():
        total = st["total"]
        organized = st["organized"]
        coverage = round(organized / total * 100, 1) if total > 0 else 0.0
        entry_stats = {
            "total": total,
            "organized": organized,
            "coverage": coverage,
            "L1": st["L1"],
            "L2": st["L2"],
            "L3": st["L3"],
            "subs": dict(subject_subs[subj]),
        }
        stats[subj] = entry_stats

    # -- Timeline --
    timeline_raw = defaultdict(lambda: {"total": 0})
    subject_set = set()
    for entry in all_entries:
        date = entry.get("date", "")
        if not date:
            continue
        subj = entry["subject"]
        subject_set.add(subj)
        timeline_raw[date]["total"] += 1
        timeline_raw[date][subj] = timeline_raw[date].get(subj, 0) + 1

    timeline = {}
    for date in sorted(timeline_raw):
        row = {"total": timeline_raw[date]["total"]}
        for subj in sorted(subject_set):
            row[subj] = timeline_raw[date].get(subj, 0)
        timeline[date] = row

    # -- Sort entries by date --
    sorted_entries = sorted(all_entries, key=lambda e: (e.get("date", ""), e.get("id", "")))

    return stats, timeline, sorted_entries

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  build_index.py — 考研笔记索引构建器")
    print("=" * 60)
    print()

    all_entries = []
    file_stats = {}

    # --- Math subjects: read from notes_index.json (notes.md are now pure hubs) ---
    MATH_LABELS = {"高数", "线代", "概率"}
    math_json_loaded = False
    if os.path.exists(JSON_INDEX_PATH):
        import json as _json
        with open(JSON_INDEX_PATH, "r", encoding="utf-8") as jf:
            idx = _json.load(jf)
        for key, subj_data in idx.get("subjects", {}).items():
            sub = subj_data.get("sub", "")
            entries_raw = subj_data.get("entries", [])
            for e in entries_raw:
                all_entries.append({
                    "id": e.get("id", ""),
                    "date": e.get("date", ""),
                    "title": e.get("title", ""),
                    "subject": "数学",
                    "sub": sub,
                    "chapter": e.get("chapter", ""),
                    "level": e.get("level", ""),
                    "status": e.get("status", "已整理"),
                    "tags": e.get("tags", []) if isinstance(e.get("tags"), list) else [],
                    "content": "",
                    "summary": e.get("title", ""),
                    "source_file": f"Math/notes_index.json#{key}",
                })
            file_stats[key] = len(entries_raw)
            print(f"  [{key}] Loaded from notes_index.json -> {len(entries_raw)} entries")
        math_json_loaded = True

    # --- 408 subjects: read from 408/notes_index.json ---
    LABELS_408 = {"DS", "CO", "OS", "CN"}
    json408_loaded = False
    if os.path.exists(JSON_INDEX_408_PATH):
        import json as _json2
        with open(JSON_INDEX_408_PATH, "r", encoding="utf-8") as jf:
            idx = _json2.load(jf)
        for key, subj_data in idx.get("subjects", {}).items():
            sub = subj_data.get("sub", "")
            entries_raw = subj_data.get("entries", [])
            for e in entries_raw:
                all_entries.append({
                    "id": e.get("id", ""),
                    "date": e.get("date", ""),
                    "title": e.get("title", ""),
                    "subject": "408",
                    "sub": sub,
                    "chapter": e.get("chapter", ""),
                    "level": e.get("level", ""),
                    "status": e.get("status", "已整理"),
                    "tags": e.get("tags", []) if isinstance(e.get("tags"), list) else [],
                    "content": "",
                    "summary": e.get("title", ""),
                    "source_file": f"408/notes_index.json#{key}",
                })
            file_stats[key] = len(entries_raw)
            print(f"  [{key}] Loaded from 408/notes_index.json -> {len(entries_raw)} entries")
        json408_loaded = True

    # --- Politics subjects: read from Politics/notes_index.json ---
    LABELS_POL = {"马原", "史纲", "毛中特", "思修", "习思想"}
    jsonpol_loaded = False
    if os.path.exists(JSON_INDEX_POL_PATH):
        import json as _json3
        with open(JSON_INDEX_POL_PATH, "r", encoding="utf-8") as jf:
            idx = _json3.load(jf)
        for key, subj_data in idx.get("subjects", {}).items():
            sub = subj_data.get("sub", "")
            entries_raw = subj_data.get("entries", [])
            for e in entries_raw:
                all_entries.append({
                    "id": e.get("id", ""),
                    "date": e.get("date", ""),
                    "title": e.get("title", ""),
                    "subject": "政治",
                    "sub": sub,
                    "chapter": e.get("chapter", ""),
                    "level": e.get("level", ""),
                    "status": e.get("status", "已整理"),
                    "tags": e.get("tags", []) if isinstance(e.get("tags"), list) else [],
                    "content": "",
                    "summary": e.get("title", ""),
                    "source_file": f"Politics/notes_index.json#{key}",
                })
            file_stats[key] = len(entries_raw)
            print(f"  [{key}] Loaded from Politics/notes_index.json -> {len(entries_raw)} entries")
        jsonpol_loaded = True

    # --- 复试 subjects: read from Re-examination/notes_index.json ---
    LABELS_RE = {"复试"}
    jsonre_loaded = False
    if os.path.exists(JSON_INDEX_RE_PATH):
        import json as _json4
        with open(JSON_INDEX_RE_PATH, "r", encoding="utf-8") as jf:
            idx = _json4.load(jf)
        for key, subj_data in idx.get("subjects", {}).items():
            sub = subj_data.get("sub", "")
            entries_raw = subj_data.get("entries", [])
            for e in entries_raw:
                all_entries.append({
                    "id": e.get("id", ""),
                    "date": e.get("date", ""),
                    "title": e.get("title", ""),
                    "subject": "复试",
                    "sub": sub,
                    "chapter": e.get("chapter", ""),
                    "level": e.get("level", ""),
                    "status": e.get("status", "已整理"),
                    "tags": e.get("tags", []) if isinstance(e.get("tags"), list) else [],
                    "content": "",
                    "summary": e.get("title", ""),
                    "source_file": f"Re-examination/notes_index.json#{key}",
                })
            file_stats[key] = len(entries_raw)
            print(f"  [{key}] Loaded from Re-examination/notes_index.json -> {len(entries_raw)} entries")
        jsonre_loaded = True

    # --- English subjects: read from English/notes_index.json ---
    # 英语此前完全不在索引里，导致大盘英语 15 格恒为 0%（2026-09-14 接入）。
    LABELS_ENG = {"VOC", "GRAM", "READ", "WRITE", "TRN"}
    jsoneng_loaded = False
    if os.path.exists(JSON_INDEX_ENG_PATH):
        import json as _json5
        with open(JSON_INDEX_ENG_PATH, "r", encoding="utf-8") as jf:
            idx = _json5.load(jf)
        for key, subj_data in idx.get("subjects", {}).items():
            sub = subj_data.get("sub", "")
            entries_raw = subj_data.get("entries", [])
            for e in entries_raw:
                all_entries.append({
                    "id": e.get("id", ""),
                    "date": e.get("date", ""),
                    "title": e.get("title", ""),
                    "subject": "英语",
                    "sub": sub,
                    "chapter": e.get("chapter", ""),
                    "level": e.get("level", ""),
                    "status": e.get("status", "已整理"),
                    "tags": e.get("tags", []) if isinstance(e.get("tags"), list) else [],
                    "content": "",
                    "summary": e.get("title", ""),
                    "source_file": f"English/notes_index.json#{key}",
                })
            file_stats[key] = len(entries_raw)
            print(f"  [{key}] Loaded from English/notes_index.json -> {len(entries_raw)} entries")
        jsoneng_loaded = True

    # --- Other subjects: parse .md draft files as before ---
    missing = []
    for label, path in DRAFT_FILES.items():
        if math_json_loaded and label in MATH_LABELS:
            continue  # already loaded from JSON
        if json408_loaded and label in LABELS_408:
            continue
        if jsonpol_loaded and label in LABELS_POL:
            continue
        if jsonre_loaded and label in LABELS_RE:
            continue
        if jsoneng_loaded and label in LABELS_ENG:
            continue
        print(f"  [{label}] Reading: {path}")
        content = read_file(path)
        if content is None:
            file_stats[label] = 0
            missing.append((label, path))
            continue
        # Relative path from the 考研 base directory
        rel_path = os.path.relpath(path, _BASE)
        entries = parse_entries(content, source_file=rel_path)
        n = len(entries)
        file_stats[label] = n
        print(f"         -> {n} entries parsed")
        all_entries.extend(entries)

    print()
    print(f"  Total entries parsed: {len(all_entries)}")
    if missing:
        print()
        print("  ⚠ 警告：以下草稿箱缺失，其条目不会被索引（首次使用可忽略，否则请检查 DRAFT_FILES 路径是否腐化）：")
        for label, path in missing:
            print(f"    - [{label}] {path}")
    print()

    # Compute sections
    stats, timeline, entries = compute_stats(all_entries)

    # Build output dict
    output = {
        "version": "1.0",
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stats": stats,
        "timeline": timeline,
        "entries": entries,
    }

    # Write YAML
    write_yaml(output, OUTPUT_PATH)
    print(f"  Output written to: {OUTPUT_PATH}")
    print()

    # Print summary
    print("-" * 60)
    print("  Summary")
    print("-" * 60)
    for subj, st in stats.items():
        print(f"  {subj}:")
        print(f"    total:     {st['total']}")
        print(f"    organized: {st['organized']}  ({st['coverage']}%)")
        print(f"    levels:    L1={st['L1']}  L2={st['L2']}  L3={st['L3']}")
        for sub_name, cnt in st["subs"].items():
            print(f"      {sub_name}: {cnt}")
    print()
    print(f"  Timeline spans {len(timeline)} dates")
    if timeline:
        dates = sorted(timeline.keys())
        print(f"    first: {dates[0]}   last: {dates[-1]}")
    print()
    print("  Per-file breakdown:")
    for label, cnt in file_stats.items():
        status = "OK" if cnt > 0 else "EMPTY/MISSING"
        print(f"    {label:4s}: {cnt:4d} entries  [{status}]")
    print()
    print("  Done.")


if __name__ == "__main__":
    main()

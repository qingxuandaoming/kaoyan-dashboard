#!/usr/bin/env python3
"""
rebuild_bitable.py
==================
Create a NEW "章节文件" table in the Feishu Bitable, with columns:
  科目 | 子科目 | 章节 | 文件名 | 飞书链接 | 条目数 | 图片数 | 本地路径
Populate it from local .md chapter files + feishu_docs_state.json + 笔记索引.yaml.
"""

import sys
import os
import re
import json
import time
import subprocess
from pathlib import Path
from collections import defaultdict

# ── Windows UTF-8 ──────────────────────────────────────────────────────
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# ── Constants ──────────────────────────────────────────────────────────
KAOYAN_DIR = Path(r"E:\NPEE")
BASE_TOKEN = "IK92bxLZoa0pFysqYNzcwjANnLW"
STATE_FILE = KAOYAN_DIR / "feishu_docs_state.json"
INDEX_FILE = KAOYAN_DIR / "笔记索引.yaml"

# lark-cli invocation
LARK_CLI = [
    "node",
    os.path.expandvars(r"%APPDATA%\npm\node_modules\@larksuite\cli\scripts\run.js"),
]

# Directory-abbreviation → subject mapping
PATH_TO_FOLDER = {
    r"408\OS": "OS",
    r"408\DS": "DS",
    r"408\CO": "CO",
    r"408\CN": "CN",
    r"Math\高数": "高数",
    r"Math\线代": "线代",
    r"Math\概率论\概率": "概率论",
    r"Politics\史纲": "史纲",
    r"Politics\毛中特": "毛中特",
    r"Politics\马原": "马原",
}

# Chinese sub names (from YAML) → (subject, dir-abbreviation)
# Used to count YAML entries per chapter and map to directory-based 子科目
CHINESE_SUB_TO_ABBR = {
    # 408 entries: subject is "408"
    ("408", "操作系统"): ("408", "OS"),
    ("408", "数据结构"): ("408", "DS"),
    ("408", "计算机组成原理"): ("408", "CO"),
    ("408", "计算机网络"): ("408", "CN"),
    # Math entries: YAML subject is "数学" (Chinese)
    ("Math", "高等数学"): ("Math", "高数"),
    ("Math", "线性代数"): ("Math", "线代"),
    ("Math", "概率论与数理统计"): ("Math", "概率论"),
    ("Math", "概率论"): ("Math", "概率论"),
    ("数学", "高等数学"): ("Math", "高数"),
    ("数学", "线性代数"): ("Math", "线代"),
    ("数学", "概率论与数理统计"): ("Math", "概率论"),
    ("数学", "概率论"): ("Math", "概率论"),
    # Politics entries: YAML subject is "政治" (Chinese)
    ("Politics", "史纲"): ("Politics", "史纲"),
    ("Politics", "毛中特"): ("Politics", "毛中特"),
    ("Politics", "毛泽东思想和中国特色社会主义理论体系概论"): ("Politics", "毛中特"),
    ("Politics", "马克思主义基本原理"): ("Politics", "马原"),
    ("Politics", "马原"): ("Politics", "马原"),
    ("Politics", "思想道德修养与法律基础"): ("Politics", "思修"),
    ("Politics", "习思想"): ("Politics", "习思想"),
    ("政治", "史纲"): ("Politics", "史纲"),
    ("政治", "毛中特"): ("Politics", "毛中特"),
    ("政治", "毛泽东思想和中国特色社会主义理论体系概论"): ("Politics", "毛中特"),
    ("政治", "马克思主义基本原理"): ("Politics", "马原"),
    ("政治", "马原"): ("Politics", "马原"),
    ("政治", "思想道德修养与法律基础"): ("Politics", "思修"),
    ("政治", "习思想"): ("Politics", "习思想"),
}

# Chapter filename pattern: 第N章_xxx.md
CHAPTER_RE = re.compile(r"^第\d+章[_\-].+\.md$")
# Image reference pattern in markdown
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

# Field definitions for the new table (order matters for positional arrays)
FIELDS = [
    # (name, type, style_type_or_None)
    ("科目", "text", None),
    ("子科目", "text", None),
    ("章节", "text", None),
    ("文件名", "text", None),
    ("飞书链接", "text", "url"),
    ("条目数", "number", None),
    ("图片数", "number", None),
    ("本地路径", "text", None),
]

FIELD_NAMES = [f[0] for f in FIELDS]

# Rate-limit pause between API calls (seconds)
API_SLEEP = 1.5


# ── Helpers ────────────────────────────────────────────────────────────

def run_lark_cli(args: list[str], desc: str = "") -> dict:
    """Run a lark-cli command and return parsed JSON output."""
    cmd = LARK_CLI + args
    print(f"  [lark-cli] {desc or ' '.join(args[:6])}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(KAOYAN_DIR),
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"  [ERROR] returncode={result.returncode}")
        print(f"  [stderr] {result.stderr[:500]}")
        # Try to parse anyway
    stdout = result.stdout.strip()
    if not stdout:
        print(f"  [WARN] empty stdout")
        return {}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        print(f"  [WARN] non-JSON stdout: {stdout[:300]}")
        return {"_raw": stdout}


def load_state() -> dict:
    """Load feishu_docs_state.json → docs dict."""
    print(f"[1/5] Loading state file: {STATE_FILE.name}")
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    docs = data.get("docs", {})
    print(f"  → {len(docs)} doc entries loaded")
    return docs


def load_yaml_index() -> dict:
    """
    Load 笔记索引.yaml and count entries per (subject, abbr_sub, chapter).
    Returns dict: (subject, abbr_sub, chapter) → count
    """
    print(f"[2/5] Loading index file: {INDEX_FILE.name}")
    try:
        import yaml
    except ImportError:
        print("  [ERROR] PyYAML not installed. Install with: pip install pyyaml")
        sys.exit(1)

    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    entries = data.get("entries", [])
    print(f"  → {len(entries)} entries found")

    counts = defaultdict(int)
    unmapped_subs = set()

    for entry in entries:
        subject = str(entry.get("subject", ""))
        sub_chinese = str(entry.get("sub", ""))
        chapter = str(entry.get("chapter", ""))

        key = (subject, sub_chinese)
        if key in CHINESE_SUB_TO_ABBR:
            subj, abbr = CHINESE_SUB_TO_ABBR[key]
            counts[(subj, abbr, chapter)] += 1
        else:
            unmapped_subs.add((subject, sub_chinese))

    if unmapped_subs:
        print(f"  [WARN] Unmapped sub names (entries not counted):")
        for s, sub in sorted(unmapped_subs):
            print(f"    subject={s}, sub={sub}")

    print(f"  → {len(counts)} unique (subject, sub, chapter) groups")
    return dict(counts)


def find_chapter_files() -> list[dict]:
    """
    Scan KAOYAN_DIR for chapter .md files matching the pattern.
    Returns list of dicts with extracted metadata.
    """
    print(f"[3/5] Scanning for chapter .md files...")
    results = []

    # Walk through subject directories
    for subject_dir in ["408", "Math", "Politics"]:
        subject_path = KAOYAN_DIR / subject_dir
        if not subject_path.is_dir():
            continue

        for root, dirs, files in os.walk(subject_path):
            # Skip non-content directories
            rel_root = os.path.relpath(root, KAOYAN_DIR)
            parts = Path(rel_root).parts

            # We need at least 2 levels: subject/sub
            if len(parts) < 2:
                continue

            for filename in sorted(files):
                if not CHAPTER_RE.match(filename):
                    continue

                filepath = Path(root) / filename

                # Determine 子科目 from path
                # Try matching against PATH_TO_FOLDER keys
                rel_to_kaoyan = os.path.relpath(filepath, KAOYAN_DIR)
                sub_abbr = None
                for path_key, folder_name in PATH_TO_FOLDER.items():
                    # Check if the file's relative path starts with this key
                    if rel_to_kaoyan.startswith(path_key + os.sep) or rel_to_kaoyan.startswith(path_key + "/"):
                        sub_abbr = folder_name
                        break

                if sub_abbr is None:
                    # Fallback: use the second path component
                    sub_abbr = parts[1] if len(parts) >= 2 else "unknown"
                    print(f"  [WARN] Could not map sub for {rel_to_kaoyan}, using '{sub_abbr}'")

                # Extract 章节 from filename (e.g., "第3章_存储器层次结构.md" → "第3章")
                chapter_match = re.match(r"^(第\d+章)", filename)
                chapter = chapter_match.group(1) if chapter_match else ""

                # 文件名 without .md
                file_stem = filename[:-3]  # remove .md

                # Count images in the file
                try:
                    content = filepath.read_text(encoding="utf-8")
                    image_count = len(IMAGE_RE.findall(content))
                except Exception as e:
                    print(f"  [WARN] Cannot read {filepath}: {e}")
                    image_count = 0

                results.append({
                    "subject": subject_dir,
                    "sub": sub_abbr,
                    "chapter": chapter,
                    "filename": file_stem,
                    "rel_path": rel_to_kaoyan.replace("/", "\\"),
                    "image_count": image_count,
                })

    print(f"  → {len(results)} chapter files found")
    return results


def enrich_with_metadata(chapter_files: list[dict], docs: dict, entry_counts: dict) -> list[dict]:
    """Add URL and entry count to each chapter file record."""
    for rec in chapter_files:
        rel_path = rec["rel_path"]

        # Look up URL from state file
        doc_info = docs.get(rel_path, {})
        rec["url"] = doc_info.get("url", "")

        # Look up entry count
        key = (rec["subject"], rec["sub"], rec["chapter"])
        rec["entry_count"] = entry_counts.get(key, 0)

    return chapter_files


def create_table() -> str:
    """Create the new table '章节文件' with the first field."""
    print(f"[4/5] Creating table '章节文件'...")

    # Build --fields JSON: first field renames default column, rest are created
    fields_json = []
    for name, ftype, style_type in FIELDS:
        field_def = {"name": name, "type": ftype}
        if style_type:
            field_def["style"] = {"type": style_type}
        fields_json.append(field_def)

    result = run_lark_cli(
        [
            "base", "+table-create",
            "--base-token", BASE_TOKEN,
            "--name", "章节文件",
            "--fields", json.dumps(fields_json, ensure_ascii=False),
            "--as", "bot",
        ],
        desc="Creating table '章节文件' with all fields",
    )

    table_id = None
    if "table" in result:
        table_id = result["table"].get("table_id") or result["table"].get("id")
    if not table_id and "table_id" in result:
        table_id = result["table_id"]
    if not table_id:
        # Try nested structures
        for key in ["data", "result"]:
            if key in result and isinstance(result[key], dict):
                t = result[key].get("table", {})
                if t:
                    table_id = t.get("table_id") or t.get("id")
                    break

    if not table_id:
        print(f"  [FATAL] Could not extract table_id from response:")
        print(f"  {json.dumps(result, ensure_ascii=False, indent=2)[:800]}")
        sys.exit(1)

    print(f"  → Table created: {table_id}")
    time.sleep(API_SLEEP)
    return table_id


def batch_insert_records(table_id: str, records: list[dict]):
    """Insert all records using batch-create (max 200 per call)."""
    print(f"[5/5] Inserting {len(records)} records...")

    # Build rows as positional arrays
    # Field order: 科目, 子科目, 章节, 文件名, 飞书链接, 条目数, 图片数, 本地路径
    all_rows = []
    for rec in records:
        row = [
            rec["subject"],
            rec["sub"],
            rec["chapter"],
            rec["filename"],
            rec["url"] or None,
            rec["entry_count"],
            rec["image_count"],
            rec["rel_path"],
        ]
        all_rows.append(row)

    # Split into batches of 200
    batch_size = 200
    total_batches = (len(all_rows) + batch_size - 1) // batch_size

    for batch_idx in range(total_batches):
        start = batch_idx * batch_size
        end = min(start + batch_size, len(all_rows))
        batch_rows = all_rows[start:end]

        payload = {
            "fields": FIELD_NAMES,
            "rows": batch_rows,
        }

        # Write payload to temp file (relative path, cwd=KAOYAN_DIR)
        payload_rel = "_bitable_payload.json"
        payload_path = KAOYAN_DIR / payload_rel
        with open(payload_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        print(f"  Batch {batch_idx + 1}/{total_batches}: rows {start+1}–{end} ({len(batch_rows)} rows)")

        result = run_lark_cli(
            [
                "base", "+record-batch-create",
                "--base-token", BASE_TOKEN,
                "--table-id", table_id,
                "--json", f"@{payload_rel}",
                "--as", "bot",
            ],
            desc=f"Batch insert {batch_idx + 1}/{total_batches}",
        )

        # Check for errors
        if "error" in result or "code" in result:
            code = result.get("code", "")
            msg = result.get("msg", result.get("error", ""))
            if code and code != 0:
                print(f"  [ERROR] API error {code}: {msg}")

        # Clean up temp file
        try:
            payload_path.unlink()
        except Exception:
            pass

        if batch_idx < total_batches - 1:
            time.sleep(API_SLEEP)

    print(f"  → Done inserting records.")


def deduplicate_chapter_files(records: list[dict]) -> list[dict]:
    """
    Remove duplicate entries for the same (subject, sub, chapter).
    Prefer the record with a URL; if both have URLs, prefer shallower path.
    """
    seen = {}  # (subject, sub, chapter) → best record
    for rec in records:
        key = (rec["subject"], rec["sub"], rec["chapter"])
        if key not in seen:
            seen[key] = rec
        else:
            existing = seen[key]
            # Prefer the one with a URL
            has_url = bool(rec.get("url"))
            existing_url = bool(existing.get("url"))
            if has_url and not existing_url:
                seen[key] = rec
                print(f"  [DEDUP] Replaced {existing['rel_path']} with {rec['rel_path']} (has URL)")
            elif has_url == existing_url:
                # Both have or both lack URL: prefer shallower path
                rec_depth = rec["rel_path"].count("\\")
                exist_depth = existing["rel_path"].count("\\")
                if rec_depth < exist_depth:
                    seen[key] = rec
                    print(f"  [DEDUP] Replaced {existing['rel_path']} with {rec['rel_path']} (shallower)")
                else:
                    print(f"  [DEDUP] Kept {existing['rel_path']}, skipped {rec['rel_path']}")
            else:
                print(f"  [DEDUP] Kept {existing['rel_path']} (has URL), skipped {rec['rel_path']}")

    result = list(seen.values())
    if len(result) < len(records):
        print(f"  → Deduplicated: {len(records)} → {len(result)} records")
    return result


# ── Sorting ────────────────────────────────────────────────────────────

def sort_key(rec: dict) -> tuple:
    """Sort by subject, sub, chapter number."""
    # Extract chapter number for numeric sorting
    ch_match = re.search(r"第(\d+)章", rec["chapter"])
    ch_num = int(ch_match.group(1)) if ch_match else 999

    # Subject order
    subj_order = {"408": 0, "Math": 1, "Politics": 2}
    return (
        subj_order.get(rec["subject"], 99),
        rec["sub"],
        ch_num,
    )


# ── Main ───────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  rebuild_bitable.py — Create '章节文件' table")
    print("=" * 60)
    print()

    # Step 1: Load doc URLs
    docs = load_state()
    print()

    # Step 2: Load YAML index and count entries
    entry_counts = load_yaml_index()
    print()

    # Step 3: Find chapter files and extract metadata
    chapter_files = find_chapter_files()
    print()

    if not chapter_files:
        print("[FATAL] No chapter files found. Check directory structure.")
        sys.exit(1)

    # Enrich with URL and entry count
    chapter_files = enrich_with_metadata(chapter_files, docs, entry_counts)

    # Deduplicate (e.g., same chapter in different subdirectories)
    chapter_files = deduplicate_chapter_files(chapter_files)

    # Sort records
    chapter_files.sort(key=sort_key)

    # Print summary
    print("Summary of records to insert:")
    print(f"  {'科目':<10} {'子科目':<8} {'章节':<8} {'文件名':<30} {'条目':>4} {'图片':>4}")
    print(f"  {'─'*10} {'─'*8} {'─'*8} {'─'*30} {'─'*4} {'─'*4}")
    for rec in chapter_files:
        print(
            f"  {rec['subject']:<10} {rec['sub']:<8} {rec['chapter']:<8} "
            f"{rec['filename'][:30]:<30} {rec['entry_count']:>4} {rec['image_count']:>4}"
        )
    print()

    # Confirm
    print(f"Total: {len(chapter_files)} records to insert into new table '章节文件'")
    print(f"Base token: {BASE_TOKEN}")
    print()

    # Step 4: Create table
    table_id = create_table()
    print()

    # Step 5: Insert records
    batch_insert_records(table_id, chapter_files)
    print()

    print("=" * 60)
    print(f"  DONE! New table '章节文件' created: {table_id}")
    print(f"  Base URL: https://gcnt42jg7rko.feishu.cn/base/{BASE_TOKEN}")
    print(f"  Old table (not deleted): tblDLcYfCHmeLLMV")
    print("=" * 60)


if __name__ == "__main__":
    main()

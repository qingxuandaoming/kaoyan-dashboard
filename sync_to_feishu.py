#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sync_to_feishu.py — CDC (Change Data Capture) Incremental Sync

Syncs local note entries from 笔记索引.yaml to Feishu Bitable via lark-cli.
Uses a sync_state.json watermark file to track what has already been synced,
so only new or changed entries are pushed on each run.

Usage:
    python sync_to_feishu.py                    # Incremental sync
    python sync_to_feishu.py --full             # Full sync (all records)
    python sync_to_feishu.py --dry-run          # Show what would change
    python sync_to_feishu.py --subject 408      # Sync only one subject
"""

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
from datetime import datetime

# Force UTF-8 on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_KAOYAN = r"E:\NPEE"
INDEX_PATH  = os.path.join(_KAOYAN, "笔记索引.yaml")
STATE_PATH  = os.path.join(_KAOYAN, "sync_state.json")
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))

BITABLE_BASE_TOKEN = "IK92bxLZoa0pFysqYNzcwjANnLW"

# Table IDs
TABLE_NOTE   = "tblDLcYfCHmeLLMV"   # 笔记条目 (math + 408)
TABLE_ENGLISH = "tblUos6NIzNtrp3L"  # 英语学习笔记

BATCH_MAX = 200  # Feishu batch-create limit


def get_table_for_subject(subject: str) -> str:
    """Return the Bitable table ID for a given subject."""
    if subject == "英语":
        return TABLE_ENGLISH
    return TABLE_NOTE


def entry_to_fields(entry: dict) -> dict:
    """Map a note entry dict to Feishu Bitable field dict."""
    tags = entry.get("tags", [])
    if isinstance(tags, list):
        tags_str = ", ".join(str(t) for t in tags)
    else:
        tags_str = str(tags)

    raw_date = str(entry.get("date", ""))
    # Convert yyyy-MM-dd to yyyy/MM/dd for Bitable datetime field
    bitable_date = raw_date.replace("-", "/") if raw_date else ""

    fields = {
        "ID":       str(entry.get("id", "")),
        "日期":     bitable_date,
        "标题":     str(entry.get("title", "")),
        "科目":     str(entry.get("subject", "")),
        "子科目":   str(entry.get("sub", "")),
        "章节":     str(entry.get("chapter", "")),
        "级别":     str(entry.get("level", "")),
        "状态":     str(entry.get("status", "")),
        "标签":     tags_str,
        "内容":     str(entry.get("content", "")),
        "摘要":     str(entry.get("summary", "")),
        "来源路径": str(entry.get("source_file", "")),
        "飞书链接": str(entry.get("feishu_link", "")),
    }
    return fields


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Load or initialize the sync state."""
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    # Fresh state
    return {
        "last_incremental": None,
        "last_full_sync": None,
        "entries": {},
    }


def save_state(state: dict) -> None:
    """Persist the sync state to disk."""
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def compute_hash(entry: dict) -> str:
    """Compute a 16-char SHA-256 hash for an entry."""
    return hashlib.sha256(
        json.dumps(entry, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# CDC detection
# ---------------------------------------------------------------------------

def detect_changes(index: dict, state: dict, subject_filter: str = None,
                   full: bool = False) -> dict:
    """
    Compare index entries against sync state.

    Returns:
        {'create': [...], 'update': [...], 'unchanged': int}
    """
    changes = {"create": [], "update": [], "unchanged": 0}

    for entry in index.get("entries", []):
        # Apply subject filter
        if subject_filter and entry.get("subject") != subject_filter:
            continue

        entry_id = entry["id"]
        entry_hash = compute_hash(entry)

        if entry_id not in state["entries"]:
            # New entry
            changes["create"].append(entry)
            state["entries"][entry_id] = {
                "hash": entry_hash,
                "bitable_record_id": None,
                "table": get_table_for_subject(entry.get("subject", "")),
                "synced_at": None,
                "action": "pending_create",
            }
        elif full or state["entries"][entry_id].get("hash") != entry_hash:
            # Changed or full-sync forced
            changes["update"].append(entry)
            state["entries"][entry_id]["hash"] = entry_hash
            state["entries"][entry_id]["action"] = "pending_update"
        else:
            changes["unchanged"] += 1

    return changes


# ---------------------------------------------------------------------------
# lark-cli wrappers
# ---------------------------------------------------------------------------

import shutil

_LARK_CMD = None

def _find_lark_cli() -> list:
    """
    Find lark-cli and return [node_exe, run_js] command prefix.
    Bypasses the .cmd shim to avoid cmd.exe argument mangling of JSON payloads.
    """
    global _LARK_CMD
    if _LARK_CMD:
        return _LARK_CMD

    # Strategy 1: Find node + npm-installed run.js directly
    node_exe = shutil.which("node")
    npm_run_js = os.path.expandvars(
        r"%APPDATA%\npm\node_modules\@larksuite\cli\scripts\run.js"
    )
    if node_exe and os.path.exists(npm_run_js):
        _LARK_CMD = [node_exe, npm_run_js]
        return _LARK_CMD

    # Strategy 2: Fallback to lark-cli command (may have arg mangling on Windows)
    _LARK_CMD = ["lark-cli"]
    return _LARK_CMD


def _lark_available() -> bool:
    """Check if lark-cli is installed and accessible."""
    try:
        cmd = _find_lark_cli()
        subprocess.run(
            cmd + ["--version"],
            capture_output=True, timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def batch_create(entries: list, table_id: str, dry_run: bool = False) -> list:
    """
    Batch-create records to a Bitable table.

    Returns list of record IDs (empty strings in dry-run mode).
    """
    if not entries:
        return []

    # Field order must match Bitable schema
    FIELD_NAMES = ["ID", "日期", "标题", "科目", "子科目", "章节", "级别", "状态", "标签",
                   "内容", "摘要", "来源路径", "飞书链接"]

    record_ids = []
    # Split into batches of BATCH_MAX
    for i in range(0, len(entries), BATCH_MAX):
        batch = entries[i:i + BATCH_MAX]
        rows = []
        for e in batch:
            fields = entry_to_fields(e)
            rows.append([fields.get(fn) for fn in FIELD_NAMES])

        payload = json.dumps({
            "fields": FIELD_NAMES,
            "rows": rows,
        }, ensure_ascii=False)

        if dry_run:
            print(f"  [DRY-RUN] Would batch-create {len(batch)} records to {table_id}")
            record_ids.extend([""] * len(batch))
            continue

        # Write payload to a JSON file in the project directory
        json_file = os.path.join(_KAOYAN, "_sync_payload.json")
        with open(json_file, "w", encoding="utf-8") as _f:
            _f.write(payload)

        try:
            lark = _find_lark_cli()
            result = subprocess.run(
                lark + ["base", "+record-batch-create",
                 "--base-token", BITABLE_BASE_TOKEN,
                 "--table-id", table_id,
                 "--json", "@_sync_payload.json"],
                capture_output=True, timeout=120,
                cwd=_KAOYAN,
            )
            stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
            stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            if result.returncode == 0:
                try:
                    resp = json.loads(stdout)
                    rid_list = resp.get("data", {}).get("record_id_list", [])
                    added = len(record_ids)
                    record_ids.extend(rid_list)
                    # Pad if fewer IDs than entries in this batch
                    while len(record_ids) - added < len(batch):
                        record_ids.append("")
                except (json.JSONDecodeError, KeyError):
                    record_ids.extend([""] * len(batch))
                print(f"  Batch-created {len(batch)} records to {table_id}")
            else:
                print(f"  ERROR batch-create: {stderr.strip()}")
                record_ids.extend([""] * len(batch))
        except Exception as e:
            print(f"  ERROR batch-create: {e}")
            record_ids.extend([""] * len(batch))
        finally:
            try:
                os.unlink(json_file)
            except OSError:
                pass

    return record_ids


def record_upsert(entry: dict, record_id: str, table_id: str,
                  dry_run: bool = False) -> str:
    """
    Upsert a single record. Returns the record_id.
    Uses a JSON file in the project dir to avoid cmd-line length limits.
    """
    fields = entry_to_fields(entry)
    payload = json.dumps(fields, ensure_ascii=False)

    if dry_run:
        action = "update" if record_id else "create"
        print(f"  [DRY-RUN] Would {action} record {entry['id']} in {table_id}")
        return record_id or ""

    # Write payload to a JSON file in the project directory
    json_file = os.path.join(_KAOYAN, "_sync_payload.json")
    with open(json_file, "w", encoding="utf-8") as _f:
        _f.write(payload)

    try:
        lark = _find_lark_cli()
        upsert_cmd = lark + ["base", "+record-upsert",
                      "--base-token", BITABLE_BASE_TOKEN,
                      "--table-id", table_id,
                      "--json", "@_sync_payload.json"]
        if record_id:
            upsert_cmd.extend(["--record-id", record_id])

        result = subprocess.run(
            upsert_cmd, capture_output=True, timeout=30,
            cwd=_KAOYAN,
        )
        stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        if result.returncode == 0:
            try:
                resp = json.loads(stdout)
                d = resp.get("data", {})
                rid = (d.get("record", {}).get("record_id")
                       or d.get("record_id")
                       or record_id)
                return rid
            except (json.JSONDecodeError, KeyError):
                return record_id
        else:
            print(f"  ERROR upsert {entry['id']}: {stderr.strip()}")
            return record_id
    except Exception as e:
        print(f"  ERROR upsert {entry['id']}: {e}")
        return record_id
    finally:
        try:
            os.unlink(json_file)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Main sync logic
# ---------------------------------------------------------------------------

def run_sync(full: bool = False, dry_run: bool = False,
             subject_filter: str = None) -> None:
    """Execute the sync pipeline."""

    # 1. Load data
    print(f"Loading index from {INDEX_PATH}...")
    if not os.path.exists(INDEX_PATH):
        print(f"ERROR: Index file not found: {INDEX_PATH}")
        sys.exit(1)

    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        index = yaml.safe_load(f)

    state = load_state()

    total_entries = len(index.get("entries", []))
    print(f"Index loaded: {total_entries} entries")

    if subject_filter:
        subject_count = sum(
            1 for e in index.get("entries", [])
            if e.get("subject") == subject_filter
        )
        print(f"Subject filter: {subject_filter} ({subject_count} entries)")

    # 2. Detect changes
    print("\nDetecting changes...")
    changes = detect_changes(index, state, subject_filter, full)

    n_create = len(changes["create"])
    n_update = len(changes["update"])
    n_unchanged = changes["unchanged"]

    print(f"  New entries:     {n_create}")
    print(f"  Updated entries: {n_update}")
    print(f"  Unchanged:       {n_unchanged}")

    if n_create == 0 and n_update == 0:
        print("\nNothing to sync. All entries are up to date.")
        state["last_incremental"] = datetime.now().isoformat(timespec="seconds")
        if not dry_run:
            save_state(state)
        return

    # 3. Check lark-cli availability
    lark_ok = _lark_available()
    if not lark_ok and not dry_run:
        print("\nWARNING: lark-cli not found. Switching to dry-run mode.")
        print("Install lark-cli to enable actual syncing.")
        dry_run = True

    now = datetime.now().isoformat(timespec="seconds")

    # 4. Process creates — batch by table
    if n_create > 0:
        print(f"\n--- Creating {n_create} new records ---")

        # Group by table
        by_table = {}
        for entry in changes["create"]:
            tbl = get_table_for_subject(entry.get("subject", ""))
            by_table.setdefault(tbl, []).append(entry)

        for table_id, entries in by_table.items():
            print(f"\nTable {table_id}: {len(entries)} records")
            record_ids = batch_create(entries, table_id, dry_run)

            # Update state with record IDs
            for entry, rid in zip(entries, record_ids):
                eid = entry["id"]
                state["entries"][eid]["bitable_record_id"] = rid or None
                state["entries"][eid]["synced_at"] = now
                state["entries"][eid].pop("action", None)

    # 5. Process updates — one by one
    if n_update > 0:
        print(f"\n--- Updating {n_update} changed records ---")

        for entry in changes["update"]:
            eid = entry["id"]
            st = state["entries"].get(eid, {})
            rid = st.get("bitable_record_id")
            tbl = get_table_for_subject(entry.get("subject", ""))

            new_rid = record_upsert(entry, rid, tbl, dry_run)
            state["entries"][eid]["bitable_record_id"] = new_rid or rid
            state["entries"][eid]["synced_at"] = now
            state["entries"][eid].pop("action", None)

    # 6. Update watermarks
    state["last_incremental"] = now
    if full:
        state["last_full_sync"] = now

    # 7. Save state
    if not dry_run:
        save_state(state)
        print(f"\nState saved to {STATE_PATH}")
    else:
        print("\n[DRY-RUN] State not saved.")

    # 8. Summary
    print("\n" + "=" * 50)
    print("SYNC SUMMARY")
    print("=" * 50)
    print(f"  Created:   {n_create}")
    print(f"  Updated:   {n_update}")
    print(f"  Unchanged: {n_unchanged}")
    print(f"  Mode:      {'DRY-RUN' if dry_run else 'LIVE'}")
    if full:
        print(f"  Type:      Full sync")
    else:
        print(f"  Type:      Incremental")
    if subject_filter:
        print(f"  Filter:    {subject_filter}")
    print("=" * 50)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="CDC Incremental Sync: 笔记索引 → Feishu Bitable",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Full sync — re-push all records regardless of hash",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without actually syncing",
    )
    parser.add_argument(
        "--subject", type=str, default=None,
        help="Sync only entries for this subject (e.g. 408, 数学, 英语, 政治)",
    )

    args = parser.parse_args()
    run_sync(full=args.full, dry_run=args.dry_run, subject_filter=args.subject)


if __name__ == "__main__":
    main()

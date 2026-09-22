#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Link Bitable records to their chapter doc URLs in Feishu.

Reads doc_mapping.json (chapter file -> Feishu doc URL),
matches each Bitable record's (子科目, 章节) to the right doc,
and batch-updates the 飞书链接 field.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import io
import yaml

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

KAOYAN = r"E:\NPEE"
BITABLE_BASE_TOKEN = "IK92bxLZoa0pFysqYNzcwjANnLW"
TABLE_NOTE = "tblDLcYfCHmeLLMV"

node_exe = shutil.which("node")
run_js = os.path.expandvars(r"%APPDATA%\npm\node_modules\@larksuite\cli\scripts\run.js")
LARK = [node_exe, run_js] if node_exe and os.path.exists(run_js) else ["lark-cli"]

# Map directory names to the 子科目 values used in the index
DIR_TO_SUB = {
    "OS": "操作系统",
    "DS": "数据结构",
    "CO": "计算机组成原理",
    "CN": "计算机网络",
    "高数": "高等数学",
    "线代": "线性代数",
    "概率": "概率论",
    "史纲": "史纲",
    "马原": "马原",
    "毛中特": "毛中特",
    "思修": "思修",
    "习思想": "习思想",
}


def build_url_lookup(doc_mapping: dict) -> dict:
    """Build lookup: (子科目, 第X章) -> doc_url from the mapping."""
    lookup = {}
    chapter_re = re.compile(r"(第\d+章)")

    for rel_path, info in doc_mapping.items():
        if not info.get("ok"):
            continue
        url = info.get("url", "")
        if not url:
            continue

        # Parse directory and chapter from path
        parts = rel_path.replace("/", "\\").split("\\")
        dir_name = parts[-2] if len(parts) >= 2 else ""
        sub = DIR_TO_SUB.get(dir_name, dir_name)
        fname = os.path.splitext(parts[-1])[0]
        m = chapter_re.search(fname)
        if m:
            chapter = m.group(1)  # e.g. "第4章"
            lookup[(sub, chapter)] = url

    return lookup


def update_record_link(record_id: str, url: str, dry_run=False) -> bool:
    """Update the 飞书链接 field for a single record."""
    if dry_run:
        return True

    payload = json.dumps({"飞书链接": url}, ensure_ascii=False)
    json_file = os.path.join(KAOYAN, "_link_payload.json")
    with open(json_file, "w", encoding="utf-8") as f:
        f.write(payload)

    try:
        cmd = LARK + [
            "base", "+record-upsert",
            "--base-token", BITABLE_BASE_TOKEN,
            "--table-id", TABLE_NOTE,
            "--record-id", record_id,
            "--json", "@_link_payload.json",
        ]
        result = subprocess.run(
            cmd, capture_output=True, timeout=30, cwd=KAOYAN,
        )
        return result.returncode == 0
    except Exception:
        return False
    finally:
        try:
            os.unlink(json_file)
        except OSError:
            pass


def main():
    # 1. Load doc mapping
    mapping_path = os.path.join(KAOYAN, "doc_mapping.json")
    doc_mapping = json.load(open(mapping_path, "r", encoding="utf-8"))
    url_lookup = build_url_lookup(doc_mapping)
    print(f"URL lookup built: {len(url_lookup)} (sub, chapter) -> url mappings")
    for key, url in sorted(url_lookup.items()):
        print(f"  {key[0]:10s} {key[1]:8s} -> ...{url[-20:]}")

    # 2. Load index and state to get record IDs and their sub/chapter
    with open(os.path.join(KAOYAN, "笔记索引.yaml"), "r", encoding="utf-8") as f:
        index = yaml.safe_load(f)
    state = json.load(open(os.path.join(KAOYAN, "sync_state.json"), "r", encoding="utf-8"))

    # 3. Match records to URLs
    to_update = []  # list of (record_id, url)
    no_match = []
    for entry in index.get("entries", []):
        eid = entry["id"]
        sub = entry.get("sub", "")
        chapter = entry.get("chapter", "")
        rid = state.get("entries", {}).get(eid, {}).get("bitable_record_id")
        if not rid:
            continue

        url = url_lookup.get((sub, chapter))
        if url:
            to_update.append((rid, url, eid))
        else:
            no_match.append((eid, sub, chapter))

    print(f"\nRecords to update: {len(to_update)}")
    print(f"Records with no matching doc: {len(no_match)}")
    if no_match:
        # Show unique (sub, chapter) combos that have no match
        missing = set((s, c) for _, s, c in no_match)
        print(f"  Missing chapters ({len(missing)}):")
        for s, c in sorted(missing):
            print(f"    {s} / {c}")

    # 4. Batch update (deduplicate by record_id since same chapter may have multiple entries)
    seen = set()
    unique_updates = []
    for rid, url, eid in to_update:
        if rid not in seen:
            unique_updates.append((rid, url))
            seen.add(rid)

    print(f"\nUnique records to update: {len(unique_updates)}")

    # 5. Execute updates
    ok = 0
    fail = 0
    for i, (rid, url) in enumerate(unique_updates, 1):
        success = update_record_link(rid, url)
        if success:
            ok += 1
        else:
            fail += 1
        if i % 20 == 0:
            print(f"  Progress: {i}/{len(unique_updates)} (ok={ok}, fail={fail})")

    print(f"\nDone. Updated: {ok}  Failed: {fail}")


if __name__ == "__main__":
    main()

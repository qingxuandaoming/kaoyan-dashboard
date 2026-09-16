#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Batch import chapter note .md files to Feishu as docx documents.

Outputs a JSON mapping: {relative_path: {token, url, name}}
"""
import json
import os
import re
import shutil
import subprocess
import sys
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

KAOYAN = r"C:\Users\92534\Desktop\考研"
MAPPING_PATH = os.path.join(KAOYAN, "doc_mapping.json")

# Reuse the same node/run.js lookup as sync_to_feishu
node_exe = shutil.which("node")
run_js = os.path.expandvars(r"%APPDATA%\npm\node_modules\@larksuite\cli\scripts\run.js")
LARK = [node_exe, run_js] if node_exe and os.path.exists(run_js) else ["lark-cli"]


def find_chapter_files():
    """Find all 第X章_*.md files."""
    pattern = re.compile(r"^第\d+章[_\-].+\.md$")
    files = []
    for root, dirs, fnames in os.walk(KAOYAN):
        dirs[:] = [d for d in dirs if d not in (
            "src", ".git", "__pycache__", "专题", "notebook", "note",
            "真题", "习题册", "Chapter1", "数据结构", "时政", "基础",
        )]
        for f in fnames:
            if pattern.match(f):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, KAOYAN)
                files.append(rel)
    files.sort()
    return files


def import_file(rel_path: str) -> dict:
    """Import one md file as Feishu docx. Returns {token, url} or None."""
    cmd = LARK + [
        "drive", "+import",
        "--file", rel_path,
        "--type", "docx",
        "--as", "bot",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=120, cwd=KAOYAN,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        if result.returncode == 0:
            data = json.loads(stdout)
            d = data.get("data", {})
            return {
                "token": d.get("token", ""),
                "url": d.get("url", ""),
                "ok": True,
            }
        else:
            stderr = result.stderr.decode("utf-8", errors="replace")
            print(f"  ERROR: {stderr[:200]}")
            return {"ok": False, "error": stderr[:200]}
    except Exception as e:
        print(f"  EXCEPTION: {e}")
        return {"ok": False, "error": str(e)}


def main():
    # Load existing mapping if any
    if os.path.exists(MAPPING_PATH):
        mapping = json.load(open(MAPPING_PATH, "r", encoding="utf-8"))
        print(f"Loaded existing mapping: {len(mapping)} entries")
    else:
        mapping = {}

    files = find_chapter_files()
    # Filter out already-imported files
    remaining = [f for f in files if f not in mapping or not mapping[f].get("ok")]
    print(f"Total chapter files: {len(files)}")
    print(f"Already imported: {len(files) - len(remaining)}")
    print(f"To import: {len(remaining)}")
    print()

    for i, rel in enumerate(remaining, 1):
        name = os.path.splitext(os.path.basename(rel))[0]
        print(f"[{i}/{len(remaining)}] Importing: {rel}")
        result = import_file(rel)
        if result.get("ok"):
            result["name"] = name
            result["rel_path"] = rel
            mapping[rel] = result
            print(f"  -> {result['url']}")
        else:
            mapping[rel] = result
            print(f"  FAILED")

    # Save mapping
    with open(MAPPING_PATH, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    print(f"\nMapping saved to {MAPPING_PATH}")

    # Summary
    ok_count = sum(1 for v in mapping.values() if v.get("ok"))
    fail_count = sum(1 for v in mapping.values() if not v.get("ok"))
    print(f"Success: {ok_count}  Failed: {fail_count}")


if __name__ == "__main__":
    main()

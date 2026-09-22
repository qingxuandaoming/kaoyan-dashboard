#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Rebuild Feishu cloud docs: create folder structure, import chapter files,
upload and embed images.

Step 1: Create folder hierarchy using bot identity (auto-grants user full_access)
Step 2: Import .md files into correct folders
Step 3: Upload images and patch docs to embed them
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

KAOYAN = r"E:\NPEE"
STATE_PATH = os.path.join(KAOYAN, "feishu_docs_state.json")

node_exe = shutil.which("node")
run_js = os.path.expandvars(r"%APPDATA%\npm\node_modules\@larksuite\cli\scripts\run.js")
LARK = [node_exe, run_js] if node_exe and os.path.exists(run_js) else ["lark-cli"]

# Map local paths to Feishu folder paths
PATH_TO_FOLDER = {
    "408\\OS": "考研笔记/408/OS",
    "408\\DS": "考研笔记/408/DS",
    "408\\CO": "考研笔记/408/CO",
    "408\\CN": "考研笔记/408/CN",
    "Math\\高数": "考研笔记/Math/高数",
    "Math\\线代": "考研笔记/Math/线代",
    "Math\\概率论\\概率": "考研笔记/Math/概率论",
    "Politics\\史纲": "考研笔记/Politics/史纲",
    "Politics\\毛中特": "考研笔记/Politics/毛中特",
    "Politics\\马原": "考研笔记/Politics/马原",
}

# Folder tree: name -> parent path (None = root)
FOLDER_SPEC = [
    ("考研笔记", None),
    ("408", "考研笔记"),
    ("Math", "考研笔记"),
    ("Politics", "考研笔记"),
    ("OS", "考研笔记/408"),
    ("DS", "考研笔记/408"),
    ("CO", "考研笔记/408"),
    ("CN", "考研笔记/408"),
    ("高数", "考研笔记/Math"),
    ("线代", "考研笔记/Math"),
    ("概率论", "考研笔记/Math"),
    ("史纲", "考研笔记/Politics"),
    ("毛中特", "考研笔记/Politics"),
    ("马原", "考研笔记/Politics"),
]


def lark_cmd(args, identity="bot", cwd=None, timeout=60):
    """Run lark-cli command and return (ok, data_dict)."""
    cmd = LARK + args + ["--as", identity]
    result = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd or KAOYAN)
    stdout = result.stdout.decode("utf-8", errors="replace")
    if result.returncode == 0:
        try:
            data = json.loads(stdout)
            return True, data.get("data", data)
        except json.JSONDecodeError:
            return True, {"raw": stdout}
    return False, stdout


def create_folder(name, parent_token=None):
    """Create a folder with bot identity. Returns (ok, folder_token)."""
    args = ["drive", "+create-folder", "--name", name]
    if parent_token:
        args += ["--folder-token", parent_token]
    ok, data = lark_cmd(args, identity="bot")
    if ok:
        token = data.get("token") or data.get("folder_token") or data.get("folder", {}).get("token", "")
        return True, token
    # Check if already exists (rate limit etc.)
    print(f"    ERROR creating folder '{name}': {str(data)[:200]}")
    return False, None


def build_folder_tree(state_folders):
    """Create the full folder hierarchy. Returns updated dict of path -> folder_token."""
    tokens = dict(state_folders)  # copy existing

    print("=== Step 1: Building folder structure ===")
    for name, parent_path in FOLDER_SPEC:
        if parent_path:
            full_path = f"{parent_path}/{name}"
        else:
            full_path = name

        if full_path in tokens and tokens[full_path]:
            print(f"  [EXISTS] {full_path} -> {tokens[full_path]}")
            continue

        parent_token = None
        if parent_path:
            parent_token = tokens.get(parent_path)
            if not parent_token:
                print(f"  [SKIP] {full_path} (parent '{parent_path}' not found)")
                continue

        print(f"  [CREATE] {full_path}")
        ok, token = create_folder(name, parent_token)
        if ok and token:
            tokens[full_path] = token
            print(f"    -> {token}")
        else:
            print(f"    FAILED")

        # Rate limit: wait between API calls
        time.sleep(1.5)

    print(f"  Total folders: {len(tokens)}")
    return tokens


def import_file(rel_path, folder_token):
    """Import a .md file as Feishu docx into a specific folder."""
    name = os.path.splitext(os.path.basename(rel_path))[0]
    args = [
        "drive", "+import",
        "--file", rel_path,
        "--type", "docx",
        "--folder-token", folder_token,
        "--name", name,
        "--as", "bot",
    ]
    cmd = LARK + args
    result = subprocess.run(cmd, capture_output=True, timeout=120, cwd=KAOYAN)
    stdout = result.stdout.decode("utf-8", errors="replace")
    if result.returncode == 0:
        data = json.loads(stdout).get("data", {})
        return {
            "ok": True,
            "token": data.get("token", ""),
            "url": data.get("url", ""),
        }
    return {"ok": False, "error": stdout[:300]}


def find_chapter_files():
    """Find all chapter .md files with their folder mapping."""
    pattern = re.compile(r"^第\d+章[_\-].+\.md$")
    files = []
    for root, dirs, fnames in os.walk(KAOYAN):
        dirs[:] = [d for d in dirs if d not in (
            "src", ".git", "__pycache__", "专题", "notebook", "note",
            "真题", "习题册", "Chapter1", "数据结构", "时政", "基础", "assets", "images",
        )]
        for f in fnames:
            if pattern.match(f):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, KAOYAN)
                rel_dir = os.path.dirname(rel)
                folder_path = PATH_TO_FOLDER.get(rel_dir)
                if folder_path:
                    files.append({"rel_path": rel, "rel_dir": rel_dir, "folder_path": folder_path})
    files.sort(key=lambda x: x["rel_path"])
    return files


def main():
    # Load or init state
    if os.path.exists(STATE_PATH):
        state = json.load(open(STATE_PATH, "r", encoding="utf-8"))
    else:
        state = {"folders": {}, "docs": {}, "images": {}}

    # Pre-seed already created folders from earlier manual creation
    known = {
        "考研笔记": "KCzxfJLLLlZUFAdrAz5c89Ion0c",
        "考研笔记/408": "ZndtfkAk5lN9JbdGuvhc9FNknjg",
        "考研笔记/Math": "HrZZfTPpRl7CAXdVYVkcW8bPn9c",
        "考研笔记/Politics": "OPWhf7IUflfOiedDqNTc6C8gntc",
    }
    for k, v in known.items():
        if k not in state["folders"] or not state["folders"][k]:
            state["folders"][k] = v

    # Step 1: Folder structure
    state["folders"] = build_folder_tree(state["folders"])
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    # Check all needed folders exist
    missing = [p for p in PATH_TO_FOLDER.values() if p not in state["folders"]]
    if missing:
        print(f"\n  WARNING: Missing folders: {missing}")
        print("  Cannot proceed with import until all folders are created.")
        return

    # Step 2: Import files
    files = find_chapter_files()
    remaining = [f for f in files if f["rel_path"] not in state["docs"] or not state["docs"][f["rel_path"]].get("ok")]
    print(f"\n=== Step 2: Importing chapter files ===")
    print(f"  Total: {len(files)}, Already done: {len(files) - len(remaining)}, To import: {len(remaining)}")

    for i, finfo in enumerate(remaining, 1):
        folder_token = state["folders"].get(finfo["folder_path"], "")
        if not folder_token:
            print(f"  [{i}/{len(remaining)}] SKIP {finfo['rel_path']} (no folder token)")
            continue
        print(f"  [{i}/{len(remaining)}] {finfo['rel_path']}")
        result = import_file(finfo["rel_path"], folder_token)
        state["docs"][finfo["rel_path"]] = result
        if result.get("ok"):
            print(f"    -> {result['url']}")
        else:
            print(f"    FAILED: {result.get('error', '?')[:100]}")

        # Save state periodically
        if i % 5 == 0:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)

        # Rate limit
        time.sleep(1)

    # Final save
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    ok_count = sum(1 for v in state["docs"].values() if v.get("ok"))
    fail_count = sum(1 for v in state["docs"].values() if not v.get("ok"))
    print(f"\n  Import complete. Success: {ok_count}, Failed: {fail_count}")


if __name__ == "__main__":
    main()

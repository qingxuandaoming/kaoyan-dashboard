#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Upload and embed images into Feishu docs.

Strategy:
- For each chapter doc with image references:
  1. Fetch doc to find existing image blocks (broken placeholders)
  2. For broken image blocks: delete them, insert real image, move to position
  3. For dropped images (no block): find text anchor, insert real image, move to position
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

KAOYAN = r"C:\Users\92534\Desktop\考研"
STATE_PATH = os.path.join(KAOYAN, "feishu_docs_state.json")

node_exe = shutil.which("node")
run_js = os.path.expandvars(r"%APPDATA%\npm\node_modules\@larksuite\cli\scripts\run.js")
LARK = [node_exe, run_js] if node_exe and os.path.exists(run_js) else ["lark-cli"]

IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def lark_run(args, identity="bot", timeout=120):
    """Run lark-cli and return parsed JSON."""
    cmd = LARK + args + ["--as", identity]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=KAOYAN)
    stdout = r.stdout.decode("utf-8", errors="replace")
    if r.returncode == 0:
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {"ok": True, "raw": stdout}
    return {"ok": False, "error": stdout[:300]}


def resolve_image_path(md_file, src):
    """Resolve image src to absolute local path."""
    if src.startswith("file:///"):
        # file:///e:/考研/408/DS/images/foo.png -> try local equivalent
        p = src.replace("file:///", "").replace("/", os.sep)
        if len(p) > 1 and p[1] == ":":
            p = p[0].upper() + ":" + p[2:]
        p = os.path.normpath(p)
        # If not found at original path, try under KAOYAN
        if not os.path.exists(p):
            # Extract path after 考研/ (or similar marker)
            parts = src.replace("file:///", "").split("/")
            for i, part in enumerate(parts):
                if part in ("考研", "kaoyan"):
                    sub = os.path.join(*parts[i + 1:]) if i + 1 < len(parts) else ""
                    alt_path = os.path.join(KAOYAN, sub)
                    if os.path.exists(alt_path):
                        return os.path.normpath(alt_path)
        return p
    elif src.startswith("./"):
        return os.path.normpath(os.path.join(os.path.dirname(md_file), src[2:]))
    else:
        return os.path.normpath(os.path.join(os.path.dirname(md_file), src))


def fetch_doc_content(doc_token):
    """Fetch doc and return raw content string."""
    resp = lark_run(["docs", "+fetch", "--doc", doc_token, "--detail", "with-ids"])
    if not resp.get("ok"):
        return None, resp.get("error", "fetch failed")
    return resp.get("data", {}).get("document", {}).get("content", ""), None


def parse_image_blocks(content):
    """Extract image block IDs with preceding text block IDs from doc content."""
    parts = re.split(r"(<img[^>]*/?>)", content)
    images = []
    for i, part in enumerate(parts):
        if "<img" in part:
            img_id = re.search(r'id="([^"]+)"', part)
            prev = parts[i - 1] if i > 0 else ""
            prev_ids = re.findall(r'id="([^"]+)"', prev)
            preceding_id = prev_ids[-1] if prev_ids else None
            if img_id:
                images.append({
                    "block_id": img_id.group(1),
                    "preceding_id": preceding_id,
                })
    return images


def find_text_anchor(content, alt_text):
    """Find the block ID of the text block closest to where an image should go.
    Uses the image alt text to find nearby context."""
    # Clean alt text for search
    search_terms = []
    # Extract key phrases from alt text (first 20 chars, unique words)
    clean_alt = alt_text[:40]
    search_terms.append(clean_alt)

    # Also try shorter fragments
    words = clean_alt.split()
    if len(words) > 3:
        search_terms.append(" ".join(words[:3]))

    for term in search_terms:
        if len(term) < 4:
            continue
        # Search in content
        idx = content.find(term)
        if idx >= 0:
            # Find the enclosing block ID
            start = max(0, idx - 500)
            snippet = content[start:idx + 200]
            ids = re.findall(r'id="([^"]+)"', snippet)
            if ids:
                return ids[-1]  # Return the closest block before the match

    # Fallback: try to find any text mentioning the topic
    # Extract subject from alt text (e.g., "Cache", "AVL", "泰勒")
    topics = re.findall(r'[A-Za-z\u4e00-\u9fff]{2,}', alt_text)
    for topic in topics[:3]:
        if len(topic) < 2:
            continue
        idx = content.find(topic)
        if idx >= 0:
            start = max(0, idx - 500)
            snippet = content[start:idx + 200]
            ids = re.findall(r'id="([^"]+)"', snippet)
            if ids:
                return ids[-1]

    return None


def delete_block(doc_token, block_id):
    return lark_run([
        "docs", "+update", "--doc", doc_token,
        "--command", "block_delete", "--block-id", block_id
    ])


def insert_image(doc_token, image_path):
    """Insert image at end of doc. Returns (block_id, file_token) or (None, error)."""
    # lark-cli requires relative paths within cwd
    rel_path = os.path.relpath(image_path, KAOYAN)
    resp = lark_run([
        "docs", "+media-insert", "--doc", doc_token,
        "--file", rel_path
    ])
    if resp.get("ok"):
        data = resp.get("data", {})
        return data.get("block_id"), data.get("file_token")
    return None, resp.get("error", "insert failed")


def move_block(doc_token, anchor_id, block_id):
    return lark_run([
        "docs", "+update", "--doc", doc_token,
        "--command", "block_move_after",
        "--block-id", anchor_id,
        "--src-block-ids", block_id
    ])


def find_chapter_files_with_images():
    """Find all chapter .md files that have image references."""
    pattern = re.compile(r"^第\d+章[_\-].+\.md$")
    skip_dirs = {"src", ".git", "__pycache__", "专题", "notebook", "note",
                 "真题", "习题册", "Chapter1", "数据结构", "时政", "基础", "assets", "images"}
    results = []
    for root, dirs, fnames in os.walk(KAOYAN):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in fnames:
            if pattern.match(f):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, KAOYAN)
                with open(full, "r", encoding="utf-8") as fh:
                    content = fh.read()
                imgs = IMG_RE.findall(content)
                if imgs:
                    img_list = []
                    for alt, src in imgs:
                        local_path = resolve_image_path(full, src)
                        exists = os.path.exists(local_path)
                        img_list.append({
                            "alt": alt,
                            "src": src,
                            "local_path": local_path,
                            "exists": exists,
                        })
                    results.append({
                        "rel_path": rel,
                        "full_path": full,
                        "images": img_list,
                    })
    results.sort(key=lambda x: x["rel_path"])
    return results


def process_doc(doc_token, md_file, images, state_images):
    """Process all images in a single doc."""
    doc_key = doc_token
    if doc_key in state_images and state_images[doc_key].get("done"):
        print(f"  [SKIP] Already processed")
        return

    # Step 1: Fetch doc content
    print(f"  Fetching doc content...")
    content, err = fetch_doc_content(doc_token)
    if err:
        print(f"  ERROR fetching: {err}")
        return

    # Parse existing image blocks in doc
    doc_images = parse_image_blocks(content)
    print(f"  Doc has {len(doc_images)} image blocks, markdown has {len(images)} references")

    processed = 0
    total = len(images)

    # First: delete all broken image blocks (these are from import, all pointing to default.png)
    for doc_img in doc_images:
        del_resp = delete_block(doc_token, doc_img["block_id"])
        if del_resp.get("ok"):
            print(f"  Deleted broken image block: {doc_img['block_id'][:20]}...")
        time.sleep(0.5)

    # Re-fetch after deletions to get updated content for anchor finding
    if doc_images:
        time.sleep(1)
        content, _ = fetch_doc_content(doc_token)
        if not content:
            print(f"  WARNING: Could not re-fetch after deletion")

    # Now insert each image and move to correct position
    for idx, md_img in enumerate(images):
        img_num = idx + 1
        fname = os.path.basename(md_img["local_path"])

        if not md_img["exists"]:
            print(f"  [{img_num}/{total}] SKIP (missing): {fname[:50]}")
            continue

        # Find anchor: the text block near where this image should go
        # For images that had broken blocks, use the preceding block
        anchor_id = None
        if idx < len(doc_images) and doc_images[idx].get("preceding_id"):
            anchor_id = doc_images[idx]["preceding_id"]
        else:
            # Find anchor by searching for alt text context
            anchor_id = find_text_anchor(content, md_img["alt"])

        print(f"  [{img_num}/{total}] Insert: {fname[:50]}")

        # Insert image at end
        new_block_id, err_msg = insert_image(doc_token, md_img["local_path"])
        if not new_block_id:
            print(f"    ERROR insert: {err_msg[:100] if err_msg else 'unknown'}")
            continue
        time.sleep(0.5)

        # Move to correct position
        if anchor_id:
            mv_resp = move_block(doc_token, anchor_id, new_block_id)
            if mv_resp.get("ok"):
                print(f"    OK (positioned)")
            else:
                print(f"    OK (at end, move failed)")
        else:
            print(f"    OK (at end, no anchor found)")

        processed += 1
        time.sleep(0.5)

    state_images[doc_key] = {"done": True, "processed": processed, "total": total}
    print(f"  Result: {processed}/{total} images embedded")


def main():
    state = json.load(open(STATE_PATH, "r", encoding="utf-8"))
    if "images" not in state:
        state["images"] = {}

    # Reset images state for fresh run
    state["images"] = {}

    files = find_chapter_files_with_images()
    print(f"Files with images: {len(files)}")
    total_imgs = sum(len(f["images"]) for f in files)
    print(f"Total images to process: {total_imgs}\n")

    for fi in files:
        rel = fi["rel_path"]
        doc_info = state["docs"].get(rel)
        if not doc_info or not doc_info.get("ok"):
            print(f"[SKIP] {rel} (not imported)")
            continue

        doc_token = doc_info["token"]
        print(f"=== {rel} ({len(fi['images'])} images) ===")
        process_doc(doc_token, fi["full_path"], fi["images"], state["images"])

        # Save state after each doc
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print()

    # Final summary
    done = sum(1 for v in state["images"].values() if v.get("done"))
    total_processed = sum(v.get("processed", 0) for v in state["images"].values())
    total_expected = sum(v.get("total", 0) for v in state["images"].values())
    print(f"\n{'='*50}")
    print(f"SUMMARY: {done} docs processed, {total_processed}/{total_expected} images embedded")


if __name__ == "__main__":
    main()

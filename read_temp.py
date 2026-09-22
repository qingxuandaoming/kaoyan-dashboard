#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
read_temp.py — Scan the temp/ folder and output file contents for classification.

Usage:
    python read_temp.py                # Read all pending files
    python read_temp.py --archive      # Move processed files to temp/done/
"""

import argparse
import io
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Force UTF-8 on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(r"E:\NPEE")
TEMP_DIR = BASE_DIR / "temp"
DONE_DIR = TEMP_DIR / "done"

# File extensions we can read
TEXT_EXTS = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif"}


def classify_subject(filepath: Path, content: str = "") -> str:
    """Guess which subject a file belongs to based on filename and content."""
    name = (filepath.stem + " " + content[:500]).lower()
    
    keywords = {
        "408": ["408", "数据结构", "ds", "计组", "计算机组成", "co", "操作系统", "os",
                 "计算机网络", "cn", "cache", "进程", "线程", "内存", "tcp", "ip",
                 "算法", "二叉树", "链表", "排序", "图论"],
        "数学": ["数学", "高数", "高等数学", "线代", "线性代数", "概率", "微分",
                 "积分", "极限", "级数", "矩阵", "特征值", "张宇", "李永乐"],
        "政治": ["政治", "马原", "毛中特", "史纲", "思修", "习思想", "唯物",
                 "辩证", "认识论", "徐涛", "肖秀荣", "优题库"],
        "英语": ["英语", "词汇", "阅读", "翻译", "作文", "长难句", "单词",
                 "短语", "grammar", "vocab", "reading"],
    }
    
    scores = {subj: 0 for subj in keywords}
    for subj, kws in keywords.items():
        for kw in kws:
            if kw in name:
                scores[subj] += 1
    
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "其他"


def read_text_file(filepath: Path) -> str:
    """Read a text file with fallback encoding."""
    for enc in ["utf-8", "gbk", "gb2312", "latin-1"]:
        try:
            return filepath.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return f"[无法读取: 编码不支持]"


def scan_temp_folder():
    """Scan temp folder and return structured file list."""
    if not TEMP_DIR.exists():
        print("temp/ 文件夹不存在")
        return []
    
    files = []
    for f in sorted(TEMP_DIR.iterdir()):
        if f.is_dir():
            continue
        if f.name == "README.md":
            continue
        if f.suffix.lower() in TEXT_EXTS:
            content = read_text_file(f)
            subject = classify_subject(f, content)
            files.append({
                "path": str(f),
                "name": f.name,
                "ext": f.suffix,
                "size": f.stat().st_size,
                "subject": subject,
                "type": "text",
                "content": content[:3000],  # truncate very long files
                "truncated": len(content) > 3000,
            })
        elif f.suffix.lower() in IMAGE_EXTS:
            subject = classify_subject(f)
            files.append({
                "path": str(f),
                "name": f.name,
                "ext": f.suffix,
                "size": f.stat().st_size,
                "subject": subject,
                "type": "image",
                "content": "[图片文件，需要 OCR 提取文字]",
                "truncated": False,
            })
        else:
            files.append({
                "path": str(f),
                "name": f.name,
                "ext": f.suffix,
                "size": f.stat().st_size,
                "subject": "其他",
                "type": "unknown",
                "content": f"[不支持的文件格式: {f.suffix}]",
                "truncated": False,
            })
    
    return files


def archive_files():
    """Move processed files to temp/done/ with date prefix."""
    DONE_DIR.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    archived = []
    
    for f in TEMP_DIR.iterdir():
        if f.is_dir() or f.name == "README.md":
            continue
        dest = DONE_DIR / f"{date_str}_{f.name}"
        # Avoid overwriting
        if dest.exists():
            dest = DONE_DIR / f"{date_str}_{f.stem}_2{f.suffix}"
        shutil.move(str(f), str(dest))
        archived.append(f"{f.name} -> {dest.name}")
    
    return archived


def main():
    parser = argparse.ArgumentParser(description="Read temp folder for evening review")
    parser.add_argument("--archive", action="store_true",
                        help="Move processed files to temp/done/")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    args = parser.parse_args()
    
    if args.archive:
        archived = archive_files()
        if archived:
            print(f"已归档 {len(archived)} 个文件:")
            for a in archived:
                print(f"  {a}")
        else:
            print("没有需要归档的文件")
        return
    
    files = scan_temp_folder()
    
    if not files:
        print("temp/ 文件夹为空，没有待处理的内容。")
        return
    
    if args.json:
        print(json.dumps(files, ensure_ascii=False, indent=2))
    else:
        print(f"temp/ 文件夹中有 {len(files)} 个待处理文件：\n")
        for i, f in enumerate(files, 1):
            print(f"{'='*60}")
            print(f"[{i}] {f['name']}")
            print(f"    分类: {f['subject']} | 类型: {f['type']} | 大小: {f['size']}B")
            if f.get('truncated'):
                print(f"    ⚠️ 内容已截断（原文件较大）")
            print(f"{'─'*60}")
            print(f['content'])
            print()
        
        # Summary by subject
        by_subject = {}
        for f in files:
            by_subject.setdefault(f['subject'], []).append(f['name'])
        print(f"\n{'='*60}")
        print("分类统计:")
        for subj, names in sorted(by_subject.items()):
            print(f"  {subj}: {len(names)} 个文件")
            for n in names:
                print(f"    - {n}")


if __name__ == "__main__":
    main()

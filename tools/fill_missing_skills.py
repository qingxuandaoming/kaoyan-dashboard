#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fill_missing_skills.py — 把缺失的考研 skill 目录从 .qoderworkcn 复制到其它共享位置。

与 sync_skills.py 的区别：
  - sync_skills.py 只「更新已存在」的 skill（不新增）。
  - 本脚本「补全缺失」：目标位置没有的考研 skill，整目录 copytree 过去。

源：C:\\Users\\92534\\.qoderworkcn\\skills（考研 skill 齐全）
复制时忽略 .SKILL.md.bak / __pycache__ / *.pyc；复制后用 SKILL.md 的 MD5 校验。

用法： python src/tools/fill_missing_skills.py
"""
import hashlib
import io
import os
import shutil
import sys

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SRC = r"C:\Users\92534\.qoderworkcn\skills"
DESTS = [
    r"C:\Users\92534\AppData\Roaming\kimi-desktop\daimon-share\daimon\skills",
    r"C:\Users\92534\.agents\skills",
    r"C:\Users\92534\.trae-cn\skills",
]

# 考研相关 skill（不含 notes-organizing，那是 E:\notes 通用笔记，非考研）
KAOGAN = [
    "408-note-taking",
    "math-one-note-taking",
    "english-note-taking",
    "politics-note-taking",
    "morning-review",
    "kaoyan-evening-review",
    "kaoyan-progress-sync",
    "exam-note-dashboard",
    "html-flashcard-builder",
    "flashcard-studio",
    "vocab-graph",
    "docx-chinese-text-extraction",
    "english-essay-correction",
]

IGNORE = shutil.ignore_patterns(".SKILL.md.bak", "__pycache__", "*.pyc")


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    copied = present = errors = 0
    for dest in DESTS:
        print("=" * 60)
        print(f"目标: {dest}")
        if not os.path.isdir(dest):
            print("  [WARN] 目标根目录不存在，跳过")
            continue
        for s in KAOGAN:
            src_dir = os.path.join(SRC, s)
            dst_dir = os.path.join(dest, s)
            if not os.path.isdir(src_dir):
                print(f"  [ERR]  源缺失 {src_dir}")
                errors += 1
                continue
            if os.path.isdir(dst_dir):
                print(f"  [HAVE] {s}（已存在，跳过）")
                present += 1
                continue
            shutil.copytree(src_dir, dst_dir, ignore=IGNORE)
            src_md = os.path.join(src_dir, "SKILL.md")
            dst_md = os.path.join(dst_dir, "SKILL.md")
            if os.path.exists(src_md) and os.path.exists(dst_md) and md5(src_md) == md5(dst_md):
                n = sum(len(fs) for _, _, fs in os.walk(dst_dir))
                print(f"  [COPY] {s} ✓（{n} 个文件）")
                copied += 1
            else:
                print(f"  [ERR]  {s} 复制后校验失败！")
                errors += 1
    print("=" * 60)
    print(f"新复制={copied}  已存在={present}  错误={errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

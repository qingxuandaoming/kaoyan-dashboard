#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""sync_skills.py — 把已优化的 skill 从权威源同步到其它共享位置。

skill 被多个工具共享（共 6 个位置）：
  - Qoderworkcn: C:\\Users\\92534\\.qoderworkcn\\skills           （权威源 SRC）
  - Kimi:        C:\\Users\\92534\\AppData\\Roaming\\kimi-desktop\\daimon-share\\daimon\\skills
  - 豆包&Qoder:  C:\\Users\\92534\\.agents\\skills
  - Trae:        C:\\Users\\92534\\.trae-cn\\skills
  - Cherry-Data: E:\\application\\CherryStudio\\Data\\Skills
  - Cherry-Agent:E:\\application\\CherryStudio\\Data\\Agents\\.claude\\skills

策略：对每个目标位置，仅同步「该位置已存在」的 skill，不向缺失的位置新增目录。
复制用 copy2 保留 mtime，使「重复运行 = 幂等空操作」。

防回退保护（重要）：
  若目标位置的 SKILL.md 比源更新（mtime 更大），说明有人直接改了目标副本而没回推源。
  此时默认 **跳过并告警**，而不是盲目覆盖——否则会把新版本静默降级成旧版本。
  确认要以源为准时加 --force。

用法：
  python src/tools/sync_skills.py            # 同步
  python src/tools/sync_skills.py --check    # 只体检，不写
  python src/tools/sync_skills.py --force    # 忽略防回退保护，强制以源为准
"""
import argparse
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
    r"E:\application\CherryStudio\Data\Skills",
    r"E:\application\CherryStudio\Data\Agents\.claude\skills",
]

# 考研系列 skill 清单（自动同步范围；新增考研 skill 时在此登记）
SKILLS = [
    "408-note-taking",
    "docx-chinese-text-extraction",
    "english-essay-correction",
    "english-note-taking",
    "exam-note-dashboard",
    "flashcard-studio",
    "html-flashcard-builder",
    "kaoyan-evening-review",
    "kaoyan-progress-sync",
    "math-one-note-taking",
    "morning-review",
    "politics-note-taking",
    "vocab-graph",
]


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def skill_files(root):
    """skill 目录下的全部文件（相对路径），用于连同 reference/scripts/assets 一起同步。"""
    out = []
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f == ".SKILL.md.bak":
                continue
            out.append(os.path.relpath(os.path.join(r, f), root))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description="同步考研系列 skill 到全部共享位置")
    ap.add_argument("--check", action="store_true", help="只体检不写入")
    ap.add_argument("--force", action="store_true", help="忽略防回退保护，强制以源为准")
    args = ap.parse_args()

    synced = skipped = removed_bak = errors = regressions = drift = 0

    for dest in DESTS:
        print("=" * 64)
        print(f"目标: {dest}{'   [体检模式]' if args.check else ''}")
        if not os.path.isdir(dest):
            print("  [WARN] 目标根目录不存在，跳过")
            continue
        for s in SKILLS:
            sdir = os.path.join(dest, s)
            if not os.path.isdir(sdir):
                print(f"  [SKIP] {s}（此位置无此 skill）")
                skipped += 1
                continue
            src_dir = os.path.join(SRC, s)
            src_md = os.path.join(src_dir, "SKILL.md")
            dst_md = os.path.join(sdir, "SKILL.md")
            if not os.path.exists(src_md):
                print(f"  [ERR]  源缺失 {src_md}")
                errors += 1
                continue

            # 防回退保护：目标比源新 → 说明目标被直接编辑过而没回推
            # 容差 2 秒：Cherry 位于 E:（exFAT），mtime 只精确到秒且会被向上取整，
            # 跨盘（NTFS↔exFAT）比较 1 秒内的差异没有意义。
            if os.path.exists(dst_md) and not args.force:
                if os.path.getmtime(dst_md) > os.path.getmtime(src_md) + 2.0:
                    print(f"  [⚠ 回退风险] {s}：目标副本比源更新，已跳过（要覆盖请加 --force）")
                    regressions += 1
                    continue

            if args.check:
                same = os.path.exists(dst_md) and md5(src_md) == md5(dst_md)
                print(f"  [{'OK  ' if same else 'DIFF'}] {s}"
                      f"{'' if same else '  <- 内容不一致，需同步'}")
                if not same:
                    drift += 1
                continue

            # 连同支持文件一起镜像（只增改，不删除目标多余文件）
            for rel in skill_files(src_dir):
                src_f = os.path.join(src_dir, rel)
                dst_f = os.path.join(sdir, rel)
                os.makedirs(os.path.dirname(dst_f), exist_ok=True)
                shutil.copy2(src_f, dst_f)

            bak = os.path.join(sdir, ".SKILL.md.bak")
            bak_note = ""
            if os.path.exists(bak):
                os.remove(bak)
                removed_bak += 1
                bak_note = " | 删除.bak"

            if md5(src_md) == md5(dst_md):
                print(f"  [SYNC] {s} ✓{bak_note}")
                synced += 1
            else:
                print(f"  [ERR]  {s} 校验失败！")
                errors += 1

    print("=" * 64)
    if args.check:
        print(f"体检完成：内容不一致={drift}  回退风险={regressions}")
    else:
        print(f"同步={synced}  跳过={skipped}  删除.bak={removed_bak}  "
              f"回退风险={regressions}  错误={errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

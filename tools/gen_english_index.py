#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gen_english_index.py — 为英语笔记生成 English/notes_index.json

英语笔记是普通 Markdown：既没有 <!-- note-meta:entry --> 块，也不在
build_index.py 的 DRAFT_FILES 里，所以长期完全没进「笔记索引.yaml」。
后果是大盘英语那 15 格恒为 0%，跟你做了多少阅读/翻译无关（2026-09-14 修复）。

本脚本按「路径 → 考点」规则表扫出**系统性笔记**，落成与
Politics/notes_index.json 同构的 JSON，交给 build_index.py 统一读入。

刻意不收：
  * word&phrase/辨析/                368 篇自动生成的单词辨析小卡
  * word&phrase/vocab-graph/         227 篇自动生成的词图输出
  * reading&magazines/外刊/**/原文/   21 篇外刊原文（原料，不是笔记）
  * listening&speaking/              英语一不考听力
  * schedule/                        规划，不是知识点笔记

收进来的是判断，不是自动发现——故意如此：这 595 篇词表要是全算「笔记」，
英语词汇 4 格会直接跳满 100%，反而掩盖你还没背完的事实。词汇掌握度交给
闪卡正确率体现（ENG-VOC-01-03 已有真实答题记录）。

Usage:
    python tools/gen_english_index.py
"""

import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent.parent
ENG_DIR = Path(os.environ.get("NOTES_ROOT", r"E:\NPEE")) / "English"   # 笔记库根（2026-09-25 起与代码根分离，可用环境变量 NOTES_ROOT 覆盖）
OUT_PATH = ENG_DIR / "notes_index.json"

# 考点前缀 → 知识图谱 sub 名（与 english_graph.json 对齐）
ENG_SUBS = {
    "VOC":   "词汇",
    "GRAM":  "语法与长难句",
    "READ":  "阅读理解",
    "WRITE": "写作",
    "TRN":   "翻译与完形",
}

# (相对 English/ 的 glob, 考点前缀, 图谱章节号)
# 顺序敏感：先匹配到的规则胜出，特例必须排在通配规则前面。
RULES = [
    # 词汇
    ("word&phrase/词根词缀/**/*.md",                          "VOC",   1),
    ("word&phrase/*.md",                                      "VOC",   2),
    ("word&phrase/固定搭配&短语&俚语/完形填空高频固定搭配.md",   "TRN",   1),
    ("word&phrase/固定搭配&短语&俚语/*.md",                    "VOC",   3),
    # 语法与长难句
    ("grammar/考研英语语法笔记.md",                             "GRAM",  1),
    ("grammar/基础语法笔记.md",                                 "GRAM",  1),
    ("grammar/长难句笔记.md",                                   "GRAM",  2),
    # 阅读理解
    ("reading&magazines/同义替换.md",                           "READ",  1),
    ("reading&magazines/阅读专题/阅读专题总览.md",               "READ",  1),
    ("reading&magazines/阅读专题/**/专题_*.md",                 "READ",  3),
    ("reading&magazines/外刊/**/题源预测与精读书单.md",           "READ",  3),
    # 写作
    # ⚠️ 目录名是 translation&**writing**（有 ing）。2026-09-20 前这里写成 translation&write，
    #    与磁盘不匹配 → 这些规则恒匹配不到文件 →「写作 0 篇」；而大盘那边靠
    #    english_coverage.py 的题型证据另算一套，于是出现「写作 3/3 已覆盖」与
    #    「[WRITE] 0 entries」并存的怪象。tools/check_metrics_drift.py 会守住这条。
    #
    # 章号必须落在图谱真有的章上（英语写作上图只有 3 个考点：01 小作文 / 02 大作文 /
    # 03 写作积累与批改；翻译只有 ENG-TRAN-01 一个）。所以按内容分派：
    ("translation&writing/积累.md",                             "WRITE", 3),
    ("translation&writing/作文/图画作文/**/*.md",                "WRITE", 2),
    ("translation&writing/作文/应用文/**/*.md",                  "WRITE", 1),
    ("translation&writing/*批改记录.md",                        "WRITE", 3),
    # 翻译与完形
    ("translation&writing/翻译/**/*.md",                        "TRN",   1),
    ("past-papers/完形填空方法论.md",                            "TRN",   1),
    ("past-papers/真题笔记.md",                                 "TRN",   1),
]


def first_heading(path: Path, fallback: str) -> str:
    """取 Markdown 首个一级标题当条目名，取不到就用文件名。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for _ in range(40):
                line = f.readline()
                if not line:
                    break
                s = line.strip()
                if s.startswith("# "):
                    return s[2:].strip() or fallback
    except OSError:
        pass
    return fallback


def collect() -> dict:
    """按 RULES 扫出条目，按考点前缀分组。"""
    seen = {}
    for pattern, subkey, chapter in RULES:
        for path in sorted(ENG_DIR.glob(pattern)):
            if not path.is_file() or path.suffix.lower() != ".md":
                continue
            rel = path.relative_to(ENG_DIR).as_posix()
            if rel in seen:
                continue          # 已被更靠前的规则收走
            seen[rel] = (subkey, chapter, path)

    grouped = {k: [] for k in ENG_SUBS}
    for rel, (subkey, chapter, path) in sorted(seen.items()):
        grouped[subkey].append({
            "id": "",             # 分好组后按前缀编号，保证 id 稳定
            "date": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
            "title": first_heading(path, path.stem),
            "chapter": f"第{chapter}章",
            "level": "L2",
            "status": "已整理",
            "tags": [],
            "_source": rel,
        })

    for subkey, items in grouped.items():
        items.sort(key=lambda e: e["_source"])
        for i, e in enumerate(items, 1):
            e["id"] = f"ENG-{subkey}-{i:03d}"

    return {
        "generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "src/tools/gen_english_index.py",
        "subjects": {
            subkey: {
                "sub": ENG_SUBS[subkey],
                "entries": [
                    {k: v for k, v in e.items() if k != "_source"}
                    for e in grouped[subkey]
                ],
            }
            for subkey in ENG_SUBS
        },
    }


def main():
    data = collect()
    total = 0
    print("=" * 60)
    print("  gen_english_index.py — 英语笔记索引")
    print("=" * 60)
    for subkey, subj in data["subjects"].items():
        n = len(subj["entries"])
        total += n
        print(f"  [ENG-{subkey:<5}] {subj['sub']:<8} -> {n} 篇")
    print(f"  合计 {total} 篇 -> {OUT_PATH}")
    if total == 0:
        print("  ERROR: 一篇都没扫到，规则表可能和目录结构脱节了", file=sys.stderr)
        return 1
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())

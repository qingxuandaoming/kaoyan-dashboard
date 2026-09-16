#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_politics_anki.py — 政治闪卡 → Anki 可导入文件

链路：question_bank.db → 中间 JSON → src/tools/make_anki_cards.py → Anki CSV

为什么要这个中间层：make_anki_cards.py 已经是一个完备的题库 JSON → Anki
导出器（校验、去重自检、分栏模式都有）。这里只负责把 DB 的 questions 行
翻译成它认的 schema，避免重复造轮子。

用法：
    python src/export_politics_anki.py                # 全量政治卡，分栏模式
    python src/export_politics_anki.py --module MY    # 只导马原
    python src/export_politics_anki.py --single       # 单文件三列模式（不分栏）
"""

import argparse
import io
import json
import os
import re
import sqlite3
import subprocess
import sys

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SRC_DIR, "question_bank.db")
EXPORT_DIR = os.path.join(SRC_DIR, "flashcards", "export")
MAKE_ANKI = os.path.join(SRC_DIR, "tools", "make_anki_cards.py")

MODULE_NAMES = {"MY": "马原", "MZ": "毛中特", "SG": "史纲", "SX": "思修", "XX": "习思想"}

# 填空卡的挖空标记：库里有 ____ 与 {{c1::X}} 两种写法，统一成 ____
CLOZE_RE = re.compile(r"\{\{c\d+::(.*?)\}\}")
BLANK_RE = re.compile(r"_{2,}")


def module_of(topic_id, qid):
    """从 topic_id / question_id 里取模块码"""
    m = re.match(r"POL-([A-Z]{2})-", topic_id or "")
    if m:
        return m.group(1)
    m = re.match(r"Q-POL-([A-Z]{2})-", qid or "")
    return m.group(1) if m else ""


def to_anki_card(qtype, content, module_name, topic_name):
    """DB 行 → make_anki_cards.py 认的卡片 dict。不认得的题型返回 None。"""
    stem = content.get("stem") or content.get("question") or ""
    if not stem.strip():
        return None
    exp = content.get("explanation") or ""
    ch = module_name or "政治"

    if qtype == "choice":
        opts = content.get("options") or []
        ans = content.get("answer")
        if not isinstance(opts, list) or len(opts) < 2:
            return None
        if not isinstance(ans, int) or isinstance(ans, bool) or not (0 <= ans < len(opts)):
            return None
        return {"type": "choice", "ch": ch, "q": stem, "opts": opts, "ans": ans, "exp": exp}

    if qtype == "judge":
        ans = content.get("answer")
        if isinstance(ans, str):
            ans = ans.strip().lower() in ("true", "正确", "对", "1")
        elif ans in (0, 1) and not isinstance(ans, bool):
            ans = bool(ans)
        if not isinstance(ans, bool):
            return None
        return {"type": "tf", "ch": ch, "q": stem, "ans": ans, "exp": exp}

    if qtype == "fill":
        ans = content.get("answer")
        if isinstance(ans, list):
            ans = [str(a) for a in ans if str(a).strip()]
            if not ans:
                return None
        elif isinstance(ans, str) and ans.strip():
            ans = [ans]
        else:
            return None
        # cloze 语法统一成 ____
        q = CLOZE_RE.sub("____", stem)
        if not BLANK_RE.search(q):
            q = q.rstrip("。？?") + "____。"
        return {"type": "fill", "ch": ch, "q": q, "ans": ans, "exp": exp}

    return None  # short_answer / essay 不导出（练习区不支持交互作答）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", default=None, help="只导某模块：MY/MZ/SG/SX/XX")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--single", action="store_true", help="单文件三列模式（不分栏）")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    sql = """
        SELECT q.id, q.type, q.content, q.topic_id, t.name
          FROM questions q JOIN topics t ON q.topic_id = t.id
         WHERE t.subject = '政治'
         ORDER BY q.id
    """
    rows = conn.execute(sql).fetchall()
    conn.close()

    cards = []
    seen = set()
    skipped = {"type": 0, "dup": 0, "bad": 0}
    for qid, qtype, raw, topic_id, topic_name in rows:
        mod = module_of(topic_id, qid)
        if args.module and mod != args.module:
            continue
        try:
            content = json.loads(raw)
        except Exception:
            skipped["bad"] += 1
            continue
        c = to_anki_card(qtype, content, MODULE_NAMES.get(mod, "政治"), topic_name)
        if c is None:
            skipped["type"] += 1
            continue
        # make_anki_cards.py 遇重复 front 会整体报错退出，所以必须先自行去重
        key = re.sub(r"\s+", "", c["q"])[:80]
        if key in seen:
            skipped["dup"] += 1
            continue
        seen.add(key)
        cards.append(c)

    if not cards:
        print("[FAIL] 没有可导出的卡片")
        return 1

    os.makedirs(EXPORT_DIR, exist_ok=True)
    suffix = f"_{args.module}" if args.module else "_all"
    mid_json = os.path.join(EXPORT_DIR, f"politics{suffix}.json")
    out_csv = os.path.join(EXPORT_DIR, f"politics{suffix}_Anki.csv")

    with io.open(mid_json, "w", encoding="utf-8") as f:
        json.dump({"cards": cards}, f, ensure_ascii=False, indent=1)

    dist = {}
    for c in cards:
        dist[c["type"]] = dist.get(c["type"], 0) + 1
    print(f"从 DB 取出政治题 {len(rows)} 条")
    print(f"  可导出 {len(cards)} 张（{dist}）")
    print(f"  跳过：题型不支持 {skipped['type']}、重复 {skipped['dup']}、解析失败 {skipped['bad']}")
    print(f"  中间 JSON：{mid_json}")

    cmd = [sys.executable, MAKE_ANKI, mid_json, out_csv, "考研政治"]
    if not args.single:
        cmd.append("--fields")
    print(f"\n调用导出器：{' '.join(cmd[1:])}\n")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    print(r.stdout or "")
    if r.returncode != 0:
        print("[FAIL] 导出器报错：", r.stderr or "")
        return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())

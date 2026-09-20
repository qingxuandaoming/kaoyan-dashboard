#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_week_cards.py — 每周错题复盘补卡 → question_bank.db 导入器（通用版）

为什么需要它
------------
政治走 import_politics_cards.py，其余科目原本只能靠 generate_targeted_cards.py
（依赖已登录的 bl CLI 出题）。每周复盘补卡是「人工/agent 定稿」的内容，不需要模型生成，
只需要一套可靠的入库逻辑。本脚本就是那套逻辑：与政治导入器同构，但科目可配。

数据来源：src/flashcards/source/*.json
    {"meta": {"subject":"英语一","module":"ENG-VOC","batch":"...","note":"..."},
     "cards": [ {"type":"choice","topic_id":"ENG-VOC-01-03","q":"…","opts":[…],"ans":1,
                 "exp":"…","tags":[…],"traps":[…]} , … ]}

设计要点
--------
1. **幂等**：ext_key = sha1(科目::归一化题干)，配已有的 ext_key 查询；
   另有「同考点下题干前 40 字」兜底查重，兼容历史卡。重复运行零新增。
2. **考点不落空**：显式 topic_id → 名称精确 → 名称包含 → 该科目权重最高的考点（兜底），
   绝不写 NULL。
3. **dry-run 先行**：只出映射报告与查重结果，不写库。
4. **只增不改**：不触碰既有行，也不动 FSRS 状态。

用法
----
    python src/import_week_cards.py --dry-run                       # 审全部
    python src/import_week_cards.py                                 # 正式导入
    python src/import_week_cards.py --file src/flashcards/source/math_week_0920.json
"""

import argparse
import glob
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SRC_DIR, "question_bank.db")
SOURCE_GLOB = os.path.join(SRC_DIR, "flashcards", "source", "*.json")

# 已有专属导入器的文件交给它自己处理，避免两份逻辑互相打架
EXCLUDE_BASENAMES = {"politics_my.json", "politics_mz.json", "politics_sg.json",
                     "politics_sx.json", "politics_xx.json"}

# meta.module → 卡号里的短代号
MODULE_SLUG = {
    "ENG-VOC": "ENGVOC", "ENG-GRAM": "ENGGRAM", "ENG-READ": "ENGREAD",
    "ENG-TRAN": "ENGTRAN", "ENG-WRITE": "ENGWRITE", "ENG-CLOZE": "ENGCLOZE",
    "MATH": "MATH", "CS408": "408", "POL": "POL",
}


# ---------------------------------------------------------------------------
# 题干归一化与 ext_key（与 import_politics_cards.py 保持同一口径）
# ---------------------------------------------------------------------------
_PUNCT_RE = re.compile(r"[\s　,，.。;；:：!！?？\"'“”‘’()（）\[\]【】<>《》、·\-—_]+")


def normalize_stem(s: str) -> str:
    if not s:
        return ""
    out = []
    for ch in s:
        code = ord(ch)
        out.append(chr(code - 0xFEE0) if 0xFF01 <= code <= 0xFF5E else ch)
    return _PUNCT_RE.sub("", "".join(out)).strip().lower()


def make_ext_key(subject: str, stem: str) -> str:
    return hashlib.sha1(f"{subject}::{normalize_stem(stem)}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 校验（完全复用政治导入器的规则，保证两边的卡结构一致）
# ---------------------------------------------------------------------------
def validate_card(idx, c, errors):
    where = f"第{idx+1}张"
    if not isinstance(c, dict):
        errors.append(f"{where}: 不是对象")
        return False
    t = c.get("type")
    if t not in ("choice", "tf", "fill"):
        errors.append(f"{where}: type 非法 '{t}'（只能是 choice/tf/fill）")
        return False
    if not c.get("q") or not isinstance(c["q"], str):
        errors.append(f"{where}: q 缺失或非字符串")
        return False
    if t == "choice":
        opts, ans = c.get("opts"), c.get("ans")
        if not isinstance(opts, list) or not (2 <= len(opts) <= 6):
            errors.append(f"{where}: choice 的 opts 必须是 2-6 项数组")
            return False
        if any((not isinstance(o, str)) or not o.strip() for o in opts):
            errors.append(f"{where}: choice 的 opts 存在空项")
            return False
        if not isinstance(ans, int) or isinstance(ans, bool) or not (0 <= ans < len(opts)):
            errors.append(f"{where}: choice 的 ans 必须是 0..{len(opts)-1} 的整数")
            return False
    elif t == "tf":
        if not isinstance(c.get("ans"), bool):
            errors.append(f"{where}: tf 的 ans 必须是布尔值")
            return False
    else:
        ans = c.get("ans")
        if isinstance(ans, str):
            if not ans.strip():
                errors.append(f"{where}: fill 的 ans 为空")
                return False
        elif isinstance(ans, list):
            if not ans or any((not isinstance(a, str)) or not a.strip() for a in ans):
                errors.append(f"{where}: fill 的 ans 数组存在空项")
                return False
        else:
            errors.append(f"{where}: fill 的 ans 必须是字符串或字符串数组")
            return False
    exp = c.get("exp")
    if exp is not None and not isinstance(exp, str):
        errors.append(f"{where}: exp 必须是字符串")
        return False
    return True


# ---------------------------------------------------------------------------
# 考点解析
# ---------------------------------------------------------------------------
def load_topics(conn, subject):
    rows = conn.execute(
        "SELECT id, name, exam_weight FROM topics WHERE subject = ?", (subject,)
    ).fetchall()
    by_id, by_name = {}, {}
    for tid, name, w in rows:
        item = {"id": tid, "name": name, "weight": w or 0.0}
        by_id[tid] = item
        by_name.setdefault(name, []).append(item)
    return by_id, by_name


def resolve_topic(card, by_id, by_name, fallback):
    tid = card.get("topic_id")
    if tid and tid in by_id:
        return by_id[tid], "topic_id"
    name = (card.get("topic") or "").strip()
    if name and name in by_name:
        return by_name[name][0], "name"
    if len(name) >= 3:
        row = by_id.get(name)
        if row:
            return row, "name"
        hits = [t for t in by_id.values() if name in t["name"] or t["name"] in name]
        if hits:
            hits.sort(key=lambda t: -t["weight"])
            return hits[0], "contains"
    return fallback, "fallback"


def next_seq(conn, prefix):
    mx = 0
    for (qid,) in conn.execute("SELECT id FROM questions WHERE id LIKE ?", (prefix + "%",)):
        m = re.search(r"-(\d+)$", qid)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1


def main():
    ap = argparse.ArgumentParser(description="每周复盘补卡导入器（通用）")
    ap.add_argument("--dry-run", action="store_true", help="只出映射/查重报告，不写库")
    ap.add_argument("--file", action="append", default=None, help="指定源文件（可多次）")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--glob", default=SOURCE_GLOB, help="默认扫描通配（默认全部 source/*.json）")
    args = ap.parse_args()

    files = args.file or sorted(glob.glob(args.glob))
    files = [f for f in files if os.path.basename(f) not in EXCLUDE_BASENAMES]
    if not files:
        print(f"[FAIL] 未找到源文件：{args.glob}")
        return 1

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA busy_timeout = 8000")

    topic_cache, fallback_cache = {}, {}
    total = inserted_q = inserted_c = skipped = 0
    all_errors, dup_report = [], []
    map_stats = defaultdict(int)
    covered = set()
    subject_counts = defaultdict(int)

    for path in files:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        meta = data.get("meta") or {}
        subject = meta.get("subject") or ""
        module = meta.get("module") or ""
        batch = meta.get("batch") or os.path.splitext(os.path.basename(path))[0]
        slug = MODULE_SLUG.get(module, re.sub(r"[^A-Za-z0-9]", "", module) or "MISC")
        cards = data.get("cards") or []

        if not subject:
            all_errors.append(f"{os.path.basename(path)}: meta.subject 缺失")
            continue

        if subject not in topic_cache:
            by_id0, by_name0 = load_topics(conn, subject)
            best = None
            for t in by_id0.values():
                if best is None or t["weight"] > best["weight"]:
                    best = t
            topic_cache[subject] = (by_id0, by_name0)
            fallback_cache[subject] = best
        by_id, by_name = topic_cache[subject]
        fallback = fallback_cache[subject]

        print(f"\n[{subject}] {os.path.basename(path)} — {len(cards)} 张")

        for i, c in enumerate(cards):
            total += 1
            if not validate_card(i, c, all_errors):
                continue
            topic, how = resolve_topic(c, by_id, by_name, fallback)
            if topic is None:
                all_errors.append(f"第{i+1}张: 无法解析考点（{subject} 无可用兜底）")
                continue
            map_stats[f"{subject}:{how}"] += 1
            covered.add(topic["id"])
            subject_counts[subject] += 1

            stem = c["q"]
            ext = make_ext_key(subject, stem)
            if args.dry_run:
                dup = conn.execute(
                    "SELECT id, topic_id FROM questions WHERE ext_key = ?", (ext,)
                ).fetchone()
                if dup is None:
                    dup = conn.execute(
                        "SELECT id, topic_id FROM questions WHERE topic_id = ? "
                        "AND REPLACE(content,' ','') LIKE ?",
                        (topic["id"], f"%{stem[:40]}%"),
                    ).fetchone()
                dup_report.append(
                    f"  [{'跳过·重复' if dup else '待入库'}] {topic['id']} "
                    f"({how}) {stem[:46]}" + (f"  ← 已存在 {dup[0]}" if dup else "")
                )
                continue

            dup = conn.execute("SELECT id FROM questions WHERE ext_key = ?", (ext,)).fetchone()
            if dup is None:
                dup = conn.execute(
                    "SELECT id FROM questions WHERE topic_id = ? "
                    "AND REPLACE(content,' ','') LIKE ?",
                    (topic["id"], f"%{stem[:40]}%"),
                ).fetchone()
            if dup:
                skipped += 1
                continue

            seq = next_seq(conn, f"Q-WK-{slug}-")
            qid = f"Q-WK-{slug}-{seq:04d}"
            cid = f"C-WK-{slug}-{seq:04d}"

            t = c["type"]
            if t == "choice":
                content = {"stem": stem, "options": c["opts"], "answer": int(c["ans"]),
                           "explanation": c.get("exp", ""), "tags": c.get("tags", []),
                           "traps": c.get("traps", [])}
                db_type = "choice"
            elif t == "tf":
                content = {"stem": stem, "options": ["正确", "错误"], "answer": bool(c["ans"]),
                           "explanation": c.get("exp", ""), "tags": c.get("tags", []),
                           "traps": c.get("traps", [])}
                db_type = "judge"
            else:
                a = c["ans"]
                content = {"stem": stem, "answer": a[0] if isinstance(a, list) else a,
                           "explanation": c.get("exp", ""), "tags": c.get("tags", []),
                           "traps": c.get("traps", [])}
                db_type = "fill"

            conn.execute(
                "INSERT INTO questions (id, topic_id, type, difficulty, source, content, ext_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (qid, topic["id"], db_type, 0.5, f"每周复盘补卡-{batch}",
                 json.dumps(content, ensure_ascii=False), ext),
            )
            inserted_q += 1
            conn.execute(
                "INSERT INTO cards (id, question_id, state, due_date, due_at, queue, interval_days) "
                "VALUES (?, ?, 0, date('now','localtime'), datetime('now','localtime'), 0, 0)",
                (cid, qid),
            )
            inserted_c += 1

    if not args.dry_run:
        conn.commit()

    print("\n" + "=" * 64)
    print(f"扫描 {len(files)} 个文件，共 {total} 张卡")
    print("映射命中方式：", dict(map_stats))
    for s, n in sorted(subject_counts.items()):
        print(f"  {s}：{n} 张")
    if args.dry_run:
        print(f"\n[DRY-RUN] 未写库。将覆盖 {len(covered)} 个考点：")
        for line in dup_report:
            print(line)
    else:
        print(f"\n新增题目 {inserted_q} 条，新增卡片 {inserted_c} 条，跳过重复 {skipped} 条")

    if all_errors:
        print(f"\n[FAIL] {len(all_errors)} 处校验错误：")
        for e in all_errors[:40]:
            print("   -", e)
        conn.close()
        return 1

    conn.close()
    print("\n[OK] 校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

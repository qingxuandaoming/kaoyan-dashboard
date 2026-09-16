#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_politics_cards.py — 政治核心闪卡 → question_bank.db 导入器

数据来源：src/flashcards/source/politics_*.json（每模块一个文件，便于维护）
产出：questions 表 + cards 表各新增若干行，参与大盘闪卡练习区的 FSRS 调度。

设计要点
--------
1. **幂等**：以 ext_key = sha1(模块 + 归一化题干) 为唯一键，配 INSERT OR IGNORE。
   重复运行零新增。另有 content LIKE 兜底查重，兼容既有历史数据。
2. **考点映射绝不落空**：显式 topic_id 优先，逐级回退到「模块内权重最高的考点」，
   保证不会产生 topic_id 为 NULL 的孤儿卡。
3. **dry-run 先行**：--dry-run 只出映射报告与校验结果，不写库。

用法
----
    python src/import_politics_cards.py --dry-run        # 审映射与校验
    python src/import_politics_cards.py                  # 正式导入
    python src/import_politics_cards.py --file src/flashcards/source/politics_my.json
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
ROOT_DIR = os.path.dirname(SRC_DIR)
DB_PATH = os.path.join(SRC_DIR, "question_bank.db")
SOURCE_GLOB = os.path.join(SRC_DIR, "flashcards", "source", "politics_*.json")

MODULE_MAP = {"马原": "MY", "毛中特": "MZ", "史纲": "SG", "思修": "SX", "习思想": "XX"}


# ---------------------------------------------------------------------------
# 题干归一化与 ext_key
# ---------------------------------------------------------------------------
_PUNCT_RE = re.compile(r"[\s　,，.。;；:：!！?？\"'“”‘’()（）\[\]【】<>《》、·\-—_]+")


def normalize_stem(s: str) -> str:
    """去空白、去标点、全角转半角，用于生成稳定的去重键。"""
    if not s:
        return ""
    # 全角字母数字 → 半角
    out = []
    for ch in s:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    t = "".join(out)
    t = _PUNCT_RE.sub("", t)
    return t.strip().lower()


def make_ext_key(module: str, stem: str) -> str:
    return hashlib.sha1(f"{module}::{normalize_stem(stem)}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def validate_card(idx, c, errors):
    """逐卡校验，返回 (ok, normalized)。错误累积在 errors 里。"""
    where = f"第{idx+1}张"
    if not isinstance(c, dict):
        errors.append(f"{where}: 不是对象")
        return None
    t = c.get("type")
    q = c.get("q")
    ch = c.get("ch")
    if t not in ("choice", "tf", "fill"):
        errors.append(f"{where}: type 非法 '{t}'（只能是 choice/tf/fill）")
        return None
    if not q or not isinstance(q, str):
        errors.append(f"{where}: q 缺失或非字符串")
        return None
    if not ch or not isinstance(ch, str):
        errors.append(f"{where}: ch 缺失")
        return None

    if t == "choice":
        opts = c.get("opts")
        ans = c.get("ans")
        if not isinstance(opts, list) or not (2 <= len(opts) <= 6):
            errors.append(f"{where}: choice 的 opts 必须是 2-6 项数组")
            return None
        if any((not isinstance(o, str)) or not o.strip() for o in opts):
            errors.append(f"{where}: choice 的 opts 存在空项")
            return None
        if not isinstance(ans, int) or isinstance(ans, bool) or not (0 <= ans < len(opts)):
            errors.append(f"{where}: choice 的 ans 必须是 0..{len(opts)-1} 的整数")
            return None
    elif t == "tf":
        if not isinstance(c.get("ans"), bool):
            errors.append(f"{where}: tf 的 ans 必须是布尔值")
            return None
    else:  # fill
        ans = c.get("ans")
        if isinstance(ans, str):
            if not ans.strip():
                errors.append(f"{where}: fill 的 ans 为空")
                return None
        elif isinstance(ans, list):
            if not ans or any((not isinstance(a, str)) or not a.strip() for a in ans):
                errors.append(f"{where}: fill 的 ans 数组存在空项")
                return None
        else:
            errors.append(f"{where}: fill 的 ans 必须是字符串或字符串数组")
            return None

    exp = c.get("exp")
    if exp is not None and not isinstance(exp, str):
        errors.append(f"{where}: exp 必须是字符串")
        return None
    return True


# ---------------------------------------------------------------------------
# 考点映射
# ---------------------------------------------------------------------------
def load_topics(conn):
    rows = conn.execute(
        "SELECT id, name, subject, chapter, exam_weight FROM topics WHERE subject='政治'"
    ).fetchall()
    by_id = {}
    by_name = {}
    for tid, name, subj, chapter, w in rows:
        by_id[tid] = {"id": tid, "name": name, "chapter": chapter, "weight": w or 0.0}
        by_name.setdefault(name, []).append(by_id[tid])
    return by_id, by_name


def resolve_topic(card, module_code, by_id, by_name, fallback):
    """
    返回 (topic_dict, 命中方式)。
    顺序：显式 topic_id → 名称精确 → 名称包含 → 模块内权重最高的一级考点。
    """
    tid = card.get("topic_id")
    if tid and tid in by_id:
        return by_id[tid], "topic_id"

    name = (card.get("topic") or "").strip()
    if name and name in by_name:
        return by_name[name][0], "name"

    if name:
        for t in by_id.values():
            if t["id"].startswith(f"POL-{module_code}-") and (name in t["name"] or t["name"] in name):
                return t, "contains"

    # 兜底：该模块权重最高的一级考点（绝不写 NULL）
    return fallback, "fallback"


# ---------------------------------------------------------------------------
# ID 生成
# ---------------------------------------------------------------------------
def topic_prefix(topic_id):
    """POL-MY-05-01 → ('MY','05')；POL-MY-05 → ('MY','05')"""
    parts = topic_id.split("-")
    if len(parts) >= 3:
        return parts[1], parts[2]
    return None, None


def next_seq(conn, module_code, nn):
    rows = conn.execute(
        "SELECT id FROM questions WHERE id LIKE ?", (f"Q-POL-{module_code}-{nn}-%",)
    ).fetchall()
    mx = 0
    for (qid,) in rows:
        m = re.search(r"-(\d+)$", qid)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只出映射报告，不写库")
    ap.add_argument("--file", action="append", default=None, help="指定源文件（可多次）")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    files = args.file or sorted(glob.glob(SOURCE_GLOB))
    if not files:
        print(f"[FAIL] 未找到源文件：{SOURCE_GLOB}")
        return 1

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA busy_timeout = 8000")
    by_id, by_name = load_topics(conn)

    # 每个模块权重最高的一级考点，作为兜底锚点
    fallback_by_module = {}
    for t in by_id.values():
        mod, nn = topic_prefix(t["id"])
        if not mod or "-" in t["id"][len(f"POL-{mod}-"):]:
            continue  # 只要一级考点（形如 POL-MY-05）
        cur = fallback_by_module.get(mod)
        if cur is None or t["weight"] > cur["weight"]:
            fallback_by_module[mod] = t

    total = inserted_q = inserted_c = skipped = 0
    all_errors = []
    map_stats = defaultdict(int)
    covered_topics = set()

    for path in files:
        with io.open(path, encoding="utf-8") as f:
            data = json.load(f)
        module = (data.get("meta") or {}).get("module") or ""
        module_code = MODULE_MAP.get(module)
        if not module_code:
            all_errors.append(f"{os.path.basename(path)}: meta.module '{module}' 无法映射到科目码")
            continue
        cards = data.get("cards") or []
        fallback = fallback_by_module.get(module_code)
        print(f"\n[{module}] {os.path.basename(path)} — {len(cards)} 张")

        for i, c in enumerate(cards):
            total += 1
            if validate_card(i, c, all_errors) is None:
                continue
            topic, how = resolve_topic(c, module_code, by_id, by_name, fallback)
            if topic is None:
                all_errors.append(f"第{i+1}张: 无法解析考点（模块 {module_code} 无可用兜底）")
                continue
            map_stats[how] += 1
            covered_topics.add(topic["id"])

            stem = c["q"]
            ext = make_ext_key(module_code, stem)

            if args.dry_run:
                continue

            # 幂等：ext_key 唯一 + 题干兜底查重
            dup = conn.execute("SELECT id FROM questions WHERE ext_key = ?", (ext,)).fetchone()
            if dup is None:
                like = "%" + normalize_stem(stem)[:40] + "%"
                dup = conn.execute(
                    "SELECT id FROM questions WHERE topic_id = ? AND REPLACE(content,' ','') LIKE ?",
                    (topic["id"], f"%{stem[:40]}%"),
                ).fetchone()
            if dup:
                skipped += 1
                continue

            _, nn = topic_prefix(topic["id"])
            seq = next_seq(conn, module_code, nn)
            qid = f"Q-POL-{module_code}-{nn}-{seq:04d}"
            cid = f"C-POL-{module_code}-{nn}-{seq:04d}"

            t = c["type"]
            if t == "choice":
                content = {
                    "stem": stem, "options": c["opts"], "answer": int(c["ans"]),
                    "explanation": c.get("exp", ""),
                    "tags": c.get("tags", []), "traps": c.get("traps", []),
                }
                db_type = "choice"
            elif t == "tf":
                content = {
                    "stem": stem, "options": ["正确", "错误"], "answer": bool(c["ans"]),
                    "explanation": c.get("exp", ""), "tags": c.get("tags", []),
                }
                db_type = "judge"
            else:
                a = c["ans"]
                content = {
                    "stem": stem, "answer": a[0] if isinstance(a, list) else a,
                    "explanation": c.get("exp", ""), "tags": c.get("tags", []),
                }
                db_type = "fill"

            conn.execute(
                "INSERT INTO questions (id, topic_id, type, difficulty, source, content, ext_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (qid, topic["id"], db_type, 0.5, "政治核心卡-core_v1",
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

    # ---- 报告 ----
    print("\n" + "=" * 60)
    print(f"扫描 {len(files)} 个文件，共 {total} 张卡")
    print("映射命中方式：", dict(map_stats))
    if args.dry_run:
        print(f"[DRY-RUN] 未写库。将覆盖 {len(covered_topics)} 个考点。")
    else:
        print(f"新增题目 {inserted_q} 条，新增卡片 {inserted_c} 条，跳过重复 {skipped} 条")

    # 高频考点覆盖自检
    # 干跑时库里没有本次的新卡，必须用映射结果统计；正式导入后才查库。
    hi = conn.execute(
        "SELECT id,name FROM topics WHERE subject='政治' AND exam_weight>=2 ORDER BY id"
    ).fetchall()
    if args.dry_run:
        have = set(covered_topics)
    else:
        have = {r[0] for r in conn.execute(
            "SELECT DISTINCT topic_id FROM questions q JOIN topics t ON q.topic_id=t.id WHERE t.subject='政治'"
        ).fetchall()}
    missing = [f"{i}({n})" for i, n in hi if i not in have]
    print(f"\nexam_weight>=2 考点覆盖：{len(hi) - len(missing)}/{len(hi)}")
    if missing:
        print("  未覆盖：")
        for m in missing:
            print("    -", m)

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

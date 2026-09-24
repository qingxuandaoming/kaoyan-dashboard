#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_deck_cards.py — 专题卡组（html-flashcard-builder 产物）→ question_bank.db

**为什么并库**：大盘上那块「闪卡库 · 专题卡组（莫兰迪）」画廊是 iframe 加载的
独立 HTML 应用，**没有间隔重复**，进度只存 localStorage，而且全库只有 1 个卡组
12 道题，却显示「已掌握 8/12」。主库 meanwhile 有 413 张走 FSRS-6 的真卡。
这个脚本把卡组的题目并进主库，让它们真正参与调度。

**与 import_politics_cards.py 的关系**：那边整套是政治专用的（MODULE_MAP、
POL- 前缀、subject='政治' 写死），参数化要动六处、回归面大。这里只 import 它的
两个纯函数（题干归一化 + ext_key），其余另写。幂等手段完全一致：ext_key 唯一键
+ 题干兜底查重，重复跑零新增。

用法：
    python src/import_deck_cards.py --dry-run     # 审映射与校验，不写库
    python src/import_deck_cards.py               # 正式导入
    python src/import_deck_cards.py --rollback    # 按 source 前缀整体撤销
"""

import argparse
import io
import json
import os
import re
import sqlite3
import sys

# ⚠️ 必须先 import 它：这个模块在 import 时会把 sys.stdout 包成 UTF-8。
# 我们再包一次的话两个 wrapper 会共享同一个 buffer，先被 GC 的那个会把 buffer 关掉，
# 于是报 "I/O operation on closed file"（weekly_flashcard_report.py 踩过这个坑）。
from import_politics_cards import make_ext_key, normalize_stem  # noqa: E402

_STDIO_REFS = (sys.stdout, sys.stderr)   # 持有引用，防止被回收

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.environ.get("NOTES_ROOT", r"E:\NPEE")   # 笔记库根（2026-09-25 起与代码根分离，可用环境变量 NOTES_ROOT 覆盖）
DB_PATH = os.path.join(SRC_DIR, "question_bank.db")

SOURCE_PREFIX = "deck:"

# 卡组登记表。subject/code 决定落到哪个科目与 ID 前缀；
# ch_topic 是**显式映射**——deck 的 ch 粒度很粗，且「导数与微分」与主库考点
# 「一元函数微分学」互不包含，靠字符串匹配根本对不上，所以显式表是主路径。
DECKS = [
    {
        "path": os.path.join(SRC_DIR, "flashcards", "decks", "kaoyan_math_gs_basic_v1.json"),
        "subject": "数学一", "code": "MATH",
        "ch_topic": {"极限与连续": "MATH-GS-01", "导数与微分": "MATH-GS-02", "积分": "MATH-GS-03"},
        "fallback": "MATH-GS-01",
    },
    {
        "path": os.path.join(ROOT_DIR, "Math", "题库", "微分方程40题.json"),
        "subject": "数学一", "code": "MATH",
        "ch_topic": {"判断类型": "MATH-GS-07", "解的形式": "MATH-GS-07"},
        "fallback": "MATH-GS-07",
    },
]

# 卡组题型 → 主库题型。两套词汇：deck 用 tf/short，主库用 judge/fill。
# short 导成 fill 是因为闪卡练习区对非 choice/judge 一律走「显示答案 + 自评」，
# 与既有 34 张 fill 完全同路径，不导白不导。
TYPE_MAP = {"choice": "choice", "tf": "judge", "fill": "fill", "short": "fill"}


# ---------------------------------------------------------------------------
# 内联 HTML → 纯文本
# ---------------------------------------------------------------------------
# 微分方程那组题干/解析带 <span class='eq fx'>、<sup>、<br> 等标签（原 HTML 闪卡
# 用它们排版）。闪卡练习区是拿 esc() 当**纯文本**渲染的，不转的话用户看到的是
# 一堆尖括号标签。这里的取舍是转成可读纯文本，而不是引入一套公式渲染。
def html_to_text(s):
    if not s:
        return ""
    t = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.I)
    t = re.sub(r"<\s*sup\s*>(.*?)<\s*/\s*sup\s*>", r"^(\1)", t, flags=re.I | re.S)
    t = re.sub(r"<\s*sub\s*>(.*?)<\s*/\s*sub\s*>", r"_(\1)", t, flags=re.I | re.S)
    t = re.sub(r"<[^>]+>", "", t)                       # 其余标签（span/b/…）脱掉
    t = t.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">")
    t = t.replace("&amp;", "&")                         # & 放最后，避免二次反转义
    return t.strip()


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def validate(idx, c, errors):
    where = "第%d张" % (idx + 1)
    if not isinstance(c, dict):
        errors.append("%s: 不是对象" % where)
        return False
    t = c.get("type")
    q = c.get("q")
    if t not in TYPE_MAP:
        errors.append("%s: type 非法 '%s'（只能是 choice/tf/fill/short）" % (where, t))
        return False
    if not q or not str(q).strip():
        errors.append("%s: q 缺失或为空" % where)
        return False
    if t == "choice":
        opts, ans = c.get("opts"), c.get("ans")
        if not isinstance(opts, list) or not (2 <= len(opts) <= 6):
            errors.append("%s: choice 的 opts 必须是 2-6 项数组" % where)
            return False
        if any((not isinstance(o, str)) or not o.strip() for o in opts):
            errors.append("%s: choice 的 opts 存在空项" % where)
            return False
        if not isinstance(ans, int) or isinstance(ans, bool) or not (0 <= ans < len(opts)):
            errors.append("%s: choice 的 ans 必须是 0..%d 的整数" % (where, len(opts) - 1))
            return False
    elif t == "tf":
        if not isinstance(c.get("ans"), bool):
            errors.append("%s: tf 的 ans 必须是布尔值" % where)
            return False
    elif t == "fill":
        ans = c.get("ans")
        if isinstance(ans, str):
            if not ans.strip():
                errors.append("%s: fill 的 ans 为空" % where)
                return False
        elif isinstance(ans, list):
            if not ans or any((not isinstance(a, str)) or not a.strip() for a in ans):
                errors.append("%s: fill 的 ans 数组存在空项" % where)
                return False
        else:
            errors.append("%s: fill 的 ans 必须是字符串或字符串数组" % where)
            return False
    else:  # short
        ans = c.get("ans")
        if not isinstance(ans, str) or not ans.strip():
            errors.append("%s: short 的 ans 必须是非空字符串" % where)
            return False
    if c.get("exp") is not None and not isinstance(c.get("exp"), str):
        errors.append("%s: exp 必须是字符串" % where)
        return False
    return True


# ---------------------------------------------------------------------------
# ID 生成
# ---------------------------------------------------------------------------
def topic_parts(topic_id):
    """MATH-GS-01 → ('GS','01')；MATH-XD-03-02 → ('XD','03')（只取到一级）"""
    parts = topic_id.split("-")
    return (parts[1], parts[2]) if len(parts) >= 3 else (None, None)


def next_seq(conn, code, mod, nn):
    rows = conn.execute(
        "SELECT id FROM questions WHERE id LIKE ?", ("Q-%s-%s-%s-%%" % (code, mod, nn),)
    ).fetchall()
    mx = 0
    for (qid,) in rows:
        m = re.search(r"-(\d+)$", qid)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1


# ---------------------------------------------------------------------------
# 顺带修孤儿卡
# ---------------------------------------------------------------------------
# 3 张英语作文卡的 topic_id 指向不存在的 ENG-WRT-01/02，导致按科目筛选时
# LEFT JOIN 得到空 subject、INNER JOIN 直接丢。topics 里本来就有语义精确对应的
# ENG-WRITE-01（小作文）/ ENG-WRITE-02（大作文），UPDATE 即可，不必新建考点。
ORPHAN_FIX = {"ENG-WRT-01": "ENG-WRITE-01", "ENG-WRT-02": "ENG-WRITE-02"}


def fix_orphans(conn, dry_run):
    fixed = []
    for bad, good in ORPHAN_FIX.items():
        n = conn.execute("SELECT COUNT(*) FROM questions WHERE topic_id = ?", (bad,)).fetchone()[0]
        if not n:
            continue
        ok = conn.execute("SELECT COUNT(*) FROM topics WHERE id = ?", (good,)).fetchone()[0]
        if not ok:
            print("  [WARN] 目标考点 %s 不存在，跳过 %s 的 %d 张" % (good, bad, n))
            continue
        if not dry_run:
            conn.execute("UPDATE questions SET topic_id = ? WHERE topic_id = ?", (good, bad))
        fixed.append("%s → %s（%d 张）" % (bad, good, n))
    return fixed


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def build_content(t, c):
    stem = html_to_text(c["q"])
    exp = html_to_text(c.get("exp", ""))
    tags = [c["ch"]] if c.get("ch") else []
    if t == "choice":
        return "choice", {
            "stem": stem, "options": [html_to_text(o) for o in c["opts"]],
            "answer": int(c["ans"]), "explanation": exp, "tags": tags,
        }
    if t == "tf":
        return "judge", {
            "stem": stem, "options": ["正确", "错误"], "answer": bool(c["ans"]),
            "explanation": exp, "tags": tags,
        }
    if t == "fill":
        a = c["ans"]
        content = {"stem": stem, "explanation": exp, "tags": tags}
        if isinstance(a, list):
            content["answer"] = html_to_text(a[0])
            if len(a) > 1:
                content["alt_answers"] = [html_to_text(x) for x in a[1:]]
        else:
            content["answer"] = html_to_text(a)
        return "fill", content
    # short → fill：练习区的作答路径与 fill 完全一致
    return "fill", {
        "stem": stem, "answer": html_to_text(c["ans"]),
        "explanation": exp, "tags": tags,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只出报告，不写库")
    ap.add_argument("--rollback", action="store_true",
                    help="删除所有 source 以 'deck:' 开头的题与卡（并库的整体回滚）")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA busy_timeout = 8000")

    if args.rollback:
        n_c = conn.execute(
            "DELETE FROM cards WHERE question_id IN (SELECT id FROM questions WHERE source LIKE ?)",
            (SOURCE_PREFIX + "%",)).rowcount
        n_q = conn.execute("DELETE FROM questions WHERE source LIKE ?", (SOURCE_PREFIX + "%",)).rowcount
        conn.commit()
        conn.close()
        print("[OK] 已回滚：删除题目 %d 条、卡片 %d 条" % (n_q, n_c))
        return 0

    total = inserted_q = inserted_c = skipped = 0
    errors = []
    hit_stats = {}
    covered = set()

    for deck in DECKS:
        if not os.path.exists(deck["path"]):
            errors.append("找不到卡组文件：%s" % deck["path"])
            continue
        with io.open(deck["path"], encoding="utf-8") as f:
            data = json.load(f)
        key = (data.get("meta") or {}).get("storageKey") or os.path.basename(deck["path"])
        cards = data.get("cards") or []
        print("\n[%s] %d 张 — %s" % (key, len(cards), (data.get("meta") or {}).get("title", "")))

        for i, c in enumerate(cards):
            total += 1
            if not validate(i, c, errors):
                continue
            ch = c.get("ch") or ""
            topic_id = deck["ch_topic"].get(ch)
            how = "ch_topic"
            if not topic_id:
                # 显式表没命中就落兜底考点，绝不写 NULL（孤儿卡就是这么来的）
                topic_id = deck["fallback"]
                how = "fallback"
            hit_stats[how] = hit_stats.get(how, 0) + 1
            covered.add(topic_id)

            stem = html_to_text(c["q"])
            ext = make_ext_key(key, stem)

            if args.dry_run:
                continue

            dup = conn.execute("SELECT id FROM questions WHERE ext_key = ?", (ext,)).fetchone()
            if dup is None:
                dup = conn.execute(
                    "SELECT id FROM questions WHERE topic_id = ? AND REPLACE(content,' ','') LIKE ?",
                    (topic_id, "%" + stem[:40].replace(" ", "") + "%"),
                ).fetchone()
            if dup:
                skipped += 1
                continue

            mod, nn = topic_parts(topic_id)
            if not mod:
                errors.append("考点 id 形态异常：%s" % topic_id)
                continue
            seq = next_seq(conn, deck["code"], mod, nn)
            qid = "Q-%s-%s-%s-%04d" % (deck["code"], mod, nn, seq)
            cid = "C-%s-%s-%s-%04d" % (deck["code"], mod, nn, seq)

            db_type, content = build_content(c["type"], c)
            conn.execute(
                "INSERT INTO questions (id, topic_id, type, difficulty, source, content, ext_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (qid, topic_id, db_type, 0.5, SOURCE_PREFIX + key,
                 json.dumps(content, ensure_ascii=False), ext),
            )
            inserted_q += 1
            conn.execute(
                "INSERT INTO cards (id, question_id, state, due_date, due_at, queue, interval_days) "
                "VALUES (?, ?, 0, date('now','localtime'), datetime('now','localtime'), 0, 0)",
                (cid, qid),
            )
            inserted_c += 1

    print("\n" + "=" * 62)
    print("扫描 %d 个卡组，共 %d 张卡" % (len(DECKS), total))
    print("映射命中：", dict(hit_stats))
    if hit_stats.get("fallback"):
        print("  [注意] 有 %d 张落到兜底考点，检查一下 ch_topic 表是否漏了章节" % hit_stats["fallback"])
    if args.dry_run:
        print("[DRY-RUN] 未写库。将覆盖 %d 个考点：%s" % (len(covered), ", ".join(sorted(covered))))
    else:
        print("新增题目 %d 条，新增卡片 %d 条，跳过重复 %d 条" % (inserted_q, inserted_c, skipped))

    fixed = fix_orphans(conn, args.dry_run)
    if fixed:
        print("孤儿考点修复：" + ("；".join(fixed) if fixed else "无"))
        if args.dry_run:
            print("  [DRY-RUN] 未写库")
    else:
        print("孤儿考点修复：无待修")

    if not args.dry_run:
        conn.commit()

    left = conn.execute("""
        SELECT COUNT(*) FROM questions q LEFT JOIN topics t ON q.topic_id = t.id WHERE t.id IS NULL
    """).fetchone()[0]
    print("剩余悬空考点的题：%d" % left)

    conn.close()
    if errors:
        print("\n[FAIL] %d 处校验错误：" % len(errors))
        for e in errors[:40]:
            print("   -", e)
        return 1
    print("\n[OK] 校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

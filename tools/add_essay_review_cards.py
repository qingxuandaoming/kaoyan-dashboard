#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
add_essay_review_cards.py — 作文复盘训练：从批改记录出卡 + 写复盘会话（2026-10-06）

为什么存在
----------
2026-06 至 2026-10 批改了 7 篇考研英语作文（4 图画 + 3 应用文），每篇都有逐条错误明细、
范文与薄弱点汇总，但这些成果只躺在 Markdown 里「被读」，没有被**练**。
纵向复盘（批改得分总表.md）显示三条硬事实：
  · 拼写硬伤命中 7/7 篇、主谓一致 5/7 篇、be 动词 4/7 篇 —— 是稳定复发的习惯，不是偶然；
  · 语言分全线卡在 1–2 / 8（错误密度 12.1–21.1 条/百词）；
  · 批改断档近 4 个月后错误密度回弹（12.1 → 17.6）—— 「改完就放下」不成立，必须进间隔重复。

本脚本把 7 篇批改记录拆成两样东西：
  1) 闪卡（进 question_bank.db，走 FSRS 间隔重复）
  2) 复盘会话（进 Review/英语一/sessions/，复用现成的错题复盘页与错因画像）

素材全部来自 `essay_review_spec.json`（纯数据）。改内容不用碰本文件。

topic 落点（都用 topics 表里已存在且为空的位置，不新建 topic、不动图谱与覆盖率口径）
--------------------------------------------------------------------------------
  ENG-WRITE-03-02 常见语法错误     ← 常犯语法错误（主谓一致 / be 动词 / 词性 / 中式直译）
  ENG-WRITE-03-04 作文批改与复盘   ← 审题突出问题 + 拼写错词（spell）
  ENG-WRITE-03-01 高分范文分析     ← 好句子背诵（fill 挖空 / short 中译英）
  ENG-WRITE-01-03 格式与语域       ← 应用文称呼 / 落款 / 敬语 / 语域

与既有先例的关系
----------------
模板取自 tools/add_cards_2006essay_errors_20260930.py（它把一篇作文的 8 类错误拆成 16 张卡），
沿用了 next_seq / insert_card / VACUUM INTO 备份 / 单连接末尾一次 commit / shuffle_opts 确定性打乱。

本脚本对先例的两处改进
----------------------
  1. **幂等靠 ext_key，不靠 id 撞号**。先例用「最大数字后缀 +1」生成 seq，
     重跑一次 seq 全部越过已有卡 → 不去重、直接插重复。这里用 questions.ext_key
     （库里有 UNIQUE 索引）做稳定外部键：`essay-review:<spec 里的稳定 key>`，
     重跑先查 ext_key，命中就复用旧 id，不新增。
  2. **新题型 spell**。系统里此前没有任何「可输入答案」的题型（fill 只是挖空→显示答案），
     拼写硬伤既然 7/7 篇全中，就单独做一类，判分在前端本地完成。

用法
----
    python tools/add_essay_review_cards.py --dry-run
    python tools/add_essay_review_cards.py
    python tools/add_essay_review_cards.py --skip-sessions   # 只出卡，不写复盘会话
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, OrderedDict
from datetime import date, datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paths as _paths   # 路径单一事实源

SRC = Path(__file__).resolve().parent.parent
DB_PATH = SRC / "question_bank.db"
SPEC_PATH = SRC / "essay_review_spec.json"
NOTES_ROOT = Path(_paths.NOTES_ROOT)
REVIEW_DIR = NOTES_ROOT / "Review"

TODAY = date.today().isoformat()
SOURCE = f"作文复盘训练-{TODAY}"
SUBJECT = "英语一"
EXT_PREFIX = "essay-review:"

SPELL_TOPIC = "ENG-WRITE-03-04"      # 作文批改与复盘（拼写错词也归在这里，作文复盘聚在一处）

sys.path.insert(0, str(Path(__file__).resolve().parent))
from card_quality import check_options_quality, check_spell_quality, shuffle_opts  # noqa: E402


# ===========================================================================
# 一、把 spec 展开成卡（每题带一个 stable key，用于 ext_key 幂等）
# ===========================================================================

def expand_cards(spec):
    """→ OrderedDict[stable_key] = {"type", "topic_id", "card"}；card 形状对齐 validate/insert。

    stable key 是幂等的根：同一个 key 在任何一次运行里都指向同一张卡。
    """
    out = OrderedDict()

    # ---- 1) spec.cards：choice / judge / short，content 原样（answer=bool / options 等） ----
    for c in spec["cards"]:
        card = dict(c["content"])
        out[f"card:{c['_id']}"] = {"type": c["type"], "topic_id": c["topic_id"], "card": card}

    # ---- 2) spec.spell：**题干由这里生成**，spec 里只写词与错拼 ----
    #     这样做是为了让正确答案永远不可能泄进 stem：E5 红线（answer 出现在 stem 里）
    #     如果靠人工写题干，24 张卡总会有写漏的时候。
    for s in spec["spell"]:
        hint = f"（提示：{s['hint']}）" if s.get("hint") else ""
        card = {
            "stem": f"写出「{s['meaning']}」对应的英文单词{hint}",
            "answer": s["word"],
            "phonetic": s.get("phonetic", ""),
            "hint": s.get("hint", ""),
            "explanation": f"正确拼写：{s['word']}。拼写是不依赖语法水平、纯靠默写就能拿回来的分，"
                           f"而它是七篇作文里 7/7 全部命中的唯一一类错。",
            "traps": [f"拼写要点：{s['hint']}"],
            "retro": f"你的错拼出处：{s['from']}",
            "tags": ["拼写", "作文复盘"],
        }
        if s.get("alts"):
            card["alt_answers"] = list(s["alts"])
        out[f"spell:{s['word']}"] = {"type": "spell", "topic_id": SPELL_TOPIC, "card": card}

    # ---- 3) spec.sentences：fill 由 blank 挖空；short 由 zh 起题干 ----
    for i, s in enumerate(spec["sentences"]):
        if s["kind"] == "fill":
            en, blank = s["en"], s["blank"]
            card = {
                "stem": en.replace(blank, "{{c1::" + blank + "}}", 1),
                "answer": blank,
                "explanation": s["explanation"] + f"\n\n全句：{en}\n中文：{s['zh']}",
                "traps": s.get("traps", []),
                "retro": f"好句出处：{s['from']}",
                "tags": ["好句背诵", "作文复盘"],
            }
        else:
            card = {
                "stem": "把下面这句译成英语：" + s["zh"],
                "reference_answer": s["reference"],
                "key_points": s["key_points"],
                "explanation": s["explanation"],
                "traps": s.get("traps", []),
                "retro": f"好句出处：{s['from']}",
                "tags": ["好句翻译", "作文复盘"],
            }
        out[f"sentence:{i:02d}"] = {"type": s["kind"], "topic_id": s["topic_id"], "card": card}

    return out


def validate(qtype, card):
    """入库前的字段闸门。口径与 add_cards_2006essay_errors_20260930.py 一致，
    另加拼写题（走 card_quality.check_spell_quality，保持单一事实来源）。"""
    problems = []
    if not card.get("stem"):
        problems.append("缺 stem")
    if not card.get("explanation"):
        problems.append("缺 explanation")
    if not card.get("traps"):
        problems.append("缺 traps")
    if not card.get("retro"):
        problems.append("缺 retro（复盘案例行，本次出卡的核心字段）")

    if qtype == "choice":
        problems.extend(check_options_quality(card.get("options")))
        if card.get("answer") != 0:
            # 引擎按 options[0] 恒为正确答案的约定做确定性打乱；
            # answer 不是 0 说明 spec 作者把正确答案放错了位置，静默入库会把答案判反。
            problems.append(f"choice 的 answer 必须为 0（正确项放 options[0]），实为 {card.get('answer')!r}")
    elif qtype == "spell":
        problems.extend(check_spell_quality(card))
    elif qtype == "fill":
        if "{{c" not in card.get("stem", ""):
            problems.append("填空题 stem 里没有 {{c1::...}} 挖空")
        if not card.get("answer"):
            problems.append("缺 answer")
    elif qtype == "judge":
        if not isinstance(card.get("answer"), bool):
            problems.append("判断题 answer 必须是布尔（true=正确）")
    elif qtype == "short":
        if not card.get("reference_answer"):
            problems.append("缺 reference_answer（AI 批改的判分依据）")
        kp = card.get("key_points") or []
        if len(kp) < 2:
            problems.append(f"得分点只有 {len(kp)} 个，至少 2 个才有判分意义")
    else:
        problems.append(f"未知题型 {qtype!r}")
    return problems


# ===========================================================================
# 二、入库
# ===========================================================================

def next_seq(conn, topic_id):
    """序号取该 topic 现有数字后缀的最大值+1（count+1 在 id 有空洞时会撞号）。"""
    rows = conn.execute("SELECT id FROM questions WHERE topic_id = ?", (topic_id,)).fetchall()
    seqs = [int(i[0].rsplit("-", 1)[1]) for i in rows if i[0].rsplit("-", 1)[1].isdigit()]
    return (max(seqs) + 1) if seqs else 1


def build_content(qtype, card, qid):
    """按题型拼 content。**所有题型都带 retro**（复盘案例行）。"""
    if qtype == "choice":
        # options[0] 为正确答案；按 qid 确定性打乱，避免答案全落 A 形成新线索
        opts, ans = shuffle_opts(qid, card["options"], 0)
        return {"stem": card["stem"], "options": opts, "answer": ans,
                "explanation": card["explanation"], "traps": card.get("traps", []),
                "retro": card["retro"], "tags": card.get("tags", [])}

    content = {
        "stem": card["stem"],
        # 简答题没有 answer 字段，用参考答案兜底，保证通用消费方也能显示点东西
        "answer": card.get("answer", card.get("reference_answer", "")),
        "explanation": card["explanation"], "traps": card.get("traps", []),
        "retro": card["retro"], "tags": card.get("tags", []),
    }
    if qtype == "spell":
        if card.get("phonetic"):
            content["phonetic"] = card["phonetic"]
        if card.get("hint"):
            content["hint"] = card["hint"]
        if card.get("alt_answers"):
            content["alt_answers"] = card["alt_answers"]
    if qtype == "short":
        content["reference_answer"] = card["reference_answer"]
        content["key_points"] = card["key_points"]
    return content


def upsert_card(conn, qtype, topic_id, card, key, counters, dry):
    """幂等入库。→ (qid, 是否新增, 拦截原因 or None)

    幂等的关键是 ext_key（questions 上有 UNIQUE 索引），不是 id 撞号：
    先按 ext_key 查，命中就直接复用旧 id——这样重跑不会插重复，
    也不会有「seq 越过已有卡后全部新插一遍」的问题。
    """
    ext = EXT_PREFIX + key
    row = conn.execute("SELECT id FROM questions WHERE ext_key = ?", (ext,)).fetchone()
    if row:
        return row[0], False, None

    if topic_id not in counters:
        if not conn.execute("SELECT 1 FROM topics WHERE id = ?", (topic_id,)).fetchone():
            return None, False, f"知识点 {topic_id} 不存在"
        counters[topic_id] = next_seq(conn, topic_id)

    qid = f"Q-{topic_id}-{counters[topic_id]:04d}"
    # 极少数情况下 seq 与历史 id 撞号（历史上曾有 id 空洞）——往后让一格
    while conn.execute("SELECT 1 FROM questions WHERE id = ?", (qid,)).fetchone():
        counters[topic_id] += 1
        qid = f"Q-{topic_id}-{counters[topic_id]:04d}"
    counters[topic_id] += 1

    content = build_content(qtype, card, qid)
    if not dry:
        conn.execute(
            "INSERT INTO questions (id, topic_id, type, difficulty, source, content, ext_key) "
            "VALUES (?, ?, ?, 0.5, ?, ?, ?)",
            (qid, topic_id, qtype, SOURCE, json.dumps(content, ensure_ascii=False), ext),
        )
        # 新卡插入 cards 初始行；**绝不改已有卡的 FSRS 状态**
        conn.execute(
            "INSERT INTO cards (id, question_id, state, due_date) VALUES (?, ?, 0, ?)",
            (qid, qid, TODAY),
        )
    return qid, True, None


# ===========================================================================
# 三、复盘会话（复用错题复盘子系统，零改动接入错因画像）
# ===========================================================================

def sid_for(essay):
    """稳定的会话 id。review_store 的 newSessionId 用随机后缀（不适合可重跑的种子），
    这里用作文 key 代替随机段，格式保持一致：r<date>-<科目>-<段>。"""
    return f"r{TODAY}-{SUBJECT}-{essay['key']}"


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def build_transcript(essay, rows, card_ids):
    """人读的复盘案例：原文 → 逐条错因 → 改法 → 对应卡片。
    同时按 review_store 的 turns 约定（**我**： / **AI**：）输出，
    这样复盘页会把它渲染成对话气泡而不是一段裸文本。"""
    L = []
    L.append(f"**我**：\n")
    L.append(f"（{essay['year']} 年英语一 {essay['kind']}作文：{essay['title']}）\n")
    L.append(f"批改结论：{essay['score']}/{essay['full']}（{essay['band']}）")
    L.append(f"\n{essay['headline']}\n")
    L.append(f"\n**AI**：\n")
    L.append(f"## 这篇的复盘\n")
    L.append(f"**得分**：{essay['score']} / {essay['full']}（{essay['band']}）  ")
    L.append(f"**批改记录**：`{essay['record']}`\n")
    L.append(f"{essay['headline']}\n")
    for i, pt in enumerate(rows, 1):
        cids = card_ids.get(pt["_cause"], []) if pt.get("_cause") else []
        L.append(f"### {i}. {pt['title']}\n")
        L.append(f"**原句**：`{pt['what_wrong']}`\n")
        L.append(f"**错因**：{pt['cause']}　（类型：{pt['cause_kind']}｜严重度 {pt['severity']}/5）\n")
        L.append(f"{pt['detail']}\n")
        if cids:
            L.append("**去练这几张**：" + "、".join(f"`{c}`" for c in cids) + "\n")
        else:
            L.append("**去练**：这一类暂时还没有对应卡片，属于待补的出题缺口。\n")
    L.append("---\n")
    L.append(f"*复盘日期：{TODAY}｜由 `tools/add_essay_review_cards.py` 从批改记录生成*")
    return "\n".join(L) + "\n"


def write_sessions(spec, card_ids, dry):
    """每篇作文 = 一次复盘会话。直接落 review_store 的四件套，绕开 HTTP 层。"""
    n = 0
    for essay in spec["essays"]:
        sid = sid_for(essay)
        sdir = REVIEW_DIR / SUBJECT / "sessions" / sid
        now = f"{TODAY}T00:00:00.000Z"

        # 每篇只保留属于本篇的复盘点
        rows = []
        for pt in essay["points"]:
            r = dict(pt)
            r["_cause"] = pt["cause"]
            rows.append(r)

        errors = []
        for i, pt in enumerate(rows, 1):
            errors.append({
                "id": f"e-{essay['key']}-{i:02d}",
                "topic_id": pt["topic_id"],
                "topic_hint": "",
                "title": pt["title"],
                "what_wrong": pt["what_wrong"],
                "cause": pt["cause"],            # 跨会话归并键（错因画像按它聚合）
                "cause_kind": pt["cause_kind"],
                "evidence": pt["detail"],
                "severity": pt["severity"],
                "card_ids": [c for ref in pt.get("cards", [])
                             for c in card_ids.get(ref, [])][:20],
                "resolved": False,
                "retro_source": essay["record"],
            })

        cause_count = Counter(e["cause"] for e in errors)
        meta = {
            "id": sid, "subject": SUBJECT, "date": TODAY,
            "title": f"{essay['year']} 年英语一{essay['kind']}作文 · {essay['title']}（{essay['score']}/{essay['full']}）",
            "source_kind": "mixed",
            "note": f"由批改记录生成：{essay['record']}",
            "status": "open",
            "files": [],
            "error_count": len(errors),
            "top_causes": [c for c, _ in cause_count.most_common(5)],
            "created_at": now, "updated_at": now,
        }

        if not dry:
            sdir.mkdir(parents=True, exist_ok=True)
            (sdir / "uploads").mkdir(exist_ok=True)
            write_json(sdir / "meta.json", meta)
            write_json(sdir / "errors.json",
                       {"id": sid, "updated_at": now, "errors": errors})
            (sdir / "transcript.md").write_text(
                build_transcript(essay, rows, card_ids), encoding="utf-8")
        print(f"  [{sid}] {len(errors):2d} 个复盘点 | "
              f"{'（dry-run）' if dry else '已落盘'}")
        n += 1

    if not dry:
        # 更新科目索引（复盘页的会话列表读它）
        idx_path = REVIEW_DIR / SUBJECT / "index.json"
        idx = {"subject": SUBJECT, "updated_at": None, "sessions": []}
        if idx_path.exists():
            try:
                idx = json.loads(idx_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        fresh = {s["id"]: s for s in idx.get("sessions", [])}
        for essay in spec["essays"]:
            sid = sid_for(essay)
            ep = REVIEW_DIR / SUBJECT / "sessions" / sid / "meta.json"
            if not ep.exists():
                continue
            m = json.loads(ep.read_text(encoding="utf-8"))
            fresh[sid] = {"id": sid, "date": m["date"], "title": m["title"],
                          "status": m["status"], "error_count": m["error_count"],
                          "top_causes": m["top_causes"]}
        idx["sessions"] = sorted(fresh.values(), key=lambda s: s["date"], reverse=True)
        write_json(idx_path, idx)
    return n


# ===========================================================================
# 四、主流程
# ===========================================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只算不写")
    ap.add_argument("--skip-sessions", action="store_true", help="只出卡，不写复盘会话")
    ap.add_argument("--spec", default=str(SPEC_PATH))
    args = ap.parse_args()
    dry = args.dry_run

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    cards = expand_cards(spec)
    print(f"数据库：{DB_PATH}{'（dry-run，不写库）' if dry else ''}")
    print(f"素材：{args.spec}｜展开出 {len(cards)} 张卡\n")

    conn = sqlite3.connect(DB_PATH)
    if not dry:
        # 改库纪律：先备份（VACUUM INTO 拿一致快照，避开 WAL 旁路文件）
        bak = DB_PATH.with_name(f"question_bank.db.bak_essayreview_{datetime.now():%Y%m%d_%H%M%S}")
        conn.execute("VACUUM INTO ?", (str(bak),))
        print(f"已备份：{bak}\n")

    counters = {}
    card_ids = {}          # ref（如 "spell:people" / "gram-sva-1"）→ [qid]
    n_new = n_skip = n_block = 0
    by_type = Counter()
    print("== 出卡 ==")
    for key, item in cards.items():
        qtype, tid, card = item["type"], item["topic_id"], item["card"]
        problems = validate(qtype, card)
        if problems:
            print(f"  ❌ [闸门拦截] {key} → {'；'.join(problems)}")
            n_block += 1
            continue
        qid, is_new, err = upsert_card(conn, qtype, tid, card, key, counters, dry)
        if err:
            print(f"  ❌ {key} → {err}")
            n_block += 1
            continue
        card_ids.setdefault(key, []).append(qid)
        # spec.cards 的 ref 是 "_id"（如 gram-sva-1），spell/sentence 的 ref 是完整 key
        if key.startswith("card:"):
            card_ids.setdefault(key.split(":", 1)[1], []).append(qid)
        by_type[qtype] += 1
        if is_new:
            n_new += 1
            print(f"  [新增·{qtype:6s}] {qid}  {card['stem'][:40]}")
        else:
            n_skip += 1

    # 复盘点里的 "spell:word" 引用 → 该词那张卡的 qid
    for s in spec["spell"]:
        card_ids.setdefault("spell:" + s["word"], card_ids.get("spell:" + s["word"], []))

    print(f"\n出卡结果：新增 {n_new}、复用已有 {n_skip}、拦截 {n_block}")
    print("题型分布：" + " / ".join(f"{t} {c}" for t, c in sorted(by_type.items())))

    if not args.skip_sessions:
        print("\n== 复盘会话 ==")
        n = write_sessions(spec, card_ids, dry)
        print(f"共 {n} 篇 → {REVIEW_DIR / SUBJECT / 'sessions'}"
              + ("（dry-run）" if dry else ""))

    if not dry:
        conn.commit()
    conn.close()

    # 复现率小结：这是错因画像的预览，也是「哪类错在复发」的答案
    cc = Counter(pt["cause"] for e in spec["essays"] for pt in e["points"])
    print("\n== 复现错因（跨篇） ==")
    for cause, k in cc.most_common():
        if k >= 2:
            print(f"  {k}/7 篇  {cause}")
    print(f"\n完成。" + ("（dry-run，未写库、未落盘）" if dry else ""))


if __name__ == "__main__":
    main()

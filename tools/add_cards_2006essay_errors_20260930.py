#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
add_cards_2006essay_errors_20260930.py — 2006 年英语一 Part A 批改后的针对性补卡

素材来源：2006-应用文作文-希望工程助学信-批改记录.md（E:\NPEE\English\translation&writing\作文\应用文\）
该篇实测 6/10，暴露 8 类错误，按「错什么练什么」出 16 张卡：

  主语从句（重点，5 张）  ENG-GRAM-01-02   原错：I can do is little / you do are great
  定语从句（2 张）        ENG-GRAM-01-02   原错：who because any reasons that couldn't...
  非谓语·不定式（2 张）   ENG-GRAM-01-04   原错：writing for contribute / would like offering
  格式与语域（2 张）      ENG-WRITE-01-03  原错：Dear commission / Yours sencerely / couldn't
  常见语法错误（6 张）    ENG-WRITE-03-02  原错：be 动词丢失、词性/复数/格、拼写

题型配比：choice 8 / fill 4 / judge 3 / short 1（简答=手写改句，走 AI 批改）。
选择题 options[0] 为正确答案，入库前用 card_quality.shuffle_opts 按 qid 确定性打乱。

用法:
    python tools/add_cards_2006essay_errors_20260930.py --dry-run
    python tools/add_cards_2006essay_errors_20260930.py
"""

import argparse
import io
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paths as _paths   # 路径单一事实源

DB_PATH = Path(__file__).resolve().parent.parent / "question_bank.db"
TODAY = date.today().isoformat()
SOURCE = f"2006作文错误针对性-{TODAY}"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from card_quality import check_options_quality, shuffle_opts  # noqa: E402

# ===========================================================================
# 一、选择题（options[0] = 正确答案；干扰项取自本篇真实错句的变体，同形态、近长度）
# ===========================================================================

CHOICE_CARDS = [
    # ---------------- 主语从句 ----------------
    {
        "topic": "ENG-GRAM-01-02",
        "stem": "下列哪一句语法正确？",
        "options": [
            "What I can do is little, but what you do is great.",
            "What can I do is little, but what do you do is great.",
            "What I can do are little, but what you do are great.",
            "I can do is little, but you do are great.",
        ],
        "explanation": "what 引导的主语从句用陈述语序（主语+谓语），且单个从句作主语谓语动词用单数：What I can do is little. "
                       "选项 B 在从句里用了疑问倒装；选项 C 把 what 从句误按复数一致；选项 D 缺引导词 what，"
                       "一个主语上挂了 can do 与 is 两个谓语。",
        "traps": ["主语从句里用疑问倒装（What can I do is…）；what 从句作主语机械按复数一致；漏引导词 what 导致一句两谓语。"],
        "tags": ["语法", "主语从句"],
    },
    {
        "topic": "ENG-GRAM-01-02",
        "stem": "下列哪一句的主语从句用法正确？",
        "options": [
            "What the remote areas lack most is educational resources.",
            "What do the remote areas lack most is educational resources.",
            "The remote areas lack most is educational resources.",
            "What the remote areas lack most are educational resources.",
        ],
        "explanation": "what 引导的主语从句整体作主语，从句内部全程陈述语序，谓语用单数 is。选项 B 疑问倒装；"
                       "选项 C 缺引导词，前半截自成一句、与后面的 is 打架；选项 D 主谓一致误用复数。",
        "traps": ["把主语从句写成疑问句语序；漏掉 what 造成双谓语连写。"],
        "tags": ["语法", "主语从句"],
    },
    # ---------------- 定语从句 ----------------
    {
        "topic": "ENG-GRAM-01-02",
        "stem": "原句 someone who because any reasons that couldn't go to school 的定语从句结构破碎。下列改写正确的是？",
        "options": [
            "a child who, for various reasons, cannot go to school",
            "a child who because of various reasons that cannot go to school",
            "a child who for various reasons cannot going to school",
            "a child because various reasons who cannot go to school",
        ],
        "explanation": "关系代词 who 引导定语从句后必须直接跟完整谓语（cannot go），原因状语 for various reasons 作插入语、"
                       "用逗号隔开。选项 B 状语后多挂一个 that，谓语被打断；选项 C 情态动词 cannot 后不能接 going；"
                       "选项 D 把 because（连词，不能接名词短语）放到关系词前面，修饰关系全乱。",
        "traps": ["把「因为任何原因」逐字搬进定语从句；引导词后多挂一个 that 打断谓语。"],
        "tags": ["语法", "定语从句"],
    },
    # ---------------- 格式与语域 ----------------
    {
        "topic": "ENG-WRITE-01-03",
        "stem": "给不知姓名的机构（如希望工程办公室）写信时，下列称呼与落款的配对正确的是？",
        "options": [
            "Dear Sir or Madam, … Yours faithfully,",
            "Dear Sir or Madam, … Yours sincerely,",
            "Dear Commission, … Yours faithfully,",
            "Dear friends, … Yours sincerely,",
        ],
        "explanation": "不知姓名 → Dear Sir or Madam, 配 Yours faithfully,；已知姓名 → Dear Mr. Smith, 配 Yours sincerely,。"
                       "commission 不是规范称呼语（原稿还拼错且小写）；Dear friends 属非正式语域，不适合给机构的公务信。",
        "traps": ["Yours sincerely 与不知姓名的称呼混配（本篇原错）；拿机构名或亲昵称呼当称呼语。"],
        "tags": ["写作", "书信格式"],
    },
    # ---------------- 常见语法错误 ----------------
    {
        "topic": "ENG-WRITE-03-02",
        "stem": "下列哪一句的谓语结构完整？",
        "options": [
            "My plan is to help build a school and support teachers there.",
            "My plan to help build a school and support teachers there.",
            "My plan is help build a school and support teachers there.",
            "My plan being to help build a school and support teachers there.",
        ],
        "explanation": "英语句子必须有限定动词作谓语。本句不定式短语 to help… 作表语，系动词 is 不能省（选项 B 正是原错 "
                       "My plan to aid building… 的形态）；is 后作表语要带 to 的不定式，不能接动词原形 help（选项 C）；"
                       "being 是非谓语，不能充当谓语，整句沦为片段（选项 D）。",
        "traps": ["按中文话题句逐字翻成「名词主语+不定式」，丢掉系动词。"],
        "tags": ["写作", "常见语法错误", "谓语"],
    },
    {
        "topic": "ENG-WRITE-03-02",
        "stem": "下列哪一句正确表达「我的能力有限」？",
        "options": [
            "My competence is limited.",
            "My competence be stricted.",
            "My competence limited.",
            "My competence is restrict.",
        ],
        "explanation": "limited / restricted 是形容词（过去分词作形容词），须由系动词 is 连接；原错 be stricted 同时犯了 "
                       "be 动词形态错与 stricted 生造词（应为 restricted）两处。选项 C 丢系动词、形容词裸奔作谓语；"
                       "选项 D 的 restrict 是动词原形，is 后须用过去分词 restricted 才能构成被动。",
        "traps": ["仿 restricted 造出 stricted；丢系动词让形容词直接作谓语。"],
        "tags": ["写作", "常见语法错误", "系动词"],
    },
    {
        "topic": "ENG-WRITE-03-02",
        "stem": "下列哪一句的词性、单复数与主谓一致全部正确？",
        "options": [
            "This world has too many different misfortunes.",
            "This world have too many different unfortunates.",
            "This world has too many different unfortunate.",
            "This world have too many different misfortune.",
        ],
        "explanation": "misfortune（不幸的事）是可数名词，too many 后须用复数 misfortunes；主语 This world 三单，谓语用 has。"
                       "选项 B 的 unfortunates 名词化后指「不幸的人」而非「不幸的事」，词义不符；选项 C 的 unfortunate 是形容词，"
                       "不能裸奔作名词；选项 D 主谓一致与单复数双错。",
        "traps": ["把形容词 unfortunate 直接当名词用；主语看着像复数就忘三单 has。"],
        "tags": ["写作", "常见语法错误", "词性与数"],
    },
    {
        "topic": "ENG-WRITE-03-02",
        "stem": "原稿有多处拼写错误。下列哪一组四个词的拼写全部正确？",
        "options": [
            "little, competence, sincerely, greatly",
            "littel, competence, sincerely, greatful",
            "little, competance, sencerely, greatly",
            "little, competence, sincerly, greatly",
        ],
        "explanation": "原稿四个高频拼写错：littel → little（元音顺序）；competance → competence（-ence 结尾）；"
                       "senerely/sencerely → sincerely（sin-cere-ly）；greatful → greatly（greatful 是把 grateful「感激的」"
                       "与副词 greatly 混写，修饰动词 help 要用副词 greatly）。",
        "traps": ["-ance/-ence 混淆（competence）；sincerely 写成 sincerly；仿 grateful 造出 greatful。"],
        "tags": ["写作", "拼写"],
    },
]

# ===========================================================================
# 二、填空题（cloze：{{c1::答案}}，未揭晓显示 ______；考规则回忆）
# ===========================================================================

FILL_CARDS = [
    {
        "topic": "ENG-GRAM-01-02",
        "stem": "把「我能做的很少」译成英语 “______ is little” 时，主语从句用 {{c1::what}} 引导，"
                "从句内部语序为 {{c2::陈述语序（主语+谓语）}}，主句谓语动词用 {{c3::单数}}。",
        "answer": "what / 陈述语序 / 单数",
        "explanation": "What I can do is little.：what 既作引导词又在从句中充当 do 的宾语；从句用陈述语序；"
                       "单个从句作主语谓语用单数。对照原错：I can do is little（缺 what）、"
                       "What can I do is little（疑问语序）、What I can do are little（一致错）。",
        "traps": ["写的时候漏掉 what，或顺手写成疑问语序。"],
        "tags": ["语法", "主语从句"],
    },
    {
        "topic": "ENG-GRAM-01-04",
        "stem": "表目的时用 {{c1::to do（不定式作目的状语）}}（或 in order to / so as to）；"
                "for + {{c2::名词或动名词}} 只表「用途、对象」不表目的；"
                "故 I am writing for contribute 应改为 {{c3::I am writing to contribute}}。",
        "answer": "to do / 名词或动名词 / I am writing to contribute",
        "explanation": "for + doing 表某物的用途（a tool for cutting），for + 名词 表对象（for you），都不能引出动作的目的；"
                       "表目的用 to 不定式：I am writing to contribute my modest share.",
        "traps": ["用 for + 动词原形 / for + doing 表写作目的。"],
        "tags": ["语法", "非谓语", "目的"],
    },
    {
        "topic": "ENG-WRITE-03-02",
        "stem": "only education can help they change themself 中，help 的宾语要用宾格 {{c1::them}}，"
                "反身代词复数为 {{c2::themselves}}；these child 中 these 后必须接 {{c3::复数 children}}。",
        "answer": "them / themselves / children",
        "explanation": "动词后接宾格（help them）；反身代词复数是 themselves（themself 不规范）；child 的复数为不规则变化 "
                       "children，these children / those children 同理。改后整句：Only education can help them change themselves.",
        "traps": ["主格 they 当宾语；反身代词复数写成 themself；these 后仍接单数 child。"],
        "tags": ["写作", "常见语法错误", "代词"],
    },
    {
        "topic": "ENG-WRITE-03-02",
        "stem": "because 是连词，只能接 {{c1::从句（有主语和谓语）}}；接名词短语（如 various reasons）要用 "
                "{{c2::because of / due to}}；定语从句里原因状语更宜作插入语：who, {{c3::for various reasons}}, cannot go to school.",
        "answer": "从句 / because of（due to） / for various reasons",
        "explanation": "because + 从句：because he is ill；because of / due to / owing to + 名词：because of various reasons。"
                       "原错 who because any reasons that… 把两种结构杂糅，还多挂一个 that。定语从句中原因状语通常作插入语、"
                       "用逗号隔开，保持「引导词+谓语」的主干不断。",
        "traps": ["because 后直接跟名词短语；定语从句里塞 because 从句打断谓语。"],
        "tags": ["写作", "常见语法错误", "连词"],
    },
]

# ===========================================================================
# 三、判断题（考概念边界：命题里埋一个错，看能不能一眼看穿）
# ===========================================================================

JUDGE_CARDS = [
    {
        "topic": "ENG-GRAM-01-02",
        "stem": "what 引导的主语从句作主语时，谓语动词的单复数一律由「从句」决定，只能用单数。",
        "answer": False,
        "explanation": "错误。通常用单数（What I can do is little.），但表语为复数名词时谓语可随表语用复数："
                       "What we need are more books.。一致关系看意义一致而非机械看「从句」；"
                       "反之 What I can do is little 中表语是单数 little，必须用 is——本篇原错 what you do are great 正栽在这里。",
        "traps": ["机械记「what 从句作主语=单数」，又在表语单数的句子里误用复数谓语。"],
        "tags": ["语法", "主语从句", "主谓一致"],
    },
    {
        "topic": "ENG-GRAM-01-04",
        "stem": "would like 与 want 后接形式相同：都只接 to 不定式（would like to do / want to do），不接动名词。",
        "answer": True,
        "explanation": "正确。would like to offer = want to offer，would like 语气更客气；不存在 would like offering。"
                       "同类只接 to do 的动词：wish、hope、decide、refuse；对照只接 doing 的：enjoy、consider、avoid、finish。",
        "traps": ["受 enjoy/consider + doing 类比，写出 would like offering、would like contributing。"],
        "tags": ["语法", "非谓语"],
    },
    {
        "topic": "ENG-WRITE-01-03",
        "stem": "正式书信（如考研英语 Part A）中，couldn't、don't、it's 这类缩写属于口语语域，应写全为 cannot、do not、it is。",
        "answer": True,
        "explanation": "正确。应用文书信、通知等语域正式：缩写、俚语、感叹句都应避免。原稿 couldn't 应写 cannot；"
                       "同理 I'm → I am、won't → will not、can't → cannot。",
        "traps": ["把口语缩写带进正式书信，语域失分。"],
        "tags": ["写作", "语域"],
    },
]

# ===========================================================================
# 四、简答题（手写改句拍照 → DeepSeek 按 key_points 批改）
# ===========================================================================

SHORT_CARDS = [
    {
        "topic": "ENG-GRAM-01-02",
        "stem": "下面两句出自 2006 年真题作文的破碎句。请在不改变原意的前提下，把它们改写成语法正确的英语（手写后拍照提交）：\n"
                "① I can do is little but you do are great.\n"
                "② My plan to aid building a school and aid teaches going there to educate these child.",
        "reference_answer": "① What I can do is little, but what you do is great. "
                            "② My plan is to help build a school and (to) support teachers to go there to educate these children.",
        "key_points": [
            "① 用 what 引导两个主语从句：What I can do / what you do",
            "① 主语从句用陈述语序，谓语动词用单数 is",
            "② 补出系动词 is，构成 My plan is to…",
            "② teaches 改为名词复数 teachers，child 改为复数 children",
        ],
        "explanation": "两句破碎的同源病因是缺「结构词」：① 缺引导词 what，导致三个动词形式抢一个主语；② 全句没有限定动词"
                       "（to aid 是非谓语）。自查法：数谓语——一个句子有几个分句就必须有几个限定谓语。",
        "traps": ["改写时又漏 what，写成 I can do is little；② 改成 My plan being to…，仍然没有限定动词。"],
        "tags": ["语法", "主语从句", "改句"],
    },
]

ALL = [("choice", CHOICE_CARDS), ("fill", FILL_CARDS), ("judge", JUDGE_CARDS), ("short", SHORT_CARDS)]


def validate(qtype, card):
    """入库前的字段闸门：与 add_judge_fill_short_cards.py 同口径，另加选择题选项闸门。"""
    problems = []
    if not card.get("stem"):
        problems.append("缺 stem")
    if not card.get("explanation"):
        problems.append("缺 explanation")
    if not card.get("traps"):
        problems.append("缺 traps")
    if qtype == "choice":
        probs = check_options_quality(card.get("options"))
        problems.extend(probs)
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
    return problems


def next_seq(conn, topic_id):
    """序号取该 topic 现有数字后缀的最大值+1（count+1 在 id 有空洞时会撞号）。"""
    rows = conn.execute("SELECT id FROM questions WHERE topic_id = ?", (topic_id,)).fetchall()
    seqs = [int(i[0].rsplit("-", 1)[1]) for i in rows if i[0].rsplit("-", 1)[1].isdigit()]
    return (max(seqs) + 1) if seqs else 1


def insert_card(conn, qtype, topic_id, seq, card, dry):
    qid = f"Q-{topic_id}-{seq:04d}"
    if conn.execute("SELECT 1 FROM questions WHERE id = ?", (qid,)).fetchone():
        print(f"  [已存在·跳过] {qid}")
        return 0
    if qtype == "choice":
        # options[0] 为正确答案；按 qid 确定性打乱，避免答案全落 A 形成新线索
        opts, ans = shuffle_opts(qid, card["options"], 0)
        content = {"stem": card["stem"], "options": opts, "answer": ans,
                   "explanation": card["explanation"], "traps": card.get("traps", []),
                   "tags": card.get("tags", [])}
    else:
        content = {"stem": card["stem"],
                   # 简答题没有 answer 字段，用参考答案兜底，保证通用消费方也能显示点东西
                   "answer": card.get("answer", card.get("reference_answer", "")),
                   "explanation": card["explanation"], "traps": card.get("traps", []),
                   "tags": card.get("tags", [])}
        if qtype == "short":
            content["reference_answer"] = card["reference_answer"]
            content["key_points"] = card["key_points"]
    if not dry:
        conn.execute(
            "INSERT INTO questions (id, topic_id, type, difficulty, source, content) "
            "VALUES (?, ?, ?, 0.5, ?, ?)",
            (qid, topic_id, qtype, SOURCE, json.dumps(content, ensure_ascii=False)),
        )
        conn.execute(
            "INSERT INTO cards (id, question_id, state, due_date) VALUES (?, ?, 0, ?)",
            (qid, qid, TODAY),
        )
    print(f"  [新增·{qtype}] {qid} {card['stem'][:34]}")
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    dry = args.dry_run

    conn = sqlite3.connect(DB_PATH)
    print(f"数据库：{DB_PATH}{'（dry-run）' if dry else ''}\n")

    if not dry:
        # 改库纪律：先备份（VACUUM INTO 拿到一致快照，避开 WAL 旁路文件）
        bak = DB_PATH.with_name(f"question_bank.db.bak_add2006essay_{datetime.now():%Y%m%d_%H%M%S}")
        conn.execute("VACUUM INTO ?", (str(bak),))
        print(f"已备份：{bak}\n")

    total = 0
    # ⚠️ 计数器在**四种题型之间共享**：每个 topic 的序号全局唯一，
    #    按题型各自数会让 choice/fill 撞同一个 id（后一张被「已存在」静默跳过）。
    counters = {}
    for qtype, cards in ALL:
        print(f"== {qtype}（{len(cards)} 张）==")
        for card in cards:
            tid = card["topic"]
            if tid not in counters:
                if not conn.execute("SELECT 1 FROM topics WHERE id = ?", (tid,)).fetchone():
                    print(f"  ❌ 知识点 {tid} 不存在，跳过")
                    counters[tid] = None
                    continue
                counters[tid] = next_seq(conn, tid)
            if counters[tid] is None:
                continue
            problems = validate(qtype, card)
            if problems:
                print(f"  ❌ [闸门拦截] {card.get('stem', '')[:28]} → {'；'.join(problems)}")
                continue
            total += insert_card(conn, qtype, tid, counters[tid], card, dry)
            counters[tid] += 1
        print()

    if not dry:
        conn.commit()
    conn.close()
    print(f"完成：新增 {total} 张。" + ("（dry-run，未写库）" if dry else ""))


if __name__ == "__main__":
    main()

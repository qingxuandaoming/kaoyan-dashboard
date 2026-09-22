#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
fix_english_flashcards_20260913.py — 英语闪卡质量重构

【用户反馈】闪卡选项没有形成有效区分：正确答案是唯一一条"裸中文释义"，
三个干扰项全都带 "词缀-(释义)" 标签。学生不看词义，只看格式就能 100% 选对。

【根因】insert_questions.py 里 eng_data 的 wrong 字段直接存了
"re-(再/重新)" 这种带标签的字符串，options = [meaning] + wrong[:3]，
于是四种选项里只有正确项没有标签 → 格式本身泄露答案。

【本脚本做四件事】只改 questions.content，不碰 cards 表任何 FSRS 调度状态
（state/due_at/stability/difficulty/reps/lapses/leech/suspended 全部原样）。

  一、重写 15 张词根词缀卡（ENG-VOC-01-03-0001..0015）：
      4 个选项统一为同形态中文释义，干扰项改为同语义场的易混词缀释义
      （前缀卡用方向前缀互扰、后缀卡用词性后缀互扰），并补 traps。
  二、剥离全库选项内嵌的 "A. / B. " 字母前缀——渲染层已经画了 A/B/C/D 徽章，
      会显示成 "Ⓐ A. access"；只剥离与选项自身序号一致的前缀，不一致的报警。
  三、修正英语选择题的选项形态/长度失衡与凑数干扰项，补 traps 易错点。
  四、按 English/word&phrase/词根词缀/词根词缀总表.md 新增 20 张卡
      （心法 / 高频词根 / 拆词示范 / 后缀陷阱 / 比较级词根 / 形近词缀应用）。

用法:
    python tools/fix_english_flashcards_20260913.py --dry-run   # 只打印不写库
    python tools/fix_english_flashcards_20260913.py             # 写库
"""

import argparse
import io
import json
import random
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(r"E:\NPEE")
DB_PATH = BASE_DIR / "src" / "question_bank.db"
TODAY = date.today().isoformat()
NEW_SOURCE = f"英语闪卡重构-{TODAY}"


# ---------------------------------------------------------------------------
# 质量规则（与 tools/check_card_quality.py 共用同一套判定）
# ---------------------------------------------------------------------------

# 选项开头形如 "A. " / "B、" / "C）" / "D．"（判定规则与 tools/card_quality.py 共用）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from card_quality import LETTER_PREFIX_RE as LETTER_PREFIX  # noqa: E402

# 选项形如 "re-(再/重新)" / "-tion（名词后缀）" —— 词缀标签形态
AFFIX_LABEL = re.compile(r"^\s*-?[A-Za-z]{1,10}-\s*[（(]")


def strip_letter_prefix(option, index):
    """剥离与选项自身序号一致的字母前缀；不一致则原样返回并标红。"""
    m = LETTER_PREFIX.match(option or "")
    if not m:
        return option, None
    want = chr(65 + index)
    got = m.group(1).upper()
    if got != want:
        return option, f"字母前缀 {got} 与序号 {want} 不一致"
    return option[m.end():], None


def shuffle_opts(qid, options, answer):
    """确定性打乱选项，并同步 answer 下标。

    正确性由 idx.index(answer) 保证：new_options[new_answer] == options[answer]，
    即正确答案的**文本**不变，只是位置变化。用 qid 作种子，重复运行结果一致。
    """
    idx = list(range(len(options)))
    random.Random(qid).shuffle(idx)
    return [options[i] for i in idx], idx.index(answer)


# ---------------------------------------------------------------------------
# 一、15 张词根词缀卡重写
#     options 里答案写在 answers 指定的下标；干扰项一律是"同形态中文释义"，
#     且取自同语义场（前缀↔方向前缀，后缀↔词性后缀），必须知道词义才能选对。
# ---------------------------------------------------------------------------

AFFIX_REWRITE = {
    # ---- 1. pre- : 干扰项为 post-(在后) / con-(一起) / ex-(向外) ----
    "Q-ENG-VOC-01-03-0001": {
        "stem": '词缀 "pre-" 的核心含义是？',
        "options": ["在前/预先", "在后/之后", "共同/一起", "向外/离开"],
        "answer": 0,
        "explanation": (
            "pre- = 在前、预先（时间上先）。拆词：predict = pre(在前) + dict(说) → 事先说出 → 预言；"
            "preview = pre + view(看) → 预览；prepare = pre + pare(准备) → 准备。"
            "辨析：pre-（在前）↔ post-（在后）；别与 pro-（向前/支持）混。"
        ),
        "traps": ["把 pre- 与 pro- 混为一谈：pre- 是时间上的“在前”，pro- 是方向上的“向前”（progress 前进）。"],
    },
    # ---- 2. anti- : 干扰项为 pro-(支持) / pre-(在前) / con-(一起) ----
    "Q-ENG-VOC-01-03-0002": {
        "stem": '词缀 "anti-" 的核心含义是？',
        "options": ["反对/对抗", "支持/赞成", "在...之前", "共同/一起"],
        "answer": 0,
        "explanation": (
            "anti- = 反对、对抗。拆词：antibiotic = anti(抗) + bio(生命) + -tic → 抗生的 → 抗生素；"
            "antidote = anti + dot(给) → 解毒剂；antisocial = anti + social(社会的) → 反社会的。"
            "辨析：anti-（反对）↔ pro-（支持），考研写作表达立场时常用这对前缀。"
        ),
        "traps": ["把 anti-（反对）与 pro-（支持）的方向记反；写作中表达立场时用反会直接跑题。"],
    },
    # ---- 3. sub- : 干扰项为 super-(上) / inter-(之间) / trans-(跨越) ----
    "Q-ENG-VOC-01-03-0003": {
        "stem": '词缀 "sub-" 的核心含义是？',
        "options": ["在...下面/次级", "在...上面/超越", "在...之间/相互", "跨越/转变"],
        "answer": 0,
        "explanation": (
            "sub- = 在下方、次级。拆词：submarine = sub(下) + marine(海的) → 潜水艇；"
            "subordinate = sub(下) + ordin(顺序) + -ate → 顺序在下的 → 下属、下级的；"
            "subway = sub + way(路) → 地下的路 → 地铁。辨析：sub-（下）↔ super-（上）。"
        ),
        "traps": ["把 sub- 与 super- 的方向记反；subsequent 是“随后的”（跟在下面），不是“卓越的”。"],
    },
    # ---- 4. -able/-ible : 干扰项为 -ful / -less / -ize ----
    "Q-ENG-VOC-01-03-0004": {
        "stem": '后缀 "-able/-ible" 的核心含义是？',
        "options": ["能被...的（含被动义）", "充满...的", "没有/缺少...的", "使...化"],
        "answer": 0,
        "explanation": (
            "-able/-ible 是形容词后缀，核心是“能被……的”，含**被动**义。"
            "reliable = rely + able → 能被依靠的 → 可靠的；visible = vis(看) + ible → 能被看见的；"
            "unmistakable = un + mistake + able → 不可能被弄错的 → 明确无误的。"
            "辨析：-able（能被…的）↔ -ful（充满…的）↔ -less（没有…的）。"
        ),
        "traps": ["把 -able 理解成主动的“能够”，忽略被动义：unmistakable 不是“能弄错”，而是“不可能被弄错”。"],
    },
    # ---- 5. re- : 干扰项为 pro- / de- / ex- ----
    "Q-ENG-VOC-01-03-0005": {
        "stem": '词缀 "re-" 的核心含义是？',
        "options": ["回/再/重新", "向前/支持", "向下/去除", "向外/离开"],
        "answer": 0,
        "explanation": (
            "re- = 回、再、重新（方向上是“往回”或“再一次”）。拆词：return = re + turn(转) → 转回来 → 返回；"
            "review = re + view(看) → 再看一遍 → 复习；reject = re + ject(扔) → 扔回去 → 拒绝。"
            "注意：re- 不总是“再一次”，reject/recede 里是“往回”。"
        ),
        "traps": ["把 re- 一律记成“再一次”，遇到 reject（扔回去→拒绝）、recede（往回走→后退）就解释不通。"],
    },
    # ---- 6. inter- : 干扰项为 intra- / extra- / sub- ----
    "Q-ENG-VOC-01-03-0006": {
        "stem": '词缀 "inter-" 的核心含义是？',
        "options": ["在...之间/相互", "在...内部/在内", "在...外部/额外", "在...下面/次级"],
        "answer": 0,
        "explanation": (
            "inter- = 在……之间、相互。拆词：international = inter + national(国家的) → 国家之间的 → 国际的；"
            "interact = inter + act(行动) → 相互作用 → 互动；internet = inter + net → 互联网络。"
            "辨析：inter-（之间）↔ intra-（内部）——intranet 是“内部网”，internet 是“互联网”。"
        ),
        "traps": ["inter-（之间）与 intra-（内部）只差一个字母：intranet 是企业内部网，internet 是互联网。"],
    },
    # ---- 7. trans- : 干扰项为 circum- / contra- / con- ----
    "Q-ENG-VOC-01-03-0007": {
        "stem": '词缀 "trans-" 的核心含义是？',
        "options": ["跨越/穿过/转变", "环绕/周围", "反对/相反", "共同/一起"],
        "answer": 0,
        "explanation": (
            "trans- = 穿过、跨越、转变。拆词：transport = trans(跨越) + port(搬运) → 搬运过去 → 运输；"
            "translate = trans + lat(带) + e → 带过去 → 翻译；transform = trans + form(形状) → 变形 → 转变。"
        ),
        "traps": ["把 trans-（跨越/转变）与 circum-（环绕）混记；circumspect 是“四处看→谨慎的”，与 trans- 无关。"],
    },
    # ---- 8. -tion/-sion : 干扰项为同场的其他词性后缀 ----
    "Q-ENG-VOC-01-03-0008": {
        "stem": '后缀 "-tion/-sion" 的核心含义是？',
        "options": ["名词后缀（行为/过程/结果）", "形容词后缀（...的）", "动词后缀（使...）", "副词后缀（...地）"],
        "answer": 0,
        "explanation": (
            "-tion/-sion 把动词变名词，表示行为、过程或结果。decide → decision（决定）；"
            "educate → education（教育）；expand → expansion（扩展）。"
            "心法：后缀定词性——看到 -tion/-sion 就按名词处理。"
        ),
        "traps": ["在长难句里把 -tion 结尾的词当成动词，导致找错谓语；decision/expansion 都是名词。"],
    },
    # ---- 9. dis-（用户截图那张） : 干扰项为 con- / pre- / re- ----
    "Q-ENG-VOC-01-03-0009": {
        "stem": '词缀 "dis-" 的核心含义是？',
        "options": ["否定/相反/分离", "共同/一起", "向前/预先", "回/再/重新"],
        "answer": 0,
        "explanation": (
            "dis- = 否定、相反、分离（两个义项要看具体词）。disagree = dis + agree(同意) → 不同意（否定）；"
            "discover = dis + cover(覆盖) → 揭开覆盖 → 发现（分离）；"
            "disconnect = dis + connect(连接) → 断开（分离）。"
            "辨析：dis-（否定/分离）↔ de-（向下/去除），decline 是“下降/拒绝”，不是“否定”。"
        ),
        "traps": ["把 dis- 与 de- 混用：dis- 是否定/分离，de- 是向下/去除；decline（下降）≠ discover（发现）。"],
    },
    # ---- 10. con-/com- : 干扰项为 se- / de- / dis- ----
    "Q-ENG-VOC-01-03-0010": {
        "stem": '词缀 "con-/com-" 的核心含义是？',
        "options": ["共同/一起", "分开/离开", "向下/去除", "否定/分离"],
        "answer": 0,
        "explanation": (
            "con-/com- = 共同、一起（com- 用在 b/m/p 前，是 con- 的语音变体）。"
            "connect = con + nect(绑) → 绑在一起 → 连接；combine = com + bine → 结合；"
            "confer = con + fer(带来) → 带到一起 → 商议。辨析：con-（一起）↔ se-（分开）。"
        ),
        "traps": ["把 con-/com- 与 se-（分开）记混；也不要把 com- 单独记成“完全”，它的本义是“一起”。"],
    },
    # ---- 11. -ment : 干扰项为同场的其他词性后缀 ----
    "Q-ENG-VOC-01-03-0011": {
        "stem": '后缀 "-ment" 的核心含义是？',
        "options": ["名词后缀（行为/结果）", "形容词后缀（具有...性质的）", "动词后缀（使...化）", "副词后缀（...地）"],
        "answer": 0,
        "explanation": (
            "-ment 把动词变名词，表示行为或结果。develop → development（发展）；"
            "manage → management（管理）；argue → argument（论点、争论）。"
            "辨析：同样表名词，-ment 侧重“行为/结果”，-tion 侧重“动作/状态”，-ness/-ity 侧重“性质”。"
        ),
        "traps": ["以为 -ment 表“人”：argument 是“论点/争论”，表“人”的是 -er/-or/-ist（arguer 才是争辩者）。"],
    },
    # ---- 12. un-/in-/im- : 改为语音同化应用，4 个选项同形态 ----
    "Q-ENG-VOC-01-03-0012": {
        "stem": '否定前缀 "in-" 在下列哪种情况下会发生语音同化，写成 "im-"？',
        "options": [
            "后接 b/m/p 开头的词根（如 possible）",
            "后接 l 开头的词根（如 legal）",
            "后接 r 开头的词根（如 regular）",
            "后接元音开头的词根（如 audible）",
        ],
        "answer": 0,
        "explanation": (
            "in- 是“最通用”的否定前缀，但会随词根首字母同化：接 b/m/p → im-（impossible, impartial）；"
            "接 l → il-（illegal）；接 r → ir-（irregular, irreversible）。"
            "例词：impossible(不可能的)、illegal(非法的)、irreversible(不可逆的)。"
        ),
        "traps": ["只记 un-/in- 两个否定前缀，遇到 irreversible、illegal 拆不出否定义，误判整句方向。"],
    },
    # ---- 13. over- : 干扰项为 under- / inter- / anti- ----
    "Q-ENG-VOC-01-03-0013": {
        "stem": '词缀 "over-" 的核心含义是？',
        "options": ["过度/超过", "不足/低于", "在...之间/相互", "反对/对抗"],
        "answer": 0,
        "explanation": (
            "over- = 过度、超过（也有“在……上方”的空间义）。overload = over + load(负载) → 超载；"
            "overcome = over + come → 从上面过来 → 克服；overlook = over + look → 从上往下看 → 俯瞰/忽视。"
            "辨析：over-（过度）↔ under-（不足）：overestimate / underestimate。"
        ),
        "traps": ["overlook 不是“过度看”：它由空间义“从上往下看”引申为“忽视”，写作里常被用错。"],
    },
    # ---- 14. mis- : 干扰项为 dis- / re- / con- ----
    "Q-ENG-VOC-01-03-0014": {
        "stem": '词缀 "mis-" 的核心含义是？',
        "options": ["错误/误", "否定/相反", "回/再/重新", "共同/一起"],
        "answer": 0,
        "explanation": (
            "mis- = 错误地、坏。mistake = mis + take(拿) → 拿错 → 错误；"
            "misunderstand = mis + understand(理解) → 误解；mislead = mis + lead(引导) → 误导。"
            "辨析：mis- 表“错误”，dis- 表“否定/分离”，de- 表“向下/去除”——三个都像否定，含义各不同。"
        ),
        "traps": ["把 mis-（错误）和 dis-（否定）当成同一个“不”：misunderstand 是“理解错了”，disagree 是“不同意”。"],
    },
    # ---- 15. -ful/-less : 4 个选项统一为"描述式"同形态 ----
    "Q-ENG-VOC-01-03-0015": {
        "stem": '后缀 "-ful" 与 "-less" 的关系是？',
        "options": [
            "-ful 表“充满...的”，-less 表“缺少...的”，二者互为反义",
            "二者含义相同，都表“充满...的”",
            "-ful 是名词后缀，-less 是动词后缀",
            "二者都是副词后缀，表“...地”",
        ],
        "answer": 0,
        "explanation": (
            "-ful 与 -less 是一对反义后缀：-ful = 充满……的（useful 有用的）；-less = 没有……的（useless 无用的）。"
            "同根对比：careful(小心的)/careless(粗心的)、hopeful(有希望的)/hopeless(绝望的)、groundless(毫无根据的)。"
        ),
        "traps": ["把 -less 理解成“少”而不是“没有”：groundless 是“毫无根据的”，不是“根据不多的”。"],
    },
}


# ---------------------------------------------------------------------------
# 三、其余英语选择题：修正选项形态失衡 / 凑数干扰项，并补 traps
# ---------------------------------------------------------------------------

ENGLISH_REWRITE = {
    # 长难句方法：原 3 个干扰项是"逐字翻译/从句头读到句尾/只关注从句"式的短句，
    # 只有正确项是完整方法描述 → 长度即答案。统一为等长方法描述。
    "Q-ENG-GRAM-01-0001": {
        "stem": "考研英语长难句分析的核心方法是？",
        "options": [
            "找主谓宾、剥离修饰成分，先抓住句子主干",
            "逐字直译，保持英文原句的语序",
            "先背下所有从句引导词，再逐词翻译",
            "只关注不认识的生词，忽略句子结构",
        ],
        "answer": 0,
        "explanation": (
            "长难句分析的四步：①找主干（主谓宾/主系表）；②识别从句类型（定语/状语/名词性）；"
            "③剥离修饰成分（定语、状语、插入语）；④逐层还原。关键是先抓骨架再补细节。"
        ),
        "traps": ["从句头按词序硬读，被嵌套从句和插入语带偏，读完不知道句子主干是谁做什么。"],
    },
    # 干扰项里"根据个人理解判断"属凑数项，且正确项表述最长
    "Q-ENG-READ-01-0001": {
        "stem": "考研英语阅读中，作者态度题的解题关键是？",
        "options": [
            "抓形容词、副词、语气词等态度线索词",
            "根据自己对文章主题的好恶来判断",
            "只看文章最后一段的结论句",
            "选表述最中立、最不绝对的那个选项",
        ],
        "answer": 0,
        "explanation": (
            "作者态度题的解题线索：①形容词/副词（positive/negative/critical/objective）；"
            "②语气词（unfortunately/surprisingly）；③转折词后的内容（but/however 后往往是真实态度）；"
            "④举例的倾向性。注意区分作者态度与文中引用他人的观点。"
        ),
        "traps": ["把文中引用的他人观点当成作者态度；作者态度通常在转折词之后才亮明。"],
    },
    # 原答案带括号解释、干扰项没有 → 格式泄露；现在 4 项统一为"类型：说明"
    "Q-ENG-READ-01-0002": {
        "stem": "考研阅读中最常见的干扰选项类型是？",
        "options": [
            "偷换概念：用原文关键词，但改变其含义或逻辑关系",
            "直接矛盾：选项表述与原文完全相反，容易排除",
            "完全无关：选项内容在原文中找不到任何对应",
            "表述绝对：选项中出现 must、never 等绝对词",
        ],
        "answer": 0,
        "explanation": (
            "偷换概念是考研阅读最常见的干扰方式：选项使用原文中的关键词，但改变了含义、范围、程度或逻辑关系，"
            "因此“看着眼熟”不等于正确。其他常见干扰类型：以偏概全、无中生有、过度推断、张冠李戴。"
            "直接矛盾或完全无关的选项通常一读就能排除。"
        ),
        "traps": ["因为选项里出现原文原词就选它——原词重现恰恰是偷换概念的诱饵，必须核对逻辑关系是否被改。"],
    },
    # 干扰项"推理题比细节题更容易""只出现在文章最后"属凑数
    "Q-ENG-READ-03-0001": {
        "stem": "阅读中推理判断题与细节理解题的核心区别是？",
        "options": [
            "推理题答案不直接出现，需在原文信息上做合理推断",
            "推理题答案能在原文中直接找到，只是换了说法",
            "推理题只看段落首尾句，细节题需通读全段",
            "推理题考生词猜测，细节题考长难句分析",
        ],
        "answer": 0,
        "explanation": (
            "细节题：答案可以在原文中直接找到（同义替换）。推理题：答案不在原文中直接出现，"
            "需要根据原文信息进行合理推断，但不得过度推断，必须有原文依据支撑。"
        ),
        "traps": ["把推理题做成“凭常识推断”，选了一个原文没有依据、但生活中成立的选项。"],
    },
    # 长句翻译：干扰项是"逐词直译/保留语序/省略从句"式的短句，正确项最长
    "Q-ENG-TRN-01-0001": {
        "stem": "英语长句翻译成中文时，最常用的方法是？",
        "options": [
            "断句拆分，按中文逻辑重新排列语序",
            "逐词直译，保持英文原句的语序",
            "先译所有从句，主句一律提到句首",
            "省略全部修饰成分，只译句子主干",
        ],
        "answer": 0,
        "explanation": (
            "英语重形合（靠连接词），中文重意合（短句流水排列）。长句翻译三步："
            "①断句拆分（在从句、分词短语处断开）；②调整语序（定语后置→前置，状语提前）；"
            "③增词减词（补充隐含主语，省略冗余连接词）。"
        ),
        "traps": ["逐词直译导致译文“英式中文”，语序和搭配都不通；翻译评分首先看通顺，其次才是准确。"],
    },
    # 完形填空第一步：干扰项已是等长短语，仅补 traps
    "Q-ENG-TRN-02-0001": {
        "traps": ["不看全文直接逐空填空，许多空要靠下文的复现或转折才能定，先填后改反而固化错误。"],
    },
    # 猜词策略：干扰项"跳过不看"属凑数，统一为等长的四种处理策略
    "Q-ENG-VOC-01-0002": {
        "stem": "在考研英语阅读中，遇到不认识的单词时最佳策略是？",
        "options": [
            "根据上下文语境和词根词缀推测词义",
            "立即查词典确认词义后再继续读",
            "跳过该词，只抓句子的主干大意",
            "标记该词，考后统一查词典处理",
        ],
        "answer": 0,
        "explanation": (
            "考研英语阅读考查的重要能力之一就是通过上下文推测生词含义。最佳策略："
            "①看上下文的解释、举例、对比关系；②分析词根词缀判断词性与感情色彩；③回到句子验证。"
        ),
        "traps": ["因一个生词卡住反复回读，既超时又打断了对段落逻辑的把握；考场上生词推测不出就果断跳过。"],
    },
    # 词根词缀法优势：干扰项长度与正确项差距过大
    "Q-ENG-VOC-01-0001": {
        "stem": "考研英语词汇学习中，“词根+词缀”方法的核心优势是？",
        "options": [
            "按词根理解词义本质、按词缀判断词性，批量拓展同族词",
            "记住更多单词的字母拼写顺序，减少拼写错误",
            "一次性掌握每个单词的全部义项和固定搭配",
            "脱离上下文也能准确确定单词在句中的含义",
        ],
        "answer": 0,
        "explanation": (
            "词根词缀法的核心：①词根决定词义核心（spect=看）；②前缀改变含义方向（re-=再次）；"
            "③后缀决定词性（-tion=名词）。掌握常见词根词缀后可批量理解和记忆同族词，效率远高于死记硬背。"
        ),
        "traps": ["把词根词缀当成万能钥匙：它帮你“猜个八九不离十”，最终词义仍要回到上下文确认，尤其是一词多义。"],
    },
    # 小作文最常考类型：干扰项"诗歌创作/小说片段"属凑数
    "Q-ENG-WRT-02-0001": {
        "stem": "考研英语小作文（Part A 应用文）最常考的类型是？",
        "options": [
            "书信（建议信、申请信、投诉信等）",
            "通知与告示",
            "摘要（abstract）写作",
            "图表描述",
        ],
        "answer": 0,
        "explanation": (
            "考研英语小作文（Part A）最常考应用文写作，其中书信类占绝大多数：建议信、申请信、投诉信、"
            "道歉信、推荐信等；通知与告示次之。格式（称呼、正文、落款）和语域（正式/半正式）是关键评分点，字数约 100 词。"
        ),
        "traps": ["只背模板不看题目要求的语域：给朋友写信用了公函口吻，或给机构写信用了口语缩写，都会扣分。"],
    },
    # 形近词 complement/compliment：D 项"前者是动词，后者是名词"是伪区分
    "Q-ENG-VOC-03-0001": {
        "options": [
            "前者是“补充、补足物”，后者是“赞美、恭维”",
            "前者是“赞美、恭维”，后者是“补充、补足物”",
            "两者含义相同，都表示“使完整”",
            "前者只能作名词，后者只能作动词",
        ],
        "answer": 0,
        "traps": ["按“compliment 里有 i，就是我夸你”来记即可：i 对应“赞美”，e 对应“补充”（complete 也是 e）。"],
    },
    # 七选五：A 项"重复最多的生僻单词"表述含混，改为真正的"原词重现"陷阱
    "Q-TGT-DEE1176B": {
        "options": [
            "选项中与原文重复出现的单词最多的那一段",
            "段落间的逻辑连接词、代词指代及上下文的同义替换",
            "选项句子的语法复杂度和长度",
            "文章首尾段的中心思想，忽略中间段落",
        ],
        "answer": 1,
        "traps": ["仅凭“原词重现”盲目选择，忽略逻辑连贯性和代词指代是否成立，正好掉进命题人设的“原词复现”陷阱。"],
    },
    # 排序题判定首段：C/D 原为凑数项，改为排序题方法论里真实的三个错误判据
    "Q-TGT-10C51A32": {
        "options": [
            "引出话题、不含指代前文的代词和逻辑转折词的段落",
            "以 however、therefore 等强逻辑连接词开头的段落",
            "含 this、these 等指代前文的代词、需要上文支撑的段落",
            "含 the + 名词等定指回指信息、暗示上文已出现过的段落",
        ],
        "answer": 0,
        "explanation": (
            "首段用于引出话题，因此不含指代前文的代词（如 this problem），也不会以强转折词（如 however）开头，"
            "通常也不含 the + 名词这类定指回指信息。三个干扰项正是排序题中判断“这段不可能是首段”的三条标准。"
        ),
        "traps": ["把含 this/that 的段落误认为首段，忽略代词必须有前文指代这一基本逻辑规则。"],
    },
    # 建议信语气：内容无误，仅剥离字母前缀（见 strip 阶段）
    # 大作文第二段：D 项"具体法律条文"属凑数
    "Q-TGT-899A7EDC": {
        "options": [
            "详细描述图画的每一个视觉细节",
            "解释图画寓意，并分析其背后的原因或影响",
            "列举三个与图画完全无关的名人名言",
            "换词重申第一段的图画描述，不再展开",
        ],
        "answer": 1,
        "traps": ["把第二段写成第一段“细节描述”的延续，或脱离图画空谈大道理，导致内容空洞、论证缺失。"],
    },
    # ---- 以下 5 张为审计后二次收尾：选项长度失衡 / 缺 traps ----
    # 建议信语气：正确项带着长例句，比其余项长 3 倍 → 统一为"语气特征 +（例句）"
    "Q-TGT-515499BF": {
        "options": [
            "语气强硬：使用祈使句（You must do this immediately.）",
            "委婉礼貌：使用建议句型（I would suggest that...）",
            "口语随意：使用网络缩写（u should...）",
            "通篇被动：避免提及建议对象（It should be done.）",
        ],
        "answer": 1,
        "traps": ["语气过于强硬（命令式）或过于随意（口语化），不符合正式/半正式书信的语域要求，格式与语体都要扣分。"],
    },
    # 大作文结构：一段式/两段式太短，只有正确项是完整流程描述
    "Q-ENG-WRT-01-0001": {
        "options": [
            "一段式：直接论述，不分层次",
            "三段式：描述图画→分析原因→给出建议或总结",
            "四段式：引言→正面论述→反面论述→结论",
            "两段式：先描述图画，再作评论",
        ],
        "answer": 1,
        "traps": ["首段花大量篇幅描述图画细节，挤掉中段论证；图画描述控制在 3 句以内，把篇幅留给原因分析。"],
    },
    # 小作文类型：正确项带括号列举显得最长
    "Q-ENG-WRT-02-0001": {
        "options": [
            "书信类（建议信、申请信、投诉信）",
            "通知与告示类应用文",
            "摘要（abstract）写作",
            "图表描述类说明文",
        ],
        "answer": 0,
        "traps": ["只背模板不看题目要求的语域：给朋友写信用了公函口吻，或给机构写信用了口语缩写，都会扣分。"],
    },
    # 虚拟语气：仅补 traps（选项本身已是同形态"主句/从句"对）
    "Q-ENG-GRAM-02-0001": {
        "traps": ["把与现在事实相反（did/were → would do）和与过去事实相反（had done → would have done）的时态配对记反。"],
    },
    # economic/economical：正确项带两个例句，长度 73 字远超其余项（例句已写在解析里，选项内不再重复）
    "Q-ENG-VOC-01-03-0031": {
        "options": [
            "economic 是“经济的”；economical 是“节约的”",
            "economic 是“节约的”；economical 是“经济的”",
            "两者完全同义，在任何语境下都可互换使用",
            "economic 形容人节约；economical 形容经济",
        ],
        "answer": 0,
        "explanation": (
            "后缀一变，意思就变：economic = 经济的（economic growth 经济增长）；"
            "economical = 节约的、实惠的（an economical car 省油的车）。同类：historic（有历史意义的）/ historical（历史的）。"
        ),
        "traps": ["写作中想说“经济实惠”却写成 economic，把“经济的”和“节约的”混用，是考研写作的典型用词错误。"],
    },
    # 说明：Q-TGT-7D0635C9（ob- 一词多义）内容本身优良、选项同形态，
    # 只需走第二阶段剥离字母前缀，故不在此重复声明。
}


# ---------------------------------------------------------------------------
# 四、新增 20 张卡（均来自 词根词缀总表.md）
#     options 首项即正确答案，入库前统一走 shuffle_opts 打散。
# ---------------------------------------------------------------------------

ROOT_GLOSS_TRAPS = "词根含义要成组记：同族的词根含义相近，混记会在完形近义词辨析里连续失分。"

NEW_CARDS = [
    # ---- 心法 ----
    {
        "stem": "词根词缀法的核心心法是“词根定意思，前缀定方向，后缀定词性”。据此，判断生词的词性应看？",
        "options": ["后缀", "前缀", "词根", "前缀 + 词根"],
        "answer": 0,
        "explanation": (
            "三步拆词：①切后缀定词性；②切前缀定方向；③找词根定意思。"
            "示范：unmistakable → 切出 -able（形容词）→ un-（否定）→ mistak(e)（弄错）→ 不可能被弄错的。"
        ),
        "traps": ["遇到生词先猜词义、忽略后缀，导致在长难句里把名词当成谓语动词，找错句子主干。"],
        "tags": ["词根词缀", "英语词汇", "拆词心法"],
    },
    # ---- 高频词根（干扰项取自同一张词根表，必须真辨析）----
    {
        "stem": "词根 “ced / cess” 的核心含义是？",
        "options": ["走、让", "站", "带来", "扔"],
        "answer": 0,
        "explanation": (
            "ced/cess = 走、让。proceed = pro(向前) + ceed(走) → 向前走 → 继续进行；"
            "concede = con(一起) + cede(让) → 让一步 → 承认；access = ac(朝向) + cess(走) → 走近 → 接近。"
            "同族：exceed(超过)、recession(衰退)。"
        ),
        "traps": ["把 ced/cess 与 sist（站）混记：insist 是“站上去不动摇”（坚称），proceed 才是“向前走”。"],
        "tags": ["词根词缀", "英语词汇", "词根ced/cess"],
    },
    {
        "stem": "词根 “sist / sta” 的核心含义是？",
        "options": ["站", "走、让", "送、派", "引导"],
        "answer": 0,
        "explanation": (
            "sist/sta = 站。insist = in(加强) + sist(站) → 站上去不动摇 → 坚称；"
            "resist = re(反/回) + sist → 站着顶回去 → 抵抗；consist = con(一起) + sist → 站在一起 → 组成。"
            "同源：stable(稳定的)、status(地位)。"
        ),
        "traps": ["把 resist（抵抗，re-=反/回）与 insist（坚称，in-=加强）都笼统记成“坚持”，近义词辨析会失分。"],
        "tags": ["词根词缀", "英语词汇", "词根sist/sta"],
    },
    {
        "stem": "词根 “fer” 的核心含义是？",
        "options": ["带来", "拿、抓", "说", "看"],
        "answer": 0,
        "explanation": (
            "fer = 带来、拿。transfer = trans(跨越) + fer → 带过去 → 转移；"
            "confer = con(一起) + fer → 带到一起 → 商议、授予；prefer = pre(在前) + fer → 拿到前面 → 更喜欢；"
            "refer = re(回) + fer → 带回到 → 提及、参考。"
        ),
        "traps": ["prefer 不是“提前带来”：pre-（在前）表“更看重”，故 prefer 义为“更喜欢”，比较时后接 to 而非 than。"],
        "tags": ["词根词缀", "英语词汇", "词根fer"],
    },
    {
        "stem": "词根 “ject” 的核心含义是？",
        "options": ["扔", "放", "关", "转"],
        "answer": 0,
        "explanation": (
            "ject = 扔。reject = re(回) + ject → 扔回去 → 拒绝；inject = in(进入) + ject → 扔进去 → 注射；"
            "object = ob(朝向/反对) + ject → 扔到面前 → 物体/反对；project = pro(向前) + ject → 向前扔 → 项目、投射。"
        ),
        "traps": ["把 object（反对/物体）与 reject（拒绝）都笼统记成“拒绝”，忽略 ob-（朝向）与 re-（回）的方向差别。"],
        "tags": ["词根词缀", "英语词汇", "词根ject"],
    },
    {
        "stem": "词根 “mit / miss” 的核心含义是？",
        "options": ["送、派", "引导", "带来", "站"],
        "answer": 0,
        "explanation": (
            "mit/miss = 送、派。submit = sub(在下) + mit → 送到下面 → 提交、屈服；"
            "admit = ad(朝向) + mit → 送进去 → 承认、准许进入；commit = con(一起/加强) + mit → 交付 → 承诺、犯罪；"
            "dismiss = dis(离开) + miss → 送走 → 解散、解雇。"
        ),
        "traps": ["admit 与 submit 都以 -mit 结尾，含义全由前缀决定：admit 是“准入/承认”，submit 是“提交/屈服”。"],
        "tags": ["词根词缀", "英语词汇", "词根mit/miss"],
    },
    {
        "stem": "词根 “duc / duct” 的核心含义是？",
        "options": ["引导", "送、派", "拿、抓", "关"],
        "answer": 0,
        "explanation": (
            "duc/duct = 引导。conduct = con(一起) + duct → 引导到一起 → 指挥、实施；"
            "induce = in(进入) + duce → 引入 → 诱导；deduce = de(向下/离开) + duce → 引导出来 → 推断；"
            "produce = pro(向前) + duce → 引出来 → 生产。"
        ),
        "traps": ["deduce（推断）与 induce（诱导）只差一个前缀且方向相反：de- 是把结论“引出来”，in- 是把你“引进去”。"],
        "tags": ["词根词缀", "英语词汇", "词根duc/duct"],
    },
    {
        "stem": "词根 “spect / spic” 的核心含义是？",
        "options": ["看", "说", "转", "扔"],
        "answer": 0,
        "explanation": (
            "spect/spic = 看。perspective = per(贯穿) + spect → 看穿 → 视角；"
            "conspicuous = con(加强) + spic → 人人都看得见 → 显眼的；speculation = spect + -ulation → 看来看去 → 推测。"
            "同义词根 vid/vis 也表“看”：visible、evident。"
        ),
        "traps": ["把 perspective（视角）与 prospect（前景，pro- 向前看）混淆——同为 spect 词根，方向由前缀决定。"],
        "tags": ["词根词缀", "英语词汇", "词根spect/spic"],
    },
    {
        "stem": "词根 “dict” 的核心含义是？",
        "options": ["说", "看", "引导", "带来"],
        "answer": 0,
        "explanation": (
            "dict = 说。predict = pre(在前) + dict → 事先说 → 预言；"
            "contradict = contra(相反) + dict → 说相反的话 → 反驳；"
            "indicate = in(朝向) + dic + -ate → 指向并说出 → 表明。"
        ),
        "traps": ["把 contradict（反驳）与 predict（预言）都记成“说”，写作中表达“与……相矛盾”时用错词。"],
        "tags": ["词根词缀", "英语词汇", "词根dict"],
    },
    {
        "stem": "词根 “vers / vert” 的核心含义是？",
        "options": ["转", "关", "站", "来"],
        "answer": 0,
        "explanation": (
            "vers/vert = 转。reverse = re(回) + vers → 转回去 → 颠倒、逆转；"
            "convert = con(完全) + vert → 整个转过来 → 转换；diverse = di(分开) + vers → 转向不同方向 → 多样的。"
            "irreversible = ir(否定) + re(回) + vers(转) + ible(能被…的) → 不可被逆转的。"
        ),
        "traps": ["把 diverse（多样的）当成“相反的”（那是 reverse/adverse）；diverse 的核心是“转向不同方向”。"],
        "tags": ["词根词缀", "英语词汇", "词根vers/vert"],
    },
    {
        "stem": "词根 “clud / clus” 的核心含义是？",
        "options": ["关", "放", "来", "伸"],
        "answer": 0,
        "explanation": (
            "clud/clus = 关。conclude = con(一起) + clud → 全关起来 → 结束、得出结论；"
            "exclude = ex(向外) + clud → 关在外面 → 排除；include = in(进入) + clud → 关进来 → 包含。"
            "同族：preclude(阻止)。"
        ),
        "traps": ["exclude（排除）与 include（包含）只需记前缀方向：ex- 向外关＝排除，in- 向里关＝包含。"],
        "tags": ["词根词缀", "英语词汇", "词根clud/clus"],
    },
    # ---- 拆词应用（词根词缀总表.md 第四节示范）----
    {
        "stem": "用词根词缀法拆解 “unmistakable”（un- + mistak(e) + -able），其最准确的含义是？",
        "options": [
            "不可能被弄错的 → 明确无误的",
            "容易被弄错的 → 模棱两可的",
            "能够弄错的 → 情有可原的",
            "已经被弄错的 → 错误的",
        ],
        "answer": 0,
        "explanation": (
            "un-（否定）+ mistake（弄错）+ -able（能被……的，含被动）→ 不可能被弄错的 → 明确无误的。"
            "这是 -able 含被动义的典型例词，考研阅读中常用来表达“确凿无疑”。"
        ),
        "traps": ["把 -able 当主动解，理解成“能弄错的”；-able 表“能被……的”，unmistakable 义为“绝不可能被弄错”。"],
        "tags": ["词根词缀", "英语词汇", "拆词示范"],
    },
    {
        "stem": "拆解 “concede”（con- 一起/加强 + cede 走/让），其核心含义是？",
        "options": ["让一步 → 承认、让步", "一起走 → 同行", "往回走 → 撤回", "向前走 → 前进"],
        "answer": 0,
        "explanation": (
            "con-（一起/加强）+ cede（走、让）→ 让一步 → 承认、让步。"
            "同族对比：concession(让步)、recede(后退)、proceed(前进)、exceed(超过)。"
        ),
        "traps": ["把 concede（承认、让步）与 recede（后退）、proceed（前进）混为一谈；词根相同，含义全由前缀方向决定。"],
        "tags": ["词根词缀", "英语词汇", "拆词示范"],
    },
    {
        "stem": "拆解 “insist”（in- 加强 + sist 站），其核心含义是？",
        "options": ["站上去不动摇 → 坚称、坚持", "站到一边 → 回避", "站着不动 → 停滞", "站到最后 → 幸存"],
        "answer": 0,
        "explanation": (
            "in-（加强）+ sist（站）→ 站在上面不动摇 → 坚称、坚持。"
            "辨析：resist = re(反) + sist → 站着顶回去 → 抵抗；consist = con(一起) + sist → 站在一起 → 组成。"
        ),
        "traps": ["搭配错误：insist 后接 on doing（insist on doing sth），没有 insist to do 的用法，完形与写作都常考。"],
        "tags": ["词根词缀", "英语词汇", "拆词示范"],
    },
    {
        "stem": "拆解 “self-evident”（self 自身 + e- 向外 + vid 看 + -ent …的），其含义是？",
        "options": ["一看就看得见 → 不言自明的", "只看得见自己 → 自负的", "需要向外求证 → 有争议的", "看不清自身 → 不自知的"],
        "answer": 0,
        "explanation": (
            "self（自身）+ e-（向外）+ vid（看）+ -ent（……的）→ 自身向外一看就看得见 → 不言自明的。"
            "词根 vid/vis 表“看”：visible、provide、evident 同源。"
        ),
        "traps": ["把 e-（向外）误认为否定前缀；e-/ex- 表“向外”，eliminate、evident、exclude 都含此义。"],
        "tags": ["词根词缀", "英语词汇", "拆词示范"],
    },
    # ---- 后缀陷阱 ----
    {
        "stem": "“economic” 与 “economical” 的词义区别是？",
        "options": [
            "economic 是“经济的”（如 economic growth）；economical 是“节约的”（如 an economical car）",
            "economic 是“节约的”；economical 是“经济的”",
            "两者完全同义，可以互换使用",
            "economic 形容人节约；economical 形容国家经济",
        ],
        "answer": 0,
        "explanation": (
            "后缀一变，意思就变：economic = 经济的（economic growth 经济增长）；"
            "economical = 节约的、实惠的（an economical car 省油的车）。同类：historic（有历史意义的）/ historical（历史的）。"
        ),
        "traps": ["写作中想说“经济实惠”却写成 economic，把“经济的”和“节约的”混用，是考研写作的典型用词错误。"],
        "tags": ["词根词缀", "英语词汇", "后缀陷阱"],
    },
    {
        "stem": "由 “benefit”（好处、福利）派生的 “beneficial”，其含义是？",
        "options": ["有益的、有好处的（不含“福利”义）", "与福利制度有关的", "无益的、有害的", "有资格领取福利的"],
        "answer": 0,
        "explanation": (
            "后缀一变，意思就变：benefit（好处、福利）→ beneficial（有益的，不含“福利”义）。"
            "be beneficial to sth = 对……有益。词根 bene- 表“好”：benefactor(恩人)、benevolent(仁慈的)。"
        ),
        "traps": ["把 beneficial 当成“福利的”：写 beneficial policy 想表达“福利政策”，实际成了“有益的政策”（福利政策是 welfare policy）。"],
        "tags": ["词根词缀", "英语词汇", "后缀陷阱"],
    },
    # ---- 比较级词根 ----
    {
        "stem": "关于 superior / inferior / prior / senior / junior 的用法，正确的是？",
        "options": [
            "本身即比较级，后接 to，不加 more、也不用 than",
            "需加 more 构成比较级，后接 than",
            "需加 more 构成比较级，后接 to",
            "本身即比较级，但后接 than",
        ],
        "answer": 0,
        "explanation": (
            "这组词本身即比较级形式，后接 to：be superior to（优于）、be inferior to（劣于）、"
            "be prior to（先于）、be senior to（年长于）。既不能再加 more，也不能用 than。"
        ),
        "traps": ["写成 more superior than 或 superior than，属双重比较级错误，是考研写作的高频失分点。"],
        "tags": ["词根词缀", "英语词汇", "比较级词根"],
    },
    # ---- 形近前缀应用 ----
    {
        "stem": "下列单词中，前缀表示“向前”（而非“在前”）的是？",
        "options": [
            "proceed（pro- 向前 + ceed 走）",
            "predict（pre- 在前 + dict 说）",
            "prepare（pre- 在前 + pare 准备）",
            "prevent（pre- 在前 + vent 来）",
        ],
        "answer": 0,
        "explanation": (
            "pro- 表“向前”（方向），pre- 表“在前”（时间）。proceed = 向前走 → 继续进行；"
            "predict / prepare / prevent 都是 pre-，表“事先”。同族：progress(前进)、promote(促进)。"
        ),
        "traps": ["把 pro- 与 pre- 都记成“前”：pro- 是空间/方向上的向前，pre- 是时间上的在前，二者不可互换。"],
        "tags": ["词根词缀", "英语词汇", "形近前缀"],
    },
    {
        "stem": "下列单词中，前缀 “sub-” 表示“次级、下属”（而非单纯空间“下面”）的是？",
        "options": [
            "subordinate（sub- 在下 + ordin 顺序 → 下级的、下属）",
            "subway（sub- 在下 + way 路 → 地铁）",
            "submarine（sub- 在下 + marine 海 → 潜水艇）",
            "submerge（sub- 在下 + merge 沉 → 浸没）",
        ],
        "answer": 0,
        "explanation": (
            "sub- 有两个义项：空间“在……下面”（subway、submarine、submerge）与等级“次级、下属”"
            "（subordinate、subsidiary）。本题问等级义，四个选项里只有 subordinate 表“下级的、下属”。"
        ),
        "traps": ["把 sub- 一律理解为空间的“下面”，遇到 subordinate（下属的）、subsequent（随后的）就无法准确释义。"],
        "tags": ["词根词缀", "英语词汇", "形近前缀"],
    },
]


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------

def strip_all_letter_prefixes(conn, dry):
    """全库剥离选项内嵌的字母前缀（仅当字母与选项序号一致）。"""
    fixed, warned = 0, []
    for qid, content in conn.execute("SELECT id, content FROM questions").fetchall():
        try:
            ct = json.loads(content)
        except Exception:
            continue
        opts = ct.get("options")
        if not isinstance(opts, list) or not opts:
            continue
        new_opts, changed = [], False
        for i, o in enumerate(opts):
            if not isinstance(o, str):
                new_opts.append(o)
                continue
            stripped, warn = strip_letter_prefix(o, i)
            if warn:
                warned.append(f"{qid} 选项{i}: {warn} → {o[:30]}")
            if stripped != o:
                changed = True
            new_opts.append(stripped)
        if changed:
            ct["options"] = new_opts
            if not dry:
                conn.execute("UPDATE questions SET content=? WHERE id=?",
                             (json.dumps(ct, ensure_ascii=False), qid))
            print(f"  [剥离字母前缀] {qid}")
            fixed += 1
    for w in warned:
        print(f"  ⚠️ {w}")
    return fixed


def apply_rewrite(conn, qid, patch, dry, shuffle=True):
    row = conn.execute("SELECT content FROM questions WHERE id=?", (qid,)).fetchone()
    if not row:
        print(f"  ⚠️ 找不到 {qid}，跳过")
        return False
    ct = json.loads(row[0])
    if "options" in patch:
        opts, ans = patch["options"], patch["answer"]
        if shuffle:
            opts, ans = shuffle_opts(qid, opts, ans)
        assert opts[ans] == patch["options"][patch["answer"]], f"{qid} 打乱后答案错位"
        ct["options"], ct["answer"] = opts, ans
    for k in ("stem", "explanation", "traps"):
        if k in patch:
            ct[k] = patch[k]
    if not dry:
        conn.execute("UPDATE questions SET content=? WHERE id=?",
                     (json.dumps(ct, ensure_ascii=False), qid))
    return True


def insert_new_card(conn, seq, card, dry):
    qid = f"Q-ENG-VOC-01-03-{seq:04d}"
    exists = conn.execute("SELECT 1 FROM questions WHERE id=?", (qid,)).fetchone()
    if exists:
        print(f"  [已存在] {qid}")
        return 0
    opts, ans = shuffle_opts(qid, card["options"], card["answer"])
    assert opts[ans] == card["options"][card["answer"]], f"{qid} 打乱后答案错位"
    content = {
        "stem": card["stem"],
        "options": opts,
        "answer": ans,
        "explanation": card["explanation"],
        "traps": card["traps"],
        "tags": card["tags"],
    }
    if not dry:
        conn.execute(
            "INSERT INTO questions (id, topic_id, type, difficulty, source, content) "
            "VALUES (?, 'ENG-VOC-01-03', 'choice', 0.4, ?, ?)",
            (qid, NEW_SOURCE, json.dumps(content, ensure_ascii=False)),
        )
        # 新卡：state=0（新卡），due_date=今天；FSRS 字段留默认，交由调度器接管
        conn.execute(
            "INSERT INTO cards (id, question_id, state, due_date) VALUES (?, ?, 0, ?)",
            (qid, qid, TODAY),
        )
    print(f"  [新增] {qid} {card['stem'][:38]}")
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写库")
    args = ap.parse_args()
    dry = args.dry_run

    conn = sqlite3.connect(DB_PATH)
    print(f"数据库：{DB_PATH}{'（dry-run）' if dry else ''}\n")

    print("一、重写 15 张词根词缀卡的选项设计")
    n = sum(apply_rewrite(conn, q, p, dry) for q, p in AFFIX_REWRITE.items())
    print(f"  → {n} 张\n")

    print("二、剥离全库选项内嵌的字母前缀")
    n = strip_all_letter_prefixes(conn, dry)
    print(f"  → {n} 张\n")

    print("三、修正其余英语选择题的选项形态与凑数干扰项")
    n = sum(apply_rewrite(conn, q, p, dry) for q, p in ENGLISH_REWRITE.items())
    print(f"  → {n} 张\n")

    print("四、按词根词缀总表新增卡片")
    n = sum(insert_new_card(conn, 16 + i, c, dry) for i, c in enumerate(NEW_CARDS))
    print(f"  → {n} 张\n")

    if not dry:
        conn.commit()
    conn.close()
    print("完成。" + ("（dry-run，未写库）" if dry else ""))


if __name__ == "__main__":
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
add_cards_grammar_math_20260913.py — 补「英语语法卡」与「数学计算陷阱卡」

【背景】英语语法只有 4 张卡（12 个语法子话题几乎全空），数学只有知识点卡、
没有一张计算陷阱卡。本脚本按笔记原文出卡：

  英语语法 ← English/grammar/考研英语语法笔记.md、长难句笔记.md
             覆盖 时态语态 / 三大从句 / 虚拟语气 / 非谓语 / 倒装
                  / 嵌套从句 / 分隔结构 / 省略还原 / 多重后置定语 / 独立主格
                  / 长难句拆分 / 主谓一致
  数学陷阱 ← Math/高数/计算陷阱.md（44 个）、线代/计算陷阱.md（13 个）、
             概率论/计算陷阱.md（8 个）

【质量红线】（与 tools/check_card_quality.py 同口径）
  1. 四个选项同形态：不能一条裸释义、其余带"标签(解释)"——格式会泄露答案
  2. 干扰项取同语义场真易混项，不用"完全无关"式凑数项
  3. 选项长度接近（最长 ≤ 最短的 2.5 倍）
  4. 必带 traps 易错点

【渲染约定】大盘闪卡区已支持按需 KaTeX（2026-09-13 改造：全局 richText + texWrap，
  只有内容里真出现 $...$ 才懒加载本地 KaTeX，加载完成前显示源码兜底）。
  但本脚本产出的数学卡仍用 Unicode 数学符号（∫ √ ≤ ² ₓ ′ π σ）而不是 $...$ LaTeX：
  周报（weekly_flashcard_report.py → 飞书）与题库导出走纯文本路径，
  写 LaTeX 会在那些地方露出源码。若哪天只用大盘看卡，可再统一改成 LaTeX。

用法:
    python tools/add_cards_grammar_math_20260913.py --dry-run
    python tools/add_cards_grammar_math_20260913.py
"""

import argparse
import importlib.util
import io
import json
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
SOURCE = f"语法与计算陷阱-{TODAY}"


def _load_gate():
    """复用 tools/card_quality.py 的选项质量闸门与打乱函数（单一事实来源）。"""
    sys.path.insert(0, str(BASE_DIR / "src" / "tools"))
    from card_quality import check_options_quality, shuffle_opts
    return check_options_quality, shuffle_opts


check_options_quality, shuffle_opts = _load_gate()


# ===========================================================================
# 一、英语语法卡（22 张）
#     options[0] 为正确答案；入库前统一打乱。
# ===========================================================================

GRAMMAR_CARDS = [
    # ---------------- ENG-GRAM-01 核心语法（主谓一致）----------------
    {
        "topic": "ENG-GRAM-01",
        "stem": "主谓一致：Bread and butter ___ my favorite breakfast. 填 is 还是 are，判据是什么？",
        "options": [
            "A and B 指同一整体概念（同一人/同一物）时，谓语用单数",
            "A and B 指两个独立个体时，谓语也用单数",
            "只要句中出现了 and，谓语一律用复数",
            "只要句中出现了 and，谓语一律用单数",
        ],
        "answer": 0,
        "explanation": (
            "A and B 视为一个不可分割的复合概念时用单数谓语：Bread and butter is my favorite breakfast."
            "（面包抹黄油＝一种食物）。第二项前重复冠词则表两个独立个体，用复数："
            "The secretary and treasurer is coming.（同一人，无第二个 the）"
            "／ The secretary and the treasurer are coming.（两个人）。"
        ),
        "traps": ["只看 and 就一律用复数，忽略第二项前是否重复冠词。"],
        "tags": ["语法", "主谓一致"],
    },
    {
        "topic": "ENG-GRAM-01",
        "stem": "主谓一致：主语是分数或百分数时，谓语的单复数取决于什么？",
        "options": [
            "of 后面的名词：不可数用单数，可数名词复数用复数",
            "分数或百分数的分子：分子大于 1 就用复数谓语",
            "句子的时态：现在时用单数，过去时用复数谓语",
            "of 前面的分数本身：分母的大小决定单复数",
        ],
        "answer": 0,
        "explanation": (
            "分数/百分数作主语时，谓语单复数由 of 后的名词决定："
            "Two thirds of the work is done.（work 不可数 → 单数）"
            "／ Two thirds of the students are present.（students 复数 → 复数）；"
            "50% of the water is polluted. ／ 50% of residents are satisfied."
        ),
        "traps": ["只看分子 two thirds 就判复数，忽略 of 后名词的单复数。"],
        "tags": ["语法", "主谓一致"],
    },
    {
        "topic": "ENG-GRAM-01",
        "stem": "主谓一致：关于 a number of 与 the number of，下列判断正确的是？",
        "options": [
            "a number of + 复数谓语；the number of + 单数谓语",
            "a number of + 单数谓语；the number of + 复数谓语",
            "两者都接单数谓语，people 也按单数处理",
            "两者都接复数谓语，people 按复数处理",
        ],
        "answer": 0,
        "explanation": (
            "a number of = 许多（后接复数谓语）：A number of students are absent.；"
            "the number of = ……的数量（后接单数谓语）：The number of students is rising.。"
            "另外 people / police / cattle 永远作复数：The police are investigating."
        ),
        "traps": ["把 a number of 和 the number of 混用同一谓语形式；把 people/police 当单数。"],
        "tags": ["语法", "主谓一致"],
    },
    # ---------------- ENG-GRAM-01-01 时态与语态 ----------------
    {
        "topic": "ENG-GRAM-01-01",
        "stem": "By the time I see you, I ___ my degree. 表达「到我见你时，我已经拿到学位了」，空白处应填？",
        "options": [
            "will have graduated",
            "will graduate",
            "would graduate",
            "had graduated",
        ],
        "answer": 0,
        "explanation": (
            "by + 时间点是完成时标志词：主句要表达「到那时已经完成」，用将来完成时 will have graduated。"
            "同时注意从句：时间/条件状语从句用一般现在时表将来（主将从现），所以是 By the time I see you，"
            "不是 By the time I will see you。整体平移到过去则从句用一般过去、主句用过去完成："
            "By the time I saw you, I had graduated."
        ),
        "traps": ["从句误用 will（By the time I will see you ❌）；把 will graduate（一般将来）当成「到那时已经完成」。"],
        "tags": ["语法", "时态"],
    },
    {
        "topic": "ENG-GRAM-01-01",
        "stem": "I lived here for ten years. 与 I have lived here for ten years. 的区别是？",
        "options": [
            "前者暗示现在已不住这里，后者暗示现在仍住着",
            "前者暗示现在仍住这里，后者暗示现已搬走",
            "两者完全同义，只是英式与美式的用词差异",
            "前者是错的，for + 时间段只能配现在完成时",
        ],
        "answer": 0,
        "explanation": (
            "动作在过去已封闭、与当下切断 → 一般过去时（I lived here for ten years. 现已不住）；"
            "过去开始、延续到当下 → 现在完成时（I have lived here for ten years. 大概率还住着）。"
            "for + 时间段本身没有方向，落点全由时态锚定。另注意 the last week（截止此刻的 7 天，配现在完成）"
            "与 last week（上一个自然周，配过去）的区别。"
        ),
        "traps": ["看到 for ten years 就一律用完成时；把 the last week 按 last week（上一个自然周）处理。"],
        "tags": ["语法", "时态"],
    },
    {
        "topic": "ENG-GRAM-01-01",
        "stem": "had been 有四种面孔，下列哪种搭配才构成被动语态？",
        "options": [
            "been + 及物动词过去分词（had been locked）",
            "been + 形容词或名词（had been ill）",
            "been + to + 地点（had been to Paris）",
            "been + doing（had been waiting）",
        ],
        "answer": 0,
        "explanation": (
            "had been 本身不等于被动，关键看 been 后面接什么："
            "been + 过去分词 → 被动（The door had been locked.）；"
            "been + 形容词/名词 → 系表结构，主动（He had been ill. / He had been a teacher.）；"
            "been + to + 地点 → 去过，主动（She had been to Paris.）；"
            "been + doing → 过去完成进行时，主动（They had been waiting.）。"
        ),
        "traps": ["见到 had been 就判为被动；把系表结构（had been + 形容词/名词）当被动。"],
        "tags": ["语法", "被动语态"],
    },
    # ---------------- ENG-GRAM-01-02 三大从句 ----------------
    {
        "topic": "ENG-GRAM-01-02",
        "stem": "the assumption that economic growth will reduce inequality 与 the assumption that he made，两个 that 从句类型不同，判据是？",
        "options": [
            "从句成分是否齐全：齐全（不缺主宾）→ 同位语从句；缺宾语 → 定语从句",
            "that 前的名词是否抽象：抽象名词 → 定语从句，具体名词 → 同位语从句",
            "that 能否省略：能省略 → 同位语从句；不能省略 → 定语从句",
            "从句谓语的时态：现在时 → 同位语从句；过去时 → 定语从句",
        ],
        "answer": 0,
        "explanation": (
            "两者都是「名词 + that + 句子」，唯一可靠判据是从句本身是否完整："
            "that 在定语从句中必须充当成分（the assumption that he made 缺宾语 → 定语从句）；"
            "在同位语从句中不充当成分、只起连接作用（economic growth will reduce inequality 主谓宾齐全 → 同位语从句）。"
            "同位语从句的 that 不可省略，且常跟在 fact / assumption / belief / notion / evidence / idea 等抽象名词后。"
        ),
        "traps": ["只看 that 就判类型；把作宾语的 that（定语从句）与纯连接作用的 that（同位语从句）混淆。"],
        "tags": ["语法", "从句"],
    },
    {
        "topic": "ENG-GRAM-01-02",
        "stem": "下列哪种情况只能用 whether、不能用 if？",
        "options": [
            "介词后作宾语从句时（It depends on whether he agrees.）",
            "口语中作动词的宾语从句时（I wonder if he is coming.）",
            "宾语从句表示「是否」且位于句中时（I wonder if…）",
            "从句谓语是现在进行时时（I wonder if he is coming.）",
        ],
        "answer": 0,
        "explanation": (
            "whether 更正式、更通用，以下四种情况只能用 whether："
            "① 介词后（It depends on whether he agrees.）；② 不定式前（whether to stay）；"
            "③ 与 or not 直接连用（whether or not he agrees）；④ 句首主语从句（Whether he will come is uncertain.）。"
            "if 口语化，不可用于介词后、不可与 or not 直接连用。"
        ),
        "traps": ["在介词后或句首主语从句里误用 if；把 whether or not 写成 if or not。"],
        "tags": ["语法", "从句"],
    },
    {
        "topic": "ENG-GRAM-01-02",
        "stem": "下列哪一组情况只能用 that 引导定语从句？",
        "options": [
            "先行词被最高级/序数词/only 修饰，或先行词是不定代词时",
            "非限制性定语从句（逗号后），或关系词前有介词时",
            "关系词指代前面整个主句时（which surprised everyone）",
            "先行词是专有名词且从句用逗号与主句隔开时",
        ],
        "answer": 0,
        "explanation": (
            "只用 that：先行词被最高级/序数词/only/very/same 修饰（the best film that I've ever seen）、"
            "先行词是不定代词（All that glitters is not gold.）、先行词既有人又有物（the men and books that interested him）。"
            "只用 which：逗号后的非限制性从句（The house, which was built in 1750, is now a museum.）、"
            "介词后（the house in which he lives）、指代整个主句（He passed the exam, which surprised everyone.）。"
        ),
        "traps": ["非限制性从句用 that；介词后误用 that；先行词是不定代词却用 which。"],
        "tags": ["语法", "从句"],
    },
    {
        "topic": "ENG-GRAM-01-02",
        "stem": "I don't think he is right. 的准确含义是？",
        "options": [
            "我认为他不对（否定从句，not 只是形式上留在主句）",
            "我不认为他对，但也不确定他错",
            "我不持有「他对」这个观点，对他对错不作判断",
            "我认为他是对的（两个否定相互抵消）",
        ],
        "answer": 0,
        "explanation": (
            "think / believe / suppose / expect / imagine / seem / appear 带宾语从句时，从句的否定习惯移到主句，"
            "这叫否定转移，语义仍在从句：I don't think he is right. = 我认为他不对。"
            "简略回应用 I don't think so.。注意 hope 不发生否定转移：只能说 I hope not.（❌ I don't hope so.）"
        ),
        "traps": ["把 I don't think he is right. 按字面理解成「我不这么认为」；把 hope 的简略回应写成 I don't hope so."],
        "tags": ["语法", "从句", "否定转移"],
    },
    # ---------------- ENG-GRAM-01-03 虚拟语气 ----------------
    {
        "topic": "ENG-GRAM-01-03",
        "stem": "虚拟语气中，与过去事实相反的条件句，从句和主句分别用什么形式？",
        "options": [
            "从句 had done；主句 would have done",
            "从句 did/were；主句 would do",
            "从句 were to do/should do；主句 would do",
            "从句 had done；主句 would do",
        ],
        "answer": 0,
        "explanation": (
            "与过去事实相反：从句用过去完成时（had done），主句用 would/should/could/might + have done。"
            "对照另两种：与现在事实相反 → 从句一般过去时（be 用 were）、主句 would do；"
            "与将来事实相反 → 从句过去时/were to do/should do、主句 would do。"
        ),
        "traps": ["把与现在事实相反（did/were → would do）和与过去事实相反（had done → would have done）的配对记反。"],
        "tags": ["语法", "虚拟语气"],
    },
    # ---------------- ENG-GRAM-01-04 非谓语动词 ----------------
    {
        "topic": "ENG-GRAM-01-04",
        "stem": "把 He made me do it. 变成被动语态，正确的是？",
        "options": [
            "I was made to do it.（被动须还原 to）",
            "I was made do it.（被动仍省略 to）",
            "I was made doing it.（被动改用 doing）",
            "I was made done it.（被动用过去分词）",
        ],
        "answer": 0,
        "explanation": (
            "使役动词（let / make / have）和感官动词（see / hear / watch / feel / notice）"
            "主动语态后接裸不定式（省略 to），被动语态必须把 to 还原："
            "He made me do it. → I was made to do it.；I saw him run. → He was seen to run."
        ),
        "traps": ["被动语态中仍省略 to（I was made do it ❌）；把主动的裸不定式结构照搬进被动。"],
        "tags": ["语法", "非谓语"],
    },
    {
        "topic": "ENG-GRAM-01-04",
        "stem": "I saw him leave. 与 I saw him leaving. 的区别是？",
        "options": [
            "前者是看见离开的全过程，后者是看见正在离开",
            "前者是看见正在离开，后者是看见离开的全过程",
            "两者完全同义，只是英式与美式的用词差异",
            "前者表示命令他离开，后者表示目送他离开",
        ],
        "answer": 0,
        "explanation": (
            "感官动词后接裸不定式表动作的**全过程**，接现在分词表动作**正在进行**："
            "I saw him leave.（看见他离开的全过程）／ I saw him leaving.（看见他正在离开）；"
            "I heard her sing.（听见她唱完）／ I heard her singing.（听见她正在唱）。"
        ),
        "traps": ["把「全过程」与「正在进行」混用，导致完成/进行的语义错位。"],
        "tags": ["语法", "非谓语"],
    },
    {
        "topic": "ENG-GRAM-01-04",
        "stem": "look forward to ___ from you. 与 be used to ___ early. 中 to 后的动词形式是？",
        "options": [
            "两处都用 doing：这两个 to 都是介词",
            "两处都用 do：两个 to 都是不定式标记",
            "前者用 do，后者用 doing",
            "前者用 doing，后者用 do",
        ],
        "answer": 0,
        "explanation": (
            "to 有两种身份：不定式标记（后接动词原形，want to do）与介词（后接名词/动名词）。"
            "介词 to 的常见搭配：look forward to doing、object to doing、be used to doing（习惯于）、"
            "devote oneself to doing、prefer doing A to doing B。"
            "区分技巧：介词 to 可换成 toward，不定式 to 不能。"
            "⚠️ be used to doing（习惯于）≠ be used to do（被用来做，to 是不定式标记）。"
        ),
        "traps": ["把 look forward to / be used to 里的介词 to 当成不定式标记，后面接动词原形。"],
        "tags": ["语法", "非谓语"],
    },
    # ---------------- ENG-GRAM-01-05 倒装句 ----------------
    {
        "topic": "ENG-GRAM-01-05",
        "stem": "把 I have never seen it. 改写成 Never 开头的倒装句，正确的是？",
        "options": [
            "Never have I seen it.",
            "Never I have seen it.",
            "Never have seen I it.",
            "Never do I have seen it.",
        ],
        "answer": 0,
        "explanation": (
            "否定/半否定词（never / hardly / scarcely / barely / seldom / rarely / little / nowhere / "
            "not only / not until / no sooner / by no means / in no case / on no account）置于句首表强调时，"
            "用部分倒装：把助动词提到主语前。Never have I seen it.／"
            "Not only did he come, but he also spoke.（还原：He not only came…）"
        ),
        "traps": ["只把否定词提前而不倒装（Never I have seen it. ❌）；多个助动词时倒装位置错（Never have I seen it. ✓）。"],
        "tags": ["语法", "倒装"],
    },
    # ---------------- ENG-GRAM-02-01 嵌套从句 ----------------
    {
        "topic": "ENG-GRAM-02-01",
        "stem": (
            "However, whether such a sense of fairness evolved independently in capuchins and humans, "
            "or whether it stems from the common ancestor that the species had 35 million years ago, "
            "is, as yet, an unanswered question. 该句共有几个分句？"
        ),
        "options": [
            "4 个：evolved、stems、had 各领一个从句，is 是主句谓语",
            "3 个：evolved、stems 各领一个从句，is 是主句谓语",
            "5 个：还要加上 as yet 构成的插入分句",
            "2 个：只有两个 whether 从句，其余都是修饰成分",
        ],
        "answer": 0,
        "explanation": (
            "方法：数谓语定分句——有几个谓语就有几个分句。"
            "evolved（主语从句①，主语 such a sense of fairness）、stems（主语从句②，主语 it）、"
            "had（定语从句，修饰 the common ancestor，主语 the species、宾语 35 million years）、"
            "is（主句系动词）。主干：whether… or whether… is an unanswered question。"
            "两个并列主语从句整体作主语，视为一个整体，谓语用单数 is。"
        ),
        "traps": ["把 to do / doing / done 等非谓语误当谓语，导致分句数虚高；把定语从句的谓语 had 误当主句谓语。"],
        "tags": ["语法", "长难句", "嵌套从句"],
    },
    # ---------------- ENG-GRAM-02-02 分隔结构 ----------------
    {
        "topic": "ENG-GRAM-02-02",
        "stem": (
            "The company, a major energy supplier in New England, provoked justified outrage in Vermont last week. "
            "该句的谓语是？"
        ),
        "options": [
            "provoked（主语是 The company，同位语被一对逗号括起）",
            "is（把 a major energy supplier 当成了主语）",
            "supplier（把同位语的中心词当成了谓语）",
            "provoked 与 is 并列作谓语",
        ],
        "answer": 0,
        "explanation": (
            "主语后紧跟「逗号 + 名词短语」时，先判断该名词短语是否与主语同指（同位语）。"
            "a major energy supplier in New England 与 The company 同指，可改写为「也就是……」，"
            "整体括起跳过后，谓语是 provoked。主语与谓语之间隔了 8 个词，这是分隔结构的典型形态。"
        ),
        "traps": ["把同位语里的中心词 supplier 误当主语，结果找不到谓语，或把后面的 provoked 当成另一个句子。"],
        "tags": ["语法", "长难句", "分隔结构"],
    },
    # ---------------- ENG-GRAM-02-03 省略与还原 ----------------
    {
        "topic": "ENG-GRAM-02-03",
        "stem": "…may, if left unchecked, be sowing the seeds of social fragmentation… 中 if left unchecked 还原后是？",
        "options": [
            "if they are left unchecked（省略了与主句相同的主语 + be）",
            "if it was left unchecked（主语指代前一句的内容）",
            "if leaving unchecked（left 在这里是现在分词）",
            "if the seeds are unchecked（主语是 the seeds）",
        ],
        "answer": 0,
        "explanation": (
            "条件/时间/比较状语从句中，若从句主语与主句主语一致且含 be，可省略「主语 + be」："
            "if left unchecked = if they are left unchecked（they 指前面的 the very innovations）。"
            "同理比较从句：a technology that evolves faster than the laws (evolve) —— than 后没有谓语时，"
            "要补出与前文对应的谓语。"
        ),
        "traps": ["把 if left unchecked 里的 left 误当主句谓语；把 faster than the laws 当完整从句，找不到比较对象。"],
        "tags": ["语法", "长难句", "省略与还原"],
    },
    # ---------------- ENG-GRAM-02-04 多重后置定语 ----------------
    {
        "topic": "ENG-GRAM-02-04",
        "stem": (
            "the practical imperatives driving legislative action are becoming increasingly difficult to ignore "
            "中，driving legislative action 的成分是？"
        ),
        "options": [
            "现在分词作后置定语修饰 imperatives；谓语是 are becoming",
            "谓语动词，与 are becoming 并列作谓语",
            "动名词，与 the practical imperatives 一起作主语",
            "宾语补足语，补充说明 action",
        ],
        "answer": 0,
        "explanation": (
            "名词后紧跟 doing / done / 形容词短语 / 介词短语 / 不定式时，先把这些后置定语整体括起，再向后找谓语。"
            "driving legislative action 修饰 imperatives（= which drive legislative action），"
            "真正的谓语是 are becoming，表语是 difficult to ignore。"
            "还原技巧：后置定语补回 which is / that is。"
        ),
        "traps": ["把 driving 误当主句谓语（真正谓语是 are becoming）；把 capable of 当谓语（capable 是形容词，无动词用法）。"],
        "tags": ["语法", "长难句", "后置定语"],
    },
    # ---------------- ENG-GRAM-02-05 同位语与独立主格 ----------------
    {
        "topic": "ENG-GRAM-02-05",
        "stem": (
            "…has intensified, with proponents arguing that its straightforward implementation outweighs "
            "the prohibitive hardware costs. 中 arguing 的逻辑主语是？"
        ),
        "options": [
            "proponents：with + 名词 + doing，这个名词不是主句主语",
            "The debate：主句的主语，也就是全句谈论的对象",
            "has intensified：主句谓语所对应的施动者",
            "its straightforward implementation：that 从句里的主语",
        ],
        "answer": 0,
        "explanation": (
            "with + 名词/代词 + doing / done / 形容词 / 介词短语 构成独立主格（with 复合结构），"
            "整体作状语；名词与该成分构成主动（doing）或被动（done）关系，"
            "且这个逻辑主语**不是**主句主语。此处 proponents 是 arguing 的逻辑主语。"
            "leave + 宾语 + doing / in + 名词 同理表「使宾语处于某状态」，不是「离开」。"
        ),
        "traps": ["把 with proponents arguing 里的 arguing 误当主句谓语；误认为 arguing 的逻辑主语是主句主语。"],
        "tags": ["语法", "长难句", "独立主格"],
    },
    # ---------------- ENG-GRAM-02-06 长难句拆分方法论 ----------------
    {
        "topic": "ENG-GRAM-02-06",
        "stem": (
            "he did not accept as well founded the charge made by some of his critics that… "
            "该句中 accept 的宾语是？"
        ),
        "options": [
            "the charge（宾补 as well founded 被提到了宾语之前）",
            "as well founded（介词短语作宾语）",
            "some of his critics（介词 by 的宾语）",
            "句末 that 从句整体作 accept 的宾语",
        ],
        "answer": 0,
        "explanation": (
            "这是宾语后置（heavy NP shift）：动词后先出现宾补、再出现长宾语，结构为"
            "「动词 + 宾补(as/for/to be + 形容词) + 长宾语」。"
            "accept as well founded the charge = accept the charge as well founded。"
            "触发词：accept / regard / consider / dismiss / treat / deem / declare。"
            "同类：Most economists dismiss as unfounded the fear, voiced by several critics, that…"
        ),
        "traps": ["把 as well founded 误当成修饰紧邻名词的短语；把 the charge 误当介词 by 的宾语。"],
        "tags": ["语法", "长难句", "拆分方法"],
    },
    {
        "topic": "ENG-GRAM-02-06",
        "stem": "He is no more diligent than his brother. 的含义是？",
        "options": [
            "他和他哥哥都不勤奋（两者都不）",
            "他不比他哥哥更勤奋（两者都勤奋，只是程度有别）",
            "他比他哥哥更不勤奋（哥哥勤奋，他不勤奋）",
            "他和他哥哥一样勤奋（两人程度相同）",
        ],
        "answer": 0,
        "explanation": (
            "no 表全盘否定，not 表程度比较："
            "not more A than B = 不比 B 更 A（两者都 A，程度有别）；"
            "no more A than B = 两者都不 A（= neither）。"
            "同理 not less A than B（不比 B 差，两者都好）／ no less A than B（两者都 A，≈ as much as）。"
        ),
        "traps": ["把 no more … than 按字面读成「不比……多」，与 not more … than 互换。"],
        "tags": ["语法", "长难句", "比较结构"],
    },
]


# ===========================================================================
# 二、数学计算陷阱卡（30 张）
#     ⚠️ 一律用 Unicode 数学符号（∫ √ ≤ ² ₓ ′ π σ ρ），不写 $...$ LaTeX
# ===========================================================================

MATH_CARDS = [
    # ---------------- 高数：极限与导数 ----------------
    {
        "topic": "MATH-GS-02-02",
        "stem": "下列求导公式正确的是？",
        "options": [
            "(aˣ)′ = aˣ·ln a（指数函数，变量在指数上）",
            "(aˣ)′ = x·aˣ⁻¹（按幂函数公式处理）",
            "(xᵃ)′ = xᵃ·ln x（按指数函数公式处理）",
            "(aˣ)′ = aˣ（漏掉了 ln a 因子）",
        ],
        "answer": 0,
        "explanation": (
            "求导前先看清变量在底数还是在指数上："
            "指数含变量 x → 指数函数公式 (aˣ)′ = aˣ·ln a；"
            "底数含变量 x → 幂函数公式 (xᵃ)′ = a·xᵃ⁻¹。"
        ),
        "traps": ["把 (aˣ)′ 按幂函数写成 x·aˣ⁻¹，或漏掉 ln a 因子。"],
        "tags": ["计算陷阱", "求导"],
    },
    {
        "topic": "MATH-GS-02-02",
        "stem": "对 f(x) = ln(1+x) − (ax + bx²) 求导，正确结果是？",
        "options": [
            "1/(1+x) − a − 2bx（负号分配到括号内每一项）",
            "1/(1+x) − a + 2bx（漏掉 bx² 项的负号）",
            "1/(1+x) + a + 2bx（整体符号写反）",
            "1/(1+x) − a − 2b（把 bx² 当成 bx 求导）",
        ],
        "answer": 0,
        "explanation": (
            "被减式整体求导时负号要分配到每一项：−(ax + bx²)′ = −(a + 2bx) = −a − 2bx，"
            "故 f′(x) = 1/(1+x) − a − 2bx。写的时候先给减式加括号再求导，不要心算合并。"
        ),
        "traps": ["含多项式的减法求导，负号只作用于第一项，漏掉 2bx 的负号。"],
        "tags": ["计算陷阱", "求导"],
    },
    {
        "topic": "MATH-GS-01-04",
        "stem": "已知 lim(x→0) [ln(1−2x) + 2x·f(x)]/x² = 0 且 f(0) = 1，则 f′(0) = ？",
        "options": [
            "1（泰勒展开到二阶，一阶主部相消后由二阶项定出）",
            "0（用 ln(1−2x) ~ −2x 替换，极限恒为 0，与 f′ 无关）",
            "−1（把二阶项 −2x² 的系数当成 f′(0)）",
            "2（把 2x·f(x) 中的系数 2 当成 f′(0)）",
        ],
        "answer": 0,
        "explanation": (
            "ln(1−2x) 的一阶项 −2x 与 2x·f(x) 的一阶项 +2x 同阶、系数相反，"
            "直接用等价无穷小替换会让一阶主部完全抵消，丢失关键信息。"
            "须泰勒展开到二阶：ln(1−2x) = −2x − 2x² + o(x²)，代入得"
            "[2x(f(x)−1) − 2x² + o(x²)]/x² → 0，故 (f(x)−1)/x → 1，即 f′(0) = 1。"
        ),
        "traps": ["分子是两个同阶无穷小相加减（系数相反）时仍用等价替换，主部抵消后得出错误结论。"],
        "tags": ["计算陷阱", "等价无穷小", "泰勒"],
    },
    {
        "topic": "MATH-GS-02-05",
        "stem": "由 ln(1+x) = x − x²/2 + x³/3 − …，求 f⁽³⁾(0)（f(x) = ln(1+x)），正确结果是？",
        "options": [
            "2（= 3! 乘展开式中 x³ 的系数 1/3）",
            "1/3（直接把 x³ 项的系数当成导数值）",
            "6（= 3! 乘 1，把系数误当成 1）",
            "0（误以为 ln(1+x) 只含奇次项）",
        ],
        "answer": 0,
        "explanation": (
            "麦克劳林展开中 xⁿ 的系数是 aₙ = f⁽ⁿ⁾(0)/n!，所以 f⁽ⁿ⁾(0) = n!·aₙ。"
            "f⁽³⁾(0) = 3! × (1/3) = 2。展开式里的系数不是导数值，中间差一个 n!。"
        ),
        "traps": ["忘记乘 n!，直接把展开式中 xⁿ 的系数当作 f⁽ⁿ⁾(0)。"],
        "tags": ["计算陷阱", "高阶导数", "泰勒"],
    },
    {
        "topic": "MATH-GS-02-03",
        "stem": "f(x) = arctan x，则 f⁽⁴⁾(0) = ？",
        "options": [
            "0（arctan x 展开只含奇次幂，x⁴ 项系数为 0）",
            "1/5（错把 x⁵ 项的系数当成 x⁴ 项的系数）",
            "−1/3（错取了 x³ 项的系数）",
            "24（漏判奇偶性，按 4! 直接计算）",
        ],
        "answer": 0,
        "explanation": (
            "arctan x = x − x³/3 + x⁵/5 − x⁷/7 + … 只含奇次幂 ⇒ 偶数阶导数全为 0，故 f⁽⁴⁾(0) = 0。"
            "规律：sin x、arctan x 只含奇次幂（偶数阶导为 0）；cos x 只含偶次幂（奇数阶导为 0）。"
        ),
        "traps": ["不先判奇偶性，硬去找 x⁴ 项系数，找不到就以为算错了，白白浪费时间。"],
        "tags": ["计算陷阱", "高阶导数"],
    },
    {
        "topic": "MATH-GS-02-06",
        "stem": "已知 g(x) 在 x = 0 处二阶可导，g(0) = g′(0) = 0。求 lim(x→0) g(x)/x² 时，正确的做法是？",
        "options": [
            "用泰勒展开 g(x) = g″(0)x²/2 + o(x²)，得极限 g″(0)/2",
            "连续两次洛必达，把 lim(x→0) g″(x)/2 直接写成 g″(0)/2",
            "由 g(0) = g′(0) = 0 断定 g(x) ≡ 0，故极限为 0",
            "洛必达一次得 lim(x→0) g′(x)/(2x)，再由 g′(0) = 0 得极限为 0",
        ],
        "answer": 0,
        "explanation": (
            "题目只给了「在 x = 0 处二阶可导」，并没有说 g″(x) 在 0 的去心邻域存在或连续，"
            "而洛必达要求分子分母在去心邻域可导到所需阶数。所以只能用泰勒："
            "g(x) = g(0) + g′(0)x + g″(0)x²/2 + o(x²) = g″(0)x²/2 + o(x²)，"
            "故 g(x)/x² → g″(0)/2。口诀「一点可导用泰勒，邻域可导才洛必达」。"
            "反例：f(x) = x²sin(1/x)，f′(0) = 0 存在，但 x ≠ 0 时 f′(x) = 2x·sin(1/x) − cos(1/x)，lim(x→0) f′(x) 不存在。"
        ),
        "traps": ["连续两次洛必达，偷设了 g″(x) 在 0 的邻域存在且连续——一点可导 ≠ 邻域可导。"],
        "tags": ["计算陷阱", "洛必达", "泰勒"],
    },
    {
        "topic": "MATH-GS-02-08",
        "stem": "f(x) = |x|x，在 x = 0 处？",
        "options": [
            "是拐点（f″(0) 不存在，但 f″ 在两侧变号）",
            "不是拐点（f″(0) 不存在，拐点必须在 f″ = 0 处取得）",
            "是极值点（f′ 在该点变号，故 f 有极值）",
            "既是极值点又是拐点（f′ 与 f″ 同时变号）",
        ],
        "answer": 0,
        "explanation": (
            "拐点判据是「f 在 x₀ 连续，且 f″ 在 x₀ 两侧变号」，对 f″(x₀) **是否存在**没有任何要求。"
            "f(x) = |x|x 在 x = 0 处 f″ 不存在，但两侧 f″ 变号，所以是拐点。"
            "「f″(x₀) = 0」只是 f″(x₀) 存在时的必要条件。"
            "可疑点必须列两类：① f″ 的零点；② f″ 不存在的点。"
        ),
        "traps": ["只扫 f″ 的零点，漏扫 f″ 不存在的点。与极值点同构记忆：极值可疑点 = 驻点 + 不可导点。"],
        "tags": ["计算陷阱", "拐点"],
    },
    # ---------------- 高数：积分 ----------------
    {
        "topic": "MATH-GS-03-04",
        "stem": "分解 P(x)/[(x−a)(x²+px+q)]（其中 x²+px+q 不可约，判别式 Δ < 0）时，正确的设法是？",
        "options": [
            "A/(x−a) + (Bx+C)/(x²+px+q)（二次因式上设一次式）",
            "A/(x−a) + B/(x²+px+q)（二次因式上设常数）",
            "A/(x−a) + (Bx+C)/(x²+px+q)²（二次因式取平方）",
            "(Ax+B)/(x−a) + C/(x²+px+q)（一次因式上设一次式）",
        ],
        "answer": 0,
        "explanation": (
            "口诀「一次因式上面设常数，二次因式上面设一次」：分母几次，分子最高就设（次数 − 1）次。"
            "若在不可约二次因式上只设常数 B，通分后分子为 A(x²+px+q) + B(x−a)，"
            "只有 A、B 两个自由度却要匹配三个系数，一般无解。"
        ),
        "traps": ["在不可约二次因式（Δ < 0）上只设常数，导致待定系数解不出。"],
        "tags": ["计算陷阱", "有理函数积分", "部分分式"],
    },
    {
        "topic": "MATH-GS-03-01",
        "stem": "下列积分公式正确的是？",
        "options": [
            "∫csc u du = ln|csc u − cot u| + C；∫sec u du = ln|sec u + tan u| + C",
            "∫csc u du = ln|sec u + tan u| + C；∫sec u du = ln|csc u − cot u| + C",
            "∫csc u du = ln|sin u| + C；∫sec u du = ln|cos u| + C",
            "∫csc u du = −cot u + C；∫sec u du = tan u + C",
        ],
        "answer": 0,
        "explanation": (
            "余割 csc u = 1/sin u（正弦的倒数），∫csc u du = ln|csc u − cot u| + C；"
            "正割 sec u = 1/cos u（余弦的倒数），∫sec u du = ln|sec u + tan u| + C。"
            "口诀「正弦配余割（公式带减号），余弦配正割（公式带加号）」；同族配对：csc 配 cot、sec 配 tan。"
            "兜底验证：(ln|csc u − cot u|)′ = csc u。"
        ),
        "traps": ["把 1/sin x 当 sec x、1/cos x 当 csc x，两题答案整体互换；倍角题（如 ∫dx/sin 2x）勿漏 1/2 系数。"],
        "tags": ["计算陷阱", "不定积分"],
    },
    {
        "topic": "MATH-GS-03-02",
        "stem": "凑微分时，(1/x)dx 应凑成？",
        "options": [
            "d(ln|x|)（1/x 的原函数是 ln|x|）",
            "d(x²)（按幂函数积分公式处理）",
            "d(x²/2)（n = −1 时套用 xⁿ⁺¹/(n+1)）",
            "d(−1/x²)（按 1/x² 的导数凑）",
        ],
        "answer": 0,
        "explanation": (
            "(1/x)dx → d(ln|x|)；(2/x)dx → d(2ln|x|)。"
            "错误根源是把幂函数公式 ∫xⁿdx = xⁿ⁺¹/(n+1) 套用到 n = −1 的情形，"
            "而 n = −1 是唯一例外。凑完可逆向验证：d(ln|x|) = (1/x)dx ✓，d(x²) = 2x dx ✗。"
        ),
        "traps": ["见到 1/x 就凑成 x² 或 x dx；口诀「1/x 凑出 ln，绝不凑出幂函数」。"],
        "tags": ["计算陷阱", "凑微分"],
    },
    {
        "topic": "MATH-GS-03-06",
        "stem": "∫(−a 到 a) 1/(1+e^{kx}) dx = ？",
        "options": [
            "a（用区间再现换元 x = −t，两式相加得 2I = 2a）",
            "2a（把被积函数当成偶函数直接加倍）",
            "0（被积函数在对称区间上正负抵消）",
            "a/2（换元后漏掉系数 2）",
        ],
        "answer": 0,
        "explanation": (
            "看到对称区间 [−a, a] 上的 1/(1+e^{kx})，第一反应是区间再现换元 x = −t："
            "I = ∫(−a 到 a) 1/(1+e^{−kx}) dx，与原式相加得 2I = ∫(−a 到 a) 1 dx = 2a，故 I = a。"
            "口诀「对称区间先想换元，定积分上限减下限，恒正函数不积负」。"
        ),
        "traps": ["硬拆分段或分部积分绕远路；把 1/(1+e^{kx}) 当奇函数或偶函数处理（它既非奇也非偶）。"],
        "tags": ["计算陷阱", "定积分", "区间再现"],
    },
    {
        "topic": "MATH-GS-03-07",
        "stem": "关于 ∫(−∞ 到 +∞) x³ dx，下列说法正确的是？",
        "options": [
            "反常积分发散：单侧积分发散，不能由奇偶性得 0",
            "等于 0：x³ 是奇函数，对称区间上正负相消",
            "等于 0：即柯西主值，故反常积分收敛",
            "等于 +∞：x³ 在正半轴无界",
        ],
        "answer": 0,
        "explanation": (
            "先判单侧收敛性：∫(0 到 +∞) x³dx 与 ∫(−∞ 到 0) x³dx 都发散，"
            "故反常积分 ∫(−∞ 到 +∞) x³dx 发散。"
            "奇函数在对称区间上的积分为 0，那只是**柯西主值**（强制 b = −a 同步趋向），"
            "不能写成反常积分等于 0。反常积分要求两端**独立**收敛。"
        ),
        "traps": ["口诀「奇函数无穷积分得 0，先判收敛再用奇偶」；把柯西主值当成反常积分的值。"],
        "tags": ["计算陷阱", "反常积分"],
    },
    {
        "topic": "MATH-GS-03-08",
        "stem": "用极坐标求三叶玫瑰线 r = a·sin 3θ 所围面积时，积分限应取？",
        "options": [
            "0 到 π（n = 3 为奇数，转半圈即画完全部 3 片叶）",
            "0 到 2π（玫瑰线都要转整圈）",
            "0 到 π/3（一个周期对应一片叶）",
            "0 到 3π/2（叶数为 3，取 3 个半圈）",
        ],
        "answer": 0,
        "explanation": (
            "口诀「奇半偶全」：n 为奇数时取 0 到 π（叶数为 n）；n 为偶数时取 0 到 2π（叶数为 2n）。"
            "n 为奇数时，θ 从 π 到 2π 的轨迹与 0 到 π 完全重合，取 0 到 2π 会重复计算一遍，面积翻倍。"
            "另注意区分曲线类型：带平方的是双纽线（r² = a²cos 2θ），不带平方的才是玫瑰线（r = a·sin nθ）。"
        ),
        "traps": ["三叶玫瑰线（n = 3）误取 0 到 2π，结果翻倍；把双纽线与玫瑰线的方程混淆。"],
        "tags": ["计算陷阱", "定积分应用", "极坐标"],
    },
    # ---------------- 高数：级数、二重积分、最值 ----------------
    {
        "topic": "MATH-GS-06-01",
        "stem": "判别 Σ ln(1 + (−1)ⁿ/√n) 的敛散性，正确的是？",
        "options": [
            "发散：泰勒展开到二阶，−1/(2n) 这一同号项发散并主导部分和",
            "收敛：由 ln(1+xₙ) ~ xₙ，与原级数 Σ(−1)ⁿ/√n 同敛散",
            "收敛：通项趋于 0，所以级数收敛（必要条件当充分用）",
            "发散：通项不趋于 0，故直接用通项判发散",
        ],
        "answer": 0,
        "explanation": (
            "变号级数禁止直接用等价无穷小判同敛散，必须泰勒展开到二阶："
            "ln(1 + (−1)ⁿ/√n) = (−1)ⁿ/√n − 1/(2n) + O(n^{−3/2})。"
            "第一项由莱布尼茨判别法收敛，第二项 −(1/2)Σ1/n 发散到 −∞，主导了部分和走向 ⇒ 原级数发散。"
            "口诀「正项才能用等价，变号必须多展一阶」。"
        ),
        "traps": ["对变号级数用等价无穷小替换判同敛散；只看通项趋于 0 就判收敛（那只是必要条件）。"],
        "tags": ["计算陷阱", "级数", "敛散判别"],
    },
    {
        "topic": "MATH-GS-05-01",
        "stem": "计算 ∬_D x·cos√(x²+y²)/(x+y) dσ（D 关于 y = x 对称）时，正确思路是？",
        "options": [
            "用轮换对称 I = ½∬_D [f(x,y) + f(y,x)]dσ，相加后分母 x+y 约掉",
            "被积函数不对称，轮换对称用不了，直接用极坐标硬算",
            "先把 D 换成不对称区域，再套用轮换对称",
            "由轮换对称得 I = ∬_D f(y,x)dσ = 0",
        ],
        "answer": 0,
        "explanation": (
            "轮换对称性约束的是**区域**，不是被积函数。只要 D 关于 y = x 对称，就有"
            "∬_D f(x,y)dσ = ∬_D f(y,x)dσ，于是 I = ½∬_D [f(x,y) + f(y,x)]dσ，"
            "相加后分子 x + y 与分母约掉，积分立刻简化。f(y,x) ≠ f(x,y) 完全正常。"
            "口诀「轮换看区域，不看函数；不对称也能用，靠相加」。"
        ),
        "traps": ["见被积函数不对称就放弃轮换对称；反向误用：区域不关于 y = x 对称时仍写轮换等式。"],
        "tags": ["计算陷阱", "二重积分", "轮换对称"],
    },
    {
        "topic": "MATH-GS-05-01",
        "stem": "求 ∬_D (1 − 2x² − y²)dσ（D: 2x² + y² ≤ 1），下列做法正确的是？",
        "options": [
            "对原被积函数积分（广义极坐标），得 π/(2√2)，小于区域面积",
            "直接写成 ∬_D dσ，取椭圆面积 π/√2 作为结果",
            "取 2π/√2，因为被积函数在 D 上的最大值是 1",
            "取 0，因为被积函数关于原点对称、正负抵消",
        ],
        "answer": 0,
        "explanation": (
            "「二重积分 = 面积」仅在被积函数恒为 1 时成立（S_D = ∬_D 1 dσ）。"
            "本题被积函数 1 − 2x² − y² 在 D 内从 1 变化到 0，不恒为 1。"
            "令 u = √2 x, v = y 化为单位圆，得 ∬_D (1−2x²−y²)dσ = (1/√2)(π − π/2) = π/(2√2)。"
            "自检：被积函数 ∈ [0,1] ⇒ 积分值必小于区域面积 π/√2。"
        ),
        "traps": ["把被积函数当成 1，直接替换为椭圆面积（错答是正解的 2 倍）。"],
        "tags": ["计算陷阱", "二重积分"],
    },
    {
        "topic": "MATH-GS-01-02",
        "stem": "求 lim(n→∞) Σ(i=1 到 n) sin(iπ/n)/(n + 1/i) 时，下列做法正确的是？",
        "options": [
            "夹逼放缩：分母分别换成 n+1 与 n，两边都是定积分定义且极限相同",
            "直接舍弃 1/i（相对 n 很小），化为 Σ sin(iπ/n)/n",
            "把 1/i 用等价无穷小换成 1/n 后再求和",
            "先取 n → ∞ 再对 i 求和，得 Σ 0 = 0",
        ],
        "answer": 0,
        "explanation": (
            "无穷多项求和不能凭「每项都是无穷小」就舍弃扰动项："
            "反例 (1/n + 1/n + … + 1/n)（共 n 项）= n·(1/n) = 1。"
            "正确做法是夹逼：Σ sin(iπ/n)/(n+1) ≤ 原式 ≤ Σ sin(iπ/n)/n，"
            "两边都化为定积分定义 ∫(0 到 1) sin(πx)dx = 2/π。口诀「有限项看阶数，无穷项看累积」。"
        ),
        "traps": ["看到分母是 n + f(i) 的微小扰动就当它不存在；先取极限再求和（次序颠倒）。"],
        "tags": ["计算陷阱", "数列极限", "夹逼"],
    },
    {
        "topic": "MATH-GS-04-06",
        "stem": "已知 3x² + 2y² = 6，求 2x + y 的最大值，正确的做法是？",
        "options": [
            "用柯西不等式配系数造上界，得 √11，等号条件 3x = 4y",
            "用均值不等式得 2x + y ≥ 2√(2xy)，取等号点代入即为最大值",
            "用均值不等式得 2x + y ≤ (2x+y 的算术平均)，取等号即为最大值",
            "令 y = 0 代入约束得 x = √2，最大值就是 2√2",
        ],
        "answer": 0,
        "explanation": (
            "线性目标 + 二次型约束 → 用柯西不等式配系数造上界："
            "2x + y = (2/√3)·√3x + (1/√2)·√2y ≤ √[(4/3 + 1/2)(3x² + 2y²)] = √11，"
            "等号条件 3x = 4y，此时取到最大值 √11 ≈ 3.32。"
            "自检三要素：方向（求最大用 ≤）、右端为常数、等号可达。"
        ),
        "traps": ["用均值不等式求最大值：均值不等式给的是下界，等号点并非最大值点（该点只有 4√66/11 ≈ 2.95）。"],
        "tags": ["计算陷阱", "最值", "柯西不等式"],
    },
    # ---------------- 线代 ----------------
    {
        "topic": "MATH-XD-06-04",
        "stem": "已知二次型矩阵 A，判断正惯性指数 p、负惯性指数 q 的正确方法是？",
        "options": [
            "由特征值的正负个数定 p、q，且 p + q = r(A)",
            "数对角线上正元素、负元素的个数，即得 p、q",
            "数行列式展开式中正项、负项的个数，即得 p、q",
            "由 A 的秩直接得 p = q = r(A)/2",
        ],
        "answer": 0,
        "explanation": (
            "惯性指数由**特征值的正负个数**决定，不是由对角元的符号决定"
            "（对角元符号只在矩阵已经是对角矩阵时才等价）。"
            "惯性定理：任何可逆线性变换（含配方法）都保持 p、q 不变。"
            "先判秩 r：p + q = r，零特征值个数 = n − r。"
            "若两行相同或成比例，则 det A = 0、r < n，更不能按对角元符号想当然。"
        ),
        "traps": ["数对角线上正负元素的个数当惯性指数；以为 3 阶矩阵一定有 3 个非零特征值。"],
        "tags": ["计算陷阱", "二次型", "惯性指数"],
    },
    {
        "topic": "MATH-XD-06-04",
        "stem": "用「实对称阵正定 ⇔ 特征值全 > 0」求参数 ε 的范围，遇到 1 + ε·λ₁ > 0 且 λ₁ < 0，正确解是？",
        "options": [
            "ε < −1/λ₁（两边同除负数，不等号必须变向）",
            "ε > −1/λ₁（直接移项相除，不等号不变）",
            "ε > 1/λ₁（把 λ₁ 移过去时忘记取负）",
            "无解（λ₁ < 0 时 1 + ε·λ₁ 恒为负）",
        ],
        "answer": 0,
        "explanation": (
            "ε·λ₁ > −1 两边同除 λ₁ < 0 必须**变号**，得 ε < −1/λ₁。"
            "取 λ₁ = −2 应得 ε < 1/2；若误写成 ε > −1/λ₁ = 1/2，恰好取到正确范围的补集"
            "（ε 越大，最小特征值 1 + ελ₁ 越负）。λ₁ ≥ 0 时对任意 ε > 0 都正定。"
        ),
        "traps": ["不等式两边除以含参或负的量时忘记变号，取到补集区间。"],
        "tags": ["计算陷阱", "正定二次型"],
    },
    {
        "topic": "MATH-XD-01-01",
        "stem": "用初等行变换化上三角求行列式时，下列对应关系正确的是？",
        "options": [
            "倍加不变号；交换两行变号；某行乘 k 则行列式乘 k",
            "倍加要变号；交换两行不变；某行乘 k 行列式不变",
            "三种行变换都不改变行列式的值",
            "倍加与交换都不变号，只有某行乘 k 才提因子",
        ],
        "answer": 0,
        "explanation": (
            "三种行变换对行列式的影响各不相同：倍加不变、交换变号、倍乘提因子。"
            "口诀「倍加随便用，倍乘要记账，交换要变号」——某行乘 k 时必须在式外同步记 D → kD。"
            "只判 |A| 是否为 0（例如判断可逆）时三种变换可随意用，因为只关心零与非零。"
        ),
        "traps": ["为消元方便把某行乘 k 后忘记在行列式外补乘 k；把「倍加不变」错误推广到倍乘。"],
        "tags": ["计算陷阱", "行列式"],
    },
    {
        "topic": "MATH-XD-01-02",
        "stem": "4 阶行列式中，副对角线项 a₁₄a₂₃a₃₂a₄₁ 的符号是？",
        "options": [
            "正号（列标 4,3,2,1 的逆序数为 6，是偶数）",
            "负号（副对角线永远是负号）",
            "正号（副对角线永远是正号）",
            "无法确定，要看其余元素的正负",
        ],
        "answer": 0,
        "explanation": (
            "副对角线项的符号是 (−1)^{n(n−1)/2}，不要死记「副对角线是负的」。"
            "n = 2：(−1)¹ = −1（负）；n = 3：(−1)³ = −1（负）；n = 4：(−1)⁶ = +1（正）；n = 5：(−1)¹⁰ = +1（正）。"
            "只有 2 阶和 3 阶的副对角线是负号，4 阶起就是正号。"
        ),
        "traps": ["误以为副对角线永远是负号；4 阶及以上展开时可现场算逆序数 t(n, n−1, …, 1) = n(n−1)/2。"],
        "tags": ["计算陷阱", "行列式", "逆序数"],
    },
    {
        "topic": "MATH-XD-02-03",
        "stem": "关于「秩 = 非零特征值个数」这一说法，正确的是？",
        "options": [
            "仅当矩阵可对角化时才成立（如实对称矩阵）",
            "对任意矩阵都成立，是秩的基本性质",
            "仅当矩阵满秩时才成立",
            "仅当矩阵为对角矩阵时才成立",
        ],
        "answer": 0,
        "explanation": (
            "「秩 = 非零特征值个数」只在矩阵**可对角化**时成立。"
            "反例：A = [0 1; 0 0] 的特征值全为 0，按该说法秩为 0，而实际 r(A) = 1。"
            "另注意：单纯求秩时行列变换可混用，但求极大无关组并表示其余向量时**只能做行变换**"
            "（做列变换会让表示系数全错）。"
        ),
        "traps": ["对一般矩阵滥用「特征值个数判秩」；求极大无关组并表示其余向量时做了列变换。"],
        "tags": ["计算陷阱", "矩阵的秩"],
    },
    {
        "topic": "MATH-XD-05-04",
        "stem": "用正交变换化实对称矩阵 A 为标准形时，求出重特征值的基础解系后还应做什么？",
        "options": [
            "对同一重根内的向量作施密特正交化，再逐列单位化",
            "直接拿基础解系拼成 Q 即可，实对称矩阵的特征向量自动正交",
            "只做单位化，不必正交化；不同特征值的向量会自动正交",
            "把重根的特征向量全部换成 1/√n 的等分向量",
        ],
        "answer": 0,
        "explanation": (
            "流程固定为「基础解系 → 施密特正交化 → 单位化 → 拼 Q → 验 QᵀQ = E」。"
            "正交矩阵要求列向量**单位且正交**，缺一不可：不同特征值的特征子空间自动正交，"
            "但同一重根的特征子空间内取出的基础解系一般**不正交**，必须手动施密特正交化。"
        ),
        "traps": ["施密特正交化后忘记单位化；重根的特征向量未正交化就拼 Q，导致 QᵀQ ≠ E。"],
        "tags": ["计算陷阱", "实对称矩阵", "正交对角化"],
    },
    {
        "topic": "MATH-XD-05-02",
        "stem": "判断两个实对称矩阵 A、B 是否合同、是否相似，正确的判据是？",
        "options": [
            "合同看 (r, p, q)；相似看特征值（含重数）",
            "合同看特征值；相似看 (r, p, q)",
            "两者判据相同：实对称矩阵合同必然相似",
            "合同看行列式的符号；相似看矩阵的秩",
        ],
        "answer": 0,
        "explanation": (
            "合同的不变量是 (r, p, q)（只看正负惯性指数的**符号个数**）；"
            "相似的不变量是特征值（看**数值**，含重数）。"
            "实对称时相似 ⇒ 合同，但合同 ⇏ 相似：反例 E 与 diag(4,1) 的 (r,p,q) 都是 (2,2,0) 故合同，"
            "而特征值 1,1 与 4,1 不同故不相似。口诀「合同看符号，相似看数值」。"
        ),
        "traps": ["判合同时要求特征值相同；认为实对称矩阵合同则必相似。"],
        "tags": ["计算陷阱", "合同", "相似"],
    },
    {
        "topic": "MATH-XD-04-03",
        "stem": "用克拉默法则解线性方程组，若求得 |A| = 0，则？",
        "options": [
            "不能判定无解：须比较 r(A) 与 r(A|b)，不等则无解，相等则无穷多解",
            "方程组无解，因为 |A| = 0 说明系数矩阵不可逆",
            "方程组必有无穷多解，因为 |A| = 0 说明方程线性相关",
            "方程组有唯一解，但需要换其他方法求出",
        ],
        "answer": 0,
        "explanation": (
            "口诀「行列式零看秩比」：|A| = 0 时克拉默法则失效，必须比较 r(A) 与 r(A|b)："
            "r(A) < r(A|b) → 无解；r(A) = r(A|b) < n → 无穷多解。"
            "另注意构造 D_j 时永远把**第 j 列**（不是行）换成 b，因为 x_j 的系数在第 j 列。"
        ),
        "traps": ["求出 |A| = 0 就直接写「方程组无解」；构造 D_j 时把行换成了 b。"],
        "tags": ["计算陷阱", "线性方程组", "克拉默法则"],
    },
    # ---------------- 概率论 ----------------
    {
        "topic": "MATH-GL-01-05",
        "stem": "已知三事件 A、B、C 两两独立，则下列结论正确的是？",
        "options": [
            "只能两两连乘；P(ABC) = P(A)P(B)P(C) 未必成立",
            "P(ABC) = P(A)P(B)P(C) 一定成立，可直接连乘",
            "A、B、C 三个事件一定相互独立，可放心连乘",
            "P(A∪B∪C) 可用 1 − P(Ā)P(B̄)P(C̄) 直接计算",
        ],
        "answer": 0,
        "explanation": (
            "两两独立 ≠ 相互独立（事件数 ≥ 3 时两者不等价）。"
            "反例：抛两枚硬币，A = 第一枚正面、B = 第二枚正面、C = 两枚相同，"
            "三者两两独立，但 P(ABC) = 1/4 ≠ 1/8 = P(A)P(B)P(C)。"
            "动笔连乘前先圈出题面写的是「两两独立」还是「相互独立」。"
        ),
        "traps": ["题面只给「两两独立」就用三事件连乘，或用「至少一个发生」的对立公式 1 − P(Ā)P(B̄)P(C̄)。"],
        "tags": ["计算陷阱", "独立性"],
    },
    {
        "topic": "MATH-GL-01-03",
        "stem": "一批 N 件产品中有 m 件一等品，不放回地先后取两件。若第一次已取到一等品，第二次取到一等品的概率是？",
        "options": [
            "(m−1)/(N−1)：分子分母同时减 1",
            "m/N：仍用箱内的初始占比",
            "(m−1)/N：只把分子减 1",
            "m/(N−1)：只把分母减 1",
        ],
        "answer": 0,
        "explanation": (
            "第二次抽取的完整条件是 B | C_i A，含两层信息：「不放回」与「第一次已取到一等品」，"
            "故箱内一等品剩 m−1 件、总数剩 N−1 件，P = (m−1)/(N−1)。"
            "分层（选箱）题的权重还要换成后验 P(C_i | A)。口诀「见不放回，分子分母同减 1」。"
        ),
        "traps": ["第二次仍用初始占比 m/N；只把分子或分母减 1（漏掉「不放回」的双重影响）。"],
        "tags": ["计算陷阱", "条件概率"],
    },
    {
        "topic": "MATH-GL-01-04",
        "stem": "一批产品中 10% 是次品；检验把正品判为正品的概率为 98%，把次品判为正品的概率为 5%（漏检率）。任取一件能通过验收的概率是？",
        "options": [
            "0.887（0.9×0.98 + 0.1×0.05，次品支取漏检率本身）",
            "0.977（次品支误取漏检率的补集 95%）",
            "0.882（漏掉次品那一支，只算 0.9×0.98）",
            "0.005（只算抽到次品且被漏检的那一支）",
        ],
        "answer": 0,
        "explanation": (
            "抽到次品时能通过验收 ⇔ 判定结果为「正品」，恰是**漏检事件本身**（5%），其补 95% 是「被正确拒收」。"
            "故 P(通过) = 0.9×0.98 + 0.1×0.05 = 0.882 + 0.005 = 0.887。"
            "条件概率直接写成带条件的数字 P(·|·)，不设无条件事件符号。"
        ),
        "traps": ["补集方向取反：把次品支写成 0.1×0.95，得 0.977；或漏掉次品那一支得 0.882。"],
        "tags": ["计算陷阱", "全概率公式"],
    },
    {
        "topic": "MATH-GL-04-03",
        "stem": "X ~ N(0, σ²)，计算 Cov(X, X³) 时下列做法正确的是？",
        "options": [
            "Cov(X, X³) = E(X⁴) = (EX²)² + D(X²) = 3σ⁴",
            "Cov(X, X³) = E(X⁴) = (EX²)² + 1 = σ⁴ + 1",
            "Cov(X, X³) = ρ(X, X³)·√(DX·DX³) = 1·√(σ²·15σ⁶)",
            "Cov(X, X³) = 0，因为 X 与 X³ 关于原点对称",
        ],
        "answer": 0,
        "explanation": (
            "与「自己」的**协方差**等于方差：cov(U, U) = DU；等于 1 的是**相关系数** ρ(U, U)。"
            "D(X²) = E(X⁴) − (EX²)² = 3σ⁴ − σ⁴ = 2σ⁴（量纲是 X⁴，与无量纲的 1 不能相加），"
            "故 Cov(X, X³) = E(X·X³) − EX·EX³ = 3σ⁴ − 0 = 3σ⁴，ρ = 3σ⁴/√(σ²·15σ⁶) = 3/√15 ≈ 0.775。"
            "自检：|ρ| ≤ 1，算出 ρ > 1 必错。"
        ),
        "traps": ["把「变量与自己的相关系数为 1」迁移成 cov(U,U) = 1，算出 ρ > 1（如 σ⁴ + 1）。"],
        "tags": ["计算陷阱", "协方差", "相关系数"],
    },
]


# ===========================================================================
# 入库
# ===========================================================================

def check_topic(conn, topic_id):
    row = conn.execute("SELECT 1 FROM topics WHERE id = ?", (topic_id,)).fetchone()
    return bool(row)


def insert_card(conn, topic_id, seq, card, dry):
    qid = f"Q-{topic_id}-{seq:04d}"
    exists = conn.execute("SELECT 1 FROM questions WHERE id = ?", (qid,)).fetchone()
    if exists:
        print(f"  [已存在·跳过] {qid}")
        return 0
    problems = check_options_quality(card["options"])
    if problems:
        print(f"  ❌ [质量拦截] {qid} {card['stem'][:30]} → {'；'.join(problems)}")
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
            "VALUES (?, ?, 'choice', 0.5, ?, ?)",
            (qid, topic_id, SOURCE, json.dumps(content, ensure_ascii=False)),
        )
        conn.execute(
            "INSERT INTO cards (id, question_id, state, due_date) VALUES (?, ?, 0, ?)",
            (qid, qid, TODAY),
        )
    print(f"  [新增] {qid} {card['stem'][:34]}")
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    dry = args.dry_run

    conn = sqlite3.connect(DB_PATH)
    print(f"数据库：{DB_PATH}{'（dry-run）' if dry else ''}\n")

    total = 0
    for name, cards in (("英语语法卡", GRAMMAR_CARDS), ("数学计算陷阱卡", MATH_CARDS)):
        print(f"== {name}（{len(cards)} 张）==")
        # 每个 topic 内按现有卡数续号，避免 id 冲突
        counters = {}
        for card in cards:
            tid = card["topic"]
            if tid not in counters:
                if not check_topic(conn, tid):
                    print(f"  ❌ 知识点 {tid} 不存在于 topics 表，跳过该卡")
                    counters[tid] = None
                    continue
                n = conn.execute(
                    "SELECT COUNT(*) FROM questions WHERE topic_id = ?", (tid,)).fetchone()[0]
                counters[tid] = n + 1
            if counters[tid] is None:
                continue
            total += insert_card(conn, tid, counters[tid], card, dry)
            counters[tid] += 1
        print()

    if not dry:
        conn.commit()
    conn.close()
    print(f"完成：新增 {total} 张。" + ("（dry-run，未写库）" if dry else ""))


if __name__ == "__main__":
    main()

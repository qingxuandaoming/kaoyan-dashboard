#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
add_judge_fill_short_cards.py — 补「判断 / 填空（回忆翻转）/ 简答（AI 批改）」三种题型

【为什么加非选择题】
选择题能覆盖"辨析"，但覆盖不了两件考研真正要练的事：
  1. 回忆：心里能不能把结论/公式默出来 → 填空（cloze）。题面挖空成 ______，
     点「显示答案」才填回并给出完整原文（大盘渲染层已支持 {{c1::...}} 挖空）。
  2. 表达：能不能把推理过程写清楚 → 简答。手写拍张照片上传，
     由 DeepSeek 视觉模型辨认 + 按得分点批改（服务端 /api/grade，见 src/grade_llm.js）。
判断题补的是"一眼看穿命题陷阱"的能力（对/错两选，最考概念边界）。

【素材来源】与 add_cards_grammar_math_20260913.py 同一批笔记：
  英语 English/grammar/考研英语语法笔记.md、长难句笔记.md、word&phrase/词根词缀/词根词缀总表.md
  数学 Math/高数/计算陷阱.md、线代/计算陷阱.md、概率论/计算陷阱.md

【简答题 content 结构】比选择题多两个字段，AI 批改直接读它们：
  { stem, reference_answer, key_points[], explanation, traps[], tags[] }

用法:
    python tools/add_judge_fill_short_cards.py --dry-run
    python tools/add_judge_fill_short_cards.py
"""

import argparse
import io
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(r"E:\NPEE")
DB_PATH = Path(__file__).resolve().parent.parent / "question_bank.db"   # 代码根（2026-09-25 起与笔记库分离）
TODAY = date.today().isoformat()
SOURCE = f"判断填空简答-{TODAY}"

# ===========================================================================
# 一、填空题（cloze：{{c1::答案}}，未揭晓时显示为 ______）
# ===========================================================================

FILL_CARDS = [
    # ---------------- 英语语法 ----------------
    {
        "topic": "ENG-GRAM-01-01",
        "stem": "虚拟语气与过去事实相反：从句用 {{c1::had done}}，主句用 {{c1::would have done}}。",
        "answer": "had done / would have done",
        "explanation": "与现在事实相反：从句 did/were、主句 would do；与过去事实相反：从句 had done、主句 would have done；"
                       "与将来事实相反：从句 were to do/should do、主句 would do。",
        "traps": ["把「与现在相反」和「与过去相反」的时态配对记反，写作里一用就露。"],
        "tags": ["语法", "虚拟语气"],
    },
    {
        "topic": "ENG-GRAM-01-01",
        "stem": "主将从现：By the time 引导的时间状语从句用 {{c1::一般现在时}} 表将来，主句表达「到那时已完成」用 {{c2::将来完成时}}。",
        "answer": "一般现在时 / 将来完成时",
        "explanation": "By the time I see you, I will have graduated.；平移到过去则从句用一般过去、主句用过去完成：By the time I saw you, I had graduated.",
        "traps": ["从句误用将来时（By the time I will see you ❌）。"],
        "tags": ["语法", "时态"],
    },
    {
        "topic": "ENG-GRAM-01-01",
        "stem": "现在完成进行时的结构是 {{c1::have/has been doing}}；短暂动词（come/go/arrive）用这个时态表示 {{c2::反复多次}}，而不是持续。",
        "answer": "have/has been doing / 反复多次",
        "explanation": "I have been coming to Beijing for 14 years. = 14 年间反复来京；表持续居住要用 I have been in Beijing for 14 years.",
        "traps": ["把短暂动词的完成进行时误读成「一直在持续」。"],
        "tags": ["语法", "时态"],
    },
    {
        "topic": "ENG-GRAM-01-01",
        "stem": "had been 只有后接 {{c1::及物动词的过去分词}} 时才构成被动语态；had been + 形容词/名词是 {{c2::系表结构}}（主动）。",
        "answer": "过去分词 / 系表结构",
        "explanation": "被动：The door had been locked.；系表（主动）：He had been ill. / He had been a teacher.；"
                       "去过（主动）：She had been to Paris.；过去完成进行（主动）：They had been waiting.",
        "traps": ["见到 had been 就判被动，把 had been ill 也当被动。"],
        "tags": ["语法", "被动语态"],
    },
    {
        "topic": "ENG-GRAM-01-04",
        "stem": "使役动词与感官动词（make/let/have/see/hear/watch）主动语态后接 {{c1::省略 to 的裸不定式}}，变成被动语态后必须 {{c2::把 to 还原}}。",
        "answer": "裸不定式 / 还原 to",
        "explanation": "He made me do it. → I was made to do it.；I saw him run. → He was seen to run.",
        "traps": ["被动语态里仍省略 to（I was made do it ❌）。"],
        "tags": ["语法", "非谓语"],
    },
    {
        "topic": "ENG-GRAM-01-02",
        "stem": "名词性从句只能用 whether 的四种情况：{{c1::介词后}}、{{c2::不定式前}}、与 or not 直接连用、以及句首主语从句。",
        "answer": "介词后 / 不定式前",
        "explanation": "It depends on whether he agrees.；I don't know whether to stay.；Whether he will come is uncertain.。"
                       "if 只用于口语化的动词宾语从句。",
        "traps": ["在介词后或句首主语从句里误用 if。"],
        "tags": ["语法", "从句"],
    },
    # ---------------- 词根词缀 ----------------
    {
        "topic": "ENG-VOC-01-03",
        "stem": "三步拆词法的顺序：切后缀定 {{c1::词性}} → 切前缀定 {{c2::方向}} → 找词根定 {{c3::意思}}。",
        "answer": "词性 / 方向 / 意思",
        "explanation": "心法：词根定意思，前缀定方向，后缀定词性。示范：unmistakable → -able（形容词）→ un-（否定）→ mistak(e)（弄错）。",
        "traps": ["先猜词义、忽略后缀，在长难句里把名词当谓语，找错句子主干。"],
        "tags": ["词根词缀", "拆词心法"],
    },
    {
        "topic": "ENG-VOC-01-03",
        "stem": "后缀 -able/-ible 的核心含义是「{{c1::能被……的}}」，含 {{c2::被动}} 义。",
        "answer": "能被……的 / 被动",
        "explanation": "unmistakable = un + mistake + able → 不可能被弄错的 → 明确无误的；reliable = 能被依靠的 → 可靠的。",
        "traps": ["把 -able 理解成主动的「能够」，于是把 unmistakable 解成「能弄错」。"],
        "tags": ["词根词缀", "后缀"],
    },
    {
        "topic": "ENG-VOC-01-03",
        "stem": "否定前缀 in- 的语音同化：接 b/m/p → {{c1::im-}}；接 l → {{c2::il-}}；接 r → {{c3::ir-}}。",
        "answer": "im- / il- / ir-",
        "explanation": "impossible、impartial；illegal；irregular、irreversible。",
        "traps": ["只记 un-/in-，遇到 irreversible、illegal 拆不出否定义。"],
        "tags": ["词根词缀", "前缀"],
    },
    {
        "topic": "ENG-VOC-01-03",
        "stem": "词根 spect/spic 表「{{c1::看}}」；词根 ced/cess 表「{{c2::走、让}}」。",
        "answer": "看 / 走、让",
        "explanation": "perspective（看穿→视角）、conspicuous（显眼的）；proceed（向前走）、concede（让一步→承认）。",
        "traps": ["把 perspective（视角）与 prospect（前景）混为一谈，二者方向不同。"],
        "tags": ["词根词缀", "词根"],
    },
    # ---------------- 数学 ----------------
    {
        "topic": "MATH-GS-02-08",
        "stem": "求拐点的可疑点必须列两类：f″ 的 {{c1::零点}} 和 f″ {{c2::不存在的点}}（拐点判据不要求 f″(x₀) 存在）。",
        "answer": "零点 / 不存在的点",
        "explanation": "f(x)=|x|x 在 x=0 处 f″ 不存在，但 f″ 两侧变号，所以是拐点。与极值点同构记忆："
                       "极值可疑点 = 驻点 + 不可导点。",
        "traps": ["只扫 f″ 的零点，漏掉 f″ 不存在的点，少数一个拐点。"],
        "tags": ["计算陷阱", "拐点"],
    },
    {
        "topic": "MATH-GS-02-05",
        "stem": "用麦克劳林展开求高阶导数：f⁽ⁿ⁾(0) = {{c1::n!}} × 展开式中 xⁿ 的系数。",
        "answer": "n!",
        "explanation": "ln(1+x) = x − x²/2 + x³/3 − …，x³ 项系数是 1/3，故 f⁽³⁾(0) = 3! × 1/3 = 2。",
        "traps": ["直接把展开式系数当成导数值，漏乘 n!。"],
        "tags": ["计算陷阱", "高阶导数"],
    },
    {
        "topic": "MATH-GS-03-07",
        "stem": "反常积分 ∫(−∞ 到 +∞) f(x)dx 收敛要求两端 {{c1::各自独立收敛}}；奇函数对称积分为 0 只是 {{c2::柯西主值}}，不能当作反常积分的值。",
        "answer": "各自独立收敛 / 柯西主值",
        "explanation": "∫(−∞,+∞) x³dx 的两侧单侧积分都发散，故反常积分发散；柯西主值强制两端同步趋向，是更弱的定义。",
        "traps": ["看到奇函数就直接写「对称区间积分为 0」。"],
        "tags": ["计算陷阱", "反常积分"],
    },
    {
        "topic": "MATH-GS-03-08",
        "stem": "极坐标下玫瑰线 r = a·sin nθ 的面积积分限：n 为奇数取 {{c1::0 到 π}}，n 为偶数取 {{c2::0 到 2π}}（口诀「奇半偶全」）。",
        "answer": "0 到 π / 0 到 2π",
        "explanation": "n 为奇数时 θ 从 π 到 2π 的轨迹与 0 到 π 完全重合，取整圈会把面积算成 2 倍。"
                       "另注意带平方的是双纽线（r² = a²cos 2θ），不带平方才是玫瑰线。",
        "traps": ["三叶玫瑰线误取 0 到 2π，结果翻倍；把双纽线与玫瑰线的方程混淆。"],
        "tags": ["计算陷阱", "定积分应用"],
    },
    {
        "topic": "MATH-XD-05-02",
        "stem": "判断两个实对称矩阵：合同看 {{c1::(r, p, q)}}（符号个数），相似看 {{c2::特征值数值}}（含重数）。",
        "answer": "(r, p, q) / 特征值",
        "explanation": "实对称时相似 ⇒ 合同，但合同 ⇏ 相似：E 与 diag(4,1) 合同（p=2,q=0 相同）但不相似（特征值 1,1 与 4,1 不同）。",
        "traps": ["判合同时要求特征值相同；认为实对称矩阵合同则必相似。"],
        "tags": ["计算陷阱", "合同与相似"],
    },
    {
        "topic": "MATH-GL-04-03",
        "stem": "协方差与相关系数的区别：cov(U, U) = {{c1::DU（方差）}}；ρ(U, U) = {{c2::1}}。",
        "answer": "DU（方差） / 1",
        "explanation": "把「变量与自己的相关系数为 1」迁移成 cov(U,U)=1 是典型错误：X~N(0,σ²) 时 Cov(X,X³) = 3σ⁴，"
                       "若按 σ⁴+1 算，相关系数会 >1，而 |ρ| ≤ 1 恒成立，可直接自检。",
        "traps": ["把 cov(U,U) 写成 1，算出相关系数大于 1 还没发现。"],
        "tags": ["计算陷阱", "协方差"],
    },
]

# ===========================================================================
# 二、判断题（考概念边界：命题里埋一个错，看能不能一眼看穿）
# ===========================================================================

JUDGE_CARDS = [
    {
        "topic": "MATH-GL-01-05",
        "stem": "若事件 A、B、C 两两独立，则必有 P(ABC) = P(A)P(B)P(C)。",
        "answer": False,
        "explanation": "错误。两两独立 ≠ 相互独立（事件数 ≥ 3 时不等价）。反例：抛两枚硬币，A=第一枚正面、B=第二枚正面、"
                       "C=两枚相同，三者两两独立但 P(ABC) = 1/4 ≠ 1/8。",
        "traps": ["把「两两独立」当成「相互独立」，直接用三事件连乘或「至少一个发生」的对立公式。"],
        "tags": ["计算陷阱", "独立性"],
    },
    {
        "topic": "MATH-GS-02-08",
        "stem": "若 f″(x₀) = 0，则点 (x₀, f(x₀)) 一定是曲线 y = f(x) 的拐点。",
        "answer": False,
        "explanation": "错误。f″(x₀)=0 只是拐点的必要条件（且仅当 f″(x₀) 存在时）。反例 f(x)=x⁴ 在 x=0 处 f″(0)=0，"
                       "但 f″ 不变号，不是拐点；反过来 f(x)=|x|x 在 x=0 处 f″ 不存在，却**是**拐点。",
        "traps": ["把 f″=0 当成拐点的充分条件；漏掉「两侧变号」这个真正的判据。"],
        "tags": ["计算陷阱", "拐点"],
    },
    {
        "topic": "MATH-GS-02-06",
        "stem": "已知 g(x) 在 x = 0 处二阶可导，求 lim(x→0) g(x)/x² 时可以连续两次使用洛必达法则。",
        "answer": False,
        "explanation": "错误。「一点二阶可导」不保证 g″(x) 在 0 的去心邻域存在或连续，而洛必达要求分子分母在去心邻域"
                       "可导到所需阶数。正确做法是用泰勒展开：g(x) = g″(0)x²/2 + o(x²)，极限为 g″(0)/2。"
                       "口诀「一点可导用泰勒，邻域可导才洛必达」。",
        "traps": ["连续两次洛必达，偷设了 g″(x) 在 0 的邻域存在且连续。"],
        "tags": ["计算陷阱", "洛必达"],
    },
    {
        "topic": "MATH-GS-01-04",
        "stem": "因为 ln(1−2x) ~ −2x（x→0），所以求 lim(x→0) [ln(1−2x) + 2x·f(x)]/x² 时可以直接把 ln(1−2x) 换成 −2x。",
        "answer": False,
        "explanation": "错误。这里 −2x 与 2x·f(x) 的一阶项同阶且系数相反，替换后一阶主部完全抵消，被丢掉的二阶项 −2x² 才是"
                       "决定极限的项。必须泰勒展开到二阶：ln(1−2x) = −2x − 2x² + o(x²)，由此得 f′(0) = 1。",
        "traps": ["分子是两个同阶无穷小相加减时仍用等价替换，主部抵消后结论全错。"],
        "tags": ["计算陷阱", "等价无穷小"],
    },
    {
        "topic": "MATH-XD-05-02",
        "stem": "两个实对称矩阵若合同，则一定相似。",
        "answer": False,
        "explanation": "错误。合同只看 (r, p, q) 即惯性指数的符号个数，相似看特征值的数值。实对称时相似 ⇒ 合同，"
                       "反向不成立：E 与 diag(4,1) 合同（p=2, q=0 相同）但不相似（特征值 1,1 与 4,1 不同）。",
        "traps": ["把「实对称时相似 ⇒ 合同」的方向记反。"],
        "tags": ["计算陷阱", "合同与相似"],
    },
    {
        "topic": "MATH-GS-03-06",
        "stem": "∫(−a 到 a) 1/(1+e^{kx}) dx = a（a > 0，k 为任意非零常数）。",
        "answer": True,
        "explanation": "正确。对称区间先试区间再现换元 x = −t：I = ∫(−a 到 a) 1/(1+e^{−kx}) dx，两式相加得 "
                       "2I = ∫(−a 到 a) 1 dx = 2a，故 I = a，与 k 无关。",
        "traps": ["硬拆分段或分部积分；误以为被积函数是奇函数或偶函数（它既非奇也非偶）。"],
        "tags": ["计算陷阱", "定积分"],
    },
    {
        "topic": "ENG-GRAM-01-02",
        "stem": "在定语从句中，that 既可以引导限制性定语从句，也可以引导非限制性定语从句。",
        "answer": False,
        "explanation": "错误。that 只能引导限制性定语从句；逗号后的非限制性定语从句要用 which/who/whom/whose。",
        "traps": ["在非限制性从句里用 that（The house, that was built in 1750 ❌）。"],
        "tags": ["语法", "定语从句"],
    },
    {
        "topic": "ENG-GRAM-01-02",
        "stem": "名词性从句中，介词后面只能用 whether 引导，不能用 if。",
        "answer": True,
        "explanation": "正确。It depends on whether he agrees. ✓ / It depends on if he agrees. ✗。"
                       "同理「与 or not 直接连用」「不定式前」「句首主语从句」三种情况也只能用 whether。",
        "traps": ["在介词后误用 if。"],
        "tags": ["语法", "从句"],
    },
    {
        "topic": "ENG-GRAM-01-04",
        "stem": "insist 后面既可以接 insist on doing sth，也可以接 insist to do sth。",
        "answer": False,
        "explanation": "错误。insist 只接 on doing 或 that 从句（insist that sb (should) do），没有 insist to do 的用法。"
                       "同类高频搭配还有：object to doing、look forward to doing、be used to doing。",
        "traps": ["受 want to do 的类比影响，写出 insist to do。"],
        "tags": ["语法", "非谓语"],
    },
    {
        "topic": "ENG-VOC-01-03",
        "stem": "后缀 -ful 与 -less 是一对反义后缀：-ful 表「充满……的」，-less 表「没有……的」。",
        "answer": True,
        "explanation": "正确。careful（小心的）/ careless（粗心的）；hopeful / hopeless；groundless = 毫无根据的。",
        "traps": ["把 -less 理解成「少」而不是「没有」：groundless 是「毫无根据」。"],
        "tags": ["词根词缀", "后缀"],
    },
    {
        "topic": "ENG-GRAM-02-06",
        "stem": "He is no more diligent than his brother. 的意思是「他不比他哥哥更勤奋」（两人都勤奋）。",
        "answer": False,
        "explanation": "错误。no 表全盘否定：no more A than B = 两者都不 A，本句意为「他和他哥哥都不勤奋」。"
                       "「不比……更」（两者都 A，程度有别）要用 not more A than B。",
        "traps": ["把 no more … than 按字面读成「不比……更」，与 not more … than 互换。"],
        "tags": ["语法", "比较结构"],
    },
]

# ===========================================================================
# 三、简答题（手写拍照 → DeepSeek 视觉模型按得分点批改）
#    key_points 是判分依据，写具体、可核对；reference_answer 要能独立看懂。
# ===========================================================================

SHORT_CARDS = [
    {
        "topic": "MATH-GS-02-06",
        "stem": "已知 g(x) 在 x=0 处二阶可导，g(0)=g′(0)=0。求 lim(x→0) g(x)/x² 时，为什么不能连续两次使用洛必达法则？正确做法是什么？",
        "reference_answer": "洛必达法则要求分子、分母在 x=0 的去心邻域内可导到所需阶数；题目只给了「在 x=0 处二阶可导」，"
                            "推不出去心邻域内 g″ 存在或连续，故第二次洛必达属于超条件使用。正确做法是用带佩亚诺余项的泰勒展开："
                            "g(x) = g(0) + g′(0)x + g″(0)x²/2 + o(x²) = g″(0)x²/2 + o(x²)，因此 g(x)/x² → g″(0)/2。"
                            "口诀：一点可导用泰勒，邻域可导才洛必达。",
        "key_points": [
            "洛必达的适用条件：分子分母在去心邻域内可导（且分母导数不为零）",
            "「一点二阶可导」推不出「邻域内二阶可导/二阶导连续」",
            "正确做法是泰勒展开到 x² 项",
            "结论是极限等于 g″(0)/2",
        ],
        "explanation": "反例：f(x)=x²sin(1/x) 在 0 处可导，但 x≠0 时 f′(x)=2x·sin(1/x)−cos(1/x)，lim(x→0) f′(x) 不存在——"
                       "可见「一点可导」与「导数连续」是两回事。",
        "traps": ["把「二阶可导」读成「二阶导连续」，于是放心地连用两次洛必达。"],
        "tags": ["计算陷阱", "洛必达", "泰勒"],
    },
    {
        "topic": "MATH-GS-06-01",
        "stem": "判别级数 Σ(n=1→∞) ln(1 + (−1)ⁿ/√n) 的敛散性，并说明为什么不能直接用等价无穷小替换。",
        "reference_answer": "发散。通项里的 ln(1+xₙ) 不能直接用 ln(1+xₙ) ~ xₙ 判同敛散，因为等价无穷小替换判同敛散"
                            "只对正项（或同号）级数成立，而本题通项变号。必须泰勒展开到二阶："
                            "ln(1 + (−1)ⁿ/√n) = (−1)ⁿ/√n − 1/(2n) + O(n^(−3/2))。第一项由莱布尼茨判别法收敛，"
                            "第二项 −(1/2)Σ1/n 是调和级数、发散到 −∞，主导了部分和，故原级数发散。",
        "key_points": [
            "结论：发散",
            "指出等价无穷小替换判同敛散只适用于正项/同号级数",
            "泰勒展开到二阶，写出 −1/(2n) 这一项",
            "说明 −(1/2)Σ1/n 发散并主导部分和",
        ],
        "explanation": "口诀：正项才能用等价，变号必须多展一阶。看到 ln(1+uₙ)、e^{uₙ}−1、sin uₙ 且 uₙ 变号，立刻想到二阶展开。",
        "traps": ["直接由 ln(1+xₙ) ~ xₙ 断言与原级数同敛散，误判为收敛。"],
        "tags": ["计算陷阱", "级数"],
    },
    {
        "topic": "MATH-GS-05-01",
        "stem": "计算 ∬_D x·cos√(x²+y²)/(x+y) dσ 时（D 关于直线 y = x 对称），如何用轮换对称性简化？使用它的前提是什么？",
        "reference_answer": "轮换对称性约束的是**积分区域**，不是被积函数：只要 D 关于 y = x 对称，就有 "
                            "∬_D f(x,y)dσ = ∬_D f(y,x)dσ，于是 I = ½∬_D [f(x,y) + f(y,x)]dσ。"
                            "本题相加后分子 x + y 与分母 x + y 约掉，积分立刻简化。"
                            "被积函数 f(y,x) ≠ f(x,y) 完全正常，不影响使用；反过来，若区域不关于 y = x 对称，"
                            "则绝不能写这个等式。",
        "key_points": [
            "前提是积分区域 D 关于 y = x 对称（不是被积函数对称）",
            "写出 I = ½∬_D [f(x,y) + f(y,x)]dσ",
            "说明本题相加后 x + y 与分母约掉",
            "指出 f(y,x) ≠ f(x,y) 不构成障碍（或指出区域不对称时不可用）",
        ],
        "explanation": "口诀：轮换看区域，不看函数；不对称也能用，靠相加。与极坐标打通：轮换 x↔y 相当于 θ ↔ π/2 − θ。",
        "traps": ["看到被积函数不对称就放弃轮换对称，改用极坐标硬算；反向误用：区域不对称时仍写轮换等式。"],
        "tags": ["计算陷阱", "二重积分", "轮换对称"],
    },
    {
        "topic": "MATH-XD-01-02",
        "stem": "4 阶行列式中，副对角线项 a₁₄a₂₃a₃₂a₄₁ 的符号是正还是负？说明判断依据，并给出 2、3、4、5 阶副对角线项的符号。",
        "reference_answer": "4 阶副对角线项的符号是**正**。副对角线项的符号为 (−1)^{n(n−1)/2}（也等于列标 n, n−1, …, 1 的逆序数"
                            "t = n(n−1)/2 的奇偶性）：n=2 时 t=1 → 负；n=3 时 t=3 → 负；n=4 时 t=6 → 正；n=5 时 t=10 → 正。"
                            "所以「副对角线永远是负号」只在 2、3 阶成立，4 阶起就是正号。",
        "key_points": [
            "结论：4 阶副对角线项为正",
            "给出符号公式 (−1)^{n(n−1)/2} 或逆序数 n(n−1)/2",
            "给出 n=2,3 为负、n=4,5 为正的对照",
            "指出「副对角线恒为负」是错的",
        ],
        "explanation": "现场算逆序数最稳妥：列标 (4,3,2,1) 的逆序数为 3+2+1 = 6，偶数 → 正号。",
        "traps": ["死记「副对角线是负号」，4 阶及以上展开时符号写反。"],
        "tags": ["计算陷阱", "行列式", "逆序数"],
    },
    {
        "topic": "MATH-XD-04-03",
        "stem": "用克拉默法则解线性方程组时求得 |A| = 0，能否直接断定方程组无解？应该怎么做？",
        "reference_answer": "不能。|A| = 0 只说明系数矩阵不可逆、克拉默法则失效，此时必须比较系数矩阵的秩 r(A) 与增广矩阵的秩 "
                            "r(A|b)：若 r(A) < r(A|b)，则方程组无解；若 r(A) = r(A|b) = r < n，则有无穷多解（自由未知量 n − r 个）；"
                            "若 r(A) = r(A|b) = n，则有唯一解（但 |A| = 0 时不可能）。口诀：行列式零看秩比。"
                            "另外用克拉默法则构造 D_j 时，换成 b 的永远是第 j **列**，不是第 j 行。",
        "key_points": [
            "不能直接断定无解，必须比较 r(A) 与 r(A|b)",
            "r(A) < r(A|b) → 无解；r(A) = r(A|b) < n → 无穷多解",
            "自由未知量个数为 n − r",
            "构造 D_j 时换的是第 j 列而不是行",
        ],
        "explanation": "齐次方程组是特例：|A| = 0 时必有非零解（无穷多解），因为 b = 0 时 r(A|b) = r(A) 恒成立。",
        "traps": ["求出 |A| = 0 就直接写「方程组无解」。"],
        "tags": ["计算陷阱", "线性方程组"],
    },
    {
        "topic": "ENG-GRAM-02-06",
        "stem": "用词根词缀法拆解单词 unmistakable，写出拆解过程、词义，并说明 -able 在这里的语法含义。",
        "reference_answer": "unmistakable = un- + mistak(e) + -able。un- 是否定前缀；mistake 意为「弄错」（mis- 错误 + take 拿）；"
                            "-able/-ible 是形容词后缀，核心含义是「能被……的」，含**被动**义。"
                            "所以字面是「不可能被弄错的」，引申为「明确无误的、不会弄错的」。"
                            "同类：reliable = 能被依靠的 → 可靠的；visible = 能被看见的 → 可见的。",
        "key_points": [
            "拆成 un- + mistake + -able 三部分",
            "指出 -able 表「能被……的」，含被动义",
            "给出词义：明确无误的 / 不可能被弄错的",
            "举出同族例词（reliable、visible 等任一同类词）",
        ],
        "explanation": "把 -able 当主动解会得出「能弄错的」，与词义正好相反——这是 -able 最高频的误读。",
        "traps": ["把 -able 理解成主动的「能够」，忽略被动义。"],
        "tags": ["词根词缀", "拆词示范"],
    },
    {
        "topic": "ENG-GRAM-02-01",
        "stem": "分析下面这句话：有几个分句？主干是什么？逐个数出每个谓语动词及其所属分句。\n"
                "However, whether such a sense of fairness evolved independently in capuchins and humans, "
                "or whether it stems from the common ancestor that the species had 35 million years ago, "
                "is, as yet, an unanswered question.",
        "reference_answer": "共 4 个分句，4 个谓语动词：① evolved——第一个主语从句的谓语，主语是 such a sense of fairness；"
                            "② stems (from)——第二个主语从句的谓语，主语是 it；③ had——定语从句的谓语，"
                            "修饰 the common ancestor，主语是 the species，宾语是 35 million years；"
                            "④ is——主句的系动词。主干是：whether… or whether… is an unanswered question（两个并列的主语从句"
                            "整体作主语，故谓语用单数 is），as yet 是插入状语。",
        "key_points": [
            "分句数为 4（或点数出 4 个谓语动词 evolved / stems / had / is）",
            "主干：whether… or whether… is an unanswered question",
            "指出 had 属于定语从句（修饰 the common ancestor），不是主句谓语",
            "指出两个并列主语从句整体作主语，谓语用单数 is",
        ],
        "explanation": "方法：数谓语定分句——有几个谓语就有几个分句；非谓语（to do / doing / done）不算谓语。",
        "traps": ["把 that 从句的谓语 had 误当主句谓语；把 to do / doing 当谓语导致分句数虚高。"],
        "tags": ["语法", "长难句", "嵌套从句"],
    },
    {
        "topic": "ENG-GRAM-02-06",
        "stem": "举例说明 no more A than B 与 not more A than B 的区别，并各举一个句子。",
        "reference_answer": "区别在 no 表全盘否定、not 表程度比较。not more A than B = 不比 B 更 A，两者都 A，只是程度有别；"
                            "no more A than B = 两者都不 A（= neither）。"
                            "例：He is not more diligent than his brother.（他不比哥哥更勤奋，两人都勤奋）"
                            "／ He is no more diligent than his brother.（他和他哥哥都不勤奋）。"
                            "同理 not less A than B（不比 B 差，两者都好）／ no less A than B（两者都 A，≈ as much as）。",
        "key_points": [
            "指出 no 表全盘否定、not 表程度比较",
            "not more A than B = 不比……更（两者都 A）",
            "no more A than B = 两者都不 A（neither）",
            "各举一个正确例句",
        ],
        "explanation": "这类结构常在阅读里反向设问，先分清「两者都」还是「两者都不」，再看程度。",
        "traps": ["把 no more … than 按字面读成「不比……多」。"],
        "tags": ["语法", "比较结构"],
    },
]

ALL = [("fill", FILL_CARDS), ("judge", JUDGE_CARDS), ("short", SHORT_CARDS)]


def validate(qtype, card):
    """入库前的字段闸门：缺关键字段直接拦下，避免出到大盘里才发现。"""
    problems = []
    if not card.get("stem"):
        problems.append("缺 stem")
    if not card.get("explanation"):
        problems.append("缺 explanation")
    if qtype == "fill":
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


def insert_card(conn, qtype, topic_id, seq, card, dry):
    qid = f"Q-{topic_id}-{seq:04d}"
    if conn.execute("SELECT 1 FROM questions WHERE id = ?", (qid,)).fetchone():
        print(f"  [已存在·跳过] {qid}")
        return 0
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

    total = 0
    # ⚠️ 计数器必须在**三种题型之间共享**：每个 topic 的序号是全局唯一的，
    #    按题型各自从 0 数会让 fill/judge 撞同一个 id（真跑时后一张被当成
    #    「已存在」静默跳过，卡就悄悄少了）。
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
                n = conn.execute("SELECT COUNT(*) FROM questions WHERE topic_id = ?",
                                 (tid,)).fetchone()[0]
                counters[tid] = n + 1
            if counters[tid] is None:
                continue
            problems = validate(qtype, card)
            if problems:
                print(f"  ❌ [字段拦截] {card.get('stem', '')[:28]} → {'；'.join(problems)}")
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

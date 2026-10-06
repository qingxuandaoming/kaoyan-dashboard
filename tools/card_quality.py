#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
card_quality.py — 闪卡选项质量闸门（单一事实来源）

出题、改卡、审计三处共用同一套判定，避免口径漂移。

这套规则来自 2026-09-13 的英语闪卡事故：15 张词缀卡的选项写成
    ["否定/相反/分离", "re-(再/重新)", "con-(共同/一起)", "pre-(前/预先)"]
只有正确答案是"裸中文释义"，三个干扰项都自带词缀标签 → 格式本身泄露答案，
学生不看词义就能 100% 选对。

判定项：
  E1 选项自带 "A. " 字母前缀（渲染层已画 A/B/C/D 徽章，会显示成 "Ⓐ A. xxx"）
  E2 选项数 ≠ 4
  E3 选项重复或近似重复
  W1 选项形态混排：部分带"词缀(释义)"标签、部分为裸释义 → 格式泄露
  W2 选项长度失衡（最长 > 最短的 2.5 倍）→"最长即答案"

拼写题（type="spell"，2026-10-06 新增，判定函数 check_spell_quality）：
  E4 answer 不是合法英文单词形式
  E5 answer 出现在 stem 里 → 等于把答案写在题面上
  E6 alt_answers 冗余或形态非法
  W4 stem 过长

被 tools/check_card_quality.py（全库审计）、generate_targeted_cards.py（入库拦截）、
以及各补卡脚本共用。
"""

import random
import re

# 选项自带 "A. " / "B、" / "C）" 等字母前缀。
# ⚠️ 标点后必须紧跟空白或中文，否则数学里的 "A、B、C 两两独立" 会被误判成字母前缀
#    （曾误报 Q-MATH-GL-01-05-0001：逗号后面是 "B" 而不是空白/中文）。
LETTER_PREFIX_RE = re.compile(r"^\s*([A-Da-d])\s*[\.、．\)）:：](?=\s|[一-鿿])\s*")

# "字母+连字符"紧接括号 = 词缀/术语标签，如 re-(再/重新)、-ful(充满...的)。
# 必须带连字符，否则 "ssthresh（慢开始阈值）" 这类"术语+括注"的正常选项会被误判。
AFFIX_LABEL_RE = re.compile(r"^\s*(-[A-Za-z]{1,12}|-?[A-Za-z]{1,12}-)\s*[（(]")

# 归一化只去**装饰性**标点，数学上有意义的符号一个都不能去：
#   "-"  去掉会让 "1" 与 "-1" 判成重复（曾误报 Q-408-OS-02-0008）
#   "()" 去掉会让 "(eˣ+C)/x" 与 "eˣ + C/x" 判成完全相同（曾误报 Q-MATH-GS-07-0042），
#        但这两个式子在数学上并不相等
# 所以这里只留中文标点、引号、空白与破折号。
PUNCT_RE = re.compile(r"[\s，。、；：“”\"'’‘—]")

LEN_RATIO_LIMIT = 2.5
# 选项普遍很短时（如词根卡只有"走、让"/"站"/"带来"），字数比不构成线索，不报 W2
SHORT_OPTION_CHARS = 8


def normalize_option(s):
    """归一化选项文本，用于重复判定。"""
    return PUNCT_RE.sub("", str(s or ""))


def check_options_quality(opts):
    """返回问题列表；空列表表示通过。"""
    if not isinstance(opts, list) or len(opts) != 4:
        return [f"选项数={len(opts) if isinstance(opts, list) else '无'}，应为 4"]

    problems = []

    # E1 字母前缀
    for i, o in enumerate(opts):
        if LETTER_PREFIX_RE.match(str(o)):
            problems.append(f"选项{i}自带字母前缀（渲染层会重复加徽章）")

    # W1 形态混排（格式泄露）
    labeled = [o for o in opts if AFFIX_LABEL_RE.match(str(o))]
    if labeled and len(labeled) < len(opts):
        problems.append("选项形态混排：部分带“词缀(释义)”标签、部分为裸释义，格式会泄露答案")

    # E3 重复：只判「归一化后完全相同」。
    # 不做「包含即近似重复」——数学选项里一个因子之差就是不同答案：
    # x·e^(x²) 是 2x·e^(x²) 的子串，但两者是不同选项（曾误报 Q-MATH-GS-03-0007）。
    ns = [normalize_option(o) for o in opts]
    for i in range(len(opts)):
        for j in range(i + 1, len(opts)):
            if ns[i] and ns[i] == ns[j]:
                problems.append(f"选项{i}与选项{j}完全相同")

    # W2 长度失衡
    lens = [len(str(o)) for o in opts]
    if (min(lens) > 0 and max(lens) > SHORT_OPTION_CHARS
            and max(lens) / min(lens) > LEN_RATIO_LIMIT):
        problems.append(f"选项长度失衡 {min(lens)}–{max(lens)} 字，最长项易成为答案线索")

    return problems


def shuffle_opts(qid, options, answer):
    """确定性打乱选项，同步 answer 下标。

    new_options[idx.index(answer)] == options[answer] —— 正确答案的**文本**不变，
    只改位置。用 qid 作种子，重复运行结果一致；否则答案会全落在 A，形成新线索。
    """
    idx = list(range(len(options)))
    random.Random(qid).shuffle(idx)
    return [options[i] for i in idx], idx.index(answer)


# ---------------------------------------------------------------- 拼写题 spell
# 2026-10-06 新增题型：给中文释义/语境，学生键入英文单词，本地模糊判分
# （完全对 / 差一个字母 / 错）。这是系统里第一个「可输入答案」的题型，
# 出现的原因是作文复盘里拼写硬伤命中 7/7 篇，而 fill 只是「挖空→显示答案」，
# 根本测不出拼写。
#
# 判定项：
#   E4 answer 不是合法的英文单词形式（空串 / 含数字或中文 / 带空格）
#   E5 answer 出现在 stem 里 —— 等于把答案写在题面上（同 E1 格式泄露的性质）
#   E6 alt_answers 里有与 answer 归一化后相同的项（冗余，且易掩盖真错）
#   W4 stem 过长（>120 字）—— 拼写题要的是「一秒看懂问什么」，不是阅读理解
SPELL_ANSWER_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]*$")
SPELL_STEM_LIMIT = 120


def normalize_word(s):
    """拼写比较用的归一化：小写、去首尾非字母、压空格。

    与前端 FLASH_JS 的 spellNorm 保持同一套规则——两边不一致会出现
    「审计说没问题、学生判错」这种最难查的偏差。
    """
    t = re.sub(r"^[^A-Za-z]+|[^A-Za-z]+$", "", str(s or "").strip().lower())
    return re.sub(r"\s+", " ", t)


def check_spell_quality(content):
    """校验拼写题 content；返回问题列表，空列表表示通过。"""
    if not isinstance(content, dict):
        return ["content 不是对象"]

    problems = []
    ans = content.get("answer")
    stem = str(content.get("stem") or "")

    # E4 answer 形态
    if not isinstance(ans, str) or not ans.strip():
        problems.append("E4 answer 缺失或为空")
    elif not SPELL_ANSWER_RE.match(ans.strip()):
        problems.append(f"E4 answer 不是合法英文单词形式：{ans!r}"
                        "（只允许字母、连字符、撇号）")

    # E5 答案泄露：answer 不能以「整词」形式出现在 stem 里。
    # ⚠️ 不能用 normalize_word(stem) 去比对：那个函数只剥首尾非字母，
    #    中文题干里"……的英文单词（名词，复数 phenomena）"会被剥成 "phenomena"，
    #    反而丢掉上下文、漏判真正写在题面上的答案。这里直接在原串上做整词搜索。
    # 用前后 lookaround 而不是 \b：answer 允许连字符/撇号，\b 在 "self-aware" 上会错切。
    if isinstance(ans, str) and ans.strip() and stem:
        na = normalize_word(ans)
        if na and len(na) >= 3 and re.search(
                r"(?<![A-Za-z])" + re.escape(na) + r"(?![A-Za-z])", stem.lower()):
            problems.append(f"E5 answer {ans!r} 出现在 stem 里，等于把答案写在题面上")

    # E6 alt_answers 冗余
    alts = content.get("alt_answers")
    if alts is not None:
        if not isinstance(alts, list):
            problems.append("E6 alt_answers 不是数组")
        else:
            na = normalize_word(ans)
            for a in alts:
                if normalize_word(a) == na:
                    problems.append(f"E6 alt_answers 里的 {a!r} 与 answer 相同，属冗余")
                if not SPELL_ANSWER_RE.match(str(a).strip()):
                    problems.append(f"E6 alt_answers 项不是合法单词形式：{a!r}")

    # W4 stem 过长
    if len(stem) > SPELL_STEM_LIMIT:
        problems.append(f"W4 stem 长 {len(stem)} 字（>{SPELL_STEM_LIMIT}），"
                        "拼写题应一句话说清问什么")

    return problems

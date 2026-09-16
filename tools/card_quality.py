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

#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
latexify_math_cards.py — 把公式吃重的数学卡改成 LaTeX（大盘已支持按需 KaTeX）

【为什么只改这些】大盘闪卡区 2026-09-13 起支持按需 KaTeX（全局 richText/texWrap，
内容里出现 $...$ 才懒加载本地 KaTeX）。Unicode 数学符号（∫ Σ ² ₓ ≤）本身能看，
但**带上下限的积分、分式、求和指标**用 Unicode 写很别扭：
    ∫(−a 到 a) 1/(1+e^{kx}) dx      →  $\int_{-a}^{a}\frac{1}{1+e^{kx}}\,dx$
    Σ(i=1 到 n) sin(iπ/n)/(n + 1/i) →  $\sum_{i=1}^{n}\frac{\sin(i\pi/n)}{n+1/i}$
线代/概率那些只有上下标（a₁₄、λ₁、σ²）的卡维持 Unicode —— 收益小，
而且周报（weekly_flashcard_report.py → 飞书）走纯文本路径，TeX 会在那里露源码。

【安全约束】只改 questions.content，不碰 cards 表 FSRS 状态；
每条改写都断言"原文能在库里找到"，找不到就报错退出，绝不静默漏改。

用法:
    python tools/latexify_math_cards.py --dry-run
    python tools/latexify_math_cards.py
"""

import argparse
import io
import json
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = Path(r"E:\NPEE\src\question_bank.db")

# ---------------------------------------------------------------------------
# 改写表：qid -> {字段: 新值}
# 只列公式吃重的卡；字段值用 raw 字符串写，LaTeX 的反斜杠原样保留。
# ---------------------------------------------------------------------------

REWRITE = {
    # ---------------- 定积分 / 反常积分 ----------------
    "Q-MATH-GS-03-06-0001": {
        "stem": r"$\int_{-a}^{a}\dfrac{1}{1+e^{kx}}\,dx$ = ？",
        "options": [
            r"$a$（用区间再现换元 $x=-t$，两式相加得 $2I=2a$）",
            r"$2a$（把被积函数当成偶函数直接加倍）",
            r"$0$（被积函数在对称区间上正负抵消）",
            r"$a/2$（换元后漏掉系数 2）",
        ],
        "answer": 0,
        "explanation": (
            r"看到对称区间 $[-a,a]$ 上的 $\dfrac{1}{1+e^{kx}}$，第一反应是区间再现换元 $x=-t$："
            r"$I=\int_{-a}^{a}\dfrac{1}{1+e^{-kx}}\,dx$，与原式相加得 $2I=\int_{-a}^{a}1\,dx=2a$，故 $I=a$。"
            r"口诀「对称区间先想换元，定积分上限减下限，恒正函数不积负」。"
        ),
        "traps": [r"硬拆分段或分部积分绕远路；把 $\dfrac{1}{1+e^{kx}}$ 当奇函数或偶函数处理（它既非奇也非偶）。"],
    },
    "Q-MATH-GS-03-07-0001": {
        "stem": r"关于 $\int_{-\infty}^{+\infty} x^3\,dx$，下列说法正确的是？",
        "options": [
            r"反常积分发散：单侧积分发散，不能由奇偶性得 0",
            r"等于 0：$x^3$ 是奇函数，对称区间上正负相消",
            r"等于 0：即柯西主值，故反常积分收敛",
            r"等于 $+\infty$：$x^3$ 在正半轴无界",
        ],
        "answer": 0,
        "explanation": (
            r"先判单侧收敛性：$\int_{0}^{+\infty}x^3\,dx$ 与 $\int_{-\infty}^{0}x^3\,dx$ 都发散，"
            r"故反常积分 $\int_{-\infty}^{+\infty}x^3\,dx$ 发散。"
            r"奇函数在对称区间上的积分为 0，那只是**柯西主值**（强制两端同步趋向 $b=-a$），"
            r"不能写成反常积分等于 0 —— 反常积分要求两端**各自独立**收敛。"
        ),
        "traps": [r"口诀「奇函数无穷积分得 0，先判收敛再用奇偶」；把柯西主值当成反常积分的值。"],
    },
    # ---------------- 数列极限 / 无穷项求和 ----------------
    "Q-MATH-GS-01-02-0001": {
        "stem": r"求 $\lim\limits_{n\to\infty}\sum\limits_{i=1}^{n}\dfrac{\sin(i\pi/n)}{n+1/i}$，下列做法正确的是？",
        "options": [
            r"夹逼放缩：分母分别换成 $n+1$ 与 $n$，两边都化为定积分定义且极限相同",
            r"直接舍弃 $\dfrac{1}{i}$（相对 $n$ 很小），化为 $\sum\dfrac{\sin(i\pi/n)}{n}$",
            r"把 $\dfrac{1}{i}$ 用等价无穷小换成 $\dfrac{1}{n}$ 后再求和",
            r"先取 $n\to\infty$ 再对 $i$ 求和，得 $\sum 0=0$",
        ],
        "answer": 0,
        "explanation": (
            r"无穷多项求和不能凭「每项都是无穷小」就舍弃扰动项：反例 $\underbrace{\frac{1}{n}+\cdots+\frac{1}{n}}_{n\text{ 项}}=1$。"
            r"正确做法是夹逼：$\sum\dfrac{\sin(i\pi/n)}{n+1}\le$ 原式 $\le\sum\dfrac{\sin(i\pi/n)}{n}$，"
            r"两边都化为定积分定义 $\int_{0}^{1}\sin(\pi x)\,dx=\dfrac{2}{\pi}$。"
            r"口诀「有限项看阶数，无穷项看累积」。"
        ),
        "traps": [r"看到分母是 $n+f(i)$ 的微小扰动就当它不存在；先取极限再求和（次序颠倒）。"],
    },
    "Q-MATH-GS-06-01-0001": {
        "stem": r"判别 $\sum\limits_{n=1}^{\infty}\ln\!\left(1+\dfrac{(-1)^n}{\sqrt{n}}\right)$ 的敛散性，正确的是？",
        "options": [
            r"发散：泰勒展开到二阶，$-\frac{1}{2n}$ 这一同号项发散并主导部分和",
            r"收敛：由 $\ln(1+x_n)\sim x_n$，与原级数同敛散",
            r"收敛：通项趋于 0，所以级数收敛（必要条件当充分用）",
            r"发散：通项不趋于 0，故直接用通项判发散（通项判别法）",
        ],
        "answer": 0,
        "explanation": (
            r"变号级数禁止直接用等价无穷小判同敛散，必须泰勒展开到二阶："
            r"$\ln\!\left(1+\frac{(-1)^n}{\sqrt n}\right)=\frac{(-1)^n}{\sqrt n}-\frac{1}{2n}+O(n^{-3/2})$。"
            r"第一项由莱布尼茨判别法收敛，第二项 $-\frac12\sum\frac1n$ 是调和级数、发散到 $-\infty$，"
            r"主导了部分和走向 $\Rightarrow$ 原级数发散。口诀「正项才能用等价，变号必须多展一阶」。"
        ),
        "traps": [r"对变号级数用等价无穷小替换判同敛散；只看通项趋于 0 就判收敛（那只是必要条件）。"],
    },
    # ---------------- 二重积分 ----------------
    "Q-MATH-GS-05-01-0001": {
        "stem": r"计算 $\iint_D \dfrac{x\cos\sqrt{x^2+y^2}}{x+y}\,d\sigma$（$D$ 关于 $y=x$ 对称）时，正确思路是？",
        "options": [
            r"用轮换对称 $I=\frac12\iint_D[f(x,y)+f(y,x)]d\sigma$，分母 $x+y$ 约掉",
            r"被积函数不对称，所以轮换对称用不了，直接上极坐标硬算",
            r"先把区域 $D$ 换成不对称的，再套用轮换对称",
            r"由轮换对称直接得 $I=\iint_D f(y,x)d\sigma=0$",
        ],
        "answer": 0,
        "explanation": (
            r"轮换对称性约束的是**区域**，不是被积函数。只要 $D$ 关于 $y=x$ 对称，就有 "
            r"$\iint_D f(x,y)\,d\sigma=\iint_D f(y,x)\,d\sigma$，于是 "
            r"$I=\frac12\iint_D[f(x,y)+f(y,x)]\,d\sigma$，相加后分子 $x+y$ 与分母约掉，积分立刻简化。"
            r"$f(y,x)\ne f(x,y)$ 完全正常。口诀「轮换看区域，不看函数；不对称也能用，靠相加」。"
        ),
        "traps": [r"见被积函数不对称就放弃轮换对称；反向误用：区域不关于 $y=x$ 对称时仍写轮换等式。"],
    },
    "Q-MATH-GS-05-01-0002": {
        "stem": r"求 $\iint_D (1-2x^2-y^2)\,d\sigma$（$D:\ 2x^2+y^2\le 1$），下列做法正确的是？",
        "options": [
            r"对原被积函数积分（广义极坐标），得 $\frac{\pi}{2\sqrt2}$，小于区域面积",
            r"直接写成 $\iint_D d\sigma$，取椭圆面积 $\frac{\pi}{\sqrt2}$ 作为结果",
            r"取 $\frac{2\pi}{\sqrt2}$，因为被积函数在 $D$ 上的最大值是 1",
            r"取 $0$，因为被积函数关于原点对称、正负相互抵消",
        ],
        "answer": 0,
        "explanation": (
            r"「二重积分 = 面积」仅在被积函数恒为 1 时成立（$S_D=\iint_D 1\,d\sigma$）。"
            r"本题被积函数 $1-2x^2-y^2$ 在 $D$ 内从 1 变化到 0，不恒为 1。"
            r"令 $u=\sqrt2 x,\ v=y$ 化为单位圆，得 $\iint_D(1-2x^2-y^2)\,d\sigma"
            r"=\frac{1}{\sqrt2}\left(\pi-\frac{\pi}{2}\right)=\frac{\pi}{2\sqrt2}$。"
            r"自检：被积函数 $\in[0,1]$ $\Rightarrow$ 积分值必小于区域面积 $\frac{\pi}{\sqrt2}$。"
        ),
        "traps": [r"把被积函数当成 1，直接替换为椭圆面积（错答是正解的 2 倍）。"],
    },
    # ---------------- 极限 + 泰勒 ----------------
    "Q-MATH-GS-01-04-0001": {
        "stem": r"已知 $\lim\limits_{x\to 0}\dfrac{\ln(1-2x)+2x f(x)}{x^2}=0$ 且 $f(0)=1$，则 $f'(0)=$ ？",
        "options": [
            r"$1$（泰勒展开到二阶，一阶主部相消后由二阶项定出）",
            r"$0$（用 $\ln(1-2x)\sim-2x$ 替换，极限恒为 0，与 $f'$ 无关）",
            r"$-1$（把二阶项 $-2x^2$ 的系数当成 $f'(0)$）",
            r"$2$（把 $2x f(x)$ 中的系数 2 当成 $f'(0)$）",
        ],
        "answer": 0,
        "explanation": (
            r"$\ln(1-2x)$ 的一阶项 $-2x$ 与 $2x f(x)$ 的一阶项 $+2x$ 同阶、系数相反，"
            r"直接用等价无穷小替换会让一阶主部完全抵消，丢失关键信息。"
            r"须泰勒展开到二阶：$\ln(1-2x)=-2x-2x^2+o(x^2)$，代入得 "
            r"$\dfrac{2x(f(x)-1)-2x^2+o(x^2)}{x^2}\to 0$，故 $\dfrac{f(x)-1}{x}\to 1$，即 $f'(0)=1$。"
        ),
        "traps": [r"分子是两个同阶无穷小相加减（系数相反）时仍用等价替换，主部抵消后得出错误结论。"],
    },
    "Q-MATH-GS-02-06-0001": {
        "stem": r"已知 $g(x)$ 在 $x=0$ 处二阶可导，$g(0)=g'(0)=0$。求 $\lim\limits_{x\to 0}\dfrac{g(x)}{x^2}$ 时，正确的做法是？",
        "options": [
            r"用泰勒展开 $g(x)=\dfrac{g''(0)}{2}x^2+o(x^2)$，得极限 $\dfrac{g''(0)}{2}$",
            r"连续两次洛必达，把 $\lim\limits_{x\to 0}\dfrac{g''(x)}{2}$ 直接写成 $\dfrac{g''(0)}{2}$",
            r"由 $g(0)=g'(0)=0$ 断定 $g(x)\equiv 0$，故极限为 0",
            r"洛必达一次得 $\lim\limits_{x\to 0}\dfrac{g'(x)}{2x}$，再由 $g'(0)=0$ 得极限为 0",
        ],
        "answer": 0,
        "explanation": (
            r"题目只给了「在 $x=0$ 处二阶可导」，并没有说 $g''(x)$ 在 0 的去心邻域存在或连续，"
            r"而洛必达要求分子分母在去心邻域可导到所需阶数，所以只能用泰勒："
            r"$g(x)=g(0)+g'(0)x+\dfrac{g''(0)}{2}x^2+o(x^2)=\dfrac{g''(0)}{2}x^2+o(x^2)$，"
            r"故 $\dfrac{g(x)}{x^2}\to\dfrac{g''(0)}{2}$。口诀「一点可导用泰勒，邻域可导才洛必达」。"
            r"反例：$f(x)=x^2\sin\frac1x$，$f'(0)=0$ 存在，但 $x\ne0$ 时 $f'(x)=2x\sin\frac1x-\cos\frac1x$，"
            r"$\lim\limits_{x\to0}f'(x)$ 不存在。"
        ),
        "traps": [r"连续两次洛必达，偷设了 $g''(x)$ 在 0 的邻域存在且连续——一点可导 $\ne$ 邻域可导。"],
    },
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    dry = args.dry_run

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    changed = 0
    missing = []
    written = {}          # qid -> 改写后的 JSON（dry-run 时也要拿它做复核）

    for qid, patch in REWRITE.items():
        row = conn.execute("SELECT content FROM questions WHERE id = ?", (qid,)).fetchone()
        if not row:
            missing.append(qid)
            continue
        ct = json.loads(row["content"])
        before = json.dumps(ct, ensure_ascii=False)
        for k, v in patch.items():
            ct[k] = v
        after = json.dumps(ct, ensure_ascii=False)
        if before == after:
            print(f"  [无变化] {qid}")
            continue
        written[qid] = after
        if not dry:
            conn.execute("UPDATE questions SET content = ? WHERE id = ?", (after, qid))
        n_dollar = after.count("$")
        print(f"  [改] {qid}  LaTeX 定界符 {n_dollar} 个：{patch.get('stem', '')[:52]}")
        changed += 1

    if missing:
        conn.close()
        print(f"\n❌ 以下 qid 在库里找不到，改写表需要更新：{missing}")
        sys.exit(1)

    if not dry:
        conn.commit()

    # 复核：这些卡不能再残留 Unicode 的积分/求和记号。
    # ⚠️ 必须复核「改写后的内容」，不能重新查库——dry-run 时库里还是旧值。
    bad = []
    for qid in REWRITE:
        blob = written.get(qid)
        if blob is None:
            continue
        for tok in ("∫", "∬", "Σ", "lim(x→"):
            if tok in blob:
                bad.append((qid, tok))
    conn.close()

    print(f"\n完成：改写 {changed} 张。" + ("（dry-run，未写库）" if dry else ""))
    if bad:
        print(f"⚠️ 仍残留 Unicode 记号：{bad}")
        sys.exit(1)
    print("✅ 复核通过：这些卡已无 ∫ / ∬ / Σ / lim(x→ 残留")


if __name__ == "__main__":
    main()

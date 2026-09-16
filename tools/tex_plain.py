#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
tex_plain.py — 把卡片内容里的 LaTeX 降级成纯文本（给不走 KaTeX 的出口用）

大盘闪卡区支持按需 KaTeX，但**周报（weekly_flashcard_report.py → 飞书）**、
PDF 导出这类出口是纯文本，直接塞 $\int_{-a}^{a}$ 会露出源码。这里把常见的
LaTeX 片段还原成 Unicode 数学符号，读起来跟手写笔记一样。

只处理数学卡会用到的那些命令；不认识的一律去掉反斜杠与花括号，保证不出现
`\dfrac` 这种乱码，也不至于把整句话吞掉。

用法:
    from tex_plain import tex_to_plain
    tex_to_plain(r"$\lim\limits_{x\to 0}\dfrac{\sin x}{x}=1$")
    # → 'lim_(x→0) (sin x)/(x) = 1'
"""

import re

# 命令 → Unicode（按字符串长度从长到短替换，避免 \le 抢在 \leq 前面）
SYMBOLS = [
    (r"\iint", "∬"), (r"\iiint", "∭"), (r"\oint", "∮"),
    (r"\int", "∫"), (r"\sum", "Σ"), (r"\prod", "Π"),
    (r"\infty", "∞"), (r"\to", "→"), (r"\rightarrow", "→"), (r"\Rightarrow", "⇒"),
    (r"\leq", "≤"), (r"\le", "≤"), (r"\geq", "≥"), (r"\ge", "≥"),
    (r"\neq", "≠"), (r"\ne", "≠"), (r"\approx", "≈"), (r"\pm", "±"),
    (r"\times", "×"), (r"\cdot", "·"), (r"\in", "∈"), (r"\subset", "⊂"),
    (r"\pi", "π"), (r"\sigma", "σ"), (r"\lambda", "λ"), (r"\mu", "μ"),
    (r"\theta", "θ"), (r"\alpha", "α"), (r"\beta", "β"), (r"\rho", "ρ"),
    (r"\varepsilon", "ε"), (r"\epsilon", "ε"), (r"\Delta", "Δ"), (r"\delta", "δ"),
    (r"\varphi", "φ"), (r"\Gamma", "Γ"), (r"\circ", "°"),
    (r"\lim", "lim"), (r"\ln", "ln"), (r"\log", "log"),
    (r"\sin", "sin"), (r"\cos", "cos"), (r"\tan", "tan"),
    (r"\arctan", "arctan"), (r"\arcsin", "arcsin"), (r"\exp", "exp"),
    (r"\max", "max"), (r"\min", "min"), (r"\sup", "sup"), (r"\inf", "inf"),
    (r"\quad", " "), (r"\qquad", "  "), (r"\,", ""), (r"\;", ""), (r"\!", ""),
    (r"\left", ""), (r"\right", ""), (r"\limits", ""), (r"\displaystyle", ""),
]

# 上下标、分式、根号先单独处理
_SUP = re.compile(r"\^\{([^{}]*)\}")
_SUB = re.compile(r"_\{([^{}]*)\}")
_SUP1 = re.compile(r"\^(\w)")
_SUB1 = re.compile(r"_(\w)")


def _frac(m):
    return "(" + m.group(1) + ")/(" + m.group(2) + ")"


def tex_to_plain(s):
    """把一串可能含 $...$ 的文本降级成纯文本。没有 $ 时原样返回。

    处理顺序很关键（踩过两次坑）：
      1. 先去掉 \\limits / \\left / \\right 这类"无内容命令"——
         否则 \\lim\\limits_{x\\to 0} 会先被当成下标处理，留给后面一句 lim_x→ 0。
      2. 再把 ^{...} / _{...} 的**花括号**化掉，
         否则 \\frac{1}{1+e^{kx}} 里嵌套的 {kx} 会让分式正则匹配不上，
         最后只剩一个光秃秃的 "frac"。
      3. 然后才是分式、根号、符号表。
    """
    if not s:
        return ""
    out = str(s).replace("$$", "$")

    # ① 无内容命令与间距命令
    for cmd in (r"\limits", r"\displaystyle", r"\left", r"\right",
                r"\bigl", r"\bigr", r"\Bigl", r"\Bigr", r"\quad", r"\qquad",
                r"\,", r"\;", r"\!", r"\ "):
        out = out.replace(cmd, "" if cmd not in (r"\quad", r"\qquad") else " ")

    # ② \lim_{...} 优先还原成 lim(...)，比 lim_x→0 好读
    out = re.sub(r"\\lim\s*_\{([^{}]*)\}", r"lim(\1)", out)

    # ③ 上下标去花括号（这一步必须在分式之前）
    for _ in range(4):
        new = _SUP.sub(r"^\1", out)
        new = _SUB.sub(r"_\1", new)
        if new == out:
            break
        out = new
    out = _SUP1.sub(r"^\1", out)
    out = _SUB1.sub(r"_\1", out)

    # ④ 根号要在分式**之前**：\frac{(-1)^n}{\sqrt{n}} 的分母里嵌了 {n}，
    #    不先把 \sqrt{n} 化成 √(n)，分式正则就匹配不上，最后只剩个 "dfrac"。
    out = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", out)
    for _ in range(6):
        new = re.sub(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}", _frac, out)
        if new == out:
            break
        out = new

    # ⑤ 符号表
    for cmd, rep in sorted(SYMBOLS, key=lambda kv: -len(kv[0])):
        out = out.replace(cmd, rep)

    # ⑥ 收尾：不留 $ 和反斜杠
    out = out.replace("$", "")
    out = re.sub(r"\\([A-Za-z]+)", r"\1", out)
    out = out.replace("{", "").replace("}", "")
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


if __name__ == "__main__":
    import io
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cases = [
        r"$\lim\limits_{x\to 0}\dfrac{\ln(1-2x)+2x f(x)}{x^2}=0$ 且 $f(0)=1$",
        r"$\int_{-a}^{a}\frac{1}{1+e^{kx}}\,dx = a$",
        r"$\iint_D (1-2x^2-y^2)\,d\sigma$（$D:\ 2x^2+y^2\le 1$）",
        r"判别 $\sum\limits_{n=1}^{\infty}\ln\!\left(1+\dfrac{(-1)^n}{\sqrt{n}}\right)$",
        r"$f^{(3)}(0)=2$，$\sigma^2$、$\lambda_1$",
        "普通中文，没有公式",
    ]
    for c in cases:
        print(f"  {c}\n    → {tex_to_plain(c)}")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_render.py —— 全量渲染体检：扫描所有笔记渲染后的 HTML，找出泄漏的原始 Markdown 语法。

用法：python tools/check_render.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import md2pdf  # noqa: E402

CHECKS = [
    ("表格分隔行泄漏", re.compile(r"\|--|--\|")),
    ("数学占位符泄漏", re.compile(r"@@MATHSLOT")),
    ("HTML注释泄漏", re.compile(r"&lt;!--|<!--(?!.*?-->)", re.S)),
    ("mark/span被转义", re.compile(r"&lt;(mark|span)")),
    ("双链语法泄漏", re.compile(r"\[\[[^\]]")),
    ("删除线语法泄漏", re.compile(r"~~[^~]+~~")),
    ("高亮语法泄漏", re.compile(r"==[^=<]+==")),
    ("管道段落疑吞表格", None),  # 特殊处理
]

PIPE_P_RE = re.compile(r"<(?:p|li)>([^<]*(?:\|[^<]*){4,})")


def strip_tags_keep_text(html: str) -> str:
    body = html.split("<article>", 1)[-1].split("</article>", 1)[0]
    # 剔除 KaTeX 数学容器（内部 LaTeX 语法如 \[ [A\mid B] 属正常内容）
    body = re.sub(r'<(?:div|span) class="math-tex">.*?</(?:div|span)>', "", body, flags=re.S)
    return body


def main():
    problems = 0
    for md_path in md2pdf.iter_notes():
        md_text = md_path.read_text(encoding="utf-8")
        html = md2pdf.md_to_html(md_path, md_text)
        body = strip_tags_keep_text(html)
        hits = []
        for name, pat in CHECKS:
            if pat is None:
                continue
            m = pat.search(body)
            if m:
                ctx = body[max(0, m.start() - 30):m.start() + 40].replace("\n", " ")
                hits.append(f"{name}: …{ctx}…")
        for m in PIPE_P_RE.finditer(body):
            snippet = m.group(1)[:60].replace("\n", " ")
            hits.append(f"管道段落疑吞表格: …{snippet}…")
        if hits:
            problems += 1
            print(f"\n⚠ {md_path.relative_to(md2pdf.ROOT)}")
            for h in hits[:6]:
                print(f"   - {h}")
    print(f"\n体检完成：{problems} 个文件存在可疑格式问题")
    return problems


if __name__ == "__main__":
    sys.exit(0 if main() == 0 else 1)

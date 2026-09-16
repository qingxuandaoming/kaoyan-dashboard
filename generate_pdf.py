#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
考研笔记 MD → PDF 生成器
================================
把每科的章节笔记（第X章_*.md）及参考文件（公式速查/错题归档/计算陷阱）
逐章转成排版精美的 PDF（模仿 QoderWork widget 风格），供平板阅读。

流程：pandoc (MD→HTML+MathML) → 内嵌 CSS → Chrome 无头打印 PDF

用法：
    python generate_pdf.py              # 生成全部科目全部章节
    python generate_pdf.py --subject 线代   # 只生成线代
    python generate_pdf.py --only 第3章    # 只生成文件名含"第3章"的
    python generate_pdf.py --list        # 只列出将处理的文件，不生成
"""

import subprocess
import sys
import argparse
import tempfile
import os
from pathlib import Path

# ---------- 路径配置 ----------
MATH_ROOT = Path(r"C:\Users\92534\Desktop\考研\Math")
CSS_PATH = Path(r"C:\Users\92534\Desktop\考研\src\note_style.css")
PDF_ROOT = MATH_ROOT / "pdf"

# 各科目目录（章节笔记所在）
SUBJECTS = {
    "高数": MATH_ROOT / "高数",
    "线代": MATH_ROOT / "线代",
    "概率论": MATH_ROOT / "概率论",
}

# 要转换的文件名模式
CHAPTER_PATTERN = "第*章*.md"
REFERENCE_FILES = ["公式速查.md", "错题归档.md", "计算陷阱.md"]

# Chrome / Edge 可执行文件候选
BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
{css}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def find_browser():
    """返回可用的 Chrome/Edge 可执行文件路径。"""
    for p in BROWSER_CANDIDATES:
        if os.path.exists(p):
            return p
    raise FileNotFoundError("未找到 Chrome 或 Edge，无法打印 PDF。")


def find_pandoc():
    """返回 pandoc 可执行文件路径。"""
    import shutil
    p = shutil.which("pandoc")
    if p:
        return p
    raise FileNotFoundError("未找到 pandoc，请先安装。")


def collect_files(subject_filter=None, only_filter=None):
    """收集所有待转换的 (subject, md_path) 列表。"""
    tasks = []
    for subject, folder in SUBJECTS.items():
        if subject_filter and subject != subject_filter:
            continue
        if not folder.exists():
            continue

        files = []
        # 章节笔记
        files.extend(sorted(folder.glob(CHAPTER_PATTERN)))
        # 参考文件
        for ref in REFERENCE_FILES:
            ref_path = folder / ref
            if ref_path.exists():
                files.append(ref_path)

        for f in files:
            if only_filter and only_filter not in f.name:
                continue
            tasks.append((subject, f))
    return tasks


def md_to_html_body(pandoc, md_path):
    """用 pandoc 把 MD 转成 HTML 片段（公式转 MathML）。"""
    result = subprocess.run(
        [
            pandoc,
            str(md_path),
            "-f",
            "markdown-yaml_metadata_block",
            "-t",
            "html5",
            "--mathml",
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pandoc 转换失败 {md_path}: {result.stderr.decode('utf-8', 'replace')}")
    return result.stdout.decode("utf-8")


def html_to_pdf(browser, html_path, pdf_path):
    """用 Chrome/Edge 无头模式把 HTML 打印成 PDF。"""
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    url = html_path.as_uri()
    cmd = [
        browser,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or not pdf_path.exists():
        raise RuntimeError(
            f"Chrome 打印 PDF 失败 {html_path}: {result.stderr.decode('utf-8', 'replace')}"
        )


def convert_one(pandoc, browser, css, subject, md_path):
    """转换单个 MD 文件为 PDF，返回输出路径。"""
    title = md_path.stem  # 如 "第3章_向量组与线性相关性"
    body = md_to_html_body(pandoc, md_path)
    html = HTML_TEMPLATE.format(title=title, css=css, body=body)

    pdf_path = PDF_ROOT / subject / (title + ".pdf")

    # 写入临时 HTML（与 PDF 同目录，避免中文临时路径问题）
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_html = pdf_path.with_suffix(".html")
    tmp_html.write_text(html, encoding="utf-8")
    try:
        html_to_pdf(browser, tmp_html, pdf_path)
    finally:
        # 清理中间 HTML
        try:
            tmp_html.unlink()
        except OSError:
            pass
    return pdf_path


def main():
    # Windows 控制台默认 GBK，无法输出 ✓/✗ 等符号，统一重配置为 UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="考研笔记 MD→PDF 生成器")
    parser.add_argument("--subject", help="只生成指定科目（高数/线代/概率论）")
    parser.add_argument("--only", help="只生成文件名包含该关键词的")
    parser.add_argument("--list", action="store_true", help="只列出文件，不生成")
    args = parser.parse_args()

    tasks = collect_files(args.subject, args.only)
    if not tasks:
        print("没有匹配的文件。")
        return

    if args.list:
        for subject, f in tasks:
            print(f"[{subject}] {f}")
        print(f"\n共 {len(tasks)} 个文件。")
        return

    pandoc = find_pandoc()
    browser = find_browser()
    css = CSS_PATH.read_text(encoding="utf-8")

    print(f"浏览器: {browser}")
    print(f"样式表: {CSS_PATH}")
    print(f"输出目录: {PDF_ROOT}\n")

    ok, fail = 0, 0
    for subject, f in tasks:
        try:
            out = convert_one(pandoc, browser, css, subject, f)
            print(f"  ✓ [{subject}] {f.name} → {out}")
            ok += 1
        except Exception as e:
            print(f"  ✗ [{subject}] {f.name} 失败: {e}", file=sys.stderr)
            fail += 1

    print(f"\n完成：成功 {ok}，失败 {fail}。输出目录: {PDF_ROOT}")


if __name__ == "__main__":
    main()

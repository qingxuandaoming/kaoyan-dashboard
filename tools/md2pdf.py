#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
md2pdf.py —— 考研笔记 Markdown → PDF 批量转换器（平板阅读版）

用法：
  python tools/md2pdf.py              # 增量：只转换有更新的笔记（默认，供笔记整理后调用）
  python tools/md2pdf.py --all        # 全量重建所有 PDF
  python tools/md2pdf.py 高数/第1章_函数极限与连续.md   # 只转指定文件（可多个）

输出：PDF/ 目录，镜像源文件目录结构，并自动添加页码。
渲染管线：markdown-it-py → HTML（KaTeX 本地渲染公式）→ Edge 无头打印 → pypdf 加页码。
"""

import argparse
import html as html_mod
import io
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from markdown_it import MarkdownIt

# ---------------- 路径配置 ----------------

TOOLS_DIR = Path(__file__).resolve().parent
# 笔记根目录：默认 = 上级目录（兼容旧布局）；真源在 src/tools/ 时由各科 shim 经 NOTES_ROOT 指定科目根
ROOT = Path(os.environ.get("NOTES_ROOT", os.getcwd())).resolve()
PDF_ROOT = ROOT / "PDF"
KATEX_DIST = TOOLS_DIR / "katex" / "dist"
STYLE_CSS = TOOLS_DIR / "pdf_style.css"

EXCLUDE_NAMES = {"AGENTS.md"}                 # 非学习内容，不转
# 排除编辑器/工具元数据目录：曾把 .qoder 的 repowiki 全量转成 PDF，白占 26MB
EXCLUDE_DIRS = {"tools", "PDF", ".git", "assets",
                ".qoder", ".obsidian", ".vscode", ".idea", "__pycache__"}

# 每科可用 ROOT/.pdfignore 追加排除项（每行一个目录名或文件名，# 开头为注释）
_ignore_file = ROOT / ".pdfignore"
if _ignore_file.exists():
    for _ln in _ignore_file.read_text(encoding="utf-8").splitlines():
        _ln = _ln.strip()
        if _ln and not _ln.startswith("#"):
            EXCLUDE_DIRS.add(_ln)
            EXCLUDE_NAMES.add(_ln)

# ---------------- 浏览器定位 ----------------

BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> Path:
    import os
    env = os.environ.get("MD2PDF_BROWSER")
    if env and Path(env).exists():
        return Path(env)
    for p in BROWSER_CANDIDATES:
        if Path(p).exists():
            return Path(p)
    sys.exit("[md2pdf] 找不到 Edge/Chrome 浏览器，请设置环境变量 MD2PDF_BROWSER 指向浏览器 exe。")


# ---------------- Markdown 预处理 ----------------

def segment_markdown(text: str):
    """把 md 文本切成 ('text'|'math'|'display') 段，跳过代码块/行内代码里的 $。"""
    segs, buf, i, n = [], [], 0, len(text)

    def flush():
        if buf:
            segs.append(("text", "".join(buf)))
            buf.clear()

    while i < n:
        # 围栏代码块 ``` 或 ~~~（行首）
        if text[i] in "`~" and (i == 0 or text[i - 1] == "\n"):
            m = re.match(r"(`{3,}|~{3,})[^\n]*\n", text[i:])
            if m:
                fence = m.group(1)
                close = re.search(rf"\n{fence[0]}{{{len(fence)},}}[ \t]*(?=\n|$)", text[i + m.end():])
                end = i + m.end() + (close.end() if close else n - (i + m.end()))
                buf.append(text[i:end])
                i = end
                continue
        # 行内代码 `...`
        if text[i] == "`":
            m = re.match(r"`+", text[i:])
            ticks = m.group(0)
            close = text.find(ticks, i + len(ticks))
            end = close + len(ticks) if close != -1 else i + len(ticks)
            buf.append(text[i:end])
            i = end
            continue
        # 转义 \$ 不算公式
        if text[i] == "\\" and i + 1 < n and text[i + 1] == "$":
            buf.append("$$")  # 还原成两个字符交还 markdown-it 处理转义
            buf[-1] = text[i:i + 2]
            i += 2
            continue
        # 块级公式 $$...$$
        if text.startswith("$$", i):
            close = text.find("$$", i + 2)
            if close != -1:
                flush()
                segs.append(("display", text[i + 2:close].strip()))
                i = close + 2
                continue
        # 行内公式 $...$（右边界不能跟数字，避免 $5 和 $10 误判）
        if text[i] == "$" and i + 1 < n and not text[i + 1].isspace():
            close = i + 1
            while True:
                close = text.find("$", close)
                if close == -1 or text[close - 1] != "\\":
                    break
                close += 1
            if close != -1 and "\n" not in text[i + 1:close] \
                    and not text[i + 1:close].endswith(" ") \
                    and (close + 1 >= n or not text[close + 1].isdigit()):
                flush()
                segs.append(("math", text[i + 1:close]))
                i = close + 1
                continue
        buf.append(text[i])
        i += 1
    flush()
    return segs


WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
HIGHLIGHT_RE = re.compile(r"==(?!=)(.+?)==(?!=)")
MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# 代码段/围栏代码块保护（单捕获组，供 re.split 使用）
CODE_SPLIT_RE = re.compile(r"(`{3,}[^\n]*\n.*?`{3,}[ \t]*(?=\n|$)|~{3,}[^\n]*\n.*?~{3,}[ \t]*(?=\n|$)|`[^`\n]+`)", re.S)
# CJK 加粗修复：**注意：**紧跟汉字 这类写法违反 CommonMark 侧翼规则，
# markdown-it 会原样输出 **。统一按 Obsidian/Typora 宽松语义转成 <strong>。
BOLD_RE = re.compile(r"(?<!\*)\*\*(?!\*)(?=\S)([^\n]*?\S)\*\*(?!\*)")


def fix_bold(md: str) -> str:
    """把 **加粗** 直接转成 <strong>（跳过代码段/代码块），解决 CJK 标点
    邻接导致的 CommonMark 侧翼规则失效（PDF 中残留字面 **）。"""
    parts = CODE_SPLIT_RE.split(md)
    for i in range(0, len(parts), 2):          # 偶数位为非代码文本
        if parts[i]:
            parts[i] = BOLD_RE.sub(r"<strong>\1</strong>", parts[i])
    return "".join(parts)


def rewrite_md_links(md: str) -> str:
    """跨文件 .md 链接 → 同名 .pdf 链接（PDF/ 目录镜像源结构，相对路径不变）。

    同文件 #锚点、http(s)、目录链接、非 md 文件链接保持原样。
    跨文件链接的 #锚点去掉（PDF 间无法定位标题，链到文件即可）。
    """
    def repl(m):
        text, target = m.group(1), m.group(2)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            return m.group(0)
        path = target.split("#", 1)[0]
        if not path.lower().endswith(".md"):
            return m.group(0)
        return f"[{text}]({path[:-3]}.pdf)"
    return MD_LINK_RE.sub(repl, md)


def preprocess(md_text: str) -> str:
    """数学公式 → 占位符；双链/高亮 → 内联 HTML；md 链接 → pdf 链接。返回 (md, math_slots)。"""
    segs = segment_markdown(md_text)
    math_slots = []
    out = []
    for kind, content in segs:
        if kind == "text":
            out.append(content)
        else:
            idx = len(math_slots)
            math_slots.append((kind, content))
            out.append(f"@@MATHSLOT{idx}@@")
    md = "".join(out)
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)          # 去掉 HTML 注释（如 review 元数据）
    md = fix_bold(md)                                      # CJK 加粗修复（**注意：**紧跟汉字等）
    md = fix_tables(md)                                      # 修复表格前后的空行缺失
    md = rewrite_md_links(md)                                # 跨文件 .md 链接 → .pdf
    md = WIKILINK_RE.sub(
        lambda m: f'<span class="wikilink">{html_mod.escape(m.group(2) or m.group(1))}</span>', md)
    md = HIGHLIGHT_RE.sub(r"<mark>\1</mark>", md)
    return md, math_slots


def _slugify(t: str) -> str:
    """锚点 slug 约定（与 tools/audit_notes.py、tools/fix_anchors.py 一致）。"""
    t = re.sub(r"@@MATHSLOT\d+@@", "", t)
    t = t.strip().lower()
    t = re.sub(r"[^\w一-鿿\- ]", "", t)
    return re.sub(r"-+", "-", t.replace(" ", "-"))


_HEADING_HTML_RE = re.compile(r"<h([1-6])>(.*?)</h\1>", re.S)


def add_heading_ids(rendered_html: str) -> str:
    """给 h1~h6 注入 id，让 [文字](#锚点) 在 PDF 内可跳转（重名标题加 -1/-2 后缀）。"""
    used = {}
    def repl(m):
        lvl, inner = m.group(1), m.group(2)
        text = html_mod.unescape(re.sub(r"<[^>]+>", "", inner))
        base = _slugify(text) or "section"
        n = used.get(base, 0)
        used[base] = n + 1
        slug = base if n == 0 else f"{base}-{n}"
        return f'<h{lvl} id="{slug}">{inner}</h{lvl}>'
    return _HEADING_HTML_RE.sub(repl, rendered_html)


def _strip_quote(line: str) -> str:
    """去掉行首引用块前缀 `> `。"""
    return re.sub(r"^\s*>\s?", "", line)


def _is_table_delim(core: str) -> bool:
    """是否为表格分隔行 |---|:---:|---:|（至少两列，避免把 --- 分隔线误判）"""
    cells = [c.strip() for c in core.strip().strip("|").split("|")]
    return len(cells) >= 2 and all(re.fullmatch(r":?-{2,}:?", c) for c in cells)


def fix_tables(md: str) -> str:
    """通用表格保护：表格前后缺空行时补空行。

    markdown-it（CommonMark 语义）中，紧跟在段落 / 列表项 / 引用行后面的表格
    会被当作惰性续行吞掉（Typora/Obsidian 则会宽松地渲染成表格）。
    规则：
    1. 表头前一行是引用行/列表标记行/表格行 → 在表头前补空行
    2. 表头前一行是被列表/引用惰性吞并的普通行（如 `**标签**：`），且它前面
       还有非空行 → 把空行补在"标签行"之前，让标签和表格一起脱离列表/引用
    3. 表格体后直接跟非表格行（无空行）→ 表体后补空行，防止被吞为单行单元格
    围栏代码块内部不处理。
    """
    lines = md.split("\n")
    out, in_table, in_fence, fence_ch = [], False, False, ""
    list_marker = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
    for i, line in enumerate(lines):
        fm = re.match(r"^(`{3,}|~{3,})", line)
        if fm and not in_fence:
            in_fence, fence_ch = True, fm.group(1)[0]
        elif fm and in_fence and fm.group(1)[0] == fence_ch:
            in_fence = False
        if in_fence:
            out.append(line)
            continue

        core = _strip_quote(line)
        is_header = (core.count("|") >= 2 and i + 1 < len(lines)
                     and _is_table_delim(_strip_quote(lines[i + 1])))
        if is_header:
            if out and out[-1].strip():
                prev = out[-1]
                if ("|" not in prev
                        and not prev.lstrip().startswith(">")
                        and not list_marker.match(prev)
                        and len(out) >= 2 and out[-2].strip()):
                    out.insert(len(out) - 1, "")   # 场景 2：空行补在标签行之前
                else:
                    out.append("")                 # 场景 1
            in_table = True
        elif in_table and "|" not in core:
            if line.strip():
                out.append("")                     # 场景 3
            in_table = False
        out.append(line)
    return "\n".join(out)


def restore_math(rendered_html: str, math_slots) -> str:
    """把占位符替换成 KaTeX auto-render 能识别的 \\( \\) / \\[ \\] 定界结构。"""
    def repl(m):
        kind, tex = math_slots[int(m.group(1))]
        esc = html_mod.escape(tex, quote=False)
        if kind == "display":
            return f'<div class="math-tex">\\[{esc}\\]</div>'
        return f'<span class="math-tex">\\({esc}\\)</span>'
    return re.sub(r"@@MATHSLOT(\d+)@@", repl, rendered_html)


# ---------------- HTML 组装 ----------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<link rel="stylesheet" href="{katex_css}">
<link rel="stylesheet" href="{style_css}">
<script defer src="{katex_js}"></script>
<script defer src="{auto_render_js}"></script>
<script>
document.addEventListener("DOMContentLoaded", function () {{
  renderMathInElement(document.body, {{
    delimiters: [
      {{left: "\\\\[", right: "\\\\]", display: true}},
      {{left: "\\\\(", right: "\\\\)", display: false}}
    ],
    throwOnError: false
  }});
  // 杜绝横向裁剪：PDF 里无法横向滚动，凡换行后仍溢出容器的块级内容
  // （超宽公式/表格/代码块等）一律等比缩放到容器内
  document.querySelectorAll("pre, table, .katex-display").forEach(function (el) {{
    var w = el.scrollWidth, cw = el.clientWidth;
    if (w > cw && w > 0) el.style.zoom = Math.max(cw / w, 0.45);
  }});
  document.querySelectorAll(".katex").forEach(function (el) {{
    if (el.closest(".katex-display")) return;
    var r = el.getBoundingClientRect();
    var p = el.parentElement.getBoundingClientRect();
    if (r.width > p.width && r.width > 0) {{
      el.style.display = "inline-block";
      el.style.zoom = Math.max(p.width / r.width, 0.45);
    }}
  }});
}});
</script>
</head>
<body>
<article>{body}</article>
<div class="pdf-meta">{title} · 生成于 {date}</div>
</body>
</html>
"""


def md_to_html(md_path: Path, md_text: str) -> str:
    md_body, math_slots = preprocess(md_text)
    renderer = MarkdownIt("default", {"html": True}).enable("table")
    body = restore_math(add_heading_ids(renderer.render(md_body)), math_slots)
    from datetime import datetime
    return HTML_TEMPLATE.format(
        title=html_mod.escape(md_path.stem),
        katex_css=(KATEX_DIST / "katex.min.css").as_uri(),
        katex_js=(KATEX_DIST / "katex.min.js").as_uri(),
        auto_render_js=(KATEX_DIST / "contrib" / "auto-render.min.js").as_uri(),
        style_css=STYLE_CSS.as_uri(),
        body=body,
        date=datetime.now().strftime("%Y-%m-%d"),
    )


# ---------------- PDF 生成 ----------------

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")
_KEEP_CHARS_RE = re.compile(r"[A-Za-z0-9一-鿿]+")


def collect_headings(md_text: str):
    """从 md 提取 h1~h4 标题（跳过代码块），返回 [(level, 纯文本标题)]。"""
    heads, in_fence, fence_ch = [], False, ""
    for line in md_text.split("\n"):
        fm = re.match(r"^(`{3,}|~{3,})", line)
        if fm:
            ch = fm.group(1)[0]
            if not in_fence:
                in_fence, fence_ch = True, ch
            elif ch == fence_ch:
                in_fence = False
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            t = re.sub(r"\$[^$]*\$", "", m.group(2))              # 去掉行内公式
            t = WIKILINK_RE.sub(lambda x: x.group(2) or x.group(1), t)
            t = re.sub(r"[*=`~]|==", "", t).strip()
            if t:
                heads.append((len(m.group(1)), t))
    return heads


def _norm(s: str) -> str:
    """只保留字母/数字/汉字，避免 emoji、破折号、空格造成的匹配失败。"""
    return "".join(_KEEP_CHARS_RE.findall(s))


def print_pdf(browser: Path, html_path: Path, pdf_path: Path, headings=None):
    tmp = pdf_path.with_suffix(".raw.pdf")
    cmd = [
        str(browser),
        "--headless=new", "--disable-gpu", "--mute-audio",
        "--allow-file-access-from-files",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=20000",
        "--no-pdf-header-footer", "--print-to-pdf-no-header",
        f"--print-to-pdf={tmp}",
        html_path.as_uri(),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    if not tmp.exists():
        raise RuntimeError(f"浏览器打印失败: {r.stderr[-500:] if r.stderr else '无输出'}")
    # try/finally：postprocess_pdf 一旦抛异常，若不做兜底就会把 .raw.pdf 永久留在
    # 输出目录（每份数 MB）。历史上正是这样积出 11MB 的 .raw.pdf 残留。
    try:
        postprocess_pdf(tmp, pdf_path, headings or [])
    finally:
        if tmp.exists():
            tmp.unlink()


def _rewrite_link_annots(page, out_pdf: Path):
    """把指向笔记区 .pdf 的 file:// 绝对链接改写为相对路径（平板阅读时可跨 PDF 跳转）。

    Chromium 打印时会把相对 href 解析成 file:/// 绝对 URI，搬到平板就失效；
    PDF/ 目录镜像源结构，因此改回相对路径后，阅读器以当前 PDF 所在目录为基准解析。
    """
    from urllib.parse import unquote, quote
    annots = page.get("/Annots")
    if not annots:
        return
    for ref in annots:
        annot = ref.get_object()
        if annot.get("/Subtype") != "/Link":
            continue
        action = annot.get("/A")
        if action is None:
            continue
        action = action.get_object()
        if action.get("/S") != "/URI":
            continue
        uri = str(action["/URI"])
        if not uri.startswith("file:///"):
            continue
        local = Path(unquote(uri[8:]))            # file:///C:/... → C:/...
        if local.suffix.lower() != ".pdf":
            continue
        try:
            rel = local.relative_to(ROOT)
        except ValueError:
            continue
        target_pdf = PDF_ROOT / rel
        rel_path = Path(__import__("os").path.relpath(target_pdf, out_pdf.parent))
        action[pypdf_name("/URI")] = pypdf_text(
            quote(str(rel_path).replace("\\", "/"), safe="/-_."))


def pypdf_name(s):
    from pypdf.generic import NameObject
    return NameObject(s)


def pypdf_text(s):
    from pypdf.generic import TextStringObject
    return TextStringObject(s)


def postprocess_pdf(raw_pdf: Path, out_pdf: Path, headings):
    """一次写出：加页码 + 跨 PDF 相对链接改写 + 按标题层级自动生成书签目录。"""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas as rl_canvas

    reader = PdfReader(str(raw_pdf))
    text_reader = PdfReader(str(raw_pdf))   # 独立实例提取文本（merge 会影响提取）
    writer = PdfWriter()
    total = len(reader.pages)
    w_pt, h_pt = float(reader.pages[0].mediabox.width), float(reader.pages[0].mediabox.height)
    pages_norm = []
    for i, page in enumerate(reader.pages, 1):
        _rewrite_link_annots(page, out_pdf)
        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(w_pt, h_pt))
        c.setFont("Helvetica", 8.5)
        c.setFillGray(0.45)
        # 页码基线距纸底约 5mm：落在 @page 的 11mm 底边距内，与正文保持约 4mm 安全间距
        c.drawCentredString(w_pt / 2, h_pt * 0.017, f"— {i} / {total} —")
        c.save()
        buf.seek(0)
        page.merge_page(PdfReader(buf).pages[0])
        writer.add_page(page)
    for tp in text_reader.pages:
        try:
            pages_norm.append(_norm(tp.extract_text() or ""))
        except Exception:  # noqa: BLE001
            pages_norm.append("")

    # 标题 → 页码（标题按文档顺序出现，页码指针单调前移）
    stack, page_ptr, missed = [], 0, 0
    for level, title in headings:
        key = _norm(title)
        if not key:
            continue
        page_idx = None
        for p in range(page_ptr, total):
            if key in pages_norm[p]:
                page_idx = p
                break
        if page_idx is None:          # 标题含公式被提取破坏等，挂到当前页
            page_idx = min(page_ptr, total - 1)
            missed += 1
        else:
            page_ptr = page_idx
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1] if stack else None
        ref = writer.add_outline_item(title, page_idx, parent=parent)
        stack.append((level, ref))
    if missed:
        print(f"  [书签] {missed} 个标题页码为近似定位")

    with open(out_pdf, "wb") as f:
        writer.write(f)


# ---------------- 批量调度 ----------------

def iter_notes():
    for p in sorted(ROOT.rglob("*.md")):
        rel = p.relative_to(ROOT)
        if p.name in EXCLUDE_NAMES or any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        yield p


def convert(md_path: Path, browser: Path, force=False) -> str:
    pdf_path = PDF_ROOT / md_path.relative_to(ROOT).with_suffix(".pdf")
    if not force and pdf_path.exists() and pdf_path.stat().st_mtime >= md_path.stat().st_mtime:
        return "skip"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_html = md_path.parent / f".{md_path.stem}.__pdfgen__.html"
    try:
        md_text = md_path.read_text(encoding="utf-8")
        tmp_html.write_text(md_to_html(md_path, md_text), encoding="utf-8")
        print_pdf(browser, tmp_html, pdf_path, headings=collect_headings(md_text))
        return "ok"
    finally:
        tmp_html.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(description="考研笔记 Markdown → PDF 批量转换")
    ap.add_argument("files", nargs="*", help="只转换指定 md（相对/绝对路径均可）")
    ap.add_argument("--all", action="store_true", help="全量重建（忽略时间戳）")
    ap.add_argument("--jobs", type=int, default=3, help="并发转换进程数（默认 3）")
    args = ap.parse_args()

    browser = find_browser()
    if args.files:
        targets = [(ROOT / f).resolve() if not Path(f).is_absolute() else Path(f) for f in args.files]
        force = True
    else:
        targets = list(iter_notes())
        force = args.all

    ok = skip = fail = 0
    lock = __import__("threading").Lock()

    def work(md):
        nonlocal ok, skip, fail
        if not md.exists():
            with lock:
                print(f"[缺失] {md}")
                fail += 1
            return
        try:
            status = convert(md, browser, force)
            with lock:
                if status == "ok":
                    ok += 1
                    print(f"[PDF] {(PDF_ROOT / md.relative_to(ROOT).with_suffix('.pdf'))}")
                else:
                    skip += 1
        except Exception as e:  # noqa: BLE001
            with lock:
                fail += 1
                print(f"[失败] {md.name}: {e}")

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        list(pool.map(work, targets))

    print(f"\n完成：转换 {ok}，跳过（已最新）{skip}，失败 {fail}")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()

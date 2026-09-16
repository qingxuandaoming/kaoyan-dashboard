#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_notes.py —— 笔记源文件体检：排查 Markdown 本身的格式与链接问题。

报告分两级：
- 真问题（需处理）：断链 / 无效锚点 / 锚点指向重复标题 / 表格列数不一致 /
  $$ 未闭合 / 行内 $ 配对失败（开$后空格、闭$前空格、缺闭$） / 多 h1 / 文件名疑似副本。
- 风格提示（可忽略）：未被锚点链接引用的重复标题、标题层级跳级——
  属于模板约定（如复习导航的固定小节结构），不影响 Obsidian 跳转与 PDF 渲染。

用法：python tools/audit_notes.py [子目录名 ...]
    不带参数：体检全部笔记目录（默认排除 .qoder/.obsidian/.uploads 等生成目录）
    带子目录：只体检指定目录，如 python tools/audit_notes.py 408 Math
"""
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import md2pdf  # noqa: E402

IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


def slugify(t: str) -> str:
    """近似 GitHub/Obsidian 锚点规则（与 fix_anchors.py 约定一致：连续连字符合并）。
    首尾连字符规范化去除（emoji 开头的标题不会产生前导 -）。"""
    t = t.strip().lower()
    t = re.sub(r"[^\w一-鿿\- ]", "", t)
    return re.sub(r"-+", "-", t.replace(" ", "-")).strip("-")


def split_code(lines):
    """逐行标注是否在围栏代码块内。"""
    in_fence, fence_ch, flags = False, "", []
    for line in lines:
        fm = FENCE_RE.match(line)
        if fm and not in_fence:
            in_fence, fence_ch = True, fm.group(1)[0]
            flags.append(True)
            continue
        if fm and in_fence and fm.group(1)[0] == fence_ch:
            in_fence = False
            flags.append(True)
            continue
        flags.append(in_fence)
    return flags


def check_inline_math(lines, in_code):
    """逐行模拟 markdown-it 数学插件的 $ 配对，返回问题行号列表。

    插件规则：开 $ 后紧跟空格、或最近的闭 $ 前有空格，配对即失败，
    开 $ 降为字面文本（PDF 中整段公式掉成纯文本的渲染失败签名）。"""
    bad = []
    for i, s in enumerate(lines):
        if in_code[i] or "$$" in s:
            continue
        pos = [j for j, ch in enumerate(s) if ch == "$"]
        keep, k = [], 0
        while k < len(pos):  # 剥离 $$ 显示数学的相邻美元符
            if k + 1 < len(pos) and pos[k + 1] == pos[k] + 1:
                k += 2
                continue
            keep.append(pos[k])
            k += 1
        idx = 0
        while idx < len(keep):
            p = keep[idx]
            nxt = s[p + 1] if p + 1 < len(s) else ""
            if nxt == " ":
                bad.append(i + 1)  # 开$后空格
                idx += 1
                continue
            if idx + 1 >= len(keep):
                bad.append(i + 1)  # 缺闭$
                idx += 1
                continue
            q = keep[idx + 1]
            if s[q - 1] != " ":
                idx += 2  # 合法配对
            else:
                bad.append(i + 1)  # 闭$前空格，开$渲染失败
                idx += 1
    return bad


def audit_file(path: Path):
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    in_code = split_code(lines)
    issues, styles, infos = [], [], []

    # ---- 标题：层级 / 多 h1 / 重复 ----
    heads = [(len(m.group(1)), m.group(2).strip(), i + 1)
             for i, line in enumerate(lines)
             if not in_code[i] and (m := HEADING_RE.match(line))]
    h1s = [h for h in heads if h[0] == 1]
    if len(h1s) > 1:
        issues.append(f"多个 h1 标题（{len(h1s)} 个）：L{h1s[1][2]}《{h1s[1][1][:20]}》等")
    prev = 0
    for lv, t, ln in heads:
        if prev and lv > prev + 1:
            styles.append(f"标题层级跳级：L{ln} h{prev}→h{lv}《{t[:20]}》")
        prev = lv
    seen = {}
    dup_records = []
    for lv, t, ln in heads:
        key = re.sub(r"\s+", "", t)
        if key in seen:
            dup_records.append((seen[key], ln, t))
        else:
            seen[key] = ln
    for first_ln, ln, t in dup_records:
        styles.append(f"重复标题：L{first_ln} 与 L{ln}《{t[:20]}》（未被锚点链接，暂无冲突）")
    # 重复标题的锚点集合：文内锚点链接若指向它们会跳到第一处——在链接检查中升级为真问题
    dup_slugs = {slugify(t) for _, _, t in dup_records}
    dup_texts = {re.sub(r"[\s\-]", "", t) for _, _, t in dup_records}
    anchors = {slugify(t) for _, t, _ in heads}
    anchors_text = {re.sub(r"\s+", "", t) for _, t, _ in heads}

    # ---- 链接与图片 ----
    for i, line in enumerate(lines):
        if in_code[i]:
            continue
        for m in IMG_RE.finditer(line):
            target = m.group(1).split("#")[0].split("?")[0]
            if target.startswith(("http://", "https://", "data:")):
                continue
            if not (path.parent / urllib.parse.unquote(target)).exists():
                issues.append(f"图片不存在：L{i + 1} `{target}`")
        for m in LINK_RE.finditer(line):
            raw = m.group(1)
            target, _, anchor = raw.partition("#")
            if raw.startswith(("http://", "https://", "mailto:")):
                continue
            if target:
                if not (path.parent / urllib.parse.unquote(target)).exists():
                    issues.append(f"链接文件不存在：L{i + 1} `{raw[:40]}`")
            elif anchor:
                a = anchor.lower()
                if a not in anchors and re.sub(r"[\s\-]", "", anchor) not in {
                        re.sub(r"[\s\-]", "", x) for x in anchors_text}:
                    issues.append(f"锚点无效：L{i + 1} `[...](#{anchor[:30]})`")
                elif a in dup_slugs or re.sub(r"[\s\-]", "", anchor) in dup_texts:
                    issues.append(
                        f"锚点指向重复标题：L{i + 1} `[...](#{anchor[:30]})`（会跳到第一处）")

    # ---- 表格列数一致性（与 md2pdf 渲染管线一致）----
    # 管线顺序：$...$ 先被替换成 @@MATHSLOT@@ 占位符（其中竖线不参与分列），
    # 再由 markdown-it 解析表格（`\|` 是字面量、代码span内竖线照常分列）。
    def _raw(line: str) -> str:
        core = md2pdf._strip_quote(line)
        # 1) 代码span里的 $ 先掩码，避免误当公式定界符（但保留其竖线——会分列）
        core = re.sub(r"`[^`]*`",
                      lambda m: m.group(0).replace("$", "\x01"), core)
        # 2) 转义竖线 \| 是字面量，掩码掉
        core = core.replace("\\|", "\x00")
        # 3) 行内公式整体剥离（管线中它们已被占位符替换）
        core = re.sub(r"\$[^$]*\$", "", core)
        return core

    def _ncols(line: str) -> int:
        return len([c for c in _raw(line).strip().strip("|").split("|")])

    i = 0
    while i < len(lines):
        if not in_code[i] and _raw(lines[i]).count("|") >= 2 and i + 1 < len(lines) \
                and md2pdf._is_table_delim(md2pdf._strip_quote(lines[i + 1])):
            ncol = _ncols(lines[i])
            j = i + 2
            while j < len(lines) and "|" in md2pdf._strip_quote(lines[j]) \
                    and lines[j].strip():
                nrow = _ncols(lines[j])
                if nrow != ncol:
                    issues.append(
                        f"表格列数不一致：L{j + 1} 表头 {ncol} 列，本行 {nrow} 列（渲染会错位）")
                j += 1
            i = j
        else:
            i += 1

    # ---- $$ 配对 ----
    n_dd = len(re.findall(r"(?<!\\)\$\$", text))
    if n_dd % 2:
        issues.append(f"$$ 数量为奇数（{n_dd} 个），存在未闭合的公式块")

    # ---- 行内 $ 配对（渲染失败签名）----
    for ln in check_inline_math(lines, in_code):
        issues.append(f"行内公式 $ 配对失败：L{ln}（开$后空格 / 闭$前空格 / 缺闭$，渲染掉成纯文本）")

    # ---- 占位与待办 ----
    n_todo = text.count("（后续补充）") + text.count("(后续补充)")
    if n_todo:
        infos.append(f"待补充占位 ×{n_todo}")

    return issues, styles, infos


# 默认排除的目录：工具输出 / PDF 产物 / 隐藏生成目录（.qoder repowiki 等）/ 依赖包
EXCLUDE_DIRS = {"tools", "PDF", ".git", "assets", "node_modules", "_backups"}


def iter_all_notes(subdirs=None):
    """体检覆盖笔记 .md 源文件。默认排除生成/工具目录与一切隐藏目录
    （.qoder repowiki 自动文档的 file:// 链接不属于笔记问题）。
    传入 subdirs 时只扫这些顶级目录（如 ['408', 'Math']）。"""
    root = md2pdf.ROOT
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root)
        if any(part in EXCLUDE_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if subdirs and rel.parts[0] not in subdirs:
            continue
        yield p


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    subdirs = sys.argv[1:] or None
    total_issues = 0
    style_stats = []  # (rel, 重复标题数, 层级跳级数)
    all_infos = []
    for p in iter_all_notes(subdirs):
        issues, styles, infos = audit_file(p)
        rel = p.relative_to(md2pdf.ROOT)
        # 命名规范检查
        if " (1)" in p.stem or "（1）" in p.stem:
            issues.insert(0, "文件名疑似重复下载副本（含 “(1)”）")
        if issues:
            total_issues += len(issues)
            print(f"\n⚠ {rel}")
            for it in issues:
                print(f"   - {it}")
        if styles:
            n_dup = sum(1 for s in styles if s.startswith("重复标题"))
            n_skip = sum(1 for s in styles if s.startswith("标题层级跳级"))
            style_stats.append((rel, n_dup, n_skip))
        if infos:
            all_infos.append(f"{rel}：{'，'.join(infos)}")

    n_dup_all = sum(d for _, d, _ in style_stats)
    n_skip_all = sum(k for _, _, k in style_stats)
    print("\n" + "=" * 50)
    print(f"真问题共 {total_issues} 处（断链 / 锚点 / 表格 / 公式 / 冲突链接）")
    print(f"风格提示共 {n_dup_all + n_skip_all} 处（重复标题 {n_dup_all} 处，"
          f"层级跳级 {n_skip_all} 处）——不影响跳转与渲染，按模板约定保留")
    if style_stats:
        print("\n—— 风格提示分布（信息项，可忽略）——")
        for rel, d, k in style_stats:
            parts = []
            if d:
                parts.append(f"重复标题×{d}")
            if k:
                parts.append(f"层级跳级×{k}")
            print(f"  {rel}：{'，'.join(parts)}")
    print("\n—— 待补充占位清单（信息项，非错误）——")
    for s in all_infos:
        print("  " + s)
    return total_issues


if __name__ == "__main__":
    main()

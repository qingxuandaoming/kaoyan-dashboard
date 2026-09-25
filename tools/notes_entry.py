#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""notes_entry.py —— 笔记"整理入账"一条链自动化（中控 + 计数 + JSON + 自检 + PDF）

背景：过去这一步被写成散落四处的散文规范（中控追加一行 / JSON 追加元数据 /
章节计数 / 顶部总数 / PDF 再生），结果是每次都由模型现写一个 _tmp_*.py 重做一遍，
且计数长期漂移（Math 曾同时存在 131 / 132 / 133 三个互不相等的总数）。
本脚本把这五步固化，模型只需提供"这条记录是什么"。

用法
----
1) 入账（推荐用 stdin 传 JSON，避免 shell 引号地狱）：

   python src/tools/notes_entry.py --root Math --json - <<'EOF'
   {
     "subject": "高数",
     "chapter": "第9讲",
     "title": "半区间偶次三角积分对称配对法",
     "level": "L2",
     "summary": "一句话摘要（可省略）",
     "tags": ["定积分", "Wallis公式"],
     "source": "用户提问（2026-09-12）",
     "links": [{"text": "公式速查.md > 轮换对称性", "path": "./高数/公式速查.md"}],
     "hub": {"text": "第9讲_一元函数积分学的计算.md > 3.9", "path": "./第9讲_一元函数积分学的计算.md"}
   }
   EOF

   ⚠️ `chapter` 的单位**按科目不同**（2026-09-22 起）：数学高数已按张宇《高数18讲》拆分
   → 填「第N讲」（1–18）；线代/概率论/408/政治/英语仍是教材章 → 填「第N章」。
   中控必须已有同名分组段（高数 18 个讲段是**常驻**的，0 条也留着），否则会退回
   「索引区末尾追加」并报 WARN。节号沿用旧的章内编号（3.9、2.7…），迁移时刻意没重排。

2) 只体检（等价于过去手写的"各章之和 == 总数 == 实际行数"自检）：

   python src/tools/notes_entry.py --root Math --check

3) 入账后顺手重建 PDF 与索引：

   ... --json - --finalize

约定
----
- id 自动续号（前缀取自既有条目的最大编号 +1），无需模型计算
- 计数结构"存在才维护"：没有 `（N 条）` 结构的库（如 Politics 部分科目）自动跳过
- 写入一律保持 LF、无 BOM，并在结尾自检；任何一项不通过则回滚并报错
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
from datetime import date

if sys.platform == "win32":
    # stdin 也必须显式按 UTF-8 解：Windows 默认用本地编码（cp936）读管道，
    # 会把 `--json -` 传来的 UTF-8 JSON 解成乱码，导致 subject 匹配失败。
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")

KAOYAN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 2026-09-25 起代码根（KAOYAN_ROOT=项目根，src/tools 的上两级）与笔记库根分离：
# --root Math 这类科目相对路径按笔记库根解析；md2pdf/build_index 仍按代码根找。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths as _paths_mod   # 路径单一事实源
NOTES_ROOT = _paths_mod.NOTES_ROOT

# 每个库的中控布局：hub 为独立文件，或 inline（索引写在科目文件内）
LAYOUTS = {
    "Math":     {"hub": "subdir", "note": "独立中控文件 `{sub}/{sub} notes.md`"},
    "408":      {"hub": "subdir", "note": "独立中控文件 `{sub}/{sub} notes.md`"},
    "Politics": {"hub": "inline", "note": "索引内联在 `{sub}.md` 末尾"},
}

TOTAL_RE = re.compile(r"(>\s*共\s*)(\d+)(\s*条已整理记录)")
SECTION_RE = re.compile(r"^#{2,4}\s*(.+?)（(\d+)\s*条）\s*$")
DATE_RANGE_RE = re.compile(r"（(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})）")


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write(path, text):
    """保持 LF、无 BOM。"""
    text = text.replace("\r\n", "\n")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def hub_path(root_abs, layout, subject):
    if layout == "inline":
        return os.path.join(root_abs, f"{subject}.md")
    d = root_abs if layout == "top" else os.path.join(root_abs, subject)
    exact = os.path.join(d, f"{subject} notes.md")
    if os.path.exists(exact):
        return exact
    # 中控文件名不总是等于科目名（如 Math 的「概率论」→「概率 notes.md」），
    # 目录内唯一一个 *notes.md 即认作中控，避免改名后规则失效。
    if os.path.isdir(d):
        cands = [f for f in os.listdir(d) if f.endswith("notes.md")]
        if len(cands) == 1:
            return os.path.join(d, cands[0])
    return exact


def next_id(entries, subject_key):
    prefix = None
    best = 0
    for e in entries:
        m = re.match(r"^(.*?)-(\d+)$", e.get("id", ""))
        if m:
            prefix, n = m.group(1), int(m.group(2))
            best = max(best, n)
    if prefix is None:
        prefix = subject_key
    return f"{prefix}-{best + 1:03d}"


def find_section(text, chapter):
    """定位 `### {chapter}（N 条）` 段；返回 (标题行起, 段内末尾插入位置, N) 或 None。"""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        m = SECTION_RE.match(ln)
        if not m:
            continue
        name = m.group(1).strip()
        if name == chapter or name.startswith(chapter) or chapter.startswith(name):
            j = i + 1
            while j < len(lines) and not re.match(r"^#{1,4} ", lines[j]):
                j += 1
            k = j - 1
            while k > i and not lines[k].strip():
                k -= 1
            return i, k + 1, int(m.group(2))
    return None


def bump_hub(root_abs, layout, subject, chapter, hub_line, day):
    """向中控追加一行，并同步"该章计数"与"顶部总数"。返回报告字符串列表。"""
    hp = hub_path(root_abs, layout, subject)
    if not os.path.exists(hp):
        return [f"  [WARN] 未找到中控文件 {hp}，跳过中控写入"]
    text = read(hp)
    notes = []

    sec = find_section(text, chapter)
    if sec:
        _, insert_at, n = sec
        lines = text.split("\n")
        lines.insert(insert_at, hub_line)
        # 段计数 +1
        for i in range(insert_at, -1, -1):
            m = SECTION_RE.match(lines[i])
            if m:
                lines[i] = lines[i].replace(f"（{m.group(2)} 条）", f"（{int(m.group(2)) + 1} 条）")
                break
        text = "\n".join(lines)
        notes.append(f"  中控：`{os.path.basename(hp)}` > {chapter}（{n} → {n + 1} 条）")
    else:
        # 无该章计数结构：只追加一行到索引区末尾
        anchor = "## 整理记录索引"
        idx = text.find(anchor)
        if idx < 0:
            notes.append(f"  [WARN] `{os.path.basename(hp)}` 无章节计数结构也无索引区，未写入中控")
            return notes
        end = len(text)
        m = re.search(r"\n#{1,2} ", text[idx + len(anchor):])
        if m:
            end = idx + len(anchor) + m.start()
        head, tail = text[:end].rstrip("\n"), text[end:]
        text = head + "\n" + hub_line + "\n" + tail
        notes.append(f"  中控：`{os.path.basename(hp)}` 索引区追加一行（该库无计数结构）")

    # 顶部总数 + 日期区间
    mt = TOTAL_RE.search(text)
    if mt:
        old = int(mt.group(2))
        text = TOTAL_RE.sub(lambda m: f"{m.group(1)}{old + 1}{m.group(3)}", text, count=1)
        dr = DATE_RANGE_RE.search(text)
        if dr and day > dr.group(2):
            text = text[:dr.start(2)] + day + text[dr.end(2):]
        notes.append(f"  总数：{old} → {old + 1} 条")

    write(hp, text)
    return notes


def selfcheck(root_abs, layout, hubmap):
    """各章声明之和 == 顶部总数 == 实际条目行数。返回 (ok, 报告行, 问题数)。"""
    problems = []
    report = []
    for subject, hp in hubmap.items():
        if not os.path.exists(hp):
            continue
        text = read(hp)
        lines = text.split("\n")
        declared = 0
        for ln in lines:
            m = SECTION_RE.match(ln)
            if m:
                declared += int(m.group(2))
        actual = sum(1 for l in lines if re.match(r"^- \*\*\d{4}-\d{2}-\d{2}\*\*", l))
        mt = TOTAL_RE.search(text)
        total = int(mt.group(2)) if mt else None
        if total is None and declared == 0:
            continue  # 该库/该科目无计数结构
        ok = (declared == actual) and (total is None or total == actual)
        flag = "OK  " if ok else "漂移"
        report.append(f"  [{flag}] {os.path.basename(hp):22} 顶部总数={total}  各章之和={declared}  实际行数={actual}")
        if not ok:
            problems.append(os.path.basename(hp))
    return (not problems), report, problems


def main():
    ap = argparse.ArgumentParser(description="笔记整理入账：中控 + 计数 + JSON + 自检")
    ap.add_argument("--root", required=True,
                    help="库根目录名或路径，如 Math / 408 / Politics")
    ap.add_argument("--json", dest="payload",
                    help="条目 JSON：内联字符串、@文件路径，或 - 读 stdin")
    ap.add_argument("--layout", choices=["top", "subdir", "inline"],
                    help="强制中控布局（默认按库根目录名自动判定）")
    ap.add_argument("--check", action="store_true", help="只体检计数一致性")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要写入的内容")
    ap.add_argument("--finalize", action="store_true",
                    help="入账后重建 PDF 与总索引")
    args = ap.parse_args()

    root_abs = args.root if os.path.isabs(args.root) else os.path.join(NOTES_ROOT, args.root)
    root_name = os.path.basename(root_abs.rstrip("\\/"))
    if not os.path.isdir(root_abs):
        sys.exit(f"[notes_entry] 库根目录不存在：{root_abs}")
    layout = args.layout or LAYOUTS.get(root_name, {}).get("hub", "top")
    json_path = os.path.join(root_abs, "notes_index.json")
    if not os.path.exists(json_path):
        sys.exit(f"[notes_entry] 未找到 {json_path}")
    data = json.loads(read(json_path))
    subjects = data.get("subjects", {})

    hubmap = {s: hub_path(root_abs, layout, s) for s in subjects}

    # ---------- 体检模式 ----------
    if args.check or not args.payload:
        ok, report, problems = selfcheck(root_abs, layout, hubmap)
        print(f"计数体检：{root_name}")
        for line in report:
            print(line)
        if not args.check and not args.payload:
            print("\n（未提供 --json，仅执行体检）")
        return 1 if problems else 0

    # ---------- 读入条目 ----------
    raw = args.payload
    if raw == "-":
        raw = sys.stdin.read()
    elif raw.startswith("@"):
        raw = read(raw[1:])
    entry = json.loads(raw)

    subject = entry.pop("subject", None)
    if subject not in subjects:
        sys.exit(f"[notes_entry] subject={subject!r} 不在 {list(subjects)} 中")
    hub = entry.pop("hub", None)
    day = entry.pop("date", None) or date.today().isoformat()

    entries = subjects[subject].setdefault("entries", [])
    new_id = entry.pop("id", None) or next_id(entries, subject)

    # links 规范化：允许 ["text|path"] 简写
    links = []
    for l in entry.get("links") or []:
        if isinstance(l, str):
            t, _, p = l.partition("|")
            links.append({"text": t.strip(), "path": p.strip()})
        else:
            links.append(l)
    entry["links"] = links

    record = {"id": new_id, "date": day}
    for k in ("title", "summary", "chapter", "level", "status", "source", "tags"):
        if k in entry and entry[k] not in (None, ""):
            record[k] = entry[k]
    record.setdefault("status", "已整理")
    record["links"] = links

    if any(e.get("id") == new_id for e in entries):
        sys.exit(f"[notes_entry] id 冲突：{new_id}")

    # 中控行：默认取 hub 或第一条链接
    hl = hub or (links[0] if links else {"text": record.get("title", ""), "path": ""})
    hub_line = f"- **{day}** {record.get('title', '')} → [{hl.get('text', '')}]({hl.get('path', '')})"

    print(f"库：{root_name}（{LAYOUTS.get(root_name, {}).get('note', layout)}）")
    print(f"条目：{new_id}  {day}  {record.get('title', '')}")
    print(f"中控行：{hub_line}")
    if args.dry_run:
        print("\n[dry-run] 未写入任何文件")
        return 0

    # ---------- 写入 ----------
    entries.append(record)
    write(json_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"  JSON：{os.path.relpath(json_path, NOTES_ROOT)} 追加 1 条（{len(entries)} 条）")

    for line in bump_hub(root_abs, layout, subject, record.get("chapter", ""), hub_line, day):
        print(line)

    # ---------- 自检（不通过则明确报错） ----------
    ok, report, problems = selfcheck(root_abs, layout, hubmap)
    print("\n自检：")
    for line in report:
        print(line)
    if not ok:
        sys.exit(f"[notes_entry] 计数仍不一致：{problems}（请检查中控章节标题）")

    # ---------- 收尾 ----------
    if args.finalize:
        targets = [os.path.relpath(hubmap[subject], root_abs).replace("\\", "/")]
        for l in links:
            p = (l.get("path") or "").lstrip("./")
            if p.endswith(".md") and os.path.exists(os.path.join(root_abs, p)):
                targets.append(p)
        print(f"\n重建 PDF（CWD={root_name}）：{targets}")
        subprocess.run([sys.executable, os.path.join(_paths_mod.SRC_DIR, "tools", "md2pdf.py"),
                        *targets], cwd=root_abs, check=False)
        print("\n重建总索引 …")
        subprocess.run([sys.executable, os.path.join(_paths_mod.SRC_DIR, "build_index.py")],
                       cwd=KAOYAN_ROOT, check=False)

    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

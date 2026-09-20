#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""export_decks_for_anki_markji.py — question_bank.db → 分科 Anki CSV + 墨墨记忆卡(Markji)批量导入 TXT

输出（src/flashcards/export/ 下）：
  Anki/<科目>/<科目>_Anki_选择.csv   choice + judge，5 列：题干/选项/答案/解析/标签
  Anki/<科目>/<科目>_Anki_问答.csv   cloze + qa，4 列：题干/答案/解析/标签
  Markji/<科目>_墨墨记忆卡_批量导入.txt   卡间用独占一行的 === 分隔
  export_manifest.json              卡片归属、跳过原因的可追溯清单

Anki CSV 遵循仓库既有约定（#separator:Tab / #html:true，见 tools/make_anki_cards.py）：
  - $...$ / $$...$$ LaTeX → \(...\) / \[...\]（Anki MathJax）
  - {{cN::x}} 挖空与 ____ 一律显示为 ____，答案在背面
Markji TXT 遵循官方制卡语法：
  - $...$ → [E##...]；{{cN::x}} → [F#N#x]；____ → [F#1#答案]
  - 选择题用 [Choice#fixed# ... ]（固定选项顺序，解析中的字母位置才对得上）
  - 判断题用 [Choice#fixed# *正确 -错误 ]
  - 普通文字中的 [ ] 转义为 \\[ \\]

用法：
  python src/tools/export_decks_for_anki_markji.py
"""
import io
import json
import os
import re
import sqlite3
import sys

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(SRC_DIR, "question_bank.db")
EXPORT_DIR = os.path.join(SRC_DIR, "flashcards", "export")
ANKI_DIR = os.path.join(EXPORT_DIR, "Anki")
MARKJI_DIR = os.path.join(EXPORT_DIR, "Markji")

SUBJECTS = ["408", "数学一", "英语一", "政治"]

# 章节代码 → 中文名（标签与 Markji 卡片标签用）
CHAPTER_NAMES = {
    "408": {"DS": "数据结构", "CO": "计算机组成原理", "OS": "操作系统",
            "CN": "计算机网络", "CROSS": "综合"},
    "数学一": {"GS": "高等数学", "XD": "线性代数", "GL": "概率论与数理统计"},
    "英语一": {"CLOZE": "完形填空", "GRAM": "语法", "READ": "阅读理解",
              "TRAN": "翻译", "VOC": "词汇", "WRITE": "写作"},
    "政治": {"MY": "马原", "MZ": "毛中特", "SG": "史纲", "SX": "思修", "XX": "习思想"},
}

# Q-STUDY 无主题题目按关键词路由到科目/章节
STUDY_ROUTES = [
    ("数学一", "GS", re.compile(r"微分|方程|特解|通解|特征方程|齐次|y″|洛必达")),
    ("408", "DS", re.compile(r"链表|排序|复杂度|主定理|顺序表|归并|静态链表|算法|循环")),
]

LATEX_BLOCK_RE = re.compile(r"\$\$(.*?)\$\$", re.S)
LATEX_INLINE_RE = re.compile(r"\$([^$]+?)\$")
CLOZE_RE = re.compile(r"\{\{c(\d+)::(.*?)\}\}", re.S)
OPT_LETTERS = "ABCDEFGHIJ"


# ---------------------------------------------------------------- 文本转换

def _chapter_code(topic_id):
    """408-DS-01-01 → ('408','DS')；MATH-GS-01 → ('数学一','GS')。取不到返回 ('','')"""
    tid = topic_id or ""
    m = re.match(r"408-([A-Z]+)", tid)
    if m:
        return "408", m.group(1)
    m = re.match(r"MATH-([A-Z]+)", tid)
    if m:
        return "数学一", m.group(1)
    m = re.match(r"ENG-([A-Z]+)", tid)
    if m:
        return "英语一", m.group(1)
    m = re.match(r"POL-([A-Z]+)", tid)
    if m:
        return "政治", m.group(1)
    return "", ""


def to_anki(text):
    """字段 → Anki（html:true）：LaTeX 改 MathJax 定界符，挖空改 ____，裸换行改 <br>"""
    def f_block(m):
        return r"\[ " + m.group(1).strip() + r" \]"
    def f_inline(m):
        return r"\(" + m.group(1).strip() + r"\)"
    s = LATEX_BLOCK_RE.sub(f_block, text)
    s = LATEX_INLINE_RE.sub(f_inline, s)
    s = CLOZE_RE.sub(lambda m: "____", s)
    s = s.replace("\t", " ").replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\n", "<br>")
    return s.strip()


def to_markji(text, blank_answer=None):
    """字段 → Markji 语法。blank_answer：把 ____ 替换为带答案的 F 标签（每题仅一个空）"""
    store = []

    def stash(rendered):
        store.append(rendered)
        return "\x00%d\x00" % (len(store) - 1)

    def f_block(m):
        return stash("[E##" + m.group(1).strip() + "]")
    def f_inline(m):
        return stash("[E##" + m.group(1).strip() + "]")
    def f_cloze(m):
        inner = m.group(2).replace("[", r"\[").replace("]", r"\]")
        return stash("[F#%s#%s]" % (m.group(1), inner))
    def f_blank(m):
        inner = (blank_answer or "").replace("[", r"\[").replace("]", r"\]")
        return stash("[F#1#" + inner + "]")

    s = LATEX_BLOCK_RE.sub(f_block, text)
    s = LATEX_INLINE_RE.sub(f_inline, s)
    s = CLOZE_RE.sub(f_cloze, s)
    if blank_answer is not None:
        s = re.sub(r"_{2,}", f_blank, s)
    # 剩余普通文本转义中括号，再回填受保护的语法
    s = s.replace("[", r"\[").replace("]", r"\]")
    s = re.sub(r"\x00(\d+)\x00", lambda m: store[int(m.group(1))], s)
    return s.strip()


def md_plain(text):
    """普通段落（答案/解析）转 Markji：去 LaTeX 定界符、保留真实换行、转义中括号"""
    return to_markji(text)


# ---------------------------------------------------------------- 取数与建模

def route_study(qid, stem):
    for subj, chap, rx in STUDY_ROUTES:
        if rx.search(stem):
            return subj, chap
    return "", ""


def load_cards(conn):
    sql = """
        SELECT q.id, q.type, q.content, q.topic_id, t.name, t.subject
        FROM questions q LEFT JOIN topics t ON q.topic_id = t.id
        ORDER BY q.id
    """
    cards, skipped = [], []
    for qid, qtype, raw, topic_id, topic_name, subject in conn.execute(sql):
        try:
            c = json.loads(raw)
        except Exception:
            skipped.append({"qid": qid, "reason": "content 解析失败"})
            continue
        stem = (c.get("stem") or c.get("question") or "").strip()
        if not stem:
            skipped.append({"qid": qid, "reason": "题干为空"})
            continue

        subj, chap = (subject or ""), ""
        _s, _c = _chapter_code(topic_id)
        if _s:
            subj, chap = _s, _c
        if subj not in SUBJECTS:
            subj, chap = route_study(qid, stem)
        if subj not in SUBJECTS:
            skipped.append({"qid": qid, "reason": "无法路由到科目"})
            continue

        exp = c.get("explanation") or ""
        tags = c.get("tags") or []
        card = {
            "qid": qid, "type": qtype, "subject": subj, "chapter": chap,
            "topic_id": topic_id, "topic_name": topic_name or "",
            "stem": stem, "explanation": exp, "tags": tags,
        }

        if qtype == "choice":
            opts = c.get("options") or []
            ans = c.get("answer")
            if not isinstance(opts, list) or len(opts) < 2:
                skipped.append({"qid": qid, "reason": "选项缺失"})
                continue
            if not isinstance(ans, int) or isinstance(ans, bool) or not (0 <= ans < len(opts)):
                skipped.append({"qid": qid, "reason": "选择题答案非法"})
                continue
            card.update(options=opts, answer=ans)

        elif qtype == "judge":
            ans = c.get("answer")
            if isinstance(ans, str):
                ans = ans.strip().lower() in ("true", "正确", "对", "1")
            elif isinstance(ans, int) and not isinstance(ans, bool):
                ans = bool(ans)
            if not isinstance(ans, bool):
                skipped.append({"qid": qid, "reason": "判断题答案非法"})
                continue
            card.update(answer=ans, options=["正确", "错误"])

        elif qtype == "fill":
            ans = c.get("answer")
            if isinstance(ans, list):
                ans = "；".join(str(a) for a in ans if str(a).strip())
            if not isinstance(ans, str) or not ans.strip():
                skipped.append({"qid": qid, "reason": "填空题答案缺失"})
                continue
            if "{{c" in stem or "____" in stem:
                card["kind"] = "cloze"
            else:
                card["kind"] = "qa"  # 名 fill 实问答（无挖空标记）
            card["answer"] = ans.strip()

        elif qtype == "short":
            ans = c.get("reference_answer") or c.get("answer") or ""
            kp = c.get("key_points") or []
            if not str(ans).strip():
                skipped.append({"qid": qid, "reason": "简答题答案缺失"})
                continue
            card["kind"] = "qa"
            card["answer"] = str(ans).strip()
            card["key_points"] = kp
        else:
            skipped.append({"qid": qid, "reason": "未知题型 %s" % qtype})
            continue

        cards.append(card)

    # 同科目按去空白题干去重
    deduped, seen, dups = [], set(), []
    for c in cards:
        key = (c["subject"], re.sub(r"\s+", "", c["stem"]))
        if key in seen:
            dups.append({"qid": c["qid"], "reason": "题干重复"})
            continue
        seen.add(key)
        deduped.append(c)
    return deduped, skipped + dups


# ---------------------------------------------------------------- 渲染 Anki

def _anki_tag(card):
    chap_name = CHAPTER_NAMES.get(card["subject"], {}).get(card["chapter"], "")
    base = "考研" + card["subject"]
    return base + ("::" + chap_name if chap_name else "")


def render_anki(cards_by_subject):
    for subj in SUBJECTS:
        cards = cards_by_subject.get(subj, [])
        if not cards:
            continue
        out_dir = os.path.join(ANKI_DIR, subj)
        os.makedirs(out_dir, exist_ok=True)
        choice_rows, qa_rows = [], []

        for c in cards:
            tag = _anki_tag(c)
            if c["type"] in ("choice", "judge"):
                if c["type"] == "choice":
                    q = to_anki(c["stem"])
                    opts_html = "<br>".join(
                        "%s. %s" % (OPT_LETTERS[i], to_anki(o))
                        for i, o in enumerate(c["options"]))
                    ans = OPT_LETTERS[c["answer"]]
                else:
                    stem = c["stem"]
                    if not re.search(r"[（(].{0,8}[）)]\s*$", stem):
                        stem += "（对/错？）"
                    q = to_anki(stem)
                    opts_html = "A. 正确<br>B. 错误"
                    ans = "A" if c["answer"] else "B"
                choice_rows.append((q, opts_html, ans, to_anki(c["explanation"]), tag))
            else:
                q = to_anki(c["stem"])
                ans = to_anki(c["answer"])
                exp = to_anki(c["explanation"])
                qa_rows.append((q, ans, exp, tag))

        def write_csv(path, columns, rows):
            lines = ["#separator:Tab", "#html:true", "#columns:" + "\t".join(columns)]
            for row in rows:
                line = "\t".join(row)
                assert "\n" not in line and "\r" not in line, "字段内残留裸换行"
                lines.append(line)
            with io.open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
            with io.open(path, encoding="utf-8") as f:
                n = sum(1 for _ in f)
            ok = n == len(rows) + 3
            print("[%s] %d 张 → %s" % ("自检通过" if ok else "自检异常", len(rows), path))

        if choice_rows:
            write_csv(os.path.join(out_dir, "%s_Anki_选择.csv" % subj),
                      ["题干", "选项", "答案", "解析", "标签"], choice_rows)
        if qa_rows:
            write_csv(os.path.join(out_dir, "%s_Anki_问答.csv" % subj),
                      ["题干", "答案", "解析", "标签"], qa_rows)


# ---------------------------------------------------------------- 渲染 Markji

def _md_label(card):
    # 卡片顶部的加粗章节/知识点标签
    name = card["topic_name"] or ""
    chap_name = CHAPTER_NAMES.get(card["subject"], {}).get(card["chapter"], "")
    if name and chap_name and name != chap_name:
        text = chap_name + " · " + name
    else:
        text = name or chap_name
    return "[T#B#" + text.replace("[", r"\[").replace("]", r"\]") + "]" if text else ""


def render_markji(cards_by_subject):
    os.makedirs(MARKJI_DIR, exist_ok=True)
    for subj in SUBJECTS:
        cards = cards_by_subject.get(subj, [])
        if not cards:
            continue
        blocks = []
        for c in cards:
            label = _md_label(c)
            lines = [label] if label else []

            if c["type"] == "choice":
                lines.append(to_markji(c["stem"]))
                opt_lines = ["[Choice#fixed#"]
                for i, o in enumerate(c["options"]):
                    prefix = "* " if i == c["answer"] else "- "
                    opt_lines.append(prefix + to_markji(o))
                opt_lines.append("]")
                lines.extend(opt_lines)
                lines.append("---")
                correct = "%s. %s" % (OPT_LETTERS[c["answer"]], c["options"][c["answer"]])
                lines.append("答案：" + md_plain(correct))
                if c["explanation"]:
                    lines.append(md_plain(c["explanation"]))

            elif c["type"] == "judge":
                stem = c["stem"]
                if not re.search(r"[（(].{0,8}[）)]\s*$", stem):
                    stem += "（对/错？）"
                lines.append(to_markji(stem))
                truth = c["answer"]
                lines += [
                    "[Choice#fixed#",
                    ("* " if truth else "- ") + "正确",
                    ("- " if truth else "* ") + "错误",
                    "]",
                    "---",
                    "答案：" + ("正确" if truth else "错误"),
                ]
                if c["explanation"]:
                    lines.append(md_plain(c["explanation"]))

            else:  # fill(cloze/qa) / short(qa)
                if c.get("kind") == "cloze":
                    lines.append(to_markji(c["stem"], blank_answer=c["answer"]))
                    lines.append("---")
                    if c["explanation"]:
                        lines.append(md_plain(c["explanation"]))
                else:
                    lines.append(to_markji(c["stem"]))
                    lines.append("---")
                    kp = c.get("key_points") or []
                    if kp:
                        lines.append("要点：" + "；".join(str(x) for x in kp))
                    lines.append("参考答案：")
                    lines.append(md_plain(c["answer"]))
                    if c["explanation"] and c["explanation"].strip() not in c["answer"]:
                        lines.append(md_plain(c["explanation"]))

            blocks.append("\n".join(x for x in lines if x is not None).strip())

        content = "\n===\n".join(blocks) + "\n"
        path = os.path.join(MARKJI_DIR, "%s_墨墨记忆卡_批量导入.txt" % subj)
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        n_cards = content.count("\n===\n") + 1
        n_lines = content.count("\n")
        print("[自检通过] %d 张（%d 行）→ %s" % (n_cards, n_lines, path))


# ---------------------------------------------------------------- main

def main():
    conn = sqlite3.connect(DB_PATH)
    cards, skipped = load_cards(conn)
    conn.close()

    by_subject = {s: [] for s in SUBJECTS}
    for c in cards:
        by_subject[c["subject"]].append(c)

    print("=" * 70)
    print("可导出卡片：%d 张；跳过：%d 张" % (len(cards), len(skipped)))
    for s in SUBJECTS:
        dist = {}
        for c in by_subject[s]:
            dist[c["type"]] = dist.get(c["type"], 0) + 1
        print("  %-4s %3d 张  %s" % (s, len(by_subject[s]), dist))
    if skipped:
        print("跳过明细：")
        for x in skipped:
            print("   ", x)
    print("=" * 70)

    render_anki(by_subject)
    render_markji(by_subject)

    manifest = {
        "total": len(cards),
        "by_subject": {s: [c["qid"] for c in by_subject[s]] for s in SUBJECTS},
        "skipped": skipped,
    }
    with io.open(os.path.join(EXPORT_DIR, "export_manifest.json"), "w",
                 encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("清单 → %s" % os.path.join(EXPORT_DIR, "export_manifest.json"))


if __name__ == "__main__":
    main()

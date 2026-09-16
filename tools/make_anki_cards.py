#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题库 JSON → Anki 导入文件生成器（单源维护于 考研/src/tools/）

支持两种输出：
  - .csv/.txt  → Anki 文本导入格式（#separator:Tab + #html:true，可直接 文件→导入）
  - .json      → flashcard-studio 中间 JSON（front/back/tags 对象数组）

支持题型：choice（选择）/ tf（判断）/ fill（填空）/ qa（简答）

用法：
    python make_anki_cards.py <题库.json> [输出文件] [标签前缀] [--fields]

    - 输出文件省略时默认生成 <题库名>_Anki.csv（与源文件同目录）
    - 标签前缀默认 "考研数学::微分方程"，最终标签为 前缀::卡片ch字段
    - 相对路径按调用方 CWD 解析，脚本本身不依赖固定工作目录
    - --fields：分栏字段模式，配合自定义笔记型使用（模板见
      题库/Anki选择题笔记型模板.txt）。按题型自动拆分为两个文件，
      避免不同题型混入同一笔记型导致导入失败：
        *_fields_选择.csv   ← choice/tf（判断题已化为 A.正确/B.错误）
                              → 导入「考研选择题」笔记型（5 列：题干/选项/答案/解析/标签）
        *_fields_问答.csv   ← fill/qa
                              → 导入「考研问答题」笔记型（4 列：题干/答案/解析/标签）
      空分组不产出文件；指定输出文件时以其文件名（去扩展名）为拆分基准

数据校验（任一失败即报错退出，不产出文件）：
    - 顶层须含非空 cards 数组；每张卡须含合法 type
    - choice：opts 为非空列表、ans 为范围内整数下标
    - tf：ans 为布尔（或 0/1）
    - fill：ans 为非空字符串或字符串列表
    - q 非空；front 文本重复的卡片视为数据错误
"""
import io
import json
import re
import sys
from pathlib import Path

# Windows 控制台强制 UTF-8，避免中文输出乱码
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

VALID_TYPES = {"choice", "tf", "fill", "qa"}
OPTION_KEYS = "ABCDEFGHIJ"
DEFAULT_PREFIX = "考研数学::微分方程"


def strip_span(html: str) -> str:
    """去掉 <span> 包裹（题库源里的 class='eq fx' 样式在 Anki 中无效），保留公式文本"""
    return re.sub(r"</?span[^>]*>", "", html)


def sanitize(html: str) -> str:
    """字段清洗：去 span、制表符转空格、换行转 <br>（Anki 文本导入按行分卡，字段内禁止裸换行/制表符）"""
    s = strip_span(str(html))
    s = s.replace("\t", " ").replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\n", "<br>")
    return s.strip()


def validate_card(idx: int, card: dict, errors: list, warnings: list):
    """单卡校验，错误写入 errors、瑕疵写入 warnings"""
    loc = "卡片 #%d" % (idx + 1)
    ctype = card.get("type")
    if ctype not in VALID_TYPES:
        errors.append("%s: 未知 type=%r（合法值：%s）" % (loc, ctype, "/".join(sorted(VALID_TYPES))))
        return
    if not str(card.get("q", "")).strip():
        errors.append("%s: q（题干）为空" % loc)
    if ctype == "choice":
        opts = card.get("opts")
        if not isinstance(opts, list) or not opts:
            errors.append("%s: choice 题 opts 必须为非空列表" % loc)
            return
        if len(opts) > len(OPTION_KEYS):
            errors.append("%s: 选项数 %d 超过上限 %d" % (loc, len(opts), len(OPTION_KEYS)))
        ans = card.get("ans")
        if not isinstance(ans, int) or isinstance(ans, bool) or not (0 <= ans < len(opts)):
            errors.append("%s: choice 题 ans 须为 0~%d 的整数下标，实际 %r" % (loc, len(opts) - 1, ans))
    elif ctype == "tf":
        ans = card.get("ans")
        if not isinstance(ans, bool) and ans not in (0, 1):
            errors.append("%s: tf 题 ans 须为 true/false（或 0/1），实际 %r" % (loc, ans))
    elif ctype == "fill":
        ans = card.get("ans")
        ok = (isinstance(ans, str) and ans.strip()) or (
            isinstance(ans, list) and ans and all(isinstance(a, str) and a.strip() for a in ans))
        if not ok:
            errors.append("%s: fill 题 ans 须为非空字符串或字符串列表，实际 %r" % (loc, ans))
    if not card.get("exp"):
        warnings.append("%s: 缺少 exp（解析），卡片仍可生成但背面只有答案" % loc)


def build_faces(card: dict):
    """按题型生成正面/背面 HTML"""
    q = sanitize(card["q"])
    ctype = card["type"]
    if ctype == "choice":
        opts_html = "<br>".join(
            "%s. %s" % (OPTION_KEYS[i], sanitize(o)) for i, o in enumerate(card["opts"]))
        front = "%s<br>%s" % (q, opts_html)
        ans = card["ans"]
        back = "<b>答案：%s. %s</b>" % (OPTION_KEYS[ans], sanitize(card["opts"][ans]))
    elif ctype == "tf":
        front = q if re.search(r"[（(].{0,6}[）)]\s*$", q) else q + "（对/错？）"
        truth = card["ans"] if isinstance(card["ans"], bool) else bool(card["ans"])
        back = "<b>答案：%s</b>" % ("正确" if truth else "错误")
    elif ctype == "fill":
        front = q
        ans = card["ans"]
        ans_text = ans if isinstance(ans, str) else "；".join(ans)
        back = "<b>答案：%s</b>" % sanitize(ans_text)
    else:  # qa
        front = q
        back = "<b>参考答案：</b><br>" + sanitize(card["ans"])
    if card.get("exp"):
        back += "<br><br>" + sanitize(card["exp"])
    return front, back


def build_field_parts(card: dict):
    """按题型拆分为分栏字段，供自定义笔记型导入。
    返回 (题型分组, 题干, 选项HTML, 答案文本, 解析HTML)：
      选择组（choice/tf）：答案为字母，配「考研选择题」笔记型
      问答组（fill/qa）  ：选项为空、答案为文本，配「考研问答题」笔记型
    """
    q = sanitize(card["q"])
    ctype = card["type"]
    exp_html = sanitize(card["exp"]) if card.get("exp") else ""
    if ctype == "choice":
        opts = [(OPTION_KEYS[i], sanitize(o)) for i, o in enumerate(card["opts"])]
        opts_html = "<br>".join("%s. %s" % kv for kv in opts)
        return "选择", q, opts_html, OPTION_KEYS[card["ans"]], exp_html
    if ctype == "tf":  # 化为 A.正确 / B.错误 的两选项形式，与选择题同一笔记型
        q = q if re.search(r"[（(].{0,6}[）)]\s*$", q) else q + "（对/错？）"
        truth = card["ans"] if isinstance(card["ans"], bool) else bool(card["ans"])
        return "选择", q, "A. 正确<br>B. 错误", ("A" if truth else "B"), exp_html
    if ctype == "fill":
        ans = card["ans"]
        ans_text = ans if isinstance(ans, str) else "；".join(ans)
        return "问答", q, "", sanitize(ans_text), exp_html
    # qa
    return "问答", q, "", sanitize(card["ans"]), exp_html


def main():
    raw_args = sys.argv[1:]
    fields_mode = "--fields" in raw_args
    args = [a for a in raw_args if a != "--fields"]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        sys.exit(0 if args else 1)

    src = Path(args[0])
    if not src.is_file():
        print("错误：找不到题库文件 %s（按当前目录 %s 解析）" % (src, Path.cwd()), file=sys.stderr)
        sys.exit(1)

    default_name = src.stem + ("_Anki_fields" if fields_mode else "_Anki.csv")
    dst = Path(args[1]) if len(args) > 1 else src.with_name(default_name)
    if fields_mode:
        dst = dst.with_suffix("")  # 拆分基准名，实际产出 *_选择.csv / *_问答.csv
    prefix = args[2] if len(args) > 2 else DEFAULT_PREFIX

    with open(src, encoding="utf-8") as f:
        data = json.load(f)

    raw_cards = data.get("cards")
    if not isinstance(raw_cards, list) or not raw_cards:
        print("错误：%s 缺少非空 cards 数组" % src, file=sys.stderr)
        sys.exit(1)

    errors, warnings = [], []
    seen_fronts = {}
    cards = []
    groups = {"选择": [], "问答": []}  # fields 模式按笔记型分组
    for i, c in enumerate(raw_cards):
        validate_card(i, c, errors, warnings)
        if errors:
            continue  # 先收集全部结构错误再统一报错
        if fields_mode:
            group, q, opts_html, ans_text, exp_html = build_field_parts(c)
            key = q + "\x00" + opts_html
        else:
            front, back = build_faces(c)
            key = front
        if key in seen_fronts:
            errors.append("卡片 #%d: 正面与卡片 #%d 完全重复" % (i + 1, seen_fronts[key] + 1))
            continue
        seen_fronts[key] = i
        ch = str(c.get("ch", "")).strip().replace(" ", "_")
        tags = (prefix + "::" + ch) if ch else prefix
        if fields_mode:
            groups[group].append({"q": q, "opts": opts_html, "ans": ans_text, "exp": exp_html, "tags": tags})
        else:
            cards.append({"front": front, "back": back, "tags": tags})

    if errors:
        print("数据校验失败，未生成任何文件：", file=sys.stderr)
        for e in errors:
            print("  ✗ " + e, file=sys.stderr)
        sys.exit(1)
    for w in warnings:
        print("  ! " + w)

    type_counts = {}
    for c in raw_cards:
        type_counts[c["type"]] = type_counts.get(c["type"], 0) + 1
    dist = "题型分布：%s" % "，".join("%s×%d" % (k, v) for k, v in sorted(type_counts.items()))

    dst.parent.mkdir(parents=True, exist_ok=True)

    def write_csv(path, columns, rows):
        lines = ["#separator:Tab", "#html:true", "#columns:" + "\t".join(columns)]
        for row in rows:
            line = "\t".join(row)
            assert "\n" not in line and "\r" not in line, "字段清洗未生效"
            lines.append(line)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        # 回读自检：行数 = 卡片数 + 3 行头部
        with open(path, encoding="utf-8") as f:
            written = sum(1 for _ in f)
        status = "自检通过" if written == len(rows) + 3 else "自检异常"
        print("[%s] 生成 %d 张卡片 → %s" % (status, len(rows), path))

    if fields_mode:
        # 按笔记型拆分输出，避免不同题型混入同一笔记型导致导入失败
        note_types = {"选择": "考研选择题", "问答": "考研问答题"}
        produced = 0
        for group in ("选择", "问答"):
            g = groups[group]
            if not g:
                continue
            path = dst.parent / (dst.name + "_%s.csv" % group)
            if group == "选择":
                write_csv(path, ["题干", "选项", "答案", "解析", "标签"],
                          [(c["q"], c["opts"], c["ans"], c["exp"], c["tags"]) for c in g])
            else:
                write_csv(path, ["题干", "答案", "解析", "标签"],
                          [(c["q"], c["ans"], c["exp"], c["tags"]) for c in g])
            produced += len(g)
            print("  ↳ 导入笔记型：「%s」（模板见 题库/Anki选择题笔记型模板.txt）" % note_types[group])
        print("共 %d 张卡片（%s）" % (produced, dist))
        return

    if dst.suffix.lower() == ".json":
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(cards, f, ensure_ascii=False, indent=1)
        print("生成 %d 张卡片 → %s（%s）" % (len(cards), dst, dist))
    else:
        # Anki 文本导入格式：头部声明 + Tab 分隔，字段已在 sanitize 中清除裸换行/制表符
        write_csv(dst, ["Front", "Back", "Tags"], [(c["front"], c["back"], c["tags"]) for c in cards])
        print("（%s）" % dist)


if __name__ == "__main__":
    main()

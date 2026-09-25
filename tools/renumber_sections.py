# -*- coding: utf-8 -*-
"""renumber_sections.py — 高数 18 讲「节号对齐讲号」一次性迁移工具（2026-09-24）。

背景：拆分自旧教材章体系（1~8 章）后，讲内节号仍是旧章号（讲10 里是 3.7、讲13 里是 4.x…）。
本工具按讲内连续重排：第 N 讲的 ## 依次 N.1、N.2…，###/#### 继承父级前缀；
讲17 分部「第一章~第六章」→ 17.1~17.6（其下 1.1 → 17.1.1）；讲18 保持 Part 字母 8.A.x → 18.A.x。

用法：
    python src/tools/renumber_sections.py            # dry-run，输出分类报告
    python src/tools/renumber_sections.py --apply    # 落盘改写

落盘后必做：audit_notes.py 验证断链归零 → md2pdf.py 增量 → 手工清理报告里 MANUAL 项。
"""
import os, re, sys, json

APPLY = "--apply" in sys.argv
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.environ.get("NOTES_ROOT", r"E:\NPEE"), "Math")   # 笔记库根（2026-09-25 起与代码根分离，可用环境变量 NOTES_ROOT 覆盖）
GS = os.path.join(ROOT, "高数")

# ---------- 讲文件与 ## 级映射（按文件内出现顺序，编号连续） ----------
MAPS = {
    "第1讲_函数极限与连续.md": [("1.4", "1.1"), ("1.5", "1.2"), ("1.6", "1.3")],
    "第2讲_数列极限.md": [],
    "第3讲_一元函数微分学的概念.md": [("2.1", "3.1")],
    "第4讲_一元函数微分学的计算.md": [("2.2b", "4.2"), ("2.2", "4.1")],
    "第5讲_一元函数微分学的应用一_几何应用.md":
        [("2.3", "5.1"), ("2.4", "5.2"), ("2.5", "5.3"), ("2.6", "5.4"), ("2.8", "5.5")],
    "第6讲_一元函数微分学的应用二_中值定理与微分等式不等式.md":
        [("2.7", "6.1"), ("2.9", "6.2"), ("2.10", "6.3")],
    "第7讲_一元函数微分学的应用三_物理应用.md": [("7A", "7.1"), ("7B", "7.2")],
    "第8讲_一元函数积分学的概念与性质.md": [("3.2", "8.1"), ("3.6", "8.2")],
    "第9讲_一元函数积分学的计算.md":
        [("3.1", "9.1"), ("3.4", "9.2"), ("3.5", "9.3"), ("3.9", "9.4"),
         ("3.10", "9.5"), ("3.14", "9.6"), ("3.16", "9.7")],
    "第10讲_一元函数积分学的应用一_几何应用.md": [("3.7", "10.1"), ("3.8", "10.2"), ("3.11", "10.3")],
    "第11讲_一元函数积分学的应用二_积分等式与积分不等式.md": [("3.3", "11.1"), ("3.15", "11.2")],
    "第12讲_一元函数积分学的应用三_物理应用.md": [("3.12", "12.1"), ("3.13", "12.2")],
    "第13讲_多元函数微分学.md": [("4.1", "13.1"), ("4.2", "13.2"), ("4.3", "13.3"), ("4.4", "13.4")],
    "第14讲_二重积分.md": [(f"5.{i}", f"14.{i}") for i in range(1, 9)],
    "第15讲_微分方程.md": [(f"6.{i}", f"15.{i}") for i in range(1, 15)],
    "第16讲_无穷级数.md": [(f"7.{i}", f"16.{i}") for i in range(1, 8)],
    "第17讲_多元函数积分学的预备知识.md": "SPECIAL17",
    "第18讲_多元函数积分学.md": [("8.", "18.")],
}
LECTURE_BY_NO = {}  # "1".."18" -> filename
for fn in MAPS:
    m = re.match(r"第(\d+)讲", fn)
    if m:
        LECTURE_BY_NO[m.group(1)] = fn

CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}

# 旧文件名（拆分前）→ 现文件名，用于 index/正文的 stale path 修复
OLD_NAMES = {
    "第8讲_一元函数积分学.md": "第8讲_一元函数积分学的概念与性质.md",
    "第9讲_一元函数积分学.md": "第9讲_一元函数积分学的计算.md",
    "第6讲_一元函数微分学.md": "第6讲_一元函数微分学的应用二_中值定理与微分等式不等式.md",
    "第14讲_多元函数积分学.md": "第14讲_二重积分.md",
    "第10讲_一元函数积分学.md": "第10讲_一元函数积分学的应用一_几何应用.md",
    "第11讲_一元函数积分学.md": "第11讲_一元函数积分学的应用二_积分等式与积分不等式.md",
    "第12讲_一元函数积分学.md": "第12讲_一元函数积分学的应用三_物理应用.md",
    "第3讲_一元函数微分学.md": "第3讲_一元函数微分学的概念.md",
    "第4讲_一元函数微分学.md": "第4讲_一元函数微分学的计算.md",
    "第5讲_一元函数微分学.md": "第5讲_一元函数微分学的应用一_几何应用.md",
    "第7讲_一元函数微分学.md": "第7讲_一元函数微分学的应用三_物理应用.md",
}

def resolve_display_name(basename):
    """显示名 → 讲文件：精确名 / 单讲 / 区间名（第1–2讲_…）。"""
    if basename in MAPS:
        return basename
    m = re.match(r"^第(\d{1,2})(?:[–\-~至](\d{1,2}))?讲_", basename)
    if not m:
        return None
    lo = int(m.group(1)); hi = int(m.group(2) or m.group(1))
    return (lo, hi)

def map_num_in_range(rng, num):
    lo, hi = rng
    for no in range(lo, hi + 1):
        fn = LECTURE_BY_NO.get(str(no))
        if fn:
            new = map_num(fn, num)
            if new:
                return new
    return None

# 显式覆盖（脚本规则吃不到的跨文件裸引）
OVERRIDES = [
    ("第2讲_数列极限.md", "「1.5 极限的计算方法」", "「1.2 极限的计算方法」"),
    ("第2讲_数列极限.md", "「1.6 极限的性质」", "「1.3 极限的性质」"),
    ("第7讲_一元函数微分学的应用三_物理应用.md", "的 6.13 迁入", "的 15.13 迁入"),
    # —— 二阶段 override（作用于首轮改写后的文本；§2.4→§5.2 等已由 §-规则完成）——
    ("第5讲_一元函数微分学的应用一_几何应用.md", "不等式证明的方法路由：§2.9（本文件）", "不等式证明的方法路由：第6讲 6.2"),
    ("第6讲_一元函数微分学的应用二_中值定理与微分等式不等式.md", "见 §2.4 约定", "见第5讲 5.2 约定"),
    ("第6讲_一元函数微分学的应用二_中值定理与微分等式不等式.md", "本笔记按 §2.4：", "本笔记按第5讲 5.2："),
    ("第6讲_一元函数微分学的应用二_中值定理与微分等式不等式.md", "见 §2.3「驻点值 + 端点值定最值的前提」辨析（本文件）", "见第5讲 5.1「驻点值 + 端点值定最值的前提」辨析"),
    ("第8讲_一元函数积分学的概念与性质.md", "计算应用：§3.7", "计算应用：第10讲 10.1"),
    ("第9讲_一元函数积分学的计算.md", "→ 见 3.2 ", "→ 见第8讲 8.1 "),
    ("公式速查.md", "（第5章）、参数方程弧长与面积（第3章 3.8）", "（第14讲）、参数方程弧长与面积（第10讲 10.2）"),
    ("公式速查.md", "见第3章 3.6.2", "见第8讲 8.2.2"),
    ("错题归档.md", "高数 > 第2章 > 拐点判定（§2.4）", "高数 > 第3–6讲 > 拐点判定（第5讲 5.2）"),
    ("错题归档.md", "已在 §2.4 易混淆点中登记", "已在第5讲 5.2 易混淆点中登记"),
    ("第18讲_多元函数积分学.md", "| 第5章 |", "| 第14讲 |"),
    ("第18讲_多元函数积分学.md", "**第8章 Part A**", "**第18讲 Part A**"),
    ("第18讲_多元函数积分学.md", "| 第8章 Part B |", "| 第18讲 Part B |"),
]

# ---------- 映射原语 ----------
def map_num(fname, num):
    """把文件 fname 里的旧节号 num 映射为新号；无映射返回 None。"""
    spec = MAPS.get(fname)
    if spec is None:
        return None
    if spec == "SPECIAL17":
        m = re.fullmatch(r"([1-6])\.(\d+)(?:\.(\d+))?", num)
        if m:
            base = f"17.{m.group(1)}.{m.group(2)}"
            return base + (f".{m.group(3)}" if m.group(3) else "")
        return None
    best = None
    for old, new in spec:
        if num == old.rstrip(".") or num.startswith(old if old.endswith(".") else old + ".") or num == old:
            if old.endswith("."):
                cand = new + num[len(old):] if num != "8." else new
            else:
                cand = new + num[len(old):]
            if best is None or len(old) > len(best[0]):
                best = (old, cand)
    return best[1] if best else None

def compact(num):  # 锚点里的数字前缀："8.A.2"->"8a2"，"3.6"->"36"
    return num.lower().replace(".", "")

# ---------- 扫描范围 ----------
def iter_md():
    for d in ("高数", "专题", "跨科综合"):
        dd = os.path.join(ROOT, d)
        for fn in sorted(os.listdir(dd)):
            if fn.endswith(".md"):
                yield d, fn, os.path.join(dd, fn)

MATH_SPAN = re.compile(r"\$\$[^$]*?\$\$|\$[^$\n]+\$")
VERB = r"(?:见|参见|详见|同|按|用|套用|归入|照|入|即)"

report = {"HEAD": [], "ANCHOR": [], "DISP": [], "BARE": [], "MANUAL": [], "JSON": [], "NAME": []}

def sub_outside_math(line, fn, handler):
    """对非数学区间应用 handler(seg)->seg。"""
    out, pos = [], 0
    for m in MATH_SPAN.finditer(line):
        out.append(handler(line[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(handler(line[pos:]))
    return "".join(out)

# 预构建：每文件的锚点表 old_slug_prefix -> new_slug_prefix
ANCHOR_TABLES = {}
for fname, spec in MAPS.items():
    path = os.path.join(GS, fname)
    if not os.path.exists(path):
        continue
    table = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^#{2,6} (\S+)", line)
            if not m:
                continue
            num = m.group(1)
            if spec == "SPECIAL17":
                cm = re.fullmatch(r"第([一二三四五六])章", num)
                if cm:
                    table[f"第{cm.group(1)}章"] = f"17{CN[cm.group(1)]}"
                    continue
            new = map_num(fname, num)
            if new:
                table[compact(num)] = compact(new)
    ANCHOR_TABLES[fname] = table

def fix_anchors(seg, self_file):
    """替换 ](#slug) 与 ](path.md#slug)。"""
    def one(m):
        pre, path, slug, tail = m.group(1), m.group(2), m.group(3).lstrip("#"), m.group(4)
        target = self_file
        if path:
            base = os.path.basename(path)
            target = base if base in MAPS else None
        if not target or target not in ANCHOR_TABLES:
            return m.group(0)
        for old, new in sorted(ANCHOR_TABLES[target].items(), key=lambda kv: -len(kv[0])):
            if slug == old or slug.startswith(old + "-"):
                report["ANCHOR"].append(f"{self_file}: #{slug[:28]} -> #{new}{slug[len(old):][:28]}")
                return f"{pre}{path or ''}#{new}{slug[len(old):]}{tail}"
        return m.group(0)
    return re.sub(r"(\]\()([^)#\s]*?\.md)?(#[^)\s]+)(\))", one, seg)

NUM = r"\d{1,2}(?:\.\d{1,2}(?:\.\d{1,2})?|[AB](?:\.\d{1,2})?)"

def fix_labels(seg, self_file):
    """链接显示文本以节号开头时（如 [8.A.2 对比](#8a2-…)），随目标文件映射同步。"""
    def one(m):
        label, target = m.group(1), m.group(2)
        lm = re.match(r"^(\d{1,2}[AB]?(?:\.[0-9A-Za-z]+){0,3})(?![0-9A-Za-z])", label)
        if not lm:
            return m.group(0)
        num = lm.group(1)
        tf = self_file
        if ".md" in target:
            base = os.path.basename(target.split("#")[0])
            tf = base if base in MAPS else None
        if not tf:
            return m.group(0)
        new = map_num(tf, num)
        if not new:
            return m.group(0)
        report["DISP"].append(f"{self_file}: [{num}…] -> [{new}…]")
        return f"[{new}{label[len(num):]}]({target})"
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", one, seg)

def fix_display(seg, self_file):
    """第N讲 > X.Y ／ 文件名.md > X.Y ／ 第N讲 X.Y ／ §自身节号。"""
    def repl_lecture(m):
        nos, num = m.group(1), m.group(2)
        mm = re.match(r"(\d{1,2})(?:[–\-~至](\d{1,2}))?讲", nos)
        if not mm:
            return m.group(0)
        rng = (int(mm.group(1)), int(mm.group(2) or mm.group(1)))
        new = map_num_in_range(rng, num)
        if not new:
            return m.group(0)
        report["DISP"].append(f"{self_file}: 第{nos} {num} -> {new}")
        return m.group(0).replace(num, new)
    seg = re.sub(rf"第((?:\d{{1,2}}(?:[–\-~至]\d{{1,2}})?)讲)\s*[>§\s]\s*({NUM})",
                 lambda m: repl_lecture(m), seg)
    def repl_file(m):
        fname, num = m.group(1), m.group(2)
        base = os.path.basename(fname)
        target = resolve_display_name(base)
        new = None
        if isinstance(target, str):
            new = map_num(target, num)
        elif target:
            new = map_num_in_range(target, num)
        if not new:
            return m.group(0)
        report["DISP"].append(f"{self_file}: {base[:12]}>{num} -> {new}")
        return m.group(0).replace(num, new)
    seg = re.sub(rf"([\w\-–~]+\.md)\)?\s*>\s*({NUM})", repl_file, seg)
    # §自身节号（§2.4、§7A 指本讲旧号）
    def repl_sec(m):
        num = m.group(1)
        if not self_file:
            return m.group(0)
        new = map_num(self_file, num)
        if not new:
            return m.group(0)
        report["DISP"].append(f"{self_file}: §{num} -> {new}")
        return f"§{new}"
    seg = re.sub(rf"§({NUM})", repl_sec, seg)
    return seg

def fix_bare(seg, self_file):
    """动词/标点紧邻的裸引用（见 3.6.2 /（3.1.13 / –3.1.13 …），仅同文件映射。"""
    def repl(m):
        verb, num = m.group(1), m.group(2)
        new = map_num(self_file, num)
        if not new:
            return m.group(0)
        report["BARE"].append(f"{self_file}: {verb}{num} -> {new}")
        return f"{verb}{new}"
    return re.sub(rf"({VERB}|[（、，\-–])\s*({NUM})(?![题例页卷\d\.])", repl, seg)

def fix_loose_bare(seg, self_file):
    """BARE3：同文件宽松裸引用（特征方程 6.1；上节 5.7；回到 7.1~7.6）。
    仅当 token 恰为该文件自身的旧 ## 节号才替换；§ 前缀（专题内部号）与数字/字母邻接排除。"""
    if not self_file or self_file not in MAPS or MAPS[self_file] == "SPECIAL17":
        return seg
    keys = sorted((k for k, _ in MAPS[self_file]), key=len, reverse=True)
    alt = "|".join(re.escape(k) for k in keys)
    pat = re.compile(rf"(?<![\w\.\$§])((?:{alt}))(?![\w\.\-\d题例页卷])")
    def repl(m):
        num = m.group(1)
        new = map_num(self_file, num)
        if not new:
            return m.group(0)
        report["BARE"].append(f"{self_file}: loose {num} -> {new}")
        return new
    return pat.sub(repl, seg)

def fix_names(seg, where):
    for old, new in OLD_NAMES.items():
        if old in seg and old != new:
            seg = seg.replace(old, new)
            report["NAME"].append(f"{where}: {old} -> {new}")
    return seg

# ---------- 主流程 ----------
def process_md():
    for d, fn, path in iter_md():
        with open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
        fence = False
        out = []
        for i, line in enumerate(lines):
            if line.startswith("```"):
                fence = not fence
                out.append(line); continue
            if fence:
                out.append(line); continue
            # 标题行
            hm = re.match(r"^(#{2,6}) (.+)$", line)
            handled = False
            if hm and d == "高数" and fn in MAPS:
                spec = MAPS[fn]
                head = hm.group(2)
                mnum = re.match(r"^(\S+)(\s.*)?$", head)
                if mnum:
                    num = mnum.group(1)
                    new = None
                    if spec == "SPECIAL17":
                        cm = re.fullmatch(r"第([一二三四五六])章", num)
                        new = f"17.{CN[cm.group(1)]}" if cm else map_num(fn, num)
                    else:
                        new = map_num(fn, num)
                    if new and new != num:
                        report["HEAD"].append(f"{fn}: {num} -> {new}")
                        line = f"{hm.group(1)} {head.replace(num, new, 1)}"
                        handled = True
            # 非标题内容：锚点/显示引用/裸引用/旧文件名（数学区间保护）
            def handler(seg):
                seg = fix_names(seg, f"{d}/{fn}")
                seg = fix_labels(seg, fn if fn in MAPS else None)
                seg = fix_anchors(seg, fn if fn in MAPS else None)
                seg = fix_display(seg, fn)
                seg = fix_bare(seg, fn)
                seg = fix_loose_bare(seg, fn)
                return seg
            if not handled:
                line = sub_outside_math(line, fn, handler)
            out.append(line)
        new_text = "\n".join(out)
        for fname, o, n in OVERRIDES:
            if fname == fn:
                if o in new_text:
                    new_text = new_text.replace(o, n)
                    report["MANUAL"].append(f"override {fn}: {o} -> {n}")
        if APPLY and d == "高数" and fn == "第17讲_多元函数积分学的预备知识.md":
            # 旧的「刻意不重排」警告块已过时
            pat = re.compile(r"> ⚠️ \*\*编号说明\*\*.*?(?=\n\n)", re.S)
            if pat.search(new_text):
                new_text = pat.sub(
                    "> ℹ️ **编号说明（2026-09-24 重排）**：本节号已对齐讲号——分部「第N章」→ `17.N`，"
                    "其下 `K.M` → `17.K.M`；全库引用（索引、错题归档、中控、跨讲链接）已随重排一次性改写。",
                    new_text, count=1)
                report["MANUAL"].append("第17讲 编号说明块已更新")
        if APPLY:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new_text)

def process_index():
    path = os.path.join(ROOT, "notes_index.json")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    def handler(seg):
        seg = fix_names(seg, "notes_index.json")
        seg = fix_display(seg, "notes_index.json")
        return seg
    # JSON 无 $ 数学，直接整段处理
    new_text = handler(text)
    # "第N讲_xxx.md > K.M" 里 K.M 若与文件名讲号不一致，按文件映射（fix_display 已覆盖）
    report["JSON"].append(f"index 改写 {sum('>' in r for r in report['DISP'] if 'notes_index' in r)} 处显示引用")
    if APPLY:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)

def main():
    process_md()
    process_index()
    print(f"模式：{'APPLY' if APPLY else 'DRY-RUN'}")
    for k in ("HEAD", "ANCHOR", "DISP", "BARE", "NAME", "MANUAL"):
        uniq = sorted(set(report[k]))
        print(f"\n[{k}] {len(uniq)} 类 / {len(report[k])} 次")
        for r in uniq:
            print("   ", r)

main()

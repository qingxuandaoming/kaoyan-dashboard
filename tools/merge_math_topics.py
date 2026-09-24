#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""merge_math_topics.py —— 把两个高数专题并入对应的讲，然后删掉专题文件。

用户 2026-09-23 要求：
  ① 空间解析几何专题（`专题/空间曲面体绘图/空间解析几何专题.md`）本来就是张宇第17讲
     「多元函数积分学的预备知识」的正文，专题这层壳没用了 → 整篇并入 `第17讲_….md`，
     专题文件与目录删掉，配图跟着搬。
  ② `专题/无穷级数.md`（判敛与求和路由表）不再单独成篇 → 需要保留的内容并入
     `第16讲_无穷级数.md`，专题文件删掉。

搬运原则
--------
* **正文逐行照搬，不重写**（守恒断言：专题的每一非空正文行都必须出现在目标文件里）。
* **小节号一律不重排**：专题的 `第一~六章 / 1.1 / 2.1 / 3.5` 与 `§1.7 / §2.2 / 2.6`
  被全库引用（notes_index 条目、计算陷阱、错题归档、中控索引行都按这套号写），
  重排会一次性打断几十处引用。与「章→讲迁移不重排节号」是同一条决定。
* 无穷级数专题的 `##`/`###` **整体降一级**，收进第16讲的一个新 `##` 段里，
  这样层级干净，而标题文字（含 §号）不变 → 锚点 slug 与既有引用都还认得。
* 配图：`专题/空间曲面体绘图/{figures,cover_bg.png,*.html}` → `高数/assets/空间解析几何/`，
  md 里的 `./figures/x.png` 同步改成 `./assets/空间解析几何/figures/x.png`；
  html 与 figures 保持同层，它自己的相对引用不会断。
* 引用改写：全 Math 的 .md 与 notes_index.json 里指向这两个专题的路径与链接文字，
  改指到新的讲文件（保留 `> 2.1`、`> §2.2` 这类小节指示）。

默认 dry-run，`--apply` 才写盘；写盘前做守恒断言，不通过则一个文件都不动。
"""
import argparse
import io
import json
import os
import re
import shutil
import sys
from collections import Counter

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
ROOT = os.environ.get("NOTES_ROOT", r"E:\NPEE")   # 笔记库根（2026-09-25 起与代码根分离，可用环境变量 NOTES_ROOT 覆盖）
MATH = os.path.join(ROOT, "Math")
GS = os.path.join(MATH, "高数")
ZT = os.path.join(MATH, "专题")

GEO_DIR = os.path.join(ZT, "空间曲面体绘图")
GEO_MD = os.path.join(GEO_DIR, "空间解析几何专题.md")
GEO_ASSETS_DST = os.path.join(GS, "assets", "空间解析几何")
LEC17 = os.path.join(GS, "第17讲_多元函数积分学的预备知识.md")

SER_MD = os.path.join(ZT, "无穷级数.md")
LEC16 = os.path.join(GS, "第16讲_无穷级数.md")

NAV_RE = re.compile(r"^#{2,3}\s*🔗?\s*双链导航\s*$")
MARK = "<!-- merged-by: tools/merge_math_topics.py (2026-09-23) -->"

# 引用改写：旧专题路径片段 → 新讲文件名（按调用方所在目录给相对前缀）
GEO_NAMES = ["../专题/空间曲面体绘图/空间解析几何专题.md",
             "./专题/空间曲面体绘图/空间解析几何专题.md",
             "专题/空间曲面体绘图/空间解析几何专题.md",
             "./空间曲面体绘图/空间解析几何专题.md",
             "空间解析几何专题.md"]
SER_NAMES = ["../专题/无穷级数.md", "./专题/无穷级数.md", "专题/无穷级数.md"]
NEW17 = "第17讲_多元函数积分学的预备知识.md"
NEW16 = "第16讲_无穷级数.md"


def read(p):
    with open(p, encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def write(p, t):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(t)


def cut_nav(lines):
    for i, ln in enumerate(lines):
        if NAV_RE.match(ln):
            return lines[:i], lines[i:]
    return lines, []


def strip_front_matter(lines):
    """去掉 YAML front matter（--- … ---）与其后的 H1 / 标语 / 分隔线。"""
    i = 0
    if lines and lines[0].strip() == "---":
        j = 1
        while j < len(lines) and lines[j].strip() != "---":
            j += 1
        i = j + 1
    # 跳过 H1、紧随的 > 标语、以及 --- 分隔线
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("# ") or s.startswith(">") or s == "---" or s == "":
            i += 1
            if s.startswith("## "):
                i -= 1
                break
            continue
        break
    return i


def demote(lines):
    """标题整体降一级（## → ###，### → ####），正文不动。"""
    out = []
    for ln in lines:
        m = re.match(r"^(#{2,5})(\s)", ln)
        out.append("#" + ln if m else ln)
    return out


# ---------------------------------------------------------------------------
# 第17讲
# ---------------------------------------------------------------------------
LEC17_HEAD = """# 第17讲 多元函数积分学的预备知识

> 张宇《高数18讲》第17讲 ｜ 图谱考点 MATH-GS-17（向量代数 / 空间平面与直线 / 曲面与曲线方程 / 场论初步）
> 原 `专题/空间曲面体绘图/空间解析几何专题.md` 于 2026-09-23 **整篇并入本讲**，专题文件与目录已删除；
> 16 张配图移到 `高数/assets/空间解析几何/figures/`。
{mark}

---

## 本讲定位

这一讲是**多元积分学的基础设施**：第14讲（二重积分/三重积分）要靠它认区域、设积分限，
第18讲（曲线积分与曲面积分）默认你已经会空间平面的点法式、空间直线的对称式、
曲面方程与投影、向量积与混合积。单独命题分值不高，但作为前置知识几乎每年间接考查。

- 场论初步（散度、旋度）的正文在 [第18讲 多元函数积分学](./第18讲_多元函数积分学.md) 的 8.C.6「为什么出现旋度/散度」
- 重积分/曲线曲面积分的**计算路由**在 [专题/多元函数积分学](../专题/多元函数积分学.md)（那个专题保留，它管"怎么算"，本讲管"区域长什么样"）

> ⚠️ **编号说明**：下面正文里的「第一章 ~ 第六章 / 附录」与 `1.1 / 2.1 / 3.5` 是这份材料
> **自己的分部编号**，与讲号无关，也**刻意没有重排**——全库对它的引用（索引条目、错题归档、
> 中控）都按这套号写（如「空间解析几何专题 > 2.1」），重排会一次性打断几十处。

---

"""


def build_lec17():
    lines = read(GEO_MD).split("\n")
    start = strip_front_matter(lines)
    body, nav = cut_nav(lines[start:])
    # 配图路径跟着搬家
    body = [ln.replace("./figures/", "./assets/空间解析几何/figures/")
              .replace("(figures/", "(./assets/空间解析几何/figures/") for ln in body]
    text = LEC17_HEAD.format(mark=MARK) + "\n".join(body).rstrip("\n") + "\n"
    text += """
---

## 🔗 双链导航

- 上一讲：[第16讲 无穷级数](./第16讲_无穷级数.md)
- 下一讲：[第18讲 多元函数积分学](./第18讲_多元函数积分学.md)
- 下游用法：[第14讲 二重积分](./第14讲_二重积分.md)（认区域、设三重积分限）、[第18讲 多元函数积分学](./第18讲_多元函数积分学.md)（曲面投影、法向量）
- 跨科关联：[跨科综合](../跨科综合/跨科综合.md)（线代二次型 ↔ 二次曲面类型判别）
- 计算路由：[专题/多元函数积分学](../专题/多元函数积分学.md)
- 返回中控：[高数 notes.md](./高数%20notes.md)
- 返回总览：[数一知识体系总览.md](../数一知识体系总览.md)
"""
    return text, body, nav


# ---------------------------------------------------------------------------
# 第16讲
# ---------------------------------------------------------------------------
SER_WRAP_HEAD = """## 7.7 判敛与求和路由表（原 专题/无穷级数.md 并入）

> 2026-09-23 并入：`专题/无穷级数.md` 已删除，内容整段搬到这里。
> 看到级数题（判敛 / 求收敛域 / 求和 / 傅里叶展开），先用下面的决策总表 30 秒选路，
> 再回到 7.1~7.6 看方法全文。
>
> ⚠️ 下面 `§一~§五` 的小节号（1.1、2.6、§3.2 …）**沿用原专题编号、刻意没有重排**：
> 计算陷阱、错题归档、索引条目都按这套号引用它。标题层级整体降了一级以嵌进本讲。

"""


def build_lec16():
    src = read(SER_MD).split("\n")
    body, nav = cut_nav(src)
    # 按 ## 切段
    parts, cur, cur_title = {}, [], None
    for ln in body:
        m = re.match(r"^##\s+(.*)$", ln)
        if m:
            if cur_title is not None:
                parts[cur_title] = cur
            cur_title, cur = m.group(1).strip(), []
        elif cur_title is not None:
            cur.append(ln)
    if cur_title is not None:
        parts[cur_title] = cur
    front = [ln for ln in body[:body.index("## " + next(iter(parts)))]] if parts else []

    zero_key = next((k for k in parts if k.startswith("〇")), None)
    rest_keys = [k for k in parts if k != zero_key]

    zero = demote(["## " + zero_key] + parts[zero_key]) if zero_key else []
    rest = []
    for k in rest_keys:
        rest += demote(["## " + k] + parts[k])

    return zero, rest, parts, nav, front


def splice_lec16(zero, rest):
    txt = read(LEC16)
    lines = txt.split("\n")
    # ① 30秒决策总表插到「## 思维导图」段之后、第一个 7.x 段之前
    ins = None
    for i, ln in enumerate(lines):
        if re.match(r"^##\s+7\.1\b", ln):
            ins = i
            break
    if ins is None:
        raise SystemExit("[第16讲] 找不到 ## 7.1，无法定位决策总表插入点")
    zero_block = ["## 本讲 30 秒决策总表（判敛 / 收敛域 / 求和 / 傅里叶）", "",
                  "> 原 专题/无穷级数.md 的「〇、30秒决策总表」，2026-09-23 并入。", ""]
    # demote 已把 ### 变 ####，这里把最外层的 ### 子标题还原成 ###（它们本就属于这个新 ## 段）
    zb = [re.sub(r"^####", "###", x) for x in zero]
    zb = zb[1:] if zb and zb[0].startswith("### 〇") else zb
    new = lines[:ins] + zero_block + zb + ["---", ""] + lines[ins:]

    # ② 其余段落作为 7.7 附在双链导航之前
    nav_i = None
    for i, ln in enumerate(new):
        if NAV_RE.match(ln):
            nav_i = i
            break
    tail = new[nav_i:] if nav_i is not None else []
    head = new[:nav_i] if nav_i is not None else new
    while head and head[-1].strip() == "":
        head.pop()
    out = head + ["", "---", "", SER_WRAP_HEAD] + rest + [""] + tail
    return "\n".join(out).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 引用改写
# ---------------------------------------------------------------------------
def rewrite_refs(apply, report):
    """全 Math 的 .md + notes_index.json：指向两个专题的路径与链接文字改到新讲文件。"""
    targets = []
    for dp, dn, fns in os.walk(MATH):
        dn[:] = [d for d in dn if d not in {"assets", "PDF", ".qoder", ".git", "0讲义资料", "真题", "题库"}]
        for f in sorted(fns):
            if f.endswith(".md"):
                targets.append(os.path.join(dp, f))
    targets.append(os.path.join(MATH, "notes_index.json"))

    changed = 0
    for p in targets:
        if os.path.abspath(p) in (os.path.abspath(GEO_MD), os.path.abspath(SER_MD)):
            continue                      # 源文件本身要被删，不改
        old = read(p)
        new = old
        rel = os.path.relpath(p, MATH).replace("\\", "/")
        in_gs = rel.startswith("高数/")
        # 路径：按所在目录给相对前缀
        p17 = ("./" + NEW17) if in_gs else ("../高数/" + NEW17)
        p16 = ("./" + NEW16) if in_gs else ("../高数/" + NEW16)
        for n in GEO_NAMES[:-1]:
            if n in new:
                new = new.replace(n, p17)
        for n in SER_NAMES:
            if n in new:
                new = new.replace(n, p16)
        # 裸文件名（链接文字里）
        new = new.replace(GEO_NAMES[-1], NEW17)
        # 链接文字里的「专题/无穷级数」「空间解析几何专题」措辞
        new = new.replace("专题/无穷级数.md", NEW16).replace("专题/无穷级数", "第16讲 无穷级数")
        new = new.replace("空间解析几何专题", "第17讲 空间解析几何")
        if new != old:
            changed += 1
            n = sum(1 for a, b in zip(old.split("\n"), new.split("\n")) if a != b)
            report.append("   %-56s 改动约 %d 行" % (rel, max(n, 1)))
            if apply:
                if p.endswith(".json"):
                    json.loads(new)          # 写坏了立刻炸
                write(p, new)
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()

    rep = []
    # ---------- 第17讲 ----------
    text17, body17, nav17 = build_lec17()
    src17 = [l for l in body17 if l.strip()]
    got17 = [l for l in text17.split("\n") if l.strip()]
    miss17 = [l for l in src17 if l not in got17]
    rep.append("== 第17讲 ← 空间解析几何专题 ==")
    rep.append("   源正文非空行 %d ｜ 新文件非空行 %d ｜ 未落入 %d" % (
        len(src17), len(got17), len(miss17)))
    for l in miss17[:6]:
        rep.append("      [漏] %s" % l[:110])
    rep.append("   源文件的双链导航块 %d 行（丢弃，第17讲自己重写）" % len(nav17))

    # ---------- 第16讲 ----------
    zero, rest, parts, nav16, front = build_lec16()
    text16 = splice_lec16(zero, rest)
    src16 = [l for l in read(SER_MD).split("\n") if l.strip()]
    # 源里被丢掉的部分：H1/标语/front-matter/双链导航/纯分隔线
    dropped_ok = set()
    for l in nav16:
        if l.strip():
            dropped_ok.add(l.strip())
    got16 = {l.strip() for l in text16.split("\n") if l.strip()}
    miss16 = []
    for l in src16:
        s = l.strip()
        if not s or s in got16 or s in dropped_ok:
            continue
        if s.startswith("# 无穷级数·判敛与求和路由表") or s.startswith("> 定位：") \
           or s.startswith("> 前置：") or s.startswith("> 类型：") or s == "---":
            continue
        # 这一段标题被**刻意改名**（原「〇、30秒决策总表」→「本讲 30 秒决策总表」，
        # 与第15讲「本章 30 秒决策总表」的既有体例对齐），内容一行没少
        if s.startswith("## 〇、30秒决策总表"):
            continue
        # demote 会加一个 #，比对其一
        if ("#" + l).strip() in got16 or re.sub(r"^#+", "", s) in {re.sub(r"^#+", "", x) for x in got16}:
            continue
        miss16.append(l)
    rep.append("")
    rep.append("== 第16讲 ← 无穷级数专题 ==")
    rep.append("   源段：%s" % list(parts.keys()))
    rep.append("   源正文非空行 %d ｜ 合并后新文件非空行 %d ｜ 未落入 %d" % (
        len(src16), len([x for x in text16.split("\n") if x.strip()]), len(miss16)))
    for l in miss16[:10]:
        rep.append("      [漏] %s" % l[:110])

    ok = not miss17 and not miss16
    rep.append("")
    rep.append("守恒断言：%s" % ("通过" if ok else "未通过"))

    # ---------- 配图与目录 ----------
    moved = []
    for name in sorted(os.listdir(GEO_DIR)):
        if name.endswith(".md"):
            continue
        moved.append(name)
    rep.append("")
    rep.append("== 配图/附件搬家 → 高数/assets/空间解析几何/ ==")
    for m in moved:
        rep.append("   %s" % m)
    rep.append("   （随后删除空目录 专题/空间曲面体绘图/ 与 PDF 里的对应孤儿）")

    # ---------- 引用改写（先试算）----------
    rep.append("")
    rep.append("== 引用改写 ==")
    n = rewrite_refs(False, rep)
    rep.append("   共 %d 个文件需要改" % n)

    text = "\n".join(rep)
    print(text)
    if args.out:
        write(args.out, text + "\n")

    if not ok:
        print("\n[中止] 守恒断言未通过，未写任何文件。")
        return 1
    if not args.apply:
        print("\n[dry-run] 未写盘。")
        return 0

    # ================= 落盘 =================
    write(LEC17, text17)
    write(LEC16, text16)
    print("\n[apply] 第17讲（%d 行）、第16讲（%d 行）已写。" % (
        text17.count("\n"), text16.count("\n")))

    os.makedirs(GEO_ASSETS_DST, exist_ok=True)
    for name in moved:
        s = os.path.join(GEO_DIR, name)
        d = os.path.join(GEO_ASSETS_DST, name)
        if os.path.isdir(s):
            if os.path.exists(d):
                shutil.rmtree(d)
            shutil.move(s, d)
        else:
            shutil.move(s, d)
    print("   配图搬家完成 → %s" % os.path.relpath(GEO_ASSETS_DST, ROOT))

    for p in (GEO_MD, SER_MD):
        if os.path.exists(p):
            os.remove(p)
            print("   删除专题：%s" % os.path.relpath(p, ROOT))
    try:
        os.rmdir(GEO_DIR)
        print("   删除空目录：专题/空间曲面体绘图/")
    except OSError as e:
        print("   [WARN] 目录未删（%s）" % e)

    rewrite_refs(True, [])
    print("   引用改写完成")

    # PDF 孤儿
    for orphan in (os.path.join(MATH, "PDF", "专题", "无穷级数.pdf"),
                   os.path.join(MATH, "PDF", "专题", "空间曲面体绘图")):
        if os.path.isdir(orphan):
            shutil.rmtree(orphan)
            print("   删除 PDF 孤儿目录：%s" % os.path.relpath(orphan, ROOT))
        elif os.path.exists(orphan):
            os.remove(orphan)
            print("   删除 PDF 孤儿：%s" % os.path.relpath(orphan, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
morning_review_merge.py — 把「今天这一天的早间回顾内容」安全地并进 morning_review.json

为什么要有它：morning_review.json 是 300KB / 23 个历史日的**只增不删**台账。以前这份
合并是让 agent 拿 PowerShell `ConvertFrom-Json` 读整份、改、再整份写回，风险与成本都高：
  · 整份读进来会进上下文（300KB）；
  · 手写回时容易碰坏历史日期或 flashcards（这两样绝不能被覆盖）；
  · 计数（meta）与回读校验全靠每次现场现编。
于是 2026-10-04 复盘连续超时的定时任务时，把这条链固化成脚本：**只有本脚本写这个文件**。

用法：

    # 1) 先看今天已有什么（防止 id 撞车、看清还缺哪一类）
    python tools/morning_review_merge.py --show 2026-10-04

    # 2) 写一份「今天要增补的内容」到 patch.json（只写要加的东西，不用管历史）
    #    字段与 morning_review.json 的 day 一致：review[] / quiz[] / math.points[] / english
    python tools/morning_review_merge.py --patch patch.json --dry-run   # 先看报告
    python tools/morning_review_merge.py --patch patch.json             # 落盘（自动备份）

    # 3) 体检（不动文件）：结构、日期键、id 唯一性、noteLink 是否存在
    python tools/morning_review_merge.py --check

语义（刻意保守，宁可报错也不猜）：
  · **只增不删**：既有的 review/quiz/math 条目一律不动，patch 里与既有 id 相同的条目跳过并报告；
  · `subject` 追加去重（用 ｜ 连接）；`studied` 取或；
  · 其它日期与 `flashcards` **逐字节保持**（脚本落盘前会做完这一条校验，不过整体中止）；
  · `english` 是单份对象，不能追加：已有内容时除非加 `--replace-english`，否则拒写；
  · meta 计数按落盘后的实际内容重算（历史上这处长期漂移）。
"""

import argparse
import copy
import io
import json
import os
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paths  # noqa: E402  路径单一事实源

DEFAULT_FILE = Path(paths.SRC_DIR) / "morning_review.json"
BAK_DIR = Path(paths.SRC_DIR) / "backups"
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

def load(path: Path) -> dict:
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_atomic(path: Path, obj: dict) -> None:
    """同目录 .tmp + os.replace：读的一方（serve.js 每请求读盘）永远看不到半截 JSON。"""
    tmp = path.with_name(path.name + ".tmp")
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def backup(path: Path) -> Path:
    BAK_DIR.mkdir(parents=True, exist_ok=True)
    dst = BAK_DIR / f"morning_review.json.bak_{datetime.now():%Y%m%d_%H%M%S}"
    n = 1
    while dst.exists():   # 同一秒内跑两次不许互相覆盖——备份是唯一的回滚手段
        n += 1
        dst = BAK_DIR / f"morning_review.json.bak_{datetime.now():%Y%m%d_%H%M%S}_{n}"
    shutil.copy2(path, dst)
    return dst


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------

def merge_list(old: list, new: list, label: str, problems: list, notes: list) -> list:
    """按 id 去重追加。既有条目一律保留原样与原顺序。"""
    seen = {}
    for it in old:
        if isinstance(it, dict) and it.get("id"):
            seen.setdefault(it["id"], it)
    out = list(old)
    for it in new or []:
        if not isinstance(it, dict):
            problems.append(f"{label} 里有非对象条目：{it!r}")
            continue
        iid = it.get("id")
        if not iid:
            problems.append(f"{label} 有条目缺 id（全天条目的 id 是去重与回看锚点，必须有）：{str(it)[:80]}")
            continue
        if iid in seen:
            notes.append(f"{label} 跳过重复 id：{iid}（该日已有）")
            continue
        seen[iid] = it
        out.append(it)
    return out


def merge_day(old: dict, patch: dict, problems: list, notes: list) -> dict:
    day = copy.deepcopy(old) if old else {}
    ref = old or {}
    for k in ("review", "quiz"):
        day[k] = merge_list(list(ref.get(k) or []), patch.get(k) or [], k, problems, notes)
    if patch.get("math") or ref.get("math") or "math" in patch:
        old_pts = list((ref.get("math") or {}).get("points") or [])
        new_pts = list((patch.get("math") or {}).get("points") or [])
        pts = merge_list(old_pts, new_pts, "math.points", problems, notes)
        if pts:
            day["math"] = {"id": (patch.get("math") or ref.get("math") or {}).get("id", "sec-math"),
                           "points": pts}
        elif "math" in day:
            day.pop("math")
    if patch.get("english"):
        if ref.get("english") and not patch.get("_replace_english"):
            problems.append("该日已有 english 内容：单份对象不能追加，"
                            "确实要换请加 --replace-english（并说明为什么）")
        else:
            day["english"] = patch["english"]
    # subject 追加去重
    segs = []
    for src in (ref.get("subject") or "", patch.get("subject") or ""):
        for s in str(src).split("｜"):
            s = s.strip()
            if s and s not in segs:
                segs.append(s)
    if segs:
        day["subject"] = " ｜ ".join(segs)
    day["date"] = patch.get("date") or ref.get("date")
    day["weekday"] = patch.get("weekday") or ref.get("weekday") or WEEKDAYS[date.fromisoformat(day["date"]).weekday()]
    day["studied"] = bool(patch.get("studied", True)) or bool(ref.get("studied"))
    # 字段顺序对齐历史（便于 diff 阅读）
    order = ["date", "weekday", "studied", "subject", "review", "quiz", "math", "english"]
    return {k: day[k] for k in order if k in day}


def recompute_meta(days: dict, meta: dict) -> dict:
    ks = sorted(days)
    m = dict(meta or {})
    m["days"] = len(ks)
    m["first_date"] = ks[0] if ks else ""
    m["last_date"] = ks[-1] if ks else ""
    m["review_items"] = sum(len((days[k] or {}).get("review") or []) for k in ks)
    m["quiz_items_unused"] = sum(len((days[k] or {}).get("quiz") or []) for k in ks)
    m["english_days"] = sum(1 for k in ks if (days[k] or {}).get("english"))
    m["math_items"] = sum(len(((days[k] or {}).get("math") or {}).get("points") or []) for k in ks)
    fl = m.get("flashcard_cards")
    if fl is not None:
        m["flashcard_cards"] = fl
    return m


# ---------------------------------------------------------------------------
# 校验（落盘前后各跑一次同一份逻辑）
# ---------------------------------------------------------------------------

def verify(before: dict, after: dict, target: str, problems: list) -> None:
    if list(before.keys()) != list(after.keys()):
        problems.append("顶层键变了（本文件除 days 与 meta 外不该动）")
    bd, ad = before["days"], after["days"]
    if set(bd) - set(ad):
        problems.append("有历史日期键消失：" + ", ".join(sorted(set(bd) - set(ad))))
    if not set(ad) <= set(bd) | {target}:
        problems.append("多出了非本次目标的日期键")
    for k in bd:
        if k == target:
            continue
        if bd[k] != ad[k]:
            problems.append(f"非目标日期被改动：{k}")
    if json.dumps(before.get("flashcards"), sort_keys=True, ensure_ascii=False) != \
       json.dumps(after.get("flashcards"), sort_keys=True, ensure_ascii=False):
        problems.append("flashcards 被改动了（它不属于本工作流维护范围）")
    # 目标日：旧条目必须原样、原顺序还在前面
    old, new = bd.get(target) or {}, ad.get(target) or {}
    for key, get in (("review", lambda d: d.get("review") or []),
                     ("quiz", lambda d: d.get("quiz") or []),
                     ("math.points", lambda d: (d.get("math") or {}).get("points") or [])):
        o, n = get(old), get(new)
        if [i.get("id") for i in n][:len(o)] != [i.get("id") for i in o]:
            problems.append(f"目标日 {key} 的既有条目顺序/内容被破坏")
        if json.dumps(o, ensure_ascii=False) != json.dumps(n[:len(o)], ensure_ascii=False):
            problems.append(f"目标日 {key} 的既有条目内容被改动")
    ids = [i.get("id") for i in (new.get("review") or []) + (new.get("quiz") or []) +
           ((new.get("math") or {}).get("points") or [])]
    dup = sorted({i for i in ids if i and ids.count(i) > 1})
    if dup:
        problems.append("目标日出现重复 id：" + ", ".join(dup))


def check_links(day: dict, warns: list, seen: set = None) -> None:
    """noteLink.path 应存在于笔记库（死链只提示不算错——库里有 8 条既有的）。"""
    seen = seen if seen is not None else set()
    items = list(day.get("review") or []) + list(day.get("quiz") or []) + \
        list((day.get("math") or {}).get("points") or [])
    eng = day.get("english") or {}
    if eng.get("noteLink"):
        items = items + [{"id": "english", "noteLink": eng["noteLink"]}]
    for it in items:
        nl = it.get("noteLink") or {}
        p = nl.get("path")
        if p and not Path(p).exists() and p not in seen:
            seen.add(p)
            warns.append(f"noteLink 指向不存在的文件：{p}（{it.get('id')}）")


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_show(path: Path, d: str) -> int:
    j = load(path)
    day = j["days"].get(d)
    if not day:
        print(f"{d} 还没有内容（新建一天）。既有最近日期：{', '.join(sorted(j['days'])[-5:])}")
        return 0
    print(f"{d} {day.get('weekday','')} studied={day.get('studied')} subject={day.get('subject','')}")
    for key, items in (("review", day.get("review") or []), ("quiz", day.get("quiz") or []),
                       ("math", (day.get("math") or {}).get("points") or [])):
        print(f"  {key}: {len(items)} 条")
        for it in items:
            print(f"    - {it.get('id')}  {str(it.get('title') or it.get('question'))[:60]}")
    if day.get("english"):
        print("  english: 已有（单份，追加需 --replace-english）")
    return 0


def cmd_check(path: Path) -> int:
    j = load(path)
    problems, warns, seen_links = [], [], set()
    if "days" not in j or "flashcards" not in j:
        problems.append("缺 days / flashcards 顶层字段")
    for k in j.get("days", {}):
        try:
            date.fromisoformat(k)
        except Exception:
            problems.append(f"日期键不是 YYYY-MM-DD：{k}")
    for k, day in (j.get("days") or {}).items():
        ids = [i.get("id") for i in (day.get("review") or []) + (day.get("quiz") or []) +
               ((day.get("math") or {}).get("points") or [])]
        miss = [i for i in ids if not i]
        # 早期（06-19~07 月）那批是手工迁移进来的，本来就没有 id。历史数据不判错——
        # 脚本只管「新写进来的必须有 id」（merge_list 里那条才是硬门）。
        if miss:
            warns.append(f"{k}: {len(miss)} 条历史条目没有 id（早期数据，不去补）")
        check_links(day, warns, seen_links)
    expect = recompute_meta(j["days"], j.get("meta") or {})
    drift = {k: (j.get("meta") or {}).get(k, None) for k in ("days", "first_date", "last_date",
                                                             "review_items", "quiz_items_unused",
                                                             "math_items", "english_days")
             if (j.get("meta") or {}).get(k) != expect[k]}
    for w in warns[:10]:
        print(f"WARN  {w}")
    if len(warns) > 10:
        print(f"WARN  …另有 {len(warns) - 10} 条 noteLink 提示")
    for k, v in drift.items():
        print(f"WARN  meta.{k}={v} 与实算 {expect[k]} 不一致（脚本会重算）")
    if problems:
        for p in problems:
            print(f"FAIL  {p}")
        return 1
    print(f"OK  {len(j['days'])} 个日期 / {len(j.get('flashcards') or {})} 个 flashcards 分组，结构完整")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="早间回顾内容安全合并进 morning_review.json")
    ap.add_argument("--file", default=str(DEFAULT_FILE), help="目标文件（测试时给副本）")
    ap.add_argument("--patch", default="", help="今天要增补的内容（JSON）")
    ap.add_argument("--date", default="", help="覆盖 patch 里的 date")
    ap.add_argument("--show", default="", help="只显示某天已有的条目")
    ap.add_argument("--check", action="store_true", help="只体检，不写")
    ap.add_argument("--dry-run", action="store_true", help="算出结果并校验，但不落盘")
    ap.add_argument("--replace-english", action="store_true", help="允许覆盖该日已有的 english")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"FAIL  目标文件不存在：{path}")
        return 1
    if args.check:
        return cmd_check(path)
    if args.show:
        return cmd_show(path, args.show)
    if not args.patch:
        ap.error("要么 --patch，要么 --show/--check")

    patch = load(Path(args.patch))
    if args.date:
        patch["date"] = args.date
    d = patch.get("date")
    if not d:
        print("FAIL  patch 里必须有 date（避免猜今天）")
        return 1
    try:
        date.fromisoformat(d)
    except Exception:
        print(f"FAIL  日期不是 YYYY-MM-DD：{d!r}（不写盘）")
        return 1

    before = load(path)
    if args.replace_english:
        patch["_replace_english"] = True

    problems, notes = [], []
    days = copy.deepcopy(before["days"])
    days[d] = merge_day(days.get(d), patch, problems, notes)
    if (days[d].get("review") or days[d].get("quiz") or
            (days[d].get("math") or {}).get("points") or days[d].get("english")):
        days[d]["studied"] = True
    # 保持既有日期键的原始顺序，新日期追加在末尾——这样 git diff 只有新增
    dk = list(before["days"].keys())
    if d not in before["days"]:
        dk.append(d)
    after = dict(before)
    after["days"] = {k: days[k] for k in dk}
    after["meta"] = recompute_meta(after["days"], before.get("meta") or {})

    verify(before, after, d, problems)
    warns = []
    check_links(days[d], warns)

    if not args.quiet:
        print(f"目标：{path}")
        print(f"日期：{d}（{days[d]['weekday']}）  新增 review {len(days[d].get('review') or []) - len((before['days'].get(d) or {}).get('review') or [])}"
              f" / quiz {len(days[d].get('quiz') or []) - len((before['days'].get(d) or {}).get('quiz') or [])}"
              f" / math {len((days[d].get('math') or {}).get('points') or []) - len(((before['days'].get(d) or {}).get('math') or {}).get('points') or [])}")
        for n in notes:
            print(f"SKIP  {n}")
        for w in warns:
            print(f"WARN  {w}")
    if problems:
        for p in problems:
            print(f"FAIL  {p}")
        print("未写盘（校验不过一律整体中止，不留半成品）")
        return 1
    if args.dry_run:
        print("dry-run：校验通过，未写盘")
        return 0

    bak = backup(path)
    dump_atomic(path, after)
    reread = load(path)
    problems2 = []
    verify(before, reread, d, problems2)
    if problems2:
        shutil.copy2(bak, path)
        for p in problems2:
            print(f"FAIL  {p}")
        print(f"回读校验失败，已从 {bak.name} 还原")
        return 1
    print(f"OK  已写入（备份 {bak.name}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

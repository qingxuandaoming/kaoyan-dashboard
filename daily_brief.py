#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""daily_brief.py — 每日备考决策简报（2026-09-19）

## 为什么要有这个文件

原来的每日任务生成，取数只走 `daily_tasks.py context`，而那份 JSON 里关于
「学得怎么样」的信息只有两个数字：近 1 天 / 7 天的**练习条数**。于是布置任务
只能顺着昨天任务文本往下猜，实测后果（2026-09-15 ~ 09-19 的真实记录）：

  · 「数据结构 树与二叉树」09-15 排过一次（#33，未完成），09-19 又原样排一遍（#58）
  · 「概率·数字特征」09-15/09-16/09-17 连排三天（#31/#37/#43）
  · 「英语阅读精练」09-15 排 2 篇、09-16 排 1 篇、09-18 被用户删掉（#53）
  · 任务里写「刷 8 题」的考点，题库里可能一张卡都没有（数学 165 个有权重考点里
    只有 13 个有题）——这种任务根本没法在大盘里执行，也就无从反馈

与此同时，三份**已经存在**的证据一直没被消费：

  · `review_log`：275 条真实答题记录，含 rating（1=Again）与 `chosen`（他实际选错的选项）
  · `explain_log`：92 条 AI 解析/追问记录——微分方程 18 次、词根词缀 22 次、PV 同步 6 次
  · 笔记文件 mtime：数学 09-19 06:39 还在写、英语 09-19 21:49 在补词义辨析、
    408 停在 09-17、政治停在 09-17

而 context 里被标成「优先级最高的素材」的 `review_hot_causes`，来源是
`Review/_patterns.json` 的复盘错因画像——那份文件今天重跑过，`errors: 0`：
复盘页从来只有 1 个会话、0 条错因。也就是说这个「最高优先级」入口
**在结构上永远是空的**，agent 拿不到就退回到猜。

## 这一版的做法

不重造规划器，而是给 `daily_planner` 已有的决策（阶段 → 可用时长 → 分科时长 →
考点优先级）**补上它没有的五条证据通道**，并把结果一次性交给 agent：

  通道 1 错误证据   review_log：按考点的错次/Again/正确率 + 错选分布（chosen）
  通道 2 卡点证据   explain_log：他在哪里反复提问、问了什么
  通道 3 载体证据   该考点在题库有几张卡（今日到期几张）、对应笔记文件在不在、多久没动
  通道 4 一贯性证据 近 7 天任务历史：这条线排过几次/做完没有/被删过没有（相似度匹配）
  通道 5 容量证据   番茄钟真实时长、各科完成率、各科到期卡量

输出 `brief`：候选池按证据分层（P1 已证明会丢分 / P2 高权重零覆盖 /
P3 笔记陈旧的主线章节 / P4 冷却科目），每条都带**载体**与**已被排过几次**，
再附上「不许原样重排」的硬规则。任务文本必须能追溯到这里的某一个字段。

命令行：
    python daily_brief.py --date today            # JSON（给 agent）
    python daily_brief.py --date today --md       # Markdown（给人看）
"""

import argparse
import glob
import io
import json
import os
import re
import sqlite3
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

BASE_DIR = Path(__file__).resolve().parent
ROOT = BASE_DIR.parent
DB_PATH = Path(os.environ.get("DB_PATH") or (BASE_DIR / "question_bank.db"))
SUBJECTS = ["408", "政治", "数学一", "英语一"]

# 任务文本里出现这些词，就认为在说这一科（用于「一贯性」匹配）
SUBJECT_HINTS = {
    "408": ["408", "数据结构", "计组", "操作系统", "计算机网络", "DS", "CO", "OS", "CN",
            "KMP", "Cache", "PV", "页表", "TCP", "UDP"],
    "数学一": ["数学", "高数", "线代", "概率", "微分方程", "级数", "积分", "矩阵", "特征值",
              "二次型", "方程组", "估计", "大数定律"],
    "政治": ["政治", "马原", "毛中特", "史纲", "思修", "习思想", "帽子题", "肖1000", "肖秀荣",
            "腿姐", "徐涛", "唯物", "矛盾"],
    "英语一": ["英语", "词汇", "单词", "阅读", "完型", "完形", "翻译", "作文", "长难句", "语法",
              "词义辨析"],
}

WEAK_LOW_SAMPLE = 5          # 少于这个练习次数，正确率只当参考（与大盘同口径）
RECENT_DAYS = 14             # 「最近」窗口


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------

def resolve_date(arg: str) -> str:
    a = (arg or "today").strip().lower()
    today = date.today()
    if a in ("today", "今天", ""):
        return today.isoformat()
    if a in ("tomorrow", "明天"):
        return (today + timedelta(days=1)).isoformat()
    if a in ("yesterday", "昨天"):
        return (today - timedelta(days=1)).isoformat()
    return date.fromisoformat(a).isoformat()


def connect():
    if not DB_PATH.exists():
        raise SystemExit(f"数据库不存在：{DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def normalize_tid(tid: str) -> str:
    """题库把图谱考点拆成子考点（408-DS-04-02），图谱粒度是前三段。"""
    return "-".join(str(tid or "").split("-")[:3])


def subject_of_tid(tid: str) -> str:
    t = str(tid or "")
    if t.startswith("MATH-"):
        return "数学一"
    if t.startswith("ENG-"):
        return "英语一"
    if t.startswith("POL-"):
        return "政治"
    if t.startswith("408-"):
        return "408"
    return ""


def load_subjects_json() -> dict:
    try:
        return json.loads((BASE_DIR / "subjects.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def prefix_index(sj: dict) -> dict:
    """{"408-DS": {subject, sub, subname, dir, chapters}} —— 用来把考点 ID 对到笔记目录。"""
    idx = {}
    for subj in (sj.get("subjects") or []):
        for sub in (subj.get("subs") or []):
            if not sub.get("prefix"):
                continue
            idx[sub["prefix"]] = {
                "subject": subj.get("id"),
                "notes_dir": subj.get("notes_dir"),
                "sub": sub.get("key"),
                "sub_name": sub.get("name"),
                "dir": sub.get("dir"),
                "chapters": sub.get("chapters") or [],
            }
    return idx


def md_files(d: Path, skip=(".obsidian", ".trash", ".git", "node_modules", "assets", "uploads",
                            "sessions", ".qoder")):
    out = []
    if not d.is_dir():
        return out
    for r, ds, fs in os.walk(d):
        ds[:] = [x for x in ds if x not in skip]
        for f in fs:
            if f.lower().endswith(".md"):
                fp = Path(r) / f
                try:
                    st = fp.stat()
                except OSError:
                    continue
                out.append({"path": str(fp), "rel": str(fp.relative_to(ROOT)).replace("\\", "/"),
                            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                            "size": st.st_size})
    return out


# ---------------------------------------------------------------------------
# 通道 1：错误证据（review_log）
# ---------------------------------------------------------------------------

def err_evidence(conn, d: str):
    """按考点（图谱粒度）汇总答题表现 + 错选分布。

    chosen 是金矿：它记录他**实际选了什么**，能区分「概念混淆」和「算错」，
    比一个"答错了"有用得多（周报任务里已经这么用了，但每日任务从来没看过）。
    """
    rows = conn.execute("""
        SELECT q.topic_id AS tid, t.subject AS subject, t.name AS name, t.exam_weight AS weight,
               rl.rating AS rating, rl.chosen AS chosen, rl.review_date AS rd
        FROM review_log rl
        JOIN questions q ON q.id = rl.question_id
        LEFT JOIN topics t ON t.id = q.topic_id
        WHERE rl.review_date >= datetime('now','localtime', ?)
    """, (f"-{RECENT_DAYS} days",)).fetchall()

    agg = {}
    for r in rows:
        if not r["tid"]:
            continue
        k = normalize_tid(r["tid"])
        a = agg.setdefault(k, {"tid": k, "subject": subject_of_tid(k), "name": r["name"] or "",
                               "weight": r["weight"] or 0, "n": 0, "wrong": 0, "again": 0,
                               "last_wrong": "", "chosen": {}})
        a["n"] += 1
        if r["rating"] <= 2:
            a["wrong"] += 1
            if r["rating"] == 1:
                a["again"] += 1
            a["last_wrong"] = max(a["last_wrong"], r["rd"] or "")
            ch = (r["chosen"] or "").strip()
            if ch:
                ch = re.sub(r"\s+", " ", ch)[:60]
                a["chosen"][ch] = a["chosen"].get(ch, 0) + 1

    # 全库练习量（区分「一直不会」和「最近才掉」）
    total = {r["tid"]: r["n"] for r in conn.execute("""
        SELECT q.topic_id AS tid, COUNT(*) AS n FROM review_log rl
        JOIN questions q ON q.id = rl.question_id GROUP BY q.topic_id""") if r["tid"]}
    total_norm = {}
    for tid, n in total.items():
        k = normalize_tid(tid)
        total_norm[k] = total_norm.get(k, 0) + n

    out = {}
    for k, a in agg.items():
        alln = total_norm.get(k, a["n"])
        a["total_all"] = alln
        a["accuracy"] = round((a["n"] - a["wrong"]) / a["n"] * 100) if a["n"] else None
        a["low_sample"] = alln < WEAK_LOW_SAMPLE
        a["chosen_top"] = sorted(a["chosen"].items(), key=lambda kv: -kv[1])[:2]
        a.pop("chosen")
        out[k] = a
    return out


# ---------------------------------------------------------------------------
# 通道 2：卡点证据（explain_log）
# ---------------------------------------------------------------------------

def ask_evidence(conn):
    """他反复问 AI 的考点 —— 说明那里是思维卡点，不是「没练够」而是「没想通」。"""
    out = {}
    rows = conn.execute("""
        SELECT e.topic_id AS tid, e.subject AS subject, e.created_at, e.role, e.question_id,
               substr(replace(e.content, char(10), ' '), 1, 70) AS txt
        FROM explain_log e LEFT JOIN questions q ON q.id = e.question_id
        WHERE e.role = 'user' AND e.topic_id IS NOT NULL AND e.topic_id != ''
        ORDER BY e.id""").fetchall()
    for r in rows:
        k = normalize_tid(r["tid"])
        a = out.setdefault(k, {"tid": k, "asks": 0, "last_ask": "", "samples": []})
        a["asks"] += 1
        a["last_ask"] = max(a["last_ask"], (r["created_at"] or "")[:10])
        if len(a["samples"]) < 2 and r["txt"]:
            a["samples"].append(r["txt"])
    return out


def ask_by_subject(conn):
    rows = conn.execute("""
        SELECT COALESCE(NULLIF(subject,''),'(未分类)') AS s, COUNT(*) n
        FROM explain_log WHERE role='user' GROUP BY s ORDER BY n DESC""").fetchall()
    return {r["s"]: r["n"] for r in rows}


# ---------------------------------------------------------------------------
# 通道 3：载体证据（题库卡片 + 笔记文件）
# ---------------------------------------------------------------------------

def card_stats(conn, d: str):
    """按考点：几张卡、今天到期几张、未来 7 天到期几张。"""
    out = {}
    rows = conn.execute("""
        SELECT q.topic_id AS tid, cd.due_date AS due, cd.suspended AS susp, cd.leech AS leech,
               cd.state AS state
        FROM cards cd JOIN questions q ON q.id = cd.question_id""").fetchall()
    d7 = (date.fromisoformat(d) + timedelta(days=7)).isoformat()
    for r in rows:
        if not r["tid"]:
            continue
        k = normalize_tid(r["tid"])
        a = out.setdefault(k, {"cards": 0, "due_now": 0, "due_7d": 0, "suspended": 0, "new": 0})
        a["cards"] += 1
        if r["susp"]:
            a["suspended"] += 1
            continue
        due = (r["due"] or "")[:10]
        if due and due <= d:
            a["due_now"] += 1
        elif due and due <= d7:
            a["due_7d"] += 1
        if str(r["state"]) == "0":
            a["new"] += 1
    return out


def note_state(pfx_idx: dict, d: str):
    """每个前缀对应的笔记目录：文件数、最近改动、陈旧天数、最近在写的几篇。

    这是「跟我的笔记联系起来」的落点：任务的载体要指到这些**真实存在**的文件上。
    """
    today = date.fromisoformat(d)
    out = {}
    for prefix, meta in pfx_idx.items():
        ddir = ROOT / (meta["notes_dir"] or "") / (meta["dir"] or "")
        files = md_files(ddir)
        # 政治/英语这类没有章节子目录的：目录不存在时退回到「一科一个 md」
        if not files:
            subj_dir = ROOT / (meta["notes_dir"] or "")
            cand = [f for f in md_files(subj_dir) if (meta["sub"] or "") in Path(f["rel"]).name]
            files = cand or md_files(subj_dir)
        if not files:
            out[prefix] = {"dir": str(ddir.relative_to(ROOT)).replace("\\", "/"), "files": 0,
                           "exists": False}
            continue
        files.sort(key=lambda f: f["mtime"], reverse=True)
        newest = datetime.fromisoformat(files[0]["mtime"])
        out[prefix] = {
            "dir": str(ddir.relative_to(ROOT)).replace("\\", "/") if ddir.is_dir() else
                   str((ROOT / (meta["notes_dir"] or "")).relative_to(ROOT)).replace("\\", "/"),
            "exists": True,
            "files": len(files),
            "newest": files[0]["rel"],
            "newest_mtime": files[0]["mtime"],
            "stale_days": (today - newest.date()).days,
            "recent": [{"rel": f["rel"], "mtime": f["mtime"], "size": f["size"]}
                       for f in files[:3]],
        }
    return out


def _stem_title(fp: Path) -> str:
    """「第6章_微分方程.md」→「微分方程」"""
    s = fp.stem
    s = re.sub(r"^第?\d+章?[_\-—\s]*", "", s)
    return s.strip()


def _bigram_sim(a: str, b: str) -> float:
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return 2 * len(ba & bb) / (len(ba) + len(bb))


def note_for_topic(tid: str, pfx_idx: dict, notes: dict, topic_name: str = ""):
    """把考点对到**真实存在**的笔记文件（载体）。

    ⚠️ 两套编号不是一套：408 的题库考点第三段就是笔记章号
    （408-OS-02 → 408/OS/第2章_进程管理.md），而数学的考点号是图谱顺序号，
    笔记章号是讲义章号（MATH-GS-15「微分方程」其实在 第6章_微分方程.md）。
    所以数学按**标题相似度**匹配、408 按章号匹配，匹配不到再退回该子科目录里
    最新的文件并如实标注 matched_by，不许假装精确。
    """
    parts = str(tid or "").split("-")
    prefix = "-".join(parts[:2])
    meta = pfx_idx.get(prefix)
    info = {"prefix": prefix, "file": "", "matched_by": "", "stale_days": None,
            "dir_exists": False, "archive": []}
    if not meta:
        return info
    ns = notes.get(prefix) or {}
    info["dir_exists"] = bool(ns.get("exists"))
    info["stale_days"] = ns.get("stale_days")
    ddir = ROOT / (meta["notes_dir"] or "") / (meta["dir"] or "")
    # ⚠️ 用正则卡死「第N章」，不要用 glob 的「第*章*」：英语那边有
    # 「第191节_印章印记.md」，中间的「印章」会被 glob 当成章号匹配上。
    cands = [Path(p) for p in glob.glob(str(ddir / "**" / "第*.md"), recursive=True)
             if re.match(r"^第\d+章", Path(p).name)]
    if not cands:
        cands = [Path(p) for p in glob.glob(str(ddir / "第*.md"))
                 if re.match(r"^第\d+章", Path(p).name)]

    pick = None
    if cands and len(parts) >= 3 and parts[2].isdigit() and meta["subject"] != "数学一":
        ch = int(parts[2])
        seg = [p for p in cands if re.match(rf"^第?0*{ch}章", p.name)]
        if len(seg) == 1:
            pick, info["matched_by"] = seg[0], "章号"
        elif len(seg) > 1:
            pick, info["matched_by"] = max(seg, key=lambda p: _bigram_sim(topic_name, _stem_title(p))), "章号+标题"
    if pick is None and cands:
        scored = sorted((( _bigram_sim(topic_name, _stem_title(p)), p) for p in cands), reverse=True)
        if scored and scored[0][0] >= 0.3:
            pick, info["matched_by"] = scored[0][1], "标题"
    if pick is None and cands:
        pick = max(cands, key=lambda p: p.stat().st_mtime if p.exists() else 0)
        info["matched_by"] = "最近改动（兜底）"

    def rel(p: Path):
        return str(p.relative_to(ROOT)).replace("\\", "/")

    if pick and pick.exists():
        info["file"] = rel(pick)
        try:
            info["stale_days"] = (date.today() -
                                  datetime.fromtimestamp(pick.stat().st_mtime).date()).days
        except OSError:
            pass
    # 错题归档 / 计算陷阱 / 公式速查：他真正落笔的地方，任务产出应该指到这里
    for name in ("错题归档.md", "计算陷阱.md", "公式速查.md", "专题"):
        for base in (ddir, ROOT / (meta["notes_dir"] or "")):
            p = base / name
            if p.is_file() and rel(p) not in info["archive"]:
                info["archive"].append(rel(p))
    if not info["file"] and ns.get("newest"):
        info["file"] = ns["newest"]
        info.setdefault("matched_by", "子科最新（兜底）")
    return info


# ---------------------------------------------------------------------------
# 通道 4：一贯性证据（近 7 天任务历史）
# ---------------------------------------------------------------------------

def _bigrams(s: str):
    s = re.sub(r"[\s，。、：；（）()【】\[\]·—\-—]+", "", str(s or ""))
    return {s[i:i + 2] for i in range(max(len(s) - 1, 0))} or ({s} if s else set())


def text_similarity(a: str, b: str) -> float:
    """中文短句的粗相似度（bigram Dice）——用来判「这条线是不是已经排过」。"""
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return 2 * len(ba & bb) / (len(ba) + len(bb))


def task_history(conn, d: str, days: int = 7):
    """近 N 天任务：各科布置/完成/删除，以及未完成的悬空条目。"""
    since = (date.fromisoformat(d) - timedelta(days=days)).isoformat()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, task_date, text, source, subject, done, deleted FROM daily_tasks "
        "WHERE task_date >= ? ORDER BY task_date, id", (since,))]
    by_subject = {}
    for r in rows:
        s = r["subject"] or "(综合)"
        a = by_subject.setdefault(s, {"issued": 0, "done": 0, "deleted": 0, "user_issued": 0})
        if r["source"] == "user":
            a["user_issued"] += 1
        else:
            a["issued"] += 1
            if r["deleted"]:
                a["deleted"] += 1
            elif r["done"]:
                a["done"] += 1
    dangling = [{"id": r["id"], "date": r["task_date"], "subject": r["subject"],
                 "days_open": (date.fromisoformat(d) - date.fromisoformat(r["task_date"])).days,
                 "text": r["text"][:80]}
                for r in rows if r["source"] == "agent" and not r["done"] and not r["deleted"]
                and r["task_date"] < d]
    return {"since": since, "by_subject": by_subject, "dangling": dangling, "rows": rows}


def repeats_for(topic_name: str, subject: str, history_rows, d: str, threshold=0.62):
    """这条线在过去 7 天被排过几次？

    用「候选名字被任务文本覆盖的比例」而不是对称相似度：任务文本往往很长
    （「408：接 CN 应用层往下推『传输层』——UDP 与 TCP …配 12 道选择题」），
    对称 Dice 会被长文本稀释到阈值以下，漏掉真正的重复（树与二叉树 #33 → #58
    就是这么漏掉的）。名字太短（<3 字）时要求整词出现，避免误伤。
    """
    hits = []
    name = re.sub(r"[\s（）()【】]+", "", str(topic_name or ""))
    if len(name) < 3:
        return hits
    nb = _bigrams(name)
    head, tail = name[:2], name[-2:]
    for r in history_rows:
        if r["source"] != "agent":
            continue
        txt = re.sub(r"[\s（）()【】]+", "", r["text"])
        cov = len(nb & _bigrams(txt)) / len(nb)
        # 除了覆盖率，还要求名字的**头尾都在**：「阅读真题精练」的覆盖率会被
        # 完型任务里的「真题」两字抬到 0.8，但「精练」不出现——那不是同一条线。
        if cov >= threshold and head in txt and (len(name) < 4 or tail in txt):
            if not subject or any(hh in r["text"] for hh in SUBJECT_HINTS.get(subject, [subject])):
                hits.append({"date": r["task_date"], "id": r["id"], "cover": round(cov, 2),
                             "done": bool(r["done"]), "deleted": bool(r["deleted"]),
                             "text": r["text"][:60]})
    return hits


def user_signals(conn, d: str, days: int = 7):
    """他自加的任务（重视什么）与删除的任务（拒绝什么）。"""
    since = (date.fromisoformat(d) - timedelta(days=days)).isoformat()
    added = [dict(r) for r in conn.execute(
        "SELECT text, subject, task_date FROM daily_tasks WHERE source='user' AND deleted=0 "
        "AND task_date >= ? ORDER BY task_date DESC, id DESC", (since,))]
    deleted = [dict(r) for r in conn.execute(
        "SELECT text, subject, task_date, deleted_at FROM daily_tasks WHERE deleted=1 "
        "AND task_date >= ? ORDER BY task_date DESC, id DESC", (since,))]
    # 删除原因若已完成（他删掉是因为"做完了/情况变了"），不该当成拒绝
    for x in deleted:
        x["likely_completed"] = any(k in x["text"] for k in ("全部完成", "已完成"))
    return {"added": added, "deleted": deleted}


# ---------------------------------------------------------------------------
# 通道 5：容量证据（番茄钟 / 到期卡 / 大盘派生）
# ---------------------------------------------------------------------------

def subject_evidence(conn, d: str):
    """每科：练习量、正确率、最近一次练习、是否「冷却」。

    冷却不能只看闪卡——他大部分时间在纸上做题（番茄钟 11 小时/天，闪卡只有几条），
    所以还要算上他自己在任务里报过的进度（user_added）。两处都为空的科目才是真冷却。
    """
    out = {s: {"近14天练习": 0, "近14天错": 0, "Again": 0, "正确率": None,
               "最近练习": "", "近3天练习": 0} for s in SUBJECTS}
    rows = conn.execute("""
        SELECT COALESCE(t.subject,'') AS s, rl.rating AS rating, rl.review_date AS rd
        FROM review_log rl JOIN questions q ON q.id = rl.question_id
        LEFT JOIN topics t ON t.id = q.topic_id
        WHERE rl.review_date >= datetime('now','localtime', ?)""", (f"-{RECENT_DAYS} days",))
    d3 = (date.fromisoformat(d) - timedelta(days=2)).isoformat()
    for r in rows:
        s = r["s"] or ""
        if s not in out:
            continue
        a = out[s]
        a["近14天练习"] += 1
        if r["rating"] <= 2:
            a["近14天错"] += 1
            if r["rating"] == 1:
                a["Again"] += 1
        rd = (r["rd"] or "")[:10]
        a["最近练习"] = max(a["最近练习"], rd)
        if rd >= d3:
            a["近3天练习"] += 1
    for s, a in out.items():
        if a["近14天练习"]:
            a["正确率"] = round((a["近14天练习"] - a["近14天错"]) / a["近14天练习"] * 100)

    # 他自己报的进度（纸面学习）——按关键词归到科目
    since = (date.fromisoformat(d) - timedelta(days=7)).isoformat()
    for r in conn.execute("SELECT text, task_date FROM daily_tasks WHERE source='user' "
                          "AND deleted=0 AND task_date >= ?", (since,)):
        for s, hints in SUBJECT_HINTS.items():
            if any(h in (r["text"] or "") for h in hints):
                out[s]["用户最近自报"] = max(out[s].get("用户最近自报", ""), r["task_date"])
    for s, a in out.items():
        a["冷却"] = a["近3天练习"] == 0 and a.get("用户最近自报", "") < d3
    return out


def parse_self_reported(texts):
    """从他自己的进度记录里抠出**客观结果**（「正确9/20」「第一篇错3个」）。

    这些是系统外（纸面真题）做出来的成绩，闪卡库里没有，但它们是全局最难看的数字
    （2007 完型 9/20 = 45%），不纳入决策就等于没看见。
    只做保守解析：认「a/b」与「错N个/题」，其余一律不猜。
    """
    out = []
    for t in texts:
        txt = t.get("text") or ""
        subject = ""
        for s, hints in SUBJECT_HINTS.items():
            if any(h in txt for h in hints):
                subject = s
                break
        for m in re.finditer(r"(\d{1,2})\s*/\s*(\d{1,2})", txt):
            a, b = int(m.group(1)), int(m.group(2))
            if 0 < b <= 80:
                out.append({"date": t.get("task_date"), "subject": subject,
                            "kind": "正确数/总数", "value": f"{a}/{b}",
                            "rate": round(a / b * 100), "raw": txt[:70]})
        for m in re.finditer(r"错\s*(\d{1,2})\s*(?:个|题|道)", txt):
            out.append({"date": t.get("task_date"), "subject": subject,
                        "kind": "错题数", "value": m.group(1), "rate": None, "raw": txt[:70]})
    return out


def pomo_budget(conn, d: str):
    row = conn.execute("SELECT value FROM config WHERE key='pomo_log'").fetchone()
    log = {}
    if row and row["value"]:
        try:
            log = json.loads(row["value"])
        except Exception:
            log = {}
    days = [(date.fromisoformat(d) - timedelta(days=i)).isoformat() for i in range(1, 8)]
    mins = [log[x]["min"] for x in days if x in log and isinstance(log[x], dict)]
    y = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
    return {
        "yesterday_min": (log.get(y) or {}).get("min"),
        "median_7d_min": round(statistics.median(mins)) if mins else None,
        "days": {x: (log[x].get("min") if isinstance(log.get(x), dict) else None) for x in days},
    }


def deck_due(conn, d: str):
    """按科目统计到期卡：原始到期 + **智能组实际会排的**（去掉已退役的连对卡）。

    ⚠️ 口径必须和 `src/card_policy.js` 一致，否则任务里写「今日到期 448 张」、
    而他打开闪卡只看到 430 张，就会怀疑卡丢了。规则（2026-09-19）：
      连对 ≥3 次、又没向 AI 提过问、也没钉住 → 不进智能组（筛选页仍可见）；
      钉住的一律不进退役名单。阈值可在 config 里改（smart_retire_streak）。
    """
    today = date.fromisoformat(d)
    d7 = (today + timedelta(days=7)).isoformat()
    retire, ask_exit = 3, 3
    try:
        row = conn.execute("SELECT value FROM config WHERE key='smart_retire_streak'").fetchone()
        if row and str(row["value"]).strip():
            retire = int(str(row["value"]).strip())
        row = conn.execute("SELECT value FROM config WHERE key='smart_ask_exit_streak'").fetchone()
        if row and str(row["value"]).strip():
            ask_exit = int(str(row["value"]).strip())
    except Exception:
        pass

    # 与 card_policy.loadReviewSignals 同源：按 id 倒序取最近几次（连**时间**一起取，
    # 「提问之后的连对」要靠时间比）
    recent = {}
    for r in conn.execute("""
        SELECT card_id, rating, review_date FROM review_log
        WHERE review_date >= datetime('now','localtime','-90 days')
        ORDER BY card_id, id DESC"""):
        lst = recent.setdefault(r["card_id"], [])
        if len(lst) < 8:
            lst.append((int(r["rating"] or 0), r["review_date"] or ""))

    def _streak(lst):
        n = 0
        for rating, _ in lst:
            if rating >= 3:
                n += 1
            else:
                break
        return n

    asked = {}      # question_id -> 最后一次提问时间
    for r in conn.execute("""
        SELECT question_id AS qid, MAX(created_at) AS last_ask FROM explain_log
        WHERE role='user' AND question_id IS NOT NULL AND question_id != ''
          AND (content LIKE '【我问】%' OR content NOT LIKE '我的选择：%')
        GROUP BY question_id"""):
        asked[r["qid"]] = r["last_ask"] or ""

    pinned = set()
    try:
        for r in conn.execute("SELECT card_id FROM card_pins"):
            pinned.add(r["card_id"])
    except Exception:
        pass      # 老库还没有 card_pins 表

    out = {}
    for r in conn.execute("""
        SELECT COALESCE(t.subject,'?') AS s, c.id AS cid, c.question_id AS qid,
               date(c.due_date) AS due
        FROM cards c JOIN questions q ON q.id = c.question_id
        LEFT JOIN topics t ON t.id = q.topic_id
        WHERE c.suspended = 0"""):
        a = out.setdefault(r["s"], {"due_now": 0, "due_7d": 0, "new_total": 0,
                                    "smart_due_now": 0, "retired": 0, "retired_any": 0,
                                    "pinned": 0, "asked": 0})
        due = r["due"] or ""
        is_due = bool(due) and due <= d
        is_due7 = bool(due) and d < due <= d7
        if is_due:
            a["due_now"] += 1
        elif is_due7:
            a["due_7d"] += 1

        lst = recent.get(r["cid"], [])
        streak = _streak(lst)
        ask_at = asked.get(r["qid"])
        # 「问过」的出口：提问之后又连对够多次 → 这个身份作废（问过也要能毕业）
        asked_after = 0
        if ask_at:
            for rating, at in lst:
                if not at or at <= ask_at:
                    continue
                if rating >= 3:
                    asked_after += 1
                else:
                    break
        ask_exempt = bool(ask_at) and (ask_exit <= 0 or asked_after < ask_exit)
        is_pinned = r["cid"] in pinned
        retired = (retire > 0 and streak >= retire and not is_pinned and not ask_exempt)
        if is_due:
            if retired:
                a["retired"] += 1
            else:
                a["smart_due_now"] += 1
        if retired:
            a["retired_any"] += 1
        if is_pinned:
            a["pinned"] += 1
        if ask_exempt:
            a["asked"] += 1
    for r in conn.execute("SELECT key, value FROM config WHERE key IN ('new_per_day','reviews_per_day')"):
        out.setdefault("_quota", {})[r["key"]] = r["value"]
    out["_rule"] = (f"连对 {retire} 次以上、且不是『问过还没学会』、也没钉住 → 不进智能组；"
                    f"问过的卡提问后再连对 {ask_exit} 次就退出豁免（见 card_policy.js）")
    return out


def dashboard_signals():
    """大盘派生的薄弱考点 / 冷热前缀（generate_dashboard.py 生成，serve.js 已在用）。

    注意 generated_at：如果是旧的，说明管道没跑，这份信号要打折看待。
    """
    fp = BASE_DIR / "dashboard_data.json"
    if not fp.exists():
        return {"available": False}
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        data["available"] = True
        return data
    except Exception:
        return {"available": False}


def review_patterns_state():
    fp = ROOT / "Review" / "_patterns.json"
    if not fp.exists():
        return {"available": False, "note": "复盘画像文件不存在"}
    try:
        p = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {"available": False}
    return {"available": True, "generated_at": p.get("generated_at"),
            "totals": p.get("totals"), "subjects_with_causes":
                [k for k, v in (p.get("subjects") or {}).items() if (v.get("top_causes") or [])],
            "note": "复盘页沉淀的错因画像（errors=0 表示复盘流程还没积累数据）"}


def notes_index_stats():
    fp = BASE_DIR / "笔记索引.yaml"
    if not fp.exists():
        return {}
    try:
        import yaml
        y = yaml.safe_load(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}
    stats = y.get("stats") or {}
    return {"generated": y.get("generated"), "stats": stats,
            "total_entries": len(y.get("entries") or [])}


# ---------------------------------------------------------------------------
# 融合：候选池
# ---------------------------------------------------------------------------

def build_candidates(err, asks, cards, notes, pfx_idx, history_rows, d, dash):
    """把五条通道合成候选池，按证据分层。

    打分只用来**排序**，不用来做决定：决定权在分层与规则（见 RULES）。
    每条候选都带：证据明细、载体（卡/笔记）、近 7 天是否已排过。
    """
    keys = set(err) | set(asks)
    cands = []
    for k in keys:
        e = err.get(k, {})
        a = asks.get(k, {})
        c = cards.get(k, {})
        name = e.get("name") or k
        subject = e.get("subject") or subject_of_tid(k)
        weight = e.get("weight") or 0
        note = note_for_topic(k, pfx_idx, notes, name)

        wrong_14 = e.get("wrong", 0)
        again_14 = e.get("again", 0)
        asks_n = a.get("asks", 0)
        # 错误证据强度：Again 权重最高（说明整块不会），提问次之（说明没想通）
        err_score = wrong_14 * 2 + again_14 * 1.5 + asks_n * 1.5
        score = err_score * (1 + (weight or 1) * 0.3)
        if e.get("low_sample"):
            score *= 0.7            # 样本太少，降权但不清零

        carrier = []
        if c.get("cards"):
            carrier.append(f"题库{c['cards']}张（今日到期{c.get('due_now',0)}）")
        else:
            carrier.append("题库无卡")
        if note.get("file"):
            stale = note.get("stale_days")
            carrier.append(f"{note['file']}" + (f"（{stale}天没动）" if stale is not None else "")
                           + (f"[按{note['matched_by']}匹配]" if note.get("matched_by") else ""))
        else:
            carrier.append("无对应笔记文件")
        has_carrier = bool(c.get("cards") or note.get("file"))

        rep = repeats_for(name, subject, history_rows, d)
        unfinished = [x for x in rep if not x["done"] and not x["deleted"]]

        # 分层
        if (wrong_14 >= 2 or again_14 >= 1 or asks_n >= 2) and has_carrier:
            tier = "P1_已证明会丢分"
        elif (weight or 0) >= 2 and not c.get("cards"):
            tier = "P2_高权重零覆盖"
        elif (note.get("stale_days") or 0) >= 5 and (weight or 0) >= 2:
            tier = "P3_笔记陈旧"
        else:
            tier = "P4_其它"
        if not has_carrier:
            tier = "P2_高权重零覆盖" if (weight or 0) >= 2 else "P4_其它"

        cands.append({
            "tid": k, "subject": subject, "name": name, "exam_weight": weight, "tier": tier,
            "score": round(score, 1),
            "evidence": {
                "近14天练习": e.get("n", 0), "近14天错": wrong_14, "其中Again": again_14,
                "正确率": e.get("accuracy"), "全库练习": e.get("total_all", 0),
                "样本少": e.get("low_sample", False),
                "最近错于": e.get("last_wrong", ""),
                "AI提问次数": asks_n, "最近提问": a.get("last_ask", ""),
                "他选的错项": e.get("chosen_top") or [],
                "提问样例": a.get("samples") or [],
            },
            "carrier": {"cards": c.get("cards", 0), "due_now": c.get("due_now", 0),
                        "note_file": note.get("file", ""), "note_matched_by": note.get("matched_by", ""),
                        "note_stale_days": note.get("stale_days"),
                        "archive_files": note.get("archive") or [],
                        "summary": " / ".join(carrier)},
            "already_issued_7d": rep,
            "dont_reissue": len(unfinished) >= 2,
            "warn_repeat": len(rep) >= 2,
            "unfinished_count": len(unfinished),
        })

    cands.sort(key=lambda x: (-x["score"], x["tier"]))
    return cands


def subject_block(subject, cands, err, notes, pfx_idx, cards, budget_hist, deck, d, dash):
    """每科的汇总：练习量/正确率/到期/笔记陈旧/该科候选。"""
    subj_dir = {"408": "408", "数学一": "Math", "政治": "Politics", "英语一": "English"}[subject]
    files = md_files(ROOT / subj_dir)
    newest = max((f["mtime"] for f in files), default=None)
    recent = sorted(files, key=lambda f: f["mtime"], reverse=True)[:5]
    today = date.fromisoformat(d)
    hist = budget_hist["by_subject"].get(subject, {})
    own = [c for c in cands if c["subject"] == subject]
    return {
        "notes": {
            "dir": subj_dir, "files": len(files),
            "newest_mtime": newest,
            "stale_days": (today - datetime.fromisoformat(newest).date()).days if newest else None,
            "recent_files": [{"rel": f["rel"], "mtime": f["mtime"], "size": f["size"]}
                             for f in recent],
        },
        "cards": deck.get(subject, {}),
        "task_history_7d": hist,
        "candidates": own[:8],
        "weak_topic_ids_from_dashboard": [t for t in (dash.get("weak_topic_ids") or [])
                                          if subject_of_tid(t) == subject],
        "cold_prefixes_from_dashboard": [p for p in (dash.get("cold_prefixes") or [])
                                         if p.startswith({"408": "408-", "数学一": "MATH-",
                                                          "政治": "POL-", "英语一": "ENG-"}[subject])],
    }


# ---------------------------------------------------------------------------
# 简报
# ---------------------------------------------------------------------------

RULES = [
    "R1 任务只来自证据：每条任务必须能指回本简报里的某个字段（考点/错选/提问/笔记/到期）。"
    "没有证据的科目不排，除非它已连续 ≥3 天零练习（P4 冷却科目）。",
    "R2 每条任务必须写明载体（题库多少张卡 / 哪个笔记文件）+ 动作 + 可检验产出"
    "（写进哪个文件、归档到哪、提交什么）。载体为「题库无卡」的考点，任务要写成"
    "「先出卡/先补笔记」，不许写「刷 N 题」。",
    "R3 优先级按分层走：P1 已证明会丢分（近 14 天错≥2 或 Again≥1 或提问≥2）>"
    "P2 高权重零覆盖 > P3 笔记陈旧的高权重章节 > P4 冷却科目。",
    "R4 一贯性闸门：某条线近 7 天已排过 ≥2 次且都没完成（dont_reissue=true）→"
    "**不许第 3 次原样重排**；改成更小切入口（只做 3 题/只补笔记）或明确写「关闭这条线」。",
    "R5 不许否定式措辞：不写「昨天没做就先补」这类话；未完成的线按 R4 处理，"
    "或者干脆不提。",
    "R6 容量约束：条数按近 7 天番茄中位数与完成率定——≥8h/天 可 4~6 条，<6h 或完成率<60% "
    "减到 3~4 条且每条更小。闪卡到期量大的日子，闪卡任务排第一条。",
    "R7 题目本身有问题的卡（card_reports）不进用户任务，进 agent 修复队列。",
    "R8 用户删过的任务类型不再提（除非删除原因是「已完成」）；他反复自加的那类要排进计划。",
]


def build_brief(d: str, include_planner: bool = True) -> dict:
    today = date.fromisoformat(d)
    conn = connect()
    err = err_evidence(conn, d)
    asks = ask_evidence(conn)
    cards = card_stats(conn, d)
    deck = deck_due(conn, d)
    budget = pomo_budget(conn, d)
    hist = task_history(conn, d)
    signals = user_signals(conn, d)
    signals["reported_results"] = parse_self_reported(signals["added"])
    ask_subj = ask_by_subject(conn)
    subj_ev = subject_evidence(conn, d)
    conn.close()

    sj = load_subjects_json()
    pfx_idx = prefix_index(sj)
    notes = note_state(pfx_idx, d)
    dash = dashboard_signals()

    cands = build_candidates(err, asks, cards, notes, pfx_idx, hist["rows"], d, dash)

    # 每科练习量（含准确率）——比 context 原来的「条数」多一层
    rev_subject = {}
    for c in cands:
        s = c["subject"]
        b = rev_subject.setdefault(s, {"近14天练习": 0, "近14天错": 0, "Again": 0})
        b["近14天练习"] += c["evidence"]["近14天练习"]
        b["近14天错"] += c["evidence"]["近14天错"]
        b["Again"] += c["evidence"]["其中Again"]

    brief = {
        "meta": {
            "date": d,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "days_to_exam": (date(2026, 12, 19) - today).days,
            "evidence_channels": {
                "review_log": "闪卡真实答题（rating + chosen 错选）",
                "explain_log": "AI 解析/追问记录（思维卡点）",
                "notes_mtime": "笔记文件改动时间（他实际在写什么）",
                "task_history": "近 7 天任务布置/完成/删除（一贯性）",
                "pomodoro": "番茄钟真实时长（容量）",
                "planner_hours": "daily_planner 的阶段与分科时长分配（沿用同一套算法）",
            },
        },
        "budget": {**budget, "task_history_7d": hist["by_subject"]},
        "subjects": {},
        "candidates": cands[:20],
        "must_not_reissue": [c for c in cands if c["dont_reissue"]],
        "coverage_gaps": [],
        "fix_queue": [],
        "user_signals": signals,
        "data_health": {},
        "rules": RULES,
    }

    # 阶段与分科时长：直接复用 daily_planner 自己的算法，避免出现第二套口径
    try:
        import daily_planner as dp
        phase_name, phase_data = dp.get_current_phase(today)
        hours = dp.get_available_hours(today, phase_name, phase_data)
        brief["meta"]["phase"] = phase_name
        brief["budget"]["available_hours"] = hours
        brief["budget"]["total_pomo_vs_plan"] = (
            f"近 7 天番茄中位数 {budget.get('median_7d_min')} 分钟"
            f"（{(budget.get('median_7d_min') or 0) / 60:.1f}h）vs 规划器给的 {hours}h"
            "——任务条数按前者排，不按后者")
        graphs = dp.load_knowledge_graphs()
        notes_index = dp.load_notes_index()
        qb = dp.load_question_bank()
        prog = dp.load_progress()
        subs_data = []
        for s in SUBJECTS:
            subs_data.append({
                "name": s,
                "target_mastery": 1.0,
                "current_mastery": dp.estimate_mastery(s, graphs, notes_index, qb, prog),
                "exam_weight": {"408": 150, "数学一": 150, "政治": 100, "英语一": 100}[s],
            })
        brief["budget"]["hours_by_subject"] = dp.allocate_hours(subs_data, hours)
        for s in SUBJECTS:
            brief["subjects"].setdefault(s, {})["mastery_estimate"] = round(
                subs_data[SUBJECTS.index(s)]["current_mastery"], 3)
    except Exception as e:                                     # noqa: BLE001
        brief["data_health"].setdefault("warnings", []).append(f"planner 取数失败：{e}")

    for s in SUBJECTS:
        blk = brief["subjects"].setdefault(s, {})
        blk.update(subject_block(s, cands, err, notes, pfx_idx, cards, hist, deck, d, dash))
        blk["evidence"] = subj_ev.get(s, {})
        if subj_ev.get(s, {}).get("冷却"):
            blk["cooling"] = True

    # 高权重但题库无卡（P2）
    conn = connect()
    gaps = []
    for r in conn.execute("""
        SELECT t.id, t.name, t.subject, t.exam_weight FROM topics t
        WHERE t.exam_weight >= 2
          AND length(t.id) - length(replace(t.id,'-','')) = 2      -- 只取图谱粒度（三段）
          AND NOT EXISTS (SELECT 1 FROM questions q
                          WHERE q.topic_id = t.id OR q.topic_id LIKE t.id || '-%')
        ORDER BY t.exam_weight DESC"""):
        gaps.append({"tid": r["id"], "name": r["name"], "subject": r["subject"],
                     "weight": r["exam_weight"]})
    conn.close()
    brief["coverage_gaps"] = gaps

    # agent 修复队列（题目本身有问题的卡）
    try:
        import daily_planner as dp
        brief["fix_queue"] = dp.load_card_reports()
    except Exception as e:
        brief["data_health"]["card_reports_error"] = str(e)

    # 数据可信度——这是简报能不能被相信的前提，必须显式说出来
    brief["data_health"] = {
        "错因画像(_patterns.json)": review_patterns_state(),
        "大盘派生(dashboard_data.json)": {
            "available": dash.get("available", False),
            "generated_at": dash.get("generated_at"),
            "weak_topic_ids": dash.get("weak_topic_ids") or [],
            "cold_prefixes": dash.get("cold_prefixes") or [],
        },
        "笔记索引(笔记索引.yaml)": {k: v for k, v in notes_index_stats().items()},
        "explain_log_按科": ask_subj,
        "warnings": [],
    }
    w = brief["data_health"]["warnings"]
    if not (review_patterns_state().get("totals") or {}).get("errors"):
        w.append("复盘页错因画像 errors=0：错因类任务不成立，改用 review_log/explain_log 的证据"
                 "（这两条通道不受复盘页使用情况影响）")
    if not dash.get("available"):
        w.append("dashboard_data.json 不可用：先跑 run_pipeline.py --no-plan 再定任务")
    if not err:
        w.append("近 14 天没有任何答题记录：本简报警的「错误证据」为空，任务只能按覆盖排")
    low = [c["tid"] for c in cands if c["evidence"]["样本少"]]
    if low:
        w.append(f"{len(low)} 个考点练习次数 <{WEAK_LOW_SAMPLE}，正确率只当参考："
                 f"{'、'.join(low[:6])}")
    return brief


# ---------------------------------------------------------------------------
# Markdown 渲染（给人看）
# ---------------------------------------------------------------------------

def render_md(b: dict) -> str:
    L = []
    m = b["meta"]
    L.append(f"# 备考决策简报 · {m['date']}（距考试 {m['days_to_exam']} 天）")
    L.append("")
    L.append(f"生成于 {m['generated_at']}｜五条证据通道：闪卡答题 / AI 提问 / 笔记改动 / 任务历史 / 番茄钟")
    L.append("")

    bud = b["budget"]
    L.append("## 容量与产出")
    if b["meta"].get("phase"):
        L.append(f"- 阶段：{b['meta']['phase']}，规划器给 {bud.get('available_hours')} 小时")
    L.append(f"- 番茄钟：昨天 {bud.get('yesterday_min')} 分钟，近 7 天中位数 "
             f"{bud.get('median_7d_min')} 分钟")
    if bud.get("hours_by_subject"):
        L.append("- 分科时长（daily_planner 分配）：" +
                 "、".join(f"{k} {v}h" for k, v in bud["hours_by_subject"].items()))
    for s, h in (b["budget"].get("task_history_7d") or {}).items():
        if h.get("issued"):
            L.append(f"- {s}：近 7 天布置 {h['issued']} 条，完成 {h['done']}，被删 {h['deleted']}")
    L.append("")
    L.append("## 用户信号（他自加 / 他删除）")
    for x in (b.get("user_signals", {}).get("added") or [])[:6]:
        L.append(f"- 自加 {x['task_date']}：{x['text'][:70]}")
    for x in (b.get("user_signals", {}).get("deleted") or [])[:6]:
        tag = "（原因：已完成/情况变了，不算拒绝）" if x.get("likely_completed") else "（明确拒绝过）"
        L.append(f"- 删除 {x['task_date']}：{x['text'][:60]}…{tag}")
    rep = b.get("user_signals", {}).get("reported_results") or []
    if rep:
        L.append("- 他自报的客观结果（系统外，纸面真题）：")
        for x in rep:
            L.append(f"  - {x['date']} [{x['subject'] or '综合'}] {x['kind']} {x['value']}"
                     + (f"（{x['rate']}%）" if x.get("rate") else "")
                     + f"　← {x['raw'][:50]}")
    L.append("")

    L.append("## 候选池（按证据分层）")
    for tier in ["P1_已证明会丢分", "P2_高权重零覆盖", "P3_笔记陈旧", "P4_其它"]:
        rows = [c for c in b["candidates"] if c["tier"] == tier]
        if not rows:
            continue
        L.append(f"### {tier}")
        for c in rows[:6]:
            e = c["evidence"]
            L.append(f"- **{c['subject']} {c['name']}**（{c['tid']}，权重 {c['exam_weight']}，"
                     f"分 {c['score']}）")
            L.append(f"  - 证据：近14天练 {e['近14天练习']} 错 {e['近14天错']}"
                     f"（Again {e['其中Again']}）正确率 {e['正确率']}%"
                     + (f"，样本少" if e["样本少"] else "")
                     + (f"；AI 提问 {e['AI提问次数']} 次，最近 {e['最近提问']}" if e["AI提问次数"] else ""))
            if e["他选的错项"]:
                L.append(f"  - 错选：{'；'.join(f'{k}×{v}' for k, v in e['他选的错项'])}")
            L.append(f"  - 载体：{c['carrier']['summary']}")
            if c["already_issued_7d"]:
                L.append(f"  - 近 7 天排过 {len(c['already_issued_7d'])} 次："
                         + "；".join(f"{x['date']}{'已完成' if x['done'] else ('已删' if x['deleted'] else '未完成')}"
                                    for x in c["already_issued_7d"])
                         + ("　→ **不许原样重排**" if c["dont_reissue"] else ""))
        L.append("")

    if b.get("must_not_reissue"):
        L.append("## 闸门：不许原样重排")
        for c in b["must_not_reissue"]:
            L.append(f"- {c['subject']} {c['name']}（{c['tid']}）已排 "
                     f"{len(c['already_issued_7d'])} 次未完成 → 换更小切入口或明确关闭")
        L.append("")

    L.append("## 各科状况")
    for s, blk in b["subjects"].items():
        n = blk["notes"]
        L.append(f"### {s}" + ("　⚠️ 冷却中（近 3 天零练习且无自报）" if blk.get("cooling") else ""))
        ev = blk.get("evidence") or {}
        if ev:
            L.append(f"- 练习（近 14 天）：{ev.get('近14天练习')} 次，错 {ev.get('近14天错')}"
                     f"（Again {ev.get('Again')}），正确率 {ev.get('正确率')}%"
                     f"，最近 {ev.get('最近练习') or '无记录'}")
        L.append(f"- 笔记：{n['files']} 篇，最新 {n['newest_mtime']}"
                 f"（{n['stale_days']} 天前）")
        c = blk["cards"]
        if c:
            L.append(f"- 闪卡：到期 {c.get('due_now')} 张（智能组实际会排 "
                     f"{c.get('smart_due_now')} 张；其中 {c.get('retired')} 张因连对已收起），"
                     f"📌 钉住 {c.get('pinned')} 张、💬 问过 AI 的 {c.get('asked')} 张，"
                     f"未来 7 天 {c.get('due_7d')} 张")
        for f in n["recent_files"][:3]:
            L.append(f"  - {f['rel']}（{f['mtime'][5:16]}，{f['size']}B）")
        if blk["weak_topic_ids_from_dashboard"]:
            L.append(f"- 大盘薄弱考点：{'、'.join(blk['weak_topic_ids_from_dashboard'])}")
        if blk["cold_prefixes_from_dashboard"]:
            L.append(f"- 大盘冷区：{'、'.join(blk['cold_prefixes_from_dashboard'])}")
        L.append("")

    if b.get("coverage_gaps"):
        L.append(f"## 高权重但库里零覆盖（{len(b['coverage_gaps'])} 个，前 10）")
        for g in b["coverage_gaps"][:10]:
            L.append(f"- [{g['subject']}] {g['name']}（{g['tid']}，权重 {g['weight']}）→ 先出卡")
        L.append("")

    fq = b.get("fix_queue") or []
    if fq:
        L.append(f"## agent 修复队列（{len(fq)} 张问题卡，不进用户任务）")
        for r in fq[:6]:
            L.append(f"- #{r.get('id')} {r.get('card_id')} {r.get('kind_label') or r.get('kind')}"
                     f"：{(r.get('stem') or '')[:40]}")
        L.append("")

    L.append("## 数据可信度")
    for k, v in b["data_health"].items():
        if k == "warnings":
            continue
        if isinstance(v, dict) and "generated_at" in v:
            L.append(f"- {k}：generated_at={v.get('generated_at')}")
        elif isinstance(v, dict) and "totals" in v:
            L.append(f"- {k}：totals={v.get('totals')}")
        elif isinstance(v, dict) and "stats" in v:
            st = v["stats"] or {}
            L.append(f"- {k}：{v.get('generated')}｜" +
                     "；".join(f"{s} 覆盖 {x.get('coverage')}%" for s, x in st.items()))
        elif isinstance(v, dict):
            L.append(f"- {k}：{json.dumps(v, ensure_ascii=False)[:160]}")
        else:
            L.append(f"- {k}：{v}")
    for w in b["data_health"]["warnings"]:
        L.append(f"- ⚠️ {w}")
    L.append("")

    L.append("## 出题规则")
    for r in b["rules"]:
        L.append(f"- {r}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="每日备考决策简报")
    ap.add_argument("--date", default="today")
    ap.add_argument("--md", action="store_true", help="输出 Markdown（给人看）")
    ap.add_argument("--out", default="", help="写文件（默认 stdout）")
    ap.add_argument("--no-planner", action="store_true", help="跳过 daily_planner 相关取数")
    args = ap.parse_args()
    d = resolve_date(args.date)
    brief = build_brief(d, include_planner=not args.no_planner)
    text = render_md(brief) if args.md else json.dumps(brief, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(json.dumps({"ok": True, "out": args.out, "bytes": len(text)}, ensure_ascii=False))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

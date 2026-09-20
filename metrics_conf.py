# -*- coding: utf-8 -*-
"""口径读取层 —— metrics_spec.json 的读写与派生（与 subjects_conf.py 同构）。

为什么要有它（2026-09-20）
==========================
系统原来把「口径」散在十来个文件里各写一份：

  * 时间窗：daily_brief 的 14 天、weekly_report 的 7 天、dashboard 的 14 天…
  * 阈值：WEAK_LOW_SAMPLE=5 在 daily_brief 与 generate_dashboard **各写一遍**
  * 组题策略：card_policy.js 的 3 个 DEFAULT_* 在 serve.js 又抄了默认值
  * 笔记目录规则：tools/gen_english_index.py 里硬编码，目录改名后静默失配
    （`translation&write` vs `translation&writing` → 英语写作恒为 0 篇）

于是每次口径出问题都要重新全盘排查。现在：

  * **metrics_spec.json 是单一事实源**：口径的取值、语义、来源、是否可改都在里面。
  * 本模块是 Python 侧的读取层；JS 侧由 serve.js 直接读同一份 JSON。
  * scope="runtime" 且 editable=true 的项，取值优先从 config 表读（设置页能改），
    读不到才回退 spec 里的 default —— 所以设置页改完**立刻生效**。
  * tools/check_metrics_drift.py 用 spec 里的 checks 锚点与源码对拍，
    文档与代码不一致会直接报错，不用再靠人去发现。

用法
----
    import metrics_conf
    metrics_conf.recent_days()          # 14
    metrics_conf.weak_low_sample()      # 5
    metrics_conf.value("srs.retire_streak")   # config 表优先
    metrics_conf.snapshot()             # 设置页用：全部口径 + 生效值 + 来源
"""
import json
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC_PATH = os.path.join(HERE, "metrics_spec.json")
DB_PATH = os.path.join(HERE, "question_bank.db")

_CACHE = None


# ---------------------------------------------------------------------------
# 载入
# ---------------------------------------------------------------------------
def load(force=False):
    """读 metrics_spec.json（进程内缓存）。失败返回 {"metrics": []} —— 不抛异常，
    调用方拿到空表就用各自的内置默认值，绝不因为口径文件坏了让整条管道挂掉。"""
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    try:
        with open(SPEC_PATH, encoding="utf-8") as f:
            _CACHE = json.load(f)
    except (OSError, ValueError):
        _CACHE = {"version": 0, "metrics": []}
    return _CACHE


def by_id(mid):
    for m in load().get("metrics", []):
        if m.get("id") == mid:
            return m
    return None


# ---------------------------------------------------------------------------
# 取值
# ---------------------------------------------------------------------------
def _spec_default(mid):
    m = by_id(mid)
    return m.get("default", m.get("value") if m else None)


def _cfg_get(key, default=None):
    """从 config 表读一个键（只读、带 busy_timeout；任何异常都回退默认）。"""
    if not key or not os.path.exists(DB_PATH):
        return default
    try:
        con = sqlite3.connect("file:%s?mode=ro" % DB_PATH.replace("\\", "/"), uri=True, timeout=3)
        try:
            row = con.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        finally:
            con.close()
        return row[0] if row and row[0] is not None else default
    except sqlite3.Error:
        return default


def _coerce(raw, m):
    if raw is None:
        return None
    t = m.get("type")
    if t == "int":
        try:
            v = int(str(raw).strip())
        except (TypeError, ValueError):
            return None
        rng = m.get("range")
        if rng and len(rng) == 2 and not (rng[0] <= v <= rng[1]):
            return None          # 越界视为坏值，回退默认，避免把系统调坏
        return v
    if t == "float":
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError):
            return None
    if t == "bool":
        return str(raw).strip().lower() in ("1", "true", "on", "yes")
    return raw


def value(mid, fallback=None):
    """取一项口径的**生效值**。

    runtime + editable → config 表优先（设置页改过的），否则 spec 里的 default；
    其它 scope → spec 里的 value 原样返回（可能是 dict / list / 字符串）。
    """
    m = by_id(mid)
    if not m:
        return fallback
    if m.get("scope") == "runtime" and m.get("editable") and m.get("key"):
        raw = _cfg_get(m["key"], None)
        v = _coerce(raw, m)
        if v is not None:
            return v
        return m.get("default", fallback)
    val = m.get("value", m.get("default", fallback))
    return val if val is not None else fallback


# ---- 常用口径的具名访问器（源码里不要再写裸数字）----
def recent_days(default=14):
    """「最近」证据窗（天）。"""
    return int(_spec_default("window.recent_days") or default)


def task_history_days(default=7):
    return int(_spec_default("window.task_history_days") or default)


def weekly_report_days(default=7):
    return int(value("window.weekly_report_days", default))


def weak_low_sample(default=5):
    """正确率最小样本量。"""
    return int(_spec_default("threshold.weak_low_sample") or default)


def tier_weight_min(default=2):
    return int(_spec_default("threshold.tier_weight_min") or default)


def strong_tier(default=None):
    """P1 入档条件 {wrong, again, asks}。"""
    return value("threshold.err_tier_strong", default or {"wrong": 2, "again": 1, "asks": 2})


def repeat_name_similarity(default=0.62):
    return float(_spec_default("threshold.repeat_name_similarity") or default)


def mature_interval_days(default=21):
    return int(_spec_default("review.mature_interval_days") or default)


def freshness(default=None):
    v = _spec_default("note.revival_thresholds") or default or {"hot": 7, "warm": 21, "cold": 60}
    return {"hot": int(v["hot"]), "warm": int(v["warm"]), "cold": int(v["cold"])}


def exam_date(default="2026-12-19"):
    return str(_spec_default("plan.exam_date") or default)


def rating_meaning(rating, default=""):
    """1~4 的文字语义（写给人看的报告用）。"""
    scale = value("review.rating_scale", {}) or {}
    return scale.get(str(rating), default)


def is_not_yet_learned(rating):
    """这次是否算「没答对」。**全系统唯一的判据**，别再各写 `rating <= 2`。"""
    try:
        return int(rating) <= 2
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# 给设置页 / 自检用
# ---------------------------------------------------------------------------
def snapshot(db_path=None):
    """返回全部口径 + 生效值（设置页「📐 口径」页的数据源）。"""
    out = []
    for m in load().get("metrics", []):
        item = {
            "id": m.get("id"),
            "title": m.get("title", ""),
            "scope": m.get("scope", "fixed"),
            "editable": bool(m.get("editable")),
            "unit": m.get("unit", ""),
            "desc": m.get("desc", ""),
            "source": m.get("source", ""),
            "used_by": m.get("used_by", []),
        }
        if m.get("editable"):
            item["key"] = m.get("key")
            item["type"] = m.get("type", "string")
            item["range"] = m.get("range")
            item["default"] = m.get("default")
            item["value"] = value(m["id"])
            item["overridden"] = str(_cfg_get(m.get("key"), "")) not in ("", "None")
        else:
            item["value"] = m.get("value", m.get("default"))
        out.append(item)
    return out


def summary():
    """各 scope 的条目数（自检/概览用）。"""
    counts = {}
    for m in load().get("metrics", []):
        counts[m.get("scope", "fixed")] = counts.get(m.get("scope", "fixed"), 0) + 1
    return counts


if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        for it in snapshot():
            flag = "可改" if it["editable"] else ("约定" if it["scope"] == "convention" else "固定")
            v = it["value"]
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)[:70]
            print("[%-4s] %-26s %-30s %s" % (flag, it["id"], str(v)[:30], it["title"]))
        print("\nscope 分布:", summary())
    else:
        print(json.dumps({
            "recent_days": recent_days(), "task_history_days": task_history_days(),
            "weekly_report_days": weekly_report_days(), "weak_low_sample": weak_low_sample(),
            "tier_weight_min": tier_weight_min(), "strong_tier": strong_tier(),
            "repeat_name_similarity": repeat_name_similarity(),
            "mature_interval_days": mature_interval_days(), "freshness": freshness(),
            "exam_date": exam_date(),
            "retire_streak": value("srs.retire_streak"),
            "extra_count": value("quota.flash_extra_count"),
        }, ensure_ascii=False, indent=1))

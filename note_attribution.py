# -*- coding: utf-8 -*-
"""note_attribution.py —— 「学习材料 → 知识单元」归因结果的**只读**消费层。

为什么单独一个文件（与 metrics_conf.py / subjects_conf.py 同一模式）：
  归因由 tools/ai_attribute_notes.js 写进 SQLite，由 Python 管道读。
  判定口径（哪些 kind 算覆盖、置信度门槛）必须只有一个出处，
  否则 generate_dashboard 说「有笔记」而 daily_brief 说「没载体」，
  用户就会看到两个互相矛盾的百分比 —— 这个项目为这类 bug 付过太多次学费。

口径全部来自 attribution_spec.json，本文件不内联任何 kind 名单或阈值。

用法：
    import note_attribution as na
    cov = na.coverage_by_subject()        # {"政治": {"POL-SG-04": {"units":3,"best":0.9}}}
    na.units_for_topic("政治", "POL-SG-04")
    na.status()                            # 各 domain 跑了多少段、最后更新时间
"""
import json
import os
import sqlite3

import paths

SPEC_PATH = os.path.join(paths.SRC_DIR, "attribution_spec.json")
DB_PATH = os.path.join(paths.SRC_DIR, "question_bank.db")

_spec_cache = None


def spec() -> dict:
    global _spec_cache
    if _spec_cache is None:
        with open(SPEC_PATH, "r", encoding="utf-8") as f:
            _spec_cache = json.load(f)
    return _spec_cache


def domains() -> list:
    return spec().get("domains", [])


def domain_for_subject(subject: str):
    """科目名 → domain 配置。任意叫法都认（"数学"/"数学一"/"408"/别名），
    因为大盘用 short、gap_analysis 用 graph_key、用户口头又说"数一"。"""
    for d in domains():
        if d.get("subject") == subject or d.get("id") == subject:
            return d
    for gf, s in _subjects_by_graph_file().items():
        names = {s.get("id"), s.get("short"), s.get("graph_key"), s.get("name")}
        names |= set(s.get("aliases") or [])
        if subject in names:
            for d in domains():
                if d.get("graph_file") == gf:
                    return d
    return None


def counting_kinds() -> set:
    """计入覆盖的材料类型。判「有没有学过」只认这些 —— 词条罗列、原文、
    目录导航存在不等于掌握，把它们算进覆盖就是自欺。"""
    return {k["id"] for k in spec().get("kinds", []) if k.get("counts_coverage")}


def thresholds() -> dict:
    return spec().get("thresholds", {})


def _connect(db_path=None):
    p = db_path or DB_PATH
    if not os.path.exists(p):
        return None
    try:
        conn = sqlite3.connect("file:" + p.replace("\\", "/") + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def has_data(domain_id: str, db_path=None) -> bool:
    conn = _connect(db_path)
    if not conn:
        return False
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM note_attributions WHERE domain = ?",
            (domain_id,)).fetchone()
        return bool(row and row["n"])
    except sqlite3.Error:
        return False          # 表还不存在 = 从没跑过归因
    finally:
        conn.close()


def run_status(db_path=None) -> dict:
    """各 domain 的归因完整性台账 {domain_id: {total, pending, prompt_ver, finished}}。

    这张表存在的理由（2026-09-27 实测踩到）：跑到一半和跑完了，在 note_attributions
    里长得一模一样 —— 英语只判了 67/725 批时，大盘照样把它的口径切成归因，
    总覆盖率从 96.9% 被误砸到 87.6%。**半截数据比没有数据更危险。**
    """
    conn = _connect(db_path)
    if not conn:
        return {}
    try:
        rows = conn.execute(
            "SELECT domain, units_total, units_pending, prompt_ver, finished_at "
            "FROM note_attr_runs").fetchall()
    except sqlite3.Error:
        conn.close()
        return {}
    out = {r["domain"]: {"total": r["units_total"], "pending": r["units_pending"],
                         "prompt_ver": r["prompt_ver"], "finished": r["finished_at"]}
           for r in rows}
    conn.close()
    return out


def trustworthy_domains(db_path=None) -> set:
    """允许按归因口径判覆盖的 domain：段落全判完，且提示词版本与当前 spec 一致。
    图谱改过会让 unit_key 变化 → pending 自动变大 → 这里自动关门，不必额外对拍。"""
    want = spec().get("prompt_version")
    return {did for did, st in run_status(db_path).items()
            if st["pending"] == 0 and st["prompt_ver"] == want}


def _subjects_by_graph_file():
    """graph_file -> subjects.json 里那条科目定义。叫法一律取自 subjects.json，
    不在这里另抄一份科目名单 —— 「两边各存一份映射」是这个项目反复踩的坑。"""
    try:
        import subjects_conf
        subs = subjects_conf.load_subjects()
    except Exception:
        return {}
    return {s["graph_file"]: s for s in subs if s.get("graph_file")}


def coverage_by_subject(db_path=None, key: str = "short") -> dict:
    """{科目名: {考点id: {"units": 段数, "best": 最高置信度}}}

    key 决定用哪种叫法当键：大盘用 "short"（数学），gap_analysis 用 "graph_key"
    （数学一）。同一份归因、两种叫法，省得两边各算一套覆盖互相矛盾。

    只收 counts_coverage 的 kind 且 confidence ≥ accept 的判定，并且**只收跑完
    归因的科目**（trustworthy_domains）：没跑完的科目整个不出现，调用方据此回退
    老规则 —— 半截归因数据比没有数据更危险。
    """
    conn = _connect(db_path)
    if not conn:
        return {}
    ok = trustworthy_domains(db_path)
    if not ok:
        conn.close()
        return {}
    kinds = counting_kinds()
    thr = thresholds().get("accept", 0.6)
    out = {}
    try:
        rows = conn.execute(
            "SELECT domain, kind, topic_ids, confidence, rel_path FROM note_attributions").fetchall()
    except sqlite3.Error:
        conn.close()
        return {}
    # domain -> topic -> agg
    agg = {}
    for r in rows:
        d = r["domain"]
        if d not in ok:
            continue          # 没跑完的科目一律不信，回退章节号口径
        agg.setdefault(d, {})
        if r["kind"] not in kinds:
            continue
        try:
            tps = json.loads(r["topic_ids"] or "[]")
        except (ValueError, TypeError):
            continue
        conf = float(r["confidence"] or 0)
        if conf < thr:
            continue
        for tid in tps:
            a = agg[d].setdefault(tid, {"units": 0, "best": 0.0, "files": set()})
            a["units"] += 1
            a["files"].add(r["rel_path"])
            a["best"] = max(a["best"], conf)
    bygf = _subjects_by_graph_file()
    for d in domains():
        did = d["id"]
        if not agg.get(did):
            continue
        s = bygf.get(d.get("graph_file") or "") or {}
        name = (s.get(key) or s.get("short") or s.get("id")
                or d.get("subject") or did)
        # files 出集合，对外换成「篇数」：段数会被批量生成的词条卡灌水
        # （英语辨析 398 个文件一人一段，报「2563 段」毫无意义，报「多少篇」才有）。
        out[name] = {tid: {"units": v["units"], "best": v["best"], "files": len(v["files"])}
                     for tid, v in agg[did].items()}
    conn.close()
    return out


def units_for_topic(subject: str, topic_id: str, db_path=None, limit: int = 20) -> list:
    """某个考点下的材料段（给每日任务当载体、给笔记页当出处）。"""
    d = domain_for_subject(subject)
    if not d:
        return []
    conn = _connect(db_path)
    if not conn:
        return []
    try:
        rows = conn.execute(
            "SELECT rel_path, heading, kind, confidence, evidence FROM note_attributions "
            "WHERE domain = ? AND topic_ids LIKE ? ORDER BY confidence DESC LIMIT ?",
            (d["id"], '%%"%s"%%' % topic_id, limit)).fetchall()
    except sqlite3.Error:
        conn.close()
        return []
    out = [dict(r) for r in rows]
    conn.close()
    return out


def cross_cutting(subject: str, db_path=None, limit: int = 40) -> list:
    """跨考点方法论材料。它们**不该**被塞进任何一格，但必须看得见 ——
    这是老口径最容易被误读成「没笔记」的一类（政治的专题/强化错题就栽在这）。"""
    d = domain_for_subject(subject)
    if not d:
        return []
    conn = _connect(db_path)
    if not conn:
        return []
    try:
        rows = conn.execute(
            "SELECT rel_path, heading, confidence, evidence FROM note_attributions "
            "WHERE domain = ? AND kind = 'cross_cutting' ORDER BY confidence DESC LIMIT ?",
            (d["id"], limit)).fetchall()
    except sqlite3.Error:
        conn.close()
        return []
    out = [dict(r) for r in rows]
    conn.close()
    return out


def status(db_path=None) -> dict:
    """每个 domain 的归因覆盖情况：跑了多少段、最后什么时候。给设置页/体检用。"""
    conn = _connect(db_path)
    if not conn:
        return {}
    out = {}
    try:
        for d in domains():
            row = conn.execute(
                "SELECT COUNT(*) n, MAX(updated_at) t, AVG(confidence) c "
                "FROM note_attributions WHERE domain = ?", (d["id"],)).fetchone()
            out[d["id"]] = {"units": row["n"] if row else 0,
                            "last": (row["t"] if row else None),
                            "avg_conf": round(row["c"], 2) if row and row["c"] is not None else None}
    except sqlite3.Error:
        conn.close()
        return {}
    conn.close()
    return out


if __name__ == "__main__":
    import io
    import sys
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    print("口径: counting_kinds =", sorted(counting_kinds()), " accept =", thresholds().get("accept"))
    print("状态:", json.dumps(status(), ensure_ascii=False, indent=1))
    cov = coverage_by_subject()
    for subj, m in cov.items():
        print(f"  {subj}: AI 判有笔记 {len(m)} 个考点")

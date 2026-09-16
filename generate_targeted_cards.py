#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
generate_targeted_cards.py — 针对性闪卡生成器（盘活笔记）

不追求全面覆盖，只基于三类信号针对性出题：
  1. 闪卡错误点：lapses>0 或近30天答错(rating<=2)的知识点 → 强化卡
  2. 近日笔记：最近 7 天修改过的笔记内容 → 盘活卡（把刚整理的知识固化）
  3. 高分考点无卡验证：exam_weight>=2 且无卡片的考点 → 验证卡
     （用户可能因内容简单而不整理笔记；答对即视为掌握，不再提醒薄弱）

生成的题目写入 question_bank.db（questions + cards 表），
在大盘闪卡练习区按优先级推送。

用法:
    python generate_targeted_cards.py            # 默认生成 12 题
    python generate_targeted_cards.py --count 20 # 指定数量
    python generate_targeted_cards.py --prefix 408-OS --prefix MATH-XD  # 指定冷笔记前缀盘活
    python generate_targeted_cards.py --dry-run  # 只展示素材，不调用 LLM
"""

import argparse
import io
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import date, datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(r"C:\Users\92534\Desktop\考研")
DB_PATH = BASE_DIR / "src" / "question_bank.db"
DASH_DATA = BASE_DIR / "src" / "dashboard_data.json"

# 笔记目录 → topic 前缀（与 generate_dashboard.py 保持一致）
NOTE_PREFIX_MAP = {
    "408/DS": "408-DS", "408/CO": "408-CO", "408/OS": "408-OS", "408/CN": "408-CN",
    "Math/高数": "MATH-GS", "Math/线代": "MATH-XD", "Math/概率论": "MATH-GL",
    "Politics/马原": "POL-MY", "Politics/史纲": "POL-SG", "Politics/毛中特": "POL-MZ",
    "Politics/思修": "POL-SX", "Politics/习思想": "POL-XX",
    "English/word&phrase": "ENG-VOC", "English/grammar": "ENG-GRAM",
    "English/reading&magazines": "ENG-READ", "English/translation&write": "ENG-WRIT",
}

# 题干关键词 → 知识点前缀（避免回退匹配跨科串题）
KEYWORD_PREFIXES = [
    ("线代", "MATH-XD"), ("线性", "MATH-XD"), ("矩阵", "MATH-XD"), ("向量", "MATH-XD"),
    ("方程组", "MATH-XD"), ("行列式", "MATH-XD"), ("特征值", "MATH-XD"), ("二次型", "MATH-XD"),
    ("极限", "MATH-GS"), ("导数", "MATH-GS"), ("微分", "MATH-GS"), ("积分", "MATH-GS"),
    ("级数", "MATH-GS"), ("函数", "MATH-GS"), ("曲线", "MATH-GS"),
    ("随机变量", "MATH-GL"), ("概率", "MATH-GL"), ("分布", "MATH-GL"), ("期望", "MATH-GL"),
    ("大作文", "ENG-WRITE"), ("小作文", "ENG-WRITE"), ("作文", "ENG-WRITE"),
    ("阅读", "ENG-READ"), ("新题型", "ENG-READ"), ("七选五", "ENG-READ"),
    ("词根", "ENG-VOC"), ("词缀", "ENG-VOC"), ("单词", "ENG-VOC"),
    ("语法", "ENG-GRAM"), ("翻译", "ENG-TRN"),
    ("数据结构", "408-DS"), ("树", "408-DS"), ("图", "408-DS"), ("排序", "408-DS"), ("查找", "408-DS"),
    ("流水线", "408-CO"), ("存储器", "408-CO"), ("指令", "408-CO"), ("总线", "408-CO"),
    ("进程", "408-OS"), ("内存", "408-OS"), ("文件系统", "408-OS"),
    ("协议", "408-CN"), ("TCP", "408-CN"), ("路由", "408-CN"),
    ("马克思", "POL-MY"), ("唯物", "POL-MY"), ("辩证", "POL-MY"),
    ("毛泽东", "POL-MZ"), ("中国特色", "POL-MZ"),
]

# ---------------------------------------------------------------------------
# 选项质量闸门：判定规则与审计器共用 tools/card_quality.py（单一事实来源）
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
from card_quality import check_options_quality  # noqa: E402

RECENT_DAYS = 7
MAX_NOTE_FILES = 4
NOTE_EXCERPT_CHARS = 1600


# ---------------------------------------------------------------------------
# 素材收集
# ---------------------------------------------------------------------------

def collect_recent_notes() -> list:
    """收集近 N 天修改的笔记（路径 + 内容摘录），用于盘活卡。"""
    cutoff = time.time() - RECENT_DAYS * 86400
    hits = []
    for rel, prefix in NOTE_PREFIX_MAP.items():
        d = BASE_DIR / rel
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.suffix.lower() != ".md":
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    continue
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            hits.append({
                "prefix": prefix,
                "file": f"{rel}/{f.name}",
                "mtime": f.stat().st_mtime,
                "excerpt": text[:NOTE_EXCERPT_CHARS],
            })
    hits.sort(key=lambda x: x["mtime"], reverse=True)
    return hits[:MAX_NOTE_FILES]


def _note_last_active(f: Path, touched: dict, relpath: str) -> float:
    """笔记最近活跃时间 = max(修改时间, 大盘标记盘活时间)。"""
    ts = f.stat().st_mtime
    t = touched.get(relpath, "")
    if t:
        try:
            ts = max(ts, datetime.fromisoformat(str(t)[:19]).timestamp())
        except Exception:
            pass
    return ts


def collect_cold_notes(prefixes: list) -> list:
    """盘活素材：指定前缀下最久未动的笔记（不受近 N 天限制）。"""
    touched = {}
    if Path(DASH_DATA).parent.joinpath("note_reviews.json").exists():
        try:
            touched = json.loads(
                Path(DASH_DATA).parent.joinpath("note_reviews.json")
                .read_text(encoding="utf-8")).get("touched", {})
        except Exception:
            pass
    hits = []
    for rel, prefix in NOTE_PREFIX_MAP.items():
        if prefix not in prefixes:
            continue
        d = BASE_DIR / rel
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.suffix.lower() != ".md" or f.name.startswith("_"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
                active = _note_last_active(f, touched, f"{rel}/{f.name}")
            except OSError:
                continue
            hits.append({
                "prefix": prefix,
                "file": f"{rel}/{f.name}",
                "mtime": active,
                "excerpt": text[:NOTE_EXCERPT_CHARS],
            })
    # 最久未动的优先（最需要盘活）
    hits.sort(key=lambda x: x["mtime"])
    return hits[:MAX_NOTE_FILES]


def collect_weak_topics(conn) -> list:
    """闪卡错误点：lapses>0 或近30天 rating<=2 的 topic。"""
    rows = conn.execute("""
        SELECT t.id, t.name, t.subject,
               COALESCE(SUM(cd.lapses), 0) AS lapse_cnt,
               COALESCE(SUM(CASE WHEN rl.rating <= 2
                      AND rl.review_date >= datetime('now', 'localtime', '-30 days')
                      THEN 1 ELSE 0 END), 0) AS recent_wrong
        FROM topics t
        LEFT JOIN questions q   ON q.topic_id = t.id
        LEFT JOIN cards cd      ON cd.question_id = q.id
        LEFT JOIN review_log rl ON rl.question_id = q.id
        GROUP BY t.id
        HAVING lapse_cnt > 0 OR recent_wrong > 0
        ORDER BY (recent_wrong * 2 + lapse_cnt) DESC
        LIMIT 6
    """).fetchall()
    return [{"id": r[0], "name": r[1], "subject": r[2],
             "lapses": r[3], "recent_wrong": r[4]} for r in rows]


def collect_uncovered_weighty(conn) -> list:
    """高分考点但无卡片 → 验证卡候选（答对即证明掌握）。"""
    rows = conn.execute("""
        SELECT t.id, t.name, t.subject, t.exam_weight
        FROM topics t
        WHERE t.exam_weight >= 2
          AND t.id NOT IN (SELECT DISTINCT topic_id FROM questions)
        ORDER BY t.exam_weight DESC
        LIMIT 6
    """).fetchall()
    return [{"id": r[0], "name": r[1], "subject": r[2], "weight": r[3]} for r in rows]


# ---------------------------------------------------------------------------
# LLM 出题
# ---------------------------------------------------------------------------

def build_prompt(notes, weak, uncovered, count) -> str:
    parts = [
        "你是考研命题专家。请基于下面的学习素材，针对性地出 "
        f"{count} 道考研复习题（选择/判断/填空混合，选择为主）。",
        "",
        "出题要求：",
        "1. 优先围绕【薄弱知识点】出强化题，考查易混淆、易错的细节；",
        "2. 围绕【近日笔记素材】出盘活题，考查笔记中的核心概念、公式、结论与辨析；",
        "3. 围绕【待验证高分考点】出代表性验证题，考查该考点最核心的内容，难度适中；",
        "4. 题干严谨无歧义，选择题恰有 4 个选项且只有 1 个正确；",
        "5. 解析讲清为什么对/错，陷阱写学生最容易犯的错误；",
        "6. 【选项形态必须完全一致】四个选项要用同一种写法，绝不能一条是裸释义、"
        "其余三条带“词缀(释义)”或“术语(解释)”标签——那样正确答案会被格式本身暴露，"
        "学生不看知识就能选对。反例（严禁）："
        '["否定/相反/分离", "re-(再/重新)", "con-(共同/一起)", "pre-(前/预先)"]；',
        "7. 【干扰项必须是真干扰】取自同一语义场的易混项（前缀用其他前缀的含义互扰、"
        "后缀用其他后缀互扰、相近概念互相辨析），不能是“完全无关”“以上都对”"
        "之类的凑数项，也不能与正确项语义重复；",
        "8. 【选项长度要接近】最长项不要超过最短项的两倍，避免“最长即答案”；"
        "选项内不要自带 A. / B. / C. / D. 字母前缀（渲染层会自动加徽章）。",
        "",
    ]

    if weak:
        parts.append("【薄弱知识点】（重点出题）：")
        for w in weak:
            parts.append(f"- {w['subject']} / {w['name']}（遗忘{w['lapses']}次，近期答错{w['recent_wrong']}次）")
        parts.append("")
    if uncovered:
        parts.append("【待验证高分考点】（每个考点出1道代表性验证题）：")
        for u in uncovered:
            parts.append(f"- {u['subject']} / {u['name']}（分值权重{u['weight']}）")
        parts.append("")
    if notes:
        parts.append("【近日笔记素材】：")
        for n in notes:
            parts.append(f"### {n['file']}\n{n['excerpt']}\n")
        parts.append("")

    parts += [
        "严格输出 JSON 数组（不要任何 markdown 代码块或解释文字），每题格式：",
        '{"type":"choice","topic":"出题来源的知识点名称","stem":"题干",'
        '"options":["A项","B项","C项","D项"],"answer":0,'
        '"explanation":"解析","traps":["易错点1"],"tags":["关键词"]}',
        '判断题：{"type":"judge","topic":"...","stem":"...","answer":"正确或错误",'
        '"explanation":"...","traps":[],"tags":[]}',
        '填空题：{"type":"fill","topic":"...","stem":"用____表示空缺","answer":"答案",'
        '"explanation":"...","traps":[],"tags":[]}',
        '注意：topic 字段必须使用上面列出的知识点名称之一或笔记的章节主题。',
    ]
    return "\n".join(parts)


def call_llm(prompt: str) -> str:
    """通过 bl CLI 调用百炼大模型。"""
    bl_path = shutil.which("bl") or shutil.which("bl.cmd") or shutil.which("bl.exe")
    if not bl_path:
        raise RuntimeError("未找到 bl CLI（bailian-cli），请先安装并登录")

    msg_file = BASE_DIR / "src" / "_targeted_prompt.json"
    msg_file.write_text(
        json.dumps([{"role": "user", "content": prompt}], ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        proc = subprocess.run(
            [bl_path, "text", "chat", "--messages-file", str(msg_file),
             "--max-tokens", "8000", "--temperature", "0.7", "--output", "json"],
            capture_output=True, text=True, encoding="utf-8", timeout=600,
        )
    finally:
        msg_file.unlink(missing_ok=True)

    if proc.returncode != 0:
        raise RuntimeError(f"bl 调用失败: {proc.stderr[:500]}")
    resp = json.loads(proc.stdout)
    return resp["choices"][0]["message"]["content"]


def parse_questions(raw: str) -> list:
    """从 LLM 回复中稳健地提取 JSON 数组。"""
    raw = raw.strip()
    m = re.search(r"\[\s*\{.*\}\s*\]", raw, re.DOTALL)
    if not m:
        raise RuntimeError("LLM 回复中未找到 JSON 数组")
    return json.loads(m.group(0))


# ---------------------------------------------------------------------------
# 入库
# ---------------------------------------------------------------------------

def match_topic_id(conn, topic_name: str, stem: str, fallback_prefixes: list) -> str:
    """把 LLM 给出的知识点名称匹配到 topics.id。"""
    if not topic_name:
        topic_name = ""
    # 精确匹配
    row = conn.execute("SELECT id FROM topics WHERE name = ?", (topic_name,)).fetchone()
    if row:
        return row[0]
    # 包含匹配
    row = conn.execute(
        "SELECT id FROM topics WHERE name LIKE ? ORDER BY LENGTH(name) LIMIT 1",
        (f"%{topic_name}%",),
    ).fetchone() if len(topic_name) >= 3 else None
    if row:
        return row[0]
    row = conn.execute(
        "SELECT id FROM topics WHERE ? LIKE '%'||name||'%' ORDER BY LENGTH(name) DESC LIMIT 1",
        (topic_name,),
    ).fetchone()
    if row:
        return row[0]
    # 题干关键词 → 学科前缀（防止跨科串题）
    kw_prefixes = [pfx for kw, pfx in KEYWORD_PREFIXES if kw in (stem or "")]
    for pfx in kw_prefixes + fallback_prefixes:
        row = conn.execute(
            "SELECT id FROM topics WHERE id LIKE ? ORDER BY exam_weight DESC LIMIT 1",
            (pfx + "%",),
        ).fetchone()
        if row:
            return row[0]
    return ""


def insert_questions(questions: list, fallback_prefixes: list) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    today = date.today().isoformat()
    inserted = 0

    for q in questions:
        stem = str(q.get("stem", "")).strip()
        if not stem:
            continue
        # 去重：同题干不重复入库
        dup = conn.execute(
            "SELECT id FROM questions WHERE content LIKE ?", (f"%{stem[:60]}%",)
        ).fetchone()
        if dup:
            print(f"  [跳过·重复] {stem[:40]}")
            continue

        qtype = q.get("type", "choice")
        if qtype not in ("choice", "judge", "fill"):
            qtype = "choice"
        content = {
            "stem": stem,
            "explanation": q.get("explanation", ""),
            "tags": q.get("tags", []),
            "traps": [t for t in q.get("traps", []) if t],
        }
        if qtype == "choice":
            opts = q.get("options") or []
            if len(opts) != 4 or not isinstance(q.get("answer"), int):
                print(f"  [跳过·选项不完整] {stem[:40]}")
                continue
            if not 0 <= q["answer"] < len(opts):
                print(f"  [跳过·答案越界] {stem[:40]}")
                continue
            problems = check_options_quality(opts)
            if problems:
                print(f"  [跳过·选项质量] {stem[:40]} → {'；'.join(problems)}")
                continue
            content["options"] = opts
            content["answer"] = q["answer"]
        else:
            content["answer"] = str(q.get("answer", ""))
            if not content["answer"]:
                print(f"  [跳过·无答案] {stem[:40]}")
                continue

        topic_id = match_topic_id(conn, q.get("topic", ""), stem, fallback_prefixes)
        qid = f"Q-TGT-{uuid.uuid4().hex[:8].upper()}"
        cid = f"C-TGT-{uuid.uuid4().hex[:8].upper()}"

        conn.execute(
            "INSERT INTO questions (id, topic_id, type, difficulty, source, content) "
            "VALUES (?, ?, ?, 0.5, ?, ?)",
            (qid, topic_id or None, qtype, f"针对性生成-{today}",
             json.dumps(content, ensure_ascii=False)),
        )
        conn.execute(
            "INSERT INTO cards (id, question_id, state, due_date) VALUES (?, ?, 0, ?)",
            (cid, qid, today),
        )
        inserted += 1
        print(f"  [入库] ({qtype}) {stem[:50]} → {topic_id or '未匹配知识点'}")

    conn.commit()
    conn.close()
    return inserted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="针对性闪卡生成器")
    ap.add_argument("--count", type=int, default=12, help="生成题目数量（默认12）")
    ap.add_argument("--prefix", action="append", default=[],
                    help="指定冷笔记前缀盘活（可多次），如 408-OS")
    ap.add_argument("--dry-run", action="store_true", help="只展示素材，不调用 LLM")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: 题库不存在: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    if args.prefix:
        notes = collect_cold_notes(set(args.prefix))
        print(f"[盘活模式] 针对前缀 {args.prefix} 收集到 {len(notes)} 篇冷笔记")
    else:
        notes = collect_recent_notes()
    weak = collect_weak_topics(conn)
    uncovered = collect_uncovered_weighty(conn)
    conn.close()

    # 回退前缀：近日笔记对应前缀优先
    fallback_prefixes = sorted({n["prefix"] for n in notes})
    if not fallback_prefixes and Path(DASH_DATA).exists():
        try:
            fallback_prefixes = json.loads(
                DASH_DATA.read_text(encoding="utf-8")).get("recent_prefixes", [])
        except Exception:
            pass

    print(f"素材盘点：近日笔记 {len(notes)} 篇 · 薄弱知识点 {len(weak)} 个 · "
          f"待验证高分考点 {len(uncovered)} 个")
    for n in notes:
        print(f"  📝 {n['file']}")
    for w in weak:
        print(f"  ⚠️ {w['subject']} / {w['name']}（遗忘{w['lapses']} 近期错{w['recent_wrong']}）")
    for u in uncovered:
        print(f"  🔍 {u['subject']} / {u['name']}（权重{u['weight']}）")

    if not notes and not weak and not uncovered:
        print("无针对性素材，退出。")
        return

    prompt = build_prompt(notes, weak, uncovered, args.count)
    if args.dry_run:
        print("\n[dry-run] prompt 预览：\n" + prompt[:800] + " ...")
        return

    print(f"\n调用百炼生成 {args.count} 题（qwen3.7-max）...")
    raw = call_llm(prompt)
    try:
        questions = parse_questions(raw)
    except Exception as e:
        print(f"ERROR: 解析 LLM 输出失败: {e}")
        print("原始回复前500字:", raw[:500])
        sys.exit(1)

    print(f"LLM 返回 {len(questions)} 题，开始入库...")
    inserted = insert_questions(questions[:args.count], fallback_prefixes)
    print(f"\n完成：新增 {inserted} 张闪卡（source=针对性生成-{date.today().isoformat()}）")
    print("提示：运行 python src/generate_dashboard.py 刷新大盘后，在闪卡练习区即可刷到新卡。")


if __name__ == "__main__":
    main()

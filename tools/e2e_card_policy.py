"""端到端验证组题策略与 📌 钉住（跑在题库副本 + 独立端口上，不碰用户的实例）。

用法：python tools/e2e_card_policy.py
"""
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DB = os.path.join(SRC, "question_bank.db")
NODE = r"C:\Program Files\nodejs\node.exe"
PORT = "18099"
BASE = f"http://127.0.0.1:{PORT}"
out = []
def p(*a):
    line = " ".join(str(x) for x in a)
    out.append(line)
    print(line)

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

def post(path, obj):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode("utf-8"),
                                headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

def post_status(path, obj):
    """允许失败：返回 (status, body)，用来验「非法卡号被拒」。"""
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode("utf-8"),
                                headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}

tmp_db = os.path.join(tempfile.gettempdir(), "kaoyan_e2e_policy.db")
shutil.copyfile(REAL_DB, tmp_db)
portfile = os.path.join(SRC, ".serve_port")
port_before = open(portfile, encoding="utf-8").read() if os.path.exists(portfile) else None

# --- 造一个确定的小世界 -------------------------------------------------
db = sqlite3.connect(tmp_db)
c = db.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS card_pins (card_id TEXT PRIMARY KEY, note TEXT,
             created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')), updated_at TEXT)""")
c.execute("DELETE FROM card_pins")
# 挑三张「复习中、且按 FSRS 还没到期」的卡——正是会被 due 检查挡掉的那一类
rows = c.execute("""SELECT c.id, c.question_id FROM cards c
                    WHERE COALESCE(c.state,0)=2 AND COALESCE(c.suspended,0)=0 AND COALESCE(c.leech,0)=0
                    ORDER BY c.id LIMIT 5""").fetchall()
PIN_CARD, PIN_Q = rows[0]
RETIRE_CARD, RETIRE_Q = rows[1]
CLEAN_CARD, CLEAN_Q = rows[2]
ASK_CARD, ASK_Q = rows[3]          # 问过但还没学会 → 应该置顶档、不被退役
ASK_EXIT_CARD, ASK_EXIT_Q = rows[4]  # 问过、之后又连对 3 次 → 应该退出豁免（被退役）
# 1) 把它们的到期时间推到未来（确保「未到期」），并且清掉它们的历史答题记录
for cid, qid in rows:
    c.execute("UPDATE cards SET due_at = datetime('now','localtime','+30 days'), "
              "due_date = date('now','localtime','+30 days') WHERE id = ?", (cid,))
    c.execute("DELETE FROM review_log WHERE card_id = ?", (cid,))
# 2) RETIRE_CARD：连续答对 3 次（没问过、没钉住）→ 应被退役
for d in (5, 3, 1):
    c.execute("INSERT INTO review_log (card_id, question_id, rating, review_date) "
              "VALUES (?,?,4,datetime('now','localtime',?))", (RETIRE_CARD, RETIRE_Q, f"-{d} days"))
# 3) CLEAN_CARD：连续答对 2 次、且**今天到期** → 降权（不退役，但在组里排最后）
#    ⚠️ 必须让它到期：没到期的复习卡本来就不会进智能组（due 检查），
#    那种情况下「降权」与否根本无从观察（第一版断言就是这么假红的）。
for d in (3, 1):
    c.execute("INSERT INTO review_log (card_id, question_id, rating, review_date) "
              "VALUES (?,?,3,datetime('now','localtime',?))", (CLEAN_CARD, CLEAN_Q, f"-{d} days"))
c.execute("UPDATE cards SET due_at = datetime('now','localtime','-1 day'), "
          "due_date = date('now','localtime','-1 day') WHERE id = ?", (CLEAN_CARD,))

# 4) ASK_CARD：提问过、提问**之后**只连对 2 次 → 还算卡点，应该进组且是 ASKED 档（9）
c.execute("DELETE FROM explain_log WHERE question_id IN (?, ?)", (ASK_Q, ASK_EXIT_Q))
c.execute("DELETE FROM review_log WHERE card_id IN (?, ?)", (ASK_CARD, ASK_EXIT_CARD))
for qid in (ASK_Q, ASK_EXIT_Q):
    # card_id 故意留 NULL —— 真实库里 explain_log.card_id 全是空的（追问只能按 question_id 关联）
    c.execute("INSERT INTO explain_log (thread_id, question_id, card_id, role, content, created_at) "
              "VALUES (?, ?, NULL, 'user', '【我问】这里为什么这样？', "
              "        datetime('now','localtime','-14 days'))", ("t-" + qid, qid))
for d in (2, 1):
    c.execute("INSERT INTO review_log (card_id, question_id, rating, review_date) "
              "VALUES (?,?,4,datetime('now','localtime',?))", (ASK_CARD, ASK_Q, f"-{d} days"))
# 5) ASK_EXIT_CARD：提问后又连对 3 次 → 豁免作废，按普通连对规则退役
for d in (3, 2, 1):
    c.execute("INSERT INTO review_log (card_id, question_id, rating, review_date) "
              "VALUES (?,?,4,datetime('now','localtime',?))", (ASK_EXIT_CARD, ASK_EXIT_Q, f"-{d} days"))
# 让这两张都到期，否则不参与组题、观察不到优先级差异
for cid in (ASK_CARD, ASK_EXIT_CARD):
    c.execute("UPDATE cards SET due_at = datetime('now','localtime','-1 day'), "
              "due_date = date('now','localtime','-1 day') WHERE id = ?", (cid,))
db.commit()
db.close()

proc = subprocess.Popen([NODE, os.path.join(SRC, "serve.js")],
                        cwd=SRC, env=dict(os.environ, DB_PATH=tmp_db, PORT=PORT),
                        stdout=subprocess.DEVNULL,
                        stderr=open(os.path.join(SRC, "_e2e_server.err"), "w", encoding="utf-8"))
try:
    ok_ready = False
    for _ in range(40):
        try:
            urllib.request.urlopen(BASE + "/dashboard.html", timeout=3).read(100)
            ok_ready = True
            break
        except Exception:
            time.sleep(0.5)
    if not ok_ready:
        p("服务没起来，放弃")
        raise SystemExit(1)

    # --- 1. 钉住一张「未到期」的卡 -------------------------------------
    p("== 1. 📌 钉住：写入 / 清单 ==")
    r = post("/api/flashcards/pin", {"card_id": PIN_CARD, "pinned": True, "note": "E2E"})
    p("  POST pin →", json.dumps(r, ensure_ascii=False))
    pins = get("/api/flashcards/pins?limit=10")
    p("  GET pins → count =", pins["count"], "| 含目标卡 =", any(x["card_id"] == PIN_CARD for x in pins["pins"]))
    bad_status, bad = post_status("/api/flashcards/pin", {"card_id": "C-不存在-1", "pinned": True})
    p("  不存在的卡号被拒 → HTTP", bad_status, json.dumps(bad, ensure_ascii=False))
    inj_status, inj = post_status("/api/flashcards/pin",
                                  {"card_id": "'; DROP TABLE cards;--", "pinned": True})
    p("  ★ SQL 片段当卡号 → 拒（HTTP " + str(inj_status) + "，白名单拦下）",
      not inj.get("ok"))
    p("  ★ 拒绝后 cards 表还在:",
      get("/api/flashcards/facets")["totals"].get("total", 0) > 0)

    # --- 2. 智能组：退役 + 钉住置顶 + 追问保留 --------------------------
    p("\n== 2. 智能组（mode=smart, limit=200）==")
    s = get("/api/flashcards/session?limit=200")
    ids = [x["card_id"] for x in s["cards"]]
    pol = s.get("policy", {})
    p("  policy =", json.dumps(pol, ensure_ascii=False))
    p("  ★ 连对 3 次的卡被退役（不在组里）:", RETIRE_CARD not in ids)
    p("  ★ 退役计数 > 0:", pol.get("retired_count", 0) > 0)
    p("  ★ 钉住的卡进组:", PIN_CARD in ids)
    pin_card = next((x for x in s["cards"] if x["card_id"] == PIN_CARD), None)
    p("  ★ 钉住的卡带上 pin 字段:", bool(pin_card and pin_card.get("pin")))
    p("  ★ 钉住的卡排在最前（优先级第 1 档）:", pin_card is not None
      and pin_card.get("smart_prio") == 10)
    p("  ★ 钉住的卡虽然未到期仍在组里（不被 due 检查挡掉）:", pin_card is not None
      and pin_card.get("due_now") is False)
    clean = next((x for x in s["cards"] if x["card_id"] == CLEAN_CARD), None)
    p("  ★ 连对 2 次、今天到期的卡仍在组里但被降权（smart_prio=2）:",
      bool(clean and clean.get("smart_prio") == 2),
      "prio=" + str(clean and clean.get("smart_prio")))
    p("  ★ 降权档确实排在薄弱/到期卡之后:",
      bool(clean) and all(
          (x.get("smart_prio") or 0) >= 2 for x in s["cards"])
      and max((x.get("smart_prio") or 0) for x in s["cards"]) > 2)
    asked = [x for x in s["cards"] if (x.get("ask_count") or 0) > 0]
    p("  ★ 下发了「问过 AI 几次」字段，命中", len(asked), "张（这类卡不会被退役）")

    # --- 2b. 「问过」的出口（2026-09-19 用户指出的漏洞）------------------
    p("\n== 2b. 问过的卡必须有出口：提问后又连对够多次就该退出待遇 ==")
    ask_card = next((x for x in s["cards"] if x["card_id"] == ASK_CARD), None)
    p("  ★ 问过、之后只连对 2 次的卡：仍在组里", bool(ask_card))
    p("  ★   并且是 ASKED 档（优先级 9）",
      bool(ask_card and ask_card.get("smart_prio") == 9),
      "prio=" + str(ask_card and ask_card.get("smart_prio")))
    p("  ★   卡片上带着「提问后已连对 2 次」的进度（前端画得出出口进度）",
      bool(ask_card and ask_card.get("ask_after_correct") == 2),
      "after=" + str(ask_card and ask_card.get("ask_after_correct")))
    p("  ★   豁免状态仍是 true（还没学会）",
      bool(ask_card and ask_card.get("ask_exempt") is True))
    p("  ★ 问过、但提问后又连对 3 次的卡：**已经退出**（不在智能组里）",
      ASK_EXIT_CARD not in ids)
    p("  ★ 出口阈值随 session 下发（前端才能写「已连对 2/3」）:",
      pol.get("ask_exit_streak"))

    # --- 3. 退役只作用于智能组：自选/筛选页仍能看到 ---------------------
    p("\n== 3. 自选通道（browse / bucket=pinned）==")
    b = get("/api/flashcards/session?limit=50&bucket=mature&mode=browse")
    p("  browse 不过滤（policy.applied =", b.get("policy", {}).get("applied"), "，退役数 "
      + str(b.get("policy", {}).get("retired_count")) + "）")
    pinb = get("/api/flashcards/session?limit=50&bucket=pinned&mode=browse")
    p("  ★ bucket=pinned 能取到钉住的卡:", PIN_CARD in [x["card_id"] for x in pinb["cards"]])

    # --- 4. facets 的 pinned 列 -----------------------------------------
    p("\n== 4. 筛选页数量（facets）==")
    f = get("/api/flashcards/facets")
    p("  totals.pinned =", f["totals"].get("pinned"), "（合计在场 =",
      "pinned" in f["totals"], "）")

    # --- 5. 取消钉住 -----------------------------------------------------
    p("\n== 5. 取消钉住 ==")
    r2 = post("/api/flashcards/pin", {"card_id": PIN_CARD, "pinned": False})
    p("  POST unpin →", json.dumps(r2, ensure_ascii=False))
    p("  ★ 清单归零:", get("/api/flashcards/pins").get("count") == 0)
    s2 = get("/api/flashcards/session?limit=200")
    p("  ★ 取消后不再置顶（pin 字段为空）:",
      not any(x["card_id"] == PIN_CARD and x.get("pin") for x in s2["cards"]))
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    # ⚠️ 测试实例会把端口写进 .serve_port —— 必须还原，否则「启动考研大盘.bat」会开错端口
    if port_before is not None:
        open(portfile, "w", encoding="utf-8").write(port_before)
    try:
        os.remove(tmp_db)
    except OSError:
        pass

open(os.path.join(SRC, "_e2e_card_policy.txt"), "w", encoding="utf-8").write("\n".join(out))
print("\n结果已写入 src/_e2e_card_policy.txt")

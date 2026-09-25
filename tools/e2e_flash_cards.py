"""闪卡出卡问题的端到端验证（2026-09-20）。

不动用户的实例：题库**复制**一份、换端口起独立 serve.js，再用 CDP 探针（tools/e2e_probe.js，
带 --pre-file 在页面脚本之前预置 localStorage＝用户浏览器里那份「上一版留下的点名脏值」）
在真浏览器里点给真人看的那些按钮。最后一定还原 src/.serve_port
（否则「启动考研大盘.bat」会开错端口）。

验证：
  ① 昨天点名（ids）的那一组不再粘住「放弃·重新挑一组」——点了要回到智能组题（不带 ids=）；
  ② 头部「🔁 再来 N 张」的张数来自设置，点它真的按 N 张走 extra 通道，并写明这一组的来路。

用法：python tools\\e2e_flash_cards.py   → 看输出的 pass/fail 与 tools/e2e_flash_shot.png
"""
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 代码根（__file__ 推导，不硬编码）
NODE = r"C:\Program Files\nodejs\node.exe"
PORT = 18098
COPY = os.path.join(SRC, "tools", "_e2e_flash_copy.db")     # 跑完删掉（副本，不是他的库）
SHOT = os.path.join(SRC, "tools", "e2e_flash_shot.png")
PORT_FILE = os.path.join(SRC, ".serve_port")

ok_count = [0]
bad_count = [0]


def check(name, cond, extra=""):
    if cond:
        ok_count[0] += 1
        print("  ok   " + name)
    else:
        bad_count[0] += 1
        print("  FAIL " + name + ("  → " + str(extra) if extra else ""))


def study_day(d=None):
    """跟 serve.js 的 localToday() 同一套：凌晨 4 点前算前一天。"""
    t = d or datetime.now()
    if t.hour < 4:
        t = t - timedelta(days=1)
    return t.strftime("%Y-%m-%d")


def seed_copy():
    """把用户的库复制一份，并把「昨天点名的那一组」摆回 flash_session 里。"""
    src = os.path.join(SRC, "question_bank.db")
    if os.path.exists(COPY):
        os.remove(COPY)
    shutil.copy2(src, COPY)
    con = sqlite3.connect(COPY)
    cur = con.cursor()
    # 用户当前真实那 10 张（截图里那组 AI 出的题）
    ids = ["C-STUDY-C882E54F", "C-STUDY-29DF6BAD", "C-STUDY-AA93743D", "C-STUDY-B8B6A730",
           "C-STUDY-54C47066", "C-STUDY-3917B2B8", "C-STUDY-239701A4", "C-STUDY-F5323C41",
           "C-STUDY-2397FDE4", "C-STUDY-88147B6E"]
    cards = []
    for cid in ids:
        r = cur.execute(
            "SELECT c.id, c.state, c.reps, c.lapses, c.due_date, c.due_at, c.interval_days,"
            " c.learning_step, c.relearning_step, c.leech, c.suspended,"
            " q.id, q.type, q.topic_id, q.content, t.name, COALESCE(t.subject,'')"
            " FROM cards c JOIN questions q ON c.question_id=q.id"
            " LEFT JOIN topics t ON q.topic_id=t.id WHERE c.id=?", (cid,)).fetchone()
        if not r:
            continue
        try:
            content = json.loads(r[14])
        except Exception:
            content = {"stem": r[14]}
        cards.append({
            "card_id": r[0], "question_id": r[11], "type": r[12], "topic_id": r[13],
            "topic_name": r[15] or "", "subject": r[16], "state": r[1], "reps": r[2],
            "lapses": r[3], "due_date": r[4], "due_at": r[5], "interval_days": r[6],
            "learning_step": r[7] or 0, "relearning_step": r[8] or 0, "leech": r[9] or 0,
            "suspended": r[10] or 0, "due_now": False, "content": content,
            "report": None, "pin": None, "ask_count": 0, "ask_after_correct": 0,
            "ask_exempt": False, "streak": 0, "smart_prio": 1,
        })
    sess = {"date": study_day(), "cards": cards, "idx": 0,
            "filter": {"subject": "", "bucket": "", "topic": "",
                       "ids": [c["card_id"] for c in cards]},
            "updated": datetime.now().isoformat(), "device": "电脑"}
    cur.execute("INSERT INTO config (key, value, updated_at) VALUES ('flash_session', ?, datetime('now','localtime'))"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (json.dumps(sess, ensure_ascii=False),))
    cur.execute("INSERT INTO config (key, value, updated_at) VALUES ('flash_extra_count', '20', datetime('now','localtime'))"
                " ON CONFLICT(key) DO UPDATE SET value='20', updated_at=datetime('now','localtime')")
    con.commit()
    con.close()
    return len(cards)


def get_raw(path):
    """拿原始文本（就绪探测用：/dashboard.html 是 HTML，不是 JSON）。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def get(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def main():
    n = seed_copy()
    print("== 副本就绪：点名组 %d 张，flash_extra_count=20 ==" % n)
    old_port = None
    if os.path.exists(PORT_FILE):
        old_port = open(PORT_FILE, encoding="utf-8").read().strip()
        os.remove(PORT_FILE)
    env = dict(os.environ, DB_PATH=COPY, PORT=str(PORT))
    log = open(os.path.join(SRC, "tools", "_e2e_flash_serve.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([NODE, os.path.join(SRC, "serve.js")], cwd=SRC, env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    try:
        ready = False
        for _ in range(60):
            st, body = get_raw("/dashboard.html")
            if st == 200:
                ready = True
                break
            time.sleep(0.5)
        check("独立实例起来了（副本库 + 端口 %d）" % PORT, ready, body if not ready else "")
        if not ready:
            return
        st, d = get("/api/settings")
        check("服务端认的 flash_extra_count = 20", st == 200 and d.get("review", {}).get("flash_extra_count") == 20,
              d if st == 200 else d)
        st, d = get("/api/flashcards/session?peek=1")
        peek_filter = (d.get("filter") or {}) if isinstance(d, dict) else {}
        check("服务端手上还留着那组点名的卡（复现用户现场）",
              st == 200 and d.get("saved") and (peek_filter.get("ids") or []), d)

        expr = os.path.join(SRC, "tools", "e2e_flash_probe.js")
        url = "http://127.0.0.1:%d/dashboard.html?chk=%d#/flash" % (PORT, int(time.time()))
        out = subprocess.run([NODE, os.path.join(SRC, "tools", "e2e_probe.js"),
                              "--url", url, "--expr-file", expr,
                              "--pre-file", os.path.join(SRC, "tools", "e2e_flash_seed.js"),
                              "--wait", "4000",
                              "--shot", SHOT],
                             cwd=SRC, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=180)
        raw = ""
        for line in (out.stdout or "").splitlines():
            if line.startswith("PROBE_RESULT="):
                raw = line[len("PROBE_RESULT="):]
        if not raw:
            print(out.stdout, out.stderr)
            check("探针跑通", False, (out.stdout or "")[-300:])
            return
        r = json.loads(raw)

        print("\n-- 闸门 --")
        print("  按钮:", r.get("gateButtons"))
        check("闸门认得出「有没刷完的那一组」（点名的）",
              any("继续本组" in (b or "") for b in (r.get("gateButtons") or [])), r.get("gateButtons"))

        print("\n-- 点了「%s」之后 --" % r.get("firstClick"))
        an = r.get("afterNew") or {}
        print("  进度:", an.get("progress"))
        print("  组题请求:", an.get("urls"))
        print("  头部按钮:", an.get("extraBtn"))
        last = [u for u in (an.get("urls") or []) if "peek=" not in u and "resume=" not in u]
        check("★ 重新挑的那一组**不再点名**（不带 ids=）",
              last and "ids=" not in last[-1], last)
        check("★ 设置里的 20 张写在了头部按钮上", an.get("extraBtn") == "🔁 再来 20 张",
              an.get("extraBtn"))

        print("\n-- 点了「🔁 再来一组」之后 --")
        ae = r.get("afterExtra") or {}
        print("  进度:", ae.get("progress"))
        print("  组题请求:", ae.get("urls"))
        print("  说明:", ae.get("extraHint"))
        check("★ 走的是 extra 通道", ae.get("urls") and any("mode=extra" in u for u in ae["urls"]),
              ae.get("urls"))
        check("★ 这一组就是设置里的 20 张", (ae.get("progress") or "").find("/ 20 张") >= 0,
              ae.get("progress"))
        check("★ 卡片上写明了这一组的来路", (ae.get("extraHint") or "").find("20 张") >= 0,
              ae.get("extraHint"))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        log.close()
        # 还原端口文件：不还原的话「启动考研大盘.bat」下次会开错端口
        try:
            if old_port:
                open(PORT_FILE, "w", encoding="utf-8").write(old_port)
            elif os.path.exists(PORT_FILE):
                os.remove(PORT_FILE)
        except Exception as e:
            print("WARN 还原 .serve_port 失败:", e)
        if os.path.exists(COPY):
            os.remove(COPY)

    print("\n结果：pass=%d fail=%d" % (ok_count[0], bad_count[0]))
    print("截图：tools/e2e_flash_shot.png")


main()


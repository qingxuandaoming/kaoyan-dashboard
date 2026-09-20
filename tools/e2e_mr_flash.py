"""早间回顾闪卡：真浏览器端到端（2026-09-20）。

用户澄清：「早间的这个闪卡的作用，不是再去学一学闪卡库里的闪卡，而就是用来学早间回顾的。
就是把早间回顾的内容变成翻转的闪卡。这样正好我把这个闪卡读完之后，就自动打卡，
早间回顾就可以了。」→ 这个脚本验证那条链真的通了。

做法（不动用户的实例）：题库**复制**一份 → 清掉副本里的打卡记录（免得断言看到旧数据）→
换端口起独立 serve.js → CDP 探针在真浏览器里点「🎯 开始闪卡练习」，把当天的卡一路翻完
→ 断言：卡是当天内容、全程零闪卡库请求、组末自动打卡、页面打卡按钮跟着变。

用法：python tools\\e2e_mr_flash.py   → 看 pass/fail；截图 tools/e2e_mr_shot.png
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

SRC = r"C:\Users\92534\Desktop\考研\src"
NODE = r"C:\Program Files\nodejs\node.exe"
PORT = 18097
COPY = os.path.join(SRC, "tools", "_e2e_mr_copy.db")
SHOT = os.path.join(SRC, "tools", "e2e_mr_shot.png")
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


def study_day():
    t = datetime.now()
    if t.hour < 4:
        t = t - timedelta(days=1)
    return t.strftime("%Y-%m-%d")


def get_raw(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def get(path):
    st, body = get_raw(path)
    if st == 200:
        return st, json.loads(body)
    return st, body


def main():
    date = study_day()
    src = os.path.join(SRC, "question_bank.db")
    if os.path.exists(COPY):
        os.remove(COPY)
    shutil.copy2(src, COPY)
    con = sqlite3.connect(COPY)
    con.execute("DELETE FROM mr_checkin")          # 副本上清干净，断言才看得准
    con.execute("DELETE FROM mr_sr")
    con.commit()
    con.close()
    print("== 副本就绪：今天=%s，打卡记录已清空 ==" % date)

    old_port = None
    if os.path.exists(PORT_FILE):
        old_port = open(PORT_FILE, encoding="utf-8").read().strip()
        os.remove(PORT_FILE)
    env = dict(os.environ, DB_PATH=COPY, PORT=str(PORT))
    log = open(os.path.join(SRC, "tools", "_e2e_mr_serve.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([NODE, os.path.join(SRC, "serve.js")], cwd=SRC, env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    try:
        ready = False
        for _ in range(60):
            st, _b = get_raw("/dashboard.html")
            if st == 200:
                ready = True
                break
            time.sleep(0.5)
        check("独立实例起来了（副本库 + 端口 %d）" % PORT, ready)
        if not ready:
            return
        st, d = get("/api/morning-review/overview")
        check("服务端能读到 morning_review.json", st == 200 and d.get("ok"), d)
        today = d.get("today") if isinstance(d, dict) else None
        st, day = get("/api/morning-review/day?date=" + (today or date))
        n_review = len((day.get("day") or {}).get("review") or [])
        n_quiz = len((day.get("day") or {}).get("quiz") or [])
        n_math = len((((day.get("day") or {}).get("math") or {}).get("points")) or [])
        print("   今天(%s)的回顾内容：知识点 %d / 小测 %d / 数学 %d" % (today, n_review, n_quiz, n_math))

        url = "http://127.0.0.1:%d/dashboard.html?chk=%d#/review" % (PORT, int(time.time()))
        out = subprocess.run([NODE, os.path.join(SRC, "tools", "e2e_probe.js"),
                              "--url", url, "--expr-file", os.path.join(SRC, "tools", "e2e_mr_probe.js"),
                              "--wait", "4500", "--shot", SHOT],
                             cwd=SRC, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=300)
        raw = ""
        for line in (out.stdout or "").splitlines():
            if line.startswith("PROBE_RESULT="):
                raw = line[len("PROBE_RESULT="):]
        if not raw:
            print(out.stdout, out.stderr)
            check("探针跑通", False, (out.stdout or "")[-300:])
            return
        r = json.loads(raw)
        panel = r.get("panel") or {}
        print("\n-- 「早」页的闪卡面板 --")
        print("  段:", panel.get("chips"))
        print("  按钮:", panel.get("startBtn"))
        print("  说明:", (panel.get("note") or "")[:160])
        check("面板按「当天内容」分段（全部/知识点/小测/数学/英语）",
              panel.get("chips") and panel["chips"][0].startswith("全部"), panel.get("chips"))
        check("★ 面板上不再有闪卡库那套科目 tab（408/数学/政治）",
              not panel.get("hasDeckTabs"), panel.get("hasDeckTabs"))
        check("按钮写明「开始闪卡练习」", "开始闪卡练习" in (panel.get("startBtn") or ""),
              panel.get("startBtn"))
        check("说明里写着「整组刷完会自动打卡」", "自动打卡" in (panel.get("note") or ""),
              panel.get("note"))

        first = r.get("first") or {}
        print("\n-- 开练 --")
        print("  进度:", first.get("progress"))
        print("  头部:", first.get("head"))
        print("  正面:", (first.get("stem") or "")[:80])
        print("  浮窗:", first.get("floatOpen"), first.get("floatTitle"))
        check("★ 组起来了：第 1 / N 张", bool(first.get("progress")) and first["progress"].startswith("第 1 / "),
              first.get("progress"))
        check("★ 卡片头部没有闪卡库的额度条（新卡 x/30 · 到期 N）",
              "可抽" not in (first.get("head") or "") and "新卡" not in (first.get("head") or ""),
              first.get("head"))
        check("浮窗标题写了这是早间回顾", "早间回顾" in (first.get("floatTitle") or ""),
              first.get("floatTitle"))

        print("\n-- 翻卡 --")
        print("  翻了", r.get("backCount"), "张；样例:", (r.get("backs") or [""])[0][:90])
        backs = r.get("backs") or []
        check("翻卡有原文（不是空白）", r.get("backCount", 0) > 0, r.get("backCount"))
        check("★ 原文按 HTML 渲染（没被转义成 &lt;p&gt; 源码）",
              backs and ("&lt;" not in backs[0]) and ("<" in backs[0]), backs[0][:120] if backs else "")

        reqs = r.get("reqs") or []
        # ?peek=1 是闪卡页闸门自己的「有没有没刷完的那一组」（只读、不组题），不算碰库
        bank = [u for u in reqs
                if (u.startswith("/api/flashcards") and "peek=" not in u and "resume=" not in u)
                or u.startswith("/api/study/cards")]
        check("★ 全程零闪卡库动作（不组题 / 不评分 / 不归卡）", not bank, bank)

        print("\n-- 组末 --")
        print("  总结:", (r.get("summary") or "")[:120])
        print("  打卡行:", r.get("finishLineAfter"))
        print("  页面打卡按钮:", r.get("checkBtnAfter"))
        check("★ 整组刷完出现总结屏", "完成" in (r.get("summary") or ""), r.get("summary"))
        check("★ 自动打卡成功（总结屏上写着已打卡）", "已打卡" in (r.get("finishLineAfter") or ""),
              r.get("finishLineAfter"))
        check("★ 「早」页顶部的打卡按钮跟着变成「已打卡」",
              "已打卡" in (r.get("checkBtnAfter") or ""), r.get("checkBtnAfter"))
        check("打卡请求真的发出去了",
              any(u.startswith("/api/morning-review/checkin") for u in reqs), reqs[-3:])

        ac = r.get("afterClose") or {}
        check("★ 收起浮窗后练习区换回闪卡页的闸门（不把早间回顾总结留在闪卡页）",
              ac.get("floatHidden") and ac.get("gate") and not ac.get("summaryStill"), ac)

        st, ov = get("/api/morning-review/overview")
        check("★ 服务端记下了这一天的打卡（SQLite mr_checkin）",
              st == 200 and (today or date) in (ov.get("checkins") or []),
              (ov or {}).get("checkins"))
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
        try:
            if old_port:
                open(PORT_FILE, "w", encoding="utf-8").write(old_port)
            elif os.path.exists(PORT_FILE):
                os.remove(PORT_FILE)
        except Exception as e:
            print("WARN 还原 .serve_port 失败:", e)
        for f in (COPY,):
            if os.path.exists(f):
                os.remove(f)

    print("\n结果：pass=%d fail=%d" % (ok_count[0], bad_count[0]))
    print("截图：tools/e2e_mr_shot.png")


main()

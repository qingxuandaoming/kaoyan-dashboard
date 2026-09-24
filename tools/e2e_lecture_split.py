#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""e2e_lecture_split.py —— 高数「章→讲」迁移的真服务端端到端验证。

起一个**独立**的 serve.js 实例（题库副本 + 独立端口），打真 HTTP，再用无头 Edge
逐个打开大盘子页，确认零控制台报错、零 404。不碰用户自己的实例。

跑完必须还原 `src/.serve_port`（否则「启动考研大盘.bat」会开错端口）。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
ROOT = os.environ.get("NOTES_ROOT", r"E:\NPEE")   # 笔记库根（2026-09-25 起与代码根分离，可用环境变量 NOTES_ROOT 覆盖）
NODE = r"C:\Program Files\nodejs\node.exe"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 18096
BASE = "http://127.0.0.1:%d" % PORT

results = []


def check(name, ok, detail=""):
    results.append((bool(ok), name, detail))
    print("  %s %s%s" % ("✓" if ok else "✗", name, (" ｜ " + detail) if detail else ""))


def get(path, raw=False):
    url = BASE + path
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def main():
    port_file = os.path.join(SRC, ".serve_port")
    saved_port = open(port_file).read() if os.path.exists(port_file) else None

    db = os.path.join(SRC, "question_bank.db")
    copy = os.path.join(SRC, "_e2e_lec.db")
    for suf in ("", "-wal", "-shm"):
        if os.path.exists(db + suf):
            shutil.copy2(db + suf, copy + suf)

    env = dict(os.environ, DB_PATH=copy, PORT=str(PORT))
    proc = subprocess.Popen([NODE, os.path.join(SRC, "serve.js")], cwd=SRC, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # 就绪探测：读原文，别用会 json.loads 的封装（dashboard.html 是 HTML）
        ready = False
        for _ in range(60):
            st, body = get("/dashboard.html")
            if st == 200 and "<html" in body.lower():
                ready = True
                break
            time.sleep(0.5)
        check("独立 serve.js 起在 %d 端口" % PORT, ready)
        if not ready:
            return

        print("\n[1] 索引真值：高数应全为「第N讲」，线代/概率应全为「第N章」")
        idx = json.loads(open(os.path.join(ROOT, "Math", "notes_index.json"),
                             encoding="utf-8").read())
        subs = idx.get("subjects", {})
        gs = subs.get("高数", {}).get("entries", [])
        BUCKETS = ("专题", "预备知识")
        gs_lec = [e for e in gs if re.match(r"^第\d+讲$", str(e.get("chapter", "")))]
        gs_ch = [e for e in gs if re.match(r"^第\d+章$", str(e.get("chapter", "")))]
        gs_bucket = [e for e in gs if e.get("chapter") in BUCKETS]
        check("高数条目全部按讲（无残留章）",
              gs and not gs_ch and len(gs_lec) + len(gs_bucket) == len(gs),
              "共 %d 条：讲 %d / 非编号桶 %d / 章 %d" % (len(gs), len(gs_lec), len(gs_bucket), len(gs_ch)))
        nos = sorted({int(re.match(r"^第(\d+)讲$", e["chapter"]).group(1)) for e in gs_lec})
        check("讲号都在 1..18 内", nos and min(nos) >= 1 and max(nos) <= 18, str(nos))
        missing = [n for n in range(1, 19) if n not in nos]
        print("     没有归档条目的讲：%s（第7讲是预期内的真缺口：从没写过笔记）" % missing)
        for sub in ("线代", "概率论"):
            xs = subs.get(sub, {}).get("entries", [])
            bad = [e for e in xs if str(e.get("chapter", "")).endswith("讲")]
            chk = [e for e in xs if re.match(r"^第\d+章$", str(e.get("chapter", "")))]
            check("%s 仍按章（%d 条，误改成讲 %d）" % (sub, len(xs), len(bad)),
                  xs and not bad and len(chk) == len(xs))

        print("\n[2] 服务端也这么发：/api/notes/search")
        # ⚠️ 这个端点要求非空 q（「关键词为空 → 明确失败，不静默返回空」是它既有的设计），
        # 也不能靠 subject=数学 过滤（那要服务端内部标签）。用真关键词取一批再按 id 前缀分组。
        st, body = get("/api/notes/search?" + urllib.parse.urlencode(
            {"q": "积分", "limit": "300"}))
        check("GET /api/notes/search?q=积分 200", st == 200, "status=%s" % st)
        data = json.loads(body) if st == 200 else {}
        items = data.get("results") or data.get("items") or []
        if items:
            print("     样本字段：%s" % sorted(items[0].keys()))
            print("     样本：%s" % json.dumps(items[0], ensure_ascii=False)[:200])
        # ⚠️ 响应里没有 id 字段（字段是 path/sub/subName/chapter/…），按 path 前缀分组
        gs_i = [x for x in items if str(x.get("path", "")).startswith("Math/高数/")]
        xd_i = [x for x in items
                if str(x.get("path", "")).startswith(("Math/线代/", "Math/概率论/"))]
        BUCKETS = ("专题", "预备知识")
        gs_bad = [x for x in gs_i if str(x.get("chapter", "")).endswith("章")]
        # chapter 为空是**正常**的：公式速查/计算陷阱/错题归档/中控这些文件不是索引条目，
        # 服务端对「命中了正文但没有索引元数据」的文件一律给空 chapter。
        gs_blank = [x for x in gs_i if not str(x.get("chapter", ""))]
        gs_ok = [x for x in gs_i if str(x.get("chapter", "")).endswith("讲")
                 or x.get("chapter") in BUCKETS]
        xd_bad = [x for x in xd_i if str(x.get("chapter", "")).endswith("讲")]
        check("服务端：高数条目无一按章下发（%d 条 = 讲/桶 %d + 无索引元数据 %d，误为章 %d）"
              % (len(gs_i), len(gs_ok), len(gs_blank), len(gs_bad)),
              gs_i and not gs_bad and len(gs_ok) + len(gs_blank) == len(gs_i),
              "chapter 取值 %s" % sorted({str(x.get("chapter")) for x in gs_i}))
        check("服务端：线代/概率条目没被误改成讲（%d 条，误为讲 %d）" % (len(xd_i), len(xd_bad)),
              not xd_bad,
              "chapter 取值 %s" % sorted({str(x.get("chapter")) for x in xd_i}))

        print("\n[2b] 大盘「章节」筛选按讲可用")
        st, body = get("/api/notes/search?" + urllib.parse.urlencode(
            {"q": "积分", "chapter": "第9讲", "limit": "100"}))
        d9 = json.loads(body) if st == 200 else {}
        it9 = d9.get("results") or d9.get("items") or []
        check("chapter=第9讲 能筛出条目", st == 200 and len(it9) > 0,
              "status=%s 命中 %d 条" % (st, len(it9)))
        check("筛出来的都在第9讲",
              it9 and all(str(x.get("chapter")) == "第9讲" for x in it9))

        print("\n[3] facets 的章节列表")
        st, body = get("/api/notes/facets")
        if st != 200:
            st, body = get("/api/notes/search?" + urllib.parse.urlencode({"limit": "1"}))
        ok = st == 200
        check("facets/search 可读", ok, "status=%s" % st)
        if ok:
            d = json.loads(body)
            f = (d.get("facets") or {})
            chs = [c.get("value") for c in (f.get("chapters") or [])]
            has_lec = any(str(c).endswith("讲") for c in chs)
            has_chap = any(str(c).endswith("章") for c in chs)
            check("章节 facet 同时含「讲」与「章」（两套并存是预期）",
                  has_lec and has_chap, "共 %d 个值" % len(chs))

        print("\n[4] 笔记原文预览：随便打开一个讲文件")
        st, body = get("/api/notes/preview?" + urllib.parse.urlencode(
            {"path": "Math/高数/第9讲_一元函数积分学的计算.md"}))
        check("第9讲 可预览", st == 200 and len(body) > 500, "status=%s len=%d" % (st, len(body)))
        st2, b2 = get("/api/notes/preview?" + urllib.parse.urlencode(
            {"path": "Math/高数/第3章_一元函数积分学.md"}))
        check("旧的第3章已不存在（应 404/失败）", st2 != 200, "status=%s" % st2)

        print("\n[5] 无头 Edge 逐子页扫报错与 404")
        tmp = os.path.join(HERE, "_e2e_lec_profile")
        shutil.rmtree(tmp, ignore_errors=True)
        bad_pages = []
        for route in ("#/overview", "#/notes", "#/flash", "#/review", "#/study", "#/settings"):
            url = "%s/dashboard.html?e2e=%d%s" % (BASE, int(time.time() * 1000), route)
            p = subprocess.run(
                [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--user-data-dir=" + tmp, "--enable-logging=stderr", "--v=0",
                 "--virtual-time-budget=9000", "--dump-dom", url],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            log = (p.stderr or "")
            hits = []
            for ln in log.split("\n"):
                low = ln.lower()
                if "uncaught" in low or "typeerror" in low or "referenceerror" in low:
                    hits.append(ln.strip()[:150])
                elif "failed to load resource" in low:
                    hits.append(ln.strip()[:150])
            dom = p.stdout or ""
            if "<html" not in dom.lower():
                hits.append("DOM 没渲染出来")
            if hits:
                bad_pages.append((route, hits[:3]))
            check("页面 %s 零报错零404" % route, not hits, "; ".join(hits[:2]))
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        for suf in ("", "-wal", "-shm"):
            try:
                os.remove(copy + suf)
            except OSError:
                pass
        if saved_port is not None:
            with open(port_file, "w") as fh:
                fh.write(saved_port)
        print("\n[清理] 已杀服务、删题库副本、还原 .serve_port=%s" % saved_port)

    ok = sum(1 for r in results if r[0])
    print("\n" + "=" * 60)
    print("E2E 结果：pass=%d fail=%d" % (ok, len(results) - ok))
    for good, name, detail in results:
        if not good:
            print("   ✗ %s ｜ %s" % (name, detail))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

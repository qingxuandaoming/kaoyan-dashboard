# -*- coding: utf-8 -*-
"""端到端验证「📐 口径」：真起一个 serve.js（副本库 + 独立端口），打真 HTTP。
不碰用户的实例：DB_PATH 指向副本、PORT 用 18097、跑完还原 .serve_port。
"""
import io, json, os, shutil, subprocess, sys, time, urllib.request, urllib.error
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SRC = r"E:\NPEE\src"
NODE = r"C:\Program Files\nodejs\node.exe"
PORT = 18097
DB = os.path.join(SRC, "question_bank.db")
COPY = os.path.join(SRC, "tools", "_wk_out", "e2e_port.db")
PORT_FILE = os.path.join(SRC, ".serve_port")
BASE = "http://127.0.0.1:%d" % PORT

results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("  ok   " if cond else "  FAIL ") + name + ("  → " + str(detail) if detail else ""))


def http(path, payload=None, method=None):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


saved_port = None
if os.path.exists(PORT_FILE):
    saved_port = open(PORT_FILE, encoding="utf-8").read()

proc = None
try:
    shutil.copyfile(DB, COPY)
    for ext in ("-wal", "-shm"):
        if os.path.exists(DB + ext):
            shutil.copyfile(DB + ext, COPY + ext)
    env = dict(os.environ)
    env["DB_PATH"] = COPY
    env["PORT"] = str(PORT)
    proc = subprocess.Popen([NODE, "serve.js"], cwd=SRC, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    ready = False
    for _ in range(60):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(BASE + "/dashboard.html", timeout=3) as r:
                if r.status == 200:
                    ready = True
                    break
        except Exception:
            pass
    print("服务就绪:", ready)
    if not ready:
        raise SystemExit("serve.js 没起来")

    print("\n== 1. GET /api/metrics ==")
    st, d = http("/api/metrics")
    ok("HTTP 200 且 ok=true", st == 200 and d.get("ok"), "status=%s" % st)
    items = d.get("items", [])
    ok("条目数 > 20", len(items) > 20, len(items))
    bad = [i for i in items if not i.get("id") or not i.get("title") or "value" not in i]
    ok("每项都有 id/title/value", not bad, bad[:2])
    counts = d.get("counts", {})
    ok("counts 三类齐全", all(k in counts for k in ("runtime", "fixed", "convention")), counts)
    editables = [i for i in items if i.get("editable")]
    ok("可改项 = 6 且有 range/key", len(editables) == 6 and all(
        i.get("key") and i.get("range") for i in editables), [i["id"] for i in editables])
    conv = [i for i in items if i.get("scope") == "convention"]
    ok("约定项带语义 value（rating 1-4）",
       any(i["id"] == "review.rating_scale" and isinstance(i["value"], dict) and i["value"].get("1") for i in conv))
    ok("带 spec_path 便于溯源", bool(d.get("spec_path")), d.get("spec_path", "")[-24:])

    target = next(i for i in items if i["id"] == "srs.retire_streak")
    print("\n== 2. 改一个可改项（smart_retire_streak 3→7）==")
    st, d2 = http("/api/settings", {"smart_retire_streak": 7})
    ok("POST /api/settings 接受新键", st == 200 and d2.get("ok"), d2)
    st, d3 = http("/api/metrics")
    got = next(i for i in d3["items"] if i["id"] == "srs.retire_streak")
    ok("生效值变 7", got["value"] == 7, got["value"])
    ok("标记 overridden", got.get("overridden") is True, got.get("raw"))

    print("\n== 3. 越界校验（应 400）==")
    st, d4 = http("/api/settings", {"smart_retire_streak": 99})
    ok("超过 range 上限被拒", st == 400, d4.get("error"))

    print("\n== 4. 恢复默认值 ==")
    st, d5 = http("/api/settings", {"smart_retire_streak": 3})
    st, d6 = http("/api/metrics")
    got = next(i for i in d6["items"] if i["id"] == "srs.retire_streak")
    ok("回到 spec 默认 3", got["value"] == 3, got["value"])

    print("\n== 5. 旧的额度键仍然工作（回归）==")
    st, d7 = http("/api/settings", {"flash_extra_count": 20})
    st, d8 = http("/api/metrics")
    got = next(i for i in d8["items"] if i["id"] == "quota.flash_extra_count")
    ok("flash_extra_count 生效 20", got["value"] == 20, got["value"])
    st, d9 = http("/api/settings", {"flash_extra_count": 0})
    ok("低于下限被拒", st == 400, d9.get("error"))

finally:
    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
    if saved_port is not None:
        with open(PORT_FILE, "w", encoding="utf-8") as f:
            f.write(saved_port)
        print("\n.serve_port 已还原")
    for p in (COPY, COPY + "-wal", COPY + "-shm"):
        try:
            os.remove(p)
        except OSError:
            pass

npass = sum(1 for _, c, _ in results if c)
nfail = len(results) - npass
print("\n" + "=" * 60)
print("E2E 结果：pass=%d fail=%d" % (npass, nfail))
for n, c, dt in results:
    if not c:
        print("  FAILED:", n, dt)
print("=" * 60)
sys.exit(1 if nfail else 0)

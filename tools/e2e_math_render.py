# -*- coding: utf-8 -*-
"""
公式渲染的真浏览器端到端探针（2026-09-22 建）。

要解决的问题：AI 解析里的公式渲染残缺（`\[`、LaTeX 源码、`\]` 三段摊在解析区）。
纯 node 桩测试碰不到这条链（要真 KaTeX、真懒加载、真 flushMath），所以这里
**把页面上真实的那份 __mdTex 拿出来、喂 explain_log 里的真实回复**，用真 Edge 跑：

  · 单条：`python tools\\e2e_math_render.py [explain_log_id]`  → dump 首次渲染与 1.5s 后的 DOM
  · 截图：`python tools\\e2e_math_render.py 124 --shot out.png`
  · 全量：`python tools\\e2e_math_render.py --corpus`  → 每条回复跑一遍，数「正文里还残留
    多少 LaTeX 痕迹」（`\\[` / `\\(` / `$$` / `\\命令`）。2026-09-22 首次跑：51 条里 10 条中招。

⚠️ 前提：先跑过 run_pipeline.py（要有 dashboard.html 产物）；走 file:// 加载，
   所以 KaTeX 的字体是 CORS 假警报（公式结构照常，只是字形回退）。
⚠️ 注入前会剥掉上一轮的 `#__probe__`（不然两段探针同页同跑）。
"""
import io, json, os, re, sqlite3, subprocess, sys, tempfile, time

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(SRC, "dashboard.html")
DB = os.path.join(SRC, "question_bank.db")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def real_reply(want_id=None):
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    if want_id:
        row = db.execute("SELECT id, content FROM explain_log WHERE id=?", (want_id,)).fetchone()
    else:
        row = db.execute(
            "SELECT id, content FROM explain_log WHERE role='assistant' AND content LIKE ?"
            " ORDER BY id DESC LIMIT 1", ("%" + chr(92) + "[%",)).fetchone()
    db.close()
    return row["id"], row["content"]


def corpus(limit=200):
    """explain_log 里所有 assistant 回复（真实的 AI 输出语料）。"""
    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT id, content FROM explain_log WHERE role='assistant' AND content IS NOT NULL"
        " AND length(content)>40 ORDER BY id ASC LIMIT ?", (limit,)).fetchall()
    db.close()
    return [{"id": r[0], "content": r[1]} for r in rows]


CORPUS_PROBE = r"""
<script id="__probe__">
window.addEventListener("load", function () {
  var BS = String.fromCharCode(92);
  var items = %s;
  var md = globalThis.__mdTex;
  // ① 先让 KaTeX 真的加载起来（第一处公式会触发懒加载）
  if (md && items.length) { try { md(items[0].content); } catch (e) {} }
  setTimeout(function () {
    var strip = function (html) {
      var d = document.createElement("div");
      d.innerHTML = html;
      return d.textContent || "";
    };
    var leftovers = function (txt) {
      var bad = [];
      [BS + "[", BS + "]", BS + "(", BS + ")", "$$"].forEach(function (m) {
        if (txt.indexOf(m) >= 0) bad.push(m);
      });
      var n = 0;
      for (var i = 0; i < txt.length - 1; i++) {
        var c = txt.charAt(i + 1);
        if (txt.charAt(i) === BS && ((c >= "a" && c <= "z") || (c >= "A" && c <= "Z"))) n++;
      }
      if (n) bad.push(BS + "cmd*" + n);
      return bad;
    };
    var lines = [];
    lines.push("PROBE_" + "katex=" + (typeof window.katex));
    lines.push("PROBE_" + "mdtex=" + (typeof md));
    var badCount = 0, list = [];
    items.forEach(function (it) {
      var html = "";
      try { html = md ? md(it.content) : ""; } catch (e) { html = "ERR " + e.message; }
      var txt = strip(html);
      var b = leftovers(txt);
      if (b.length) { badCount++; list.push("#" + it.id + " " + b.join(",")); }
    });
    lines.push("PROBE_" + "total=" + items.length);
    lines.push("PROBE_" + "bad=" + badCount);
    lines.push("PROBE_" + "badlist=" + list.join(" | "));
    var pre = document.createElement("pre");
    pre.id = "probe-result";
    pre.textContent = lines.join("\n@@\n");
    document.body.appendChild(pre);
  }, 4000);
});
</script>
"""


def build_probe(text):
    sample = json.dumps(text, ensure_ascii=False)
    return """
<script id="__probe__">
window.addEventListener("load", function () {
  var out = [];
  var sample = %s;
  var md = globalThis.__mdTex;
  out.push("PROBE_" + "mdfn=" + (typeof md));
  out.push("PROBE_" + "katex0=" + (typeof window.katex));
  var host = document.createElement("div");
  host.id = "probe-host";
  document.body.appendChild(host);
  var html = "";
  try { html = md ? md(sample) : "NOMDTEX"; } catch (e) { html = "ERR " + e.message; }
  out.push("PROBE_" + "html=" + html);
  host.innerHTML = (typeof md === "function") ? md(sample) : "";
  var shot = /[?&]shot=1/.test(location.search);
  setTimeout(function () {
    out.push("PROBE_" + "katex1=" + (typeof window.katex));
    setTimeout(function () {
      out.push("PROBE_" + "katex2=" + (typeof window.katex));
      out.push("PROBE_" + "dom=" + host.innerHTML);
      if (shot) {
        // 只留解析区（样式在 <head>，照样生效），方便截图看真实观感
        document.body.innerHTML = "";
        var wrap = document.createElement("div");
        wrap.className = "fs-exp-body";
        // ⚠️ .fs-exp-body 自带 max-height:420px + overflow:auto，截图会被裁掉下半截
        //    （曾经误判成「公式渲染坏了」，其实是探针容器在高 420px 处截断）
        wrap.style.cssText = "background:var(--bg-primary);padding:14px;max-width:1100px;"
            + "max-height:none;overflow:visible;";
        wrap.appendChild(host);
        document.body.appendChild(wrap);
        return;
      }
      var pre = document.createElement("pre");
      pre.id = "probe-result";
      pre.textContent = out.join("\\n@@\\n");
      document.body.appendChild(pre);
    }, 1500);
  }, 2500);
});
</script>
""" % sample


def main():
    want = None
    shot = None
    do_corpus = False
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--shot":
            shot = args[i + 1] if i + 1 < len(args) else os.path.join(SRC, "_probe_shot.png")
        elif a == "--corpus":
            do_corpus = True
        elif a.isdigit():
            want = int(a)
    if do_corpus:
        items = corpus()
        rid, text = ("corpus(%d 条)" % len(items)), json.dumps(items, ensure_ascii=False)
        probe = CORPUS_PROBE % text
    else:
        rid, text = real_reply(want)
        probe = build_probe(text)
    html = io.open(DASH, encoding="utf-8").read()
    # 注入前先剥掉上一轮的探针（只靠备份还原会残留两段同跑的探针）
    html = re.sub(r'<script id="__probe__">.*?</script>', "", html, flags=re.S)
    idx = html.rfind("</body>")
    html = html[:idx] + probe + html[idx:]
    tmp = os.path.join(SRC, "_probe_math.html")
    io.open(tmp, "w", encoding="utf-8").write(html)
    print("语料: %s" % rid)
    print("临时页: %s（跑完自动删）" % tmp)

    profile = tempfile.mkdtemp(prefix="edgeprobe_")
    url = "file:///" + tmp.replace("\\", "/") + "?probe=" + str(int(time.time()))
    if shot:
        url += "&shot=1"
    cmd = [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
           "--run-all-compositor-stages-before-draw",
           "--user-data-dir=" + profile, "--virtual-time-budget=25000"]
    if shot:
        cmd += ["--window-size=1200,1500", "--screenshot=" + shot]
    else:
        cmd += ["--dump-dom"]
    cmd.append(url)
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=180)
        dom = p.stdout.decode("utf-8", "replace")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if shot:
        print("截图: " + shot)
        return 0
    # dump 里带着全部源码，先剥 script/style，再看 #probe-result
    dom = re.sub(r"<script[\s\S]*?</script>", "", dom)
    dom = re.sub(r"<style[\s\S]*?</style>", "", dom)
    m = re.search(r'<pre id="probe-result">([\s\S]*?)</pre>', dom)
    if not m:
        print("!! 没有拿到 probe-result（探针没跑完 / 页面报错）")
        print(dom[-3000:])
        return 1
    body = m.group(1)
    for part in body.split("\n@@\n"):
        print(part.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
              .replace("&amp;", "&"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

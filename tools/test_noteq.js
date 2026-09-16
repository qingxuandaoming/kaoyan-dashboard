/*
 * 笔记搜索 + 读笔记提问（NOTEQ_JS）行为测试。
 *
 * 真实逻辑都在前端：URL 怎么拼、筛选之间怎么互相约束、结果怎么高亮、
 * 点结果怎么把笔记交给阅读器；提问面板怎么带上「当前读到的那一段」、
 * 多轮怎么续同一 thread、历史怎么回看。这些用 DOM 桩跑最快也最稳。
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const SRC = path.join(__dirname, "..", "generate_dashboard.py");
const OUT = path.join(os.tmpdir(), "kaoyan_noteq_extracted.js");
execFileSync("python", ["-c", `
import ast, io
src = io.open(r"${SRC}", encoding="utf-8").read()
tree = ast.parse(src)
for node in tree.body:
    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "NOTEQ_JS":
        io.open(r"${OUT}", "w", encoding="utf-8").write(ast.literal_eval(node.value))
        break
`], { maxBuffer: 1 << 24 });
const js = fs.readFileSync(OUT, "utf-8");

/* ---------------- DOM 桩 ---------------- */
let REG = {}, BYATTR = {}, BYCLS = {}, listeners = {};
function mkEl(tag) {
  const el = {
    tagName: String(tag || "div").toUpperCase(), children: [], _cls: new Set(),
    _html: "", _text: "", value: "", hidden: false, dataset: {}, disabled: false, onclick: null,
    _l: {},
    appendChild(c) { this.children.push(c); c.parent = this; return c; },
    remove() {},
    addEventListener(ev, fn) { (this._l[ev] = this._l[ev] || []).push(fn); },
    _fire(ev, obj) { ((this._l[ev] || []).forEach(fn => fn(obj || {}))); },
    querySelector(sel) { return qsa(sel)[0] || null; },
    querySelectorAll(sel) { return qsa(sel); },
    scrollIntoView() {}, focus() { this._focused = true; },
    click() { if (this.disabled) return; if (this.onclick) this.onclick({ target: this }); },
    get innerHTML() { return this._html; },
    set innerHTML(v) {
      // 重渲染前先把自己上一批登记的元素摘掉：不摘的话 querySelectorAll
      // 会连上一轮（已不在页面上）的元素一起返回，断言就会看着旧元素说话。
      for (const old of (this._made || [])) {
        if (old._id && REG[old._id] === old) delete REG[old._id];
        for (const c of old._cls) {
          const arr = BYCLS[c];
          if (arr) { const k = arr.indexOf(old); if (k >= 0) arr.splice(k, 1); }
        }
        for (const kk of Object.keys(old.dataset || {})) {
          const arr = BYATTR["data-" + kk];
          if (arr) { const k = arr.indexOf(old); if (k >= 0) arr.splice(k, 1); }
        }
      }
      this._made = [];
      this._html = String(v); this.children = []; index(this, this._html);
    },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); this._html = String(v); this.children = []; index(this, this._html); },
  };
  Object.defineProperty(el, "classList", { value: {
    add: c => el._cls.add(c), remove: c => el._cls.delete(c), contains: c => el._cls.has(c),
    toggle: (c, on) => { const w = on === undefined ? !el._cls.has(c) : !!on;
                         if (w) el._cls.add(c); else el._cls.delete(c); return w; },
  }});
  Object.defineProperty(el, "className", {
    get: () => Array.from(el._cls).join(" "),
    set: v => { el._cls = new Set(String(v).split(/\s+/).filter(Boolean));
                for (const c of el._cls) (BYCLS[c] = BYCLS[c] || []).push(el); },
  });
  el.scrollTop = 0; el.scrollHeight = 100;
  return el;
}
// 解析 innerHTML 里的 id / data-* / class，供 querySelector 用
function index(host, html) {
  const re = /<(\w+)([^>]*)>/g; let m;
  host._made = host._made || [];
  while ((m = re.exec(html))) {
    const attrs = m[2];
    const e = mkEl(m[1]);
    const id = /\bid="([\w-]+)"/.exec(attrs);
    if (id) { e._id = id[1]; REG[id[1]] = e; }
    const cls = /class="([^"]*)"/.exec(attrs);
    if (cls) cls[1].split(/\s+/).filter(Boolean).forEach(c => { e._cls.add(c); (BYCLS[c] = BYCLS[c] || []).push(e); });
    const da = /\bdata-([\w-]+)="([^"]*)"/g; let g;
    while ((g = da.exec(attrs))) { e.dataset[g[1]] = g[2]; (BYATTR["data-" + g[1]] = BYATTR["data-" + g[1]] || []).push(e); }
    (host._sub = host._sub || []).push(e);
    host._made.push(e);
    e.parent = host;
  }
}
function qsa(sel) {
  const s = String(sel).trim();
  if (s[0] === "#") return REG[s.slice(1)] ? [REG[s.slice(1)]] : [];
  if (s[0] === ".") return (BYCLS[s.slice(1)] || []).slice();
  let m = /^\[data-([\w-]+)="([^"]*)"\]$/.exec(s);
  if (m) return (BYATTR["data-" + m[1]] || []).filter(e => e.dataset[m[1]] === m[2]);
  m = /^\[data-([\w-]+)\]$/.exec(s);
  if (m) return (BYATTR["data-" + m[1]] || []).slice();
  return [];
}

/* ---------------- 环境 ---------------- */
let fetched = []; let timers = [];
function flushTimers() { const q = timers.slice(); timers = []; q.forEach(t => t.f()); }
function boot(opts) {
  REG = {}; BYATTR = {}; BYCLS = {}; listeners = {}; fetched = []; timers = [];
  const setT = (f, ms) => { timers.push({ f: f, ms: ms || 0 }); return timers.length; };
  const mk = id => (REG[id] = mkEl("div"));
  const input = mk("ns-input"), clearB = mk("ns-clear"), browseB = mk("ns-browse");
  const facetBox = mk("ns-facets"), metaBox = mk("ns-meta"), listBox = mk("ns-results");
  input.value = "";
  const doc = {
    body: mkEl("body"), documentElement: mkEl("html"),
    getElementById: id => REG[id] || null,
    createElement: t => mkEl(t),
    addEventListener: (ev, fn) => { (listeners[ev] = listeners[ev] || []).push(fn); },
    querySelector: () => null, querySelectorAll: () => [],
  };
  globalThis.location = { protocol: "http:", origin: "http://localhost:8080", hash: "#/notes" };
  const payload = (opts && opts.payload) || defaultPayload();
  const fetchStub = (url, o) => {
    fetched.push({ url: url, body: o && o.body ? JSON.parse(o.body) : null, method: (o && o.method) || "GET" });
    let p = { ok: true };
    if (url.indexOf("/api/notes/search") >= 0) p = payload.search(url);
    else if (url.indexOf("/api/notes/ask") >= 0) p = payload.ask(JSON.parse((o && o.body) || "{}"));
    else if (url.indexOf("/api/notes/qa") >= 0) p = payload.qa(url);
    return Promise.resolve({ json: () => Promise.resolve(p) });
  };
  let renderers = [];
  globalThis.__mdTex = (raw) => "<p>" + String(raw) + "</p>";
  globalThis.__revOpenNote = t => { opened.push(t); return true; };
  const win = { __pageRenderers: null, addEventListener() {} };
  new Function("document", "window", "fetch", "location", "setTimeout", "console", js)(
    doc, win, fetchStub, globalThis.location, setT, console);
  return { doc, win, input, clearB, browseB, facetBox, metaBox, listBox };
}
let opened = [];
function defaultPayload() {
  return {
    search: (url) => ({
      ok: true, q: "", total: 2, took_ms: 12, how: "exact", soft: ["进程"], tokens: ["进程"],
      filters: {}, facets: {
        subjects: [{ value: "408", label: "408", n: 46 }, { value: "政治", label: "政治", n: 8 }],
        subs: [{ subject: "408", value: "OS", label: "操作系统", n: 7 },
               { subject: "408", value: "DS", label: "数据结构", n: 9 }],
        chapters: [{ value: "第2章", n: 4 }], levels: [{ value: "L3", n: 3 }],
        tags: [{ value: "进程", n: 5 }], statuses: [], total: 761,
      },
      results: [
        { path: "408/OS/第2章_进程管理.md", name: "第2章_进程管理", title: "进程管理笔记",
          subject: "408", subjectLabel: "408", sub: "OS", subName: "操作系统", chapter: "第2章",
          level: "L2", tags: ["进程"], titleHit: true, hits: 60, snippet: "进程与线程的区别…" },
        { path: "", name: "仅索引条目", title: "逻辑结构辨析", subject: "408", subjectLabel: "408",
          sub: "DS", subName: "数据结构", chapter: "第1章", level: "L2", tags: [], hits: 0,
          snippet: "", orphan: true },
      ],
    }),
    ask: (body) => ({ ok: true, thread_id: "T-" + (fetched.filter(f => f.url.indexOf("/ask") >= 0).length),
                      path: body.path, section: body.section, text: "**回答**：笔记没写，补充一下。" }),
    qa: () => ({ ok: true, threads: [
      { thread_id: "OLD1", section: "2.1", at: "2026-09-21 01:02:03",
        first_question: "协程算什么单位？", messages: [{ role: "user", content: "协程算什么单位？" },
                                                      { role: "assistant", content: "**结论**：用户态调度。" }] },
    ] }),
  };
}
const tick = () => new Promise(r => setTimeout(r, 0));

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}
const searches = () => fetched.filter(f => f.url.indexOf("/api/notes/search") >= 0);
const lastSearch = () => searches().slice(-1)[0];

(async () => {
  console.log("\n[1] 懒渲染：进「笔」页才拉索引");
  {
    const env = boot();
    check("注册了 notes 页渲染器", env.win.__pageRenderers && env.win.__pageRenderers.notes
      && env.win.__pageRenderers.notes.length === 1);
    check("没进页面前不发请求", searches().length === 0);
    env.win.__pageRenderers.notes[0]();
    await tick(); await tick();
    check("进页面后拉了搜索接口", searches().length === 1, JSON.stringify(fetched.map(f => f.url)));
    check("首次请求带 facets=1", /facets=1/.test(lastSearch().url), lastSearch().url);
  }

  console.log("\n[2] 搜索框 → 关键词（防抖 + Enter 立即）");
  {
    const env = boot();
    env.win.__pageRenderers.notes[0]();
    await tick(); await tick();
    const before = searches().length;
    env.input.value = "进程";
    env.input._fire("input");
    await tick(); await tick();
    check("防抖：刚输入还没发请求", searches().length === before, "before=" + before + " now=" + searches().length);
    flushTimers();
    await tick(); await tick();
    check("防抖到点后发起了搜索", searches().length > before, "before=" + before);
    check("关键词进了 URL（URL 编码）", /q=%E8%BF%9B%E7%A8%8B/.test(lastSearch().url), lastSearch().url);
    check("清空按钮露出来了", env.clearB.hidden === false);
    const n1 = searches().length;
    env.input._fire("keydown", { key: "Enter", preventDefault() {} });
    await tick(); await tick();
    check("Enter 立即再搜一次（不等防抖）", searches().length === n1 + 1, "now=" + searches().length);
  }

  console.log("\n[3] 结果渲染：标题、命中高亮、仅索引条目");
  {
    const env = boot();
    env.win.__pageRenderers.notes[0]();
    await tick(); await tick();
    const html = env.listBox._html;
    check("渲染了两条结果", (html.match(/class="ns-item"/g) || []).length === 2,
      String((html.match(/class="ns-item"/g) || []).length));
    check("显示标题（命中的词被高亮包住也算）",
      html.indexOf("管理笔记") >= 0 && /ns-item-title[^>]*>[^<]*<mark>/.test(html),
      html.slice(0, 220));
    check("显示命中次数徽章", html.indexOf("命中 60") >= 0);
    check("正文片段里的关键词被高亮（<mark>）", /ns-item-snip[^>]*>[\s\S]{0,80}<mark>/.test(html), html.slice(html.indexOf("ns-item-snip"), html.indexOf("ns-item-snip") + 120));
    check("无文件的条目明确提示", html.indexOf("仅索引条目") >= 0 && html.indexOf("无对应文件") >= 0);
    check("给带文件的条目准备了「打开」入口（正文命中的写「打开并定位」）",
      /打开(并定位)? →/.test(html), html.slice(-200));
  }

  console.log("\n[4] 点结果 → 交给阅读器打开");
  {
    const env = boot();
    env.win.__pageRenderers.notes[0]();
    await tick(); await tick();
    opened = [];
    const items = (BYCLS["ns-item"] || []);
    items[0].click();
    check("把笔记路径与标题交给了阅读器",
      opened.length === 1 && opened[0].file === "408/OS/第2章_进程管理.md" && opened[0].name === "进程管理笔记",
      JSON.stringify(opened));
    items[1].click();
    check("没文件的条目不打开（只提示）", opened.length === 1);
  }

  console.log("\n[4b] 正文命中的结果：打开时带上关键词与最近的小标题（定位用）");
  {
    const env = boot({ payload: Object.assign(defaultPayload(), {
      search: () => ({ ok: true, q: "进程", total: 2, took_ms: 8, how: "exact",
                       soft: ["进程"], tokens: ["进程"], facets: null,
                       results: [
                         { path: "408/OS/第2章_进程管理.md", name: "第2章_进程管理", title: "进程管理",
                           subject: "408", subjectLabel: "408", sub: "OS", subName: "操作系统",
                           chapter: "第2章", level: "L2", tags: [], hits: 60, anchor: "2.1 进程与线程",
                           snippet: "进程是资源分配的基本单位。" },
                         { path: "408/OS/第2章_进程管理.md", name: "第2章_进程管理", title: "同一篇，仅标题命中",
                           subject: "408", subjectLabel: "408", sub: "OS", subName: "操作系统",
                           chapter: "第2章", level: "L2", tags: [], hits: 0, anchor: "",
                           snippet: "" },
                       ] }),
    }) });
    env.win.__pageRenderers.notes[0]();
    await tick(); await tick();
    env.input.value = "进程";
    env.input._fire("input");
    flushTimers();
    await tick(); await tick();
    opened = [];
    const items = (BYCLS["ns-item"] || []);
    items[0].click();
    check("正文命中：带上了关键词（供正文里定位）",
      opened[0] && opened[0].query === "进程", JSON.stringify(opened[0]));
    check("正文命中：带上了服务端算的最近小标题（兜底定位）",
      opened[0] && opened[0].anchor === "2.1 进程与线程", JSON.stringify(opened[0]));
    items[1].click();
    check("只有标题命中时不带关键词（不必定位）",
      opened[1] && opened[1].query === "" && opened[1].anchor === "", JSON.stringify(opened[1]));
    // 结果行的 HTML 在 listBox 上（桩不会把子树内容同步到子元素的 _html 上）
    const chunks = env.listBox._html.split('class="ns-item"');
    check("结果行写的是「打开并定位」而不是「打开」",
      chunks.length === 3 && chunks[1].indexOf("打开并定位") >= 0 && chunks[2].indexOf("打开并定位") < 0,
      chunks.slice(1).map(c => (c.match(/打开[^<]*/) || [""])[0]).join(" | "));
  }

  console.log("\n[5] 筛选：点 chip → 进 URL；科目与子科互相约束");
  {
    const env = boot();
    env.win.__pageRenderers.notes[0]();
    await tick(); await tick();
    const chips = BYATTR["data-f"] || [];
    check("渲染出筛选 chip（科目+子科+章节+级别+标签）", chips.length >= 6, "chips=" + chips.length);
    const osChip = chips.find(c => c.dataset.f === "sub" && c.dataset.v === "OS");
    check("有「操作系统」子科 chip", !!osChip);
    osChip.onclick();
    await tick(); await tick();
    const u = lastSearch().url;
    check("子科筛选进了 URL", /sub=OS/.test(u), u);
    check("选了子科会自动带上科目（筛选是「与」的关系）", /subject=408/.test(u), u);
    const osChipOn = (BYATTR["data-f"] || []).find(c => c.dataset.f === "sub" && c.dataset.v === "OS");
    check("chip 进入选中态（重渲染后仍标着）", osChipOn && osChipOn._cls.has("on"), osChipOn && osChipOn.className);
    // 再点同一颗 → 取消
    const osChip2 = (BYATTR["data-f"] || []).find(c => c.dataset.f === "sub" && c.dataset.v === "OS");
    osChip2.onclick();
    await tick(); await tick();
    check("再点一次取消该筛选", !/sub=OS/.test(lastSearch().url), lastSearch().url);
  }

  console.log("\n[6] 浏览模式与空状态");
  {
    const env = boot();
    env.win.__pageRenderers.notes[0]();
    await tick(); await tick();
    env.input.value = "进程";
    env.input._fire("input");
    flushTimers();
    await tick(); await tick();
    env.browseB.onclick();
    await tick(); await tick();
    check("「浏览」清空关键词与筛选", lastSearch().url.indexOf("q=") < 0 && lastSearch().url.indexOf("sub=") < 0,
      lastSearch().url);
    const env2 = boot({ payload: Object.assign(defaultPayload(), {
      search: () => ({ ok: true, q: "zzz", total: 0, results: [], how: "exact", soft: [], tokens: [], facets: null }),
    }) });
    env2.win.__pageRenderers.notes[0]();
    await tick(); await tick();
    env2.input.value = "zzz";
    env2.input._fire("input");
    flushTimers();
    await tick(); await tick();
    check("搜不到时给的是「换同义词/点筛选」的提示，不是空白",
      env2.listBox._html.indexOf("没找到包含") >= 0 && env2.listBox._html.indexOf("浏览") >= 0,
      env2.listBox._html.slice(0, 120));
  }

  console.log("\n[7] 分级降级：整句搜不到 → 拆词（并在元信息里说明）");
  {
    const env = boot({ payload: Object.assign(defaultPayload(), {
      search: () => ({ ok: true, q: "旋转和平衡", total: 1, took_ms: 9, how: "split",
                       soft: ["旋转", "平衡"], tokens: ["旋转和平衡"], facets: null,
                       results: [{ path: "408/DS/第7章_查找.md", name: "第7章_查找", title: "查找",
                                   subject: "408", subjectLabel: "408", sub: "DS", subName: "数据结构",
                                   chapter: "第7章", level: "L2", tags: [], hits: 23,
                                   snippet: "AVL 树的旋转与平衡因子…" }] }),
    }) });
    env.win.__pageRenderers.notes[0]();
    await tick(); await tick();
    env.input.value = "旋转和平衡";
    env.input._fire("input");
    flushTimers();
    await tick(); await tick();
    check("元信息说明了「按拆开的关键词匹配」", env.metaBox._html.indexOf("按拆开的关键词") >= 0,
      env.metaBox._html);
    check("高亮用的是拆出来的词", env.listBox._html.indexOf("<mark>旋转</mark>") >= 0,
      env.listBox._html.slice(0, 260));
  }

  console.log("\n[8] 「就问这段」面板：上下文 + 多轮 + 历史");
  {
    const env = boot();
    const pane = mkEl("aside");
    let ctx = { section: "2.1 进程与线程", text: "进程是资源分配单位。" };
    globalThis.__noteQaMount(pane, {
      path: "408/OS/第2章_进程管理.md", title: "进程管理",
      getContext: () => ctx,
      scroller: (() => { const s = mkEl("div"); return s; })(),
    });
    check("面板渲染出输入框与发送键", !!pane.querySelector('[data-f="in"]')
      && !!pane.querySelector('[data-act="send"]'), pane._html.slice(0, 80));
    check("显示了正在读的小节", pane.querySelector('[data-f="sec"]').textContent.indexOf("2.1") >= 0,
      pane.querySelector('[data-f="sec"]').textContent);
    check("显示了上下文预览（字数 + 摘要）", pane.querySelector('[data-f="ctx"]').textContent.indexOf("字") >= 0,
      pane.querySelector('[data-f="ctx"]').textContent);
    await tick(); await tick();
    check("拉了这篇笔记的历史提问", fetched.some(f => f.url.indexOf("/api/notes/qa?path=") >= 0));
    check("历史列表渲染出上次的问题", pane.querySelector('[data-f="hist"]')._html.indexOf("协程算什么单位") >= 0,
      pane.querySelector('[data-f="hist"]')._html.slice(0, 100));

    // 发一个问题：请求里必须带上「正在读的那一段」
    const inBox = pane.querySelector('[data-f="in"]');
    inBox.value = "协程和线程什么区别？";
    pane.querySelector('[data-act="send"]').onclick();
    await tick(); await tick();
    const ask = fetched.filter(f => f.url.indexOf("/api/notes/ask") >= 0).slice(-1)[0];
    check("发了提问请求", !!ask, JSON.stringify(fetched.map(f => f.url)));
    check("请求带笔记路径", ask.body.path === "408/OS/第2章_进程管理.md", JSON.stringify(ask.body));
    check("请求带当前小节", ask.body.section === "2.1 进程与线程", ask.body.section);
    check("请求带正在读的那段正文（上下文）", ask.body.context.indexOf("资源分配单位") >= 0, ask.body.context);
    check("首次提问不带 thread_id", !ask.body.thread_id, JSON.stringify(ask.body.thread_id));
    const answers = BYCLS["qa-a"] || [];
    check("回答渲染进日志（走 Markdown 渲染）", answers.some(e => (e._html || "").indexOf("<p>") >= 0),
      answers.map(e => (e._html || "").slice(0, 40)).join(" | "));
    check("输入框已清空", inBox.value === "");

    // 续问：应当带上 thread_id
    inBox.value = "那考研会考吗？";
    pane.querySelector('[data-act="send"]').onclick();
    await tick(); await tick();
    const ask2 = fetched.filter(f => f.url.indexOf("/api/notes/ask") >= 0).slice(-1)[0];
    check("续问带上了 thread_id（同一轮对话）", !!ask2.body.thread_id, JSON.stringify(ask2.body));

    // 「新对话」清空 thread
    pane.querySelector('[data-act="new"]').onclick();
    inBox.value = "另一个问题";
    pane.querySelector('[data-act="send"]').onclick();
    await tick(); await tick();
    const ask3 = fetched.filter(f => f.url.indexOf("/api/notes/ask") >= 0).slice(-1)[0];
    check("「新对话」后重新开一条线索", !ask3.body.thread_id);
  }

  console.log("\n[9] 上下文跟着滚动更新");
  {
    const env = boot();
    const pane = mkEl("aside");
    const scroller = mkEl("div");
    let ctx = { section: "2.1", text: "第一段。" };
    globalThis.__noteQaMount(pane, { path: "x.md", getContext: () => ctx, scroller: scroller });
    scroller._fire("scroll");
    ctx = { section: "2.3 线程调度", text: "第二段内容。" };
    await new Promise(r => setTimeout(r, 450));   // 面板对滚动有 400ms 节流
    scroller._fire("scroll");
    check("滚动后小节名跟着变", pane.querySelector('[data-f="sec"]').textContent.indexOf("2.3") >= 0,
      pane.querySelector('[data-f="sec"]').textContent);
  }

  console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
  process.exit(fail === 0 ? 0 : 1);
})();
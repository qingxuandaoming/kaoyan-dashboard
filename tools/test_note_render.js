/*
 * 笔记渲染的行为测试。
 *
 * 做法：从 generate_dashboard.py 用 ast 抽出**真实的** REVIVE_JS，在末尾注入一行
 * 把 mdRender / resolveImgSrc 挂到 globalThis，再用极简 DOM 桩跑起来。这样测的是
 * 线上那份代码，不是照着重写的一份——之前两轮真 bug（U 键被吞、反斜杠转义过头）
 * 都是这么抓出来的。
 *
 * 重点覆盖：插图相对路径解析（全库扫一遍，确认每张图都真能取到）、六级标题、
 * mermaid 代码块、以及 img 标签的属性白名单。
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = process.env.NOTES_ROOT || "E:\\NPEE";   // 笔记库根（2026-09-25 起与代码根分离）
const GEN = path.join(__dirname, "..", "generate_dashboard.py");
const OUT = path.join(os.tmpdir(), "kaoyan_revive_extracted.js");

// ---- 1. 抽出 REVIVE_JS ----
execFileSync("python", ["-c", `
import ast, io
src = io.open(r"${GEN}", encoding="utf-8").read()
for node in ast.parse(src).body:
    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "REVIVE_JS":
        io.open(r"${OUT}", "w", encoding="utf-8").write(ast.literal_eval(node.value))
        break
`], { maxBuffer: 1 << 24 });

// ---- 1b. 先跑 FLASH_JS 的第一个 IIFE，拿到**真实的** asciiMath ----
// mdRender 靠 window.asciiMath 调它（跨 IIFE 只能走 window）。这里必须装真的：
// 用桩函数糊过去的话，「桥没搭上」这种坑测试照样绿 —— 而那是这个仓库踩过的坑
// （见 generate_dashboard.py 里 mdInline/splitMath 的桥接注释）。
const FOUT = path.join(os.tmpdir(), "kaoyan_flash_for_note.js");
execFileSync("python", ["-c", `
import ast, io
src = io.open(r"${GEN}", encoding="utf-8").read()
for node in ast.parse(src).body:
    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "FLASH_JS":
        io.open(r"${FOUT}", "w", encoding="utf-8").write(ast.literal_eval(node.value))
        break
`], { maxBuffer: 1 << 24 });
let fjs = fs.readFileSync(FOUT, "utf-8");
const fAnchor = fjs.indexOf("window.asciiMath = asciiMath;");
const fClose = fAnchor < 0 ? -1 : fjs.indexOf("})();", fAnchor);
if (fClose < 0) throw new Error("FLASH_JS 结构变了：找不到全局 KaTeX IIFE 的导出/结尾");
fjs = fjs.slice(0, fClose + 5);
const _win = globalThis.window, _doc = globalThis.document;
globalThis.window = globalThis;
globalThis.document = {
  createElement() {
    let t = "";
    return {
      set textContent(v) { t = v == null ? "" : String(v); },
      get textContent() { return t; },
      get innerHTML() {
        return String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;")
          .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
      },
    };
  },
  head: { appendChild() {} },
  querySelectorAll() { return []; },
};
new Function(fjs)();
const realAsciiMath = globalThis.asciiMath;
globalThis.window = _win;
globalThis.document = _doc;
if (typeof realAsciiMath !== "function") throw new Error("FLASH_JS 没有导出 asciiMath");

let js = fs.readFileSync(OUT, "utf-8");
// REVIVE_JS 里有**两个** IIFE（笔记盘活区、活跃度图表），mdRender 在第一个里。
// 所以要从 mdRender 的声明往下找第一个 `})();`，不能简单取最后一个。
const mi = js.indexOf("function mdRender");
const tail = mi < 0 ? -1 : js.indexOf("})();", mi);
if (tail < 0) throw new Error("REVIVE_JS 结构变了，找不到 mdRender 所在 IIFE 的结尾");
js = js.slice(0, tail)
  + "globalThis.__revive = { mdRender: mdRender, resolveImgSrc: resolveImgSrc,"
  + " setNoteDir: function(d) { noteDir = d; }, mermaid: mermaidBlocks };\n"
  + js.slice(tail);

// ---- 2. 极简 DOM 桩 ----
let registry = {};
function mkEl(tag) {
  const el = {
    tagName: (tag || "div").toUpperCase(),
    children: [], _cls: new Set(), dataset: {}, style: {}, _html: "", _text: "",
    appendChild(c) { this.children.push(c); return c; },
    insertAdjacentHTML(pos, h) { this._html = h + this._html; },
    addEventListener() {}, removeEventListener() {},
    querySelectorAll(sel) { return qsa(this, sel); },
    querySelector(sel) { return qsa(this, sel)[0] || null; },
    remove() {},
  };
  Object.defineProperty(el, "classList", { value: {
    add: c => el._cls.add(c), remove: c => el._cls.delete(c),
    contains: c => el._cls.has(c), toggle: (c, on) => { on ? el._cls.add(c) : el._cls.delete(c); },
  }});
  Object.defineProperty(el, "className", {
    get: () => Array.from(el._cls).join(" "),
    set: v => { el._cls = new Set(String(v).split(/\s+/).filter(Boolean)); },
  });
  Object.defineProperty(el, "innerHTML", {
    get: () => el._html,
    set: v => { el._html = String(v); el.children = []; },
  });
  Object.defineProperty(el, "textContent", {
    get: () => el._text,
    set: v => {
      el._text = String(v);
      el._html = String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      el.children = [];
    },
  });
  return el;
}
function matches(el, sel) {
  sel = sel.trim();
  if (sel[0] === ".") return el._cls.has(sel.slice(1));
  return el.tagName === sel.toUpperCase();
}
function qsa(root, sel) {
  const parts = String(sel).split(",").map(s => s.trim());
  const out = [];
  (function walk(n) {
    (n.children || []).forEach(c => { if (parts.some(p => matches(c, p))) out.push(c); walk(c); });
  })(root);
  return out;
}

function boot() {
  registry = {};
  const doc = {
    head: mkEl("head"), body: mkEl("body"),
    createElement: t => mkEl(t),
    getElementById: id => (registry[id] || (registry[id] = mkEl("div"))),
    querySelectorAll: sel => qsa(doc.body, sel),
    querySelector: sel => qsa(doc.body, sel)[0] || null,
    addEventListener() {}, removeEventListener() {},
  };
  const el = (tag, attrs, parent) => {
    const e = mkEl(tag);
    if (attrs) for (const k in attrs) { if (k === "style") Object.assign(e.style, attrs[k]); else e[k] = attrs[k]; }
    if (parent && parent.appendChild) parent.appendChild(e);
    return e;
  };
  // PALETTE 的键很多且只用于上色，用 Proxy 一律返回一个色值即可
  const PALETTE = new Proxy({}, { get: () => "#888888" });
  const win = { asciiMath: realAsciiMath };   // 跨 IIFE 的桥，装真实现
  const fetchStub = () => Promise.resolve({ json: () => Promise.resolve({ ok: true, content: "" }) });
  // ⚠️ 必须传 location：REVIVE_JS 开头就用 location.protocol 决定 API 基址，
  //    少了这个参数整段 IIFE 直接 ReferenceError，导出也就拿不到（2026-09-21 修）
  new Function("document", "window", "el", "PALETTE", "D", "fetch", "setTimeout", "location", js)(
    doc, win, el, PALETTE, {}, fetchStub, (f) => f && f(),
    { protocol: "http:", origin: "http://localhost:8080", hash: "" });
  return globalThis.__revive;
}

const R = boot();
if (!R) throw new Error("没能拿到 mdRender —— IIFE 可能在导出前就抛了");

// ---- 3. 断言 ----
let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}
const read = p => fs.readFileSync(path.join(ROOT, p), "utf-8");
function render(relPath, src) {
  const cut = relPath.lastIndexOf("/");
  R.setNoteDir(cut >= 0 ? relPath.slice(0, cut) : "");
  return R.mdRender(src === undefined ? read(relPath) : src);
}

console.log("\n[1] 真实笔记：408/DS/第7章_查找.md");
{
  const note = "408/DS/第7章_查找.md";
  const src = read(note);
  const html = render(note, src);

  check("没有把 <img> 转义成字面文本", !html.includes("&lt;img"), html.match(/.{0,40}&lt;img.{0,40}/));
  check("原样保留了 <img 源码之外的行", src.includes("<img "));

  const imgs = html.match(/<img class="rev-img"[^>]*>/g) || [];
  check("渲染出 " + imgs.length + " 张图", imgs.length > 0);
  let okAll = true, detail = "";
  imgs.forEach(tag => {
    const m = tag.match(/src="([^"]+)"/);
    const p = decodeURIComponent((m[1].split("path=")[1] || ""));
    if (!fs.existsSync(path.join(ROOT, p))) { okAll = false; detail = p; }
  });
  check("每张图的文件都真实存在", okAll, detail);
  check("路径换算到了笔记所在目录", imgs.every(t => t.includes("408%2FDS%2Fassets") || t.includes("408/DS/assets")));

  // 六级标题：笔记里用 ###### 标图注
  const six = (src.match(/^#{6}\s+/gm) || []).length;
  check("笔记里确有 " + six + " 处六级标题", six > 0);
  check("六级标题渲染成了 <h3>", /<h3 id="md-h\d+">图 /.test(html) || !html.includes("######"));
  check("输出里没有残留的 ######", !html.includes("######"));

  // mermaid
  const mCount = (src.match(/^```mermaid/gm) || []).length;
  const boxes = (html.match(/class="mermaid-box"/g) || []).length;
  check("笔记里 " + mCount + " 个 mermaid 块 → " + boxes + " 个待绘制容器", mCount === boxes && mCount > 0);
  check("mermaid 源码先以代码块兜底", html.includes("<pre><code>graph TD") || html.includes("<pre><code>flowchart"));
  check("mermaid 源码没有被当成正文渲染", !/<div>graph TD<\/div>/.test(html));
}

console.log("\n[2] 六级标题（构造用例）");
{
  const html = render("408/DS/x.md", "###### 图 7.1 标题\n##### 五级\n#### 四级\n# 一级");
  const hs = html.match(/<h[1-6][^>]*>([^<]*)<\/h[1-6]>/g) || [];
  check("四行都成了标题", hs.length === 4, JSON.stringify(hs));
  check("层级被收敛到 h1–h3（大纲只认这三档）", hs.every(h => /<h[123]/.test(h)));
  check("内容正确", html.includes("图 7.1 标题"));
}

console.log("\n[3] 插图路径解析");
{
  check("./ 开头", R.resolveImgSrc("./assets/a.png") === "http://localhost:8080/api/notes/asset?path=408%2FDS%2Fassets%2Fa.png", R.resolveImgSrc("./assets/a.png"));
  check("裸相对路径", R.resolveImgSrc("assets/a.png").includes("408%2FDS%2Fassets%2Fa.png"));
  check("向上逃逸被拒", R.resolveImgSrc("../../../etc/passwd") === "", R.resolveImgSrc("../../../etc/passwd"));
  check("中途 .. 可正常归一", R.resolveImgSrc("assets/../assets/a.png").includes("408%2FDS%2Fassets%2Fa.png"));
  check("http 外链原样放行", R.resolveImgSrc("https://x.com/a.png") === "https://x.com/a.png");
  check("data URI 原样放行", R.resolveImgSrc("data:image/png;base64,AAA").startsWith("data:"));
  check("空 src 返回空", R.resolveImgSrc("") === "");
}

console.log("\n[4] img 标签属性白名单");
{
  const h1 = render("408/DS/x.md", '<img src="./assets/a.png" onerror="alert(1)" onclick="x()" style="width:70%">');
  check("onerror 被丢弃", !h1.includes("onerror"), h1);
  check("onclick 被丢弃", !h1.includes("onclick"));
  check("width 白名单内保留", h1.includes('style="width:70%"'), h1);

  const h2 = render("408/DS/x.md", '<img src="./assets/a.png" style="width:expression(alert(1))">');
  check("style 里的非尺寸值被丢弃", !h2.includes("expression"), h2);

  const h3 = render("408/DS/x.md", '<img src="./assets/a.png" style="position:fixed;top:0">');
  check("position 之类被丢弃", !h3.includes("position"), h3);

  const h4 = render("408/DS/x.md", "![架构图](./assets/a.png)");
  check("markdown 图片语法也渲染成图", h4.includes('class="rev-img"') && h4.includes("assets%2Fa.png"), h4);

  const h5 = render("408/DS/x.md", '<img src="../../../secret.png">');
  check("越权路径给出可见提示而非静默吞掉", h5.includes("rev-img-bad"), h5);
}

console.log("\n[5] 提示块 > [!TYPE]");
{
  const tip = render("408/DS/x.md", "> [!TIP]\n> **本质**：局部降低子树高度。");
  check("TIP → 提示框", tip.includes('class="rev-alert tip"'), tip);
  check("标题渲染成「提示」", tip.includes(">提示</div>"), tip);
  check("正文加粗生效", tip.includes("<b>本质</b>"), tip);
  check("不再残留 [!TIP] 字面量", !tip.includes("[!TIP]"), tip);
  check("不再残留行首 > 号", !tip.includes("&gt;"), tip);

  const note = render("408/DS/x.md", "> [!NOTE]\n> 注意一下");
  check("NOTE → note 类，标签「注意」", note.includes("rev-alert note") && note.includes(">注意</div>"), note);

  const warn = render("408/DS/x.md", "> [!WARNING]\n> 危险操作");
  check("WARNING → warning 类，标签「警告」", warn.includes("rev-alert warning") && warn.includes(">警告</div>"), warn);

  const imp = render("408/DS/x.md", "> [!IMPORTANT]\n> 必考");
  check("IMPORTANT → important 类", imp.includes("rev-alert important") && imp.includes(">重要</div>"), imp);

  const cus = render("408/DS/x.md", "> [!TIP] 自定义标题\n> 正文");
  check("自定义标题被采用", cus.includes("<b>自定义标题</b>"), cus);

  const unk = render("408/DS/x.md", "> [!BOGUS]\n> 内容");
  check("未知类型退化成普通引用", unk.includes("rev-quote") && !unk.includes("rev-alert"), unk);

  const math = render("408/DS/x.md", "> [!TIP]\n> 高度为 $h$ 时");
  check("提示块里的公式仍走 KaTeX", math.includes("@@MATH") === false && /katex|tex-fallback/.test(math), math);
}

console.log("\n[6] 普通引用块");
{
  const q = render("408/DS/x.md", "> 第一行\n> 第二行");
  check("两行收进一个引用块", q.includes('class="rev-quote"') && (q.match(/<div>/g) || []).length === 2, q);
  check("行首 > 号已剥掉", !q.includes("&gt;"), q);

  const seg = render("408/DS/x.md", "> 上段\n>\n> 下段\n\n正文");
  check("空行 > 分段", seg.includes("上段") && seg.includes("下段"));
  // 引用块要在「正文」之前收口，别把后面的内容也圈进去
  const qEnd = seg.indexOf("</div></div>");
  check("引用块被正文终止", qEnd > 0 && seg.indexOf("正文") > qEnd, seg);

  const nested = render("408/DS/x.md", "> > 嵌套内层");
  check("嵌套引用不再漏出 > 号", !nested.includes("&gt;"), nested);

  const qt = render("408/DS/x.md", "> | 列A | 列B |\n> |---|---|\n> | 1 | 2 |");
  check("引用里的表格还原成 <table>", qt.includes("rev-quote-table") && qt.includes("<td>列A</td>"), qt);

  const multi = render("408/DS/x.md", "> [!TIP]\n> 第一段\n\n普通段落\n\n> 普通引用");
  check("提示块与普通引用互不串台",
    multi.includes("rev-alert") && multi.includes("rev-quote") && multi.includes("普通段落"), multi);
}

console.log("\n[7] 全库扫图：每条引用都要能取到文件");
{
  const dirs = ["408", "Math", "Politics", "English"];
  const files = [];
  (function walk(d) {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      if (e.name === ".qoder" || e.name.startsWith(".")) continue;
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith(".md")) files.push(p);
    }
  })(path.join(ROOT, dirs[0]));
  ["Math", "Politics", "English"].forEach(d => {
    const p = path.join(ROOT, d);
    if (fs.existsSync(p)) (function walk(q) {
      for (const e of fs.readdirSync(q, { withFileTypes: true })) {
        if (e.name === ".qoder" || e.name.startsWith(".")) continue;
        const c = path.join(q, e.name);
        if (e.isDirectory()) walk(c); else if (e.name.endsWith(".md")) files.push(c);
      }
    })(p);
  });

  let withImg = 0, total = 0, broken = [], bad = [], leaked = [], mismatch = [];
  let alerts = 0, quotes = 0;
  for (const abs of files) {
    const src = fs.readFileSync(abs, "utf-8");
    const rel = path.relative(ROOT, abs).replace(/\\/g, "/");
    const html = render(rel, src);
    // 行首 > 号没被吃掉 = 引用/提示块解析漏了
    const leaks = (html.match(/<div>&gt;/g) || []).length;
    if (leaks) leaked.push(rel + " (" + leaks + " 行)");
    // 标记数要减掉连着写两遍的重复行，才能和渲染出的框数对齐
    const marks = (src.match(/^> *\[![A-Za-z]+\]/gm) || []).length;
    let dup = 0, prevMark = false;
    for (const l of src.split("\n")) {
      const isMark = /^> *\[![A-Za-z]+\]/.test(l);
      if (isMark && prevMark) dup++;
      prevMark = isMark;
    }
    const al = (html.match(/class="rev-alert /g) || []).length;
    if (marks - dup !== al) mismatch.push(rel + " 标记" + (marks - dup) + " vs 渲染" + al);
    alerts += al;
    quotes += (html.match(/class="rev-quote"/g) || []).length;
  }
  check("全库渲染出 " + alerts + " 个提示框、" + quotes + " 个引用块", alerts > 0 && quotes > 0);
  check("标记数与提示框数逐篇对齐", mismatch.length === 0, mismatch.slice(0, 6).join(" | "));
  check("没有行首 > 号泄漏成正文", leaked.length === 0, leaked.slice(0, 6).join(" | "));
  for (const abs of files) {
    const src = fs.readFileSync(abs, "utf-8");
    if (!/<img\s|!\[/.test(src)) continue;
    withImg++;
    const rel = path.relative(ROOT, abs).replace(/\\/g, "/");
    const html = render(rel, src);
    for (const tag of html.match(/<img class="rev-img"[^>]*>/g) || []) {
      total++;
      const p = decodeURIComponent((tag.match(/src="([^"]+)"/)[1].split("path=")[1] || ""));
      if (!fs.existsSync(path.join(ROOT, p))) broken.push(rel + " → " + p);
    }
    const bads = html.match(/rev-img-bad/g) || [];
    if (bads.length) bad.push(rel + " (" + bads.length + " 处无法解析)");
  }
  check("扫了 " + withImg + " 篇带图笔记，共 " + total + " 张图", total > 0);
  check("全部图片文件存在", broken.length === 0, broken.slice(0, 6).join(" | "));
  check("没有无法解析的路径", bad.length === 0, bad.slice(0, 6).join(" | "));
}

console.log("\n[8] 笔记里的 ASCII 上下标（与闪卡、AI 回复共用同一套规则）");
{
  const html = R.mdRender("式：O(n^2) 与 ∬_D 和 a_i 都该有上下标；`x_i` 是代码不动。");
  check("文本里的 _ 下标 → <sub>", html.includes("∬<sub>D</sub>") && html.includes("a<sub>i</sub>"), html);
  check("文本里的 ^ 上标 → <sup>", html.includes("O(n<sup>2</sup>)"), html);
  check("行内代码里的 _ 原样保留", html.includes("<code>x_i</code>"), html);
  check("桥真的搭上了（不是原样漏出）", !html.includes("O(n^2)") && !html.includes("∬_D"), html);
  check("文件名/标识符不误伤", (() => {
    const h = R.mdRender("见 第5章_IO管理.md，字段 book_id，区间 60~70分钟。");
    return h.includes("第5章_IO管理.md") && h.includes("book_id") && h.includes("60~70分钟");
  })());
  // 公式仍然归 KaTeX（asciiMath 只碰文本段）
  const m = R.mdRender("公式 $x_{i}$ 与文本 x_i 混排");
  check("$...$ 仍走 KaTeX、文本段走上下标",
    /katex|tex-fallback/.test(m) && m.includes("x<sub>i</sub>") && !m.includes("@@MATH"), m);
}

console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
process.exit(fail === 0 ? 0 : 1);

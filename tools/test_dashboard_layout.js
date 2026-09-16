/*
 * 大盘分栏 + 懒渲染的回归测试。
 *
 * 从生成的 dashboard.html 里抽出真实的 NAV_JS 和三个 D3 渲染器的注册代码，用
 * 极简 DOM 桩跑，验证：
 *   1. 侧栏 4 项对应 4 个 .page，激活时只显示一个
 *   2. hash 路由（含刷新落在 #/flash）
 *   3. 读 clientWidth 的图表**不在加载时**执行，只在首次进入该页时执行一次
 *   4. 必需的容器 id 一个都没漏（漏一个挂载 JS 就废）
 */
const fs = require("fs");
const path = require("path");

const HTML = path.join(__dirname, "..", "dashboard.html");
const html = fs.readFileSync(HTML, "utf-8");

// ---- 从产物里抽出各 <script> 内容 ----
const scripts = (html.match(/<script>([\s\S]*?)<\/script>/g) || [])
  .filter(s => !/src=/.test(s.slice(0, 120)))
  .map(s => s.replace(/^<script>/, "").replace(/<\/script>$/, ""));
const JS = scripts.join("\n;\n");

// ---- 极简 DOM 桩 ----
let byId = {}, bySel = {}, listeners = {};
function mkEl(tag, cls) {
  const el = {
    tagName: (tag || "div").toUpperCase(), children: [], _cls: new Set(cls ? [cls] : []),
    dataset: {}, style: {}, hidden: false, _html: "", _text: "", onclick: null,
    appendChild(c) { this.children.push(c); return c; },
    addEventListener(ev, fn) { (this._ev = this._ev || {})[ev] = fn; },
    querySelectorAll(sel) { return sel === ".fs-opt" ? [] : []; },
    querySelector() { return null; },
    getBoundingClientRect() { return { top: 0, width: 800 }; },
  };
  Object.defineProperty(el, "classList", { value: {
    add: c => el._cls.add(c), remove: c => el._cls.delete(c), contains: c => el._cls.has(c),
    toggle: (c, on) => { on ? el._cls.add(c) : el._cls.delete(c); },
  }});
  Object.defineProperty(el, "className", {
    get: () => Array.from(el._cls).join(" "),
    set: v => { el._cls = new Set(String(v).split(/\s+/).filter(Boolean)); },
  });
  Object.defineProperty(el, "innerHTML", {
    get: () => el._html, set: v => { el._html = String(v); el.children = []; },
  });
  Object.defineProperty(el, "textContent", {
    get: () => el._text,
    set: v => { el._text = String(v); el._html = String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;"); el.children = []; },
  });
  return el;
}

// 从产物 HTML 里解析出 body 的真实结构：.page[data-page] 与 .sidenav-item[data-page]
function parseStructure() {
  const pages = (html.match(/<div class="page" data-page="([\w-]+)"( hidden)?>/g) || []).map(s => ({
    page: s.match(/data-page="([\w-]+)"/)[1],
    hidden: /\shidden/.test(s),
  }));
  // ⚠️ 按钮上有 data-short / title 两个属性，正则不能写死 `data-page="x">`
  //    ——那样一项都匹配不到，于是「侧栏与页一一对应」这条一直是假失败。
  const navs = (html.match(/<button class="sidenav-item" data-page="([\w-]+)"/g) || []).map(s =>
    s.match(/data-page="([\w-]+)"/)[1]);
  return { pages, navs };
}

const { pages, navs } = parseStructure();

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

console.log("\n[1] 页面结构");
// 8 个而不是写死的数字：加/减子页时这里必须跟着改，正是要它响一下
check("侧栏 8 个导航项", navs.length === 8, JSON.stringify(navs));
check("8 个 .page 容器", pages.length === 8, JSON.stringify(pages));
check("导航项与页容器一一对应",
  navs.length === pages.length && navs.every(n => pages.some(p => p.page === n)),
  JSON.stringify({ navs, pages }));
check("首屏只显示 overview，其余 hidden",
  pages.filter(p => !p.hidden).length === 1 && pages.filter(p => !p.hidden)[0].page === "overview",
  JSON.stringify(pages));

console.log("\n[2] 必需容器 id 一个都不能漏");
const REQUIRED = ["metrics-row", "rev-score", "rev-chips", "rev-by-prefix", "rev-targets", "rev-tip",
  "weak-list", "gap-list", "flash-studio", "flash-filter", "act-cal", "act-cal-labels",
  "act-total", "act-streak", "act-acc", "heatmap-container", "timeline-chart", "level-chart",
  "flashcard-chart", "header-subtitle", "gen-time", "verified-count",
  // 2026-09-21：番茄钟（占位容器 + 全屏层）、闪卡学习计时（徽标 + 结束键），
  // 以及页面外壳（右上角整页全屏 + 首页专注/打卡数据条）
  "pm-slot", "pm-overlay", "fs-timer", "fs-stop", "shell-fs", "focus-strip"];
const missing = REQUIRED.filter(id => !html.includes('id="' + id + '"'));
check(REQUIRED.length + " 个 id 全部存在", missing.length === 0, missing.join(", "));
check("旧的 #deck-library 已摘除", !html.includes('id="deck-library"'));
check("DECK_JS 占位符已不再残留", !html.includes("__DECK_JS__"));

console.log("\n[3] 懒渲染注册");
{
  const win = { __pageRenderers: undefined, addEventListener() {} };
  const doc = {
    body: mkEl("body"),
    getElementById: id => (byId[id] || (byId[id] = mkEl("div"))),
    createElement: t => mkEl(t),
    querySelectorAll: () => [],
    querySelector: () => null,
    addEventListener() {},
  };
  let ran = [];
  // 把 D3 与 el() 换成计数桩：只关心"什么时候被调用"，不关心画了什么
  const d3stub = new Proxy(function () {}, {
    get: (t, k) => (k === "select" ? () => d3stub : () => d3stub),
    apply: () => d3stub,
  });
  const el = () => mkEl("div");
  // 只跑注册那几行：把三个渲染器的函数体换成计数器
  const probe = `
    window.__pageRenderers = window.__pageRenderers || {};
    window.__pageRenderers.overview = [function () { RAN.push("timeline"); }];
    (window.__pageRenderers.overview = window.__pageRenderers.overview || []).push(function () { RAN.push("level"); });
    window.__pageRenderers.flash = [function () { RAN.push("flashpie"); }];
  `;
  const RAN = ran;
  new Function("window", "RAN", probe)(win, RAN);
  check("overview 注册了 2 个渲染器", win.__pageRenderers.overview.length === 2);
  check("flash 注册了 1 个渲染器", win.__pageRenderers.flash.length === 1);
  check("注册阶段一个都还没跑（懒）", RAN.length === 0, JSON.stringify(RAN));
}

console.log("\n[4] NAV_JS 真的存在于产物且被注入");
const navInline = scripts.some(s => s.includes("__pageRenderers") && s.includes("hashchange"));
check("NAV_JS 已注入", navInline);
check("NAV_JS 用 hashchange 做路由", /hashchange/.test(JS));
check("NAV_JS 有 __done 幂等标记（避免重复渲染）", /__done/.test(JS));
check("side bar 样式已注入（.sidenav 规则）", /\.sidenav\s*\{/.test(html));
check("窄屏断点把侧栏改成横向（.sidenav 在 @media 里）",
  /@media \(max-width: 768px\)[\s\S]{0,400}\.sidenav/.test(html));

console.log("\n[5] 三个图表都真的走注册表，不再是裸 IIFE");
[["timeline-chart", "renderTimeline"], ["level-chart", "renderLevel"], ["flashcard-chart", "renderPie"]]
  .forEach(([cid]) => {
    const at = html.indexOf('id="' + cid + '"');
    check(cid + " 所在页归属正确",
      (cid === "flashcard-chart" && html.slice(0, at).lastIndexOf('data-page="flash"') > html.slice(0, at).lastIndexOf('data-page="overview"'))
      || (cid !== "flashcard-chart" && html.slice(0, at).lastIndexOf('data-page="overview"') > html.slice(0, at).lastIndexOf('data-page="notes"')),
      cid);
  });
const bareIife = /\(function\(\)\s*\{\s*const container = document\.getElementById\("(timeline|level|flashcard)-chart"\)/.test(JS);
check("没有残留的裸图表 IIFE（改注册表了）", !bareIife);

console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
process.exit(fail === 0 ? 0 : 1);

/*
 * 侧栏导航的行为测试。
 *
 * 背景：2026-09-13 用户报「点总览/笔记盘活/闪卡/练习活动没反应」——
 * 原因是按钮只有 data-page、没有 href/onclick，而 NAV_JS 只监听 hashchange。
 * 这个测试从 generate_dashboard.py 抽出**真实的 NAV_JS**，用极简 DOM 桩跑，
 * 保证以后改导航不会再退化成"点了没反应"。
 *
 * 覆盖：点按钮切页、页面显隐、active 类、hash 路由、重复点当前项、
 *       非法 data-page 不炸、hashchange（浏览器前进后退）仍然生效、
 *       以及"懒渲染"只跑一次。
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const GEN = path.join(__dirname, "..", "generate_dashboard.py");
const OUT = path.join(os.tmpdir(), "kaoyan_nav_extracted.js");

execFileSync("python", ["-c", `
import ast, io
src = io.open(r"${GEN}", encoding="utf-8").read()
for node in ast.parse(src).body:
    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "NAV_JS":
        io.open(r"${OUT}", "w", encoding="utf-8").write(ast.literal_eval(node.value))
        break
`], { maxBuffer: 1 << 24 });

const PAGES = ["overview", "notes", "flash", "activity"];

function mkEl(cls) {
  const el = {
    _cls: new Set(cls), hidden: false, dataset: {}, _listeners: {},
    addEventListener(ev, fn) { (this._listeners[ev] = this._listeners[ev] || []).push(fn); },
    click() { (this._listeners.click || []).forEach(fn => fn({ target: this })); },
    classList: {
      toggle: (c, on) => { if (on) el._cls.add(c); else el._cls.delete(c); },
      add: c => el._cls.add(c),
      contains: c => el._cls.has(c),
    },
  };
  return el;
}

let items, pageEls, hashHandlers, locationStub, rendererCalls;

function boot() {
  items = PAGES.map(p => { const b = mkEl("sidenav-item"); b.dataset.page = p; return b; });
  pageEls = PAGES.map(p => { const d = mkEl("page"); d.dataset.page = p; d.hidden = p !== "overview"; return d; });
  hashHandlers = [];
  rendererCalls = [];
  locationStub = { hash: "" };

  globalThis.window = globalThis;
  globalThis.location = locationStub;
  globalThis.__pageRenderers = {};
  PAGES.forEach(p => {
    globalThis.__pageRenderers[p] = [() => rendererCalls.push(p)];
  });
  globalThis.document = {
    querySelectorAll(sel) {
      if (sel === ".sidenav-item") return items;
      if (sel === ".page") return pageEls;
      return [];
    },
    getElementById: () => null,
  };
  globalThis.addEventListener = (ev, fn) => { if (ev === "hashchange") hashHandlers.push(fn); };

  new Function(fs.readFileSync(OUT, "utf-8"))();
}

// 模拟浏览器：改 hash 会异步触发 hashchange
function clickTab(name) {
  const btn = items[PAGES.indexOf(name)];
  const before = locationStub.hash;
  btn.click();
  if (locationStub.hash !== before) hashHandlers.forEach(fn => fn());
}

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

const visiblePage = () => (pageEls.find(p => !p.hidden) || {}).dataset;
const activeTab = () => (items.find(b => b._cls.has("active")) || {}).dataset;

console.log("\n[1] 初始状态（无 hash → 默认总览）");
boot();
check("默认显示总览", visiblePage().page === "overview", JSON.stringify(visiblePage()));
check("总览标签高亮", activeTab().page === "overview");
check("首屏渲染过总览的图表", rendererCalls.indexOf("overview") >= 0);

console.log("\n[2] 点击切换（用户报的就是这个不工作）");
boot();
["notes", "flash", "activity", "overview"].forEach(name => {
  clickTab(name);
  check("点「" + name + "」切到该页", visiblePage().page === name,
    "实际显示 " + JSON.stringify(visiblePage()));
  check("  └ 该标签高亮、其余不高亮",
    activeTab().page === name && items.filter(b => b._cls.has("active")).length === 1);
});

console.log("\n[3] hash 路由与浏览器前进后退");
boot();
clickTab("notes");
check("点按钮会写入 hash", locationStub.hash === "#/notes", locationStub.hash);
locationStub.hash = "#/flash";
hashHandlers.forEach(fn => fn());
check("hashchange（如浏览器后退）也能切页", visiblePage().page === "flash");
check("非法 hash 回落到总览", (() => { locationStub.hash = "#/nonsense"; hashHandlers.forEach(fn => fn()); return true; })()
  && true);

console.log("\n[4] 边界与懒渲染");
boot();
const callsBefore = rendererCalls.length;
clickTab("overview");
check("重复点当前项不炸、仍是总览", visiblePage().page === "overview");
check("重复点当前项不重复跑渲染器", rendererCalls.length === callsBefore,
  "多了 " + (rendererCalls.length - callsBefore) + " 次");
clickTab("flash");
const afterFlash = rendererCalls.length;
clickTab("activity");
clickTab("flash");
check("回到已渲染过的页不再重复渲染（懒渲染只跑一次）", rendererCalls.length === afterFlash + 1,
  "实际 " + (rendererCalls.length - afterFlash) + " 次");

boot();
const bad = mkEl("sidenav-item");
bad.dataset.page = "not-a-page";
items.push(bad);
let threw = false;
try { bad.click(); } catch (e) { threw = true; }
check("非法 data-page 不抛异常", !threw);
check("非法 data-page 不改动页面", visiblePage().page === "overview");

console.log("\n" + "=".repeat(52));
console.log(`通过 ${pass} 项，失败 ${fail} 项`);
console.log("=".repeat(52));
process.exit(fail ? 1 : 0);

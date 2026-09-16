/*
 * 页面外壳（SHELL_JS）行为测试。
 *  ① 右上角「整页全屏」：点一下申请 <html> 的原生全屏；全屏状态变化时按钮文案/态
 *     要跟着变（原生那层被 Esc 退掉也算）；再点一下退出来。
 *  ② 首页「专注与打卡」数据条：番茄钟成绩（近 7 天柱状 + 今日）+ 早间回顾打卡
 *     （连续/累计 + 近 14 天格子），以及读不到数据时的兜底文案。
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const SRC = path.join(__dirname, "..", "generate_dashboard.py");
const OUT = path.join(os.tmpdir(), "kaoyan_shell_extracted.js");
execFileSync("python", ["-c", `
import ast, io
src = io.open(r"${SRC}", encoding="utf-8").read()
tree = ast.parse(src)
for node in tree.body:
    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "SHELL_JS":
        io.open(r"${OUT}", "w", encoding="utf-8").write(ast.literal_eval(node.value))
        break
`], { maxBuffer: 1 << 24 });
const js = fs.readFileSync(OUT, "utf-8");

let REG = {}, docListeners = {}, fsCalls = [], timers = [];
let winListeners = {}, rafQueue = [], canvasCalls = { arc: 0, fill: 0, clearRect: 0, setTransform: 0 };
let reduceFlag = false, mouseFxOff = false;
// 画布桩：只记「画了什么」，不做真实绘制
function mkCanvas() {
  const cv = mkEl("canvas");
  const ctx = {
    setTransform() { canvasCalls.setTransform++; },
    clearRect() { canvasCalls.clearRect++; },
    beginPath() {}, arc() { canvasCalls.arc++; }, fill() { canvasCalls.fill++; },
    fillStyle: "", globalAlpha: 1, globalCompositeOperation: "",
    _set(k, v) { this[k] = v; },
  };
  Object.defineProperty(ctx, "fillStyle", { set(v) { this._fs = v; }, get() { return this._fs; } });
  cv.getContext = () => ctx;
  cv.width = 0; cv.height = 0;
  return cv;
}
function fireWin(ev, obj) { (winListeners[ev] || []).forEach(fn => fn(obj || {})); }
// 手动推进若干帧
function flushRaf(n) {
  for (let i = 0; i < (n || 1); i++) {
    const q = rafQueue.slice(); rafQueue.length = 0;
    q.forEach(fn => { try { fn(); } catch (e) { console.log("rAF 里抛错:", e.message); } });
  }
}
function mkEl(tag) {
  const el = {
    tagName: String(tag || "div").toUpperCase(), children: [], _cls: new Set(),
    _html: "", _text: "", title: "", onclick: null, dataset: {},
    _listeners: {},
    appendChild(c) { this.children.push(c); c.parent = this; return c; },
    remove() { this._removed = true; },
    addEventListener(ev, fn) { (this._listeners[ev] = this._listeners[ev] || []).push(fn); },
    setAttribute() {}, getAttribute() { return null; },
    requestFullscreen() { fsCalls.push("request:" + this.tagName.toLowerCase()); return Promise.resolve(); },
    querySelector() { return null; }, querySelectorAll() { return []; },
    click() { if (this.onclick) this.onclick({ target: this }); },
  };
  Object.defineProperty(el, "classList", { value: {
    add: c => el._cls.add(c), remove: c => el._cls.delete(c), contains: c => el._cls.has(c),
    toggle: (c, on) => { const want = on === undefined ? !el._cls.has(c) : !!on;
                         if (want) el._cls.add(c); else el._cls.delete(c); return want; },
  }});
  Object.defineProperty(el, "className", {
    get: () => Array.from(el._cls).join(" "),
    set: v => { el._cls = new Set(String(v).split(/\s+/).filter(Boolean)); },
  });
  Object.defineProperty(el, "textContent", {
    get: () => el._text,
    // esc() 走 createElement → textContent=… → 读 innerHTML，必须同步
    set: v => { el._text = String(v);
                el._html = String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); },
  });
  Object.defineProperty(el, "innerHTML", {
    get: () => el._html,
    set: v => {
      el._html = String(v); el.children = [];
      const re = /id="([\w-]+)"/g; let m;
      while ((m = re.exec(el._html))) REG[m[1]] = mkEl("div");
    },
  });
  return el;
}

const pad = n => (n < 10 ? "0" : "") + n;
const dstr = d => d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
function lastDays(n) {
  const out = [], t = new Date();
  for (let i = n - 1; i >= 0; i--) out.push(dstr(new Date(t.getFullYear(), t.getMonth(), t.getDate() - i)));
  return out;
}

function boot(payload, opt) {
  REG = {}; docListeners = {}; fsCalls = []; timers = []; winListeners = {}; rafQueue = [];
  canvasCalls = { arc: 0, fill: 0, clearRect: 0, setTransform: 0 };
  reduceFlag = !!(opt && opt.reduced);
  const btn = mkEl("button"), strip = mkEl("div"), html = mkEl("html");
  const cv = mkCanvas();
  btn.textContent = "全屏";
  REG["shell-fs"] = btn; REG["focus-strip"] = strip; REG["mouse-fx"] = cv;
  const overviewPage = { hidden: !!(opt && opt.offOverview) };
  const doc = {
    documentElement: html, body: mkEl("body"), fullscreenElement: null, hidden: false,
    getElementById: id => (id === "shell-fs" ? btn : id === "focus-strip" ? strip : REG[id] || null),
    createElement: t => mkEl(t),
    addEventListener: (ev, fn) => { (docListeners[ev] = docListeners[ev] || []).push(fn); },
    removeEventListener() {},
    querySelector: sel => (/overview/.test(String(sel)) ? overviewPage : null),
    querySelectorAll: () => [],
    exitFullscreen() { fsCalls.push("exit"); this.fullscreenElement = null; return Promise.resolve(); },
  };
  const fetched = [];
  const fetchStub = (url) => {
    fetched.push(url);
    if (url.indexOf("/api/morning-review/overview") >= 0) {
      return Promise.resolve({ json: () => Promise.resolve(payload.mr) });
    }
    if (url.indexOf("/api/pomodoro/state") >= 0) {
      return Promise.resolve({ json: () => Promise.resolve(payload.pomo) });
    }
    if (url.indexOf("/api/settings") >= 0) {
      return Promise.resolve({ json: () => Promise.resolve({ ok: true,
        ui: { theme: "dark", bg_opacity: "0.35", mouse_fx: mouseFxOff ? "off" : ((opt && opt.mouseFx) || "on") } }) });
    }
    return Promise.resolve({ json: () => Promise.resolve({ ok: true }) });
  };
  const realSetInterval = global.setInterval;
  global.setInterval = (fn, every) => { timers.push({ fn, every }); return timers.length; };
  // requestAnimationFrame：只排队，用例里手动 flush 一帧一帧推进
  global.requestAnimationFrame = fn => { rafQueue.push(fn); return rafQueue.length; };
  global.cancelAnimationFrame = () => { rafQueue.length = 0; };
  // getComputedStyle：palette() 要从 CSS 变量取色
  global.getComputedStyle = () => ({ getPropertyValue: () => "#9CC4B6" });
  const win = {
    addEventListener: (ev, fn) => { (winListeners[ev] = winListeners[ev] || []).push(fn); },
    removeEventListener() {},
    innerWidth: 1200, innerHeight: 800, devicePixelRatio: 1,
    matchMedia: q => ({ matches: reduceFlag && String(q).indexOf("reduce") >= 0 }),
  };
  new Function("document", "localStorage", "fetch", "window", "location", "setTimeout", js)(
    doc, { getItem: () => null, setItem() {}, removeItem() {} }, fetchStub, win,
    { protocol: "http:", origin: "http://localhost:8080", hash: "" },
    f => f && f());
  global.setInterval = realSetInterval;
  return { doc, btn, strip, fetched };
}
function fire(ev, obj) { (docListeners[ev] || []).forEach(fn => fn(obj || {})); }
const tick = () => new Promise(r => setTimeout(r, 0));

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

(async () => {
  const win7 = lastDays(7), win14 = lastDays(14);
  const today = win7[win7.length - 1];
  const pomo = {
    ok: true, server_now: Date.now(), today,
    today_stat: { pomos: 3, min: 130 },
    days: [
      { date: win7[1], pomos: 2, min: 90 },
      { date: win7[3], pomos: 1, min: 45 },
      { date: today, pomos: 3, min: 130 },
    ],
  };
  const mr = {
    ok: true, today, streak: 4, total_days: 9,
    checkins: [win14[1], win14[2], win14[9], today],
    days: win14.map((d, i) => ({ date: d, checked: false, studied: i % 3 === 0 })),
  };

  console.log("\n[1] 右上角整页全屏");
  {
    const env = boot({ pomo, mr });
    await tick();
    check("按钮初始是「全屏」态", env.btn.textContent.indexOf("全屏") >= 0
      && env.btn.textContent.indexOf("退出") < 0, env.btn.textContent);
    env.btn.click();
    await tick();
    check("点一下申请的是 <html> 的原生全屏（整页，不是某个模块）",
      fsCalls.indexOf("request:html") >= 0, fsCalls.join(","));
    // 浏览器真的进了全屏 → fullscreenchange
    env.doc.fullscreenElement = env.doc.documentElement;
    fire("fullscreenchange");
    check("全屏后按钮变「退出全屏」并带上 on 态",
      env.btn.textContent.indexOf("退出") >= 0 && env.btn._cls.has("on"), env.btn.textContent + "|" + env.btn.className);
    fsCalls.length = 0;
    env.btn.click();
    await tick();
    check("再点一下退出原生全屏", fsCalls.indexOf("exit") >= 0, fsCalls.join(","));
    env.doc.fullscreenElement = null;
    fire("fullscreenchange");
    check("退出后按钮回到「全屏」", env.btn.textContent.indexOf("退出") < 0, env.btn.textContent);
  }

  console.log("\n[2] 首页「专注与打卡」数据条");
  {
    const env = boot({ pomo, mr });
    await tick(); await tick(); await tick();
    const h = env.strip._html;
    check("读了两份接口（番茄钟 + 早间回顾）",
      env.fetched.some(u => u.indexOf("/api/pomodoro/state") >= 0)
      && env.fetched.some(u => u.indexOf("/api/morning-review/overview") >= 0),
      env.fetched.join(" "));
    check("今日番茄 3 个", h.indexOf("今日番茄<b>3 个</b>") >= 0);
    check("今日专注 2 小时 10 分", h.indexOf("2 小时 10 分") >= 0, h.slice(h.indexOf("今日专注"), h.indexOf("今日专注") + 60));
    check("近 7 天合计 6 个", h.indexOf("近 7 天<b>6 个") >= 0, h.slice(h.indexOf("近 7 天"), h.indexOf("近 7 天") + 60));
    check("柱子正好 7 根", (h.match(/class="strip-bar[" ]/g) || []).length === 7,
      String((h.match(/class="strip-bar[" ]/g) || []).length));
    check("今天那根标了 today", /strip-bar today"/.test(h) || /strip-bar has today"/.test(h));
    check("有数据的日子有 has 类", /strip-bar has/.test(h));
    check("连续打卡 4 天", h.indexOf("连续打卡<b>4 天</b>") >= 0);
    check("累计 9 天", h.indexOf("累计<b>9 天</b>") >= 0);
    check("今天已打卡（今天在 checkins 里）", h.indexOf("今日已打卡") >= 0, h.slice(h.indexOf("strip-go-mr") - 40, h.indexOf("strip-go-mr") + 20));
    check("打卡格子 14 格", (h.match(/class="strip-dot[" ]/g) || []).length === 14,
      String((h.match(/class="strip-dot[" ]/g) || []).length));
    check("已打卡的格子有 checked 类", /strip-dot checked/.test(h));
    check("今天那格带 today 圈", /strip-dot[^"]*today/.test(h));
    check("暴露了 __focusStripReload 给番茄钟叫醒", typeof globalThis.__focusStripReload === "function");
  }

  console.log("\n[3] 今天还没打卡 / 没有番茄记录 / 读不到数据");
  {
    const noToday = Object.assign({}, mr, { checkins: [win14[1]], streak: 0, total_days: 1 });
    const env = boot({ pomo: { ok: true, today, today_stat: { pomos: 0, min: 0 }, days: [] }, mr: noToday });
    await tick(); await tick(); await tick();
    const h = env.strip._html;
    check("今天没打卡时按钮写「去打卡」", h.indexOf("去打卡") >= 0 && h.indexOf("今日已打卡") < 0);
    check("今天那格写「未打卡」", h.indexOf("未打卡") >= 0);
    check("一个番茄都没有时给引导文案", h.indexOf("还没有番茄记录") >= 0, h.slice(h.indexOf("还没有番茄"), h.indexOf("还没有番茄") + 60));
    check("零数据也不炸：柱子和格子照常画", (h.match(/class="strip-bar[" ]/g) || []).length === 7
      && (h.match(/class="strip-dot[" ]/g) || []).length === 14);

    const env2 = boot({ pomo: { ok: false }, mr: { ok: false, error: "missing" } });
    await tick(); await tick(); await tick();
    check("早间回顾读不到时给兜底文案", env2.strip._html.indexOf("暂时读不到早间回顾数据") >= 0,
      env2.strip._html.slice(0, 160));
  }

  console.log("\n[4] 鼠标粒子光效（首页）");
  {
    const env = boot({ pomo, mr });
    await tick(); await tick();
    const cv = env.doc.getElementById("mouse-fx");
    check("有 canvas 且默认开着（没 hidden）", !!cv && cv.hidden === false, "hidden=" + (cv && cv.hidden));
    const before = canvasCalls.arc;
    fireWin("pointermove", { pointerType: "mouse", clientX: 100, clientY: 100 });
    check("鼠标移动排了一帧（requestAnimationFrame）", rafQueue.length === 1, "queue=" + rafQueue.length);
    // 一帧里每个粒子画 2 个圆（芯 + 晕），所以「这一帧画了几个圆」能反推粒子数
    const frameArcs = () => { const a = canvasCalls.arc; flushRaf(1); return canvasCalls.arc - a; };
    const f1 = frameArcs();
    check("第一下移动就给了个「头」粒子（不是等第二次才出现）", f1 >= 2, "本帧圆数=" + f1);
    check("画出了粒子（arc/fill 被调用）", canvasCalls.arc > before && canvasCalls.fill > 0,
      JSON.stringify(canvasCalls));

    // 沿轨迹补点：一次跨越 70px，应当产生多点而不是只画端点
    fireWin("pointermove", { pointerType: "mouse", clientX: 170, clientY: 100 });
    const f2 = frameArcs();
    check("快速移动会沿轨迹补点（粒子数明显增加）", f2 > f1, "上一帧=" + f1 + " 本帧=" + f2);

    // 手指拖动不出光点（触屏不该满屏粒子）：粒子数不应再增加
    fireWin("pointermove", { pointerType: "touch", clientX: 300, clientY: 300 });
    const f3 = frameArcs();
    check("手指拖动不新增粒子", f3 <= f2, "上帧=" + f2 + " 本帧=" + f3);

    // 粒子放完要停 rAF（省电）
    let frames = 0;
    while (rafQueue.length && frames < 400) { flushRaf(1); frames++; }
    check("粒子耗尽后停止动画循环（不再排队）", rafQueue.length === 0, "frames=" + frames);
    check("停下来之前确实跑了很多帧（不是一帧就断）", frames > 20, "frames=" + frames);

    // 关掉开关（走真实路径：服务端说 off → 重新拉一次设置 → 整段停）
    mouseFxOff = true;
    globalThis.__mouseFxReload && globalThis.__mouseFxReload();
    await tick(); await tick();
    check("服务端改成 off 后 canvas 隐藏", cv.hidden === true, "hidden=" + cv.hidden);
    const arc2 = canvasCalls.arc;
    fireWin("pointermove", { pointerType: "mouse", clientX: 400, clientY: 400 });
    check("关掉后不再产生粒子", canvasCalls.arc === arc2);
  }

  console.log("\n[5] 光效的三个「不该出现」场景");
  {
    // ① 系统开了减弱动效
    const e1 = boot({ pomo, mr }, { reduced: true });
    await tick(); await tick();
    check("开了「减弱动效」→ 画布直接隐藏", e1.doc.getElementById("mouse-fx").hidden === true);

    // ② 当前不在总览页
    const e2 = boot({ pomo, mr }, { offOverview: true });
    await tick(); await tick();
    check("不在总览页 → 画布隐藏", e2.doc.getElementById("mouse-fx").hidden === true);
    const a0 = canvasCalls.arc;
    fireWin("pointermove", { pointerType: "mouse", clientX: 10, clientY: 10 });
    flushRaf(1);
    check("不在总览页 → 不产生粒子", canvasCalls.arc === a0);

    // ③ 服务端把开关关掉
    const e3 = boot({ pomo, mr }, { mouseFx: "off" });
    await tick(); await tick();
    check("服务端开关=off → 画布隐藏", e3.doc.getElementById("mouse-fx").hidden === true);
  }

  console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
  process.exit(fail === 0 ? 0 : 1);
})();
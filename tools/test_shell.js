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
// 走公共脚本：它会把 DAY_START_JS（studyDay / DAY_START_HOUR）一起带上，
// 只抠 SHELL_JS 的话跑起来是 ReferenceError。
execFileSync("python", [path.join(__dirname, "extract_js.py"), "SHELL_JS", OUT],
  { maxBuffer: 1 << 24 });
const js = fs.readFileSync(OUT, "utf-8");
const _day = require("./_day");

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
    querySelector() { return null; },
    // 只有「周曲线 / 月热力图」那对切换按钮需要真返回节点：从 innerHTML 里把
    // data-view 抠出来造成可点的桩，其它选择器一律空数组。
    // 同一份 innerHTML 缓存同一批对象——render() 里刚挂上的 onclick 得留到用例点击时。
    querySelectorAll(sel) {
      if (String(sel).indexOf("range-btn") < 0) return [];
      if (!this._qc || this._qc.html !== this._html) this._qc = { html: this._html, nodes: null };
      if (!this._qc.nodes) {
        const out = [], re = /<button class="([^"]*)" data-view="(\w+)"/g;
        let m;
        while ((m = re.exec(this._html))) {
          const b = mkEl("button");
          b.className = m[1]; b.dataset.view = m[2];
          out.push(b);
        }
        this._qc.nodes = out;
      }
      return this._qc.nodes;
    },
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
// 以学习日为最后一天（跟 SHELL_JS 的 lastDays 同一套）。用日历日的话，
// 0:00~4:00 之间会跟页面差一天，数据条的断言全错。见 tools/_day.js。
const lastDays = n => _day.lastDays(js, n);

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
  // 桩要像真的 Response 一样带 ok/status：代码里有 r.ok 的分支，
  // 少了这两个字段会一律走「请求失败」那条路，把正常路径全测成兜底路径。
  const res = (body, ok, status) => Promise.resolve({
    ok: ok === undefined ? true : ok,
    status: status || (ok === false ? 404 : 200),
    json: () => Promise.resolve(body),
  });
  const fetchStub = (url) => {
    fetched.push(url);
    // 旧版服务端：认识 /api/pomodoro/state，但不认 ?days=
    if (opt && opt.oldPomoServer && /\/api\/pomodoro\/state\?/.test(url)) {
      return res({ error: "not found" }, false, 404);
    }
    if (url.indexOf("/api/morning-review/overview") >= 0) return res(payload.mr);
    if (url.indexOf("/api/pomodoro/state") >= 0) return res(payload.pomo);
    if (url.indexOf("/api/settings") >= 0) {
      return res({ ok: true,
        ui: { theme: "dark", bg_opacity: "0.35", mouse_fx: mouseFxOff ? "off" : ((opt && opt.mouseFx) || "on") } });
    }
    return res({ ok: true });
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
    check("番茄钟要了 42 天（月热力图铺 5 周，14 天不够）",
      env.fetched.some(u => /\/api\/pomodoro\/state\?days=42/.test(u)), env.fetched.join(" "));
    check("今日番茄 3 个", h.indexOf("今日番茄<b>3 个</b>") >= 0);
    check("今日专注 2 小时 10 分", h.indexOf("2 小时 10 分") >= 0, h.slice(h.indexOf("今日专注"), h.indexOf("今日专注") + 60));
    check("近 7 天合计 6 个", h.indexOf("近 7 天<b>6 个") >= 0, h.slice(h.indexOf("近 7 天"), h.indexOf("近 7 天") + 60));
    check("连续打卡 4 天", h.indexOf("连续打卡<b>4 天</b>") >= 0);
    check("累计打卡 9 天", h.indexOf("累计打卡<b>9 天</b>") >= 0);
    check("今天已打卡（今天在 checkins 里）", h.indexOf("今日已打卡") >= 0, h.slice(h.indexOf("strip-go-mr") - 40, h.indexOf("strip-go-mr") + 20));

    check("两个视角按钮都在，默认停在周曲线",
      h.indexOf(">周曲线</button>") >= 0 && h.indexOf(">月热力图</button>") >= 0
      && h.indexOf('class="range-btn is-active" data-view="week"') >= 0,
      h.slice(h.indexOf("strip-view"), h.indexOf("strip-view") + 220));
    check("周曲线正好 7 个点", (h.match(/class="curve-pt[" ]/g) || []).length === 7,
      String((h.match(/class="curve-pt[" ]/g) || []).length));
    check("轴上 7 个星期标签", (h.match(/class="curve-axis-x"/g) || []).length === 7);
    check("画的是曲线（面积 + 线），不是柱子",
      h.indexOf("curve-area") >= 0 && h.indexOf("curve-line") >= 0 && h.indexOf("strip-bar") < 0);
    // 值的映射：max=130 顶到 26%，0 压在基线 86%（都是 viewBox 的百分数）。
    // 基线不能再往下挪了——离轴太近，零值那几天的平线会从星期上压过去。
    check("峰值那天顶到最高（bottom:74%）", h.indexOf("bottom:74.00%") >= 0,
      h.slice(h.indexOf("curve-layer"), h.indexOf("curve-layer") + 260));
    check("没记录的那几天压在基线上", (h.match(/bottom:14\.00%/g) || []).length === 4,
      String((h.match(/bottom:14\.00%/g) || []).length));
    // win7 = 最近 7 天。checkins 落在 win7 里的是 win7[2] 和今天；win7[5] 只 studied。
    check("轴上标出 2 天已打卡", (h.match(/em class="checked"/g) || []).length === 2,
      String((h.match(/em class="checked"/g) || []).length));
    check("轴上标出 1 天「有复习没打卡」", (h.match(/em class="studied"/g) || []).length === 1,
      String((h.match(/em class="studied"/g) || []).length));
    check("曲线下面给了状态的说明", h.indexOf("圆点下面那格是当天的打卡状态") >= 0);
    check("暴露了 __focusStripReload 给番茄钟叫醒", typeof globalThis.__focusStripReload === "function");

    // ---- 切到月热力图 ----
    const pick = v => env.strip.querySelectorAll("#strip-view .range-btn")
      .filter(b => b.dataset.view === v)[0];
    pick("month").click();
    await tick();
    const h2 = env.strip._html;
    check("切过去后按钮态跟着换",
      h2.indexOf('class="range-btn is-active" data-view="month"') >= 0,
      h2.slice(h2.indexOf("strip-view"), h2.indexOf("strip-view") + 220));
    check("周曲线撤掉了，换成热力图", h2.indexOf("curve-line") < 0 && h2.indexOf("hm-grid") >= 0);
    // 标签配平：hm-stats 必须是 .hm-wrap 的直接子元素，嵌进 .hm-side 里就靠不了右
    check("div 标签配平（没有嵌套写错）",
      (h2.match(/<div[\s>]/g) || []).length === (h2.match(/<\/div>/g) || []).length,
      (h2.match(/<div[\s>]/g) || []).length + " vs " + (h2.match(/<\/div>/g) || []).length);
    check("汇总数没被嵌进 .hm-side",
      h2.indexOf("</div><div class=\"hm-stats\">") >= 0,
      h2.slice(h2.indexOf("hm-stats") - 80, h2.indexOf("hm-stats") + 20));
    check("热力图 35 格（5 周 × 7 天）", (h2.match(/class="hm-cell hm-d /g) || []).length === 35,
      String((h2.match(/class="hm-cell hm-d /g) || []).length));
    check("格子有底色分档", /hm-d l[1-5]/.test(h2));
    check("打过卡的格子带 checked 圈", /l\d checked"/.test(h2));
    check("今天那格带 today 框", /hm-d [^"]*today/.test(h2));
    check("侧边有图例", h2.indexOf("专注时长") >= 0 && h2.indexOf("已打卡") >= 0);
    // 三天都在最近 5 周里：90 + 45 + 130 = 265 分钟
    check("右侧三个汇总数（合计 / 天数 / 最久）",
      h2.indexOf("近 5 周专注<b>4 小时 25 分</b>") >= 0
      && h2.indexOf("有专注的天数<b>3 天</b>") >= 0
      && h2.indexOf("最久的一天<b>2 小时 10 分</b>") >= 0,
      h2.slice(h2.indexOf("hm-stats"), h2.indexOf("hm-stats") + 220));
    check("切回周曲线也认",
      h2.indexOf("月热力图") >= 0 && pick("week").onclick !== null);
    pick("week").click();
    await tick();
    check("切回来还是周曲线", env.strip._html.indexOf("curve-line") >= 0
      && (env.strip._html.match(/class="curve-pt[" ]/g) || []).length === 7);
  }

  console.log("\n[3] 今天还没打卡 / 没有番茄记录 / 读不到数据");
  {
    const noToday = Object.assign({}, mr, { checkins: [win14[1]], streak: 0, total_days: 1 });
    const env = boot({ pomo: { ok: true, today, today_stat: { pomos: 0, min: 0 }, days: [] }, mr: noToday });
    await tick(); await tick(); await tick();
    const h = env.strip._html;
    check("今天没打卡时按钮写「去打卡」", h.indexOf("去打卡") >= 0 && h.indexOf("今日已打卡") < 0);
    check("今天那格写「未打卡」", h.indexOf("未打卡") >= 0);
    check("一个番茄都没有时给引导文案（周视角）", h.indexOf("还没有专注记录") >= 0,
      h.slice(h.indexOf("还没有专注"), h.indexOf("还没有专注") + 60));
    check("零数据也不炸：切换按钮照常在", h.indexOf("data-view=\"month\"") >= 0);

    // 空数据切到月视角：不该摆一排空格子，换文案
    const pick2 = v => env.strip.querySelectorAll("#strip-view .range-btn")
      .filter(b => b.dataset.view === v)[0];
    pick2("month").click();
    await tick();
    check("零数据也月视角给引导文案", env.strip._html.indexOf("近 5 周还没有专注记录") >= 0,
      env.strip._html.slice(env.strip._html.indexOf("strip-view"), env.strip._html.indexOf("strip-view") + 220));
    check("零数据不画空网格", env.strip._html.indexOf("hm-grid") < 0);

    const env2 = boot({ pomo: { ok: false }, mr: { ok: false, error: "missing" } });
    await tick(); await tick(); await tick();
    // MR 是 null 时打卡那几个数写 "--"，写 0 天会被当成「断签了」
    check("早间回顾读不到时打卡数写「--」",
      /连续打卡<b>-- 天<\/b>/.test(env2.strip._html) && /累计打卡<b>-- 天<\/b>/.test(env2.strip._html),
      env2.strip._html.slice(env2.strip._html.indexOf("连续打卡"), env2.strip._html.indexOf("连续打卡") + 80));
    check("两个接口都读不到时不炸：引导文案 + 打卡数 --",
      env2.strip._html.indexOf("还没有专注记录") >= 0
      && env2.strip._html.indexOf("data-view=\"week\"") >= 0);

    // 服务端是旧版（不认识 ?days=42）时只能退回 14 天：月视角必须说一声，
    // 不然 3 周前的空格子会被当成「那天没学」
    const env3 = boot({ pomo: { ok: false }, mr }, { oldPomoServer: true });
    await tick(); await tick(); await tick();
    check("退回 14 天时周视角不聒噪", env3.strip._html.indexOf("只读到近 14 天") < 0);
    env3.strip.querySelectorAll("#strip-view .range-btn").filter(b => b.dataset.view === "month")[0].click();
    await tick();
    check("退回 14 天时月视角挂出提示",
      env3.strip._html.indexOf("只读到近 14 天") >= 0,
      env3.strip._html.slice(env3.strip._html.indexOf("strip-hint")));
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
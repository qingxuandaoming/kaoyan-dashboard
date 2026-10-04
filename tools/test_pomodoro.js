/*
 * 番茄钟（POMO_JS）行为测试。
 *
 * 从 generate_dashboard.py 里抽出真实的 POMO_JS，用「虚拟时钟 + 极简 DOM」跑：
 *   · Date.now / setInterval 全接管 → 45 分钟的段不用真等，advance() 一推就到
 *   · 覆盖：预设段序、开始/暂停/继续的墙钟语义、跳过与提前结束只记已专注的分钟数、
 *     关页面期间补齐、全屏搬家（节点从 #pm-slot 搬到 #pm-overlay）、轮播背景三层开关与交叉淡入
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const SRC = path.join(__dirname, "..", "generate_dashboard.py");
const OUT = path.join(os.tmpdir(), "kaoyan_pomo_extracted.js");
// 走公共脚本：它会把 DAY_START_JS（studyDay / DAY_START_HOUR）一起带上，
// 只抠 POMO_JS 的话跑起来是 ReferenceError。
execFileSync("python", [path.join(__dirname, "extract_js.py"), "POMO_JS", OUT],
  { maxBuffer: 1 << 24 });
const js = fs.readFileSync(OUT, "utf-8");
const { dayKey } = require("./_day");

/* ---------------- 虚拟时钟 ---------------- */
const MIN = 60000;
// ⚠️ 用例里的「今天」必须跟着**真实日期**走，而且必须和页面同一套规则
//    （凌晨 4 点前算前一天）。写死某一天会失败；用日历日而页面用学习日也会失败
//    ——2026-09-16 凌晨踩过一次（桩里回 09-15、todayKey() 已是 09-16），
//    2026-09-17 凌晨加学习日起点时又踩了一次（桩里回 09-17、todayKey() 是 09-16）。
//    两次都是 mergeToday 判定「服务端那份属于新的一天」把刚记的成绩覆盖成 0。
//    规则和常量统一从抽出来的页面 JS 里读，见 tools/_day.js。
const localToday = d => dayKey(js, d);
let fakeNow = 0;
let timers = [];        // {id, fn, every, next}
let nextTimerId = 1;
function advance(ms, step) {
  step = step || 200;
  let left = ms;
  while (left > 0) {
    const d = Math.min(step, left);
    fakeNow += d; left -= d;
    // 只跑「到点」的那些：背景的 20 秒一换不能被 250ms 一档拖着跑
    for (const t of timers.slice()) {
      if (!t || t.dead) continue;
      if (t.next <= fakeNow) { t.fn(); t.next = fakeNow + t.every; }
    }
  }
}
const realSetInterval = global.setInterval, realClearInterval = global.clearInterval;

/* ---------------- 画面桩（文字自适应配色用） ----------------
   POMO_JS 换轮播图时会按 cover 反推可见矩形，画进 32×32 的 canvas 量一次亮度，
   再决定这一轮的卡片用深字还是白字。Node 里没有真 canvas，所以这一档是可插拔的：
     · px(u, v) 按归一化坐标给一个像素色 —— 「这是什么样的一张照片」
     · w/h      图片原始尺寸；cw/ch 卡片尺寸（不量就当作卡片被藏着）
     · ink      遮罩色 --mo-rgb，不写就用深色主题默认
   不打开这一档时 clientWidth=0、naturalWidth=0，measureBg 一律返回 null ——
   和真浏览器里「卡片正被切在别的子页」是同一条路，老用例不受影响。 */
let visual = null;
let REG = {}, CLASSES = {}, BYATTR = {};
function mkEl(tag) {
  const el = {
    tagName: String(tag || "div").toUpperCase(), children: [], _cls: new Set(),
    dataset: {}, hidden: false, onclick: null, title: "", _text: "", _html: "",
    _made: [], parent: null,
    // 小窗要量尺寸才摆位置；给个固定值，别让 placeMini 因为 offsetWidth=0 直接返回
    offsetWidth: 152, offsetHeight: 122,
    get clientWidth() { return (visual && visual.cw) || 0; },
    get clientHeight() { return (visual && visual.ch) || 0; },
    // canvas：drawImage 不用真画，getImageData 直接按 px() 现造一张
    getContext() {
      return {
        drawImage() {},
        getImageData: (x, y, w, h) => {
          const a = new Uint8ClampedArray(w * h * 4);
          for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
            const p = visual.px(i / w, j / h), o = (j * w + i) * 4;
            a[o] = p[0]; a[o + 1] = p[1]; a[o + 2] = p[2]; a[o + 3] = 255;
          }
          return { data: a };
        },
      };
    },
    getBoundingClientRect() { return { left: 100, top: 100, width: this.offsetWidth, height: this.offsetHeight }; },
    style: { _vars: {}, backgroundImage: "", strokeDasharray: "", strokeDashoffset: "",
             width: "", left: "", top: "",
             setProperty(k, v) { this._vars[k] = v; } },
    appendChild(c) {
      // 真 DOM 的 appendChild 会把它从原父节点摘走；番茄钟「全屏 = 节点搬家」
      // 正是靠这个语义，桩里不跟着做就会一直挂在两个容器里，测不出搬家。
      if (c.parent && c.parent !== this) {
        const k = c.parent.children.indexOf(c);
        if (k >= 0) c.parent.children.splice(k, 1);
      }
      this.children.push(c); c.parent = this; return c;
    },
    addEventListener(ev, fn) { (this._l = this._l || {})[ev] = ((this._l[ev]) || []).concat([fn]); },
    _fire(ev, obj) { ((this._l || {})[ev] || []).forEach(fn => fn(obj || {})); },
    // data-* 走 dataset（真 DOM 也是这么对应的），其余属性单独存。
    // ⚠️ 早期版本这里恒返回 null，导致 bindMiniActions 里 getAttribute("data-act")
    //    拿不到值 → 小窗按钮一个都没接上，测试却「看起来」在跑。
    // 元素祖先链查找：拖拽把手要判断「这一下是不是落在按钮/滑杆上」，
// 靠的就是 closest —— 桩里没有它，那条守卫就等于没测。
    closest(sel) {
      let n = this;
      const parts = String(sel).split(",").map(x => x.trim()).filter(Boolean);
      while (n) {
        for (const one of parts) {
          if (one[0] === "." && n._cls && n._cls.has(one.slice(1))) return n;
          if (one[0] === "#" && n._id === one.slice(1)) return n;
          if (one[0] !== "." && one[0] !== "#" && n.tagName === one.toUpperCase()) return n;
        }
        n = n.parent;
      }
      return null;
    },
    setAttribute(k, v) { this._attrs = this._attrs || {}; this._attrs[k] = String(v); },
    getAttribute(k) {
      if (/^data-/.test(k)) {
        const key = k.slice(5);
        return this.dataset && key in this.dataset ? this.dataset[key] : null;
      }
      return (this._attrs && this._attrs[k] != null) ? this._attrs[k] : null;
    },
    set id(v) { this._id = String(v); REG[this._id] = this; },
    get id() { return this._id || ""; },
    // 锁屏那节用：audio.play()/pause()
    play() { audioPlays++; return Promise.resolve(); },
    pause() { audioPauses++; },
    querySelector(sel) { return qsa(sel)[0] || null; },
    querySelectorAll(sel) { return qsa(sel); },
    click() { if (this.disabled) return; if (this.onclick) this.onclick({ target: this }); this._fire("click", { target: this }); },
  };
  Object.defineProperty(el, "classList", {
    get: () => ({ add: c => el._cls.add(c), remove: c => el._cls.delete(c), contains: c => el._cls.has(c),
                  toggle: (c, on) => { const w = on === undefined ? !el._cls.has(c) : !!on;
                                       if (w) el._cls.add(c); else el._cls.delete(c); return w; } }),
  });
  Object.defineProperty(el, "className", {
    get: () => Array.from(el._cls).join(" "),
    // ️ 真 DOM 里「元素有 class」就等于「能被该 class 选中」，不管 class 是
    //    HTML 解析来的还是 js 赋的。桩只索引 HTML 里那几个的话，程序化建出来的
    //    小窗就永远查不到——所以这里赋完类名要补登记。
    set: v => {
      for (const c of el._cls) {
        const arr = CLASSES[c];
        if (arr) { const k = arr.indexOf(el); if (k >= 0) arr.splice(k, 1); }
      }
      el._cls = new Set(String(v).split(/\s+/).filter(Boolean));
      for (const c of el._cls) (CLASSES[c] = CLASSES[c] || []).push(el);
    },
  });
  Object.defineProperty(el, "textContent", {
    get: () => el._text,
    // esc() 走的是 createElement → textContent=… → 读 innerHTML，
    // 所以这里必须把 _html 同步起来，否则所有 esc 出来的值都是空串
    set: v => {
      el._text = String(v);
      el._html = String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    },
  });
  Object.defineProperty(el, "innerHTML", {
    get: () => el._html,
    set: v => {
      el._html = String(v);
      // 重绘前先把自己上一批注册出去的节点摘掉，否则 querySelectorAll 会越点越多
      for (const old of el._made) {
        if (old._id && REG[old._id] === old) delete REG[old._id];
        for (const c of old._cls) {
          const arr = CLASSES[c];
          if (arr) { const k = arr.indexOf(old); if (k >= 0) arr.splice(k, 1); }
        }
        for (const kk of Object.keys(old.dataset)) {
          const arr = BYATTR["data-" + kk];      // 注册时用的是 data-xxx，这里不能拿裸名去查
          if (arr) { const k = arr.indexOf(old); if (k >= 0) arr.splice(k, 1); }
        }
      }
      el._made = []; el.children = [];
      parseInto(el, el._html);
    },
  });
  return el;
  function qsa(sel) {
    const s = String(sel).trim();
    if (s[0] === "#") return REG[s.slice(1)] ? [REG[s.slice(1)]] : [];
    if (s[0] === ".") return (CLASSES[s.slice(1)] || []).slice();
    // [data-act="toggle"] 这种带值的选择器：小窗/系统小窗都靠它找按钮
    let m = /^\[([\w-]+)="([^"]*)"\]$/.exec(s);
    if (m) {
      const key = m[1].replace(/^data-/, "");
      return (BYATTR[m[1]] || []).filter(e => e.dataset && e.dataset[key] === m[2]);
    }
    m = /^\[([\w-]+)\]$/.exec(s);
    if (m) return (BYATTR[m[1]] || []).slice();
    return [];
  }
}
function parseInto(host, html) {
  const re = /<(\w+)([^>]*)>/g;
  let m;
  while ((m = re.exec(html))) {
    const attrs = m[2];
    const e = mkEl(m[1]);
    const id = /\bid="([\w-]+)"/.exec(attrs);
    if (id) { e._id = id[1]; REG[id[1]] = e; }
    const cls = /class="([^"]*)"/.exec(attrs);
    if (cls) for (const c of cls[1].split(/\s+/).filter(Boolean)) {
      e._cls.add(c); (CLASSES[c] = CLASSES[c] || []).push(e);
    }
    const da = /\bdata-([\w-]+)="([^"]*)"/g;
    let g;
    while ((g = da.exec(attrs))) { e.dataset[g[1]] = g[2]; (BYATTR["data-" + g[1]] = BYATTR["data-" + g[1]] || []).push(e); }
    e.parent = host; host.children.push(e); host._made.push(e);
  }
}

let encoder = null;
let audioPlays = 0, audioPauses = 0, pipCalls = [], mediaSession = null, lastPipWin = null;

/* ---------------- 环境 ---------------- */
// srv：模拟服务端上的番茄钟状态（跨设备那一路）
//   { run, nowOffset, today_stat, days }
// nowOffset 用来模拟「服务端时钟比本机快 N 毫秒」，验证 end_at 的换算
// caps：模拟浏览器能力 { media: Media Session, pip: Document PiP }
function boot(pomoCfg, savedRun, savedDay, srv, caps, extraStore, vis) {
  REG = {}; CLASSES = {}; BYATTR = {}; timers = []; nextTimerId = 1; fakeNow = 1770000000000;
  visual = vis || null;
  audioPlays = 0; audioPauses = 0; pipCalls = []; mediaSession = null; lastPipWin = null;
  const slot = mkEl("div"), overlay = mkEl("div");
  overlay.hidden = true;
  const store = {};
  if (savedRun) store["kaoyan.pomo.run.v1"] = JSON.stringify(savedRun);
  if (savedDay) store["kaoyan.pomo.day.v1"] = JSON.stringify(savedDay);
  // extraStore：塞任意 localStorage 项（自己存的预设、小窗透明度…）做「刷新后还在」那类用例
  if (extraStore) Object.assign(store, extraStore);
  const doc = {
    title: "改造我们的学习", hidden: false,
    body: mkEl("body"),                // setFull 要往 body 上挂 pm-lock
    getElementById: id => (id === "pm-slot" ? slot : id === "pm-overlay" ? overlay : REG[id] || null),
    createElement: t => mkEl(t),
    addEventListener() {},
    querySelector: () => null, querySelectorAll: () => [],
    fullscreenElement: null,
  };
  global.setInterval = (fn, every) => { const t = { id: nextTimerId, fn, every, next: fakeNow + every };
                                        timers.push(t); nextTimerId++; return t.id; };
  global.clearInterval = id => { const t = timers.find(x => x && x.id === id); if (t) t.dead = true; };
  // 只换 Date.now，不换 Date 本身：todayKey() 里还要 new Date()
  Date.now = () => fakeNow;
  global.confirm = () => true;
  // 量亮度时要读 --mo-rgb（遮罩色跟主题走）。Node 里没有 getComputedStyle，给个最小的。
  global.getComputedStyle = () => ({
    getPropertyValue: k => (k === "--mo-rgb" && visual && visual.ink) ? visual.ink : "",
  });
  const fetched = [];
  const nowOffset = (srv && srv.nowOffset) || 0;
  // srv.rtt：模拟一次请求的往返耗时（毫秒）。桩把「服务端生成响应那一刻」定在
  // 往返中点，再把虚拟时钟往前推整个 RTT —— 这正是前端要做往返中点补偿的场景：
  // 不补偿的话 skew 里会多吃进 rtt/2（实测墙外授时源 361ms 的往返，一半就是 180ms）。
  // 每次请求都现读 srv.rtt，用例中途能把它调快（先按 0 起一轮，再拉长往返看补偿）
  const rttOf = () => (srv && srv.rtt) || 0;
  const srvNow = () => fakeNow + nowOffset + rttOf() / 2;
  const settle = (payload) => {
    const rtt = rttOf();
    if (rtt) fakeNow += rtt;
    return Promise.resolve({ json: () => Promise.resolve(payload) });
  };
  const fetchStub = (url, opt) => {
    fetched.push({ url, body: opt && opt.body ? JSON.parse(opt.body) : null, method: (opt && opt.method) || "GET" });
    if (url.indexOf("/api/settings") >= 0) {
      return Promise.resolve({ json: () => Promise.resolve(Object.assign({ ok: true,
        pomo: { images: [], interval: 20, dim: 0.35, show: "both", sound: "on" } }, pomoCfg && { pomo: pomoCfg })) });
    }
    if (url.indexOf("/api/pomodoro/state") >= 0) {
      const isPost = !!(opt && opt.method === "POST");
      const base = { ok: true, server_now: srvNow(), today: localToday(),
                     clock: (srv && srv.clock) || undefined,
                     today_stat: (srv && srv.today_stat) || { pomos: 0, min: 0 },
                     days: (srv && srv.days) || [] };
      if (isPost) {
        // 回一个带 server_updated 的 run，模拟服务端落库后的版本
        const echo = JSON.parse(JSON.stringify((opt && JSON.parse(opt.body).run) || {}));
        echo.server_updated = (srv && srv.nextUpdated) || 90001;
        return settle(Object.assign({}, base, { run: echo }));
      }
      return settle(Object.assign({}, base, { run: (srv && srv.run) || null }));
    }
    if (url.indexOf("/api/pomodoro/credit") >= 0) {
      // 回一个「服务端自增之后」的成绩：默认按请求本身算（pomos 默认 +1，
      // 显式传 0 就只加分钟）。srv.after_credit 可以覆盖它（跨设备用例要更大的累计值）。
      const req = (opt && opt.body) ? JSON.parse(opt.body) : {};
      const add = req.pomos === 0 ? 0 : 1;
      return settle({ ok: true, server_now: srvNow(), today: localToday(),
        clock: (srv && srv.clock) || undefined,
        today_stat: (srv && srv.after_credit) || { pomos: add, min: Math.round(Number(req.min) || 0) },
        days: (srv && srv.days) || [] });
    }
    if (url.indexOf("/api/time") >= 0) {
      const st = (srv && srv.time) || { calibrated: false, source: "system", offset_ms: 0,
                                        synced_at: 0, age_ms: 0, now: srvNow() };
      return settle(Object.assign({ ok: true }, st, { now: srvNow() }));
    }
    return Promise.resolve({ json: () => Promise.resolve({ ok: true }) });
  };
  // 浏览器能力桩：锁屏（Media Session）与系统小窗（Document PiP）
  // 默认都不给，正好验「不支持时按钮自己藏起来」这条。
  if (caps && caps.media) {
    mediaSession = {
      metadata: "unset", playbackState: "", position: null, handlers: {},
      setActionHandler(a, f) { this.handlers[a] = f; },
      setPositionState(o) { this.position = o; },
    };
    Object.defineProperty(global, "navigator", { value: { mediaSession }, configurable: true, writable: true });
    global.MediaMetadata = class { constructor(o) { Object.assign(this, o); } };
  } else {
    Object.defineProperty(global, "navigator", { value: {}, configurable: true, writable: true });
    global.MediaMetadata = undefined;
  }
  if (caps && caps.pip) {
    global.documentPictureInPicture = {
      requestWindow(o) {
        pipCalls.push(o);
        const body = mkEl("body");
        lastPipWin = {
          closed: false,
          document: { body, querySelector: s => body.querySelector(s),
                      querySelectorAll: s => body.querySelectorAll(s) },
          addEventListener() {}, close() { this.closed = true; },
        };
        return Promise.resolve(lastPipWin);
      },
    };
  } else {
    global.documentPictureInPicture = undefined;
    lastPipWin = null;
  }
  new Function("document", "localStorage", "fetch", "window", "location", "Image", js)(
    doc,
    { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } },
    fetchStub, global, { protocol: "http:", origin: "http://localhost:8080" },
    class { constructor() { setTimeout(() => this.onload && this.onload(), 0); }
            set src(v) { this._s = v; } get src() { return this._s; }
            get naturalWidth() { return (visual && visual.w) || 0; }
            get naturalHeight() { return (visual && visual.h) || 0; } }
  );
  return { doc, slot, overlay, store, fetched };
}

const tick = () => new Promise(r => realSetInterval.call(global, r, 0));
const card = () => REG["pm-time"] && REG["pm-time"].parent ? null : null;   // 见下方 host()
function hostOf() {
  // 卡片根节点：#pm-time 的祖先链上带 pm-card 的那个
  let n = REG["pm-time"];
  while (n && !n._cls.has("pm-card")) n = n.parent;
  return n;
}
const txt = id => (REG[id] ? String(REG[id].textContent || REG[id]._html || "") : "<缺失>");
const cls = id => (REG[id] ? REG[id].className : "<缺失>");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

(async () => {
  console.log("\n[1] 结构与预设");
  {
    const env = boot();
    await tick();
    check("卡片被塞进 #pm-slot", env.slot.children.length === 1);
    check("预设 6 个（正计时 / 45+10×3 / 60+15×2 / 90 / 120 / 180）",
      BYATTR["data-preset"] && BYATTR["data-preset"].length === 6,
      BYATTR["data-preset"] && BYATTR["data-preset"].map(b => b.dataset.preset).join(","));
    check("默认读数是 45:00（正计时排在 chip 第一位，但不当默认）",
      txt("pm-time") === "45:00", txt("pm-time"));
    check("默认按钮是「开始」", txt("pm-toggle").indexOf("开始") >= 0, txt("pm-toggle"));
    check("45+10×3 共 6 段 / 165 分钟", txt("pm-plan").indexOf("165") >= 0 || txt("pm-plan").indexOf("2 小时 45") >= 0, txt("pm-plan"));
    check("轮播图没配时不铺背景", hostOf() && !hostOf()._cls.has("pm-hasbg"));
    check("一轮都没开始时不抢页面标题", env.doc.title === "改造我们的学习", env.doc.title);
    check("「结束」按钮在待开始时不出现", REG["pm-stop"].hidden === true);
  }

  console.log("\n[2] 开始 / 暂停 / 继续（墙钟语义）");
  {
    const env = boot(); await tick();
    REG["pm-toggle"].click();
    check("点开始 → 按钮变暂停", txt("pm-toggle").indexOf("暂停") >= 0, txt("pm-toggle"));
    check("点开始 → 卡片进专注态", hostOf()._cls.has("pm-brk") === false);
    advance(10 * MIN);
    check("走 10 分钟 → 剩 35:00", txt("pm-time") === "35:00", txt("pm-time"));
    check("标题带上倒计时", env.doc.title.indexOf("▶") >= 0, env.doc.title);
    REG["pm-toggle"].click();
    const frozen = txt("pm-time");
    check("暂停 → 按钮变继续", txt("pm-toggle").indexOf("继续") >= 0, txt("pm-toggle"));
    advance(9 * MIN);
    check("暂停期间读数冻住", txt("pm-time") === frozen, txt("pm-time") + " vs " + frozen);
    check("暂停时标题是 ⏸", env.doc.title.indexOf("⏸") >= 0, env.doc.title);
    REG["pm-toggle"].click();
    advance(MIN);
    check("继续 → 从冻住的那一秒接着走", txt("pm-time") === "34:00", txt("pm-time"));
    advance(34 * MIN);
    check("45 分钟走完 → 进休息段 10:00", txt("pm-time") === "10:00", txt("pm-time"));
    check("休息段卡片是 brk 态", hostOf()._cls.has("pm-brk"));
    check("专注段走完记一个番茄 · 45 分钟", txt("pm-day").indexOf("<b>1</b> 个番茄 · 专注 <b>45 分</b>") >= 0, txt("pm-day"));
  }

  console.log("\n[3] 跳过：已专注的分钟数照记，但不计番茄");
  {
    const env = boot(); await tick();
    REG["pm-toggle"].click(); advance(MIN);
    REG["pm-skip"].click();
    await tick();
    check("跳过专注段 → 进入休息段", txt("pm-time") === "10:00", txt("pm-time"));
    check("跳过不计番茄", txt("pm-day").indexOf("<b>0</b> 个番茄") >= 0, txt("pm-day"));
    check("跳过把已专注的 1 分钟记进今日", txt("pm-day").indexOf("专注 <b>1 分</b>") >= 0, txt("pm-day"));
    const cred = env.fetched.filter(f => f.url.indexOf("/api/pomodoro/credit") >= 0).pop();
    check("记账带上分钟数、并显式声明不计番茄",
      cred && cred.body.min === 1 && cred.body.pomos === 0, JSON.stringify(cred && cred.body));
    REG["pm-skip"].click();
    await tick();
    check("再跳过休息 → 回到第 2 轮专注 45:00", txt("pm-time") === "45:00", txt("pm-time"));
    check("轮次点显示到第 2 轮", txt("pm-phase").indexOf("2/3") >= 0, txt("pm-phase"));
    check("休息段不进成绩（还是那 1 分钟）", txt("pm-day").indexOf("专注 <b>1 分</b>") >= 0, txt("pm-day"));
    check("休息段跳过不发记账请求（只发过专注段那一次）",
      env.fetched.filter(f => f.url.indexOf("/api/pomodoro/credit") >= 0).length === 1,
      String(env.fetched.filter(f => f.url.indexOf("/api/pomodoro/credit") >= 0).length));
  }

  console.log("\n[4] 关掉页面期间补齐（刷新不丢表）");
  {
    // 快照：正在跑 45 分钟的专注段，endAt 在 43 分钟处，而「现在」已经比它晚 2 分钟
    const T0 = 1770000000000;
    const run = {
      plan: { id: "45x3", label: "45 + 10 × 3", work: 45, brk: 10, rounds: 3, note: "" },
      pos: 0, running: true, startedOnce: true, endAt: T0 - 2 * MIN, remain: 0,
    };
    const env = boot(null, run, { date: null, pomos: 0, min: 0 });
    await tick();
    // 专注段在 T0-2min 那一刻就走完了，休息段从那一刻起算 10 分钟 → 现在只剩 8:00。
    // 若按「发现超时的那一刻」续表（老写法），这里会读到 10:00：休息被凭空推后 2 分钟，
    // 后台被节流时每段都这么推，整轮就越拖越远——这正是「后台不计时」的观感来源。
    check("补齐后休息段按专注段末尾顺接，只剩 08:00", txt("pm-time") === "08:00", txt("pm-time"));
    check("走完的专注段照记成绩", txt("pm-day").indexOf("<b>1</b> 个番茄 · 专注 <b>45 分</b>") >= 0, txt("pm-day"));
    check("补齐后仍在跑（休息段自动开始）", txt("pm-toggle").indexOf("暂停") >= 0, txt("pm-toggle"));
  }

  console.log("\n[5] 全屏是搬家不是复制");
  {
    const env = boot({ images: ["src/assets/pomo/a.png", "src/assets/pomo/b.jpg"], interval: 20, dim: 0.5, show: "full" });
    await tick();
    const h = hostOf();
    check("非全屏时不铺轮播背景（show=full）", !h._cls.has("pm-hasbg"));
    REG["pm-full"].click();
    check("点全屏 → 卡片搬进 #pm-overlay", h.parent === env.overlay);
    check("搬走后 #pm-slot 空了", env.slot.children.length === 0);
    check("overlay 不再 hidden", env.overlay.hidden === false);
    check("卡片带 pm-fs 类", h._cls.has("pm-fs"));
    check("show=full 在全屏下铺背景", h._cls.has("pm-hasbg"));
    check("遮罩浓度按设置走", h.style._vars["--pm-dim"] === "0.5", JSON.stringify(h.style._vars));
    await tick();
    const layers = CLASSES["pm-bg"] || [];
    check("两层轮播都在", layers.length === 2, String(layers.length));
    check("第一张图已淡入", layers.some(l => l.style.backgroundImage.indexOf("a.png") >= 0 && l._cls.has("on")),
      layers.map(l => l.style.backgroundImage + "|" + l.className).join(" / "));
    advance(20000 + 100); await tick();
    check("20 秒后换到第二张", layers.some(l => l.style.backgroundImage.indexOf("b.jpg") >= 0),
      layers.map(l => l.style.backgroundImage).join(" / "));
    REG["pm-full"].click();
    check("退出全屏 → 搬回 #pm-slot", h.parent === env.slot && !h._cls.has("pm-fs"));
    check("回到卡片态 → show=full 不再铺图", !h._cls.has("pm-hasbg"));
  }

  console.log("\n[6] 提示音与设置联动");
  {
    const env = boot({ images: [], interval: 30, dim: 0.2, show: "off", sound: "off" });
    await tick();
    check("服务端说静音就静音（按钮回显）", txt("pm-sound").indexOf("🔕") >= 0, txt("pm-sound"));
    REG["pm-sound"].click();
    check("本地立刻切回有声", txt("pm-sound").indexOf("🔔") >= 0, txt("pm-sound"));
    const post = env.fetched.filter(f => f.url.indexOf("/api/settings") >= 0 && f.body).pop();
    check("切换写回服务端 pomo_sound", post && post.body.pomo_sound === "on", JSON.stringify(post && post.body));
    check("show=off 时不铺背景", !hostOf()._cls.has("pm-hasbg"));
    check("暴露 __pomoReload 给设置页", typeof global.__pomoReload === "function");
  }

  console.log("\n[7] 90 分钟预设不该塞收尾休息");
  {
    const env = boot(); await tick();
    const chips = BYATTR["data-preset"] || [];
    const chip = chips.find(b => b.dataset.preset === "90");
    chip.click();
    check("选 90 分钟 → 读数 1:30:00", txt("pm-time") === "1:30:00", txt("pm-time"));
    check("90 分钟没有休息段", txt("pm-next").indexOf("之后就收工") >= 0 || txt("pm-next").indexOf("第一段") >= 0, txt("pm-next"));
    REG["pm-toggle"].click();              // 换预设会停表，得再点一次开始才走表
    advance(90 * MIN + 1000);
    check("走完 → 完成态而不是再冒出一段休息", hostOf()._cls.has("pm-done"), hostOf().className);
    check("完成态读数 00:00", txt("pm-time") === "00:00", txt("pm-time"));
    check("完成时记上一个 90 分钟番茄", txt("pm-day").indexOf("1 小时 30 分") >= 0, txt("pm-day"));
  }

  console.log("\n[8] 重置与结束（别抢标题；结束记已专注的分钟数）");
  {
    const env = boot(); await tick();
    REG["pm-toggle"].click(); advance(5 * MIN);
    REG["pm-reset"].click();
    check("重置 → 回到第 1 段 45:00", txt("pm-time") === "45:00", txt("pm-time"));
    check("重置 → 按钮回到「▶ 开始」而不是「继续」", txt("pm-toggle") === "▶ 开始", txt("pm-toggle"));
    check("重置 → 标题交还给页面", env.doc.title === "改造我们的学习", env.doc.title);
    check("重置不记成绩（那是「重来」，不是「结束」）",
      txt("pm-day").indexOf("专注 <b>0 分</b>") >= 0, txt("pm-day"));
    REG["pm-toggle"].click(); advance(MIN);
    REG["pm-stop"].click();
    await tick();
    check("中途结束不写「完成」标题（那是放弃不是跑完）", env.doc.title === "改造我们的学习", env.doc.title);
    check("中途结束不计番茄", txt("pm-day").indexOf("<b>0</b> 个番茄") >= 0, txt("pm-day"));
    check("中途结束把已专注的 1 分钟记进今日", txt("pm-day").indexOf("专注 <b>1 分</b>") >= 0, txt("pm-day"));
    // 结束后要把「这一轮完了」推给服务端：不推的话别的设备还看到这一段在跑，
    // 到点会把同一段又记一遍（提前结束已经记过已专注的那部分了）。
    const last = env.fetched.filter(f => f.url.indexOf("/api/pomodoro/state") >= 0 && f.method === "POST").pop();
    check("结束会把「没有下一段、没在跑」推给服务端",
      last && last.body.run.running === false && last.body.run.pos === 6,
      JSON.stringify(last && last.body.run));
  }

  console.log("\n[9] 跨设备同步（服务端是唯一真相源）");
  {
    // 场景：电脑上开着 45+10×3 的第 1 段，已经跑了 25 分钟；平板这时打开页面。
    // 服务端时钟比平板快 7 秒——end_at 必须按服务端时钟存，平板才能算出对的时间。
    const T0 = 1770000000000;
    const srv = {
      nowOffset: 7000,
      nextUpdated: 90001,
      run: {
        plan: { id: "45x3", work: 45, brk: 10, rounds: 3, label: "45 + 10 × 3" },
        pos: 0, running: true, started_once: true,
        end_at: T0 + 20 * MIN + 7000,      // 服务端时钟：还剩 20 分钟
        remain_ms: 0, server_updated: 90001,
      },
      today_stat: { pomos: 2, min: 80 },
      days: [{ date: localToday(), pomos: 2, min: 80 }],
      after_credit: { pomos: 3, min: 125 },
    };
    const env = boot(null, null, null, srv);
    await tick(); await tick();
    check("平板认出了电脑那边正在跑的表", txt("pm-toggle").indexOf("暂停") >= 0, txt("pm-toggle"));
    check("按服务端时钟换算出剩余 20:00（skew 生效）", txt("pm-time") === "20:00", txt("pm-time"));
    check("今日成绩也用服务端的", txt("pm-day").indexOf("<b>2</b> 个番茄") >= 0, txt("pm-day"));
    check("读的是同一份 45+10×3", txt("pm-plan").indexOf("45 + 10 × 3") >= 0, txt("pm-plan"));

    // 平板暂停：写回服务端，end_at 归零、剩余量以毫秒存（不带时钟）
    REG["pm-toggle"].click();
    await tick(); await tick();
    const post = env.fetched.filter(f => f.url.indexOf("/api/pomodoro/state") >= 0 && f.method === "POST").pop();
    check("暂停动作写回了服务端", !!post, JSON.stringify(env.fetched.map(f => f.method + " " + f.url)));
    check("写回的是暂停态", post && post.body.run.running === false, JSON.stringify(post && post.body));
    check("暂停态存的是剩余毫秒（20 分钟）", post && post.body.run.remain_ms === 20 * MIN,
      JSON.stringify(post && post.body.run));
    check("暂停态 end_at 归零", post && post.body.run.end_at === 0);

    // 平板继续跑：end_at 必须换算回**服务端时钟**再存，否则电脑那边会少 7 秒
    REG["pm-toggle"].click();
    await tick(); await tick();
    const post2 = env.fetched.filter(f => f.url.indexOf("/api/pomodoro/state") >= 0 && f.method === "POST").pop();
    check("继续时 end_at 用服务端时钟写回（+7s skew）",
      post2 && post2.body.run.end_at === fakeNow + 20 * MIN + 7000,
      JSON.stringify({ got: post2 && post2.body.run.end_at, want: fakeNow + 20 * MIN + 7000, skew: 7000 }));

    // 跑完这一段 → 记一个番茄，走服务端自增（两端各记一次不会互相覆盖）
    advance(20 * MIN + 1000);
    await tick(); await tick();
    const cred = env.fetched.filter(f => f.url.indexOf("/api/pomodoro/credit") >= 0).pop();
    check("跑完一段会向服务端记一个番茄", !!cred, JSON.stringify(env.fetched.map(f => f.url)));
    check("记账带上这一段的分量（满额，番茄 +1）",
      cred && cred.body.min === 45 && cred.body.pomos === 1, JSON.stringify(cred && cred.body));
    check("服务端回的成绩覆盖了本地估算", txt("pm-day").indexOf("<b>3</b> 个番茄") >= 0, txt("pm-day"));
  }

  console.log("\n[9b] 服务端还没有这只表时，本机手上那轮要交上去");
  {
    // 场景：刚升级上来，本机 localStorage 里还跑着一轮，服务端 pomo_run 为空。
    // 不交上去的话，平板永远看不到这一轮（用户实测过的那个现象）。
    const env = boot(null, {
      plan: { id: "60x2", work: 60, brk: 15, rounds: 2, label: "60 + 15 × 2" },
      pos: 0, running: true, startedOnce: true, endAt: 1770000000000 + 40 * MIN, remain: 0,
    }, null, { run: null, nowOffset: 0, nextUpdated: 5001 });
    await tick(); await tick(); await tick();
    const pushes = env.fetched.filter(f => f.url.indexOf("/api/pomodoro/state") >= 0 && f.method === "POST");
    check("服务端为空时把本机正在跑的表交上去", pushes.length >= 1, JSON.stringify(env.fetched.map(f => f.method + " " + f.url)));
    check("交上去的确实是运行态", pushes.length > 0 && pushes[0].body.run.running === true,
      JSON.stringify(pushes[0] && pushes[0].body.run));
    check("本机照常走表（还剩 40 分钟）", txt("pm-time") === "40:00", txt("pm-time"));
  }

  console.log("\n[10] 老服务端 / 没有 server_now 时不能把时钟算坏");
  {
    // 桩里不返回 server_now：skew 必须保持 0，不能变成 -Date.now() 那种天文数字
    const env = boot(null, {
      plan: { id: "90", work: 90, brk: 0, rounds: 1, label: "90 分钟" },
      pos: 0, running: false, startedOnce: false, endAt: 0, remain: 30 * MIN,
    });
    await tick(); await tick();
    check("没有 server_now 时读数照常", txt("pm-time") === "30:00", txt("pm-time"));
    REG["pm-toggle"].click();
    advance(MIN);
    check("计时照常走（时钟没被 -Date.now() 带飞）", txt("pm-time") === "29:00", txt("pm-time"));
  }

  console.log("\n[11] 浮动小窗：拖动、透明度、与主表联动");
  {
    // 小窗的字段与按钮用 data-f / data-act 标记（页面内小窗与 PiP 窗口共用同一份结构）
    const field = n => { const e = (BYATTR["data-f"] || []).find(x => x.dataset.f === n); return e ? String(e.textContent || "") : "<缺失>"; };
    const btn = a => (BYATTR["data-act"] || []).find(x => x.dataset.act === a) || null;
    const miniOf = () => (CLASSES["pm-mini"] || [])[0] || null;

    const env = boot();
    await tick();
    const mini = miniOf();
    check("小窗节点已建出来（默认收起）", !!mini && mini.hidden === true);
    REG["pm-toggle"].click();
    await tick();
    check("开始后小窗自动浮出来", mini.hidden === false);
    check("小窗与主表读数一致", field("time") === txt("pm-time"), field("time") + " vs " + txt("pm-time"));
    check("小窗写着第几轮", field("phase").indexOf("第 1/3 轮") >= 0, field("phase"));

    // 小窗上的播放/暂停和主表是同一套状态机
    btn("toggle").click();
    await tick();
    check("在小窗上点暂停 → 主表也暂停", txt("pm-toggle").indexOf("继续") >= 0, txt("pm-toggle"));
    check("小窗进入暂停态", mini._cls.has("pm-pause"));
    check("小窗的暂停图标也换了", field("phase").indexOf("暂停") >= 0, field("phase"));
    btn("toggle").click();
    await tick();
    check("再点一下继续", txt("pm-toggle").indexOf("暂停") >= 0, txt("pm-toggle"));

    // 透明度：滑杆立刻生效并落盘
    const op = btn("op");
    op.value = "50"; op.oninput();
    check("透明度写进 CSS 变量", mini.style._vars["--pm-mini-op"] === "0.5", JSON.stringify(mini.style._vars));
    check("滑杆旁显示百分比", field("opv") === "50%", field("opv"));
    check("透明度落盘（按设备记）", JSON.parse(env.store["kaoyan.pomo.mini.v1"] || "{}").op === 0.5,
      env.store["kaoyan.pomo.mini.v1"]);
    op.value = "10"; op.oninput();     // 低于下限要被夹到 MINI_OP_MIN（12%）
    check("透明度有下限（不会调到彻底看不见）", mini.style._vars["--pm-mini-op"] === "0.12",
      mini.style._vars["--pm-mini-op"]);
    // 这两条只能查源码：极简 DOM 桩没有 CSS 引擎，量不出「滑杆在 PiP 里显不显示」
    check("滑杆下限放到 12%（比原来的 25% 更透）", /data-act="op" min="12"/.test(js));
    check("PiP 里也不再藏着透明度滑杆",
      js.indexOf(".pm-mini-op{display:none}") < 0 && /\.pm-mini-op\{display:flex/.test(js));

    // 拖动 + 边界夹紧
    const head = (CLASSES["pm-mini-head"] || [])[0];
    head._fire("pointerdown", { clientX: 120, clientY: 120, button: 0, pointerId: 1, preventDefault() {} });
    head._fire("pointermove", { clientX: 9999, clientY: 9999 });
    check("拖到屏幕外会被夹回视口内", mini.style.left === "202px" && mini.style.top === "512px",
      mini.style.left + "," + mini.style.top);
    head._fire("pointermove", { clientX: -500, clientY: -500 });
    check("往左上拖同样有下限", mini.style.left === "6px" && mini.style.top === "6px",
      mini.style.left + "," + mini.style.top);
    head._fire("pointerup", {});
    check("松手后位置落盘", JSON.parse(env.store["kaoyan.pomo.mini.v1"] || "{}").x === 6,
      env.store["kaoyan.pomo.mini.v1"]);

    // 番茄钟本体全屏时，小窗收起来（同一个表不用看两遍）
    REG["pm-full"].click();
    await tick();
    check("番茄钟全屏时小窗自动收起", mini.hidden === true);
    REG["pm-full"].click();
    await tick();
    check("退出全屏后小窗回来", mini.hidden === false);

    // 收起按钮：隐藏 + 落盘，刷新后不再自动冒出来
    btn("close").click();
    await tick();
    check("点 ✕ 收起小窗", mini.hidden === true);
    check("收起状态落盘", JSON.parse(env.store["kaoyan.pomo.mini.v1"] || "{}").on === false,
      env.store["kaoyan.pomo.mini.v1"]);
    REG["pm-mini-btn"].click();
    await tick();
    check("卡片上的「小窗」按钮能再打开", mini.hidden === false);
  }

  console.log("\n[12] 系统小窗（Document PiP，桌面版 Chromium 才有）");
  {
    const noPip = boot();
    await tick();
    check("浏览器不支持时按钮藏起来", noPip && REG["pm-pip"] && REG["pm-pip"].hidden === true);

    const env = boot(null, null, null, null, { pip: true });
    await tick();
    check("支持时按钮露出来", REG["pm-pip"].hidden === false);
    REG["pm-pip"].click();
    await tick(); await tick();
    // 196 高：比原来多留一条给透明度滑杆（PiP 里也放出来了）
    check("点了会申请一个 208×196 的 PiP 窗口",
      pipCalls.length === 1 && pipCalls[0].width === 208 && pipCalls[0].height === 196,
      JSON.stringify(pipCalls));
    check("PiP 里注入了小窗结构", (CLASSES["pm-mini-pip"] || []).length === 1);
    const pipBody = (CLASSES["pm-mini-pip"] || [])[0];
    check("PiP 里带着时间/进度/按钮", !!pipBody && /pm-mini-time/.test(pipBody.parent ? pipBody.parent._html || "" : ""),
      "（结构由父节点 innerHTML 提供）");
  }

  console.log("\n[13] 锁屏 / 通知栏（Media Session）");
  {
    const noMedia = boot();
    await tick();
    check("浏览器不支持时「锁屏」按钮藏起来", noMedia && REG["pm-media"] && REG["pm-media"].hidden === true);

    const env = boot(null, null, null, null, { media: true });
    await tick();
    check("支持时按钮露出来", REG["pm-media"].hidden === false);
    REG["pm-toggle"].click();
    await tick();
    REG["pm-media"].click();
    await tick();
    check("开启后会起播静音音轨（锁屏卡片靠它挂住）", audioPlays >= 1, "plays=" + audioPlays);
    check("写入了锁屏元信息", mediaSession.metadata && mediaSession.metadata !== "unset",
      JSON.stringify(mediaSession.metadata));
    check("锁屏标题带剩余时间与阶段",
      mediaSession.metadata.title.indexOf("专注") >= 0 && /\d\d:\d\d/.test(mediaSession.metadata.title),
      mediaSession.metadata.title);
    check("锁屏副标题带第几轮", mediaSession.metadata.artist.indexOf("1/3") >= 0, mediaSession.metadata.artist);
    check("上报了播放状态", mediaSession.playbackState === "playing", mediaSession.playbackState);
    check("进度条按本段总时长上报",
      mediaSession.position && mediaSession.position.duration === 45 * 60, JSON.stringify(mediaSession.position));
    check("注册了锁屏的播放/暂停/跳过键",
      typeof mediaSession.handlers.pause === "function" && typeof mediaSession.handlers.play === "function"
      && typeof mediaSession.handlers.nexttrack === "function", Object.keys(mediaSession.handlers).join(","));

    // 锁屏上的暂停键要真的能控制这只表
    mediaSession.handlers.pause();
    await tick();
    check("锁屏暂停 → 主表暂停", txt("pm-toggle").indexOf("继续") >= 0, txt("pm-toggle"));
    check("暂停时上报 paused", mediaSession.playbackState === "paused", mediaSession.playbackState);
    mediaSession.handlers.play();
    await tick();
    check("锁屏播放 → 主表继续", txt("pm-toggle").indexOf("暂停") >= 0, txt("pm-toggle"));
    mediaSession.handlers.nexttrack();
    await tick();
    check("锁屏下一曲键 → 跳过这一段（进休息段）", txt("pm-time") === "10:00", txt("pm-time"));

    REG["pm-media"].click();
    await tick();
    check("关掉后停掉音轨并清空元信息", audioPauses >= 1 && mediaSession.metadata === null,
      "pauses=" + audioPauses + " meta=" + JSON.stringify(mediaSession.metadata));
    check("关闭状态落盘", env.store["kaoyan.pomo.media"] === "0", env.store["kaoyan.pomo.media"]);
  }

  console.log("\n[14] 小窗的 ✕ 与拖窗互不干扰（用户实测「叉不起作用」的那个 bug）");
  {
    const env = boot();
    await tick();
    REG["pm-toggle"].click();
    await tick();
    const mini = (CLASSES["pm-mini"] || [])[0];
    const head = (CLASSES["pm-mini-head"] || [])[0];
    const closer = (BYATTR["data-act"] || []).find(x => x.dataset.act === "close");

    // 先正常拖到一个位置：阈值内不动、超过阈值才动
    const x0 = mini.style.left;
    head._fire("pointerdown", { target: head, clientX: 100, clientY: 100, button: 0, pointerId: 1 });
    head._fire("pointermove", { target: head, clientX: 102, clientY: 101, pointerId: 1 });
    check("手抖 2~3px 不算拖动（阈值生效）", mini.style.left === x0, x0 + " → " + mini.style.left);
    head._fire("pointermove", { target: head, clientX: 120, clientY: 130, pointerId: 1 });
    head._fire("pointerup", { target: head, pointerId: 1 });
    const placed = mini.style.left;
    check("真的拖动才移动", placed !== x0 && placed === "120px", placed);

    // 真实点击序列：pointerdown 落在 ✕ 上 —— 这一下不许被当成开始拖窗
    head._fire("pointerdown", { target: closer, clientX: 200, clientY: 200, button: 0, pointerId: 2 });
    head._fire("pointermove", { target: closer, clientX: 320, clientY: 320, pointerId: 2 });
    check("按在 ✕ 上不进入拖拽（窗口没被拖着跑）", mini.style.left === placed,
      placed + " → " + mini.style.left);
    head._fire("pointerup", { target: closer, pointerId: 2 });
    closer.click();
    check("点 ✕ 真的收起来了", mini.hidden === true, "hidden=" + mini.hidden);
    check("收起状态落盘", JSON.parse(env.store["kaoyan.pomo.mini.v1"] || "{}").on === false,
      env.store["kaoyan.pomo.mini.v1"]);

    // 收起后拖拽不应再动（把手已经不可见）
    REG["pm-mini-btn"].click();
    await tick();
    check("再打开小窗又回来了", mini.hidden === false);
  }

  console.log("\n[15] 独立小窗：开了收页内、关了页内回来");
  {
    const env = boot(null, null, null, null, { pip: true });
    await tick();
    REG["pm-toggle"].click();
    await tick();
    const mini = (CLASSES["pm-mini"] || [])[0];
    check("页内小窗先浮着", mini.hidden === false);
    check("页内小窗上给了「跳出浏览器」的入口", ((BYATTR["data-act"] || []).find(x => x.dataset.act === "pip") || {}).hidden === false);
    REG["pm-pip"].click();
    await tick(); await tick();
    check("开独立小窗后页内小窗收起（两份同样的表只会打架）", mini.hidden === true,
      "hidden=" + mini.hidden + " pipCalls=" + pipCalls.length
      + " timeFields=" + (BYATTR["data-f"] || []).length
      + " pipCls=" + REG["pm-pip"].className);
    check("独立小窗按钮进入开启态", REG["pm-pip"]._cls.has("is-off") === false, REG["pm-pip"].className);
    REG["pm-pip"].click();
    await tick();
    check("再点一下把独立小窗关掉", lastPipWin && lastPipWin.closed === true);
    check("关掉后页内小窗自己回来", mini.hidden === false, "hidden=" + mini.hidden);

    // 不支持独立小窗时（手机/平板）：按钮藏起来，点按入口也不给假希望
    const env2 = boot();
    await tick();
    REG["pm-toggle"].click();
    await tick();
    check("不支持的浏览器上按钮是藏着的", REG["pm-pip"].hidden === true);
    check("页内小窗里也不给「跳出浏览器」按钮",
      ((BYATTR["data-act"] || []).find(x => x.dataset.act === "pip") || {}).hidden === true);
  }

  console.log("\n[16] 自己存预设（＋ 存为预设 / 删掉）");
  {
    const env = boot(); await tick();
    check("一开始只有 6 个内置预设", (BYATTR["data-preset"] || []).length === 6,
      String((BYATTR["data-preset"] || []).length));
    check("内置那六个没有删除尾巴", (BYATTR["data-del"] || []).length === 0,
      String((BYATTR["data-del"] || []).length));

    // 存一组：50 分专注 + 8 分歇 × 2 轮
    REG["pm-cwork"].value = "50";
    REG["pm-cbrk"].value = "8";
    REG["pm-crounds"].value = "2";
    REG["pm-csave"].click();
    await tick();
    const chips = BYATTR["data-preset"] || [];
    check("存完多出一个 chip", chips.length === 7, String(chips.length));
    const mine = chips.find(b => b.dataset.preset === "my:50x8x2");
    check("id 由参数拼出来（同样数字天然去重）", !!mine,
      chips.map(b => b.dataset.preset).join(","));
    // chip 的文字读不出来：parseInto 只认标签和属性，建出来的桩节点不带文本。
    // 所以查渲染出来的整段 innerHTML（REG["pm-presets"] 就是 #pm-presets）。
    check("文案跟内置一个长法：50 + 8 × 2",
      /50 \+ 8 × 2/.test(REG["pm-presets"]._html),
      REG["pm-presets"]._html.slice(0, 220));
    check("它带着删除尾巴", (BYATTR["data-del"] || []).some(b => b.dataset.del === "my:50x8x2"));
    check("落盘了", JSON.parse(env.store["kaoyan.pomo.presets.v1"] || "[]").length === 1,
      env.store["kaoyan.pomo.presets.v1"]);
    check("存预设不顺手把正在跑的那轮换掉（还是 45+10×3）",
      txt("pm-plan").indexOf("2 小时 45") >= 0, txt("pm-plan"));

    mine.click();
    await tick();
    check("点自己存的 chip 能切过去（50×2 + 8×2 = 1 小时 56 分）",
      txt("pm-plan").indexOf("1 小时 56") >= 0, txt("pm-plan"));

    REG["pm-csave"].click();
    await tick();
    check("同样的数字存第二次不多出一个 chip", (BYATTR["data-preset"] || []).length === 7,
      String((BYATTR["data-preset"] || []).length));

    // ⚠️ 删掉的正是在用的那个：表不能被重置（那等于把用户当前这一轮清掉了）
    (BYATTR["data-del"] || []).find(b => b.dataset.del === "my:50x8x2").click();
    await tick();
    check("删掉后 chip 没了", (BYATTR["data-preset"] || []).length === 6,
      String((BYATTR["data-preset"] || []).length));
    check("落盘也清了", JSON.parse(env.store["kaoyan.pomo.presets.v1"] || "[]").length === 0);
    check("★ 删掉在用的预设不会把表重置（读数还是 50:00，不是内置的 45:00）",
      txt("pm-time") === "50:00", txt("pm-time"));
    check("它只是改回「自定义」不再挂任何 chip",
      txt("pm-plan").indexOf("自定义") >= 0, txt("pm-plan"));

    // 刷新后还在
    const env2 = boot(null, null, null, null, null, {
      "kaoyan.pomo.presets.v1": JSON.stringify([
        { id: "my:30x5x4", label: "30 + 5 × 4", work: 30, brk: 5, rounds: 4, note: "x" }]),
    });
    await tick();
    check("刷新后自己存的预设还在", (BYATTR["data-preset"] || []).length === 7,
      String((BYATTR["data-preset"] || []).length));
    check("也带着删除尾巴", (BYATTR["data-del"] || []).length === 1);

    // localStorage 里的东西不可信（手改过 / 旧版本写的）
    const env3 = boot(null, null, null, null, null, {
      "kaoyan.pomo.presets.v1": JSON.stringify([
        { id: "evil", work: 30 }, { id: "my:0x0x1", work: 0 },
        { id: "my:25x5x2", label: "25 + 5 × 2", work: 25, brk: 5, rounds: 2 }]),
    });
    await tick();
    check("坏数据被挡掉：不是 my: 前缀的、work<=0 的",
      (BYATTR["data-preset"] || []).length === 7
      && (BYATTR["data-preset"] || []).some(b => b.dataset.preset === "my:25x5x2"),
      (BYATTR["data-preset"] || []).map(b => b.dataset.preset).join(","));
  }

  console.log("\n[17] 轮播图上的字自动配色（换图 → 量亮度 → 换字色）");
  {
    // 几种「照片」和它们的理论值（dim=.35，遮罩取渐变中档 .4，青墨 21,26,26）：
    //   暗图 (40,45,50)     → 叠完 ≈ .14，白字本来就看得清，什么都不用改
    //   亮图 (235,240,245)  → 叠完 ≈ .60，白字糊 → 整张换深字
    //   上亮下暗            → 平均 ≈ .37（够不到 .42 那条线，说明还有暗处），
    //                         但亮部仍是 .60 → 换深字则下半张糊、留白字则上半张糊
    //                         → 只能自动补一档遮罩
    const solid = (r, g, b) => ({ w: 1600, h: 900, cw: 1000, ch: 300, px: () => [r, g, b] });
    const mixed = () => ({ w: 1600, h: 900, cw: 1000, ch: 300,
      px: (u, v) => (v < 0.5 ? [235, 240, 245] : [40, 45, 50]) });
    const cfg = { images: ["src/assets/pomo/a.png"], interval: 20, dim: 0.35, show: "both" };
    const scrim = h => Number(h.style._vars["--pm-scrim"]);
    // ⚠️ 要两拍：配置是 fetch 回来的（微任务链），图 onload 又是个 setTimeout，
    //    排在它后面的一拍才轮到。只 await 一次 tick() 时量到的还是配置没到位的状态
    //    ——测出来「字色没变」，看着像功能坏了，其实是没等够。
    const settle = async () => { await tick(); await tick(); };

    {
      boot(cfg, null, null, null, null, null, solid(40, 45, 50));
      await settle();
      const h = hostOf();
      check("暗图：不套深字", !h._cls.has("pm-on-lit"));
      check("暗图：也不补遮罩", scrim(h) === 0, h.style._vars["--pm-scrim"]);
    }
    {
      boot(cfg, null, null, null, null, null, solid(235, 240, 245));
      await settle();
      const h = hostOf();
      check("亮图：整张换成深字", h._cls.has("pm-on-lit"));
      check("亮图：换深字就够了，不额外压遮罩", scrim(h) === 0, h.style._vars["--pm-scrim"]);
    }
    {
      boot(cfg, null, null, null, null, null, mixed());
      await settle();
      const h = hostOf();
      check("半亮半暗：不套深字（换深字暗处会糊）", !h._cls.has("pm-on-lit"));
      check("半亮半暗：自动补一档遮罩", scrim(h) > 0.2, h.style._vars["--pm-scrim"]);
      check("补的量封顶（不把照片压成灰板）", scrim(h) <= 0.45, h.style._vars["--pm-scrim"]);
      // 反推：补完之后亮部应该刚好落回「白字还算稳」的那条线以下。
      // 把 32×32 的取样点全带进来算太绕，这里只按理论值验一个量级。
      check("补的量是「够用就好」，不是往死里压", scrim(h) < 0.4, h.style._vars["--pm-scrim"]);

      // 卡片被藏在别的子页时量不到（clientWidth=0）→ 要保持上一次的判断，
      // 不能把字色清回默认闪一下
      visual = Object.assign({}, mixed(), { cw: 0, ch: 0 });
      advance(20000 + 100); await settle();
      check("卡片被藏着量不到时，维持上一次的判断", !h._cls.has("pm-on-lit") && scrim(h) > 0.2);

      // 轮播关掉 → 字色回到纯色卡那一套
      boot({ images: cfg.images, interval: 20, dim: 0.35, show: "off" },
        null, null, null, null, null, mixed());
      await settle();
      const h2 = hostOf();
      check("不用轮播时不留深字类", !h2._cls.has("pm-on-lit"));
      check("不用轮播时遮罩归零", scrim(h2) === 0, h2.style._vars["--pm-scrim"]);
    }
    visual = null;
  }

  console.log("\n[18] 后台（定时器被节流）仍按墙钟记账，且段序顺接上一段末尾");
  {
    // 用户反馈「后台好像不计时」。番茄钟走的是墙钟（endAt），记账不该受
    // 后台影响——切到别的标签页时浏览器会把 250ms 的心跳节流到约 1 分钟
    // 一次，这里用大 step 复现这种形态：每步只让心跳跑一次。
    //
    // 45+10×3 的时间轴：0~45 专注1，45~55 休息1，55~100 专注2，100~110 休息2，
    // 110~155 专注3。所以「后台 1 小时」醒来时应该已经在第 2 轮专注里、只剩 40 分钟。
    // 老写法把下一段的起点定在「发现超时的那一刻」，休息会被推后到第 60 分钟才开始
    // （读到 10:00），整轮越拖越远——那正是用户看到的「一组结束就卡在休息开头」。
    const env = boot(); await tick();
    REG["pm-toggle"].click();                       // 开始 45 分钟专注段
    advance(60 * MIN, 60 * MIN);
    check("后台 1 小时：走完的 45 分钟专注段照记一个番茄",
      txt("pm-day").indexOf("<b>1</b> 个番茄") >= 0, txt("pm-day"));
    check("段序顺接上一段末尾：1 小时时已在第 2 轮专注",
      txt("pm-phase").indexOf("专注") >= 0 && txt("pm-phase").indexOf("2/3") >= 0, txt("pm-phase"));
    check("没有把休息段推后（读数 40:00 而不是 10:00）", txt("pm-time") === "40:00", txt("pm-time"));

    advance(60 * MIN, 60 * MIN);                    // 累计 2 小时
    check("第 2 轮专注也照记（累计 2 个番茄）",
      txt("pm-day").indexOf("<b>2</b> 个番茄") >= 0, txt("pm-day"));
    check("2 小时时在第 3 轮专注、剩 35:00",
      txt("pm-phase").indexOf("3/3") >= 0 && txt("pm-time") === "35:00",
      txt("pm-phase") + " " + txt("pm-time"));

    advance(60 * MIN, 60 * MIN);                    // 累计 3 小时 → 整轮（155 分钟）走完
    check("整轮走完记 3 个番茄", txt("pm-day").indexOf("<b>3</b> 个番茄") >= 0, txt("pm-day"));
    check("走完进入完成态（不再凭空多出一段）", hostOf()._cls.has("pm-done"), hostOf().className);
    const mins = env.fetched.filter(f => f.url.indexOf("/api/pomodoro/credit") >= 0)
      .reduce((a, f) => a + ((f.body && f.body.min) || 0), 0);
    check("三小时里共记 135 分钟专注（三段 45）", mins === 135, String(mins));
  }

  console.log("\n[19] 正计时：不设结束时间，结束只记分钟、不计番茄");
  {
    const env = boot(); await tick();
    const upChip = (BYATTR["data-preset"] || []).find(b => b.dataset.preset === "up");
    check("预设里有正计时入口", !!upChip);
    upChip.click();
    await tick();
    check("切到正计时：读数从 00:00 起", txt("pm-time") === "00:00", txt("pm-time"));
    check("正计时卡片带 pm-up 类", hostOf()._cls.has("pm-up"), hostOf().className);
    check("正计时不摆「跳过」（没有下一段）", REG["pm-skip"].hidden === true);
    check("文案说明「不设结束时间」", txt("pm-plan").indexOf("不设结束时间") >= 0, txt("pm-plan"));

    REG["pm-toggle"].click();                        // 开始
    advance(37 * MIN);
    check("正计时表往上走：37 分钟读到 37:00", txt("pm-time") === "37:00", txt("pm-time"));
    check("不会自己到点收工（还在跑）", txt("pm-toggle").indexOf("暂停") >= 0, txt("pm-toggle"));
    advance(3 * 60 * MIN, 60 * MIN);                 // 再走 3 小时，越过名义目标的一大截
    check("越过名义目标仍继续往上走，不夹在目标上", txt("pm-time") === "3:37:00", txt("pm-time"));

    const st = env.fetched.filter(f => f.url.indexOf("/api/pomodoro/state") >= 0 && f.method === "POST").pop();
    check("写回服务端时带上 up 标记（另一台设备才知道这是正计时）",
      st && st.body.run.plan.up === true, JSON.stringify(st && st.body.run.plan));

    REG["pm-stop"].click();                          // 结束
    await tick();
    check("结束只记分钟、不计番茄", txt("pm-day").indexOf("<b>0</b> 个番茄") >= 0, txt("pm-day"));
    check("记入 217 分钟（3 小时 37 分）",
      txt("pm-day").indexOf("专注 <b>3 小时 37 分</b>") >= 0, txt("pm-day"));
    const cred = env.fetched.filter(f => f.url.indexOf("/api/pomodoro/credit") >= 0).pop();
    check("记账带分钟数并显式声明不计番茄",
      cred && cred.body.min === 217 && cred.body.pomos === 0, JSON.stringify(cred && cred.body));
    check("结束后回到完成态", hostOf()._cls.has("pm-done"), hostOf().className);
  }

  console.log("\n[20] 跨设备：服务端那份正计时表原样接回来（不被还原成 10 小时倒计时）");
  {
    // 正计时的 end_at 是「名义目标」：起点 + 600 分钟。这里造一份「已经走了 30 分钟」
    // 的远端快照 → end_at = 起点 + 570 分钟。
    const T0 = 1770000000000;
    const srv = {
      nowOffset: 0, nextUpdated: 90001,
      run: {
        plan: { id: "up", work: 600, brk: 0, rounds: 1, label: "⏱ 正计时", note: "", up: true },
        pos: 0, running: true, started_once: true,
        end_at: T0 + 570 * MIN, remain_ms: 0, server_updated: 90001,
      },
    };
    const env = boot(null, null, null, srv);
    await tick(); await tick();
    check("接回来的还是正计时（不是 10 小时倒计时）", hostOf()._cls.has("pm-up"), hostOf().className);
    check("读数按「已走多久」显示 30:00", txt("pm-time") === "30:00", txt("pm-time"));
    check("阶段写着正计时", txt("pm-phase").indexOf("正计时") >= 0, txt("pm-phase"));
    check("小窗文案也不套「第几轮」", (BYATTR["data-f"] || []).filter(x => x.dataset.f === "phase")
      .every(x => String(x.textContent).indexOf("轮") < 0),
      (BYATTR["data-f"] || []).filter(x => x.dataset.f === "phase").map(x => x.textContent).join("|"));
  }

  console.log("\n[21] 时间校准：往返中点补偿 + 状态那一行");
  {
    // 场景：服务端时钟比本机快 7 秒，且一次往返要 400ms（墙外授时源的量级）。
    // 桩把「服务端那一刻」放在往返中点、随后把本机时钟推完整个 RTT。
    // 不做中点补偿的话 skew 会算成 7000 - 200 = 6800。
    //
    // ⚠️ 这里必须用**服务端暂停态**起步，不能拿「服务端在跑」的表来验 skew：
    //    在跑的表读进来时 end_at 要减 skew、写回去时又加 skew，同一次往返里
    //    skew 自己抵消掉了（暂停/继续一圈写回的 end_at 与 skew 无关），
    //    于是 skew 错 200ms 也测不出来 —— 曾据此写出一个永远通过的假用例。
    //    暂停态不同：剩余时间来自服务端的 remain_ms（纯时长，不含任何时钟），
    //    点「继续」时本机拿自己的 now 起表、再 +skew 写回，skew 的误差无处可躲。
    const T0 = 1770000000000;
    const srv = {
      nowOffset: 7000, rtt: 400, nextUpdated: 90001,
      clock: { calibrated: true, source: "taobao", offset_ms: 416, rtt_ms: 361,
               synced_at: T0 - 3000, age_ms: 3000 },
      run: {
        plan: { id: "45x3", work: 45, brk: 10, rounds: 3, label: "45 + 10 × 3" },
        pos: 0, running: false, started_once: true,
        end_at: 0, remain_ms: 20 * MIN, server_updated: 90001,
      },
    };
    const env = boot(null, null, null, srv);
    await tick(); await tick();
    // 暂停态的读数是服务端给的 remain_ms，原样 20:00（不掺时钟）
    check("暂停态：读数就是服务端给的剩余 20:00", txt("pm-time") === "20:00", txt("pm-time"));

    const tResume = fakeNow;    // 「继续」那一刻的本机时刻
    REG["pm-toggle"].click();   // 继续
    await tick(); await tick();
    const post = env.fetched.filter(f => f.url.indexOf("/api/pomodoro/state") >= 0 && f.method === "POST").pop();
    // 真值：服务端此刻 = tResume + 7000（桩里服务端就快 7 秒），这一段应到
    // 「服务端此刻 + 剩余 20 分钟」结束。skew 若少了 rtt/2（7000→6800），
    // 写回的 end_at 就会短 200ms —— 别的设备读出来表就短 200ms。
    const wantEnd = (tResume + 7000) + 20 * MIN;
    check("写回的 end_at 对准服务端时钟 + 剩余（skew 没吃进 rtt/2）",
      post && post.body.run.end_at === wantEnd,
      JSON.stringify({ got: post && post.body.run.end_at, want: wantEnd }));

    check("时间基准那行写「外部标准时间」", txt("pm-clock").indexOf("外部标准时间") >= 0, txt("pm-clock"));
    check("带上授时源名", txt("pm-clock").indexOf("taobao") >= 0, txt("pm-clock"));
    check("带上电脑差多少（0.4 秒）", txt("pm-clock").indexOf("电脑慢 0.4 秒") >= 0, txt("pm-clock"));
    check("校准过就不染提醒色", !REG["pm-clock"]._cls.has("is-off"), cls("pm-clock"));
  }

  console.log("\n[22] 离线：退回电脑时钟，仍要有清楚的交代");
  {
    const env = boot(null, null, null, {
      nowOffset: 0,
      clock: { calibrated: false, source: "system", offset_ms: 0, synced_at: 0, age_ms: 0,
               last_error: "taobao:fetch failed" },
    });
    await tick(); await tick();
    check("离线时写明用电脑时钟", txt("pm-clock").indexOf("电脑时钟") >= 0, txt("pm-clock"));
    check("离线时说明未对表", txt("pm-clock").indexOf("未对表") >= 0, txt("pm-clock"));
    check("离线时染提醒色", REG["pm-clock"]._cls.has("is-off"), cls("pm-clock"));
    check("表照常走（离线不影响计时）", txt("pm-time") === "45:00", txt("pm-time"));

    // 点一下那一行 → 重新对时；桩回「已校准」
    REG["pm-clock"].click();
    await tick(); await tick();
    const req = env.fetched.filter(f => f.url.indexOf("/api/time") >= 0).pop();
    check("点那一行会去请求 /api/time", !!req && req.body && req.body.force === 1,
      JSON.stringify(req && req.body));
  }

  console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
  global.setInterval = realSetInterval; global.clearInterval = realClearInterval;
  delete global.getComputedStyle;
  process.exit(fail === 0 ? 0 : 1);
})();

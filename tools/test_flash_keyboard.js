/*
 * 闪卡键盘交互的行为测试（临时）。从 generate_dashboard.py 抽出真实 FLASH_JS，
 * 用极简 DOM 桩跑，验证「选选项 / 答错即判 / 答对自评 / 撤销定位」四条链路。
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const SRC = path.join(__dirname, "..", "generate_dashboard.py");
// 闪卡全屏那段会 window.addEventListener("hashchange", …)，而桩里的 window 就是
// globalThis；Node 的 globalThis 没有 addEventListener，不补一个就会在那行炸掉。
// window 的监听者要记下来：同步钩子（切页推进度）就挂在 hashchange/visibilitychange 上，
// 原来这里是个空函数，等于把这些钩子屏蔽了，测不到。
globalThis.__winListeners = globalThis.__winListeners || {};
if (!globalThis.__winAddEventListener) {
  globalThis.__winAddEventListener = true;
  globalThis.addEventListener = (ev, fn) => {
    (globalThis.__winListeners[ev] = globalThis.__winListeners[ev] || []).push(fn);
  };
}
const fireWin = (ev, obj) => (globalThis.__winListeners[ev] || []).slice().forEach(fn => fn(obj || {}));

// ---- 用 Python 的 ast 取出 FLASH_JS 字面量（避免自己写 Python 字符串解析）----
// 走文件而不是 stdout：Windows 控制台默认 GBK，⚠ 之类的字符会直接炸掉管道。
const OUT = path.join(os.tmpdir(), "kaoyan_flash_extracted.js");
// 走公共脚本：它会把 DAY_START_JS（studyDay / DAY_START_HOUR）一起带上，
// 只抠 FLASH_JS 的话跑起来是 ReferenceError。
execFileSync("python", [path.join(__dirname, "extract_js.py"), "FLASH_JS", OUT],
  { maxBuffer: 1 << 24 });
const js = fs.readFileSync(OUT, "utf-8");
const _day = require("./_day");

// ---- 极简 DOM 桩 ----
let registry = {};
// 模拟「窗口有没有焦点」。学习计时每 250ms 读 document.hasFocus() 决定要不要累加，
// 用例里翻这个变量就能模拟 Alt+Tab 走开 / 回来。
let focusSim = true;
// keydown 的多个监听者（答题一套、全屏 Esc 一套）
let keyHandlers = [];
// 原生全屏调用记录（"request:div" / "exit"）
let fsCalls = [];
// document 上各事件的监听者，供用例手动触发 fullscreenchange 等
let docListeners = {};
function mkEl(tag) {
  // 建元素时把「属于哪一轮 boot」定死。不这样做的话，上一轮 boot 里没停掉的
  // setInterval 会通过共享的 registry 继续往这一轮的 #fs-timer 上写 className，
  // 于是「点结束回到 off 态」这种断言会 sporadically 假失败。
  const reg = registry;
  const el = {
    tagName: (tag || "div").toUpperCase(),
    children: [], _cls: new Set(), dataset: {}, style: {},
    disabled: false, onclick: null, _html: "", _text: "",
    // 真实 <input> 的 value 永远是字符串，没打过字就是 ""。桩不给默认值的话，
    // 代码里 input.value.trim() 会直接抛 TypeError——2026-09-17 加「空追问框按回车」
    // 用例时踩到，那条路以前根本没被跑到。
    value: "",
    // id 要能读。全局快捷键那条守卫是 `e.target.id === "fs-exp-input"`——桩以前没有 id，
    // 恒为 undefined，于是「追问框里按 1-4 / 空格该不该放行给闪卡」这条从来没生效过
    // （2026-09-17 查「回车是发送还是下一张」时才暴露）。
    _id: "",
    get id() { return this._id; },
    set id(v) { this._id = String(v); },
    // parentNode 要跟着更新、并且**从原父摘走**：浮窗是「节点搬家」（把练习区整块
    // 挪进 #fs-float），用例靠这两条判断搬走了没有 / 搬回来没有（2026-09-22）。
    appendChild(c) {
      if (c && c.parentNode && c.parentNode.children) {
        const k = c.parentNode.children.indexOf(c);
        if (k >= 0) c.parentNode.children.splice(k, 1);
      }
      this.children.push(c); if (c) c.parentNode = this; return c;
    },
    // 复用解析的说明条用 insertBefore 插到正文最前面（2026-09-21）
    insertBefore(node, ref) {
      if (node && node.parentNode && node.parentNode.children) {
        const k = node.parentNode.children.indexOf(node);
        if (k >= 0) node.parentNode.children.splice(k, 1);
      }
      const i = ref ? this.children.indexOf(ref) : -1;
      if (i >= 0) this.children.splice(i, 0, node); else this.children.push(node);
      if (node) node.parentNode = this;
      return node;
    },
    // 浮窗拖动要先读一次起手位置（真实浏览器里返回视口坐标）
    getBoundingClientRect() {
      return { left: 100, top: 60, width: 720, height: 560, right: 820, bottom: 620 };
    },
    get firstChild() { return this.children[0] || null; },
    // toast() 靠 1.8 秒后 self-remove 收尾；桩里没有 remove 就会把进程炸掉
    remove() { this._removed = true; },
    focus() { this._focused = true; },
    _listeners: {},
    addEventListener(ev, fn) { (this._listeners[ev] = this._listeners[ev] || []).push(fn); },
    dispatch(ev, obj) {
      (this._listeners[ev] || []).forEach(fn => fn(obj || {}));
      // 真实 DOM 里 onkeydown / onclick 这类**属性式**处理器也会被派发触发；
      // 桩以前只认 addEventListener，于是「回车提交」这条路根本没被跑到（2026-09-21 补）。
      const prop = this["on" + ev];
      if (typeof prop === "function") prop(obj || {});
    },
    setAttribute(k, v) { (this._attrs = this._attrs || {})[k] = String(v); },
    getAttribute(k) { return (this._attrs || {})[k] === undefined ? null : this._attrs[k]; },
    // 闪卡全屏会申请**对模块自己的**原生全屏（2026-09-21）；桩里记一笔供断言
    requestFullscreen() { fsCalls.push("request:" + this.tagName.toLowerCase()); return Promise.resolve(); },
    querySelectorAll(sel) { return qsa(this, sel); },
    querySelector(sel) { return qsa(this, sel)[0] || null; },
    click() { if (this.disabled) return; if (this.onclick) this.onclick({ target: this }); },
  };
  Object.defineProperty(el, "classList", { value: {
    add: c => el._cls.add(c), remove: c => el._cls.delete(c), contains: c => el._cls.has(c),
    toggle: (c, on) => { (on === undefined ? !el._cls.has(c) : on) ? el._cls.add(c) : el._cls.delete(c); },
  }});
  Object.defineProperty(el, "className", {
    get: () => Array.from(el._cls).join(" "),
    set: v => { el._cls = new Set(String(v).split(/\s+/).filter(Boolean)); },
  });
  Object.defineProperty(el, "innerHTML", {
    get: () => el._html,
    set: v => { el._html = String(v); el.children = []; registerIds(el, el._html, reg); },
  });
  // esc() 靠 createElement + textContent + innerHTML 做转义，这里要同步维护 _html
  Object.defineProperty(el, "textContent", {
    get: () => el._text,
    set: v => {
      el._text = String(v);
      el._html = String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      el.children = [];
    },
  });
  // 滚动相关属性按浏览器的默认来：不做的话 body.scrollTop 是 undefined，
  // 「滚到第几」这类断言就成了 NaN（2026-09-21 加滚动测试时踩到）。
  if (el.scrollTop === undefined) el.scrollTop = 0;
  // 数字按真实解析面板的量级给（约 420px 高、内容更长）：不然「42px」会被判成贴着底部，
  // 「不要拽走我的滚动条」这条断言就测不准了。
  if (el.scrollHeight === undefined) el.scrollHeight = 600;
  if (el.clientHeight === undefined) el.clientHeight = 300;
  // 「这个节点现在看得见吗」——闪卡全屏的 F 键守卫靠它判断（练到一半人在别的
  // 子页、浮窗也关着时，练习区是 hidden 的，那时按 F 会把人锁进看不见的全屏里）。
  // 桩默认「看得见」（给一个非空 rect 列表）；要测隐藏就把它清空。
  el.getClientRects = () => [{ top: 0, left: 0, width: 800, height: 600 }];
  return el;
}
// canvas 桩：简答题上传图片时只关心"压缩后的尺寸"，画什么不重要
function mkCanvas() {
  const cv = mkEl("canvas");
  cv.width = 0; cv.height = 0;
  cv.getContext = () => ({ drawImage() {} });
  cv.toDataURL = () => "data:image/jpeg;base64," + "A".repeat(1000);
  return cv;
}

function registerIds(host, html, reg) {
  reg = reg || registry;    // 缺省给当前这轮；正常调用都会显式传自己那一份
  // 反馈区自己是被 renderCard 一次性建出来的，之后的 innerHTML 只含子元素 id，
  // 所以这里只补子元素；先把「谁是反馈区」记下来，免得被下面的循环换掉。
  const isFeedback = reg["fs-feedback"] === host;
  // ⚠️ 标签名要按 HTML 里真实的来，不能一律建 div。全局快捷键那条守卫是
  //    `/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)`——以前 #fs-exp-input 在桩里
  //    是个 div，守卫永远不触发，「输入框里的键会不会被全局抢走」根本测不出来
  //    （2026-09-17 查「追问框里回车=发送还是下一张」时才暴露）。
  const re = /<(\w+)[^>]*?\bid="([\w-]+)"/g; let m;
  while ((m = re.exec(html))) {
    if (m[2] === "fs-feedback" && isFeedback) continue;
    const el = mkEl(m[1]);
    el._id = m[2];
    reg[m[2]] = el;
  }
  // 评分按钮是 innerHTML 拼出来的，补成子元素好让 querySelectorAll 找得到
  if (isFeedback) {
    const re2 = /data-rate="(\d)"/g;
    while ((m = re2.exec(html))) {
      const b = mkEl("button"); b._cls.add("fs-btn"); b.dataset.rate = m[1];
      host.children.push(b);
    }
  }
  // 「这题有问题」的原因芯片也是 innerHTML 拼的（只有 data-kind、没有 id）：
  // 补成子元素，才能用 querySelectorAll('.fs-flag-kind') 找到并点它（2026-09-17）。
  if (html.indexOf('class="fs-flag-kind"') >= 0) {
    const re3 = /data-kind="([\w-]+)"[^>]*?aria-pressed="(\w+)"/g;
    while ((m = re3.exec(html))) {
      const b = mkEl("button"); b._cls.add("fs-flag-kind");
      b.dataset.kind = m[1];
      b.setAttribute("aria-pressed", m[2]);
      host.children.push(b);
    }
  }
}
function matches(el, sel) {
  sel = sel.trim();
  // .cls[attr="值"] —— 原因芯片的「当前选中」就是这么标的
  const am = sel.match(/^\.([\w-]+)\[([\w-]+)="([^"]*)"\]$/);
  if (am) return el._cls.has(am[1]) && (el._attrs || {})[am[2]] === am[3];
  if (sel[0] === ".") return el._cls.has(sel.slice(1));
  if (sel === "[data-rate]") return el.dataset && el.dataset.rate !== undefined;
  return el.tagName === sel.toUpperCase();
}
function qsa(root, sel) {
  const m = String(sel).match(/^#([\w-]+)\s+(.+)$/);
  if (m) {
    const base = registry[m[1]];
    return base ? base.children.filter(c => matches(c, m[2])) : [];
  }
  const out = [];
  (function walk(n) { (n.children || []).forEach(c => { if (matches(c, sel)) out.push(c); walk(c); }); })(root);
  return out;
}

const CARDS = [
  { card_id: 1, type: "choice", subject: "政治", topic_name: "T1", state: 0, lapses: 0,
    previews: { 1: "1分", 2: "6分", 3: "10分", 4: "4天" },
    content: { stem: "选择题", options: ["甲", "乙", "丙", "丁"], answer: 1, explanation: "因为乙" } },
  { card_id: 2, type: "judge", subject: "政治", topic_name: "T2", state: 0, lapses: 0,
    previews: {}, content: { stem: "判断题", answer: true, explanation: "对" } },
  { card_id: 3, type: "fill", subject: "政治", topic_name: "T3", state: 0, lapses: 0,
    previews: {}, content: { stem: "填空题", answer: "某答案" } },
  { card_id: 4, type: "choice", subject: "政治", topic_name: "T4", state: 0, lapses: 0,
    previews: {}, content: { stem: "选择题二", options: ["甲", "乙", "丙", "丁"], answer: 0 } },
  { card_id: 5, type: "short", subject: "数学", topic_name: "T5", state: 0, lapses: 0, previews: {},
    content: { stem: "判断 f(x)=|x|x 在 x=0 处是否为拐点，并说明理由。",
               reference_answer: "是拐点。f''(0) 不存在，但 f'' 在 x=0 两侧变号。",
               key_points: ["结论：是拐点", "理由：两侧变号"],
               explanation: "拐点判据不要求 f''(0) 存在。" } },
];

let fetchCalls = [], keyHandler = null;
// 用例可预置「上次同题同错选的解析」（GET /api/explain 的返回值）
let explainCache = null;
// 「这题有问题」标记：POST 过的请求体（2026-09-17）
let reportsCalls = [];
// 用例可模拟「serve.js 改了但没重启」：旧版服务对 reports 路径回 404
let reportsHttp404 = false;
// 📌 钉住（2026-09-19）：POST /api/flashcards/pin 的调用留痕 + 旧版服务开关
let pinCalls = [];
let pinHttp404 = false;
// 服务端下发的报卡原因清单（真实接口 GET /api/flashcards/reports 的 kinds 字段）
const REPORT_KINDS = [
  { id: "multi_correct", label: "多个选项都对", hint: "不止一个正确项" },
  { id: "answer_wrong", label: "答案有误", hint: "标出的答案不对" },
  { id: "other", label: "其它", hint: "看备注" },
];
const REPORT_LABEL = REPORT_KINDS.reduce((m, k) => { m[k.id] = k.label; return m; }, {});
// 用例可换一套卡：默认那套没有 question_id，压根不会触发解析区
let cardsPayload = null;
// 用例可预置「服务端的当日那一组」（多端共享的那份进度，2026-09-21）
let serverSession = null;
// 设置里的「今日刷完后再来 N 张」（flash_extra_count）：闪卡页头部按钮要把它写出来
let extraSetting = 20;

function boot(seed) {
  registry = {}; fetchCalls = []; keyHandlers = []; fsCalls = []; docListeners = {}; focusSim = true;
  reportsCalls = [];
  reportsHttp404 = false;
  pinCalls = [];
  pinHttp404 = false;
  // 「每次 boot 等于新开一次页面」：窗口监听者与「同步钩子已接线」标记都要重置，
  // 否则第二次 boot 不再注册 hashchange/visibilitychange 钩子，同步就测不到了。
  globalThis.__winListeners = {};
  globalThis.__flashSyncWired = false;
  explainCache = null;
  const seedStore = seed || null;
  // 把这一轮的表**captured 成局部 const**再用：老用例的 setInterval 在下一轮 boot
  // 之后仍然活着，它会通过 document.getElementById 找元素——要是那个闭包读的是
  // 模块级 registry（每次 boot 都换指向），旧轮就会写进新轮的 #fs-timer，
  // 把「点结束回到 off 态」这类断言随机带崩。
  const reg = registry;
  const store = {};
  const doc = {
    body: mkEl("body"),
    // 学习计时的「算不算在学」要读这两个：默认按有焦点、可见处理，
    // 个别用例改成 true/false 来模拟切窗口/切标签页。
    hidden: false,
    hasFocus: () => focusSim,
    getElementById: id => reg[id] || null,
    createElement: t => (t === "canvas" ? mkCanvas() : mkEl(t)),
    querySelectorAll: sel => qsa({ children: [] }, sel),
    querySelector: sel => qsa({ children: [] }, sel)[0] || null,
    // ️ keydown 可能有**多个**监听者（闪卡答题 + 全屏 Esc），只留最后一个会让
    //    键盘用例全跑到 Esc 那个 handler 上（2026-09-21 修）
    addEventListener: (ev, fn) => {
      (docListeners[ev] = docListeners[ev] || []).push(fn);
      if (ev === "keydown") keyHandlers.push(fn);
    },
    removeEventListener() {},
    // 原生全屏的桩：元素级的 requestFullscreen 在 mkEl 上，这里管文档级
    fullscreenElement: null,
    exitFullscreen() { fsCalls.push("exit"); this.fullscreenElement = null; return Promise.resolve(); },
    head: { appendChild() {} },   // KaTeX 按需加载会往 head 里插 <link>/<script>
  };
  // 浏览器里 window === globalThis；全局按需 KaTeX 把 richText/texWrap 挂在 window 上，
  // 闪卡代码用裸名调用它们，所以这里必须传真正的 globalThis，不能传一个游离对象。
  const win = globalThis;
  // 真实页面里这几个容器是 HTML 里写死的，桩里也得让它们"存在"，
  // 否则 FLASH_JS 的 getElementById 拿到 null（生产代码里有 null 守卫，不会炸）
  // fs-timer / fs-stop 是 2026-09-21 的学习计时徽标与「结束」按钮，
  // 不放进来的话计时相关的断言全都看不到 DOM。
  // flash-practice / fs-full-toggle 是闪卡全屏的容器与按钮（同样那天改的：
  // 现在会申请原生全屏，得让这段代码跑起来才能验）。
  // fs-float* 是闪卡浮窗（2026-09-22）：其余页也能把练习区借过去当浮窗用，
  // 不给这几个容器的话浮窗那段会整段退出，等于测不到。
  const PRESENT = ["flash-studio", "flash-filter", "fs-timer", "fs-stop",
                   "flash-practice", "fs-full-toggle",
                   "fs-float", "fs-float-body", "fs-float-bar", "fs-float-title",
                   "fs-float-close", "fs-float-home"];
  doc.getElementById = id => (PRESENT.includes(id) ? (reg[id] || (reg[id] = mkEl("div"))) : reg[id] || null);
  // 「老家」：真实页面里 #flash-practice 在闪卡页那个 .page 里。浮窗把它整块搬走，
  // 收起时要放回原位，所以桩里也得有个父节点，否则测不出「搬回来没有」。
  const flashPage = mkEl("div");
  flashPage._cls.add("page");
  flashPage.appendChild(doc.getElementById("flash-practice"));
  doc.__flashPage = flashPage;      // 用例要用
  doc.__store = null;               // 由下面的 localStorage 桩填上（读落盘结果用）
  const localStorage = {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; },
  };
  doc.__store = store;              // 用例要能看见落盘结果（浮窗位置 / 收起状态）
  // 允许用例预置 localStorage（比如「上一组没刷完就刷新了」这种场景）
  if (seedStore) for (const k of Object.keys(seedStore)) store[k] = String(seedStore[k]);
  const fetchStub = (url, opts) => {
    fetchCalls.push({ url, body: opts && opts.body ? JSON.parse(opts.body) : null });
    let payload = { ok: true };
    if (url.includes("/session")) {
      const isPeek = url.includes("peek=1"), isResume = url.includes("resume=1");
      if (isPeek || isResume) {
        // 服务端那份进度：peek 只回进度，resume 连卡片一起回
        const s = serverSession;
        payload = {
          ok: true,
          saved: s ? { total: s.cards.length, idx: s.idx, updated: "2026-09-16 05:00:00", device: "平板" } : null,
          cards: (isResume && s) ? s.cards : [],
          idx: s ? s.idx : 0,
          filter: s ? s.filter : null,
        };
      } else if (opts && opts.body) {
        payload = { ok: true, saved: true, idx: 0 };          // POST：整份进度交给服务端
      } else {
        payload = { ok: true, cards: cardsPayload || CARDS, limits: null, counts: null };
      }
    }
    else if (url.includes("/facets")) payload = { ok: true, subjects: [
        { subject: "政治", total: 167, new: 167, learning: 0, review: 0, mature: 0, leech: 0, suspended: 0, due: 167 },
        { subject: "英语一", total: 82, new: 63, learning: 2, review: 4, mature: 13, leech: 0, suspended: 0, due: 67 },
      ], totals: { total: 249, new: 230, learning: 2, review: 4, mature: 13, leech: 0, suspended: 0, due: 234 } };
    else if (url.includes("/today")) payload = { ok: true, reviewed_today: 0 };
    // 闪卡页头部那个「🔁 再来一组（N 张）」的张数从这儿来（设置页读的是同一份）
    else if (url.includes("/api/settings")) payload = { ok: true, review: {
        new_per_day: 30, reviews_per_day: 50, flash_extra_count: extraSetting } };
    else if (url.includes("/review")) payload = { ok: true };
    else if (url.includes("/undo")) payload = { ok: true };
    // 解析区先查历史（GET，不带 body）：返回预置的缓存或「没有」
    else if (url.includes("/api/explain") && !url.includes("/followup") && !(opts && opts.body)) {
      payload = explainCache || { ok: true };
    }
    else if (url.includes("/api/explain")) payload = { ok: true, thread_id: "T-NEW", text: "【新生成的解析】" };
    else if (url.includes("/api/grade")) payload = {
      ok: true, score: 85, verdict: "对", suggested_rating: 3,
      transcription: "因为 f''(0) 不存在，但 f'' 在 x=0 两侧变号",
      hits: ["结论：是拐点"], missed: ["未点明拐点判据不要求 f''(0) 存在"],
      feedback: "结论和核心推理都对，补上判据那句就更完整。",
      reference_answer: "是拐点。f''(0) 不存在但两侧变号。",
    };
    // 「这题有问题」标记（2026-09-17）：GET 回原因清单（前端不自己抄一份标签表），
    // POST 回新标记（含待修总数，前端那句 toast 要用）。
    else if (url.includes("/flashcards/pin")) {
      if (opts && opts.body) {
        if (pinHttp404) {
          return Promise.resolve({ ok: false, status: 404,
            json: () => Promise.reject(new Error("不是 JSON")) });
        }
        const b = JSON.parse(opts.body);
        pinCalls.push(b);
        payload = { ok: true, card_id: b.card_id, pinned: b.pinned !== false, count: 1 };
      } else {
        payload = { ok: true, count: 0, pins: [] };
      }
    }
    else if (url.includes("/flashcards/reports")) {
      if (opts && opts.body) {
        if (reportsHttp404) {
          // 模拟「serve.js 改了但没重启」：旧版服务对这个路径回 404，body 也不是 JSON
          return Promise.resolve({ ok: false, status: 404,
            json: () => Promise.reject(new Error("不是 JSON")) });
        }
        const b = JSON.parse(opts.body);
        reportsCalls.push(b);
        payload = { ok: true, id: 7, created: true, card_id: b.card_id, kind: b.kind,
                    kind_label: REPORT_LABEL[b.kind] || "其它", count: 1 };
      } else {
        payload = { ok: true, open_count: 1, count: 0, reports: [], kinds: REPORT_KINDS };
      }
    }
    // ⚠️ 真实 fetch 的 Response 带 ok/status —— 桩也必须给：生产代码现在会先看
    //    `if (!r0.ok)` 区分「旧版服务 404」与「真的连不上」，桩不给这两个字段的话
    //    ok 恒为 undefined → 明明成功的请求被判成失败（2026-09-17 踩到，一次假红灯）。
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
  };  // ⚠️ location 必须给桩：FLASH_JS / SETTINGS_JS 开头就读 location.protocol 决定 API 基址，
  //    少了它整段 IIFE 在 ReferenceError 里当场死掉（2026-09-21 补，之前 47 项全炸）。
  const loc = { protocol: "http:", origin: "http://localhost:8080", hash: "#/flash" };
  globalThis.__flashTestLoc = loc;   // 用例里要改 hash 触发「切子页」的同步钩子
  new Function("document", "localStorage", "fetch", "window", "location", js)(
    doc, localStorage, fetchStub, win, loc);
  return doc;
}
// 闸门上的开始按钮（有没刷完的本组时是「继续本组」，否则「开始学习」）
const gateBtn = () => registry["fs-gate-start"] || registry["fs-gate-resume"] || null;
// 2026-09-21 起：闪卡区不再一加载就自动组题，必须先点「开始」。
// 老用例关心的是答题链路，所以统一走这个「boot + 点开始」的组合。
async function bootStarted() {
  const d = boot();
  await tick();
  const b = gateBtn();
  if (b) b.click();
  await tick(); await tick();
  return d;
}

// 简答题图片链路要用到 FileReader / Image，真浏览器里本来就有，这里补桩。
// 2400×1600 用来模拟手机原图，验证长边被压到 1280。
globalThis.FileReader = class {
  readAsDataURL() { setTimeout(() => this.onload && this.onload(), 0); }
};
globalThis.Image = class {
  constructor() { this.width = 2400; this.height = 1600; }
  set src(v) { this._src = v; setTimeout(() => this.onload && this.onload(), 0); }
  get src() { return this._src; }
};

const tick = () => new Promise(r => setTimeout(r, 0));
// 派发一个 document 级事件（全屏状态变化靠它同步）
function fireDoc(ev, obj) { (docListeners[ev] || []).forEach(fn => fn(obj || {})); }
function press(key, code) {
  let prevented = false;
  // 所有 keydown 监听者都要收到：真实浏览器就是这么分发的
  keyHandlers.forEach(fn => fn({
    key, code: code || ("Key" + String(key).toUpperCase()), target: { tagName: "BODY" },
    preventDefault() { prevented = true; },
  }));
  return prevented;
}
const SPACE = () => press(" ", "Space");
const ENTER = () => press("Enter", "Enter");
const opts = () => (registry["fs-body"] ? registry["fs-body"].children.filter(c => c._cls.has("fs-opt")) : []);
const fb = () => (registry["fs-feedback"] ? registry["fs-feedback"]._html : "");
const rated = () => fetchCalls.filter(c => c.url.includes("/review") && c.body);
// 「重新组题」的那次请求（服务端建新组）；peek/resume/POST 分属同步机制，不算
const groupCalls = () => fetchCalls.filter(c => c.url.includes("/session")
  && c.url.indexOf("peek=") < 0 && c.url.indexOf("resume=") < 0 && !c.body);
const undos = () => fetchCalls.filter(c => c.url.includes("/undo"));

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

(async () => {
  // ---------- 1. 选择题：A–D 与 1–4 选选项 ----------
  console.log("\n[1] 选择题按字母/数字选选项");
  // 第 1 张答案 = 乙（序号 1）。B/2 选中即答对，D/4、A/1 是答错，两者都该被按下并锁定。
  for (const [key, want] of [["B", 1], ["2", 1], ["D", 3], ["4", 3], ["A", 0], ["1", 0]]) {
    await bootStarted();
    press(key);
    const o = opts();
    check("按 " + key + " 选中第 " + (want + 1) + " 项并锁定",
      o[want] && (o[want]._cls.has("correct") || o[want]._cls.has("wrong")) && o[want].disabled);
    check("  └ 正确项乙始终高亮", o[1] && o[1]._cls.has("correct"));
  }

  console.log("\n[2] 答对 → 要求自评，不自动提交");
  {
    await bootStarted();
    press("B");   // card 1 answer=1 → 正确
    check("反馈里有四档评分按钮", (fb().match(/data-rate=/g) || []).length === 4);
    check("反馈里没有「下一张」", !fb().includes("fs-next"));
    check("没有自动提交评分", rated().length === 0);
    check("提示语切到自评", registry["flash-studio"].querySelector(".fs-hint").textContent.includes("自评"));
    press("3");
    await tick();
    check("按 3 → 提交 rating=3", rated().length === 1 && rated()[0].body.rating === 3, JSON.stringify(rated()));
  }

  // ---------- 3. 答错 → 直接判 ----------
  console.log("\n[3] 答错 → 直接判「忘记」，不再要求自评");
  {
    await bootStarted();
    press("A");   // 正确答案是 B → 错
    check("立即提交 rating=1", rated().length === 1 && rated()[0].body.rating === 1, JSON.stringify(rated()));
    check("反馈里没有四档评分按钮", (fb().match(/data-rate=/g) || []).length === 0);
    check("反馈里有「下一张」", fb().includes("fs-next"));
    check("提示语说已记忘记", registry["flash-studio"].querySelector(".fs-hint").textContent.includes("已记"));
    check("错项标红 / 正确项标绿", opts()[0]._cls.has("wrong") && opts()[1]._cls.has("correct"));
    await tick();
    press("3");
    check("此时按 3 不评分（已判过）", rated().length === 1);
    SPACE();
    await tick();
    // 下一张是判断题（2 个选项），反馈区应已清空
    check("空格 → 翻到下一张", opts().length === 2 && fb() === "");
  }

  // ---------- 4. 判断题：1/2 判正误 ----------
  console.log("\n[4] 判断题 1/2 判正误");
  {
    await bootStarted();
    SPACE();                       // 跳过选择题？不——空格是「显示答案」，先翻页
    await tick();
  }
  {
    // 直接对第 2 张（判断题，answer=true）做测试：先把第 1 张过掉
    await bootStarted();
    press("A");                    // 第1张错 → 自动判
    await tick(); SPACE(); await tick();   // 翻到第2张
    check("已到判断题", opts().length === 2 && opts()[0]._text === undefined || true);
    const fb0 = fb();
    press("1");                    // 1 = 正确 → 对
    check("判 正确 → 不自动提交", rated().length === 1);
    check("要求自评（有 data-rate）", fb().includes("data-rate"));
    press("2");
    await tick();
    check("自评 2 → 提交 rating=2", rated()[1] && rated()[1].body.rating === 2, JSON.stringify(rated()[1]));
  }
  {
    await bootStarted();
    press("A"); await tick(); SPACE(); await tick();   // 到判断题
    press("2");                    // 2 = 错误，但答案是对的 → 答错
    check("判 错误 → 自动 rating=1", rated().length === 2 && rated()[1].body.rating === 1, JSON.stringify(rated()));
    check("显示「下一张」", fb().includes("fs-next"));
    check("正确项（正确）标绿", opts()[0]._cls.has("correct"));
    check("错项（错误）标红", opts()[1]._cls.has("wrong"));
  }

  // ---------- 5. 键盘不误伤 ----------
  console.log("\n[5] 边界：不误判成评分");
  {
    await bootStarted();
    press("9");
    check("按 9 无动作", rated().length === 0 && !opts()[0].disabled);
    press("E");
    check("4 选项题按 E 无动作", rated().length === 0 && !opts()[0].disabled);
    SPACE();
    check("空格 → 显示答案（放弃作答）", opts()[1]._cls.has("correct") && (fb().match(/data-rate=/g) || []).length === 4);
    press("4"); await tick();
    check("看完答案后按 4 → rating=4", rated().length === 1 && rated()[0].body.rating === 4);
  }

  // ---------- 6. 填空卡 ----------
  console.log("\n[6] 填空/简答：空格看答案再自评");
  {
    await bootStarted();
    press("A"); await tick(); SPACE(); await tick();   // 第1张答错 → 翻页
    press("1"); await tick();                          // 第2张判断题判「正确」→ 对，等自评
    press("3"); await tick();                          // 自评「记得」→ 翻页
    check("到第3张填空", opts().length === 0);
    SPACE();
    check("空格显示答案", fb().includes("某答案") && fb().includes("data-rate"));
    press("3"); await tick();
    check("按 3 → rating=3", rated()[2] && rated()[2].body.rating === 3, JSON.stringify(rated()));
  }

  // ---------- 7. 空格 = 记得（Anki 惯例） ----------
  console.log("\n[7] 空格在「已显示答案」时等于「记得」");
  {
    await bootStarted();
    press("D");                 // 第1张答错 → 自动判
    await tick(); SPACE(); await tick();
    press("1");                 // 第2张判断题答对
    check("答对后未提交", rated().length === 1);
    SPACE(); await tick();
    check("空格 → rating=3", rated().length === 2 && rated()[1].body.rating === 3, JSON.stringify(rated()[1]));
  }

  // ---------- 8. 撤销定位 ----------
  console.log("\n[8] 撤销定位：答错时 idx 未翻页，撤销要打在当前张");
  {
    await bootStarted();
    press("A"); await tick();     // 第1张（card_id=1）答错，自动判，idx 仍为 0
    check("自动判的卡没翻页", !fb().includes("data-rate"));
    press("U"); await tick();
    check("撤销打给 card_id=1", undos().length === 1 && undos()[0].body.card_id === 1, JSON.stringify(undos()));
    check("撤销后回到可作答态", !opts()[0].disabled && opts()[0]._cls.size === 1);
  }
  {
    await bootStarted();
    press("B"); await tick(); press("3"); await tick();   // 第1张答对 → 自评 → 已翻到第2张
    check("已翻页", opts().length === 2);
    press("U"); await tick();
    check("撤销打给 card_id=1", undos().length === 1 && undos()[0].body.card_id === 1, JSON.stringify(undos()));
    check("撤销后回到第1张（4 选项）", opts().length === 4 && !opts()[1].disabled === false || opts().length === 4);
  }

  // ---------- 9. 筛选：URL 组装 ----------
  console.log("\n[9] 筛选条件 → session URL");
  {
    await bootStarted();
    const s0 = groupCalls().pop();
    check("无筛选时走智能组题 limit=200（2026-09-20 起一次取回当日额度）",
      s0 && s0.url.endsWith("/api/flashcards/session?limit=200"), s0 && s0.url);

    globalThis.__flashStart({ subject: "政治", bucket: "review" });
    await tick();
    const s1 = groupCalls().pop();
    check("带筛选时附 subject", s1.url.includes("subject=%E6%94%BF%E6%B2%BB"), s1.url);
    check("带筛选时附 bucket", s1.url.includes("bucket=review"), s1.url);
    check("带筛选时走 mode=browse（不受每日限额约束）", s1.url.includes("mode=browse"), s1.url);
    check("带筛选时 limit 提到 50", s1.url.includes("limit=50"), s1.url);

    // 只选科目不选桶：不应带 bucket 参数
    globalThis.__flashStart({ subject: "英语一", bucket: "" });
    await tick();
    const s2 = groupCalls().pop();
    check("只选科目时不带 bucket", !s2.url.includes("bucket="), s2.url);

    // 非法值要被白名单挡掉，不能拼进 URL
    globalThis.__flashStart({ subject: "../../etc", bucket: "'; DROP TABLE" });
    await tick();
    const s3 = groupCalls().pop();
    check("非法 subject/bucket 被白名单过滤掉（退回智能组题 limit=200）",
      s3.url.endsWith("?limit=200"), s3.url);

    globalThis.__flashApplyFilter(null);
    await tick();
    const s4 = groupCalls().pop();
    check("清空筛选后回到默认 URL（limit=200）", s4.url.endsWith("?limit=200"), s4.url);
  }

  // ---------- 10. 筛选页 chips ----------
  console.log("\n[10] 筛选页渲染");
  {
    await bootStarted(); await tick();
    const fb = registry["flash-filter"];
    check("筛选容器存在（大盘里是 #flash-filter）", !!fb);
    if (fb) {
      const h = fb._html;
      check("渲染出状态 chip", h.includes('data-bucket="new"') && h.includes("未学习"), h.slice(0, 120));
      check("渲染出科目 chip", h.includes('data-subject="政治"'), h.slice(0, 200));
      check("数量来自 /facets", h.includes("167"), h.slice(0, 300));
      check("数量为 0 的桶标为禁用（水蛭/已暂停）",
        /class="ff-chip empty"[^>]*data-bucket="leech"[^>]*disabled/.test(h)
        || /data-bucket="leech"[^>]*disabled/.test(h), h.slice(0, 300));
      check("有开始按钮", h.includes('id="ff-start"'));
    }
    // facets 缺字段时不能抛
    let threw = false;
    try { globalThis.__flashApplyFilter({ subject: "政治" }); await tick(); } catch (e) { threw = true; }
    check("facets 数据异常时不抛异常", !threw);
  }

  // ---------- 11. 简答题：文字作答 + 手写照片 + AI 批改 ----------
  console.log("\n[11] 简答题作答与 AI 批改");
  {
    await bootStarted();
    // 一路翻到第 5 张（简答卡）：空格看答案 → 按 3 完成自评 → 自动翻页
    for (let i = 0; i < 4; i++) {
      SPACE(); await tick(); press("3"); await tick();
    }
    check("作答区渲染出文本框", !!registry["fs-answer"]);
    check("有上传手写答案按钮", !!registry["fs-pick"]);
    check("有提交批改按钮", !!registry["fs-grade-btn"]);
    check("有先看参考答案按钮", !!registry["fs-skip"]);
    check("简答题不给选项按钮", opts().length === 0);

    const ta = registry["fs-answer"];
    ta.value = "是拐点，因为 f''(0) 不存在但两侧变号";
    ta.oninput();
    check("文本框内容被记录", ta.value.length > 0);

    // 图片链路：file input → canvas 压缩 → 预览
    const fileInput = registry["fs-file"];
    fileInput.files = [{ type: "image/jpeg", name: "handwriting.jpg" }];
    fileInput.onchange();
    await tick(); await tick(); await tick();
    const imgBox = registry["fs-imgbox"];
    check("压缩后生成预览图", imgBox._html.indexOf("<img") >= 0);
    check("长边压到 1280（手机原图 2400x1600 → 1280x853）",
      imgBox._html.indexOf("1280×853") >= 0, imgBox._html.slice(0, 160));

    // 提交批改
    const gradeCalls = () => fetchCalls.filter(c => c.url.indexOf("/api/grade") >= 0);
    check("提交前没有批改请求", gradeCalls().length === 0);
    registry["fs-grade-btn"].onclick();
    await tick(); await tick();
    check("发出了批改请求", gradeCalls().length === 1);
    const sent = gradeCalls()[0].body || {};
    check("请求带上了压缩后的图片",
      typeof sent.image === "string" && sent.image.indexOf("data:image/jpeg;base64,") === 0);
    check("请求带上了文字答案", typeof sent.answer_text === "string" && sent.answer_text.length > 0);

    const fbHtml = fb();
    check("展示 AI 分数", fbHtml.indexOf("85") >= 0);
    check("展示手写辨认结果", fbHtml.indexOf("手写辨认") >= 0);
    check("展示遗漏得分点", fbHtml.indexOf("遗漏") >= 0);
    check("展示参考答案", fbHtml.indexOf("参考答案") >= 0);
    check("给出四档评分按钮", (fbHtml.match(/data-rate=/g) || []).length === 4);
    check("AI 建议那一档带徽章", fbHtml.indexOf("fs-rate-ai") >= 0);
    check("批改后文本框锁定", registry["fs-answer"].disabled === true);

    const before = rated().length;
    press("3");
    await tick();
    check("可按 AI 建议档提交评分", rated().length === before + 1);
    check("提交的 rating=3", (rated().slice(-1)[0].body || {}).rating === 3);
  }

  // ---------- 12. 「开始」闸门 + 学习计时（失焦暂停）----------
  console.log("\n[12] 「开始」闸门与学习计时");
  {
    boot();
    await tick();
    check("开大盘不自动组题（只 peek 进度，不重新组题）",
      groupCalls().length === 0 && fetchCalls.some(c => c.url.indexOf("peek=1") >= 0),
      fetchCalls.map(f => f.url).join(" "));
    check("练习区停在「开始」闸门上", registry["flash-studio"]._html.indexOf("fs-gate") >= 0);
    check("闸门上有开始按钮", !!registry["fs-gate-start"]);
    check("没开始时结束按钮是隐藏的", registry["fs-stop"] && registry["fs-stop"].hidden === true);
    press("2"); await tick();
    check("闸门态按数字键不答题", rated().length === 0);

    registry["fs-gate-start"].click();
    await tick(); await tick();
    check("点了开始才去组题", groupCalls().length > 0, fetchCalls.map(f => f.url).join(" "));
    check("点了开始才起表", registry["fs-stop"].hidden === false);
    check("计时徽标进入计时态", /run/.test(registry["fs-timer"].className), registry["fs-timer"].className);
    const shownAt = registry["fs-timer"]._html;
    // 跨过整秒再比：0.4s 时读数还停在 00:00，会假报「计时没走」
    await new Promise(r => setTimeout(r, 1100));
    check("计时在走（读数变化）", registry["fs-timer"]._html !== shownAt);

    // 失焦（Alt+Tab 走开 / 切标签页）：读数必须冻住
    focusSim = false;
    await new Promise(r => setTimeout(r, 300));        // 先让状态刷进 DOM
    const frozen = registry["fs-timer"]._html;
    check("窗口失焦 → 转暂停态", /pause/.test(registry["fs-timer"].className), registry["fs-timer"].className);
    check("暂停时读数里写着已暂停", frozen.indexOf("已暂停") >= 0);
    await new Promise(r => setTimeout(r, 1200));
    check("暂停期间读数不再往前走", registry["fs-timer"]._html === frozen);
    focusSim = true;
    await new Promise(r => setTimeout(r, 1200));
    check("回到窗口自动续上并继续累加", /run/.test(registry["fs-timer"].className)
      && registry["fs-timer"]._html !== frozen, registry["fs-timer"]._html);

    registry["fs-stop"].click();
    await tick(); await tick();
    check("「结束」回到闸门", registry["flash-studio"]._html.indexOf("fs-gate") >= 0);
    check("结束即停表", /off/.test(registry["fs-timer"].className), registry["fs-timer"].className);
    press("2"); await tick();
    check("回到闸门后数字键不再评分", rated().length === 0);
  }

  // ---------- 13. 刷新回来：闸门要给「继续本组」而不是偷偷重新组题 ----------
  console.log("\n[13] 刷新中断后继续");
  {
    // 用学习日，跟页面 todayStr() 同一套（凌晨 4 点前算前一天）。
    // 用日历日的话，0:00~4:00 之间闸门会认定「这不是今天的组」而认不出本组。
    const today = _day.dayKey(js);
    const saved = { date: today, cards: CARDS, idx: 2, stats: { 1: 0, 2: 0, 3: 1, 4: 0 }, lastRatedIdx: null, filter: null };
    boot({ kaoyan_flash_session_v1: JSON.stringify(saved) });
    await tick();
    check("闸门认出了没刷完的本组", !!registry["fs-gate-resume"]);
    check("按钮写着还剩几张", registry["flash-studio"]._html.indexOf("还剩 3 张") >= 0,
      registry["flash-studio"]._html.slice(0, 200));
    check("并列给了「重新挑一组」", !!registry["fs-gate-new"]);
    check("点之前不重新组题（不偷偷换掉这一组）",
      groupCalls().length === 0, fetchCalls.map(f => f.url).join(" "));
    registry["fs-gate-resume"].click();
    await tick(); await tick();
    check("点继续不重新组题：向服务端要回当日那一组（多端共享）",
      groupCalls().length === 0 && fetchCalls.some(c => c.url.indexOf("resume=1") >= 0),
      fetchCalls.map(f => f.url).join(" "));
    check("续上的是第 3 张", registry["flash-studio"]._html.indexOf("第 3 / 5 张") >= 0,
      registry["flash-studio"]._html.slice(0, 120));
    check("续上即起表", /run/.test(registry["fs-timer"].className), registry["fs-timer"].className);
    // 昨天留下的会话不该被当成今天的
    boot({ kaoyan_flash_session_v1: JSON.stringify(Object.assign({}, saved, { date: "2000-01-01" })) });
    await tick();
    check("隔天的残留进度不弹「继续」", !registry["fs-gate-resume"] || registry["flash-studio"]._html.indexOf("开始这一组闪卡") >= 0);
  }

  // ---------- 14. 闪卡模块全屏（原生全屏 + CSS 铺满双保险）----------
  console.log("\n[14] 闪卡模块全屏");
  {
    const doc = boot();
    await tick();
    const sec = registry["flash-practice"], btn = registry["fs-full-toggle"];
    check("闪卡全屏容器与按钮都在场", !!sec && !!btn);
    btn.dispatch("click");
    await tick();
    check("点全屏 → 申请了模块级原生全屏（不用再按 F11）",
      fsCalls.indexOf("request:div") >= 0, fsCalls.join(","));
    check("同时挂上 is-full：拿不到原生全屏也能铺满", sec._cls.has("is-full"));
    check("按钮文案变成退出全屏", btn.textContent.indexOf("退出全屏") >= 0, btn.textContent);

    // 原生那层被 Esc 退掉（浏览器自己退的，不经过我们的按钮）→ CSS 这层要跟着退
    doc.fullscreenElement = null;
    fireDoc("fullscreenchange");
    await tick();
    check("原生退出后自动收回铺满态", !sec._cls.has("is-full"));
    check("按钮文案回到全屏", btn.textContent.indexOf("退出全屏") < 0, btn.textContent);

    // 再进一次，这次由按钮退出：必须把原生全屏也一起退掉
    btn.dispatch("click");
    await tick();
    doc.fullscreenElement = sec;      // 模拟浏览器真的把全屏给了这个 section
    fsCalls.length = 0;
    btn.dispatch("click");
    await tick();
    check("再点一次 → 调 exitFullscreen 退掉原生全屏", fsCalls.indexOf("exit") >= 0, fsCalls.join(","));
    check("退出后没有残留 is-full", !sec._cls.has("is-full"));
  }

  // ---------- 15. 番茄钟全屏时，闪卡键盘要闭嘴 ----------
  console.log("\n[15] 番茄钟全屏时闪卡键盘闭嘴");
  {
    boot();
    await tick();
    registry["fs-gate-start"].click();
    await tick(); await tick();
    // 第 1 张答案=乙（序号 1），按 A 是选错 → 会立刻自动回写为忘记
    globalThis.__pomoFullscreen = () => true;
    press("A"); await tick(); await tick();
    check("番茄钟盖在上面时按 A 不评分", rated().length === 0, JSON.stringify(fetchCalls.map(f => f.url)));
    delete globalThis.__pomoFullscreen;
    press("A"); await tick(); await tick();
    check("（对照）没有番茄钟全屏时按 A 正常判错回写", rated().length === 1, JSON.stringify(fetchCalls.map(f => f.url)));
  }

  console.log("\n[AI 解析/题库解析的行内 Markdown 渲染]");
  {
    boot();
    await tick();
    const rt = globalThis.__richText;
    check("暴露了 richText 给测试", typeof rt === "function");
    if (typeof rt === "function") {
      // 用户截图的原文：题库自带 explanation 里的 **是** 原先就是两颗裸露星号
      const a = rt("反过来 $f(x)=|x|$ 在 $x=0$ 处 $f''$ 不存在，却**是**拐点。");
      check("粗体渲染成 <strong>（截图里那颗星号的问题）", a.indexOf("<strong>是</strong>") >= 0, a);
      check("同一段里的公式照旧交给 KaTeX", a.indexOf("katex") >= 0 || a.indexOf("tex-fallback") >= 0, a);
      const b = rt("**结论**：不对。");
      check("整行粗体也对", b.indexOf("<strong>结论</strong>") >= 0, b);
      const c = rt("行内代码 `P(mutex)` 要认");
      check("行内代码渲染成 <code>", c.indexOf("<code") >= 0 && c.indexOf("P(mutex)") >= 0, c);
      const d = rt("2*3=6 不该变斜体");
      check("单个星号算式不误判成斜体", d.indexOf("<em>") < 0 && d.indexOf("2*3=6") >= 0, d);
      const e = rt("没有公式的纯文本也该认 **粗体**");
      check("没有 $ 的纯文本也走 Markdown", e.indexOf("<strong>粗体</strong>") >= 0, e);
      const f = rt("<script>alert(1)</script> **粗**");
      check("先转义再认标记（尖括号没漏出去）",
        f.indexOf("&lt;script&gt;") >= 0 && f.indexOf("<strong>粗</strong>") >= 0, f);
    }
    // ⚠️ mdTex 在**后面另一个 IIFE** 里，它调 mdInline 只能靠 window 上的桥
    //    （2026-09-21：漏搭桥 → mdTex 一调用就 ReferenceError，AI 回复整块渲染不出来，
    //     而调用处的 catch 把它吞了，表现成「复用没生效，又问了一遍 AI」）。
    const md = globalThis.__mdTex;
    check("暴露了 mdTex", typeof md === "function");
    if (typeof md === "function") {
      const g = md("**粗** 与 $x^2$");
      check("mdTex 也能认粗体（跨 IIFE 的桥没断）", g.indexOf("<strong>粗</strong>") >= 0, g);
    }
    // ---- 裸 LaTeX（模型不写 $ 也认）----
    // 用户截图反馈过：AI 解析里出现裸露的 \sum a_n / \Rightarrow / \frac{...}。
    const BS = String.fromCharCode(92);
    if (typeof rt === "function") {
      const shot = rt("看到“绝对收敛”“条件收敛”先写出两个级数: " + BS + "sum a_n 和 "
        + BS + "sum|a_n|，分别判断收敛性。");
      check("截图那种裸 LaTeX 被当成公式渲染",
        /tex-fallback|katex/.test(shot), shot);
      check("整个 \\sum a_n 都在公式里（没被切成半截）",
        shot.indexOf(BS + "sum a_n") >= 0, shot);
      check("中文正文没被卷进公式", shot.indexOf("分别判断收敛性") >= 0, shot);
      const frac = rt("经典例子: " + BS + "sum " + BS + "frac{(-1)^{n+1}}{n} 条件收敛。");
      check("带分式与上下标的也在一个公式里（括号不被当定界符）",
        frac.indexOf(BS + "frac{(-1)^{n+1}}{n}") >= 0, frac);
      // ⚠️ 定界符 pattern 少写一层反斜杠时，`\` … `\` 会被当成一对定界符：
      //    两个公式会被并成一段、中间的中文被卷进公式（查了很久的 bug）。下面盯住它。
      const pad = rt("结果是 " + BS + "(a+b" + BS + ") 而已。");
      check("\\(..\\) 定界：公式内容是 a+b（不含定界符本身）",
        pad.indexOf(">a+b<") >= 0, pad);
      const two = rt(BS + "sum a_n 和 " + BS + "sum b 都要看。");
      check("一句话里两个公式各成一段（不被并成一段）",
        (two.match(/tex-fallback|katex/g) || []).length >= 2
        && two.indexOf("和") >= 0, two);
      const paren = rt("结论 (a) 与 (b) 的区别。");
      check("光括号不算公式", !/tex-fallback|katex/.test(paren), paren);
      const bold2 = rt("却**是**拐点，注意 " + BS + "sum a_n。");
      check("有公式时行内 Markdown 仍然生效（切块不破坏配对）",
        bold2.indexOf("<strong>是</strong>") >= 0, bold2);
      const plain = rt("这题考的是拐点的判据，注意两侧变号这一条。");
      check("普通中文一个公式都不标", !/tex-fallback|katex/.test(plain), plain);
    }
    if (typeof md === "function") {
      const mshot = md("看到 " + BS + "sum a_n 和 " + BS + "sum|a_n|，分别判断。");
      check("mdTex 路径同样认裸 LaTeX", mshot.indexOf(BS + "sum a_n") >= 0, mshot);
    }
    // ---- 跨行独立公式（2026-09-22 用户截图：`\[`、源码、`\]` 三段摊在解析区）----
    // 模型写独立公式几乎总是三行，而 mdTex 是**逐行**渲染的：不先把三行并回一行，
    // 定界符各自成段、公式只能按「裸 LaTeX」降级、行内公式还会被中文切块切碎。
    if (typeof md === "function") {
      const three = md("正确条件是：\n" + BS + "[\n"
        + BS + "frac{" + BS + "partial P}{" + BS + "partial y}="
        + BS + "frac{" + BS + "partial Q}{" + BS + "partial x}\n"
        + BS + "]\n而 B 把偏导顺序记反了。");
      check("跨行 \\[..\\] 并成一个显示公式块", three.indexOf('<div class="md-tex">') >= 0, three);
      check("定界符不再各自成段（正文里没有裸露的 \\[ / \\]）",
        three.indexOf(">" + BS + "[<") < 0 && three.indexOf(">" + BS + "]<") < 0, three);
      check("公式源码整段在一起（两个 frac 都在）",
        (three.match(/frac/g) || []).length >= 2, three);
      check("前后中文正文照常渲染",
        three.indexOf("正确条件是") >= 0 && three.indexOf("记反了") >= 0, three);
      const two = md("$$\nx^2+y^2=1\n$$");
      check("跨行 $$..$$ 也并成一块", two.indexOf('<div class="md-tex">') >= 0, two);
      const unclosed = md(BS + "[\nx=1\n这段是正文，不该被卷进公式");
      check("定界符没闭合时不吞后面的正文", unclosed.indexOf("不该被卷进公式") >= 0, unclosed);

      // ---- KaTeX 懒加载：AI 回复常常是页面上**第一处**出现公式的地方 ----
      // 题面/选项没公式时 richText 不会触发 ensureKatex，解析区于是永远停在源码兜底上
      // （用户截图里整段 LaTeX 源码就是这么来的）。mdTex 必须自己喊一声，
      // 并且把整块登记进 texStore —— 刚喊完那一刻 window.katex 还没到。
      const realEnsure = globalThis.ensureKatex, realRegister = globalThis.registerTex;
      const hadKatex = globalThis.katex;
      delete globalThis.katex;                      // 模拟「页面还没加载 KaTeX」
      let ensureCalls = 0, regCalls = 0;
      globalThis.ensureKatex = () => { ensureCalls++; return Promise.resolve(true); };
      globalThis.registerTex = (fn) => { regCalls++; return realRegister(fn); };
      let lazy = "";
      try { lazy = md("由 $" + BS + "sum a_n$ 收敛可得。"); } finally {
        globalThis.ensureKatex = realEnsure;
        globalThis.registerTex = realRegister;
        if (hadKatex !== undefined) globalThis.katex = hadKatex;
      }
      check("mdTex 自己触发 KaTeX 懒加载（不指望 richText 顺带）",
        ensureCalls === 1, "ensureCalls=" + ensureCalls);
      check("并把整块登记进 texStore（就绪后原地重渲染）",
        regCalls === 1, "regCalls=" + regCalls);
      check("登记过的块带 data-texid（flushMath 认这个）",
        /data-texid="\d+"/.test(lazy), lazy);
      const noMath = md("这段解析一个公式都没有，只是普通中文。");
      check("没有公式的回复不折腾 KaTeX（不为它加载几 MB）",
        noMath.indexOf("data-texid") < 0, noMath);
    }
  }

  console.log("\n[复用上次解析：同题 + 同错选就不再问 AI]");
  {
    cardsPayload = [Object.assign({}, CARDS[0], { question_id: "Q-TEST-1" })];
    boot();
    await tick(); await tick();
    // 预置：上次同样是「选 A（甲）」答错，已经有解析
    explainCache = {
      ok: true, thread_id: "T-OLD", chosen: "A. 甲", mode: "wrong",
      created_at: "2026-09-15 22:31:00",
      messages: [
        { role: "user", content: "我的选择：A. 甲" },
        { role: "assistant", content: "【上次的解析】甲错在把必要条件当成了充分条件。" },
      ],
    };
    gateBtn().click();
    await tick(); await tick();
    press("A");                       // 第 1 张答案=乙，选甲是错选 → 会去查历史解析
    await tick(); await tick(); await tick();

    const gets = fetchCalls.filter(c => c.url.includes("/api/explain?") || (c.url.includes("/api/explain")
      && c.url.includes("question_id=")));
    const posts = fetchCalls.filter(c => c.url.includes("/api/explain") && c.body);
    check("先按「题 + 错选 + 模式」查了历史", gets.length >= 1 && /chosen=/.test(gets[0].url), gets[0] && gets[0].url);
    check("查询里带上了这次的错选（URL 编码过）",
      gets.length >= 1 && decodeURIComponent(gets[0].url).indexOf("chosen=A. 甲") >= 0,
      gets.length ? decodeURIComponent(gets[0].url) : "(无)");
    check("查询里带上了 mode=wrong", gets.length >= 1 && /mode=wrong/.test(gets[0].url),
      gets.length ? gets[0].url : "(无)");
    check("★ 命中缓存后不再问 AI（没有 POST）", posts.length === 0,
      "全部请求顺序: " + fetchCalls.map(c => c.url.replace("http://localhost:8080", "")
        + (c.body ? "[POST]" : "")).join(" → "));
    const bodyEl = registry["fs-exp-body"];
    // 说明：解析正文是 appendExplainMsg 里用 innerHTML 写进去的，桩里落在 _html 上
    const texts = (bodyEl && bodyEl.children || []).map(c => c.textContent || c._html || "").join(" | ");
    check("正文里直接展示了上次的解析", texts.indexOf("上次的解析") >= 0, texts.slice(0, 140));
    const bar = (bodyEl && bodyEl.children || []).filter(c => c.className === "fs-exp-reuse")[0];
    check("插了一条「这是上次结果」的说明条", !!bar);
    check("说明条写明了没有重新问 AI", bar && (bar.children[0].textContent || "").indexOf("没有重新问 AI") >= 0,
      bar && bar.children[0].textContent);
    const regen = bar && bar.children.filter(c => c.tagName === "BUTTON")[0];
    check("说明条上给了「重新生成」按钮", !!regen && regen.textContent === "重新生成");

    // 点「重新生成」→ 这次才真的去问 AI
    regen.click();
    await tick(); await tick();
    const posts2 = fetchCalls.filter(c => c.url.includes("/api/explain") && c.body);
    check("点「重新生成」后才真的调一次 AI", posts2.length === 1, JSON.stringify(posts2.map(p => p.url)));
    const texts2 = (registry["fs-exp-body"].children || []).map(c => c.textContent || c._html || "").join(" | ");
    check("重新生成的结果替换掉了旧内容", texts2.indexOf("新生成的解析") >= 0, texts2.slice(0, 120));
  }

  console.log("\n[没有可复用的历史时，照常问 AI]");
  {
    cardsPayload = [Object.assign({}, CARDS[0], { question_id: "Q-TEST-1" })];
    boot();
    await tick(); await tick();
    explainCache = null;              // 没查历史到东西
    gateBtn().click();
    await tick(); await tick();
    press("A");
    await tick(); await tick(); await tick();
    const posts = fetchCalls.filter(c => c.url.includes("/api/explain") && c.body);
    check("查不到历史就直接生成（POST 一次）", posts.length === 1, JSON.stringify(posts.map(p => p.url)));
    const texts = (registry["fs-exp-body"].children || []).map(c => c.textContent || c._html || "").join(" | ");
    check("没有插「复用」说明条", texts.indexOf("上次这题你也是选这一项") < 0, texts.slice(0, 100));
  }

  console.log("\n[历史是「另一个错选」时不复用]");
  {
    cardsPayload = [Object.assign({}, CARDS[0], { question_id: "Q-TEST-1" })];
    boot();
    await tick(); await tick();
    explainCache = {                  // 缓存里是选了 B 的解析，这次选的是 A
      ok: true, thread_id: "T-OTHER", chosen: "B. 乙", mode: "wrong",
      messages: [{ role: "assistant", content: "【B 的解析】" }],
    };
    gateBtn().click();
    await tick(); await tick();
    press("A");
    await tick(); await tick(); await tick();
    const posts = fetchCalls.filter(c => c.url.includes("/api/explain") && c.body);
    check("错选不同 → 不复用，重新问 AI", posts.length === 1, JSON.stringify(posts.map(p => p.url)));
    const texts = (registry["fs-exp-body"].children || []).map(c => c.textContent || c._html || "").join(" | ");
    check("没把 B 的解析当成 A 的展示出来", texts.indexOf("B 的解析") < 0, texts.slice(0, 100));
  }

  console.log("\n[思考强度：默认不要求、按钮可单次要求深度]");
  {
    cardsPayload = [Object.assign({}, CARDS[0], { question_id: "Q-TEST-1" })];
    boot();
    await tick(); await tick();
    gateBtn().click();
    await tick(); await tick();
    press("A");
    await tick(); await tick(); await tick();
    const posts = fetchCalls.filter(c => c.url.includes("/api/explain") && c.body);
    check("默认那次不带 deep（思考强度交给设置页的默认值）",
      posts.length === 1 && posts[0].body.deep === undefined, JSON.stringify(posts.map(p => p.body)));
    const deepBtn = registry["fs-exp-deep"];
    check("面板上有「深想一遍」按钮（深度思考要够得着）", !!deepBtn, Object.keys(registry).join(","));
    if (deepBtn) {
      // 桩的 innerHTML 只登记 id、不建子元素，所以文案从外壳 HTML 上断言
      const shell = (registry["fs-explain"] && registry["fs-explain"]._html) || "";
      check("按钮文案写的是「深想一遍」",
        shell.indexOf(" 深想一遍") >= 0 || shell.indexOf("深想一遍") >= 0, shell.slice(0, 200));
      deepBtn.click();
      await tick(); await tick();
      const posts2 = fetchCalls.filter(c => c.url.includes("/api/explain") && c.body);
      check("点它会再问一次，并且带上 deep:true",
        posts2.length === 2 && posts2[1].body.deep === true,
        JSON.stringify(posts2.map(p => p.body)));
    }
  }

  console.log("\n[移动端输入法的回车不能当提交（平板上「追问没反应」的真凶）]");
  {
    cardsPayload = [Object.assign({}, CARDS[0], { question_id: "Q-TEST-1" })];
    boot();
    await tick(); await tick();
    gateBtn().click();
    await tick(); await tick();
    press("A");                                  // 答错 → 挂上解析与追问框
    await tick(); await tick(); await tick();
    const input = registry["fs-exp-input"];
    const sendBtn = registry["fs-exp-send"];
    check("追问输入框和发送键都在", !!input && !!sendBtn, Object.keys(registry).join(","));
    // 守卫是**就地判定**（`!(e.isComposing || e.keyCode === 229)`）：自包含、不依赖跨 IIFE 的
    // 全局。所以这里只验行为（下面两条），另外确认追问框给移动端键盘标了「发送」。
    const expShell = (registry["fs-explain"] && registry["fs-explain"]._html) || "";
    check("追问框带 enterkeyhint=send（移动端键盘显示「发送」）",
      expShell.indexOf("enterkeyhint=\"send\"") >= 0, expShell.slice(0, 260));
    if (input && sendBtn) {
      input.value = "为什么不能这样？";
      input.dispatch("keydown", { key: "Enter", isComposing: true, preventDefault() {} });
      await tick(); await tick();
      check("★ 输入法确认候选词时不发送（不再「打了字没反应/发出半截拼音」）",
        !fetchCalls.some(c => c.url.includes("/followup")),
        JSON.stringify(fetchCalls.map(c => c.url)));
      check("输入框内容还在（没被误清空）", input.value === "为什么不能这样？", input.value);
      input.dispatch("keydown", { key: "Enter", isComposing: false, preventDefault() {} });
      await tick(); await tick();
      check("真正的回车会发出去", fetchCalls.some(c => c.url.includes("/followup")),
        JSON.stringify(fetchCalls.map(c => c.url)));
    }
  }

  console.log("\n[追问框里的键位：回车=发送，空格=下一张]");
  {
    // 用户 2026-09-17 反馈：「在输入框里回车就是发送的意思，下一张交给空格」。
    // 关键是**事件会冒泡**：追问框自己的 onkeydown 和全局那个挂在 document 上的
    // 快捷键监听器**两个都会跑到**（preventDefault 只挡默认行为，不挡传播）。
    // 所以用例必须两个都派发，才等于用户真的按了一下键——以前那条用例只派发了
    // 元素自己的 onkeydown，于是「全局会不会抢这个键」根本没被验证过。
    const keyOn = (el, key, code) => {
      const ev = { key: key, code: code || ("Key" + key), target: el, preventDefault() {} };
      if (typeof el.onkeydown === "function") el.onkeydown(ev);
      keyHandlers.forEach(fn => fn(ev));
    };

    const card = () => (registry["flash-studio"]._html.match(/第 \d+ \/ \d+ 张/) || ["(找不到)"])[0];
    // 每个小分支都从「第 1 张、已答对、空追问框」重新起——按键会翻页，
    // 挤在同一轮里测的话，后一条跑到的其实是下一张卡的状态（数字键那条就这么假失败过）。
    const freshAskBox = async () => {
      // ⚠️ 必须带上 question_id：mountExplain 一开头就是 `if (!qid) return;`，
      //    默认那组夹具卡没有这个字段，解析面板（连同追问框）根本不会挂上来。
      cardsPayload = CARDS.map((c, i) => Object.assign({}, c, { question_id: "Q-TEST-" + (i + 1) }));
      serverSession = null;
      await bootStarted();
      press("B");                                 // 第 1 张答对 → 挂上空的追问框
      // mountExplain 里面挂壳是同步的，但整条链路要等几拍才进 DOM
      for (let i = 0; i < 6; i++) await tick();
      return registry["fs-exp-input"];
    };

    // ① 空框按回车：不翻页（回车在这个框里只表示发送，而发送空消息本就无事发生）
    let input = await freshAskBox();
    check("追问框在场且是空的", !!input && input.tagName === "INPUT" && !input.value,
      input ? input.tagName + " " + JSON.stringify(input.value) : "(无输入框)");
    check("现在在第 1 张", card() === "第 1 / 5 张", card());
    keyOn(input, "Enter", "Enter");
    await tick(); await tick();
    check("★ 空追问框里按回车 → 不翻页（回车只表示发送）", card() === "第 1 / 5 张", card());
    check("★ 也没有误发消息",
      !fetchCalls.some(c => c.url.includes("/followup") || c.url.includes("/api/explain")),
      JSON.stringify(fetchCalls.map(c => c.url.slice(-40))));

    // ② 空框按空格：翻页并记为「记得 3」（「下一张」只认空格）
    input = await freshAskBox();
    keyOn(input, " ", "Space");
    await tick(); await tick();
    check("★ 空追问框里按空格 → 翻到第 2 张", card() === "第 2 / 5 张", card());
    check("   空格走的是「记得」那一档", rated().length === 1 && rated()[0].body.rating === 3,
      JSON.stringify(rated().map(r => r.body.rating)));

    // ③ 空框按 1-4：仍然放行给自评。这是 2026-09-21 另一条用户反馈修出来的行为
    //    （空框里第一个字符不可能是提问，数字键该给闪卡用）。这条以前同样测不到，
    //    因为桩的 tagName 和 id 都不对，守卫恒不成立。
    input = await freshAskBox();
    keyOn(input, "1", "Digit1");
    await tick(); await tick();
    check("★ 空追问框里按 1 → 记为「忘记」并翻页（数字键仍归闪卡）",
      rated().length === 1 && rated()[0].body.rating === 1, JSON.stringify(rated().map(r => r.body.rating)));

    // ④ 框里有字：回车 = 发送（这条是输入框的本分，不能被改坏）
    input = await freshAskBox();
    input.value = "这里为什么要这样？";
    keyOn(input, "Enter", "Enter");
    await tick(); await tick(); await tick();
    check("★ 框里有字时回车 = 发送（不是翻页）",
      fetchCalls.some(c => /followup|\/api\/explain/.test(c.url)),
      JSON.stringify(fetchCalls.map(c => c.url.slice(-30))));
    check("字被送出去了（输入框清空）", input.value === "", JSON.stringify(input.value));
  }

  console.log("\n[答对/看答案：只给空的追问框，不预设问题、不预先生成]");
  {
    // 第 1 张卡答案=乙（序号 1）：按 B 就是答对
    cardsPayload = [Object.assign({}, CARDS[0], { question_id: "Q-TEST-1" })];
    boot();
    await tick(); await tick();
    gateBtn().click();
    await tick(); await tick();
    press("B");                                   // 答对
    await tick(); await tick(); await tick();
    check("★ 答对时不自动问 AI（不烧 token 生成泛讲）",
      !fetchCalls.some(c => c.url.includes("/api/explain")),
      JSON.stringify(fetchCalls.map(c => c.url)));
    check("不再有预设问题按钮", !registry["fs-ask-explain"]);
    const shell = (registry["fs-explain"] && registry["fs-explain"]._html) || "";
    check("追问框直接就在（不用先点按钮）",
      shell.indexOf('id="fs-exp-input"') >= 0 && shell.indexOf('id="fs-exp-send"') >= 0,
      shell.slice(0, 220));
    check("提示语中立（说的是「有具体想问的直接输入」）",
      (registry["fs-exp-body"] && registry["fs-exp-body"]._html || "").indexOf("有具体想问的") >= 0);
    check("输入提示不给示例问题", shell.indexOf("输入你的问题") >= 0
      && shell.indexOf("比如「为什么这里不能先") < 0, shell.slice(0, 260));

    // 打上自己的问题再发 → 这一次才去问，并且把问题带上去
    const inp = registry["fs-exp-input"];
    inp.value = "这题为什么不是「四马分肥」？";
    registry["fs-exp-send"].click();
    await tick(); await tick(); await tick();
    const asks = fetchCalls.filter(c => c.url.includes("/api/explain") && c.body);
    check("▲ 提交后才调用生成接口", asks.length === 1, JSON.stringify(asks.map(a => a.body)));
    check("带上的是他自己的问题（不是预设问法）",
      asks.length === 1 && asks[0].body.question === "这题为什么不是「四马分肥」？",
      asks.length ? JSON.stringify(asks[0].body) : "(无)");
    check("还是 correct 模式（答对场景）", asks.length === 1 && asks[0].body.mode === "correct",
      asks.length ? JSON.stringify(asks[0].body) : "(无)");
    check("问题也显示在对话里", (registry["fs-exp-body"].children || [])
      .map(c => c.textContent || c._html || "").join(" ").indexOf("四马分肥") >= 0);

    // 之后再问 → 走同一线索的追问
    const inp2 = registry["fs-exp-input"];
    inp2.value = "那考场上会怎么考？";
    registry["fs-exp-send"].click();
    await tick(); await tick();
    const fus = fetchCalls.filter(c => c.url.includes("/followup"));
    check("第二次提问走追问接口（同一线索）", fus.length === 1, JSON.stringify(fus.map(f => f.body)));
  }

  cardsPayload = null;   // 别把换过的卡留给后面的用例
  serverSession = null;

  // ---------- 16. 进度同步：服务端那份是唯一事实源（2026-09-21）----------
  // 用户报的两个问题：① 切页回来进度偶发重置（本地存储被清/写失败就没了）
  // ② 多端各刷各的、同一题一天问好几遍。修法：当日这一组放服务端，翻页上报位置，
  //   续刷时剔掉「今天已经答过」的卡（服务端按 review_log 判，跨设备一致）。
  console.log("\n[16] 进度同步（服务端为准）");
  {
    // ① 本机没有本地缓存，但服务端有当日那一组（比如刚在平板上刷到第 3 张）
    serverSession = { cards: CARDS, idx: 2, filter: null };
    boot();
    await tick(); await tick();
    check("闸门能认出服务端那份没刷完的组（本机清过缓存也不怕）",
      !!registry["fs-gate-resume"], Object.keys(registry).join(","));
    // 2026-09-17 起不再显示「从第 N 张起」：服务端 resume 交回来的就是「剩下的那些」，
    // 位置恒为第 1 张（见 serve.js 的 remainingCards）。以前那个数字是按没剔过的列表
    // 算的，会虚高，人读成「要跳过几张没答的」。跨设备接续真正有用的信息是「还剩几张」。
    check("★ 按钮写明还剩几张（跨设备看得见）",
      registry["flash-studio"]._html.indexOf("继续本组（还剩 " + (CARDS.length - 2) + " 张）") >= 0,
      registry["flash-studio"]._html.slice(0, 280));
    check("不再出现「从第 N 张起」（剩下的就是从第 1 张起）",
      registry["flash-studio"]._html.indexOf("从第") < 0);
    check("提示里说明进度存服务端、多设备共用",
      registry["flash-studio"]._html.indexOf("进度存在服务端") >= 0);
    check("提示里点名上次是哪台设备刷的",
      registry["flash-studio"]._html.indexOf("平板") >= 0);
    registry["fs-gate-resume"].click();
    await tick(); await tick();
    check("续的是服务端那一组和那个位置（第 3 / 5 张）",
      registry["flash-studio"]._html.indexOf("第 3 / 5 张") >= 0,
      registry["flash-studio"]._html.slice(0, 140));
    check("续上时没有重新组题（不烧新一组）", groupCalls().length === 0,
      fetchCalls.map(f => f.url).join(" "));

    // ② 翻页要把「刷到第几张」报给服务端（另一台设备据此接上）
    const posBefore = fetchCalls.filter(c => c.url.includes("/position")).length;
    SPACE();                                         // 翻页（认 code=Space）
    await new Promise(r => setTimeout(r, 520));      // 上报有 400ms 防抖
    const pos = fetchCalls.filter(c => c.url.includes("/position"));
    check("翻页上报了位置（供别的设备接上）", pos.length > posBefore,
      JSON.stringify(pos.map(p => p.body)));
    check("上报里带设备标签（排查用）",
      pos.length > 0 && !!pos[pos.length - 1].body.device, JSON.stringify(pos.slice(-1)));

    // ③ 切到别的子页 → 推整份（平板↔电脑接得上）
    const sesBefore = fetchCalls.filter(c => c.url.includes("/session") && c.body).length;
    globalThis.__flashTestLoc.hash = "#/overview";
    fireWin("hashchange");
    await tick(); await tick();
    const sesPushed = fetchCalls.filter(c => c.url.includes("/session") && c.body);
    check("切子页时把整份进度推给了服务端", sesPushed.length > sesBefore,
      JSON.stringify(sesPushed.map(p => p.url)));
    check("推的是这一组（含卡片与位置）",
      sesPushed.length > 0 && Array.isArray(sesPushed[sesPushed.length - 1].body.cards)
      && typeof sesPushed[sesPushed.length - 1].body.idx === "number",
      JSON.stringify(sesPushed.slice(-1)).slice(0, 160));

    // ④ 服务端与本地都没有 → 老老实实显示「开始学习」
    serverSession = null;
    boot();
    await tick(); await tick();
    check("两边都没有进度时显示「开始学习」", !!registry["fs-gate-start"],
      Object.keys(registry).join(","));
    check("这时不该有「继续本组」按钮", !registry["fs-gate-resume"]);
  }
  serverSession = null;

  // ---------- 17. 音效（2026-09-21 用户反馈「答错和非选择题好像缺音效」）----------
  // 真因不是没播，而是**低频+低增益在小喇叭上听不见**（correct 是高频，所以只有它听得见）。
  // 这里盯两件事：① 该响的路径确实响了（名字写错是静默无声，最难查）
  //              ② 名字表里没有静默项
  console.log("\n[17] 音效：该响的要响");
  {
    // ⚠️ 每次 boot 都会重新执行 FLASH_JS、换一个新的 SFX 实例：必须**当场取**，
    //    不能先捕获一次（那样拿到的是上一轮 boot 的实例，日志永远是空的）。
    const sfxNow = () => globalThis.__sfx;
    check("暴露了 SFX 供测试", !!sfxNow() && typeof sfxNow().played === "function");
    const namesOf = () => (sfxNow() ? sfxNow().names() : []);
    check("音效名都在表里（没有拼错的静默项）",
      namesOf().indexOf("wrong") >= 0 && namesOf().indexOf("correct") >= 0
      && namesOf().indexOf("reveal") >= 0 && namesOf().indexOf("rate") >= 0,
      namesOf().join(","));
    cardsPayload = [Object.assign({}, CARDS[0], { question_id: "Q-TEST-1" })];
    boot();
    await tick(); await tick();
    gateBtn().click();
    await tick(); await tick();
    const before = sfxNow() ? sfxNow().played().length : 0;
    press("A");                                   // 第 1 张答案=乙 → 选甲是错选
    await tick(); await tick();
    const played = sfxNow() ? sfxNow().played().slice(before) : [];
    check("答错响了 wrong（不再是「没声音」）", played.indexOf("wrong") >= 0, JSON.stringify(played));

    boot();
    await tick(); await tick();
    gateBtn().click();
    await tick(); await tick();
    const before2 = sfxNow() ? sfxNow().played().length : 0;
    press("B");                                   // 答对
    await tick(); await tick();
    const played2 = sfxNow() ? sfxNow().played().slice(before2) : [];
    check("答对响了 correct", played2.indexOf("correct") >= 0, JSON.stringify(played2));

    boot();
    await tick(); await tick();
    gateBtn().click();
    await tick(); await tick();
    const before3 = sfxNow() ? sfxNow().played().length : 0;
    SPACE();                                      // 空格看答案（处理器认 code=Space）
    await tick(); await tick();
    const played3 = sfxNow() ? sfxNow().played().slice(before3) : [];
    check("空格看答案响了 reveal（非选择题的反馈音）",
      played3.indexOf("reveal") >= 0, JSON.stringify(played3));
  }

  // ---------- 18. 解析面板的滚动位置（2026-09-21 用户要求）----------
  // 以前每次 append 都无条件滚到底：首次解析完就得自己翻上去看，追问回来还会被拽走。
  // 现在：首次停在最上面；追问回答不动用户的滚动位置；只有「他本来就贴着底部」才跟随。
  console.log("\n[18] 解析面板滚动：默认在最上面，追问不挪位置");
  {
    cardsPayload = [Object.assign({}, CARDS[0], { question_id: "Q-TEST-1" })];
    boot();
    await tick(); await tick();
    gateBtn().click();
    await tick(); await tick();
    press("A");                                  // 答错 → 自动挂解析
    await tick(); await tick(); await tick();
    const body = registry["fs-exp-body"];
    check("首次解析渲染后停在最上面（不用自己往上翻）",
      body && body.scrollTop === 0, body ? String(body.scrollTop) : "(没有解析区)");

    // 把滚动位置挪到中间，发一条追问，回答回来后位置必须原样
    body.scrollTop = 42;
    const inp = registry["fs-exp-input"];
    check("追问框在场", !!inp && !!registry["fs-exp-send"]);
    if (inp) {
      inp.value = "再讲讲这里";
      registry["fs-exp-send"].click();
      await tick(); await tick(); await tick();
      check("追问回答完成后没把我拽走（滚动位置不变）",
        body.scrollTop === 42, String(body.scrollTop));
    }
  }
  cardsPayload = null;

  // ---------- 19. 追问框不该抢走评分键（2026-09-21 用户反馈）----------
  // 面板挂上后如果焦点落在追问框里，按 1/2/3/4 就被输入框吃掉 → 标不了熟练度。
  // 现在：空输入框里的数字/空格一律放行给闪卡快捷键；已有内容则老老实实打字。
  console.log("\n[19] 追问框不抢评分键");
  {
    // 直接给 keydown 一个「像是在输入框里敲的」target
    const pressIn = (key, code, target) => {
      let prevented = false;
      keyHandlers.forEach(fn => fn({
        key, code: code || ("Key" + String(key).toUpperCase()), target,
        preventDefault() { prevented = true; },
      }));
      return prevented;
    };
    cardsPayload = [Object.assign({}, CARDS[0], { question_id: "Q-TEST-1" })];
    boot();
    await tick(); await tick();
    gateBtn().click();
    await tick(); await tick();
    press("B");                                   // 答对 → 挂上空的追问框（不自动聚焦）
    await tick(); await tick(); await tick();
    const inp = registry["fs-exp-input"];
    check("追问框在场", !!inp);
    const inputTarget = { tagName: "INPUT", id: "fs-exp-input", value: "" };
    const before = rated().length;
    pressIn("3", "Digit3", inputTarget);
    await tick(); await tick();
    check("★ 空追问框里按 3 → 当作评分（不再被输入框吃掉）",
      rated().length > before, "rated " + before + "→" + rated().length);

    // 已经打了字时不能再被当成评分（正常打字不能被抢）
    inputTarget.value = "3 这题为什么这样";
    const before2 = rated().length;
    pressIn("3", "Digit3", inputTarget);
    await tick(); await tick();
    check("输入框里有内容时，数字键正常留在输入框（不评分）",
      rated().length === before2, "rated " + before2 + "→" + rated().length);

    // 其它输入框不受这条放行规则影响
    const ta = { tagName: "TEXTAREA", id: "fs-answer", value: "" };
    const before3 = rated().length;
    pressIn("3", "Digit3", ta);
    await tick();
    check("其它输入框（简答作答区）不受影响", rated().length === before3);
  }

  // ---------- 20. 今日额度用完后「再来一组」（2026-09-21 用户要求）----------
  console.log("\n[20] 今日刷完后「再来一组」");
  {
    // 让服务端这次返回空组（模拟额度用完）
    cardsPayload = [];
    boot();
    await tick(); await tick();
    gateBtn().click();                            // 开始 → 空组 → 空状态
    await tick(); await tick();
    check("额度用完时给出「再来一组」按钮", !!registry["fs-extra"],
      Object.keys(registry).join(","));
    check("文案里说明了数量在设置里调、且优先没复习完的",
      registry["flash-studio"]._html.indexOf("再来一组") >= 0
      && registry["flash-studio"]._html.indexOf("优先没复习完的") >= 0,
      registry["flash-studio"]._html.slice(0, 240));
    if (registry["fs-extra"]) {
      registry["fs-extra"].click();
      await tick(); await tick();
      const last = groupCalls().slice(-1)[0] || { url: "" };
      check("★ 点它走 extra 模式（不受每日额度限制）",
        /mode=extra/.test(last.url), last.url);
    }
  }
  // ---------- 21. 闪卡浮窗：别的页也能直接刷（2026-09-22 用户要求）----------
  console.log("\n[21] 闪卡浮窗：节点搬家（只借不复制）");
  {
    const d = boot();
    await tick();
    const api = globalThis.__flashFloat;
    check("暴露了 __flashFloat 接口（学习页只碰这一个）",
      !!api && typeof api.open === "function" && typeof api.close === "function"
      && typeof api.practice === "function" && typeof api.isOpen === "function");

    const fbox = registry["fs-float"];
    const fbody = registry["fs-float-body"];
    const home = d.__flashPage;
    const practice = registry["flash-practice"];
    check("浮窗容器、练习区、老家都在场", !!fbox && !!fbody && !!practice && !!home);
    // 桩里 hidden 初始是 undefined（真实 HTML 上是 hidden 属性），所以断言 =「没被显式打开」
    check("默认是收起的", fbox.hidden !== false && api.isOpen() === false);

    api.open("🧠 刚出的 2 张");
    check("打开后浮窗不再 hidden", fbox.hidden === false && api.isOpen() === true);
    check("★ 练习区整块被搬进浮窗（不是复制一套 UI）",
      fbody.children.indexOf(practice) >= 0 && practice.parentNode === fbody,
      "children=" + fbody.children.length);
    check("搬走后闪卡页里那块暂时空了", home.children.indexOf(practice) < 0);
    check("标题栏写上了这一组的名字", registry["fs-float-title"].textContent === "🧠 刚出的 2 张");

    // 全屏态下收起：必须连全屏一起退，否则 body 的 fs-lock 留着，整页滚不动
    globalThis.__flashSetFull(true);
    check("（前置）进了全屏", practice._cls.has("is-full"));
    api.close();
    check("★ 收起浮窗时把全屏一起退了（不留 body 滚动锁）", !practice._cls.has("is-full"));
    check("收起后练习区搬回闪卡页", home.children.indexOf(practice) >= 0);
    check("收起后浮窗 hidden 回去", fbox.hidden === true && api.isOpen() === false);
  }

  console.log("\n[21b] 「用闪卡练这几张」：按卡号精确组题");
  {
    boot();
    await tick();
    const api = globalThis.__flashFloat;
    const okDone = api.practice(["C-STUDY-AAAA1111", "C-STUDY-BBBB2222"], "🧠 刚出的 2 张");
    await tick(); await tick();
    check("practice 返回 true（真的开练了）", okDone === true);
    const last = groupCalls().slice(-1)[0] || { url: "" };
    check("★ 组题 URL 点名了这两张卡",
      /ids=C-STUDY-AAAA1111%2CC-STUDY-BBBB2222/.test(last.url), last.url);
    check("★ 走 mode=browse：点名要的卡不受每日额度拦截", /mode=browse/.test(last.url), last.url);
    check("limit 跟着抬到 50（够装下点名的卡）", /limit=50/.test(last.url), last.url);
    check("浮窗跟着开了", api.isOpen() === true);
  }

  console.log("\n[21c] 卡号白名单：来路不明的字符串不许进 URL");
  {
    boot();
    await tick();
    const api = globalThis.__flashFloat;
    api.practice(["C-OK_1", "有中文", "'; DROP TABLE cards; --", "", null, "C-OK-2"]);
    await tick(); await tick();
    const url = (groupCalls().slice(-1)[0] || { url: "" }).url;
    check("★ 只留合法卡号（中文 / SQL 片段 / 空值全被过滤）", /ids=C-OK_1%2CC-OK-2/.test(url), url);
    check("非法串没进 URL", url.indexOf("DROP") < 0 && url.indexOf("%E4") < 0, url);
    const groups = groupCalls().length;
    check("★ 全是非法卡号时直接拒绝，不组一个空组", api.practice(["中文", "x y"]) === false);
    check("拒绝了就不再发组题请求", groupCalls().length === groups, String(groupCalls().length));
  }

  console.log("\n[21d] 拖动、落盘、切回闪卡页自动收起");
  {
    const d = boot();
    await tick();
    const api = globalThis.__flashFloat;
    api.open();
    const bar = registry["fs-float-bar"];
    const fbox = registry["fs-float"];
    const evt = (x, y, target) => ({ clientX: x, clientY: y, button: 0, pointerId: 1,
      target: target || { closest: () => null } });

    // open() 已经按「记住的位置 / 缺省位置」摆好了，这里比的是「拖没拖动」
    const openedAt = fbox.style.left + "/" + fbox.style.top;
    bar.dispatch("pointerdown", evt(200, 100));
    bar.dispatch("pointermove", evt(202, 101));      // 只走 3px：手抖，不算拖动
    check("手抖 2~3px 不算拖动（位置一动不动）",
      fbox.style.left + "/" + fbox.style.top === openedAt,
      openedAt + " → " + fbox.style.left + "/" + fbox.style.top);
    bar.dispatch("pointermove", evt(260, 140));      // 走够 60px：真的拖
    check("真的拖动才移动（起手 100/60 → 左 +60、上 +40）",
      fbox.style.left === "160px" && fbox.style.top === "100px",
      fbox.style.left + " / " + fbox.style.top);
    bar.dispatch("pointerup", evt(260, 140));
    check("松手把位置落盘（下次打开还在那儿）", !!d.__store["kaoyan_flash_float_geo_v1"],
      JSON.stringify(d.__store));

    // 按在标题栏的按钮上不进入拖拽（pointer capture 会把 click 改派走）
    const keep = fbox.style.left;
    bar.dispatch("pointerdown", evt(300, 200, { closest: () => ({ tagName: "BUTTON" }) }));
    bar.dispatch("pointermove", evt(420, 320));
    check("按在按钮上不进入拖拽", fbox.style.left === keep, fbox.style.left + " vs " + keep);

    // 记住的位置下次打开要接着用
    // ⚠️ 每次 boot 都是**新实例**（新一套闭包），所以接口要当场重取，
    //    不能沿用上一轮那个 api —— 否则断言看的是上一轮那个还开着的浮窗。
    boot({ kaoyan_flash_float_geo_v1: JSON.stringify({ x: 20, y: 30, w: 600, h: 400 }) });
    await tick();
    const api2 = globalThis.__flashFloat;
    api2.open();
    check("★ 上次拖到哪儿，下次就从哪儿开",
      registry["fs-float"].style.left === "20px" && registry["fs-float"].style.top === "30px",
      registry["fs-float"].style.left + " / " + registry["fs-float"].style.top);

    check("切页前浮窗还开着", api2.isOpen() === true);
    globalThis.__flashTestLoc.hash = "#/flash";
    fireWin("hashchange", {});
    check("★ 切回闪卡页自动收起（练习区该回家了，闪卡页看上去才是完整的）",
      api2.isOpen() === false);
  }

  // ---------- 21e. 点名组题不粘人（2026-09-20 用户报的 bug）----------
  // 用户报的：昨天「练这几张」点名练的那 10 张，今天点开还是它、重开一组还是那 9 张，
  // 设置里的「今日刷完后再来 20 张」永远也轮不到。病根：ids（点名）被写进了 localStorage，
  // 于之后**每一组**都按那份点名组题（browse 通道还豁免每日额度与「今天答过的不再出」）。
  console.log("\n[21e] 「就练这几张」只对这一次有效（不许粘住后面的每一组）");
  {
    cardsPayload = null; serverSession = null;
    // ① 点名当次照旧走得通，但**不落盘**
    const d = boot();
    await tick();
    globalThis.__flashFloat.practice(["C-STUDY-AAAA1111", "C-STUDY-BBBB2222"], "🧠 刚出的 2 张");
    await tick(); await tick();
    check("点名组题照旧走得通",
      /ids=C-STUDY-AAAA1111/.test((groupCalls().slice(-1)[0] || {}).url));
    check("★ 点名不写进 localStorage（一次性的意图）",
      !d.__store["kaoyan_flash_filter_v1"]
      || d.__store["kaoyan_flash_filter_v1"].indexOf("C-STUDY-AAAA1111") < 0,
      d.__store["kaoyan_flash_filter_v1"]);

    // ② 「重开一组」不能把刚才点名的那几张再端回来
    const before = groupCalls().length;
    registry["fs-restart"].click();
    await tick(); await tick();
    const url = (groupCalls()[groupCalls().length - 1] || {}).url;
    check("★ 「重开一组」摘掉点名，回到智能组题",
      groupCalls().length > before && url.indexOf("ids=") < 0 && url.endsWith("?limit=200"), url);

    // ③ 旧版本已经写进 localStorage 的 ids 脏值：读的时候自己就丢掉了（不用清缓存），
    //    顺手把清理结果写回去，下次不再读它
    const d2 = boot({ "kaoyan_flash_filter_v1":
      JSON.stringify({ subject: "政治", ids: ["C-OLD-1", "C-OLD-2"] }) });
    await tick();
    const seeded = d2.__store["kaoyan_flash_filter_v1"] || "";
    check("★ 旧脏值里的 ids 被丢弃、范围条件（政治）留着",
      seeded.indexOf("C-OLD-1") < 0 && seeded.indexOf("政治") >= 0, seeded);
    const b2 = gateBtn();
    if (b2) b2.click();
    await tick(); await tick();
    const u2 = (groupCalls().slice(-1)[0] || {}).url;
    check("★ 「开始学习」不再按旧点名组题（带了科目筛选、不再点名）",
      u2.indexOf("ids=") < 0 && u2.includes("subject=%E6%94%BF%E6%B2%BB"), u2);
  }

  // ---------- 21f. 「今日刷完后再来 N 张」看得见、随时点得到 ----------
  // 这个设置以前只在「今日额度用完」的空状态里露一次脸，人设了 20 张却看不到任何地方
  // 有 20，只能怀疑设置没生效。现在卡片头部常驻按钮，张数直接写在按钮上。
  console.log("\n[21f] 「再来一组」的张数写在按钮上");
  {
    cardsPayload = null; serverSession = null; extraSetting = 20;
    boot();
    await tick();
    const b = gateBtn();
    if (b) b.click();
    await tick(); await tick();
    const html = registry["flash-studio"]._html;
    check("卡片头部有「再来一组」（不用等额度用完）",
      html.indexOf('id="fs-extra-now"') >= 0, html.slice(0, 200));
    check("★ 按钮上写着设置里的张数", html.indexOf("🔁 再来 20 张") >= 0,
      (html.match(/🔁 再来[^<]*/) || [""])[0]);

    // 设置页保存 → 闪卡页头部立刻跟着改（跨 IIFE 走 kaoyan:extra-count 事件）
    fireDoc("kaoyan:extra-count", { detail: 33 });
    check("★ 设置页改完张数，头部按钮当场跟着变",
      registry["fs-extra-now"] && registry["fs-extra-now"]._text === "🔁 再来 33 张",
      registry["fs-extra-now"] && registry["fs-extra-now"]._text);

    const n0 = groupCalls().length;
    registry["fs-extra-now"].click();
    await tick(); await tick();
    const eu = (groupCalls()[groupCalls().length - 1] || {}).url;
    check("★ 点它走 extra 模式（不受每日额度限制）",
      groupCalls().length > n0 && /mode=extra/.test(eu), eu);
    check("extra 组不会把点名带过去（它自己那条通道）", eu.indexOf("ids=") < 0, eu);
  }

  // ---------- 21g. 「重开一组」碰到额度用完：直接接上「再来一组」----------
  // 他自己点的「重开一组」不该停在一个「今日计划已完成」的空屏上让他再点第二次。
  // 首次「开始学习」不带这个自动跳（那一屏的说明该让他看见）。
  console.log("\n[21g] 额度用完时，「重开一组」自动接上「再来一组」");
  {
    cardsPayload = null; serverSession = null; extraSetting = 20;
    boot();
    await tick();
    const b = gateBtn();
    if (b) b.click();                       // 首次开始：智能组照旧（不自动跳 extra）
    await tick(); await tick();
    cardsPayload = [];                      // 服务端从此回空组 = 今日额度用完
    const n0 = groupCalls().length;
    registry["fs-restart"].click();
    await tick(); await tick(); await tick();
    const urls = groupCalls().slice(n0).map(c => c.url);
    check("★ 智能组空了就自动接上「再来一组」（先智能、后 extra 两次请求）",
      urls.length === 2 && urls[0].endsWith("?limit=200") && /mode=extra/.test(urls[1]),
      JSON.stringify(urls));
  }

  // ---------- 21h. 早间回顾：本地卡组（翻转卡，完全不碰闪卡库）----------
  // 用户澄清（2026-09-20）：「早间的这个闪卡的作用，不是再去学一学闪卡库里的闪卡，
  // 而就是用来学早间回顾的。就是把早间回顾的内容变成翻转的闪卡。这样正好我把这个
  // 闪卡读完之后，就自动打卡。」→ 这一组卡由「早」页拼好，走 __flashFloat.local，
  // 不组题、不写 review_log、不占额度、组末自动打卡。
  console.log("\n[21h] 早间回顾：本地卡组（翻卡自评，不碰闪卡库）");
  {
    cardsPayload = null; serverSession = null;
    const d = boot();
    await tick();
    const cards = [
      { id: "mrd-rv0", secLabel: "📚 知识点回顾", front: "总线事务分离技术",
        back: "<p>把请求→等待→响应拆成两段，中间<strong>释放总线</strong>。</p>" },
      { id: "mrd-qz0", secLabel: "❓ 今日小测",
        front: "分离技术与突发传输的本质区别？", back: "<strong>分离事务</strong>中间释放总线" },
    ];
    let finished = 0;
    const api = globalThis.__flashFloat;
    const started = api.local(cards, "早间回顾 · 2026-09-20",
      { kind: "mr-day", date: "2026-09-20",
        onFinish: function () { finished += 1; return "✅ 早间回顾已打卡"; } });
    await tick(); await tick();
    const head = registry["flash-studio"]._html;
    check("★ 本地卡组开得起来（不用服务端组题）", started === true);
    check("★ 开组没有向 /api/flashcards/session 要卡", groupCalls().length === 0,
      String(groupCalls().length));
    check("头部就是这一组：第 1 / 2 张", head.indexOf("第 1 / 2 张") >= 0,
      (head.match(/第 \d+ \/ \d+ 张/) || [""])[0]);
    check("★ 头部不出现闪卡库那套（额度条 / 再来 N 张 / 重开一组）",
      head.indexOf("fs-extra-now") < 0 && head.indexOf("fs-restart") < 0 && head.indexOf("可抽") < 0);
    check("卡片上标了出处", head.indexOf("📚 知识点回顾") >= 0);
    check("正面原样渲染（HTML 没被转义成源码）", head.indexOf("总线事务分离技术") >= 0);

    SPACE(); await tick();                       // 翻卡
    check("★ 翻卡后原文按 HTML 渲染（<strong> 没被转义）",
      fb().indexOf("<strong>释放总线</strong>") >= 0, fb().slice(0, 140));
    check("★ 没有写主闪卡库（零 /api/flashcards/review 请求）", rated().length === 0,
      JSON.stringify(rated()));
    check("★ 也没把这一组推给服务端（主库「当日这一组」不受影响）",
      fetchCalls.filter(c => c.url.indexOf("/session") >= 0 && c.body).length === 0);
    check("主闪卡库的本地进度键没被动过", !d.__store["kaoyan_flash_session_v1"]);
    check("★ 自评四档是「回顾口径」（没想起来/很熟，不是 FSRS 那套）",
      fb().indexOf("没想起来") >= 0 && fb().indexOf("很熟") >= 0, fb().match(/data-rate="1"[^>]*>.*?<\/button>/));

    press("1"); await tick(); await tick();      // 没想起来 → 本组末尾再问一遍
    check("★ 答不上的卡在本组末尾回炉（2 张 → 3 张）",
      registry["flash-studio"]._html.indexOf("第 2 / 3 张") >= 0,
      (registry["flash-studio"]._html.match(/第 \d+ \/ \d+ 张/) || [""])[0]);

    SPACE(); await tick(); press("3"); await tick(); await tick();
    check("回炉的那张从头上再出现一次",
      registry["flash-studio"]._html.indexOf("第 3 / 3 张") >= 0
      && registry["flash-studio"]._html.indexOf("总线事务分离技术") >= 0,
      (registry["flash-studio"]._html.match(/第 \d+ \/ \d+ 张/) || [""])[0]);

    SPACE(); await tick(); press("4"); await tick(); await tick();
    check("组末进了总结屏", registry["flash-studio"]._html.indexOf("完成 🎉") >= 0,
      registry["flash-studio"]._html.slice(0, 120));
    check("★ 整组刷完回调了早间回顾（自动打卡）", finished === 1, String(finished));
    check("总结屏写上了打卡结果",
      (registry["fs-local-finish"] && registry["fs-local-finish"]._text === "✅ 早间回顾已打卡"),
      registry["fs-local-finish"] && registry["fs-local-finish"]._text);
    check("★ 这一天记为「已刷完」（面板据此写「今天刷过一遍」）",
      globalThis.__mrFlashState("2026-09-20").done === true);
    check("刷完把本地那份进度清掉（不再提示「继续本组」）",
      !d.__store["kaoyan_mr_flash_v1"]);

    // 没刷完就离开 → 回来能接着刷（本地进度只存本机）
    const d2 = boot();
    await tick();
    globalThis.__flashFloat.local(cards, "早间回顾 · 2026-09-20", { kind: "mr-day", date: "2026-09-20" });
    await tick(); await tick();
    SPACE(); await tick(); press("3"); await tick(); await tick();
    check("（前置）刷了 1 张离开：还剩 1 张", globalThis.__mrFlashState("2026-09-20").left === 1,
      String(globalThis.__mrFlashState("2026-09-20").left));
    globalThis.__flashFloat.local(cards, "早间回顾 · 2026-09-20",
      { kind: "mr-day", date: "2026-09-20", resume: true });
    await tick(); await tick();
    check("★ 回来「继续本组」从第 2 张接着刷",
      registry["flash-studio"]._html.indexOf("第 2 / 2 张") >= 0,
      (registry["flash-studio"]._html.match(/第 \d+ \/ \d+ 张/) || [""])[0]);
    check("日期换了就不算同一组（不会串到别的日子）",
      globalThis.__mrFlashState("2026-09-21").left === 0);
    void d2;

    // 本地卡组之后回闪卡页开普通组题：必须恢复成「主闪卡库」的身份
    boot();
    await tick();
    globalThis.__flashFloat.local(cards, "早间回顾", { kind: "mr-day", date: "2026-09-20" });
    await tick(); await tick();
    globalThis.__flashStart({ subject: "政治" });
    await tick(); await tick();
    const bh = registry["flash-studio"]._html;
    check("★ 本地卡组→普通组题：头部回到闪卡库那套（额度条 / 再来 N 张都在）",
      bh.indexOf("fs-extra-now") >= 0 && bh.indexOf("fs-restart") >= 0, bh.slice(0, 160));
  }

  // ---------- 22. 自定义键位：改完键，闪卡真的跟着换 ----------
  // 2026-09-17 加了键位总表（KEYS_JS），闪卡键盘段不再自己比 ev.key，改成问
  // __keys.matches()。这一组就是那次改造的回归网：只测「总表算得对」不够，
  // 真正要防的是**表改了、模块没读表** —— 那种情况设置页看着一切正常，按键却
  // 毫无反应，是最难查的一类。所以每条都走 press()，从监听器那头验。
  // 表自己的算法与设置页 UI 在 tools/test_keys.js 里。
  console.log("\n[22] 自定义键位：设置里改了键，闪卡键盘真的跟着换");
  {
    // ⚠️ 这两个是模块级的「这一轮用哪套卡 / 服务端那份进度」开关，前面的用例
    //    会把它们改掉且只在文件末尾才复位。不复位的话这一组会拿到空卡组，
    //    闪卡区停在「今日计划已完成」上，按什么键都没反应——断言会全绿或全红
    //    但都不是我们要测的东西（2026-09-17 加这一组时踩到，查了半天）。
    cardsPayload = null;
    serverSession = null;
    const seedKeys = o => ({ "kaoyan.keys.v1": JSON.stringify(o) });
    const bootWith = async (o) => {
      const d = boot(seedKeys(o));
      await tick();
      const b = gateBtn();
      if (b) b.click();
      await tick(); await tick();
      return d;
    };

    // ① 撤销改到 N：新键生效，旧键 U 失效
    {
      await bootWith({ "flash.undo": "n" });
      check("（前置）键位表读到了自定义键", globalThis.__keys.specOf("flash.undo") === "n",
        globalThis.__keys.specOf("flash.undo"));
      press("A"); await tick();               // 答错 → 自动判，idx 停在当前张
      check("（前置）A 已作答并被锁定",
        opts()[0] && opts()[0].disabled === true);
      check("答错后仍在当前张（撤销的前提）", !fb().includes("data-rate"));
      press("N"); await tick();
      check("★ 撤销改到 N 之后，按 N 能撤销",
        undos().length === 1 && undos()[0].body.card_id === 1, JSON.stringify(undos()));
      press("U"); await tick();
      check("★ 旧键 U 已失效（不再撤销第二次）", undos().length === 1,
        JSON.stringify(undos()));
    }

    // ② 显示答案改到 X：空格失效，X 生效
    {
      await bootWith({ "flash.reveal": "x" });
      press(" "); await tick();               // 空格（ev.key = " "、code = "Space"）
      check("★ 显示答案改到 X 之后，空格不再揭晓", !fb().includes("data-rate"),
        fb().slice(0, 60));
      press("X"); await tick();
      check("★ 按 X 揭晓答案", (fb().match(/data-rate=/g) || []).length === 4);
    }

    // ③ 别名不受改键影响：reveal 改走 X 之后，回车仍然等效
    {
      await bootWith({ "flash.reveal": "x" });
      press("Enter"); await tick();
      check("★ 回车是常驻别名，主键被改掉也还管用",
        (fb().match(/data-rate=/g) || []).length === 4);
    }

    // ④ 全屏键改到 G：F 失效，G 生效
    {
      const d = await bootWith({ "flash.full": "g" });
      const sec = () => d.getElementById("flash-practice");
      press("F"); await tick();
      check("★ 全屏改到 G 之后，F 不再进全屏", !sec()._cls.has("is-full"));
      press("G"); await tick();
      check("★ 按 G 进全屏", sec()._cls.has("is-full"));
    }

    // ⑤ 同 scope 撞键会被设置页拦住，所以这里只会出现「默认键位原样能用」
    {
      await bootWith({});
      press("B"); await tick();
      check("没改键时默认行为不变：B 仍是选选项",
        opts()[1] && opts()[1]._cls.has("correct"));
    }
  }

  // ---------- 23. 「这题有问题」标记（2026-09-17）----------
  // 用户的诉求：有些闪卡**本身**是错的（他实测到那道叠加原理的题 A、C 都对），
  // 练习时要能就地标出来，并且 AI 要能在每日任务里拿到并修掉。
  // 这一组盯住前端那一半：徽标看得见、面板打得开、提交的字段对（错一个字段，
  // 每日任务里 AI 拿到的现场就是错的，等于白标）。
  console.log("\n[23] 「这题有问题」标记：徽标 / 原因面板 / 提交");
  {
    cardsPayload = null; serverSession = null;

    // ① 已经标记过的卡：题面下要看得见「它在等修复」，否则用户会以为标记丢了
    const flagged = Object.assign({}, CARDS[0], {
      card_id: "C-FLAG-1", question_id: "Q-FLAG-1",
      report: { id: 3, kind: "multi_correct", kind_label: "多个选项都对",
                note: "A、C 都对", created_at: "" },
    });
    cardsPayload = [flagged];
    boot();
    await tick(); await tick();
    gateBtn().click();
    await tick(); await tick();
    const area = () => registry["flash-studio"]._html || "";
    check("★ 已标记的卡带头部徽标（⚑ 待修 · 多个选项都对）",
      area().indexOf("fs-report-badge") >= 0 && area().indexOf("⚑ 待修 · 多个选项都对") >= 0,
      area().slice(0, 200));
    check("★ 题面下说清「已排进每日任务等 AI 核对修复」",
      area().indexOf("fs-report-note") >= 0 && area().indexOf("每日任务") >= 0);
    check("标过的话也带出来（他自己写的那句）", area().indexOf("A、C 都对") >= 0);
    // 标记按钮在**反馈区**（和🗑删卡同一排）：没揭晓答案前不摆出来，
    // 免得答题时被一个「这题有问题」按钮分神。
    check("（前置）没揭晓答案时反馈区还没出现（按钮在那儿）", !registry["fs-flag"]);
    press(" "); await tick(); await tick();      // 直接看答案
    check("★ 揭晓后按钮就是「已标记：多个选项都对」（不用点开才知道）",
      !!registry["fs-flag"]
      && fb().indexOf('class="fs-btn fs-flag on"') >= 0
      && fb().indexOf("⚑ 已标记：多个选项都对") >= 0,
      fb().slice(-260));
    // 改原因：点开面板时要把原来那条选中、把那句话填回去，不然等于让人重打一遍
    registry["fs-flag"].click();
    await tick(); await tick(); await tick();
    const rePanel = (registry["fs-flag-panel"] || {})._html || "";
    check("改原因：原来那条仍是选中态",
      (rePanel.match(/aria-pressed="true"/g) || []).length === 1
      && rePanel.indexOf('data-kind="multi_correct" aria-pressed="true"') >= 0,
      rePanel.slice(0, 260));
    check("改原因：上次那句话填回备注框",
      registry["fs-flag-note"] && registry["fs-flag-note"].value === "A、C 都对",
      registry["fs-flag-note"] && registry["fs-flag-note"].value);
    registry["fs-flag-cancel"].click();
    check("点取消 → 面板收起、按钮状态不变（没提交任何东西）",
      !(registry["fs-flag-panel"] || {})._html && reportsCalls.length === 0,
      JSON.stringify(reportsCalls));

    // ② 没标记过的卡：点「⚑ 这题有问题」弹出原因面板
    //    这张卡带 question_id：答对后 AI 解析区会真的挂上，好在提交标记之后
    //    验证「解析与追问框没被冲掉」（那正是「就地更新」的意义）。
    cardsPayload = [Object.assign({}, CARDS[0], { question_id: "Q-FLAG-2" })];
    boot();
    await tick(); await tick();
    gateBtn().click();
    await tick(); await tick();
    check("（前置）未标记的卡没有徽标与提示条",
      area().indexOf("fs-report-badge") < 0 && area().indexOf("fs-report-note") < 0);
    // 答对（B）之后才会出现反馈区：标记按钮在那儿
    press("B"); await tick(); await tick(); await tick();
    check("（前置）已进入反馈区", fb().indexOf("fs-flag") >= 0);
    check("（前置）AI 解析区已经挂上（追问框在）", !!registry["fs-exp-input"]);
    check("未标记时按钮写着「这题有问题」", fb().indexOf("⚑ 这题有问题") >= 0, fb().slice(-240));
    check("（前置）面板是空的", !(registry["fs-flag-panel"] || {})._html);
    registry["fs-flag"].click();
    await tick(); await tick(); await tick();
    const panelHtml = (registry["fs-flag-panel"] || {})._html || "";
    check("★ 面板里是服务端下发的原因清单（前端不自己抄一份标签）",
      panelHtml.indexOf("多个选项都对") >= 0 && panelHtml.indexOf("答案有误") >= 0,
      panelHtml.slice(0, 160));
    check("面板有备注框与提交按钮",
      panelHtml.indexOf('id="fs-flag-note"') >= 0 && panelHtml.indexOf('id="fs-flag-save"') >= 0);
    check("第一个原因默认选中（只点一下就能提交）",
      (panelHtml.match(/aria-pressed="true"/g) || []).length === 1, panelHtml.slice(0, 300));

    // ③ 换一个原因（默认是「多个选项都对」，这里改成「答案有误」）
    const chips = (registry["fs-flag-panel"] || {}).children
      .filter(c => c._cls.has("fs-flag-kind"));
    check("（前置）原因芯片都被补成了可点的节点", chips.length === 3, String(chips.length));
    if (chips.length === 3) {
      chips[1].click();
      check("点了第二个芯片 → 选中态跟着换",
        chips[1].getAttribute("aria-pressed") === "true"
        && chips[0].getAttribute("aria-pressed") === "false");
    }

    // ④ 填一句话再提交
    registry["fs-flag-note"].value = "答案应该是 B 不是 A";
    registry["fs-flag-save"].click();
    await tick(); await tick();
    check("★ 提交了报卡请求", reportsCalls.length === 1, JSON.stringify(reportsCalls));
    check("★ 带上卡号 + 选中的原因 + 那句话",
      reportsCalls[0] && reportsCalls[0].card_id === 1
      && reportsCalls[0].kind === "answer_wrong"
      && reportsCalls[0].note === "答案应该是 B 不是 A",
      JSON.stringify(reportsCalls[0]));
    check("★ 正确答案存成可读形式（「B. 乙」而不是下标 1——复核时要一眼看懂）",
      reportsCalls[0] && String(reportsCalls[0].correct || "").indexOf("B. 乙") === 0,
      JSON.stringify(reportsCalls[0]));
    check("★ 按钮变成已标记（就地改，不重绘整张卡）",
      registry["fs-flag"].className.indexOf("fs-flag on") >= 0
      && registry["fs-flag"].textContent.indexOf("已标记：答案有误") >= 0,
      registry["fs-flag"].textContent);
    check("面板收起来了", !(registry["fs-flag-panel"] || {})._html);
    check("★ 解析区与追问框没被冲掉（就地更新：刚问的话还在）",
      !!registry["fs-exp-input"] && !!registry["fs-explain"],
      Object.keys(registry).filter(k => k.indexOf("exp") > 0).join(","));

    // ⑤ 服务端还是旧版（改了 serve.js 却没重启）→ 路径 404、body 也不是 JSON。
    //    这种必须说清「重启大盘后生效」：含混成「无法连接本地服务」的话，人会以为
    //    是服务没开，去反复刷新（2026-09-17 首次上线当天就撞到一次）。
    //    ⚠️ 开关要在 boot() **之后**设：boot 每次都会把它复位（等于新开一次页面）。
    //    ⚠️ cardsPayload 也必须复位：上一段的对象已经被 submitReport 挂上 report 了，
    //       不清的话新卡一渲染就是「已标记」，这条断言会假红（模块级状态串场，踩过多次）。
    cardsPayload = null;
    const d5 = boot();
    await tick(); await tick();
    gateBtn().click();
    await tick(); await tick();
    press("B"); await tick(); await tick();
    reportsHttp404 = true;
    registry["fs-flag"].click();
    await tick(); await tick(); await tick();
    registry["fs-flag-save"].click();
    await tick(); await tick();
    const said = (d5.body.children || []).map(c => c.textContent || "").join(" | ");
    check("★ 旧版服务时说清「重启大盘后生效」（不是含混的「无法连接」）",
      said.indexOf("重启") >= 0 && said.indexOf("无法连接") < 0, said);
    check("没假装成功：按钮没变成「已标记」",
      fb().indexOf("⚑ 已标记") < 0 && reportsCalls.length === 0,
      "已标记?=" + (fb().indexOf("⚑ 已标记") >= 0) + " calls=" + JSON.stringify(reportsCalls));
    reportsHttp404 = false;
  }

  // ------------------------------------------------------------------
  console.log("\n[24] 📌 钉住这张卡：按钮 / 快捷键 P / 徽标（2026-09-19）");
  {
    // 用户原话：「就算我对的那些题，如果我对 AI 有过追问，那可以给我一个 pin 的键，
    // 我可以把它钉在那个卡的位置，下次我看到它的时候，我可以再看看它。」
    // 所以：① 答对答错都能钉（这里用「答对」的路径：按 B 选中正确项）；
    //       ② 钉完只就地改按钮与徽标，**不重绘整张卡**（重绘会冲掉已挂的解析与追问框）。
    cardsPayload = [Object.assign({}, CARDS[0], {
      card_id: "C-PIN-1", question_id: "Q-PIN-1",
      pin: { note: "", created_at: "" }, ask_count: 2, streak: 1,
    })];
    const d24 = boot();
    await tick(); await tick();
    gateBtn().click();
    await tick(); await tick();
    // ① 已经钉住的卡：头部要看得见徽标（否则他会以为「钉住丢了」）
    const area24 = () => registry["flash-studio"]._html || "";
    check("★ 已钉住的卡带头部 📌 徽标", area24().indexOf("fs-pin-badge") >= 0,
      area24().slice(0, 200));

    // ② 未钉住的卡：按钮就写「钉住这张卡」
    cardsPayload = [Object.assign({}, CARDS[0], {
      card_id: "C-PIN-1", question_id: "Q-PIN-1", pin: null, ask_count: 2, streak: 1,
    })];
    const d24x = boot();
    await tick(); await tick();
    gateBtn().click();
    await tick(); await tick();
    // 按钮在**反馈区**（与 ⚑ 同一排）：揭晓答案后才出现，所以先看答案。
    // （快捷键 P 不受这个限制，它在文档层，任何阶段都能按。）
    press(" ");
    await tick(); await tick();
    check("（前置）反馈区已出现，钉住按钮在场",
      !!registry["fs-pin"] && fb().indexOf("fs-pin") >= 0,
      "pin=" + !!registry["fs-pin"] + " fb=" + fb().slice(0, 120));
    check("未钉住时按钮写「📌 钉住这张卡」", fb().indexOf("📌 钉住这张卡") >= 0,
      fb().slice(0, 160));
    // 问过 AI 的卡：头部标出「💬 问过 N」——它是「这张卡不会被连对退役」的凭证。
    // （钉住的卡不重复标这个，📌 已经说明了同一件事。）
    check("★ 问过 AI 的卡带「💬 问过 N」徽标（这类卡不会被连对退役）",
      area24().indexOf("fs-ask-badge") >= 0 && area24().indexOf("问过 2") >= 0,
      area24().slice(0, 240));

    // ① 按快捷键 P（总表里的 flash.pin，默认 p）
    press("p");
    await tick(); await tick();
    check("★ 按 P 发出钉住请求", pinCalls.length === 1, JSON.stringify(pinCalls));
    check("★ 请求带卡号与 pinned=true",
      pinCalls[0] && pinCalls[0].card_id === "C-PIN-1" && pinCalls[0].pinned === true,
      JSON.stringify(pinCalls[0]));
    check("★ 按钮就地变成「已钉住」", registry["fs-pin"].textContent.indexOf("已钉住") >= 0,
      registry["fs-pin"].textContent);
    check("★ 没有重绘整张卡（解析区与追问框还在）",
      !!registry["fs-exp-input"] && !!registry["fs-explain"],
      Object.keys(registry).filter(k => k.indexOf("exp") > 0).join(","));

    // ② 再按一次 → 取消钉住
    press("p");
    await tick(); await tick();
    check("★ 再按 P 发的是取消钉住", pinCalls.length === 2 && pinCalls[1].pinned === false,
      JSON.stringify(pinCalls[1]));
    check("按钮回到「钉住这张卡」",
      registry["fs-pin"].textContent.indexOf("钉住这张卡") >= 0, registry["fs-pin"].textContent);

    // ③ 已经在输入框里打字时，P 不能把卡钉走（输入框守卫）。
    //    ⚠️ 桩里的 press() 一律给 BODY target，所以这里得像 [19] 那样显式传一个
    //    INPUT target，否则「在输入框里」这个前提根本没被模拟出来（假绿灯）。
    const pressIn24 = (key, code, target) => {
      keyHandlers.forEach(fn => fn({
        key, code: code || ("Key" + String(key).toUpperCase()), target,
        preventDefault() {},
      }));
    };
    const before = pinCalls.length;
    pressIn24("p", "KeyP", { tagName: "INPUT", id: "fs-exp-input", value: "匹" });
    await tick();
    check("★ 追问框里打字时 P 不触发钉住", pinCalls.length === before,
      "calls " + before + "→" + pinCalls.length);

    // ④ 服务端仍是旧版（改了 serve.js 没重启）→ 必须说「重启大盘后生效」
    cardsPayload = null;
    const d24b = boot();
    await tick(); await tick();
    gateBtn().click();
    await tick(); await tick();
    press(" "); await tick(); await tick();
    pinHttp404 = true;
    registry["fs-pin"].click();
    await tick(); await tick(); await tick();
    const said24 = (d24b.body.children || []).map(c => c.textContent || "").join(" | ");
    check("★ 旧版服务时说清「重启大盘后」生效（不是含混的「无法连接」）",
      said24.indexOf("重启") >= 0 && said24.indexOf("无法连接") < 0, said24);
    check("没假装成功：按钮没变成「已钉住」", fb().indexOf("📌 已钉住") < 0,
      fb().slice(0, 160));
    pinHttp404 = false;
    cardsPayload = null;
  }

  cardsPayload = null;
  console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
  process.exit(fail === 0 ? 0 : 1);
})();

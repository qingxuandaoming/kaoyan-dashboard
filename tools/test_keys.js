/*
 * 快捷键总表（KEYS_JS）的行为测试。
 *
 * 为什么值得单独测：这张表是**唯一真相源**，闪卡/阅读弹窗都改成问它了。表本身
 * 算错的话，症状是「设置页显示得好好的，按键就是没反应」——最难查的那类。
 * 所以这里盯四件事：
 *   ① 键名归一化。空格在 ev.key 里是 " "，不是 "Space"；写错就永远匹配不上。
 *      这是历史上真踩过的坑（一边用 ev.key、一边用 ev.code）。
 *   ② Shift 不参与匹配。F 和 Shift+F 是同一个物理键，ev.key 一个是 "f" 一个是
 *      "F"，要求精确相等的话大写状态就没反应——原代码专门判了两种写法。
 *   ③ 撞键只在同 scope 内算冲突。闪卡区和阅读弹窗互斥出现，同一个 F 各管各的。
 *   ④ 改键真的落到 localStorage，且**只存改过的**（没改的不落盘，将来默认值
 *      调整了才能跟着走）。
 *
 * 集成那一头（改完键、闪卡真的跟着换）在 tools/test_flash_keyboard.js 的
 * [22] 一节里，走真实监听器验；这里只管表自己的算法与 UI。
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

// ---- 抽出真实的 KEYS_JS（走公共脚本，它会连 DAY_START_JS 一起带上）----
const OUT = path.join(os.tmpdir(), "kaoyan_keys_extracted.js");
execFileSync("python", [path.join(__dirname, "extract_js.py"), "KEYS_JS", OUT],
  { maxBuffer: 1 << 24 });
const JS = fs.readFileSync(OUT, "utf-8");

// ---- 极简 DOM 桩：只实现 KEYS_JS 真正用到的那几个成员 ----
function mkEl(tag) {
  const el = {
    tagName: (tag || "div").toUpperCase(),
    id: "", children: [], className: "", textContent: "", title: "", type: "",
    disabled: false, onclick: null, _html: "",
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      contains(c) { return this._s.has(c); },
      remove(c) { this._s.delete(c); },
    },
    appendChild(c) { this.children.push(c); return c; },
  };
  // innerHTML = "" 是 KEYS_JS 清空表格的写法，清的时候子节点也要跟着没
  Object.defineProperty(el, "innerHTML", {
    get() { return el._html; },
    set(v) { el._html = String(v); if (el._html === "") el.children = []; },
  });
  return el;
}
const hasClass = (el, c) =>
  el.classList.contains(c) || String(el.className || "").split(/\s+/).indexOf(c) >= 0;
function walk(el, fn) {
  fn(el);
  (el.children || []).forEach(c => walk(c, fn));
}
function allByTag(root, tag) {
  const out = [];
  walk(root, el => { if (el.tagName === tag.toUpperCase()) out.push(el); });
  return out;
}
function allByClass(root, cls) {
  const out = [];
  walk(root, el => { if (hasClass(el, cls)) out.push(el); });
  return out;
}

// 跑一次 KEYS_JS，返回那一轮的实例与工具。每次调用 = 新开一次页面。
function runKeys(seedStore) {
  const store = {};
  if (seedStore) for (const k of Object.keys(seedStore)) store[k] = String(seedStore[k]);

  const box = mkEl("div");
  box.id = "set-keys";                       // renderUI 是按 id 找容器的
  const findById = (el, id) => {
    if (el.id === id) return el;
    for (const c of el.children || []) {
      const hit = findById(c, id);
      if (hit) return hit;
    }
    return null;
  };

  const docListeners = {};
  const doc = {
    getElementById: id => findById(box, id),
    createElement: t => mkEl(t),
    addEventListener(ev, fn) { (docListeners[ev] = docListeners[ev] || []).push(fn); },
    removeEventListener(ev, fn) {
      docListeners[ev] = (docListeners[ev] || []).filter(f => f !== fn);
    },
    dispatchEvent() {},
  };
  const localStorage = {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; },
  };
  const CustomEvent = function (t) { this.type = t; };

  // 只跑那一块（KEYS_JS 是自执行 IIFE，跑完就把 __keys 挂上 globalThis）
  new Function("document", "localStorage", "CustomEvent", JS)(doc, localStorage, CustomEvent);

  return {
    keys: globalThis.__keys, box, store,
    fireKey(ev) {
      const list = (docListeners.keydown || []).slice();
      list.forEach(fn => fn(Object.assign({
        preventDefault() {}, stopPropagation() {}, ctrlKey: false, altKey: false,
      }, ev)));
    },
    hasKeyListener: () => (docListeners.keydown || []).length > 0,
  };
}

const chipFor = (box, label) =>
  allByTag(box, "button").filter(b => {
    const name = allByTag(box, "div").filter(d => hasClass(d, "key-name") && d.textContent === label)[0];
    return name && b.parent && b.parent.children.indexOf(name.parent) >= 0;
  })[0];

(async () => {

  // ---------- 1. 键名归一化：空格那个坑 ----------
  console.log("\n[1] 键名归一化（空格在 ev.key 里是 \" \"，不是 \"Space\"）");
  {
    const { keys } = runKeys();
    // 真实浏览器按空格的形状：key 是一个空格字符，code 是 "Space"
    check("★ 空格（ev.key=' '）能匹配默认的显示答案键",
      keys.matches("flash.reveal", { key: " ", code: "Space" }) === true);
    check("老浏览器给了 'Spacebar' 也认",
      keys.matches("flash.reveal", { key: "Spacebar" }) === true);
    check("回车是常驻别名，同样匹配",
      keys.matches("flash.reveal", { key: "Enter" }) === true);
    check("不相关的键不匹配", keys.matches("flash.reveal", { key: "q" }) === false);
  }

  // ---------- 2. Shift 不参与匹配 ----------
  console.log("\n[2] Shift 不参与匹配（F 与 Shift+F 是同一个物理键）");
  {
    const { keys } = runKeys();
    check("★ 小写 f 匹配全屏", keys.matches("flash.full", { key: "f" }) === true);
    check("★ 大写 F（按住 Shift）也匹配全屏",
      keys.matches("flash.full", { key: "F", shiftKey: true }) === true);
    check("大写 U（按住 Shift）匹配撤销",
      keys.matches("flash.undo", { key: "U", shiftKey: true }) === true);
  }

  // ---------- 3. 修饰键要精确 ----------
  console.log("\n[3] Ctrl / Alt 必须精确相等（不能把 Ctrl+U 当成 U）");
  {
    const { keys } = runKeys();
    check("★ 单独按 U 匹配撤销", keys.matches("flash.undo", { key: "u" }) === true);
    check("★ Ctrl+U 不匹配撤销（Ctrl 组合另有含义）",
      keys.matches("flash.undo", { key: "u", ctrlKey: true }) === false);
    check("Meta（macOS 的 Cmd）也算 Ctrl",
      keys.matches("flash.undo", { key: "u", metaKey: true }) === false);
  }

  // ---------- 4. 撞键检测按 scope 分组 ----------
  console.log("\n[4] 撞键只在同一个 scope 内算冲突");
  {
    const { keys } = runKeys();
    const hit = keys.conflicts("flash.undo", "f");
    check("★ 闪卡内把撤销改成 F → 撞上「全屏练习」",
      hit && hit.id === "flash.full", hit ? hit.id : "null");
    check("★ 阅读弹窗的 F 不算冲突（两个模块互斥出现，各管各的）",
      keys.conflicts("read.full", "f") === null);
    check("改成自己原来的键不算冲突", keys.conflicts("flash.undo", "u") === null);
    check("带 Ctrl 的键与不带的不冲突",
      keys.conflicts("flash.undo", "Ctrl+f") === null
      || keys.conflicts("flash.undo", "Ctrl+f").id !== "flash.full");
  }

  // ---------- 5. 改键落盘：只存改过的 ----------
  console.log("\n[5] 改键落盘：只存被改过的那些");
  {
    const R = runKeys();
    check("一上来没有自定义", R.keys.isCustom("flash.undo") === false);
    R.keys.set("flash.undo", "n");
    const saved = JSON.parse(R.store["kaoyan.keys.v1"] || "{}");
    check("★ 落盘了 kaoyan.keys.v1", saved["flash.undo"] === "n", R.store["kaoyan.keys.v1"]);
    check("★ 没动过的键不落盘（将来默认值改了能跟着走）",
      !("flash.reveal" in saved) && !("flash.full" in saved), JSON.stringify(saved));
    check("specOf 给出新键", R.keys.specOf("flash.undo") === "n");
    check("isCustom 标成已自定义", R.keys.isCustom("flash.undo") === true);

    // 改回默认值 = 撤销自定义，不该继续占着盘
    R.keys.set("flash.undo", "u");
    const saved2 = JSON.parse(R.store["kaoyan.keys.v1"] || "{}");
    check("★ 改回默认值后就不再落盘（不再算自定义）",
      !("flash.undo" in saved2) && R.keys.isCustom("flash.undo") === false,
      JSON.stringify(saved2));
  }

  // ---------- 6. 恢复默认 ----------
  console.log("\n[6] 恢复默认（单个 / 全部）");
  {
    const R = runKeys();
    R.keys.set("flash.undo", "n");
    R.keys.set("flash.full", "g");
    R.keys.reset("flash.undo");
    check("单个恢复：" + "撤销回到 U", R.keys.specOf("flash.undo") === "u");
    check("单个恢复不牵连别的", R.keys.specOf("flash.full") === "g");
    R.keys.resetAll();
    check("★ 全部恢复：两个都回到默认",
      R.keys.specOf("flash.undo") === "u" && R.keys.specOf("flash.full") === "f");
    check("全部恢复后盘里是空对象",
      JSON.parse(R.store["kaoyan.keys.v1"] || "{}") &&
      Object.keys(JSON.parse(R.store["kaoyan.keys.v1"] || "{}")).length === 0);
  }

  // ---------- 7. 坏的存档不能带崩页面 ----------
  console.log("\n[7] 存档坏了/隐私模式：退回默认，不抛异常");
  {
    let R = runKeys({ "kaoyan.keys.v1": "{这不是 JSON" });
    check("★ 坏 JSON 退回默认",
      R.keys.specOf("flash.undo") === "u" && R.keys.isCustom("flash.undo") === false);
    R = runKeys({ "kaoyan.keys.v1": JSON.stringify({ "flash.undo": 123 }) });
    check("值是数字这种脏数据也退回默认", R.keys.specOf("flash.undo") === "u");
    R = runKeys({ "kaoyan.keys.v1": JSON.stringify({ "flash.undo": "n" }) });
    check("正常存档要读得进来", R.keys.specOf("flash.undo") === "n");
  }

  // ---------- 8. 展示串 ----------
  console.log("\n[8] 展示串：芯片与提示里显示的键名");
  {
    const { keys } = runKeys();
    check("Space → 空格", keys.pretty("Space") === "空格");
    check("Escape → Esc", keys.pretty("Escape") === "Esc");
    check("Enter → 回车", keys.pretty("Enter") === "回车");
    check("单字母大写显示", keys.pretty("u") === "U");
    check("★ 主键 + 别名一起排（提示条要显示全部能用的键）",
      keys.prettyAll("flash.reveal") === "空格 / 回车", keys.prettyAll("flash.reveal"));
    check("改名后展示跟着变", (() => {
      const R = runKeys();
      R.keys.set("flash.undo", "n");
      return R.keys.pretty(R.keys.specOf("flash.undo")) === "N";
    })());
  }

  // ---------- 9. 设置页表格渲染 ----------
  console.log("\n[9] 设置页表格：可改的给芯片，固定的也给一行");
  {
    const R = runKeys();
    R.keys.renderUI();
    const texts = [];
    walk(R.box, el => { if (el.textContent) texts.push(el.textContent); });
    check("★ 渲染出了「闪卡练习区」分组", texts.indexOf("闪卡练习区") >= 0);
    check("★ 渲染出了「笔记阅读弹窗」分组", texts.indexOf("笔记阅读弹窗") >= 0);
    check("固定项也列出来（各处输入框 / 退出关闭）",
      texts.indexOf("各处输入框") >= 0 && texts.indexOf("退出 / 关闭") >= 0);
    // 可改的芯片是 <button>（要能点）；固定的用 <span>（不给点，别做成可聚焦的假按钮）
    const chips = allByTag(R.box, "button").filter(b => hasClass(b, "key-chip"));
    const fixedChips = allByClass(R.box, "key-chip").filter(b => hasClass(b, "fixed"));
    check("★ 屏幕上有个「改键」按钮给撤销", chips.some(b => b.textContent === "U"));
    check("★ 显示答案的芯片只写主键（芯片是拿来点的，写成「空格 / 回车」会误导）",
      chips.some(b => b.textContent === "空格"), chips.map(b => b.textContent).join(" | "));
    check("★ 别名在行内单独标出来（不然用户以为回车不管用了）",
      allByClass(R.box, "key-alias").some(e => /回车/.test(e.textContent)),
      allByClass(R.box, "key-alias").map(e => e.textContent).join(" | "));
    check("★ 固定的那几行不是按钮（点了也没用，别做成可点的）",
      fixedChips.every(e => e.tagName !== "BUTTON"));
    check("固定的那几行标了 fixed（虚线框，不给改）", fixedChips.length >= 5,
      String(fixedChips.length));
    check("有「全部恢复默认」按钮",
      allByTag(R.box, "button").some(b => b.textContent === "全部恢复默认"));
  }

  // ---------- 10. 录制改键：点一下 → 按新键 ----------
  console.log("\n[10] 录制改键：点芯片 → 按新键 / Esc 取消 / 撞键被拦");
  {
    const R = runKeys();
    R.keys.renderUI();
    const chip = allByTag(R.box, "button")
      .filter(b => hasClass(b, "key-chip") && b.textContent === "U")[0];
    check("找到了「撤销」的芯片", !!chip);

    chip.onclick();
    check("★ 点一下进入录制态（芯片变「按下新键…」）",
      hasClass(chip, "rec") && /按下新键/.test(chip.textContent), chip.textContent);
    check("★ 录制时真的挂上了键盘监听（不然按键收不到）", R.hasKeyListener() === true);

    R.fireKey({ key: "n" });
    const saved = JSON.parse(R.store["kaoyan.keys.v1"] || "{}");
    check("★ 按下 N → 存进 localStorage", saved["flash.undo"] === "n",
      R.store["kaoyan.keys.v1"]);
    check("★ 录制结束，监听摘掉了", R.hasKeyListener() === false);
    const chip2 = allByTag(R.box, "button")
      .filter(b => hasClass(b, "key-chip") && b.textContent === "N")[0];
    check("★ 芯片显示新键 N 并标成已自定义", !!chip2 && hasClass(chip2, "custom"));
  }
  {
    const R = runKeys();
    R.keys.renderUI();
    const chip = allByTag(R.box, "button")
      .filter(b => hasClass(b, "key-chip") && b.textContent === "U")[0];
    chip.onclick();
    R.fireKey({ key: "Escape" });
    check("★ 按 Esc 取消：不落盘", R.store["kaoyan.keys.v1"] === undefined,
      R.store["kaoyan.keys.v1"]);
    check("取消后也把监听摘掉了", R.hasKeyListener() === false);
  }
  {
    const R = runKeys();
    R.keys.renderUI();
    const chip = allByTag(R.box, "button")
      .filter(b => hasClass(b, "key-chip") && b.textContent === "U")[0];
    chip.onclick();
    R.fireKey({ key: "f" });          // f 已经被「全屏练习」占了
    check("★ 撞键被拦下：没落盘", R.store["kaoyan.keys.v1"] === undefined,
      R.store["kaoyan.keys.v1"]);
    const st = R.box.children.length ? (function find(el) {
      if (el.id === "set-keys-status") return el;
      for (const c of el.children || []) { const h = find(c); if (h) return h; }
      return null;
    })(R.box) : null;
    check("★ 状态行说明了撞上谁", !!st && /全屏练习/.test(st.textContent),
      st ? st.textContent : "(没有状态行)");
    check("还留在录制态（可以直接换一个键）", R.hasKeyListener() === true);
  }
  {
    const R = runKeys();
    R.keys.renderUI();
    const chip = allByTag(R.box, "button")
      .filter(b => hasClass(b, "key-chip") && b.textContent === "U")[0];
    chip.onclick();
    R.fireKey({ key: "Control" });    // 光按修饰键不算键位
    check("★ 只按 Ctrl 不算数，还在等真正的键", R.hasKeyListener() === true);
    check("{} 也没落盘", R.store["kaoyan.keys.v1"] === undefined);
  }

  console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
  process.exit(fail === 0 ? 0 : 1);
})();

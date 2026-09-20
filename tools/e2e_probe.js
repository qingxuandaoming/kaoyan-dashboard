/*
 * e2e_probe.js —— 用 CDP 在**真浏览器**里跑一段探针表达式（不改 dashboard.html）。
 *
 * 为什么不用「往 dashboard.html 尾部注入 __probe__ 再 dump-dom」那套：
 * 那要动用户正在用的产物文件（得备份再还原），served 出去的中间态万一被他刷新到就穿帮。
 * 这里改成：无头 Edge 起一个远程调试端口 → 直接 Runtime.evaluate。
 *
 * 用法:
 *   node tools/e2e_probe.js --hash "#/review" --expr-file probe.js [--wait 4000]
 *   node tools/e2e_probe.js --url "http://127.0.0.1:8080/dashboard.html#/flash" --expr "1+1"
 * 输出: 打印 PROBE_RESULT=<表达式返回的 JSON 字符串>
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const { spawn } = require("child_process");
const WebSocket = require("ws");

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 19222;

const argv = process.argv.slice(2);
const arg = (name, dflt) => {
  const i = argv.indexOf(name);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : dflt;
};
const wait = parseInt(arg("--wait", "4500"), 10);
// ⚠️ 缓存参数要挂在 **# 之前**：`dashboard.html#/review?probe=1` 里的 ?probe 属于 hash，
//    路由会把页面名读成 "review?probe=1" → 那一页根本不渲染（查了一轮才发现）。
//
// --base：换一个服务实例来探（默认打用户的 8080）。做端到端验证时我们一律另起
//   `DB_PATH=副本 PORT=180xx node serve.js`，如果探针只会打 8080，那就等于**从来没打开
//   过测试实例**（现象：探针里 location.origin 是 8080、fetch 一个都没发出去，
//   看起来像"页面有 bug"，其实是探针打错了地方 —— 2026-09-20 踩过）。
//   用法：--base http://127.0.0.1:18096
const base = arg("--base", "http://127.0.0.1:8080").replace(/\/+$/, "");
const url = arg("--url", base + "/dashboard.html?probe=" + Date.now()
  + arg("--hash", "#/review"));
const exprFile = arg("--expr-file", "");
const expr = exprFile ? fs.readFileSync(exprFile, "utf-8") : arg("--expr", "document.title");
// --pre-file：在**页面脚本之前**跑的一段（CDP 的 addScriptToEvaluateOnNewDocument）。
// 用它预置 localStorage 这类必须在 boot 之前就位的东西——比如「上一版把点名写进了
// localStorage」这种现场。探针表达式是页面加载完之后才求值的，那时候已经晚了。
const preFile = arg("--pre-file", "");
const preSrc = preFile ? fs.readFileSync(preFile, "utf-8") : "";

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "edgecdp_"));
const child = spawn(EDGE, [
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--remote-debugging-port=" + PORT, "--user-data-dir=" + profile, url,
], { stdio: "ignore" });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const getJson = (p) => new Promise((resolve, reject) => {
  http.get({ host: "127.0.0.1", port: PORT, path: p }, (res) => {
    let b = "";
    res.on("data", (c) => (b += c));
    res.on("end", () => { try { resolve(JSON.parse(b)); } catch (e) { reject(e); } });
  }).on("error", reject);
});

async function pickPage() {
  let firstPage = null;
  for (let i = 0; i < 60; i++) {
    try {
      const list = await getJson("/json/list");
      const pages = list.filter(t => t.type === "page" && t.webSocketDebuggerUrl);
      if (pages.length) { firstPage = firstPage || pages[0]; }
      // ⚠️ --headless=new 会同时挂一个 about:blank 目标：按 url 挑更稳，
      //    挑错就是在空白页上求值（现象：location.hash 空、页面上什么都 undefined）。
      const mine = pages.find(t => String(t.url).indexOf("dashboard.html") >= 0);
      if (mine) { firstPage = mine; break; }
    } catch (e) { /* 还没起来 */ }
    await sleep(300);
  }
  if (!firstPage) throw new Error("等不到 Edge 的调试目标");
  return firstPage;
}

/** 连上之后**显式导航**一次，别指望命令行参数那条 URL 已经在那个 target 上 */
function send(ws, id, method, params) {
  return new Promise((resolve, reject) => {
    const onMsg = (raw) => {
      let m;
      try { m = JSON.parse(raw.toString()); } catch (e) { return; }
      if (m.id !== id) return;
      ws.off("message", onMsg);
      if (m.error) return reject(new Error(JSON.stringify(m.error)));
      resolve(m.result);
    };
    ws.on("message", onMsg);
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
}

function evaluate(ws, expression, id) {
  return new Promise((resolve, reject) => {
    const onMsg = (raw) => {
      let m;
      try { m = JSON.parse(raw.toString()); } catch (e) { return; }
      if (m.id !== id) return;
      ws.off("message", onMsg);
      if (m.error) return reject(new Error(JSON.stringify(m.error)));
      const r = m.result && m.result.result;
      if (r && r.subtype === "error") return reject(new Error(r.description || "页面里抛错了"));
      resolve(r && r.value);
    };
    ws.on("message", onMsg);
    ws.send(JSON.stringify({
      id, method: "Runtime.evaluate",
      params: { expression, returnByValue: true, awaitPromise: true },
    }));
  });
}

(async () => {
  let ws;
  try {
    const page = await pickPage();
    ws = new WebSocket(page.webSocketDebuggerUrl, { perMessageDeflate: false });
    await new Promise((res, rej) => { ws.on("open", res); ws.on("error", rej); });
    await send(ws, 1, "Runtime.enable");
    await send(ws, 2, "Page.enable");
    if (preSrc) await send(ws, 33, "Page.addScriptToEvaluateOnNewDocument", { source: preSrc });
    await send(ws, 3, "Page.navigate", { url });
    await sleep(wait);
    const where = await evaluate(ws, "location.href", 4);
    console.log("PROBE_URL=" + where);
    const out = await evaluate(ws, expr, 5);
    console.log("PROBE_RESULT=" + (typeof out === "string" ? out : JSON.stringify(out)));
    const shot = arg("--shot", "");
    if (shot) {
      const clipSel = arg("--shot-selector", "");
      let params = { format: "png", captureBeyondViewport: true };
      if (clipSel) {
        const box = await evaluate(ws, `(function(){var e=document.querySelector(${JSON.stringify(clipSel)});
          if(!e) return null; var r=e.getBoundingClientRect();
          return {x:r.left+(window.scrollX||0),y:r.top+(window.scrollY||0),width:r.width,height:r.height,scale:1};})()`, 6);
        if (box) params.clip = box;
      }
      const r = await send(ws, 7, "Page.captureScreenshot", params);
      fs.writeFileSync(shot, Buffer.from(r.data, "base64"));
      console.log("PROBE_SHOT=" + shot);
    }
  } catch (e) {
    console.log("PROBE_ERROR=" + (e && e.message));
    process.exitCode = 1;
  } finally {
    try { if (ws) ws.close(); } catch (e) {}
    try { child.kill(); } catch (e) {}
  }
})();

/*
 * 早间回顾闪卡探针（2026-09-20）：在真浏览器里把「今天的回顾内容」翻卡刷完，
 * 验证三件事：
 *  ① 这一组卡是**当天内容**拼的（知识点/小测/数学要点/英语），不是闪卡库的卡；
 *  ② 全程不碰主闪卡库（没有 /api/flashcards/session、/api/flashcards/review、/api/study/cards）；
 *  ③ 整组刷完 → 自动打卡（POST /api/morning-review/checkin），页面上的打卡按钮跟着变。
 * 用法（由 tools/e2e_mr_flash.py 驱动）：node tools/e2e_probe.js --hash "#/review" --expr-file 本文件
 */
(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const out = { reqs: [] };
  const origFetch = window.fetch;
  window.fetch = function (u) {
    try {
      const s = String(u);
      if (s.indexOf("/api/") >= 0) out.reqs.push(s.replace(location.origin, ""));
    } catch (e) {}
    return origFetch.apply(this, arguments);
  };
  const txt = (el) => (el ? el.textContent.replace(/\s+/g, " ").trim() : null);
  const key = (k, code) => document.dispatchEvent(new KeyboardEvent("keydown", { key: k, code: code, bubbles: true }));

  // 等「早」页把当天的内容渲染出来
  for (let i = 0; i < 40 && !document.querySelector(".mr-card"); i++) await sleep(250);

  out.panel = {
    cards: document.querySelectorAll(".mr-card").length,
    chips: Array.prototype.map.call(document.querySelectorAll(".mr-tabs .mr-tab"), (b) => b.textContent.trim()),
    startBtn: txt(document.getElementById("mr-fc-start")),
    note: txt(document.querySelector(".mr-fc-note")),
    checkBtn: txt(document.getElementById("mr-check")),
    hasDeckTabs: !!document.querySelector('[data-tab="408"]'),
  };

  const start = document.getElementById("mr-fc-start");
  if (!start) return out;
  start.click();
  await sleep(1500);
  out.first = {
    progress: txt(document.querySelector(".fs-progress")),
    head: txt(document.querySelector(".fs-head")),
    stem: txt(document.querySelector(".fs-stem")),
    floatOpen: !document.getElementById("fs-float").hidden,
    floatTitle: txt(document.getElementById("fs-float-title")),
  };
  out.reqsAfterStart = out.reqs.slice();

  let guard = 0, backs = [], summary = null;
  while (guard++ < 80) {
    if (document.querySelector(".fs-summary")) { summary = txt(document.querySelector(".fs-summary")); break; }
    key(" ", "Space");                       // 翻卡看原文
    await sleep(200);
    const back = document.querySelector(".fs-local-back");
    if (back) backs.push(String(back.innerHTML).slice(0, 200));
    key("4", "Digit4");                      // 自评「很熟」→ 下一张
    await sleep(220);
  }
  out.backCount = backs.length;
  out.backs = backs.slice(0, 4);
  out.summary = summary;
  out.clicks = guard;
  out.finishLine = txt(document.getElementById("fs-local-finish"));
  await sleep(1200);
  out.finishLineAfter = txt(document.getElementById("fs-local-finish"));
  out.checkBtnAfter = txt(document.getElementById("mr-check"));
  out.panelAfter = txt(document.querySelector(".mr-fc-note"));

  // 收起浮窗：练习区回闪卡页，应该换回闪卡页的闸门，而不是留着早间回顾的总结屏
  const closeBtn = document.getElementById("fs-float-close");
  if (closeBtn) closeBtn.click();
  await sleep(500);
  out.afterClose = {
    floatHidden: !!document.getElementById("fs-float").hidden,
    gate: !!document.querySelector("#flash-practice .fs-gate"),
    summaryStill: !!document.querySelector("#flash-studio .fs-summary"),
  };

  window.fetch = origFetch;
  return out;
})()

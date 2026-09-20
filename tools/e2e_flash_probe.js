/*
 * 闪卡「出卡」探针（2026-09-20）：在**真浏览器 + 真服务端**上验证两件事
 *  ① 昨天「练这几张」点名的那一组，不再粘住「放弃·重新挑一组 / 重开一组」（不该再拿到同一批）
 *  ② 头部「🔁 再来一组（N 张）」在，点它真的按设置里的 N 张走 extra 通道
 * 用法（由 tools/e2e_flash_out.py 驱动）：node tools/e2e_probe.js --hash "#/flash" --expr-file 本文件
 */
(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const out = { urls: [], steps: [] };
  const origFetch = window.fetch;
  window.fetch = function (u) {
    try {
      const s = String(u);
      if (s.indexOf("/api/") >= 0) out.urls.push(s.replace(location.origin, ""));
    } catch (e) {}
    return origFetch.apply(this, arguments);
  };
  const txt = (el) => (el ? el.textContent.replace(/\s+/g, " ").trim() : null);
  const progress = () => txt(document.querySelector(".fs-progress"));

  await sleep(800);
  out.gate = txt(document.querySelector(".fs-gate"));
  out.gateButtons = Array.prototype.map.call(
    document.querySelectorAll(".fs-gate button"), (b) => b.textContent.trim());
  out.extraBtn = txt(document.getElementById("fs-extra-now"));

  // ① 走「放弃，重新挑一组」（没有未完成的本组时是「开始学习」）——这一条以前会把
  //    昨天点名的那几张原样端回来
  const giveUp = document.getElementById("fs-gate-new") || document.getElementById("fs-gate-start");
  out.firstClick = giveUp ? giveUp.textContent.trim() : null;
  if (giveUp) giveUp.click();
  await sleep(2600);
  out.afterNew = {
    progress: progress(),
    urls: out.urls.slice(),
    head: document.querySelector(".fs-head") ? document.querySelector(".fs-head").innerHTML.slice(0, 400) : null,
    stem: txt(document.querySelector(".fs-stem")),
    extraHint: txt(document.querySelector(".fs-policy-hint")),
    // 头部那个「🔁 再来一组（N 张）」只在有卡在场上时才存在（闸门态没有）
    extraBtn: txt(document.getElementById("fs-extra-now")),
  };

  // ② 头部「🔁 再来一组（N 张）」
  const eb = document.getElementById("fs-extra-now");
  out.extraBtnAfter = txt(eb);
  const n0 = out.urls.length;
  if (eb) eb.click();
  await sleep(2600);
  out.afterExtra = {
    progress: progress(),
    urls: out.urls.slice(n0),
    head: document.querySelector(".fs-head") ? document.querySelector(".fs-head").innerHTML.slice(0, 400) : null,
    extraHint: txt(document.querySelector(".fs-policy-hint")),
    extraBtn: txt(document.getElementById("fs-extra-now")),
  };
  window.fetch = origFetch;
  return out;
})()

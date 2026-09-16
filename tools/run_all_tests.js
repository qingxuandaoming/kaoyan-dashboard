#!/usr/bin/env node
/**
 * run_all_tests.js —— 依次执行 tools/ 下所有 test_*.js，汇总通过/失败。
 *
 * 用法：
 *     node tools/run_all_tests.js        （或 npm test）
 *
 * 说明：测试文件里多数会自己开无头 Edge / 起临时服务，
 * 因此整套跑下来需要几分钟，且要求本机装了 Edge
 * （默认 C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe）。
 */
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const DIR = __dirname;
const files = fs
  .readdirSync(DIR)
  .filter((f) => /^test_.*\.js$/.test(f))
  .sort();

if (files.length === 0) {
  console.error("[ERROR] tools/ 下没有找到 test_*.js");
  process.exit(1);
}

console.log(`考研大盘 · 测试套件（${files.length} 个文件）`);

const failed = [];
const t0 = Date.now();

for (const f of files) {
  console.log("\n" + "=".repeat(64));
  console.log("[TEST] " + f);
  console.log("=".repeat(64));
  const r = spawnSync(process.execPath, [path.join(DIR, f)], {
    stdio: "inherit",
    cwd: DIR,
  });
  if (r.error) {
    console.log("[ERROR] 无法启动：" + r.error.message);
    failed.push(f);
  } else if (r.status !== 0) {
    failed.push(f);
  }
}

const dt = ((Date.now() - t0) / 1000).toFixed(1);
console.log("\n" + "=".repeat(64));
console.log(`汇总：${files.length} 个文件，耗时 ${dt}s`);
console.log("=".repeat(64));

if (failed.length) {
  console.log("失败 " + failed.length + " 个：");
  failed.forEach((f) => console.log("  x " + f));
  process.exit(1);
}
console.log("全部通过 ✓");

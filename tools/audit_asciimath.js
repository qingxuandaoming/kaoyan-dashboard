/*
 * audit_asciimath.js —— 拿**线上那份** asciiMath 跑全库笔记，逐条列出会改哪些片段。
 *
 * 为什么不用 Python 原型：原型只是当初选规则时用的草稿，改了 JS 就得同步一份，
 * 迟早对不上。这里直接从 generate_dashboard.py 抽出 FLASH_JS 的第一个 IIFE，
 * 用真的 maskMath（把 $公式$ 摘成占位符，与 mdRender 的走法一致）+ 真的 asciiMath。
 *
 * 只读不写：输出是「改前 → 改后」的清单，给人眼过一遍有没有把文件名/区间号当公式。
 *
 * 用法: node tools/audit_asciimath.js [--all]
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = process.env.NOTES_ROOT || "E:\\NPEE";   // 笔记库根（2026-09-25 起与代码根分离）
const GEN = path.join(__dirname, "..", "generate_dashboard.py");
const OUT = path.join(os.tmpdir(), "kaoyan_audit_flashjs.js");

execFileSync("python", ["-c", `
import ast, io
src = io.open(r"${GEN}", encoding="utf-8").read()
for node in ast.parse(src).body:
    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "FLASH_JS":
        io.open(r"${OUT}", "w", encoding="utf-8").write(ast.literal_eval(node.value))
        break
`], { maxBuffer: 1 << 24 });

let js = fs.readFileSync(OUT, "utf-8");
const anchor = js.indexOf("window.asciiMath = asciiMath;");
const close = js.indexOf("})();", anchor);
if (close < 0) throw new Error("FLASH_JS 结构变了：找不到全局 KaTeX IIFE 的结尾");
js = js.slice(0, close + 5);

globalThis.window = globalThis;
globalThis.document = {
  createElement() {
    let t = "";
    return {
      set textContent(v) { t = v == null ? "" : String(v); },
      get textContent() { return t; },
      get innerHTML() { return String(t); },
    };
  },
  head: { appendChild() {} },
  querySelectorAll() { return []; },
};
new Function(js)();
const { asciiMath, maskMath } = globalThis;
if (typeof asciiMath !== "function" || typeof maskMath !== "function") {
  throw new Error("FLASH_JS 没有导出 asciiMath / maskMath");
}

// 与 mdRender 同序：先摘围栏代码块，再摘插图，再摘公式，剩下的才是 inline() 会看到的文本。
// 行内代码 mdRender 是在 inline() 里摘的（在 asciiMath 之前），这里也照做——
// 少摘一层就会把「代码里的 _ ^」当成误报（第一版审计把 `MIPS=主频/(CPI×10^6)` 报了出来）。
function visibleText(src) {
  const codes = [];
  let t = src.replace(/```[\s\S]*?```/g, (m) => {
    codes.push(m);
    return "@@CODE" + (codes.length - 1) + "@@";
  });
  t = t.replace(/!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)/g,
    (m, alt) => "@@IMG:" + alt + "@@");
  t = t.replace(/`([^`]+)`/g, (m) => "@@INLINE@@");
  const maths = [];
  t = maskMath(t, maths);                       // 与笔记渲染器同一个函数
  return t;
}

const dirs = ["408", "Math", "English", "Politics"];
const files = [];
for (const d of dirs) {
  const walk = (p) => {
    for (const e of fs.readdirSync(p, { withFileTypes: true })) {
      const full = path.join(p, e.name);
      if (e.isDirectory()) walk(full);
      else if (e.name.endsWith(".md")) files.push(full);
    }
  };
  const base = path.join(ROOT, d);
  if (fs.existsSync(base)) walk(base);
}

const showAll = process.argv.includes("--all");
const samples = [];
let changedFiles = 0, changedSpots = 0;
const shape = new Map();      // 命中写法 → 次数
const byFile = [];

for (const f of files) {
  const src = fs.readFileSync(f, "utf-8");
  const text = visibleText(src);
  const out = asciiMath(text);
  if (out === text) continue;
  changedFiles++;
  // 逐处 diff：按行比对，记下改了哪几段
  const a = text.split("\n"), b = out.split("\n");
  const spots = [];
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if (a[i] !== b[i]) {
      spots.push({ line: i + 1, before: a[i], after: b[i] });
      changedSpots++;
      const m = (b[i].match(/<(sub|sup)>/g) || []).length;
      const key = (a[i].match(/[_^][\(\{]?[A-Za-z0-9]{1,3}/g) || []).slice(0, 3).join(" ");
      if (key) shape.set(key, (shape.get(key) || 0) + m);
    }
  }
  byFile.push({ file: path.relative(ROOT, f), spots });
  if (samples.length < 8) samples.push({ file: path.relative(ROOT, f), spots: spots.slice(0, 3) });
}

console.log(`扫描 ${files.length} 个笔记文件：${changedFiles} 个会被改动，共 ${changedSpots} 行\n`);
console.log("前几个文件的改动样例：");
for (const s of samples) {
  console.log("  " + s.file);
  for (const p of s.spots) {
    console.log(`    L${p.line}`);
    console.log(`      前: ${p.before.trim().slice(0, 100)}`);
    console.log(`      后: ${p.after.trim().slice(0, 100)}`);
  }
}
console.log("\n命中写法分布（前 20）：");
for (const [k, n] of [...shape.entries()].sort((x, y) => y[1] - x[1]).slice(0, 20)) {
  console.log(`  ${String(n).padStart(4)}  ${k}`);
}
if (showAll) {
  console.log("\n全部改动：");
  for (const f of byFile) for (const p of f.spots) {
    console.log(`  ${f.file}:${p.line}\n    前: ${p.before.trim()}\n    后: ${p.after.trim()}`);
  }
} else {
  console.log("\n（加 --all 看全部改动）");
}

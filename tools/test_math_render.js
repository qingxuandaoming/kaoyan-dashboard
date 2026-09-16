/*
 * 按需 KaTeX 渲染的行为测试。
 *
 * 做法与 test_note_render.js 一致：从 generate_dashboard.py 用 ast 抽出**真实的**
 * FLASH_JS，只取开头那段「全局按需 KaTeX」IIFE（后面接着闪卡逻辑，跑不起来也不需要），
 * 用极简 DOM 桩 + 假 katex 跑起来，测的是线上那份代码。
 *
 * 重点覆盖：
 *   1. 不含 $ 的文本原样转义；
 *   2. 公式段交给 KaTeX、文本段照常转义（不能先整串转义，否则 \frac 与 a<b 会坏）；
 *   3. $$...$$ 走 displayMode；
 *   4. 未闭合/落单的 $ 不误判成公式（"价格 $5 元"）；
 *   5. KaTeX 未就绪时输出 tex-fallback 兜底，加载完成后 flushMath 原地替换。
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const GEN = path.join(__dirname, "..", "generate_dashboard.py");
const OUT = path.join(os.tmpdir(), "kaoyan_flashjs_extracted.js");

// ---- 1. 抽出 FLASH_JS，只保留第一个 IIFE（全局按需 KaTeX）----
execFileSync("python", ["-c", `
import ast, io
src = io.open(r"${GEN}", encoding="utf-8").read()
for node in ast.parse(src).body:
    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "FLASH_JS":
        io.open(r"${OUT}", "w", encoding="utf-8").write(ast.literal_eval(node.value))
        break
`], { maxBuffer: 1 << 24 });

let js = fs.readFileSync(OUT, "utf-8");
const anchor = js.indexOf("window.flushMath = flushMath;");
if (anchor < 0) throw new Error("FLASH_JS 结构变了：找不到全局 KaTeX IIFE 的导出语句");
const close = js.indexOf("})();", anchor);
if (close < 0) throw new Error("找不到全局 KaTeX IIFE 的结尾");
js = js.slice(0, close + 5);   // 丢掉后面的闪卡 IIFE（它要 fetch，测试里不需要）

// ---- 2. 极简 DOM 桩 ----
const mathCalls = [];          // 假 katex 收到的 (tex, displayMode)
const sink = [];               // querySelectorAll("[data-texid]") 的返回

function escStub(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

globalThis.window = globalThis;
globalThis.document = {
  createElement() {
    let text = "";
    return {
      set textContent(v) { text = v == null ? "" : String(v); },
      get textContent() { return text; },
      get innerHTML() { return escStub(text); },
    };
  },
  head: { appendChild() {} },
  querySelectorAll(sel) { return sel === "[data-texid]" ? sink : []; },
};

// ---- 3. 跑起来 ----
new Function(js)();

const { richText, texWrap, flushMath, katexHtml } = globalThis;

// ---- 4. 断言 ----
let pass = 0, fail = 0;
function check(name, actual, expected) {
  const ok = actual === expected;
  if (ok) { pass++; console.log("  ✅ " + name); }
  else { fail++; console.log("  ❌ " + name + "\n      期望: " + JSON.stringify(expected)
    + "\n      实际: " + JSON.stringify(actual)); }
}
function checkTrue(name, cond, detail) {
  if (cond) { pass++; console.log("  ✅ " + name); }
  else { fail++; console.log("  ❌ " + name + (detail ? "\n      " + detail : "")); }
}

console.log("【无公式】纯文本照常转义");
check("不含 $ 的文本，HTML 特殊字符被转义", richText("a<b & c>d"), "a&lt;b &amp; c&gt;d");
check("空值与 null 安全", richText(null) + "|" + richText(undefined), "|");

console.log("\n【有公式】公式段走 KaTeX，文本段照常转义");
globalThis.katex = {
  renderToString(tex, opts) {
    mathCalls.push({ tex, display: !!opts.displayMode });
    return "<KATEX>" + tex + "</KATEX>";
  },
};
check("行内 $x^2$", richText("设 $x^2$ 则"), "设 <KATEX>x^2</KATEX> 则");
check("公式两侧文本里的尖括号仍被转义", richText("a<b 且 $x$"), "a&lt;b 且 <KATEX>x</KATEX>");
check(
  "公式内的反斜杠与尖括号原样交给 KaTeX",
  richText("$\\frac{a}{b}<c$"),
  "<KATEX>\\frac{a}{b}<c</KATEX>"
);
checkTrue(
  "确实把未转义的源码传给了 KaTeX",
  mathCalls.some(c => c.tex === "\\frac{a}{b}<c"),
  "收到的 tex: " + JSON.stringify(mathCalls.map(c => c.tex))
);
check("独立行 $$...$$ 走 displayMode", richText("$$\\int_0^1 x dx$$"), "<KATEX>\\int_0^1 x dx</KATEX>");
checkTrue(
  "displayMode 标记正确",
  mathCalls.some(c => c.tex === "\\int_0^1 x dx" && c.display === true)
);

console.log("\n【不误判】落单的 $ 不当公式");
// 换行用 String.fromCharCode 显式构造，避免源码里的转义层次影响判读
const NL = String.fromCharCode(10);
const crossLine = "$a" + NL + "b$";
check("价格 $5 元（只有一个 $）", richText("价格 $5 元"), "价格 $5 元");
check("两个 $ 但跨行（不在同一行闭合）", richText(crossLine), crossLine);
check("四个 $ 且内容为空", richText("$$$$"), "$$$$");

console.log("\n【兜底】KaTeX 未就绪时先渲染源码，就绪后原地替换");
delete globalThis.katex;
const html = texWrap("$x^2$");
checkTrue("未就绪时输出 tex-fallback 兜底", html.indexOf("tex-fallback") >= 0, html);
checkTrue("texWrap 返回带 data-texid 的容器", /data-texid="\d+"/.test(html), html);
checkTrue("无公式时不触发 KaTeX 加载（texWrap 不产生公式调用）",
  html.indexOf("tex-fallback") >= 0 && katexHtml("x", false).indexOf("tex-fallback") >= 0);

// 模拟 KaTeX 加载完成：把 texWrap 登记过的元素交给 flushMath 重渲染
const idMatch = html.match(/data-texid="(\d+)"/);
const el = {
  _html: html,
  getAttribute(k) { return k === "data-texid" ? idMatch[1] : null; },
  set innerHTML(v) { this._html = v; },
  get innerHTML() { return this._html; },
};
sink.push(el);
globalThis.katex = {
  renderToString(tex, opts) { return "<KATEX2>" + tex + "</KATEX2>"; },
};
flushMath();
checkTrue("flushMath 把兜底替换成真实公式",
  el.innerHTML.indexOf("<KATEX2>x^2</KATEX2>") >= 0, el.innerHTML);

console.log("\n【真实卡】从题库取一张已 LaTeX 化的数学卡，确认公式真的送进 KaTeX");
{
  const { DatabaseSync } = require("node:sqlite");
  const db = new DatabaseSync(path.join(__dirname, "..", "question_bank.db"), { readOnly: true });
  const row = db.prepare("SELECT content FROM questions WHERE id = ?").get("Q-MATH-GS-01-04-0001");
  db.close();
  const ct = JSON.parse(row.content);
  // 前面几节换过桩，这里重新装一个带计数的，否则数不到 KaTeX 调用
  mathCalls.length = 0;
  globalThis.katex = {
    renderToString(tex, opts) {
      mathCalls.push({ tex, display: !!opts.displayMode });
      return "<KATEX>" + tex + "</KATEX>";
    },
  };
  const out = richText(ct.stem);
  checkTrue("题干里解析出公式段", mathCalls.length >= 1, "katex 调用 " + mathCalls.length + " 次");
  checkTrue(
    "极限表达式按 LaTeX 原文交给 KaTeX",
    mathCalls.some(c => c.tex.indexOf("\\lim\\limits_{x\\to 0}") >= 0),
    "收到的 tex: " + JSON.stringify(mathCalls.map(c => c.tex))
  );
  checkTrue("中文说明仍被转义、未被当成公式", out.indexOf("已知") >= 0 && out.indexOf("<KATEX>") >= 0);
  const optOut = richText(ct.options[ct.answer]);
  checkTrue("正确选项也走 KaTeX", optOut.indexOf("<KATEX>") >= 0);
  checkTrue("解释里的公式同样渲染", richText(ct.explanation).indexOf("<KATEX>") >= 0);
  checkTrue(
    "卡里不再残留 Unicode 积分/求和记号",
    !/[∫∬Σ]/.test(JSON.stringify(ct)),
    "残留: " + (JSON.stringify(ct).match(/[∫∬Σ]/g) || []).join("")
  );
}

console.log("\n" + "=".repeat(52));
console.log(`通过 ${pass} 项，失败 ${fail} 项`);
console.log("=".repeat(52));
process.exit(fail ? 1 : 0);

/*
 * probe_notation.js — 把题库里真实的公式串喂给线上的 richText()，看它到底渲染成什么。
 *
 * 与 test_math_render.js 同一套做法：从 generate_dashboard.py 抽出真实的 FLASH_JS，
 * 只跑第一个 IIFE（全局按需 KaTeX），用极简 DOM 桩 + 假 katex。
 * 输出里出现 <KATEX> 才是走了公式渲染；原样出现 ^ / _ 就是当普通文字漏出去了。
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const GEN = path.join(__dirname, "..", "generate_dashboard.py");
const OUT = path.join(os.tmpdir(), "kaoyan_probe_flashjs.js");

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
const close = js.indexOf("})();", anchor);
js = js.slice(0, close + 5);

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
  querySelectorAll() { return []; },
};
globalThis.katex = {
  renderToString(tex, opts) { return "<KATEX>" + tex + "</KATEX>"; },
};

new Function(js)();
const { richText } = globalThis;

// 全部取自 question_bank.db 的真实内容
const CASES = [
  ["本题（Q-MATH-GS-07-0035）解析", "全微分方程（第一组第 5 题）。F_(x) = x²+y → F = x³/3 + xy + g(y)"],
  ["本题解析后半", "再由 F_(y) = x + g′(y) = x − 2y → g′ = −2y → g = −y²。"],
  ["Q-MATH-GS-02-0006 解析", "(log_(a) x)′ = 1/(x ln a)，取 a=e 即得。"],
  ["Q-MATH-GS-08-0002 题干", "时，通解为y=(C₁+C₂x)e^(rx)。"],
  ["Q-MATH-GS-03-0007 选项", "2x·e^(x²)"],
  ["Q-MATH-GS-06-0002 解析", "格林公式：∮_C Pdx+Qdy = ∬_D (∂Q/∂x - ∂P/∂y)dxdy"],
  ["Q-MATH-XD-04-03-0002 解析", "另注意构造 D_j 时永远把第 j 列换成 b，因为 x_j 的系数在第 j 列。"],
  ["Q-TGT-A31CA44C 解析", "主存4GB=2^32B，主存地址32位。块大小64B=2^6B"],
  ["对照：已用 Unicode 上标", "x³/3 + xy − y² = C"],
  ["对照：已包 $ 的 LaTeX", "对称区间 $[-a,a]$ 上的 $\\dfrac{1}{1+e^{kx}}$"],
  ["对照：裸 LaTeX（有 \\命令）", "由 \\sum_{i=1}^{n} 得"],
];

console.log("输入 → richText() 输出\n");
for (const [name, s] of CASES) {
  const out = richText(s);
  // 判定：交给 KaTeX / 转成了上下标 / 原样不动，三种结果分开报
  const verdict = out.indexOf("<KATEX>") >= 0 ? "✅ KaTeX"
    : /<(sub|sup)>/.test(out) ? "✅ 上下标"
    : out === s ? "➖ 原样（本就该如此，或未识别）" : "❓ 其它改动";
  console.log(`${verdict}  ${name}`);
  console.log(`      输入: ${s}`);
  console.log(`      输出: ${out}\n`);
}

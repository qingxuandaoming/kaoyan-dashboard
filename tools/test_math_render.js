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

const { richText, texWrap, flushMath, katexHtml, registerTex, asciiMath, asciiMathHtml } = globalThis;

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

console.log("\n【行内 \\(..\\)】内容里带圆括号也要匹配上（2026-09-22 全量语料探针抓到的）");
// 模型写的 \(P(x)Q(y)\)、\(F(x,y)\) 的内容里本来就有圆括号；旧 pattern 是 [^)]*?，
// 撞上内容里的 ) 就整段失配 —— 定界符连同 \frac 一起原样摊在正文里。
// 拿 explain_log 里 51 条真实 AI 回复跑真浏览器探针：10 条中招。
globalThis.katex = {
  renderToString(tex, opts) {
    mathCalls.push({ tex, display: !!opts.displayMode });
    return "<KATEX>" + tex + "</KATEX>";
  },
};
check("内容带圆括号：\\(P(x)Q(y)\\)", richText("即 \\(P(x)Q(y)\\) 的形式"),
  "即 <KATEX>P(x)Q(y)</KATEX> 的形式");
check("内容带逗号与圆括号：\\(F(x,y)\\)", richText("存在 \\(F(x,y)\\) 使得"),
  "存在 <KATEX>F(x,y)</KATEX> 使得");
{
  mathCalls.length = 0;
  check("同一行两个 \\(..\\) 各成一段", richText("\\(a\\) 与 \\(b\\) 都对"),
    "<KATEX>a</KATEX> 与 <KATEX>b</KATEX> 都对");
  checkTrue("KaTeX 收到的是括号里的原文（定界符已剥掉）",
    mathCalls.length === 2 && mathCalls[0].tex === "a" && mathCalls[1].tex === "b",
    "收到的 tex: " + JSON.stringify(mathCalls.map(c => c.tex)));
}
// 放宽到「本行内任意字符」之后，仍然不能跨行吞（否则一行落单的 \( 会把后面全卷进公式）
{
  const halfOpen = "\\(a" + NL + "b\\)";
  check("\\(..\\) 不跨行匹配", richText(halfOpen), halfOpen);
}

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

console.log("\n【函数式登记】AI 回复（mdTex）整块重渲染，不能退化成 richText");
// texWrap 登记的是原文（重切公式）；mdTex 登记的是函数（连 Markdown 一起重渲染）。
// 只认字符串的话，AI 那一屏的标题/表格/粗体在 KaTeX 就绪后会被冲掉。
{
  sink.length = 0;
  let called = 0;
  const id = registerTex(() => { called++; return "<RERENDERED>" + id + "</RERENDERED>"; });
  const fnEl = {
    _html: "",
    getAttribute(k) { return k === "data-texid" ? String(id) : null; },
    set innerHTML(v) { this._html = v; },
    get innerHTML() { return this._html; },
  };
  sink.push(fnEl);
  flushMath();
  checkTrue("函数式登记被调用（没被当成字符串喂给 richText）", called === 1, "called=" + called);
  checkTrue("重渲染结果写进了元素", fnEl.innerHTML.indexOf("<RERENDERED>") >= 0, fnEl.innerHTML);
}

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

console.log("\n【ASCII 上下标】_(x) / _i / ^{n} / ^n 渲染成 <sub>/<sup>");
// 2026-09-17：题库 38 张卡、笔记 200 余处用这种写法（O(n^2)、∬_D、e^(x²/2)、W_T），
// 而 splitMath 只认 $...$ 与带反斜杠命令的裸 LaTeX，原先整串原样漏出。
check("单选题解析里的 F_(x)", richText("全微分方程。F_(x) = x²+y → F = x³/3"),
  "全微分方程。F<sub>x</sub> = x²+y → F = x³/3");
check("括号式上标 e^(x²/2)", richText("得 y = e^(x²/2)"), "得 y = e<sup>x²/2</sup>");
check("花括号式上标", richText("T(n)=Θ(n^{log_b a})"), "T(n)=Θ(n<sup>log_b a</sup>)");
check("单词式上标 O(2^n)", richText("O(2^n) 与 O(n^3)"), "O(2<sup>n</sup>) 与 O(n<sup>3</sup>)");
check("单词式下标 ∬_D / ∮_C", richText("∮_C Pdx = ∬_D 积分"), "∮<sub>C</sub> Pdx = ∬<sub>D</sub> 积分");
check("多字母下标 W_T（括号式）", richText("GBN：W_{T} ≤ 2^n"), "GBN：W<sub>T</sub> ≤ 2<sup>n</sup>");
check("函数括号里的下标 LOC(a_i)", richText("LOC(a_i)=LOC(a_0)+i×L"),
  "LOC(a<sub>i</sub>)=LOC(a<sub>0</sub>)+i×L");
check("数字底数 10^6", richText("f/(CPI×10^6)"), "f/(CPI×10<sup>6</sup>)");
check("右括号底数 (x+y)^2", richText("先算 (x+y)^2"), "先算 (x+y)<sup>2</sup>");
check("多个标记共存", richText("e^(−y)dy = e^(2x)dx"), "e<sup>−y</sup>dy = e<sup>2x</sup>dx");
// ③ Pandoc 配对写法：收尾的 ^ 必须一起吃掉，不能留下孤立的 ^
check("Pandoc 配对 O(n^2^)", richText("答案：O(n^2^)"), "答案：O(n<sup>2</sup>)");
check("Pandoc 配对 3^x^", richText("则 3^x^ = n"), "则 3<sup>x</sup> = n");

console.log("\n【ASCII 上下标·不误判】文件名 / 标识符 / 区间号 / 填空线一律不动");
check("文件名 第5章_IO管理.md", richText("见 第5章_IO管理.md"), "见 第5章_IO管理.md");
check("章节号 CO_6.4", richText("CO_6.4 中断系统"), "CO_6.4 中断系统");
check("标识符 book_id", richText("字段 book_id、exam_frequency"), "字段 book_id、exam_frequency");
check("文件名 scan_pdf.py", richText("跑 src/scan_pdf.py"), "跑 src/scan_pdf.py");
check("正则 (^|x)", richText("正则 (^|x) 的写法"), "正则 (^|x) 的写法");
check("行首 ^", richText("^abc 开头"), "^abc 开头");
// ④ 中文里的 ~ 是区间号，绝不能当上下标（审计时抓到的真实用例）
check("区间号 60~70分钟", richText("控制在60~70分钟内"), "控制在60~70分钟内");
check("题号区间 1~11", richText("数据结构1~11、计组12~22"), "数据结构1~11、计组12~22");
check("Pandoc 下标 a~i~ 不处理", richText("结点 a~i~"), "结点 a~i~");
check("填空线 ____", richText("使____、调____"), "使____、调____");
// 中文底数：笔记里有「真题_2009_15_Cache_Tag字段_8路组相联.jpg」这类文件名，
// 中文永远不是数学底数（全库审计抓到的误判）
check("中文底数 字段_8路", richText("真题_Tag字段_8路组相联.jpg"), "真题_Tag字段_8路组相联.jpg");
// C 类型名：int64_t 的数字底数后面还粘着字母，不是 10^6 那种数字上标（同样来自审计）
check("C 类型名 int64_t", richText("假设 int64_t*，5×8=40"), "假设 int64_t*，5×8=40");
check("uint8_t / float32_t", richText("uint8_t 与 float32_t"), "uint8_t 与 float32_t");
check("数字底数 10^6 仍要转", richText("MIPS=主频/(CPI×10^6)"), "MIPS=主频/(CPI×10<sup>6</sup>)");
// 括号式是自定界的，多字母底数（函数名）照收：log_(a) x、Qe^(∫Pdx)
check("函数名底数 log_(a) x", richText("(log_(a) x)′ = 1/(x ln a)"), "(log<sub>a</sub> x)′ = 1/(x ln a)");
check("乘积底数 Qe^(∫Pdx)", richText("y=e^(-∫Pdx)[∫Qe^(∫Pdx)dx+C]"),
  "y=e<sup>-∫Pdx</sup>[∫Qe<sup>∫Pdx</sup>dx+C]");
check("中文后面的括号式不动", richText("见第5章_(x)"), "见第5章_(x)");
// 括号内容里带反斜杠 → 是 LaTeX 残段，整体放弃交给 KaTeX 或原样显示
check("括号内含反斜杠则跳过", richText("x^{2\\3}"), "x^{2\\3}");
// $...$ 与 ASCII 记号混在一句里，各走各的
globalThis.katex = {
  renderToString(tex, opts) { mathCalls.push({ tex, display: !!opts.displayMode }); return "<KATEX>" + tex + "</KATEX>"; },
};
check("$公式$ 与 x_i 混排", richText("由 $\\sum a_n$ 得 x_i 收敛"),
  "由 <KATEX>\\sum a_n</KATEX> 得 x<sub>i</sub> 收敛");
// 2^32B 这类「上标后面还粘着字母」的写法语义有歧义（2³² B 还是 2^(32B)？），
// 渲染层故意不动，交给题库数据层写成 $2^{32}$ B —— 别在这里"顺手"改掉。
check("歧义写法 2^32B 保持原样", richText("主存4GB=2^32B"), "主存4GB=2^32B");

console.log("\n【早间回顾那种「本来就带 HTML」的字段】只转标签外的文本");
// 2026-09-22 用户截图：数学要点的标题/正文里 `(1+x)^α`、`(−1)^{n−1}` 原样漏出。
// 那份数据（morning_review.json）的字段本身就是 HTML 片段（<strong>/<code>/<span>），
// 不能走 richText（会先转义、把标签变成可见源码），所以单独有一版 asciiMathHtml。
check("标签外的 2^n / (1+x)^α 照转", asciiMathHtml("<strong>2^n</strong> 与 (1+x)^α"),
  "<strong>2<sup>n</sup></strong> 与 (1+x)<sup>α</sup>");
check("整段括号式 (−1)^{n−1}", asciiMathHtml("系数 (−1)^{n−1}/n"), "系数 (−1)<sup>n−1</sup>/n");
check("<code> 的内容不动（与笔记/闪卡「代码里的 _ ^ 不当上下标」同一条规矩）",
  asciiMathHtml("极限 <code>lim_{x→0}f'(x)</code>"), "极限 <code>lim_{x→0}f'(x)</code>");
check("标签属性里的 _ / ^ 也不会被啃",
  asciiMathHtml('<span data-a_b="1">x^2</span>'), '<span data-a_b="1">x<sup>2</sup></span>');
check("没有 ^ / _ 的字段原样返回（快路径）",
  asciiMathHtml("普通正文，一个记号都没有"), "普通正文，一个记号都没有");

console.log("\n【asciiMath 的两处扩建】希腊字母 + Unicode 下标跟着上标走");
check("希腊字母上标 (1+x)^α", asciiMath("(1+x)^α"), "(1+x)<sup>α</sup>");
// ⚠️ 只吃 1 个字符会得到 e<sup>u</sup>ₙ —— 下标挂到外面、意思直接错（真实语料里抓到）
check("Unicode 下标一起收进上标 e^uₙ−1", asciiMath("e^uₙ−1"), "e<sup>uₙ</sup>−1");
check("原有规则没退化", asciiMath("O(2^n) 与 W_T"), "O(2<sup>n</sup>) 与 W<sub>T</sub>");
check("文件名照旧不误判", asciiMath("第5章_IO管理.md 与 book_id"), "第5章_IO管理.md 与 book_id");

console.log("\n【ASCII 上下标·与既有规则不打架】");
globalThis.katex = {
  renderToString(tex, opts) { mathCalls.push({ tex, display: !!opts.displayMode }); return "<KATEX>" + tex + "</KATEX>"; },
};
mathCalls.length = 0;
check("$...$ 里的 ^ _ 仍归 KaTeX，不被 asciiMath 抢", richText("$10^6$ 与 $x_{i}$"),
  "<KATEX>10^6</KATEX> 与 <KATEX>x_{i}</KATEX>");
checkTrue("KaTeX 收到的是未改动的 LaTeX 原文",
  mathCalls.some(c => c.tex === "10^6") && mathCalls.some(c => c.tex === "x_{i}"),
  "收到的 tex: " + JSON.stringify(mathCalls.map(c => c.tex)));
check("行内代码里的 _ 原样保留", richText("写 `a_i` 即可"),
  '写 <code class="md-code">a_i</code> 即可');
check("粗体与上下标共存", richText("**重点**：x_j 的系数"),
  "<strong>重点</strong>：x<sub>j</sub> 的系数");
check("转义仍生效（上下标内容里的尖括号）", richText("a<b 且 x_i"), "a&lt;b 且 x<sub>i</sub>");

console.log("\n" + "=".repeat(52));
console.log(`通过 ${pass} 项，失败 ${fail} 项`);
console.log("=".repeat(52));
process.exit(fail ? 1 : 0);

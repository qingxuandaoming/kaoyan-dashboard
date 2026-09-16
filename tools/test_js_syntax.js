/*
 * 前端 JS 常量的语法守卫（2026-09-22）
 *
 * 为什么需要它：generate_dashboard.py 里的 `*_JS` 常量是**普通 Python 字符串**，
 * 里面写单个 `\n` 会被 Python 先变成真换行 → JS 字符串当场断开 → 整段脚本
 * SyntaxError，页面上那一块**什么都点不动**，而其他测试（结构/桩）全都照样绿。
 * 这个坑已经踩过两次（本次是 RV_JS 里的 `split("\n")`），所以立一道闸：
 * 把每个 `*_JS` 常量抽出来，逐个 `new Function(...)` 编译一遍 —— 编译不过就是失败。
 *
 * 只编译不执行：没有 DOM、没有副作用，跑得飞快。
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const SRC = path.join(__dirname, "..", "generate_dashboard.py");
const OUT = path.join(os.tmpdir(), "kaoyan_js_consts.json");
const PY_CANDIDATES = [
  process.env.PYTHON,
  "C:\\Users\\92534\\AppData\\Local\\Programs\\Python\\Python311\\python.exe",
  "python", "python3",
].filter(Boolean);

// 用 Python 的 ast 取字面量（自己解析 Python 字符串必踩坑）
const PY_CODE = `
import ast, io, json, sys
src = io.open(r"${SRC}", encoding="utf-8").read()
tree = ast.parse(src)
out = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and node.targets and hasattr(node.targets[0], "id"):
        name = node.targets[0].id
        if name.endswith("_JS"):
            try:
                out[name] = ast.literal_eval(node.value)
            except Exception:
                out[name] = None   # 不是纯字面量（拼接出来的），跳过
io.open(r"${OUT}", "w", encoding="utf-8").write(json.dumps(
    {k: v for k, v in out.items() if v is not None}, ensure_ascii=False))
print("ok")
`;

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

let consts = {};
let extracted = false;
let lastErr = "";
for (const py of PY_CANDIDATES) {
  try {
    execFileSync(py, ["-c", PY_CODE], { maxBuffer: 1 << 24, stdio: "pipe" });
    consts = JSON.parse(fs.readFileSync(OUT, "utf-8"));
    extracted = true;
    break;
  } catch (e) { lastErr = e.message; }
}
try { fs.unlinkSync(OUT); } catch (e) {}

console.log("\n[1] 抽取 *_JS 常量");
{
  check("用 Python ast 抽到了前端 JS 常量", extracted, lastErr.slice(0, 200));
  const names = Object.keys(consts);
  // 少一个就说明抽取方式坏了（改名/换写法），别让「一个都没抽到」变成假绿
  check("抽到的常量不少于 10 个（防止抽空了还判通过）", names.length >= 10, names.join(","));
  ["FLASH_JS", "RV_JS", "POMO_JS", "SETTINGS_JS", "SHELL_JS", "NOTEQ_JS", "MR_JS", "NAV_JS", "TASK_JS", "DECK_JS", "REVIVE_JS"]
    .forEach(n => check("抽到了 " + n, !!consts[n]));
}

console.log("\n[2] 逐个编译（编译不过 = 那一块在页面上彻底不能点）");
{
  for (const name of Object.keys(consts).sort()) {
    const js = consts[name];
    let err = "";
    try { new Function(js); }
    catch (e) { err = e.message; }
    check(name + " 语法正确", !err, err);
  }
}

console.log("\n[3] 抽出来的内容没被截断 / 没有隐形断行符");
{
  const names = Object.keys(consts);
  const empty = names.filter(n => String(consts[n]).trim().length < 100);
  check("每个常量都有实质内容（防止抽成空串还判通过）", empty.length === 0, empty.join(","));
  const total = names.reduce((a, n) => a + String(consts[n]).length, 0);
  check("JS 总量在合理量级（10 万字符以上）", total > 100000, String(total));
  // U+2028/2029 在 JS 里是合法换行但对老引擎是隐形杀手；本项目不用它
  const ctrl = names.filter(n => /[\u2028\u2029]/.test(String(consts[n])));
  check("没有 U+2028/2029 这类隐形断行符", ctrl.length === 0, ctrl.join(","));
  // 顶格（第 0 列）的 return：函数体里的 return 都是缩进的，顶格说明常量被切坏了
  const naked = names.filter(n => /^return\b/m.test(String(consts[n])));
  check("没有顶格的 return（那说明常量被切坏了）", naked.length === 0, naked.join(","));
}

console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
process.exit(fail === 0 ? 0 : 1);

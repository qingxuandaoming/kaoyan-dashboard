/*
 * 学习日起点（凌晨 4 点前算前一天）的一致性测试。
 *
 * 这件事的要害是「两边必须一样」：serve.js 用 DAY_START_HOUR 算服务端今天，
 * generate_dashboard.py 把同一个值注入页面给 FLASH_JS / POMO_JS 用。
 * 差一天不是显示难看的问题——POMO_JS 会拿本地的 todayKey 去跟服务端返回的
 * today 比，判定「服务端那份属于新的一天」时会把刚记上的成绩覆盖成 0。
 * 所以这个文件守两件事：
 *   ① 两边的常量还相等
 *   ② 边界算得对（3:59 算前一天，4:00 算当天，跨月/跨年也别错）
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const SRC = path.join(__dirname, "..", "generate_dashboard.py");
const SERVE = path.join(__dirname, "..", "serve.js");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

/* ---- ① 两边的常量必须相等 ---- */
const serveSrc = fs.readFileSync(SERVE, "utf-8");
const genSrc = fs.readFileSync(SRC, "utf-8");
const mServe = /^const DAY_START_HOUR = (\d+);/m.exec(serveSrc);
const mGen = /^DAY_START_HOUR = (\d+)$/m.exec(genSrc);

console.log("\n[1] 两边的 DAY_START_HOUR 必须相等");
check("serve.js 里有这个常量", !!mServe, String(mServe));
check("generate_dashboard.py 里有这个常量", !!mGen, String(mGen));
check("★ 两处值一致", mServe && mGen && mServe[1] === mGen[1],
  (mServe && mServe[1]) + " vs " + (mGen && mGen[1]));

const HOUR = mServe ? Number(mServe[1]) : 4;
check("起点在 0~8 点之间（再离谱就是写错了）", HOUR >= 0 && HOUR <= 8, String(HOUR));

/* ---- ② 把 serve.js 的两个函数抠出来真跑一遍 ---- */
// serve.js 是完整服务，不能 require。用 new Function 把函数体摘出来单独跑，
// 只给它一个最小的 FSRS.localDateStr 桩。
function extract(name) {
  const re = new RegExp("^function " + name + "\\([^)]*\\) \\{[\\s\\S]*?\\n\\}", "m");
  const m = re.exec(serveSrc);
  if (!m) throw new Error("摘不出 " + name);
  return m[0];
}
const FSRS = {
  localDateStr(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0")
         + "-" + String(d.getDate()).padStart(2, "0");
  },
};
const sandbox = new Function("FSRS", "DAY_START_HOUR",
  extract("studyDayStart") + "\n" + extract("localToday")
  + "\nreturn { studyDayStart, localToday };")(FSRS, HOUR);

const day = (y, mo, d, h, mi) => new Date(y, mo - 1, d, h, mi || 0, 0);
const cases = [
  // [说明, 时刻, 期望的学习日]
  ["刚过午夜 00:30 → 还算前一天", day(2026, 9, 17, 0, 30), "2026-09-16"],
  ["凌晨 3:59 → 还算前一天", day(2026, 9, 17, 3, 59), "2026-09-16"],
  ["凌晨 4:00 整 → 翻篇", day(2026, 9, 17, 4, 0), "2026-09-17"],
  ["早上 9:00 → 当天", day(2026, 9, 17, 9, 0), "2026-09-17"],
  ["晚上 23:59 → 当天", day(2026, 9, 17, 23, 59), "2026-09-17"],
  ["跨月：9/1 凌晨 2 点 → 8/31", day(2026, 9, 1, 2, 0), "2026-08-31"],
  ["跨年：1/1 凌晨 2 点 → 去年 12/31", day(2027, 1, 1, 2, 0), "2026-12-31"],
];

console.log("\n[2] 边界算得对（起点 = " + HOUR + ":00）");
if (HOUR === 0) {
  // 起点设成 0 就是「不启用」，边界等同于原来的日历日
  check("起点为 0 时退回日历日（不启用）", sandbox.localToday(day(2026, 9, 17, 0, 30)) === "2026-09-17",
    sandbox.localToday(day(2026, 9, 17, 0, 30)));
} else {
  for (const [name, when, want] of cases) {
    // 已经设成 0 的用例跳过（起点不是 0 时才成立）
    if (HOUR === 0 && want !== name) continue;
    const got = sandbox.localToday(when);
    check(name, got === want, "得到 " + got + "，期望 " + want);
  }
}

console.log("\n[3] studyDayStart() 给的是起点时刻，不是午夜");
{
  const s = sandbox.studyDayStart(day(2026, 9, 17, 2, 0));
  check("凌晨 2 点 → 前一天的 " + HOUR + ":00:00", s === "2026-09-16 " + String(HOUR).padStart(2, "0") + ":00:00", s);
  const s2 = sandbox.studyDayStart(day(2026, 9, 17, 8, 0));
  check("早上 8 点 → 当天的 " + HOUR + ":00:00", s2 === "2026-09-17 " + String(HOUR).padStart(2, "0") + ":00:00", s2);
}

/* ---- ③ 前端那份必须是注入的，不能各写各的 ---- */
console.log("\n[4] 前端用的是注入的 studyDay()，不是本地再算一遍");
{
  const out = path.join(os.tmpdir(), "kaoyan_daystart_page.html");
  execFileSync("python", ["-c", `
import io, sys
sys.path.insert(0, r"${path.join(__dirname, "..")}")
html = io.open(r"${path.join(__dirname, "..", "dashboard.html")}", encoding="utf-8").read()
io.open(r"${out}", "w", encoding="utf-8").write(html)
`], { maxBuffer: 1 << 26 });
  const page = fs.readFileSync(out, "utf-8");
  check("页面里注入了 const DAY_START_HOUR", new RegExp("const DAY_START_HOUR = " + HOUR + ";").test(page));
  check("页面里有 studyDay()", /function studyDay\(d\) \{/.test(page));
  check("页面里没有漏掉的裸 todayStr 实现（应该只剩 return studyDay()）",
    !/return d\.getFullYear\(\) \+ "-" \+ String\(d\.getMonth/.test(page));
  check("POMO 的 todayKey 走 studyDay", /function todayKey\(\) \{ return studyDay\(\); \}/.test(page));
}

console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
process.exit(fail === 0 ? 0 : 1);

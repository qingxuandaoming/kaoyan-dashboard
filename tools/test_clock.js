/*
 * 时间校准（src/clock.js）测试。
 *
 * 这件事的要害是「大家站在同一个基准上」：番茄钟的 end_at 是绝对时刻，写进共享的
 * pomo_run，谁读谁算剩余时间。服务端对一次表（联网=外部标准时间，离线=本机时钟），
 * 客户端读同一份偏差。所以这个文件守四件事：
 *   ① 往返中点补偿算得对（不补的话偏差里会掺进 RTT/2 的固定误差）
 *   ② 多源采样里挑得对（剔除离谱值、挑延迟最小的）
 *   ③ 保鲜期 / 本机时间倒退时退回本机时钟（与前端 effectiveOffset 同规则）
 *   ④ 落盘 / 读盘 / 坏文件 / 全失败 的兜底行为
 * 另外对拍一下 App 端的常量与文件名，防止两边各写各的漂开。
 */
const fs = require("fs");
const os = require("os");
const path = require("path");

const clockMod = require("../clock.js");
const {
  createClock, offsetFromSample, pickSample, effectiveOffset,
  FRESH_MS, OFFSET_LIMIT_MS,
} = clockMod;

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

const tmpFile = () => path.join(os.tmpdir(),
  "kaoyan_clock_" + process.pid + "_" + Math.random().toString(36).slice(2) + ".json");
const TMP = [];

(async () => {

console.log("\n[1] offsetFromSample：往返中点补偿");
{
  // 本机慢 7 秒、一次往返 400ms：服务端标的是「往返中点」那一刻的时间
  const mid = 1000 + 200 + 7000;
  check("400ms 往返 → 偏差正好 7000（不含 rtt/2 的 200ms 误差）",
    offsetFromSample(1000, mid, 1400) === 7000,
    String(offsetFromSample(1000, mid, 1400)));
  check("零延迟 → 就是「服务器时刻 − 本机时刻」",
    offsetFromSample(1000, 8000, 1000) === 7000,
    String(offsetFromSample(1000, 8000, 1000)));
  check("本机快 3 秒 → 偏差为负",
    offsetFromSample(1000, 1000 + 100 - 3000, 1200) === -3000,
    String(offsetFromSample(1000, 1000 + 100 - 3000, 1200)));
  check("t1 < t0（时钟回拨）时 RTT 夹到 0，不炸出负值/NaN",
    offsetFromSample(1400, 7000, 1000) === 6000,
    String(offsetFromSample(1400, 7000, 1000)));
}

console.log("\n[2] pickSample：剔除离谱值、挑延迟最小的");
{
  check("空样本 → null", pickSample([]) === null);
  check("只有错误样本 → null", pickSample([{ name: "a", error: "x" }]) === null);
  const s = pickSample([
    { name: "slow", rtt: 900, offset: 7000 },
    { name: "fast", rtt: 40, offset: 7000 },
    { name: "absurd", rtt: 10, offset: OFFSET_LIMIT_MS + 1 },
  ]);
  check("挑 RTT 最小的那份（延迟越小，中点假设越准）", s && s.name === "fast", JSON.stringify(s));
  check("偏差超过一天的直接剔除",
    pickSample([{ name: "x", rtt: 5, offset: OFFSET_LIMIT_MS + 1 }]) === null);
  check("偏差刚好等于上限仍算合格",
    pickSample([{ name: "x", rtt: 5, offset: OFFSET_LIMIT_MS - 1 }]) !== null);
}

console.log("\n[3] effectiveOffset：保鲜期内才认，本机时间倒退不认");
{
  const T = 1770000000000;
  check("没校准过 → 0", effectiveOffset(null, T) === 0);
  check("synced_at=0 → 0", effectiveOffset({ offset_ms: 7000, synced_at: 0 }, T) === 0);
  check("保鲜期内 → 用偏差", effectiveOffset({ offset_ms: 7000, synced_at: T - 1000 }, T) === 7000);
  check("刚好 24h → 还算有效", effectiveOffset({ offset_ms: 7000, synced_at: T - FRESH_MS }, T) === 7000);
  check("过了 24h → 退回 0",
    effectiveOffset({ offset_ms: 7000, synced_at: T - FRESH_MS - 1 }, T) === 0);
  check("本机时间倒退（synced_at 成了未来）→ 退回 0",
    effectiveOffset({ offset_ms: 7000, synced_at: T + 5000 }, T) === 0);
}

console.log("\n[4] sync：多源并发，挑最优并落盘");
let local = 1000000;
{
  const file = tmpFile(); TMP.push(file);
  // 服务端标准时间 = 本机 + 7000；两个源的往返分别是 200ms / 40ms
  const fetchText = async (url) => {
    const rtt = url === "slow" ? 200 : 40;
    const t0 = local;
    const serverMs = t0 + rtt / 2 + 7000;
    local = t0 + rtt;                 // 推完整个往返
    return { text: String(serverMs), date: "" };
  };
  const sources = [
    { name: "slow", url: "slow", parse: (r) => Number(r.text) },
    { name: "fast", url: "fast", parse: (r) => Number(r.text) },
  ];
  const c = createClock({ file, now: () => local, fetchText, sources, log() {}, warn() {} });
  const st = await c.sync();
  check("校准成功", st.calibrated === true);
  check("挑中 RTT 最小的源", st.source === "fast", st.source);
  check("偏差 = 7000（两个源都算对了，只是挑更快的）", st.offset_ms === 7000, String(st.offset_ms));
  check("now() 用上了偏差", c.now() === local + 7000, String(c.now()));
  check("status.now 与 now() 一致", st.now === c.now());
  check("status 带前端要用的字段",
    ["calibrated", "now", "sys_now", "offset_ms", "source", "synced_at", "age_ms", "fresh_ms", "rtt_ms", "syncing"]
      .every((k) => k in st),
    JSON.stringify(Object.keys(st)));
  const saved = JSON.parse(fs.readFileSync(file, "utf-8"));
  check("落盘 offset_ms", saved.offset_ms === 7000, String(saved.offset_ms));
  check("落盘 source", saved.source === "fast", String(saved.source));
  check("落盘 rtt_ms（排查用）", saved.rtt_ms === 40, String(saved.rtt_ms));
}

console.log("\n[5] 全部失败：退回本机时钟，且不覆盖上次的好结果");
{
  const file = tmpFile(); TMP.push(file);
  const c = createClock({
    file, now: () => local,
    fetchText: async () => { throw new Error("ENOTFOUND"); },
    sources: [{ name: "s1", url: "u", parse: () => 1 }],
    log() {}, warn() {},
  });
  const st = await c.sync();
  check("未校准", st.calibrated === false);
  check("退回本机时钟（偏差 0）", st.offset_ms === 0, String(st.offset_ms));
  check("now() = 本机时刻", c.now() === local, String(c.now()));
  check("source 记 system", st.source === "system", st.source);
  check("记下失败原因（便于排查）", /ENOTFOUND/.test(st.last_error), st.last_error);
  check("失败不写文件（别拿空的覆盖上次的好结果）", !fs.existsSync(file));
}

console.log("\n[6] 解析不出 / 离谱的源：不影响其它源");
{
  const file = tmpFile(); TMP.push(file);
  const sources = [
    { name: "bad", url: "bad", parse: () => NaN },
    { name: "absurd", url: "absurd", parse: () => 9e15 },
    { name: "good", url: "good", parse: (r) => Number(r.text) },
  ];
  const fetchText = async (url) => {
    const t0 = local;
    const serverMs = url === "good" ? t0 + 7000 : 0;
    local = t0 + 20;
    return { text: String(serverMs), date: "" };
  };
  const c = createClock({ file, now: () => local, fetchText, sources, log() {}, warn() {} });
  const st = await c.sync();
  check("仍能校准", st.calibrated === true);
  check("挑到唯一合格的那份", st.source === "good", st.source);
  check("偏差算对", st.offset_ms === 6990, String(st.offset_ms));
}

console.log("\n[7] 读盘：已落盘的结果直接用，过期的退回本机");
{
  const T = 1770000000000;
  const file = tmpFile(); TMP.push(file);
  const mk = () => createClock({
    file, now: () => T,
    fetchText: async () => { throw new Error("offline"); },
    log() {}, warn() {},
  });
  fs.writeFileSync(file, JSON.stringify(
    { offset_ms: 7000, synced_at: T - 60000, source: "taobao", rtt_ms: 361 }), "utf-8");
  const c1 = mk();
  check("读盘后直接已校准", c1.status().calibrated === true);
  check("偏差沿用盘里那份", c1.status().offset_ms === 7000, String(c1.status().offset_ms));
  check("now() 带上偏差", c1.now() === T + 7000, String(c1.now()));

  fs.writeFileSync(file, JSON.stringify(
    { offset_ms: 7000, synced_at: T - FRESH_MS - 1, source: "taobao" }), "utf-8");
  const c2 = mk();
  check("过期的对表结果 → 退回本机时钟",
    c2.status().calibrated === false && c2.status().offset_ms === 0);
  check("但仍记着 synced_at（好提示「上次对表已过期」）",
    c2.status().synced_at === T - FRESH_MS - 1, String(c2.status().synced_at));
  check("过期时 source 回落 system", c2.status().source === "system", c2.status().source);
}

console.log("\n[8] 坏文件 / 无文件：不抛异常");
{
  const T = 1770000000000;
  const bad = tmpFile(); TMP.push(bad);
  fs.writeFileSync(bad, "{ 这不是 json", "utf-8");
  const c1 = createClock({
    file: bad, now: () => T, fetchText: async () => { throw new Error("x"); }, log() {}, warn() {},
  });
  check("坏 JSON → 退回本机时钟，不抛", c1.status().calibrated === false && c1.now() === T);

  const none = tmpFile(); TMP.push(none);
  const c2 = createClock({
    file: none, now: () => T, fetchText: async () => { throw new Error("x"); }, log() {}, warn() {},
  });
  check("文件不存在 → 退回本机时钟，不抛",
    c2.status().calibrated === false && c2.status().source === "system");
}

console.log("\n[9] 并发 sync 只问一次外部网络");
{
  let calls = 0;
  const c = createClock({
    file: tmpFile(), now: () => local,
    fetchText: async () => {
      calls++;
      await new Promise((r) => setTimeout(r, 10));
      return { text: "1", date: "" };
    },
    sources: [{ name: "x", url: "u", parse: () => local + 5000 }],
    log() {}, warn() {},
  });
  const p1 = c.sync(), p2 = c.sync();
  check("并发调用返回同一个 promise", p1 === p2);
  await p1; await p2;
  check("只发了一次请求", calls === 1, String(calls));
}

console.log("\n[10] 与 App 端对齐：同一份文件、同一个保鲜期");
{
  const dart = fs.readFileSync(
    path.join(__dirname, "..", "..", "app", "lib", "core", "clock.dart"), "utf-8");
  const pathsDart = fs.readFileSync(
    path.join(__dirname, "..", "..", "app", "lib", "core", "paths.dart"), "utf-8");
  check("App 端 freshMs 也是 24h",
    /freshMs = 24 \* 3600 \* 1000/.test(dart), "App 端未对齐 FRESH_MS");
  check("App 端读同一个 time_cal.json",
    /time_cal\.json/.test(pathsDart), "paths.dart 里没有 time_cal.json");
  check("App 端也是「过期/倒退就退回 0」",
    /offsetMs => _fresh \? _offsetMs : 0/.test(dart), "App 端偏差规则与 clock.js 不一致");
}

for (const f of TMP) { try { fs.unlinkSync(f); } catch (e) {} }

console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
process.exit(fail === 0 ? 0 : 1);

})();
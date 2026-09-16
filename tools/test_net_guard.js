/*
 * 网络闸门测试（2026-09-21）
 *
 * 为什么单独有这个文件：服务要监听 0.0.0.0 才能让平板连上，可一旦路由把端口转发出去，
 * 公网来客就能白用 AI 额度、甚至改写 API Key。所以吃 key 的接口只放行内网来源。
 * isPrivateAddress 是纯函数（好测），直接从 serve.js 抠出来跑；路径判定同理。
 */
const fs = require("fs");
const path = require("path");

const SRC = path.join(__dirname, "..", "serve.js");
const src = fs.readFileSync(SRC, "utf-8");

// 按括号配平从源码里抠出「const NAME = …;」（serve.js 一 require 就起服务，不能直接引）。
// ⚠️ 不能假设函数体是块：isSensitivePath 是单表达式箭头函数，按 { } 配对会抠到别处去。
function grab(name) {
  const idx = src.indexOf("const " + name + " = ");
  if (idx < 0) return null;
  let depth = 0, i = src.indexOf("=", idx);
  for (; i < src.length; i++) {
    const ch = src[i];
    if (ch === "(" || ch === "[" || ch === "{") depth++;
    else if (ch === ")" || ch === "]" || ch === "}") depth--;
    else if (ch === ";" && depth === 0) return src.slice(idx, i + 1);
  }
  return null;
}
// SENSITIVE_PATHS 也要一起抠（isSensitivePath 依赖它）
const arr = (() => {
  const idx = src.indexOf("const SENSITIVE_PATHS = [");
  if (idx < 0) return null;
  const end = src.indexOf("];", idx);
  return end < 0 ? null : src.slice(idx, end + 2);
})();
const body = [arr, grab("isPrivateAddress"), grab("isSensitivePath")].filter(Boolean).join("\n");
if (!body) { console.log("没能从 serve.js 抠出闸门函数"); process.exit(1); }
const sandbox = {};
new Function("exports", body + "\nexports.isPrivateAddress = isPrivateAddress; exports.isSensitivePath = isSensitivePath;")(sandbox);
const { isPrivateAddress, isSensitivePath } = sandbox;

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

console.log("\n[1] 内网/本机地址要放行（平板走局域网，不能被挡）");
[
  ["127.0.0.1", "本机 IPv4"],
  ["::1", "本机 IPv6"],
  ["::ffff:127.0.0.1", "本机（IPv4-mapped）"],
  ["localhost", "字面 localhost"],
  ["192.168.1.23", "常见家用网段"],
  ["10.0.0.7", "10/8"],
  ["172.16.5.9", "172.16/12 下界"],
  ["172.31.255.254", "172.16/12 上界"],
  ["169.254.3.3", "链路本地"],
  ["100.101.102.103", "CGNAT / Tailscale"],
  ["::ffff:192.168.0.9", "内网（IPv4-mapped）"],
  ["fd00::5", "IPv6 唯一本地"],
  ["fe80::1", "IPv6 链路本地"],
].forEach(([ip, why]) => check("放行 " + ip + "（" + why + "）", isPrivateAddress(ip) === true, String(isPrivateAddress(ip))));

console.log("\n[2] 公网地址要挡住");
[
  ["8.8.8.8", "公网 DNS"],
  ["1.1.1.1", "公网 DNS"],
  ["172.32.0.1", "刚出 172.16/12 上界"],
  ["172.15.0.1", "刚出下界"],
  ["11.0.0.1", "挨着 10/8 但不在里面"],
  ["192.169.1.1", "挨着 192.168 但不在里面"],
  ["100.63.0.1", "刚出 CGNAT 下界"],
  ["100.128.0.1", "刚出 CGNAT 上界"],
  ["2001:4860:4860::8888", "公网 IPv6"],
  ["", "空地址"],
  ["不是IP", "垃圾输入"],
  ["999.1.1.1", "非法段"],
  ["192.168.1", "段数不够"],
].forEach(([ip, why]) => check("挡住 " + (ip || "(空)") + "（" + why + "）", isPrivateAddress(ip) === false, String(isPrivateAddress(ip))));

console.log("\n[3] 只有吃 key / 能改配置的路径受闸门管");
[
  ["/api/explain", true], ["/api/explain/followup", true], ["/api/notes/ask", true],
  ["/api/study/chat", true], ["/api/grade", true], ["/api/settings", true],
  ["/api/ark/v3/chat", true],
  ["/dashboard.html", false], ["/api/flashcards/session", false],
  ["/api/notes/search", false], ["/api/notes/qa?path=x", false], ["/api/pomodoro/state", false],
].forEach(([u, want]) => check((want ? "受管 " : "不受管 ") + u, isSensitivePath(u) === want, String(isSensitivePath(u))));

console.log("\n[4] 闸门本身接在请求入口上，且有环境变量可以显式关掉");
check("请求入口调用了 isSensitivePath", /isSensitivePath\(req\.url\)/.test(src));
check("非内网来源直接 403", /403[\s\S]{0,200}只允许局域网\/本机访问/.test(src));
check("拒绝时会写日志（便于排查）", /\[Guard\] 拒绝来自公网地址/.test(src));
check("ALLOW_PUBLIC_AI=1 可以关掉这道闸", /ALLOW_PUBLIC_AI !== '1'/.test(src));
check("CORS 不再无条件放 * （防任意网页跨域白用额度）",
  !/setHeader\('Access-Control-Allow-Origin', '\*'\)/.test(src));
check("同源才回 CORS 头", /reqOrigin === 'http:\/\/' \+ host/.test(src));
check("密钥只留尾 4 位下发（不泄露原文）", /slice\(-4\)/.test(src));
check("没有任何接口把 api_key 原文回给客户端",
  !/sendJson\([^)]*apiKey\b/.test(src) && !/json\(\{\s*[^}]*apiKey\s*:/.test(src));

console.log("\n[5] 公式组件 KaTeX 的静态路由（2026-09-21：白名单收紧后它曾 404）");
check("有 /tools/katex/ 专用路由",
  /pathname\.indexOf\('\/tools\/katex\/'\) === 0/.test(src));
check("路径穿越被挡（resolve 后必须还在 tools/katex 内）",
  /const inside = abs === KATEX_ROOT \|\| abs\.startsWith\(KATEX_ROOT \+ path\.sep\)/.test(src)
  && /if \(!inside \|\| !mime\)/.test(src));
check("只放行 js/css/mjs/字体（挡 .json/.db/.secrets）",
  /KATEX_MIME = \{/.test(src) && /'\.woff2': 'font\/woff2'/.test(src)
  && !/KATEX_MIME[\s\S]{0,400}?\.json/.test(src));
check("其余路径仍走白名单（老洞不能回来）",
  /const STATIC_PAGES = \{/.test(src) && /'\/dashboard\.html': 'dashboard\.html'/.test(src)
  && !/STATIC_PAGES[\s\S]{0,300}?secrets/.test(src));

console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
process.exit(fail === 0 ? 0 : 1);
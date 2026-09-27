/*
 * 归因层（attribution_spec.json + tools/ai_attribute_notes.js + note_attribution.py）的离线测试。
 *
 * 这一层把「这段笔记属于哪个考点」从人肉规则换成了模型判定，所以必须守住三件事：
 *   ① 规格自洽（阈值、kinds、domain 字段别写歪 —— 它是唯一的判定出处）
 *   ② 模型的输出过得了闸（编造的考点 id 必须被丢弃，不许静默进库）
 *   ③ 跨语言口径一致（spec 的 accept 与 Python 读取层的兜底值必须相等，
 *      否则 JS 判「跑完了」、Python 判「没跑完」，覆盖口径会来回跳）
 * 全部不碰 API：切段、校验、兜底解析都是纯函数。
 */
const fs = require("fs");
const path = require("path");

const SRC = path.join(__dirname, "..");
const eng = require("./ai_attribute_notes.js");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

const spec = JSON.parse(fs.readFileSync(path.join(SRC, "attribution_spec.json"), "utf-8"));

/* ---- ① 规格自洽 ---- */
console.log("\n[1] attribution_spec.json 自洽");
check("kinds 非空且每条有 id/counts_coverage",
  spec.kinds.length >= 5 && spec.kinds.every(k => k.id && typeof k.counts_coverage === "boolean"),
  JSON.stringify(spec.kinds.map(k => k.id)));
check("thresholds.review < accept（否则待确认带是空的）",
  spec.thresholds.review < spec.thresholds.accept,
  spec.thresholds.review + " vs " + spec.thresholds.accept);
check("accept 在 0.5~0.9 之间（低于 0.5 等于不信门槛，高于 0.9 覆盖率会假低）",
  spec.thresholds.accept >= 0.5 && spec.thresholds.accept <= 0.9, String(spec.thresholds.accept));
check("prompt_version 非空", !!spec.prompt_version, spec.prompt_version);
check("batch.units_per_call >= 1", spec.batch.units_per_call >= 1, String(spec.batch.units_per_call));
check("unit.max_chars > unit.min_chars", spec.unit.max_chars > spec.unit.min_chars,
  spec.unit.min_chars + "/" + spec.unit.max_chars);
check("每个 domain 都有 id/graph_file/corpus_root",
  spec.domains.every(d => d.id && d.graph_file && d.corpus_root),
  JSON.stringify(spec.domains.map(d => d.id)));
check("每个 domain 的图谱文件真在磁盘上",
  spec.domains.every(d => fs.existsSync(path.join(SRC, "knowledge_graph", d.graph_file))));
check("每个 domain 的语料目录在笔记根下存在",
  spec.domains.every(d => {
    const P = require("../paths");
    return fs.existsSync(path.join(P.NOTES_ROOT, d.corpus_root));
  }));
check("计入覆盖的 kind 至少 2 个（一个都没有 = 覆盖永远 0）",
  spec.kinds.filter(k => k.counts_coverage).length >= 2);
check("参考资料类目录被挡在语料外（教材不是用户笔记）",
  (spec.corpus.ignore_dirs || []).some(x => x.indexOf("参考") >= 0),
  JSON.stringify(spec.corpus.ignore_dirs));

/* ---- ② 切段 ---- */
console.log("\n[2] splitUnits 按标题切材料单元");
const md = [
  "# 第5章 树与二叉树 — 笔记",
  "<!-- note-meta:entry",
  "id: 408-DS-005",
  "chapter: 第5章",
  "-->",
  "",
  "## 5.2 二叉树的概念",
  "二叉树是每个结点至多两棵子树的树形结构，且左右子树有先后次序，不能颠倒。",
  "性质1：第 i 层至多 2^(i-1) 个结点。",
  "",
  "![配图](assets/x.png)",
  "### 5.2.1 细枝末节",
  "这一小节的内容也应当单独成段，不该和上面混在一起，因为它讲的是另一个知识点。",
  "",
  "## 太短",
  "一句",
].join("\n");
const units = eng.splitUnits("408/DS/第5章_树与二叉树.md", md, spec);
check("切出多于 1 段", units.length >= 2, units.length + " 段");
check("note-meta 注释块没被当正文",
  !units.some(u => u.text.indexOf("note-meta") >= 0 || u.text.indexOf("408-DS-005") >= 0));
check("图片引用被剥掉", !units.some(u => u.text.indexOf("assets/x.png") >= 0));
const h2 = units.find(u => u.heading.indexOf("5.2 二叉树的概念") >= 0);
check("h2 成段且 heading 带上 h1 路径", !!h2 && h2.heading.indexOf("第5章") >= 0,
  h2 && h2.heading);
check("h1 自己不成段（它只是标题）", !units.some(u => u.heading === "第5章 树与二叉树 — 笔记"));
const h3 = units.find(u => u.heading.indexOf("5.2.1") >= 0);
check("h3 独立成段（粒度到小节，才能归到具体考点）", !!h3, JSON.stringify(units.map(u => u.heading)));
check("低于 min_chars 的段被丢掉", !units.some(u => u.heading.indexOf("太短") >= 0),
  JSON.stringify(units.map(u => u.heading)));
check("rel 与 chars 带上了", units.every(u => u.rel && u.chars > 0));

/* ---- ③ 校验闸门 ---- */
console.log("\n[3] normalizeVerdict：模型输出不许直接进库");
const tax = eng.loadTaxonomy(spec.domains.find(d => d.id === "kaoyan-408"));
const validId = tax.topics[0].id;
const mk = (v) => eng.normalizeVerdict(v, units, tax, spec);

check("合法考点 id 通过", mk({ i: 0, kind: "knowledge_note", topics: [validId], confidence: 0.9 }).topics[0] === validId);
const fake = mk({ i: 0, kind: "knowledge_note", topics: [validId, "NOT-A-TOPIC-99"], confidence: 0.9 });
check("★ 编造的考点 id 被丢弃", fake.topics.length === 1 && fake.topics[0] === validId, JSON.stringify(fake.topics));
check("★ 丢弃数被记下来（不许静默）", fake.invented === 1, String(fake.invented));
check("未知 kind 降级为 other", mk({ i: 0, kind: "随便写的", topics: [], confidence: 0.5 }).kind === "other");
check("confidence 越界被钳到 [0,1]",
  mk({ i: 0, kind: "knowledge_note", topics: [], confidence: 9 }).confidence === 1 &&
  mk({ i: 0, kind: "knowledge_note", topics: [], confidence: -3 }).confidence === 0);
check("confidence 非数字按 0（保守，不白送覆盖）",
  mk({ i: 0, kind: "knowledge_note", topics: [validId], confidence: "很高" }).confidence === 0);
check("i 越界返回 null（不越界写别人的段）",
  mk({ i: 999, kind: "knowledge_note", topics: [validId], confidence: 0.9 }) === null);
check("topics 去重", mk({ i: 0, kind: "knowledge_note", topics: [validId, validId], confidence: 0.9 }).topics.length === 1);
check("topics 传字符串也不是异常", typeof mk({ i: 0, kind: "knowledge_note", topics: "x", confidence: 0.9 }).topics === "object");

/* ---- ④ 兜底解析 ---- */
console.log("\n[4] extractJsonArray：为什么不能只用 grade_llm.extractJson");
const llm = require("../grade_llm");
const arrText = '[{"i":0,"kind":"knowledge_note"},{"i":1,"kind":"plan_or_index"}]';
check("顶层数组抠得出来", (eng.extractJsonArray(arrText) || []).length === 2);
const shared = llm.extractJson(arrText);
check("★ 对照：共用的 extractJson 会把数组抠成第一个对象（这就是必须自备数组解析的原因）",
  shared && !Array.isArray(shared) && shared.i === 0, JSON.stringify(shared));
check("代码围栏里的数组也认",
  (eng.extractJsonArray("说明\n```json\n" + arrText + "\n```\n") || []).length === 2);
check("嵌套数组不被截断",
  (eng.extractJsonArray('[{"a":[1,2]},{"b":3}]') || []).length === 2);
check("没有数组返回 null", eng.extractJsonArray("纯文本没有 JSON") === null);
// 引擎的顺序是 extractJson 优先（对象契约），数组只是兜底；
// 所以对象形态该由 extractJson 接住，不该走到兜底那条路。
const objForm = llm.extractJson('{"items":[{"i":0}]}');
check("对象契约由共用的 extractJson 接住（引擎不会误走数组兜底）",
  objForm && Array.isArray(objForm.items) && objForm.items[0].i === 0,
  JSON.stringify(objForm));

/* ---- ⑤ 跨语言口径对拍 ---- */
console.log("\n[5] JS 引擎与 Python 读取层口径必须一致");
const py = fs.readFileSync(path.join(SRC, "note_attribution.py"), "utf-8");
const mAccept = /thresholds\(\)\.get\("accept",\s*([\d.]+)\)/.exec(py);
check("note_attribution.py 里有 accept 兜底值", !!mAccept);
check("★ 兜底值 == spec 的 accept（两边判「到不到门槛」必须同一个数）",
  mAccept && Number(mAccept[1]) === spec.thresholds.accept,
  (mAccept && mAccept[1]) + " vs " + spec.thresholds.accept);
const ckBody = (py.split("def counting_kinds")[1] || "").split("\ndef ")[0];
check("Python 侧不内联 kind 名单（一律回 spec 取）",
  /spec\(\)/.test(ckBody) && !/knowledge_note/.test(ckBody),
  ckBody.slice(0, 160).replace(/\n/g, " "));
check("★ 完整性闸门在位：coverage_by_subject 会过滤未跑完的 domain",
  /def trustworthy_domains/.test(py) && /if d not in ok/.test(py));
check("Python 侧只读打开题库（不许写坏用户的库）", /mode=ro/.test(py));

/* ---- ⑥ 表结构与台账 ---- */
console.log("\n[6] 引擎建表与台账");
const js = fs.readFileSync(path.join(__dirname, "ai_attribute_notes.js"), "utf-8");
check("建了 note_attributions", /CREATE TABLE IF NOT EXISTS note_attributions/.test(js));
check("★ 建了完整性台账 note_attr_runs（半截数据不许冒充完整归因）",
  /CREATE TABLE IF NOT EXISTS note_attr_runs/.test(js));
check("★ 有回收步骤（文件删改名后旧判定不许继续算覆盖）", /DELETE FROM note_attributions/.test(js));
check("考点 id 白名单来自图谱（不是模型说了算）", /const valid = new Set\(tax\.topics\.map/.test(js));
check("失败批次不落库（下次会重问）", /整批作废（不写库/.test(js));
check("解析不出的原文留证据", /saveBadResponse/.test(js));
check("被 require 时不自动跑 main", /if \(require\.main === module\)/.test(js));

console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
process.exit(fail === 0 ? 0 : 1);

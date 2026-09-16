/*
 * 简答批改模块的纯函数测试（不调 API、不花钱）。
 *
 * 覆盖 2026-09-13 出过问题或容易出问题的几处：
 *   1. 模型返回裹了 ```json 代码块 / 前后带说明时，JSON 抠不抠得出来
 *   2. 嵌套花括号的 JSON 不能被截断（{a:{b:1}} 要在最外层配平才收）
 *   3. 分数 → FSRS 档位的边界（90/89、75/74、45/44）
 *   4. 解析失败时保守判「忘记」，不能崩、不能给出"满分"
 *   5. prompt 必须带上题干、参考答案、得分点、易错点
 *   6. 图片走 image_url 结构，纯文字走字符串（走错模型会说"unsupported image"）
 */
const path = require("path");
const {
  extractJson, scoreToRating, buildMessages, normalizeResult, loadConfig,
} = require(path.join(__dirname, "..", "grade_llm"));

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}
function eq(name, actual, expected) {
  check(name + "（期望 " + JSON.stringify(expected) + "）",
    JSON.stringify(actual) === JSON.stringify(expected),
    "实际 " + JSON.stringify(actual));
}

console.log("\n[1] JSON 抽取容错");
eq("纯 JSON", extractJson('{"score":80}'), { score: 80 });
eq("裹 ```json 代码块", extractJson('```json\n{"score":80}\n```'), { score: 80 });
eq("前后带说明文字", extractJson('好的，批改如下：{"score":80,"verdict":"对"} 以上。'), { score: 80, verdict: "对" });
eq("嵌套对象不被截断", extractJson('{"a":{"b":1},"c":2}'), { a: { b: 1 }, c: 2 });
eq("字符串里出现花括号不误判", extractJson('{"feedback":"用 { 表示集合"}'), { feedback: "用 { 表示集合" });
eq("没有 JSON 时返回 null", extractJson("模型跑偏了没给 JSON"), null);
eq("JSON 语法错误时返回 null", extractJson('{"score":80,}'), null);

console.log("\n[2] 分数 → FSRS 档位");
eq("100 → 简单", scoreToRating(100), 4);
eq("90 → 简单（边界）", scoreToRating(90), 4);
eq("89 → 记得", scoreToRating(89), 3);
eq("75 → 记得（边界）", scoreToRating(75), 3);
eq("74 → 模糊", scoreToRating(74), 2);
eq("45 → 模糊（边界）", scoreToRating(45), 2);
eq("44 → 忘记", scoreToRating(44), 1);
eq("0 → 忘记", scoreToRating(0), 1);
eq("超范围上界被夹住", scoreToRating(500), 4);
eq("负数被夹住", scoreToRating(-10), 1);
eq("非数字保守判忘记", scoreToRating("abc"), 1);
eq("null 保守判忘记", scoreToRating(null), 1);

console.log("\n[3] 结果归一化");
{
  const r = normalizeResult({ score: "88", verdict: " 部分对 ", hits: ["a", "", null, "b"], missed: "不是数组" },
    "raw");
  eq("字符串分数转成数字", r.score, 88);
  eq("verdict 去空格", r.verdict, "部分对");
  eq("hits 过滤空值", r.hits, ["a", "b"]);
  eq("missed 非数组时给空数组", r.missed, []);
  check("给出建议档", r.suggested_rating === 3);
  const bad = normalizeResult(null, "模型输出了一堆废话");
  check("解析失败时 ok=false", bad.ok === false);
  check("解析失败时不给分数", bad.score === null);
  check("解析失败时保留原文片段便于排查", String(bad.raw).indexOf("废话") >= 0);
}

console.log("\n[4] prompt 组装");
{
  const q = {
    stem: "为什么一点二阶可导不能连用两次洛必达？",
    reference_answer: "洛必达要求去心邻域内可导；一点可导推不出邻域可导。应改用泰勒展开。",
    key_points: ["洛必达的使用条件", "一点可导 ≠ 邻域可导", "改用泰勒"],
    traps: ["偷设二阶导在邻域存在"],
  };
  const msgs = buildMessages(q, "因为条件不满足", null);
  const text = msgs.map(m => (typeof m.content === "string" ? m.content : JSON.stringify(m.content))).join("\n");
  check("带题干", text.indexOf("为什么一点二阶可导") >= 0);
  check("带参考答案", text.indexOf("去心邻域") >= 0);
  check("带得分点", text.indexOf("洛必达的使用条件") >= 0);
  check("带易错点", text.indexOf("偷设二阶导") >= 0);
  check("带学生答案", text.indexOf("因为条件不满足") >= 0);
  check("要求输出 JSON", text.indexOf("score") >= 0 && text.indexOf("missed") >= 0);
  check("有 system 角色", msgs[0].role === "system" && msgs[1].role === "user");
  check("纯文字时 content 是字符串", typeof msgs[1].content === "string");

  const withImg = buildMessages(q, "", "data:image/jpeg;base64,AAAA");
  const parts = withImg[1].content;
  check("带图时 content 是数组", Array.isArray(parts));
  check("第一段是文字", parts[0].type === "text");
  check("第二段是 image_url（走错模型会说 unsupported image）", parts[1].type === "image_url"
    && parts[1].image_url.url.indexOf("data:image/jpeg") === 0);
  check("要求先辨认手写再批改", JSON.stringify(parts).indexOf("辨认") >= 0);
  const none = buildMessages(q, "", null);
  check("既没文字也没图时提示按 0 分处理",
    JSON.stringify(none[1].content).indexOf("未提交") >= 0);
}

console.log("\n[5] 配置读取");
{
  const cfg = loadConfig({ DEEPSEEK_API_KEY: "sk-test", DEEPSEEK_MODEL: "deepseek-flash" });
  eq("环境变量优先", cfg.model, "deepseek-flash");
  check("baseUrl 去掉结尾斜杠", !/\/$/.test(cfg.baseUrl));
  const solo = loadConfig({});
  check("没有 key 时 apiKey 为空串而不是 undefined", typeof solo.apiKey === "string");
}

(async () => {
  console.log("\n[6] 交互类调用要快：默认关掉「思考」且限长（2026-09-21）");
  {
    const { explainAnswer, explainFollowup, coachChat, buildExplainMessages } =
      require(path.join(__dirname, "..", "grade_llm"));
    const calls = [];
    const cfg = { apiKey: "sk-test", baseUrl: "https://example.invalid", model: "m", timeoutMs: 5000 };
    const stub = async (url, init) => {
      calls.push(JSON.parse(init.body));
      return { ok: true, text: async () => JSON.stringify({
        choices: [{ message: { content: "好，这是解析。" }, finish_reason: "stop" }] }) };
    };
    const q = { content: { stem: "若 f''(x0)=0，则一定是拐点。", answer: false } };

    await explainAnswer(q, "正确", "错误", { mode: "wrong", config: cfg, fetchImpl: stub });
    check("解析默认关掉思考（thinking.type=disabled）",
      calls[0].thinking && calls[0].thinking.type === "disabled", JSON.stringify(calls[0].thinking));
    eq("解析默认的 token 预算够写长答案（不再按旧字数配）", calls[0].max_tokens, 3000);

    await explainAnswer(q, "正确", "错误", { mode: "wrong", config: cfg, fetchImpl: stub, quick: false });
    check("显式 quick:false 时不发 thinking", !calls[1].thinking, JSON.stringify(calls[1].thinking));
    eq("quick:false 用更大的预算（要装下思考）", calls[1].max_tokens, 6000);

    await explainFollowup(q, "", "错误", [{ role: "assistant", content: "上一轮" }], "那下次看什么？",
      { mode: "wrong", config: cfg, fetchImpl: stub });
    check("追问同样默认关掉思考（追问尤其等不起）",
      calls[2].thinking && calls[2].thinking.type === "disabled");
    check("追问带上了历史", JSON.stringify(calls[2].messages).indexOf("上一轮") >= 0);

    await coachChat([{ role: "user", content: "问题" }], "sys", { config: cfg, fetchImpl: stub, quick: true });
    check("coachChat 传 quick:true 才关思考", calls[3].thinking && calls[3].thinking.type === "disabled");
    await coachChat([{ role: "user", content: "问题" }], "sys", { config: cfg, fetchImpl: stub });
    check("coachChat 默认保持深度思考（复盘/薄弱点学习值得多想）", !calls[4].thinking);

    const sysText = buildExplainMessages(q, "正确", "错误", "wrong")[0].content;
    // 提示词归用户自己写（2026-09-21 用户要求）：内置默认只留角色 + 公式格式，
    // 不出来替用户定风格（之前塞过「讲陷阱/边界」「限制字数」这类要求，被指有误导性）。
    check("内置默认 prompt 只说角色与格式",
      sysText.indexOf("你是考研辅导老师") >= 0 && sysText.indexOf("公式") >= 0, sysText);
    check("内置默认不再替他规定讲什么",
      !/陷阱|边界|自查|建议补进笔记|硬上限|字数/.test(sysText), sysText);
    const custom = "你是我的私人助教，只讲结论，用表格对比。";
    const sysCustom = buildExplainMessages(q, "正确", "错误", "wrong", "", custom)[0].content;
    check("★ 用户自己写的 prompt 原样使用（不追加任何要求）", sysCustom === custom, sysCustom);
    const withAsk = buildExplainMessages(q, "正确", "错误", "correct", "为什么不能这样？", custom);
    check("自定义 prompt + 自带问题时，system 仍然是他那一份",
      withAsk[0].content === custom, withAsk[0].content);
    check("自带问题进的是用户消息（数据，不是替他写要求）",
      withAsk[1].content.indexOf("【学生的问题】为什么不能这样？") >= 0, withAsk[1].content);
    const noCustom = buildExplainMessages(q, "正确", "错误", "correct", "为什么不能这样？");
    check("没自定义时仍按内置默认回答他的问题",
      noCustom[1].content.indexOf("【学生的问题】") >= 0
      && noCustom[0].content.indexOf("ask") < 0 && noCustom[0].content.indexOf("你是考研辅导老师") >= 0,
      noCustom[0].content);
  }

  console.log("\n" + "=".repeat(52));
  console.log(`通过 ${pass} 项，失败 ${fail} 项`);
  console.log("=".repeat(52));
  process.exit(fail ? 1 : 0);
})();

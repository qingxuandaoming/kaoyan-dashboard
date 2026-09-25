/*
 * test_ai_providers.js — 多供应商注册表 + 双协议适配（2026-09-26）
 *
 * 不花一分钱：所有上游调用都走 fetchImpl 桩；密钥读写走 SECRETS_PATH 临时副本
 * （真钥被自动化覆盖的坑踩过一次，隔离是纪律）。
 *
 * 覆盖：
 *   1. 注册表：旧配置合成 / 增删改 / 启用切换 / 掩码 / 校验 / 旧 deepseek 段同步 / env 覆盖
 *   2. Anthropic 消息与工具schema 转换（system 提级、图片块、tool_use/tool_result 配对）
 *   3. callLLM 双协议：URL / 头 / payload / 响应归一（含 finish=length 口径）
 *   4. agentTurnStream 的 Anthropic 流：分片 tool_calls 归并、text/thinking 事件
 *   5. pingModel 连通测试回执
 */
const fs = require("fs");
const os = require("os");
const path = require("path");

// ⚠️ 必须在 require 被测模块之前设好：SECRETS_PATH 是模块加载时解析的
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "kaoyan-prov-"));
process.env.SECRETS_PATH = path.join(TMP, "secrets.json");

const prov = require(path.join(__dirname, "..", "ai_providers"));
const g = require(path.join(__dirname, "..", "grade_llm"));

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
function throws(name, fn) {
  try { fn(); check(name, false, "没有抛错"); }
  catch (e) { check(name, true); }
}

function writeSecrets(obj) {
  fs.writeFileSync(process.env.SECRETS_PATH, JSON.stringify(obj, null, 2), "utf-8");
}
function readSecrets() {
  return JSON.parse(fs.readFileSync(process.env.SECRETS_PATH, "utf-8"));
}

// ---------------------------------------------------------------------------
console.log("\n[1] 注册表：旧配置合成与掩码");
writeSecrets({ deepseek: { api_key: "sk-abcdefgh1234cfd1", model: "deepseek-flash" } });
{
  const reg = prov.loadRegistry();
  eq("旧配置合成一个供应商", reg.providers.length, 1);
  eq("合成项 id", reg.providers[0].id, "legacy-deepseek");
  eq("合成项带出旧钥与模型", [reg.providers[0].api_key, reg.providers[0].model],
    ["sk-abcdefgh1234cfd1", "deepseek-flash"]);
  eq("active 落到合成项", reg.active, "legacy-deepseek");
  const pub = prov.publicList(reg)[0];
  eq("密钥只回显尾 4 位", pub.key_hint, "****cfd1");
  check("清单不下发明文钥", !JSON.stringify(pub).includes("sk-abcdefgh1234cfd1"));
  check("旧配置不写盘（第一次改动前保持原形状）",
    !("providers" in readSecrets()));
}

console.log("\n[2] 注册表：增删改与启用切换");
{
  let reg = prov.loadRegistry();
  const added = prov.addProvider(reg, {
    name: "Claude 主站", protocol: "anthropic", base_url: "https://api.anthropic.com/",
    api_key: "sk-ant-abcdefgh9999zzzz", model: "claude-sonnet-4-5",
  });
  reg = added.reg;
  eq("新增后两个供应商", reg.providers.length, 2);
  eq("新增不抢启用位", reg.active, "legacy-deepseek");
  check("尾斜杠被剥掉", reg.providers[1].base_url === "https://api.anthropic.com");
  check("anthropic 默认不发关思考参数", reg.providers[1].nothink === false);
  check("deepseek 域名默认发关思考参数", reg.providers[0].nothink === true);

  prov.saveRegistry(reg);
  const onDisk = readSecrets();
  eq("落盘 active_provider", onDisk.active_provider, "legacy-deepseek");
  eq("旧 deepseek 段同步成启用中的 openai 供应商", onDisk.deepseek.model, "deepseek-flash");

  reg = prov.setActive(prov.loadRegistry(), added.provider.id).reg;
  prov.saveRegistry(reg);
  const cfg = prov.activeConfig({});
  eq("切换后 activeConfig 走新供应商", [cfg.providerName, cfg.protocol, cfg.model],
    ["Claude 主站", "anthropic", "claude-sonnet-4-5"]);
  eq("旧 deepseek 段不被 anthropic 启用项覆盖", readSecrets().deepseek.model, "deepseek-flash");

  // update：api_key 留空 = 不修改
  reg = prov.updateProvider(prov.loadRegistry(), added.provider.id,
    { name: "Claude 主站", protocol: "anthropic", base_url: "https://api.anthropic.com",
      model: "claude-opus-4-7" }).reg;
  const upd = reg.providers.find(x => x.id === added.provider.id);
  check("留空密钥保持旧钥", upd.api_key === "sk-ant-abcdefgh9999zzzz");
  eq("模型已改", upd.model, "claude-opus-4-7");

  // remove 启用中的 → 落到剩余第一个
  reg = prov.removeProvider(reg, added.provider.id).reg;
  eq("删启用项后 active 回落", reg.active, "legacy-deepseek");
  eq("剩一个", reg.providers.length, 1);
  prov.saveRegistry(reg);
}

console.log("\n[3] 注册表：校验与 env 覆盖");
{
  const reg = prov.loadRegistry();
  throws("非法 Base URL 被拒", () => prov.addProvider(reg, { base_url: "ftp://x", api_key: "sk-12345678", model: "m" }));
  throws("模型名带空格被拒", () => prov.addProvider(reg, { base_url: "https://a.b", api_key: "sk-12345678", model: "my model" }));
  throws("密钥带空白被拒", () => prov.addProvider(reg, { base_url: "https://a.b", api_key: "sk-1234 5678", model: "m" }));
  throws("密钥过短被拒", () => prov.addProvider(reg, { base_url: "https://a.b", api_key: "short", model: "m" }));
  throws("新增缺密钥被拒", () => prov.addProvider(reg, { base_url: "https://a.b", model: "m" }));
  throws("改不存在的供应商被拒", () => prov.updateProvider(reg, "pv-nope", { model: "m" }));
  eq("env 覆盖模型（测试隔离口径不变）",
    prov.activeConfig({ DEEPSEEK_MODEL: "env-model" }).model, "env-model");
  eq("env 覆盖密钥",
    prov.activeConfig({ DEEPSEEK_API_KEY: "sk-envenvenv" }).apiKey, "sk-envenvenv");
}

// ---------------------------------------------------------------------------
console.log("\n[4] Anthropic 消息/工具转换");
{
  const { system, rest } = g.splitSystem([
    { role: "system", content: "你是老师" },
    { role: "user", content: "题" },
  ]);
  eq("system 提级", system, "你是老师");
  eq("rest 只剩非 system", rest.length, 1);

  const msgs = g.toAnthropicMessages([
    { role: "user", content: [
      { type: "text", text: "看图" },
      { type: "image_url", image_url: { url: "data:image/png;base64,QUJD" } },
    ] },
    { role: "assistant", content: "我用工具", tool_calls: [
      { id: "tu_1", type: "function", function: { name: "search_bank", arguments: '{"q":"树"}' } },
    ] },
    { role: "tool", tool_call_id: "tu_1", content: "结果1" },
    { role: "tool", tool_call_id: "tu_2", content: "结果2" },
  ]);
  eq("四条转三条（连续 tool 合并）", msgs.length, 3);
  eq("图片转 base64 source 块", msgs[0].content[1],
    { type: "image", source: { type: "base64", media_type: "image/png", data: "QUJD" } });
  eq("assistant tool_calls 转 tool_use", msgs[1].content[1],
    { type: "tool_use", id: "tu_1", name: "search_bank", input: { q: "树" } });
  eq("tool 结果合并成一条 user", msgs[2].content.length, 2);
  eq("tool_result 带 tool_use_id", msgs[2].content[0].tool_use_id, "tu_1");

  const tools = g.toAnthropicTools([
    { type: "function", function: { name: "f", description: "d", parameters: { type: "object", properties: { a: { type: "string" } } } } },
  ]);
  eq("工具 schema 换名 input_schema", tools[0],
    { name: "f", description: "d", input_schema: { type: "object", properties: { a: { type: "string" } } } });

  eq("base_url 到域名自动补 /v1/messages",
    g.anthropicUrl("https://api.anthropic.com/"), "https://api.anthropic.com/v1/messages");
  eq("base_url 已带 /v1 不重复补",
    g.anthropicUrl("https://x.dev/v1"), "https://x.dev/v1/messages");

  const r = g.anthropicResult({
    stop_reason: "max_tokens",
    content: [
      { type: "thinking", thinking: "嗯" },
      { type: "text", text: "答" },
      { type: "tool_use", id: "tu_9", name: "make_cards", input: { n: 2 } },
    ],
  });
  eq("text/thinking 分开收", [r.text, r.reasoning], ["答", "嗯"]);
  eq("stop_reason=max_tokens 映射 length（截断重试靠它）", r.finish, "length");
  eq("tool_use 转 OpenAI 形状", r.tool_calls[0].function.arguments, '{"n":2}');
  eq("end_turn 映射 stop", g.anthropicResult({ stop_reason: "end_turn", content: [] }).finish, "stop");
}

// ---------------------------------------------------------------------------
console.log("\n[5] callLLM 双协议（mock fetch）");
function mockFetch(reply) {
  const calls = [];
  const fn = async (url, opts) => {
    calls.push({ url, opts });
    return {
      ok: true, status: 200,
      text: async () => JSON.stringify(reply),
    };
  };
  fn.calls = calls;
  return fn;
}
{
  const cfgO = { protocol: "openai", apiKey: "sk-openaiopenai", baseUrl: "https://api.deepseek.com",
    model: "deepseek-flash", nothink: true, timeoutMs: 5000, providerName: "DS" };
  const fO = mockFetch({ choices: [ { message: { content: "正文", reasoning_content: "思" }, finish_reason: "stop" } ] });
  g.callLLM([{ role: "user", content: "hi" }], cfgO, fO, 100, { quick: true })
    .then(r => {
      eq("openai 走 /chat/completions", fO.calls[0].url, "https://api.deepseek.com/chat/completions");
      check("Bearer 头", fO.calls[0].opts.headers.Authorization === "Bearer sk-openaiopenai");
      const body = JSON.parse(fO.calls[0].opts.body);
      check("quick+nothink 发关思考参数", body.thinking && body.thinking.type === "disabled");
      eq("openai 响应归一", [r.ok, r.text, r.reasoning, r.finish], [true, "正文", "思", "stop"]);
    })
    .then(() => {
      const cfgN = Object.assign({}, cfgO, { nothink: false });
      const fN = mockFetch({ choices: [ { message: { content: "x" }, finish_reason: "stop" } ] });
      return g.callLLM([{ role: "user", content: "hi" }], cfgN, fN, 100, { quick: true })
        .then(() => {
          const body = JSON.parse(fN.calls[0].opts.body);
          check("nothink=false 不发关思考参数（非 DeepSeek 端点不会被 400）", !("thinking" in body));
        });
    })
    .then(() => {
      const cfgA = { protocol: "anthropic", apiKey: "sk-ant-testtest", baseUrl: "https://api.anthropic.com",
        model: "claude-sonnet-4-5", nothink: false, timeoutMs: 5000, providerName: "C" };
      const fA = mockFetch({
        stop_reason: "end_turn",
        content: [ { type: "text", text: "你好" } ],
      });
      return g.callLLM([
        { role: "system", content: "sys" },
        { role: "user", content: "hi" },
      ], cfgA, fA, 100, { quick: true }).then(r => {
        eq("anthropic 走 /v1/messages", fA.calls[0].url, "https://api.anthropic.com/v1/messages");
        check("x-api-key 头", fA.calls[0].opts.headers["x-api-key"] === "sk-ant-testtest");
        check("anthropic-version 头", fA.calls[0].opts.headers["anthropic-version"] === "2023-06-01");
        const body = JSON.parse(fA.calls[0].opts.body);
        eq("system 提级到顶层", body.system, "sys");
        eq("messages 不含 system", body.messages.length, 1);
        check("anthropic 不发 thinking 参数", !("thinking" in body));
        eq("anthropic 响应归一", [r.ok, r.text, r.finish], [true, "你好", "stop"]);
      });
    })
    .then(() => {
      console.log("\n[6] agentTurnStream：Anthropic 流式分片归并");
      const events = [
        { type: "message_start", message: { id: "m1" } },
        { type: "content_block_start", index: 0, content_block: { type: "thinking" } },
        { type: "content_block_delta", index: 0, delta: { type: "thinking_delta", thinking: "推" } },
        { type: "content_block_start", index: 1, content_block: { type: "text" } },
        { type: "content_block_delta", index: 1, delta: { type: "text_delta", text: "正" } },
        { type: "content_block_delta", index: 1, delta: { type: "text_delta", text: "文" } },
        { type: "content_block_start", index: 2, content_block: { type: "tool_use", id: "tu_5", name: "search_bank" } },
        { type: "content_block_delta", index: 2, delta: { type: "input_json_delta", partial_json: '{"q":' } },
        { type: "content_block_delta", index: 2, delta: { type: "input_json_delta", partial_json: '"树"}' } },
        { type: "message_delta", delta: { stop_reason: "tool_use" } },
        { type: "message_stop" },
      ];
      const chunk = "data: " + JSON.stringify(events[0]) + "\n\n"
        + events.slice(1).map(e => "data: " + JSON.stringify(e) + "\n\n").join("");
      const enc = new TextEncoder();
      const streamFake = {
        getReader: () => {
          let sent = false;
          return {
            read: async () => {
              if (!sent) { sent = true; return { done: false, value: enc.encode(chunk) }; }
              return { done: true, value: undefined };
            },
          };
        },
      };
      const fS = async (url, opts) => {
        fS.url = url; fS.body = opts.body;
        return { ok: true, status: 200, body: streamFake };
      };
      const seen = [];
      const cfgA = { protocol: "anthropic", apiKey: "sk-ant-testtest", baseUrl: "https://api.anthropic.com",
        model: "m", nothink: false, timeoutMs: 5000, providerName: "C" };
      return g.agentTurnStream([{ role: "user", content: "hi" }],
        [{ type: "function", function: { name: "search_bank", description: "d", parameters: { type: "object" } } }],
        { config: cfgA, fetchImpl: fS }, (d) => seen.push(d.type))
        .then(r => {
          eq("流式正文拼全", r.message.content, "正文");
          eq("thinking 收到", r.reasoning, "推");
          eq("分片 arguments 拼全", r.tool_calls[0].function.arguments, '{"q":"树"}');
          eq("tool_use stop_reason 映射 tool_calls", r.finish, "tool_calls");
          eq("事件口径与 OpenAI 流一致", seen, ["reasoning", "content", "content"]);
          const sbody = JSON.parse(fS.body);
          check("流式 payload 带 stream:true 与 tools", sbody.stream === true
            && Array.isArray(sbody.tools) && sbody.tools[0].name === "search_bank");
        });
    })
    .then(() => {
      console.log("\n[7] pingModel 连通测试");
      const fP = mockFetch({ choices: [ { message: { content: "在的" }, finish_reason: "stop" } ] });
      const cfgO = { protocol: "openai", apiKey: "sk-openaiopenai", baseUrl: "https://api.deepseek.com",
        model: "deepseek-flash", nothink: true, timeoutMs: 5000, providerName: "DS" };
      return g.pingModel(cfgO, fP).then(r => {
        check("回执带耗时", typeof r.ms === "number");
        eq("回执带模型与回声", [r.ok, r.model, r.reply], [true, "deepseek-flash", "在的"]);
      });
    })
    .then(() => {
      console.log("\n" + (fail ? "有失败：" : "全部通过：") + "pass=" + pass + " fail=" + fail);
      fs.rmSync(TMP, { recursive: true, force: true });
      process.exit(fail ? 1 : 0);
    })
    .catch(e => {
      console.log("  FAIL 未捕获异常 → " + e.stack);
      console.log("有失败：pass=" + pass + " fail=" + (fail + 1));
      fs.rmSync(TMP, { recursive: true, force: true });
      process.exit(1);
    });
}

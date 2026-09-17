/*
 * 「网页里的 AI 自己调接口」那一层的用例（2026-09-22）
 *
 * 工具层是整个 A 方案的枢纽：模型说的算不算数，全靠这里。
 * 最要紧的一条是 open_practice 的闸门 —— 模型可能编一个卡号出来，
 * 那时「弹一个练不了的浮窗」比不弹更糟，所以只认本轮 make_cards 返回的卡号。
 *
 * 全部用假 deps 跑，不碰数据库、不发请求。
 */
const path = require("path");
const agent = require(path.join(__dirname, "..", "study_agent.js"));
// 流式那一轮（agentTurnStream）也在这里测：它是工具回路的传输层，
// 而流式下 tool_calls 是**分片**来的，拼错了工具参数就废了 —— 最该盯的一处。
const gradeLlm = require(path.join(__dirname, "..", "grade_llm.js"));

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

// 一套假依赖：题库/笔记都是现成的固定结果，写库只记数
function mkDeps(over) {
  const o = over || {};
  const filed = [];
  return {
    actions: [],
    filed: filed,
    db: { prepare() { throw new Error("用例里没有真库"); } },
    subject: "数学一",
    today: "2026-09-22",
    searchTopics: () => [
      { id: "MATH-GS-07", name: "常微分方程", subject: "数学一", cards: 3, acc: 67, answered: 6 },
    ],
    searchNotes: () => [
      { title: "微分方程与差分方程", path: "Math/高数/第7章.md", subject: "数学一" },
    ],
    insertCards: (cards) => {
      filed.push(cards);
      return { inserted: cards.length, reused: 0,
               card_ids: cards.map((c, i) => "C-STUDY-TEST" + i) };
    },
    ...o,
  };
}
const card = (stem) => ({
  type: "choice", stem: stem || "【用例】设 y1,y2 是解，则通解是",
  options: ["甲", "乙", "丙", "丁"], answer: 2, explanation: "因为丙", topic: "常微分方程",
});

// 带「这题有问题」标记模块的假 deps（2026-09-17）
function mkReportDeps(over) {
  const resolved = [];
  const base = {
    cardReports: {
      listReports: () => [{
        id: 12, card_id: "C-STUDY-ABCD1234", type: "choice", kind: "multi_correct",
        kind_label: "多个选项都对", note: "A、C 都对", subject: "数学一", topic_name: "微分方程",
        stem: "已知 y₁* 是 y″+p(x)y′+q(x)y=f₁(x) 的特解…",
        content: { options: ["甲", "乙", "丙", "丁"], answer: 0 }, stale: false,
      }],
      resolveReport: (db, id, opts) => {
        resolved.push({ id: id, opts: opts });
        if (opts.action === "fixed" && !opts.note && !opts.patch) {
          return { ok: false, error: "fixed 要么给 patch（改题），要么给 note" };
        }
        return {
          ok: true, id: id, action: opts.action,
          status: opts.action === "fixed" ? "fixed"
            : (opts.action === "dismissed" ? "dismissed" : "deleted"),
          card_id: "C-STUDY-ABCD1234",
          changes: opts.patch ? Object.keys(opts.patch) : [],
          warnings: [],
        };
      },
    },
    resolved: resolved,
  };
  return mkDeps(Object.assign(base, over));
}

// ---------------------------------------------------------------------------
console.log("\n[1] 工具定义：模型看不看得懂，全靠这几句描述");
{
  const names = agent.TOOLS.map(t => t.function.name);
  check("六个工具都在：search_bank / search_notes / make_cards / open_practice / list_card_reports / fix_card",
    ["search_bank", "search_notes", "make_cards", "open_practice", "list_card_reports", "fix_card"]
      .every(n => names.includes(n)),
    names.join(","));
  check("每个工具都带 description 与 parameters",
    agent.TOOLS.every(t => t.function.description && t.function.parameters
      && t.function.parameters.type === "object"));
  const mc = agent.TOOLS.find(t => t.function.name === "make_cards").function;
  check("make_cards 的参数里有 cards 数组", mc.parameters.properties.cards.type === "array");
  check("make_cards 的说明写明了「出了题就调它」",
    /出了题就调它|出题就调/.test(mc.description), mc.description);
  const op = agent.TOOLS.find(t => t.function.name === "open_practice").function;
  check("open_practice 的说明写明只能用本轮 make_cards 的 card_id",
    /make_cards/.test(op.description) && /card_id/.test(op.description));
  check("模型听得到「不要再贴 cards JSON 块」（否则题会被给两遍）",
    /不要再在正文里贴/.test(agent.AGENT_HINT), agent.AGENT_HINT.slice(0, 80));
  check("轮次上限是有限的（不会一直调工具）",
    Number.isInteger(agent.MAX_ROUNDS) && agent.MAX_ROUNDS >= 1 && agent.MAX_ROUNDS <= 6,
    String(agent.MAX_ROUNDS));
}

// ---------------------------------------------------------------------------
console.log("\n[2] 查题库 / 查笔记");
(async () => {
  {
    const d = mkDeps();
    const run = agent.makeToolRunner(d);
    const r = await run("search_bank", { query: "微分方程 通解" });
    check("命中考点：结果里有考点清单", r.ok && r.topics.length === 1 && r.topics[0].id === "MATH-GS-07");
    check("summary 给人看得懂（界面那行芯片）", /命中 1 个考点/.test(r.summary), r.summary);
    check("题库查不动时也不炸（假库没有 prepare）", Array.isArray(r.samples) && r.samples.length === 0);

    const d2 = mkDeps({ searchTopics: () => [] });
    const r2 = await agent.makeToolRunner(d2)("search_bank", { query: "没这个东西" });
    check("没命中要给提示（别让模型瞎编题库里有什么）",
      r2.ok && /没有命中/.test(r2.summary) && !!r2.hint, r2.summary);

    const r3 = await agent.makeToolRunner(mkDeps())("search_bank", { query: "" });
    check("关键词为空 → 明确失败（不静默返回空）", r3.ok === false && !!r3.error);

    const d4 = mkDeps({ searchTopics: () => { throw new Error("库炸了"); } });
    const r4 = await agent.makeToolRunner(d4)("search_bank", { query: "微分方程" });
    check("检索抛错也不炸整轮（降级成没命中）", r4.ok && /没有命中/.test(r4.summary));

    const r5 = await agent.makeToolRunner(mkDeps())("search_notes", { query: "微分方程" });
    check("查笔记：给出标题与路径", r5.ok && r5.notes[0].path === "Math/高数/第7章.md", r5.summary);
  }

  // -------------------------------------------------------------------------
  console.log("\n[3] 归卡 make_cards");
  {
    const d = mkDeps();
    const run = agent.makeToolRunner(d);
    const r = await run("make_cards", { cards: [card(), card("第二道")] });
    check("合法题 → 入库并回卡号", r.ok && r.inserted === 2 && r.card_ids.length === 2, JSON.stringify(r));
    check("规范化后的题才交给写库（选项前缀/答案都收敛过）",
      d.filed.length === 1 && d.filed[0][0].answer === 2 && d.filed[0][0].options.length === 4);
    check("留痕：actions 里有 card_filed（界面上看得见）",
      d.actions.filter(a => a.type === "card_filed").length === 2);
    check("summary 说明新增几张", /归卡：新增 2 张/.test(r.summary), r.summary);

    const d2 = mkDeps();
    const r2 = await agent.makeToolRunner(d2)("make_cards", {
      cards: [{ type: "choice", stem: "只有三个选项", options: ["甲", "乙", "丙"], answer: 0 }],
    });
    check("★ 选项不完整 → 拒绝入库（宁可少一张，也不要一张永远判错的卡）",
      r2.ok === false && d2.filed.length === 0, JSON.stringify(r2));

    const d3 = mkDeps({ insertCards: () => { throw new Error("库写满了"); } });
    const r3 = await agent.makeToolRunner(d3)("make_cards", { cards: [card()] });
    check("写库抛错 → 如实失败（不谎报归卡成功）", r3.ok === false && /库写满了/.test(r3.error));

    const d4 = mkDeps();
    const r4 = await agent.makeToolRunner(d4)("make_cards", { cards: [] });
    check("空 cards → 失败而不是静默成功", r4.ok === false && d4.filed.length === 0);
  }

  // -------------------------------------------------------------------------
  console.log("\n[4] 开浮窗 open_practice 的闸门（最容易出事的一环）");
  {
    const d = mkDeps();
    const run = agent.makeToolRunner(d);

    const r0 = await run("open_practice", { card_ids: ["C-STUDY-TEST0"] });
    check("★ 还没归卡就想开练 → 拒绝（不许弹一个练不了的浮窗）",
      r0.ok === false && d.actions.length === 0, JSON.stringify(r0));

    const made = await run("make_cards", { cards: [card()] });
    const ids = made.card_ids;

    const r1 = await run("open_practice", { card_ids: ["C-编的-9999", ids[0]] });
    check("★ 混着编的卡号 → 只留本轮真归过的那张",
      r1.ok === true && r1.card_ids.length === 1 && r1.card_ids[0] === ids[0], JSON.stringify(r1));
    check("动作登记成 practice（前端收到就弹浮窗）",
      d.actions.some(a => a.type === "practice" && a.card_ids.length === 1));
    check("浮窗标题自动带上「🧠」前缀",
      d.actions.filter(a => a.type === "practice")[0].title.indexOf("🧠") === 0,
      d.actions.filter(a => a.type === "practice")[0].title);
    check("summary 写明浮窗已弹出", /开练：1 张（浮窗已弹出）/.test(r1.summary), r1.summary);

    const r2 = await run("open_practice", { card_ids: ["C-编的-1", "C-编的-2"] });
    check("★ 全是编的卡号 → 拒绝", r2.ok === false, JSON.stringify(r2));

    const r3 = await run("open_practice", { card_ids: ids, title: "微分方程·解的结构 1 张" });
    check("自定义标题被采用", d.actions.filter(a => a.type === "practice")[1].title
      === "🧠 微分方程·解的结构 1 张", d.actions.filter(a => a.type === "practice")[1].title);
  }

  console.log("\n[5] 未知工具 / 参数异常");
  {
    const d = mkDeps();
    const run = agent.makeToolRunner(d);
    const r = await run("no_such_tool", {});
    check("未知工具 → 明确失败并带 summary", r.ok === false && !!r.summary);

    const r2 = await run("search_bank", null);
    check("参数是 null 也不炸", r2.ok === false || typeof r2.summary === "string");

    const r3 = await run("make_cards", { cards: "不是数组" });
    check("cards 不是数组 → 失败而不是抛异常", r3.ok === false);
  }

  // -------------------------------------------------------------------------
  console.log("\n[5b] 「这题有问题」标记的读写（list_card_reports / fix_card）");
  {
    const d = mkReportDeps();
    const run = agent.makeToolRunner(d);

    const r = await run("list_card_reports", {});
    check("列标记：带回 id / 原因 / 题干 / 选项（模型要接着改题）",
      r.ok && r.reports.length === 1 && r.reports[0].id === 12
      && r.reports[0].kind_label === "多个选项都对" && r.reports[0].options.length === 4,
      JSON.stringify(r.reports));
    check("summary 说明有几条待修", /1 条待修/.test(r.summary), r.summary);

    const bad = await run("list_card_reports", { subject: "数学一" });
    check("带科目也不炸（假实现忽略科目）", bad.ok === true);

    const noId = await run("fix_card", { note: "改好了" });
    check("★ 没有 report_id → 拒绝（不许瞎改一张不知道是哪张的卡）",
      noId.ok === false && !!noId.error, JSON.stringify(noId));

    const noNote = await run("fix_card", { report_id: 12 });
    check("既没 patch 也没 note → 如实失败（模块那句错误原样带回）",
      noNote.ok === false && /patch/.test(noNote.error), JSON.stringify(noNote));

    const fixed = await run("fix_card", {
      report_id: 12, note: "把 C 改成真干扰项",
      patch: { options: ["甲", "乙", "丙", "丁"], answer: 0 },
    });
    check("改题成功：报出改了哪些字段", fixed.ok && /改好了/.test(fixed.summary)
      && fixed.changes.indexOf("options") >= 0, JSON.stringify(fixed));
    check("传下去的是 report_id + note + patch（原样交给模块，不在这里重写规则）",
      d.resolved.some(x => x.id === 12 && x.opts.note === "把 C 改成真干扰项" && !!x.opts.patch),
      JSON.stringify(d.resolved));
    check("留痕：actions 里有 card_fixed（前端 toast 用）",
      d.actions.some(a => a.type === "card_fixed" && a.report_id === 12 && a.status === "fixed"));

    const dis = await run("fix_card", { report_id: 12, action: "dismissed", note: "我记错了，题没毛病" });
    check("驳回想得通", dis.ok && dis.status === "dismissed" && /驳回/.test(dis.summary), dis.summary);

    const noDep = await agent.makeToolRunner(mkDeps({}))("list_card_reports", {});
    check("★ 没接上标记模块时如实失败（不抛异常、不谎报「没有」）",
      noDep.ok === false && !!noDep.error, JSON.stringify(noDep));

    const boom = await agent.makeToolRunner(mkReportDeps({
      cardReports: { listReports: () => [], resolveReport: () => { throw new Error("库锁了"); } },
    }))("fix_card", { report_id: 1, note: "x" });
    check("改题抛错也不炸整轮", boom.ok === false && /库锁了/.test(boom.error), JSON.stringify(boom));

    check("实时标签：翻标记 / 核对某条", /标记/.test(agent.runningLabel("list_card_reports", {}))
      && /#12/.test(agent.runningLabel("fix_card", { report_id: 12 })),
      agent.runningLabel("list_card_reports", {}) + " / " + agent.runningLabel("fix_card", { report_id: 12 }));
    check("工具说明里写了「核对过再调」和「宁可驳回也不要硬改」",
      /核对过/.test(agent.TOOLS.find(t => t.function.name === "fix_card").function.description));
    check("AGENT_HINT 教它什么时候用（学生问「那些标了有问题的题」）",
      /list_card_reports/.test(agent.AGENT_HINT) && /fix_card/.test(agent.AGENT_HINT));
  }

  // -------------------------------------------------------------------------
  console.log("\n[6] 「正在干什么」的实时标签（流式时先报它）");
  {
    check("搜题库：带上关键词（不然等于没说）",
      agent.runningLabel("search_bank", { query: "微分方程 解的结构" }).indexOf("微分方程") > 0,
      agent.runningLabel("search_bank", { query: "微分方程 解的结构" }));
    check("没关键词时也不显示 undefined",
      agent.runningLabel("search_bank", {}).indexOf("undefined") < 0,
      agent.runningLabel("search_bank", {}));
    check("归卡：报出几张",
      /2 道题/.test(agent.runningLabel("make_cards", { cards: [{}, {}] })),
      agent.runningLabel("make_cards", { cards: [{}, {}] }));
    check("开浮窗：报出几张",
      /3 张/.test(agent.runningLabel("open_practice", { card_ids: ["a", "b", "c"] })),
      agent.runningLabel("open_practice", { card_ids: ["a", "b", "c"] }));
    check("未知工具也不会崩",
      typeof agent.runningLabel("whatever", null) === "string");
  }

  // -------------------------------------------------------------------------
  console.log("\n[7] 流式装配：SSE 分片必须拼回完整的一轮");
  {
    const enc = new TextEncoder();
    const ev = (o) => "data: " + JSON.stringify(o) + "\n\n";
    // 把一串 SSE 文本按**任意切法**喂进去（真实网络下就是会切成这样）
    const fakeFetch = (text, cuts) => async () => {
      const parts = [];
      let rest = text, i = 0;
      while (rest.length) {
        const n = Math.max(1, Math.min(cuts[i % cuts.length], rest.length));
        parts.push(rest.slice(0, n)); rest = rest.slice(n); i++;
      }
      let k = 0;
      return {
        ok: true, status: 200,
        body: { getReader: () => ({ read: async () =>
          (k < parts.length ? { done: false, value: enc.encode(parts[k++]) } : { done: true }) }) },
      };
    };
    const cfg = { apiKey: "k", baseUrl: "http://x", model: "m", timeoutMs: 5000 };

    // ① 正文流 + 一行被切开
    {
      const text = ev({ choices: [{ delta: { content: "甲" } }] })
        + ev({ choices: [{ delta: { content: "乙丙" } }] })
        + ev({ choices: [{ delta: {}, finish_reason: "stop" }] }) + "data: [DONE]\n\n";
      const seen = [];
      const r = await gradeLlm.agentTurnStream([{ role: "user", content: "x" }], null,
        { config: cfg, fetchImpl: fakeFetch(text, [3, 7, 11]) }, (e) => seen.push(e.type + ":" + e.text));
      check("正文流：delta 按顺序回调（前端才能逐字画出来）",
        seen.join("|") === "content:甲|content:乙丙", seen.join("|"));
      check("正文流：拼回完整 message.content", r.ok && r.message.content === "甲乙丙", r.message.content);
      check("正文流：推理内容与正文分开报（界面要区分「在想」和「在写」）",
        r.reasoning === "" && r.tool_calls.length === 0);
    }

    // ② 工具调用：arguments 分三片到（流式下必然如此）
    {
      const text = ev({ choices: [{ delta: { reasoning_content: "先想" } }] })
        + ev({ choices: [{ delta: { tool_calls: [{ index: 0, id: "call_A",
            function: { name: "make_cards", arguments: '{"cards":[{"type":"choice",' } }] } }] })
        + ev({ choices: [{ delta: { tool_calls: [{ index: 0,
            function: { arguments: '"stem":"甲","answer":0,' } }] } }] })
        + ev({ choices: [{ delta: { tool_calls: [{ index: 0,
            function: { arguments: '"options":["a","b","c","d"]}]}' } }] } }] })
        + ev({ choices: [{ delta: {}, finish_reason: "tool_calls" }] }) + "data: [DONE]\n\n";
      const seen = [];
      const r = await gradeLlm.agentTurnStream([{ role: "user", content: "x" }], agent.TOOLS,
        { config: cfg, fetchImpl: fakeFetch(text, [5, 13, 2]) }, (e) => seen.push(e));
      check("★ 工具调用只算一个（按 index 归并，不是三片算三个）",
        r.ok && r.tool_calls.length === 1, JSON.stringify(r.tool_calls));
      check("★ arguments 拼回了合法 JSON（拼不回来工具参数就废了）", (() => {
        try { const a = JSON.parse(r.tool_calls[0].function.arguments);
              return a.cards && a.cards[0].stem === "甲" && a.cards[0].options.length === 4; }
        catch (e) { return false; }
      })(), r.tool_calls[0] && r.tool_calls[0].function.arguments);
      check("工具名与 id 都留住了（回灌时要配对）",
        r.tool_calls[0].function.name === "make_cards" && r.tool_calls[0].id === "call_A");
      check("推理分片按 reasoning 回调（界面显示「正在推演（已写 N 字）」）",
        seen.filter(e => e.type === "reasoning").map(e => e.text).join("") === "先想",
        JSON.stringify(seen));
    }

    // ③ 两个工具并行（index 0/1 交错到）
    {
      const text = ev({ choices: [{ delta: { tool_calls: [
          { index: 0, id: "c0", function: { name: "search_bank", arguments: '{"que' } },
          { index: 1, id: "c1", function: { name: "search_notes", arguments: '{"que' } }] } }] })
        + ev({ choices: [{ delta: { tool_calls: [
          { index: 1, function: { arguments: 'ry":"b"}' } },
          { index: 0, function: { arguments: 'ry":"a"}' } }] } }] })
        + "data: [DONE]\n\n";
      const r = await gradeLlm.agentTurnStream([], agent.TOOLS, { config: cfg, fetchImpl: fakeFetch(text, [9]) });
      check("★ 两个工具并行时各归各的（交错到也不能串）", r.tool_calls.length === 2
        && JSON.parse(r.tool_calls[0].function.arguments).query === "a"
        && JSON.parse(r.tool_calls[1].function.arguments).query === "b",
        JSON.stringify(r.tool_calls.map(c => c.function.arguments)));
    }

    // ④ 出错要如实回报（上游 400 / 断流）
    {
      const bad = async () => ({ ok: false, status: 400, text: async () =>
        JSON.stringify({ error: { message: "tools is not supported" } }) });
      const r = await gradeLlm.agentTurnStream([], agent.TOOLS, { config: cfg, fetchImpl: bad });
      check("上游拒绝（模型不支持工具）→ ok:false 且带原因",
        r.ok === false && /tools is not supported/.test(r.error), r.error);

      const noBody = async () => ({ ok: true, status: 200, body: null });
      const r2 = await gradeLlm.agentTurnStream([], agent.TOOLS, { config: cfg, fetchImpl: noBody });
      check("没有流可读 → 如实失败（不静默返回空）", r2.ok === false && !!r2.error);
    }
  }

  console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
  process.exit(fail === 0 ? 0 : 1);
})();

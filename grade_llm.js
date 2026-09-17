/*
 * grade_llm.js — 简答题批改（DeepSeek 视觉模型）
 *
 * 设计要点：
 *   1. 密钥只在**服务端**读取（src/.secrets.json 或环境变量 DEEPSEEK_API_KEY），
 *      绝不下发给浏览器、也不写进生成的 dashboard.html。
 *   2. 批改逻辑做成纯函数模块，serve.js 只负责 HTTP 转发 —— 这样能单独测
 *      （tools/test_grade_llm.js：prompt 构造、JSON 解析容错、分数→FSRS 档位映射）。
 *   3. 模型：deepseek-flash（**支持图片输入**，2026-09-13 实测）。
 *      ⚠️ 官方端点实际只有 deepseek-flash 与 deepseek-v4-pro 两个模型；
 *         "deepseek-v4-flash-vision-exp" 会被静默路由到 deepseek-flash，
 *         deepseek-v4-pro 对图片返回 "unsupported image"。
 *   4. 它是推理模型：响应里有 reasoning_content，且推理会吃掉 token，
 *      max_tokens 要给够（默认 2000），否则 content 会是空串。
 */

const fs = require('fs');
const path = require('path');

// 允许 SECRETS_PATH 指向副本文件。测试必须能隔离密钥——否则一次设置页的
// 自动化测试就会把真实 API Key 覆盖掉，而且不可恢复（踩过一次）。
const SECRETS_PATH = process.env.SECRETS_PATH
  ? path.resolve(__dirname, process.env.SECRETS_PATH)
  : path.join(__dirname, '.secrets.json');

// ---------------------------------------------------------------------------
// 配置
// ---------------------------------------------------------------------------

function loadConfig(env = process.env) {
  let file = {};
  try {
    file = JSON.parse(fs.readFileSync(SECRETS_PATH, 'utf-8')).deepseek || {};
  } catch (e) { /* 没配置文件就走环境变量 */ }
  const cfg = {
    apiKey: env.DEEPSEEK_API_KEY || file.api_key || '',
    baseUrl: (env.DEEPSEEK_BASE_URL || file.base_url || 'https://api.deepseek.com').replace(/\/+$/, ''),
    model: env.DEEPSEEK_MODEL || file.model || 'deepseek-flash',
    timeoutMs: parseInt(env.DEEPSEEK_TIMEOUT_MS || '60000', 10),
  };
  return cfg;
}

function hasKey(env = process.env) {
  return !!loadConfig(env).apiKey;
}

// ---------------------------------------------------------------------------
// 评分口径
// ---------------------------------------------------------------------------

// 分数 → FSRS 档位。口径与大盘四档按钮一致：1 忘记 / 2 模糊 / 3 记得 / 4 简单
const RATING_BANDS = [
  [90, 4],   // 要点全中、表述准确 → 简单
  [75, 3],   // 基本答对、细节有瑕 → 记得
  [45, 2],   // 答对一半、关键处含糊 → 模糊
  [0, 1],    // 没答到点上 → 忘记
];

function scoreToRating(score) {
  const s = Number(score);
  if (!Number.isFinite(s)) return 1;          // 解析不出分数时按「忘记」保守处理
  const clamped = Math.max(0, Math.min(100, s));
  for (const [min, rating] of RATING_BANDS) {
    if (clamped >= min) return rating;
  }
  return 1;
}

// ---------------------------------------------------------------------------
// Prompt
// ---------------------------------------------------------------------------

// 简答题内容结构：{ stem, reference_answer, key_points[], explanation?, traps?[] }
// 兼容两种传法：{content:{...}}（serve.js 从库里取出来是这形状）或直接传 content 本身。
// ⚠️ 不做这个兼容的话，直接传 content 会静默拿到空题干——prompt 里没有题目，
//    模型只能对着"学生的答案"空评，分数毫无意义（由 tools/test_grade_llm.js 抓出）。
function buildMessages(question, userAnswer, imageDataUrl) {
  const q = question || {};
  const ct = (q.content && typeof q.content === 'object')
    ? q.content
    : (q.stem ? q : {});
  const keyPoints = Array.isArray(ct.key_points) ? ct.key_points.filter(Boolean) : [];
  const traps = Array.isArray(ct.traps) ? ct.traps.filter(Boolean) : [];

  const sys = [
    '你是一位考研阅卷老师，负责批改学生的简答题。',
    '要求：',
    '1. 先判断学生答案是否命中参考答案的每个得分点，不要因为表述不同就判错（同义表述应给分）。',
    '2. 学生可能上传手写答案的照片：先逐字辨认手写内容，再把辨认出的内容当作学生答案来批改。',
    '   辨认不确定的字用 ? 标注，不要猜测后当成学生写对。',
    '3. 数学/公式必须核对正确性：符号、上下标、正负号、分母不为零等条件都要查。',
    '4. 严格但公正：不要因为学生写了更多内容就多给分，也不要因为表述简略就扣分。',
    '5. 只输出一个 JSON 对象，不要任何寒暄、解释或 markdown 代码块。',
  ].join('\n');

  const parts = [
    '【题目】', String(ct.stem || ''),
    '',
    '【参考答案】', String(ct.reference_answer || ct.answer || '（无）'),
  ];
  if (keyPoints.length) parts.push('', '【得分点】', ...keyPoints.map((p, i) => `${i + 1}. ${p}`));
  if (traps.length) parts.push('', '【本题易错点】', ...traps.map(t => '- ' + t));
  if (userAnswer && String(userAnswer).trim()) {
    parts.push('', '【学生答案（文字输入）】', String(userAnswer).trim());
  }
  if (imageDataUrl) {
    parts.push('', '【学生答案（手写照片，见下图）】', '请先辨认照片中的手写内容，再据此批改。');
  }
  if (!userAnswer && !imageDataUrl) {
    parts.push('', '【学生答案】', '（学生未提交任何答案，score 给 0，并提示需要作答）');
  }
  parts.push(
    '',
    '【输出格式】严格输出如下 JSON：',
    '{"transcription":"手写辨认结果（无图片时为空串）",',
    ' "score":0到100的整数,',
    ' "verdict":"对|部分对|错",',
    ' "hits":["命中的得分点"],',
    ' "missed":["遗漏或答错的点，并说明正确说法"],',
    ' "feedback":"给学生的中文点评，2-4句，先肯定对的部分再指出错在哪、怎么改"}',
  );

  const content = imageDataUrl
    ? [
        { type: 'text', text: parts.join('\n') },
        { type: 'image_url', image_url: { url: imageDataUrl } },
      ]
    : parts.join('\n');

  return [
    { role: 'system', content: sys },
    { role: 'user', content },
  ];
}

// ---------------------------------------------------------------------------
// 响应解析（容错）
// ---------------------------------------------------------------------------

// 推理模型有时会裹 ```json 代码块或在 JSON 前后加说明，这里把第一个完整对象抠出来
function extractJson(text) {
  const raw = String(text == null ? '' : text);
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const body = fenced ? fenced[1] : raw;
  const start = body.indexOf('{');
  if (start < 0) return null;
  let depth = 0, inStr = false, esc = false;
  for (let i = start; i < body.length; i++) {
    const ch = body[i];
    if (inStr) {
      if (esc) esc = false;
      else if (ch === '\\') esc = true;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') inStr = true;
    else if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) {
        try { return JSON.parse(body.slice(start, i + 1)); }
        catch (e) { return null; }
      }
    }
  }
  return null;
}

function normalizeResult(parsed, rawText) {
  if (!parsed || typeof parsed !== 'object') {
    return {
      ok: false, score: null, verdict: '', transcription: '',
      hits: [], missed: [], feedback: '',
      error: '模型没有返回可解析的 JSON', raw: String(rawText || '').slice(0, 500),
    };
  }
  const score = Number.parseInt(parsed.score, 10);
  const strArr = v => (Array.isArray(v) ? v.filter(x => typeof x === 'string' && x.trim()) : []);
  return {
    ok: true,
    score: Number.isFinite(score) ? Math.max(0, Math.min(100, score)) : null,
    verdict: typeof parsed.verdict === 'string' ? parsed.verdict.trim() : '',
    transcription: typeof parsed.transcription === 'string' ? parsed.transcription.trim() : '',
    hits: strArr(parsed.hits),
    missed: strArr(parsed.missed),
    feedback: typeof parsed.feedback === 'string' ? parsed.feedback.trim() : '',
    suggested_rating: scoreToRating(score),
  };
}

// ---------------------------------------------------------------------------
// 调用
// ---------------------------------------------------------------------------

/**
 * 关掉「思考」的参数。
 *
 * 这个端点的模型默认会先产一段 reasoning_content 再给正文。对**延迟敏感**的
 * 交互（答错/答对后现场追问），那段时间纯粹是白等：实测同一个问题
 *   基线 3127ms（reasoning 645 字） → 关掉后 1086ms（reasoning 0 字），
 * 正文长度与质量基本不变（205 字 vs 203 字）。
 * 试过的其它参数（reasoning_effort=low/minimal、enable_thinking=false）都没什么效果，
 * 只有 thinking:{type:'disabled'} 真正生效，所以只发这一个。
 */
const NO_THINK = { thinking: { type: 'disabled' } };

async function callDeepSeek(messages, cfg, fetchImpl, maxTokens, opts = {}) {
  const doFetch = fetchImpl || globalThis.fetch;
  if (!cfg.apiKey) throw new Error('未配置 DeepSeek API key（src/.secrets.json 或 DEEPSEEK_API_KEY）');
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), cfg.timeoutMs);
  try {
    const payload = Object.assign({
      model: cfg.model, messages, max_tokens: maxTokens || 3000, temperature: 0.2,
    }, opts.quick ? NO_THINK : null, opts.extra || null);
    const resp = await doFetch(`${cfg.baseUrl}/chat/completions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${cfg.apiKey}` },
      body: JSON.stringify(payload),
      signal: ctl.signal,
    });
    const bodyText = await resp.text();
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`;
      try { msg += ': ' + (JSON.parse(bodyText).error?.message || ''); } catch (e) {}
      return { ok: false, error: msg, raw: bodyText.slice(0, 500) };
    }
    let json;
    try { json = JSON.parse(bodyText); }
    catch (e) { return { ok: false, error: '响应不是合法 JSON', raw: bodyText.slice(0, 500) }; }
    const msg = json.choices?.[0]?.message || {};
    return {
      ok: true,
      text: msg.content || '',
      reasoning: String(msg.reasoning_content || ''),
      finish: json.choices?.[0]?.finish_reason || '',
      // function calling 用（2026-09-22）：工具调用要**原样交回下一轮**——
      // assistant 的 tool_calls 与随后的 tool 结果必须配成对，少一个字段这条链就断了。
      tool_calls: Array.isArray(msg.tool_calls) ? msg.tool_calls : [],
      message: msg,
    };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 批改一道简答题。
 * @param {{content:object}} question  questions 表里那行（content 已解析）
 * @param {string} userAnswer 文字答案（可为空）
 * @param {string} imageDataUrl 手写答案照片，data:image/...;base64,...（可为空）
 * @returns {Promise<object>} {ok, score, verdict, transcription, hits, missed, feedback, suggested_rating}
 */
async function gradeAnswer(question, userAnswer, imageDataUrl, opts = {}) {
  const cfg = opts.config || loadConfig();
  const messages = buildMessages(question, userAnswer, imageDataUrl);
  let resp = await callDeepSeek(messages, cfg, opts.fetchImpl);

  // 推理模型的两类截断都要重试：
  //   1) reasoning 占满预算，content 为空且 finish=length；
  //   2) content 写了一部分但被截断（残缺 JSON），此时 extractJson 返回 null。
  // 两类都必须加大预算重试，否则用户看到的是"模型没回答"或半截 JSON 解析失败。
  if (resp.ok && resp.finish === 'length' && (!resp.text.trim() || !extractJson(resp.text))) {
    console.warn('[Grade] 首轮输出不完整（finish=length, textLen=' + resp.text.length
      + '），用更大 max_tokens=6000 重试一次');
    resp = await callDeepSeek(messages, cfg, opts.fetchImpl, 6000);
  }

  if (!resp.ok) return { ok: false, error: resp.error, raw: resp.raw };
  if (!resp.text.trim()) {
    return {
      ok: false,
      error: '模型没给出正文（推理过程耗尽了 token 预算），可再试一次',
      raw: String(resp.reasoning || '').slice(0, 300),
    };
  }
  return normalizeResult(extractJson(resp.text), resp.text);
}

// ---------------------------------------------------------------------------
// 错题针对性解析与追问（2026-09-14）
//
// 与 gradeAnswer 的分工：批改是「判断对错并给分」，这里是「针对学生的**具体错选**
// 讲清楚为什么错」。题库自带的 explanation 通常只解释正确答案为什么对，不解释
// 学生选的那个为什么不行——而错选恰恰暴露了真正的理解偏差，这才是要讲的地方。
// ---------------------------------------------------------------------------

/**
 * 内置的 system prompt —— **刻意写得极简**（2026-09-21 用户要求）。
 *
 * 原委：我在里面塞过「讲陷阱/边界/自查」「要点最多几条」「建议补进笔记」这类要求，
 * 用户反馈**有误导性**，他要自己写。所以内置的只剩两句机械必需的东西：
 *   ① 角色（你是谁）
 *   ② 输出格式（中文 + Markdown + $公式$，前端要用 KaTeX 渲染）
 * 想让它怎么讲，去「设置 → AI 提示词」里写；写了就用你的，我一个字都不加。
 */
const EXPLAIN_SYSTEM = [
  '你是考研辅导老师。',
  '用中文和 Markdown；公式用 $...$（行内）或 $$...$$（独立行）。',
  // 2026-09-17：题库里的老卡片是 e^(x^2)、∬_D、2^32B 这类 ASCII 写法，
  // 学生问的那道题连同它自带的解析会一起放进上下文，模型会照着模仿。
  // 大盘虽已加了兜底（简单写法也能渲染），但「指数后面还粘着字母」的分不出来，
  // 所以这里明确要求用 $...$ 写。
  '上标写 $e^{x^2}$、下标写 $x_i$；不要写 e^(x^2)、x_i、2^32B 这类 ASCII 写法。',
].join('\n');

/** 同上，答对后的追问用同一个内置默认（区别在请求里的用户消息）。 */
const EXPLAIN_CORRECT_SYSTEM = EXPLAIN_SYSTEM;

/**
 * 取这次要用的 system prompt。
 * ⚠️ 用户自定义时**原样使用**，绝不追加我的要求（否则又是「我替他写提示词」）。
 */
function explainSystem(mode, custom) {
  const c = String(custom == null ? '' : custom).trim();
  if (c) return c;
  return mode === 'correct' ? EXPLAIN_CORRECT_SYSTEM : EXPLAIN_SYSTEM;
}



/** 把题目内容整理成模型能读的文本 */
function describeQuestion(question, correct) {
  const c = (question && question.content) || {};
  const L = [];
  if (c.stem) L.push('【题干】\n' + c.stem);
  if (Array.isArray(c.options) && c.options.length) {
    L.push('【选项】\n' + c.options
      .map((o, i) => String.fromCharCode(65 + i) + '. ' + o).join('\n'));
  }
  if (correct) L.push('【正确答案】' + correct);
  else if (c.reference_answer) L.push('【正确答案】' + c.reference_answer);
  if (c.explanation) L.push('【题库自带解析（可能不完整，仅作参考）】\n' + c.explanation);
  return L.join('\n\n');
}

/**
 * 学生自己带了问题时（答对/看答案后「我有具体想问的」）追加的要求。
 *
 * 用户明确说过：他要问的肯定是**针对性**的问题，不要预设问题清单。
 * 所以这里把重心交还给学生的问题——上面那套「陷阱/边界/自查」的要求只在
 * 有助于回答时才用，不许为了凑齐要点而偏题。
 */
const ASK_CENTERED = [
  '',
  '【这次学生带了具体问题】',
  '以他的问题为中心回答，先把这个问题本身讲清楚；上面那些要点只是备选，',
  '只有在有助于回答时才提，不要为了面面俱到而偏题，也不要反问他还有什么想问的。',
].join('\n');

/**
 * 首轮消息。mode='correct' 走「答对追问」那套要求（学生没错选可讲，
 * 但心里没底——重点是陷阱、边界和自查），否则按错选讲解。
 * @param {string} [ask] 学生自己输入的问题；给了就以它为中心回答
 * @param {string} [custom] 用户在设置里自己写的 system prompt；给了就原样用
 */
function buildExplainMessages(question, chosen, correct, mode, ask, custom) {
  const body = describeQuestion(question, correct);
  const q = String(ask == null ? '' : ask).trim();
  const sys = explainSystem(mode, custom);
  if (mode === 'correct') {
    return [
      { role: 'system', content: sys + (q && !custom ? ASK_CENTERED : '') },
      { role: 'user', content: body
          + '\n\n【学生选的】' + (chosen ? String(chosen) : (correct || '（本题正确项）'))
          + (q ? '\n\n【学生的问题】' + q
               : '\n\n他答对了，但对这道题仍有疑问，请按上面的要求讲透。') },
    ];
  }
  const pick = chosen ? String(chosen) : '（未作答，直接看了答案）';
  return [
    { role: 'system', content: sys + (q && !custom ? ASK_CENTERED : '') },
    { role: 'user', content: body + '\n\n【学生选的是】' + pick
        + (q ? '\n\n【学生的问题】' + q : '\n\n请针对这个错选讲解。') },
  ];
}

/**
 * 调一次，网络类失败自动重试一次。
 * 生成长解析比批改慢得多（实测 30s 量级），途中偶发 fetch failed / 连接重置很常见；
 * 直接把失败抛给用户既浪费一次生成，体验也差。
 *
 * ⚠️ 截断处理（2026-09-20 修复）：
 *   推理模型有两个坑都会导致"有文本但被腰斩"：
 *     1) reasoning_content 吃掉大量 token，留给正文的预算不足，finish_reason=length
 *        但 resp.text 里已经写了几百字、末尾公式/句子被切断（最常见的 bug 表现）；
 *     2) 偶发 finish_reason='stop' 但正文依然是空串（早期只对这一种重试）。
 *   两种情况都必须用更大预算重试：截断后的残缺文本（如公式 `V(f` 戛然而止）直接展示
 *   给用户比"模型没回答"更糟糕——因为它看起来像完整回答，学生容易被误导。
 */
async function callWithRetry(messages, cfg, fetchImpl, maxTokens, opts = {}) {
  let last = { ok: false, error: '未知错误' };
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const budget = attempt === 0 ? maxTokens : maxTokens * 3;
      const resp = await callDeepSeek(messages, cfg, fetchImpl, budget, opts);
      if (!resp.ok) return resp;
      // 被 length 截断：不管有没有正文，都要加大预算重试（首刀可能写了一大段被腰斩）
      if (resp.finish === 'length') {
        last = { ok: false, error: '模型输出被截断（token 预算不足）', finish: resp.finish };
        console.warn('[Explain] 输出被截断（finish=length, textLen=' + resp.text.length
          + '），用更大 budget=' + (maxTokens * 3) + ' 重试');
      } else if (resp.text.trim()) {
        return resp;
      } else {
        // 正文为空：推理模型可能把预算全花在 reasoning 上了，加大预算再试一次。
        last = { ok: false, error: '模型没给出正文（推理可能占满了 token）' };
        console.warn('[Explain] 正文为空，加大 token 预算重试（finish=' + resp.finish + '）');
      }
    } catch (e) {
      last = { ok: false, error: '网络请求失败：' + e.message };
      console.warn('[Explain] 调用失败：' + e.message);
    }
    if (attempt === 0) await new Promise(r => setTimeout(r, 800));
  }
  return last;
}

/**
 * 生成针对错选的解析。
 *
 * ⚠️ 交互类调用由**调用方**决定要不要思考：默认（quick）关掉，省掉那 600ms 上下的白等；
 *    「深想一遍」按钮或设置页把默认改成深度时传 `{quick:false}`。
 * @returns {Promise<{ok:boolean, text?:string, error?:string, raw?:string}>}
 */
async function explainAnswer(question, chosen, correct, opts = {}) {
  const cfg = opts.config || loadConfig();
  const messages = buildExplainMessages(question, chosen, correct, opts.mode, opts.ask, opts.system);
  const quick = opts.quick !== false;
  // ⚠️ 预算别抠（2026-09-21 实测）：去掉字数上限后正文常有 1000~1800 字，
  //    1200/3000 这种「按旧字数配的预算」会被 finish=length 截断 → 触发 3 倍预算重试
  //    → 白烧一次调用、耗时翻倍（见过 31s 的解析，其中一半是重试）。
  const resp = await callWithRetry(messages, cfg, opts.fetchImpl, quick ? 3000 : 6000, { quick: quick });
  if (!resp.ok) return { ok: false, error: resp.error, raw: resp.raw };
  if (!resp.text.trim()) {
    return { ok: false, error: '模型没给出正文（推理占满了 token），可以再试一次' };
  }
  return { ok: true, text: resp.text.trim() };
}

/**
 * 追问：把之前的对话历史一起带上，保持上下文连贯。
 * 同样默认快速模式（见 explainAnswer 的说明）——追问尤其等不起。
 * @param {Array<{role:string,content:string}>} history 该线索已有的消息（含首轮解析）
 */
async function explainFollowup(question, chosen, correct, history, userMessage, opts = {}) {
  const cfg = opts.config || loadConfig();
  // 复用首轮构造，保证追问沿用同一套 system 要求：
  // 答对追问的线索若在这里退回「针对错选讲解」，模型会开始纠正一个根本不存在的错选。
  // ⚠️ 首轮若来自「学生自己提问」（日志里以【我问】开头），要按同样口径重建首轮消息，
  //   否则追问时模型看到的是那套「讲陷阱/边界」的泛化框架，而不是学生原本的问题。
  const messages = buildExplainMessages(question, chosen, correct, opts.mode, opts.ask, opts.system);
  // 历史里已经包含上面那条 user 消息之后的内容，直接续上
  for (const m of (history || [])) {
    if (m && (m.role === 'user' || m.role === 'assistant') && m.content) {
      messages.push({ role: m.role, content: String(m.content) });
    }
  }
  messages.push({ role: 'user', content: String(userMessage || '') });

  const quick = opts.quick !== false;
  const resp = await callWithRetry(messages, cfg, opts.fetchImpl, quick ? 3000 : 6000, { quick: quick });
  if (!resp.ok) return { ok: false, error: resp.error, raw: resp.raw };
  if (!resp.text.trim()) return { ok: false, error: '模型没给出正文，可以再试一次' };
  return { ok: true, text: resp.text.trim() };
}

// ---------------------------------------------------------------------------
// 教练式对话（错题复盘 / 薄弱点学习共用，2026-09-20）
//
// 与 explainAnswer 的分工：那个是「一道题的错选讲解」，输入固定；
// 这里是开放多轮对话，可带多张图（整页试卷、错题照片），system prompt 由调用方注入
// 检索到的上下文（笔记原文、历史错因、已有闪卡）。复用 callWithRetry，
// 因此推理模型把 token 预算吃光导致的截断（finish=length）同样会自动加大预算重试。
// ---------------------------------------------------------------------------

/**
 * 把内部历史转成 OpenAI 格式。图片只挂在 user 消息上——
 * assistant 消息带图没有意义，且部分端点会直接拒。
 * @param {Array<{role:string, content:string, images?:string[]}>} history
 */
function toApiMessages(history) {
  const out = [];
  for (const m of (history || [])) {
    if (!m) continue;
    const role = m.role === 'assistant' ? 'assistant' : 'user';
    const imgs = Array.isArray(m.images) ? m.images.filter(x => typeof x === 'string' && x.startsWith('data:image/')) : [];
    const text = String(m.content || '');
    if (role === 'assistant' || !imgs.length) {
      if (text) out.push({ role, content: text });
      continue;
    }
    out.push({
      role,
      content: [{ type: 'text', text: text || '（见图）' }]
        .concat(imgs.map(u => ({ type: 'image_url', image_url: { url: u } }))),
    });
  }
  return out;
}

async function coachChat(history, system, opts = {}) {
  const cfg = opts.config || loadConfig();
  const messages = [{ role: 'system', content: String(system || '') }].concat(toApiMessages(history));
  // ⚠️ 这里**默认保持深度思考**（复盘/薄弱点学习值得多想一会儿）；
  //    现场等着的短问答（读笔记时的追问）由调用方显式传 quick:true 省掉那段白等。
  const resp = await callWithRetry(messages, cfg, opts.fetchImpl, opts.maxTokens || 3500,
    { quick: !!opts.quick });
  if (!resp.ok) return { ok: false, error: resp.error, raw: resp.raw };
  if (!resp.text.trim()) return { ok: false, error: '模型没给出正文（推理可能占满了 token），请再试一次' };
  return { ok: true, text: resp.text.trim(), finish: resp.finish };
}

/**
 * 带工具的一轮对话（function calling）——给「网页里的 AI 自己调接口」用（2026-09-22）。
 *
 * 与 coachChat 的差别只在一点：它要把**模型的原始 message**（含 tool_calls）交回调用方，
 * 因为多轮工具调用要靠 tool_call_id 把「模型要求调什么」和「工具返回了什么」配成对。
 * tools 传空 / 不传就是普通对话（轮次用完后逼模型收尾时用这一档）。
 *
 * @returns {Promise<{ok:boolean, message?:object, tool_calls?:Array, error?:string}>}
 */
async function agentTurn(messages, tools, opts = {}) {
  const cfg = opts.config || loadConfig();
  const useTools = Array.isArray(tools) && tools.length;
  const resp = await callDeepSeek(messages, cfg, opts.fetchImpl, opts.maxTokens || 2500,
    Object.assign({ quick: !!opts.quick },
      useTools ? { extra: { tools, tool_choice: opts.toolChoice || 'auto' } } : null));
  if (!resp.ok) return { ok: false, error: resp.error, raw: resp.raw };
  return {
    ok: true,
    message: resp.message || { role: 'assistant', content: resp.text || '' },
    tool_calls: resp.tool_calls || [],
    finish: resp.finish,
  };
}

/**
 * 流式的一轮（+工具）。给「边做边播」用（2026-09-22 用户反馈：工具回路十几秒，
 * 只给一个「思考中…」等于什么都看不见）。
 *
 * onDelta 会收到两类事件，分开报是为了界面能区分两件事：
 *   {type:'reasoning', text} —— 还在推演（DeepSeek 默认开思考，这段可能好几秒）
 *   {type:'content', text}   —— 正文在长
 *
 * ⚠️ 流式下 tool_calls 是**分片**来的（同一个 index 多次 delta），必须按 index 归并、
 *    把 arguments 拼起来，否则拿到的是一段残缺 JSON，工具参数就废了。
 */
async function agentTurnStream(messages, tools, opts = {}, onDelta) {
  const cfg = opts.config || loadConfig();
  const emit = typeof onDelta === 'function' ? onDelta : () => {};
  const useTools = Array.isArray(tools) && tools.length;
  const doFetch = opts.fetchImpl || globalThis.fetch;
  if (!cfg.apiKey) return { ok: false, error: '未配置 DeepSeek API key（src/.secrets.json 或 DEEPSEEK_API_KEY）' };
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), cfg.timeoutMs);
  try {
    const payload = Object.assign({
      model: cfg.model, messages, max_tokens: opts.maxTokens || 2500,
      temperature: 0.2, stream: true,
    }, opts.quick ? NO_THINK : null,
      useTools ? { tools, tool_choice: opts.toolChoice || 'auto' } : null);
    const resp = await doFetch(`${cfg.baseUrl}/chat/completions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${cfg.apiKey}` },
      body: JSON.stringify(payload),
      signal: ctl.signal,
    });
    if (!resp.ok) {
      let bodyText = '';
      try { bodyText = await resp.text(); } catch (e) {}
      let msg = `HTTP ${resp.status}`;
      try { msg += ': ' + (JSON.parse(bodyText).error?.message || ''); } catch (e) {}
      return { ok: false, error: msg, raw: String(bodyText).slice(0, 500) };
    }
    if (!resp.body) return { ok: false, error: '服务端没有返回流（resp.body 为空）' };

    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '', content = '', reasoning = '', finish = '';
    const calls = [];
    for (;;) {
      const step = await reader.read();
      if (step.done) break;
      buf += dec.decode(step.value, { stream: true });
      let nl;
      // SSE 行可能被切开，所以按「完整行」消费
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).replace(/\r$/, '').trim();
        buf = buf.slice(nl + 1);
        if (!line || line.indexOf('data:') !== 0) continue;
        const data = line.slice(5).trim();
        if (!data || data === '[DONE]') continue;
        let j = null;
        try { j = JSON.parse(data); } catch (e) { continue; }
        const ch = j.choices && j.choices[0];
        if (!ch) continue;
        if (ch.finish_reason) finish = ch.finish_reason;
        const d = ch.delta || {};
        if (d.reasoning_content) { reasoning += d.reasoning_content; emit({ type: 'reasoning', text: d.reasoning_content }); }
        if (d.content) { content += d.content; emit({ type: 'content', text: d.content }); }
        if (Array.isArray(d.tool_calls)) {
          for (const tc of d.tool_calls) {
            const i = Number.isInteger(tc.index) ? tc.index : 0;
            if (!calls[i]) calls[i] = { id: '', type: 'function', function: { name: '', arguments: '' } };
            if (tc.id) calls[i].id = tc.id;
            if (tc.function) {
              if (tc.function.name) calls[i].function.name += tc.function.name;
              if (tc.function.arguments) calls[i].function.arguments += tc.function.arguments;
            }
          }
        }
      }
    }
    const tool_calls = calls.filter(Boolean);
    return {
      ok: true,
      message: { role: 'assistant', content: content, tool_calls: tool_calls },
      tool_calls: tool_calls, finish: finish, reasoning: reasoning,
    };
  } finally {
    clearTimeout(timer);
  }
}

module.exports = {
  loadConfig, hasKey, buildMessages, extractJson, normalizeResult,
  scoreToRating, gradeAnswer, RATING_BANDS,
  buildExplainMessages, explainAnswer, explainFollowup, explainSystem,
  coachChat, agentTurn, agentTurnStream, toApiMessages,
};

// CLI 自测：node grade_llm.js --qid Q-xxx --text "答案"
if (require.main === module) {
  const args = process.argv.slice(2);
  const get = k => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
  const text = get('--text') || '';
  const imgPath = get('--image');
  const qid = get('--qid');
  const fsx = require('fs');
  const question = qid
    ? JSON.parse(require('node:sqlite').DatabaseSync(path.join(__dirname, 'question_bank.db'), { readOnly: true })
        .prepare('SELECT content FROM questions WHERE id = ?').get(qid).content)
    : {
        stem: '简述洛必达法则不能用于"一点可导"情形的原因。',
        reference_answer: '洛必达要求分子分母在去心邻域内可导到所需阶数；「一点可导」不保证邻域可导，故只能用泰勒展开。',
        key_points: ['洛必达的使用条件（去心邻域可导）', '一点可导 ≠ 邻域可导', '替代方法是泰勒展开'],
      };
  let img = null;
  if (imgPath) {
    const b = fsx.readFileSync(imgPath).toString('base64');
    const ext = path.extname(imgPath).slice(1).toLowerCase() || 'png';
    img = `data:image/${ext === 'jpg' ? 'jpeg' : ext};base64,` + b;
  }
  gradeAnswer({ content: question }, text, img)
    .then(r => console.log(JSON.stringify(r, null, 2)))
    .catch(e => { console.error('批改失败:', e.message); process.exit(1); });
}

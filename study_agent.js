/**
 * study_agent.js —— 「网页里的 AI 自己调接口」（2026-09-22）
 *
 * 背景：薄弱点学习页的 AI 原来只能聊天——它想出题就写在正文里，学生再点按钮才归卡。
 * 用户要的是**让页面的 AI 自己会用这些功能**：它自己搜题库、自己搜笔记、自己把题写进
 * 题库、**自己把闪卡浮窗弹出来让学生练**。这一层就是那些「工具」的定义与实现。
 *
 * 为什么单独成模块：
 *   ① serve.js 里的端点只管 HTTP 与库连接，工具的规则在这儿能单独跑用例；
 *   ② 以后要走重的那条路（把同一套工具包成 MCP 喂给 DeepSeek Harness / 别的
 *      coding agent），改的只是「谁来驱动这些工具」，工具本身一行不用动。
 *
 * 边界：只认传进来的 deps（db 与几个查询/写库函数都由 serve.js 注入），
 *      模块自己不 open 数据库、不碰文件——这样用例里塞假实现就能跑。
 */

const studyCards = require('./study_cards');

/** 一輪对话里最多让模型调几轮工具。再多就是模型在打转，直接逼它出结论。 */
const MAX_ROUNDS = 3;

const SUBJECT_ENUM = ['408', '数学一', '政治', '英语一'];

/** 工具定义（OpenAI function calling 的形状）。描述写清楚「什么时候该用」，
 *  模型才会在对的时候调 —— 这几句比参数 schema 更重要。 */
const TOOLS = [
  {
    type: 'function',
    function: {
      name: 'search_bank',
      description: '检索闪卡题库：命中的考点（含现有卡数、正确率）与几道已有题目。'
        + '想确认「这个知识点现在有没有卡、练得怎么样」时用它，别凭印象说题库里有没有。',
      parameters: {
        type: 'object',
        properties: {
          query: { type: 'string', description: '知识点关键词，如「微分方程 通解结构」「Cache 写回」' },
          subject: { type: 'string', enum: SUBJECT_ENUM, description: '限定科目；不确定就别传' },
        },
        required: ['query'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'search_notes',
      description: '检索这个学生的笔记，返回标题与相对路径。要他重读笔记前先搜一次，'
        + '拿准确的标题和路径给他，不要凭印象编一个路径。',
      parameters: {
        type: 'object',
        properties: {
          query: { type: 'string', description: '知识点关键词' },
          subject: { type: 'string', enum: SUBJECT_ENUM, description: '限定科目；不确定就别传' },
        },
        required: ['query'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'make_cards',
      description: '把你刚出的练习题写进题库（归卡），返回 card_id。**出了题就调它** —— '
        + '只有归了卡这些题才能用闪卡练、才进 FSRS 调度；同题干会自动复用已有卡，重复问不会插第二张。',
      parameters: {
        type: 'object',
        properties: {
          cards: {
            type: 'array',
            description: '要归卡的题，一次最多 8 道',
            items: {
              type: 'object',
              properties: {
                type: { type: 'string', enum: ['choice', 'judge', 'fill'] },
                stem: { type: 'string', description: '题干本身，不要重复选项' },
                options: {
                  type: 'array', items: { type: 'string' },
                  description: 'choice 必填，恰好 4 条，不要带 A./B. 前缀（渲染层自己加）',
                },
                answer: {
                  description: 'choice 填正确项下标（从 0 起）；judge 填「正确」或「错误」；fill 填答案本身',
                },
                explanation: { type: 'string', description: '为什么对、其余项为什么错' },
                traps: { type: 'array', items: { type: 'string' }, description: '学生最容易犯的错' },
                topic: { type: 'string', description: '考点名称，用来归到知识图谱' },
              },
              required: ['type', 'stem', 'answer'],
            },
          },
        },
        required: ['cards'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'open_practice',
      description: '让学生屏幕上**直接弹出闪卡浮窗**开始练这几张卡（全屏、键盘、评分都在）。'
        + '只能在 make_cards 拿到 card_id 之后再调，而且只在学生确实要练的时候调；'
        + '没归卡就调它是无意义的（卡号必须是这一轮 make_cards 返回的）。',
      parameters: {
        type: 'object',
        properties: {
          card_ids: { type: 'array', items: { type: 'string' }, description: 'make_cards 返回的 card_id' },
          title: { type: 'string', description: '浮窗标题，如「微分方程·解的结构 3 张」' },
        },
        required: ['card_ids'],
      },
    },
  },
];

/** 「正在干什么」的实时标签（流式时先报这个，工具跑完再报结果）。
 *  带着关键词一起显示，人才知道它到底在搜什么 —— 只写「正在调用工具」等于没说。 */
function runningLabel(name, args) {
  const a = (args && typeof args === 'object') ? args : {};
  const q = String(a.query || '').slice(0, 18);
  if (name === 'search_bank') return q ? ('正在搜题库「' + q + '」…') : '正在搜题库…';
  if (name === 'search_notes') return q ? ('正在翻笔记「' + q + '」…') : '正在翻笔记…';
  if (name === 'make_cards') {
    const n = Array.isArray(a.cards) ? a.cards.length : 0;
    return n ? ('正在把 ' + n + ' 道题写进题库…') : '正在归卡…';
  }
  if (name === 'open_practice') {
    const n = Array.isArray(a.card_ids) ? a.card_ids.length : 0;
    return n ? ('正在打开闪卡浮窗（' + n + ' 张）…') : '正在打开闪卡浮窗…';
  }
  return '正在干活…';
}

/** 讲给模型听的「你有工具」说明。拼在 STUDY_SYSTEM 后面。 */
const AGENT_HINT = [
  '',
  '【本轮你有工具，可以直接动手（重要）】',
  '· search_bank(query)：查题库里有没有对得上的考点与卡（别凭印象说「题库里没有」）。',
  '· search_notes(query)：查笔记的准确标题与路径，要学生重读时给他。',
  '· make_cards(cards)：**出了小题就调它**，把题写进题库并拿到 card_id（同题干自动复用已有卡）。',
  '· open_practice(card_ids)：让学生屏幕上直接弹出闪卡浮窗开始练（只能用本轮 make_cards 拿到的 card_id）。',
  '学生说「要练 / 给我出题」这类诉求时，正常顺序是：讲清原理 → make_cards → open_practice。',
  '⚠️ 走工具这条路时，**不要再在正文里贴 cards JSON 块**（那是没有工具时的退路，贴了等于给两遍）。',
].join('\n');

/** 结果裁短：进 trace 给人看，也进 tool 消息给模型看。 */
function clip(s, n) {
  const t = String(s == null ? '' : s);
  return t.length > n ? t.slice(0, n) + '…' : t;
}

/**
 * 造一个工具执行器。
 *
 * @param deps {
 *   db,                    已打开的库（search_bank 会用它的 SQL）
 *   subject,               本轮的科目（'all' 或具体科目）
 *   today,                 本地日期（归卡的 source 用）
 *   searchTopics(db, subject, query, limit),
 *   searchNotes(subject, query, limit),
 *   insertCards(cards, subject)         → {inserted, reused, card_ids}
 *   findTopic(db, subject, topicName, stem)
 *   actions: []            出口：前端要执行的动作会 push 到这里
 * }
 * @returns async (name, args) → 工具结果对象（每个结果都带 summary，给界面显示用）
 */
function makeToolRunner(deps) {
  const d = deps || {};
  const actions = Array.isArray(d.actions) ? d.actions : [];
  // 本轮真的归过卡的卡号。open_practice 只认这一批 —— 模型可能编一个卡号出来，
  // 那种「弹一个练不了的浮窗」比不弹更糟。
  const created = new Set();

  return async function run(name, args) {
    const a = (args && typeof args === 'object') ? args : {};
    const subj = (SUBJECT_ENUM.indexOf(a.subject) >= 0) ? a.subject
      : (SUBJECT_ENUM.indexOf(d.subject) >= 0 ? d.subject : 'all');

    if (name === 'search_bank') {
      const query = String(a.query || '').slice(0, 120);
      if (!query) return { ok: false, summary: '搜题库：关键词为空', error: 'query 不能为空' };
      let topics = [];
      try { topics = d.searchTopics(d.db, subj, query, 6) || []; } catch (e) { topics = []; }
      let samples = [];
      if (topics.length) {
        try {
          const ids = topics.map(t => t.id);
          samples = d.db.prepare(
            'SELECT q.type, q.topic_id, q.content FROM questions q '
            + 'LEFT JOIN cards c ON c.question_id = q.id '
            + 'WHERE q.topic_id IN (' + ids.map(() => '?').join(',') + ') '
            + 'AND COALESCE(c.suspended, 0) = 0 ORDER BY q.created_at DESC LIMIT 6'
          ).all(...ids).map(r => {
            let stem = '';
            try { stem = JSON.parse(r.content).stem || ''; } catch (e) { stem = ''; }
            return { type: r.type, topic_id: r.topic_id, stem: clip(stem, 80) };
          });
        } catch (e) { samples = []; }
      }
      return {
        ok: true,
        summary: topics.length ? ('搜题库「' + clip(query, 14) + '」：命中 ' + topics.length + ' 个考点')
                               : ('搜题库「' + clip(query, 14) + '」：没有命中'),
        topics: topics.map(t => ({ id: t.id, name: t.name, subject: t.subject,
                                   cards: t.cards, acc: t.acc, answered: t.answered })),
        samples: samples,
        hint: topics.length ? '' : '题库里没有直接命中的考点，可以换个更细的说法，或直接手写题再用 make_cards 归卡。',
      };
    }

    if (name === 'search_notes') {
      const query = String(a.query || '').slice(0, 120);
      if (!query) return { ok: false, summary: '搜笔记：关键词为空', error: 'query 不能为空' };
      let list = [];
      try { list = d.searchNotes(subj, query, 6) || []; } catch (e) { list = []; }
      return {
        ok: true,
        summary: '搜笔记「' + clip(query, 14) + '」：命中 ' + list.length + ' 篇',
        notes: list.map(n => ({ title: n.title, path: n.path || '', subject: n.subject || '' })),
      };
    }

    if (name === 'make_cards') {
      const norm = studyCards.normalizeCards(Array.isArray(a.cards) ? a.cards : []);
      if (!norm.length) {
        return { ok: false, summary: '归卡失败：题干或选项不完整',
                 error: '没有可入库的题（题干/选项不完整，选择题必须恰好 4 个选项且 answer 合法）' };
      }
      let out;
      try { out = d.insertCards(norm, subj); }
      catch (e) { return { ok: false, summary: '归卡失败：' + clip(e.message, 40), error: e.message }; }
      const ids = (out && out.card_ids) || [];
      ids.forEach(id => created.add(String(id)));
      norm.forEach((c, i) => actions.push({
        type: 'card_filed', card_id: ids[i] || '', stem: clip(c.stem, 40), kind: c.type,
      }));
      return {
        ok: true,
        summary: '归卡：新增 ' + (out.inserted || 0) + ' 张' + (out.reused ? '，复用 ' + out.reused + ' 张' : ''),
        inserted: out.inserted || 0, reused: out.reused || 0, card_ids: ids,
        hint: ids.length ? '现在可以调 open_practice 让学生直接开练。' : '',
      };
    }

    if (name === 'open_practice') {
      const want = (Array.isArray(a.card_ids) ? a.card_ids : []).map(x => String(x == null ? '' : x));
      const ids = want.filter(x => created.has(x)).slice(0, 40);
      if (!ids.length) {
        return { ok: false, summary: '开练失败：卡号不是本轮归卡的',
                 error: '这些 card_id 不是本轮 make_cards 返回的。先调 make_cards 拿到卡号，再调它。' };
      }
      const title = clip(String(a.title || '').trim(), 40) || ('刚出的 ' + ids.length + ' 张');
      actions.push({ type: 'practice', card_ids: ids, title: '🧠 ' + title });
      return { ok: true, summary: '开练：' + ids.length + ' 张（浮窗已弹出）',
               card_ids: ids, note: '页面上的闪卡浮窗已为这 ' + ids.length + ' 张卡打开。' };
    }

    return { ok: false, summary: '未知工具：' + clip(name, 20), error: 'unknown tool: ' + name };
  };
}

module.exports = {
  MAX_ROUNDS,
  TOOLS,
  AGENT_HINT,
  runningLabel,
  makeToolRunner,
};

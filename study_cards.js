/**
 * study_cards.js —— 「薄弱点学习」里 AI 手写的练习题 → 题库卡片（2026-09-22）
 *
 * 为什么单独成模块：这一段的规则最脏（模型给什么格式的都有），也最容易回归，
 * 所以要能单独跑用例（见 tools/test_study_cards.js）。serve.js 只负责 HTTP 与库连接。
 *
 * 职责边界：
 *   · parseCardsArray / normalizeCards / splitCards —— 纯函数，不碰文件与数据库；
 *   · insertCards —— 只认「传进来的 db」，卡号生成与去重在这里，SQL 也在这里，
 *     但 topic 匹配由调用方注入（serve.js 用学习区那套检索口径）。
 *
 * 为什么非规范化不可：模型给的 answer 可能是字母 A、可能是下标 0，判断题可能写
 * true/false/√/对 —— 直接入库就会出现「选了正确答案却判错」那一类脏数据，
 * 而且这种错在界面上看着完全正常，只有学生点下去才知道，属于最贵的一种 bug。
 */

const crypto = require('crypto');

/** 题目数量的硬上限：一轮回复里不该出现更多，多了就是模型跑偏。 */
const MAX_CARDS = 8;

/** 抽掉 ```cards 块：返回 { text: 去掉块的正文, cards: 规范化的题 }。
 *  ⚠️ 块**解析不出 JSON 数组**时原样返回正文——宁可让学生看到一段难看的
 *     JSON，也不能把正文里真正的题目一起删掉。 */
function splitCards(text) {
  const s = String(text == null ? '' : text);
  const m = /```cards[^\n]*\n?([\s\S]*?)```/i.exec(s);
  if (!m) return { text: s, cards: [] };
  const arr = parseCardsArray(m[1]);
  if (!arr) return { text: s, cards: [] };
  // 连着前面的空行一起去掉，别把一大段 JSON 摊在学生眼前
  const cleaned = (s.slice(0, m.index) + s.slice(m.index + m[0].length))
    .replace(/\n{3,}/g, '\n\n').trim();
  return { text: cleaned, cards: normalizeCards(arr) };
}

/** 从一段文本里抠出 JSON **数组**。
 *  ⚠️ grade_llm.extractJson 只认对象（它找第一个 `{`），这里顶层是数组，
 *     用它会只取回第一道题——所以自己配一个括号配平扫描（字符串里的括号不算）。 */
function parseCardsArray(raw) {
  const s = String(raw == null ? '' : raw);
  const start = s.indexOf('[');
  if (start < 0) return null;
  let depth = 0, inStr = false, esc = false;
  for (let i = start; i < s.length; i++) {
    const ch = s[i];
    if (inStr) {
      if (esc) esc = false;
      else if (ch === '\\') esc = true;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') inStr = true;
    else if (ch === '[' || ch === '{') depth++;
    else if (ch === ']' || ch === '}') {
      depth--;
      if (depth === 0) {
        try {
          const v = JSON.parse(s.slice(start, i + 1));
          return Array.isArray(v) ? v : null;
        } catch (e) { return null; }
      }
    }
  }
  return null;
}

/** 把模型给的题收敛成 { type, stem, options?, answer, explanation, traps, topic }。
 *  过不去的直接丢：宁可少一张，也不要一张永远判错的卡。 */
function normalizeCards(arr) {
  const out = [];
  if (!Array.isArray(arr)) return out;
  for (const c of arr) {
    if (!c || typeof c !== 'object') continue;
    if (out.length >= MAX_CARDS) break;
    let type = String(c.type || 'choice').toLowerCase().trim();
    if (type === 'single' || type === 'mcq' || type === 'select') type = 'choice';
    if (type === 'truefalse' || type === 'tf') type = 'judge';
    if (['choice', 'judge', 'fill'].indexOf(type) < 0) type = 'choice';
    const stem = String(c.stem || c.question || c.title || '').trim().slice(0, 1000);
    if (!stem) continue;
    const traps = (Array.isArray(c.traps) ? c.traps : [])
      .map(t => String(t == null ? '' : t).trim()).filter(Boolean).slice(0, 4);
    const explanation = String(c.explanation || c.analysis || '').trim().slice(0, 2000);
    const topic = String(c.topic || c.topic_name || '').trim().slice(0, 60);
    let answer = c.answer;
    if (type === 'choice') {
      // 选项：去掉模型爱加的 "A. " / "A、" 前缀——渲染层自己会加字母徽章，
      // 不去掉就成了「A. A. 甲」这种双份字母。
      const opts = (Array.isArray(c.options) ? c.options : [])
        .map(o => String(o == null ? '' : o).trim().replace(/^[A-Da-d]\s*[.、．)）:：]\s*/, ''))
        .filter(Boolean);
      if (opts.length !== 4) continue;
      if (typeof answer === 'string') {
        const a = answer.trim();
        if (/^[A-Da-d]$/.test(a)) answer = a.toUpperCase().charCodeAt(0) - 65;
        else { const i = opts.indexOf(a); if (i >= 0) answer = i; }
      }
      answer = Number(answer);
      if (!Number.isInteger(answer) || answer < 0 || answer > 3) continue;
      out.push({ type: type, stem: stem, options: opts, answer: answer,
                 explanation: explanation, traps: traps, topic: topic });
      continue;
    }
    if (type === 'judge') {
      const a = String(answer == null ? '' : answer).trim();
      // 判断题在库里 answer 就是「正确/错误」两个字（correctIndex 认它们）
      const yes = /^(true|t|yes|y|正确|对|√|是|1)$/i.test(a);
      const no = /^(false|f|no|n|错误|错|×|x|否|0)$/i.test(a);
      if (!yes && !no) continue;
      out.push({ type: type, stem: stem, answer: yes ? '正确' : '错误',
                 explanation: explanation, traps: traps, topic: topic });
      continue;
    }
    const fill = String(answer == null ? '' : answer).trim().slice(0, 500);
    if (!fill) continue;
    out.push({ type: 'fill', stem: stem, answer: fill,
               explanation: explanation, traps: traps, topic: topic });
  }
  return out;
}

const randTail = () => crypto.randomBytes(4).toString('hex').toUpperCase();

/**
 * 把规范化后的题写进 questions + cards，返回卡号（前端拿它精确组题）。
 *
 * @param db       已打开的 node:sqlite DatabaseSync（可写）
 * @param cards    normalizeCards 的输出
 * @param opts     { subject, today, findTopic(db, subject, topicName, stem) → topic_id }
 * @returns        { inserted, reused, card_ids }
 *
 * 去重：同题干已经入过库就**复用那张卡**。同一个知识点反复问很常见，复用而不是
 * 插第二张，才不会出现「同一道题一天被问两遍」（这是用户明确抱怨过的问题）。
 */
function insertCards(db, cards, opts) {
  const o = opts || {};
  const today = o.today || new Date().toISOString().slice(0, 10);
  const subject = String(o.subject || '');
  const findTopic = typeof o.findTopic === 'function' ? o.findTopic : () => '';
  const res = { inserted: 0, reused: 0, card_ids: [] };
  if (!Array.isArray(cards) || !cards.length) return res;
  const source = 'AI手写-' + today;
  for (const c of cards) {
    const stem = String(c.stem || '').trim();
    // 用 instr 而不是 LIKE：题干里的 % 和 _ 在 LIKE 里是通配符，会误判成重复。
    let dup = null;
    try {
      dup = db.prepare(
        'SELECT q.id AS qid, c.id AS cid FROM questions q '
        + 'LEFT JOIN cards c ON c.question_id = q.id '
        + 'WHERE instr(q.content, ?) > 0 ORDER BY q.created_at DESC LIMIT 1'
      ).get(stem.slice(0, 60));
    } catch (e) { dup = null; }
    if (dup && dup.cid) { res.reused += 1; res.card_ids.push(dup.cid); continue; }
    if (dup && dup.qid) {   // 有题无卡（历史脏数据）：补一张卡，不再插一道新题
      const cid = 'C-STUDY-' + randTail();
      db.prepare('INSERT INTO cards (id, question_id, state, due_date) VALUES (?, ?, 0, ?)')
        .run(cid, dup.qid, today);
      res.inserted += 1; res.card_ids.push(cid);
      continue;
    }
    const content = { stem: stem, explanation: c.explanation || '', traps: c.traps || [] };
    if (c.type === 'choice') { content.options = c.options; content.answer = c.answer; }
    else content.answer = String(c.answer);
    let tid = '';
    try { tid = findTopic(db, subject, c.topic, stem) || ''; } catch (e) { tid = ''; }
    const qid = 'Q-STUDY-' + randTail();
    const cid = 'C-STUDY-' + randTail();
    db.prepare('INSERT INTO questions (id, topic_id, type, difficulty, source, content) '
      + 'VALUES (?, ?, ?, 0.5, ?, ?)')
      .run(qid, tid || null, c.type, source, JSON.stringify(content));
    db.prepare('INSERT INTO cards (id, question_id, state, due_date) VALUES (?, ?, 0, ?)')
      .run(cid, qid, today);
    res.inserted += 1; res.card_ids.push(cid);
  }
  return res;
}

module.exports = {
  MAX_CARDS,
  splitCards,
  parseCardsArray,
  normalizeCards,
  insertCards,
};

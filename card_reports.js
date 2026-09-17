/**
 * card_reports.js —— 「这道题有问题」标记与复核（2026-09-17）
 *
 * 为什么要有它：闪卡里有一类错**不是学生记错**，是题目本身错了。用户实测到的典型
 * 是选择题有两个正确选项（叠加原理那道：y₁*+y₂* 是 f₁+f₂ 的解、y₁*−y₂* 是 f₁−f₂ 的解，
 * 按真值 A、C 都对），学生照着正确答案点反而被判错——越练越糊涂，还把 FSRS 的
 * 难度/稳定度带偏，属于「最贵的一种脏数据」。
 *
 * 做法：练习时一键标记（带原因 + 可选一句话）→ 每日任务里由 agent 逐张核对并修好
 * （改选项/答案/题干/解析，或删卡），修完把结论回写。前端只在标记还 open 时画徽标。
 *
 * 为什么单独成模块（与 study_cards.js 同一个理由）：
 *   ① serve.js 的端点只管 HTTP 与库连接，规则在这儿能单独跑用例（tools/test_card_reports.js）；
 *   ② 复核这一步要能从命令行跑（每日任务里 agent 不一定要开着网页）——
 *      `node src/card_reports.js list` / `fix` / `dismiss` 就是给那条链路用的。
 *
 * 边界：所有函数都只认**传进来的 db**，模块自己不决定路径（CLI 的 openDb 除外，
 *      它认 DB_PATH 环境变量，用来在副本库上验证）。
 */

const path = require('path');
const { DatabaseSync } = require('node:sqlite');

/** 标记原因。id 落库，中文标签只在这里定义（Python 侧 daily_planner.py 有一份镜像，
 *  改一边要改另一边——跨语言没法共享常量，和 due_date/due_at 的处理一样）。 */
const KINDS = [
  { id: 'multi_correct', label: '多个选项都对',
    hint: '不止一个正确项（如 A、C 都对）→ 把多余的正确项改成真干扰项，别改成多选题' },
  { id: 'answer_wrong', label: '答案有误',
    hint: '标出的答案不对 → 亲自验算/查证后改正 answer' },
  { id: 'stem_wrong', label: '题干有误',
    hint: '条件、符号、下标写错 → 改 stem（选项可能也要跟着改）' },
  { id: 'unclear', label: '表述不清',
    hint: '看不懂在问什么、或问法有歧义 → 重写题干，把问的东西写明确' },
  { id: 'dup', label: '与别的卡重复',
    hint: '同一考点换个说法又问了一遍 → 留信息更全的那张，删掉这张' },
  { id: 'other', label: '其它',
    hint: '看 note' },
];
const KIND_IDS = KINDS.map(k => k.id);
const KIND_MAP = KINDS.reduce((m, k) => { m[k.id] = k; return m; }, {});

/** 用户自己那句话的上限。留痕是给人看的，太长没有意义。 */
const MAX_NOTE = 500;
/** 一次列出的上限（正常库里不会攒到这么多，纯防御）。 */
const MAX_LIST = 200;
/** 单条标记被复核前的默认排序权重不需要，但列表默认按时间倒序。 */

/** 允许被 patch 的字段——白名单，别的一律拒。写得下才敢让模型来改题。 */
const PATCH_FIELDS = ['stem', 'options', 'answer', 'explanation', 'traps', 'reference_answer'];

function kindLabel(kind) {
  const k = KIND_MAP[String(kind || '')];
  return k ? k.label : '其它';
}

function kindHint(kind) {
  const k = KIND_MAP[String(kind || '')];
  return k ? k.hint : '';
}

function normalizeKind(raw) {
  // 大小写/空格都容错：链路另一头是模型（agent 复核时可能写 "Answer_Wrong"），
  // 认不出就静默落成 other，那等于把用户的标记降级成一个笼统的"其它"。
  const k = String(raw == null ? '' : raw).trim().toLowerCase();
  return KIND_IDS.indexOf(k) >= 0 ? k : 'other';
}

function clip(s, n) {
  const t = String(s == null ? '' : s);
  return t.length > n ? t.slice(0, n) : t;
}

function nowLocal() {
  // 与库里的 datetime('now','localtime') 同一口径（'YYYY-MM-DD HH:MM:SS'）
  const d = new Date();
  const p = (x) => String(x).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
    + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}

function parseContent(raw) {
  try {
    const o = JSON.parse(raw);
    return (o && typeof o === 'object') ? o : { stem: String(raw || '') };
  } catch (e) {
    return { stem: String(raw == null ? '' : raw) };
  }
}

/** 取一张卡（含题目类型与内容），找不到回 null。 */
function cardOf(db, cardId) {
  const id = String(cardId == null ? '' : cardId).trim();
  if (!id) return null;
  const row = db.prepare(
    'SELECT c.id AS card_id, c.question_id, c.state, c.lapses, c.due_date,'
    + ' COALESCE(c.suspended, 0) AS suspended,'
    + ' q.type, q.topic_id, q.content AS raw,'
    + ' t.name AS topic_name, COALESCE(t.subject, \'\') AS subject'
    + ' FROM cards c LEFT JOIN questions q ON q.id = c.question_id'
    + ' LEFT JOIN topics t ON t.id = q.topic_id WHERE c.id = ?'
  ).get(id);
  if (!row) return null;
  row.content = parseContent(row.raw);
  return row;
}

/**
 * 打标记（幂等）：同一张卡再来一次是**改内容**，不会插第二行
 * （部分唯一索引 idx_card_reports_open 也是这么定的）。
 *
 * @param db    已打开的 node:sqlite DatabaseSync（可写）
 * @param opts  { card_id, kind, note, chosen, correct, source, now }
 * @returns     { ok, id, created, card_id, kind, count, error? }
 */
function addReport(db, opts) {
  const o = opts || {};
  const cardId = String(o.card_id == null ? '' : o.card_id).trim();
  if (!cardId) return { ok: false, error: '需要 card_id' };
  const card = cardOf(db, cardId);
  if (!card) return { ok: false, error: '卡片不存在：' + cardId };

  const kind = normalizeKind(o.kind);
  const note = clip(String(o.note == null ? '' : o.note).trim(), MAX_NOTE);
  const chosen = o.chosen == null ? null : clip(String(o.chosen).trim(), 120) || null;
  const correct = o.correct == null ? null : clip(String(o.correct).trim(), 120) || null;
  const source = clip(String(o.source || 'user').trim(), 20) || 'user';
  const now = o.now || nowLocal();
  // 快照存**原始 JSON 文本**：以后改题时能一眼看出「报卡时是什么样、现在是什么样」，
  // 也能认出「这条标记是不是已经过期了」（见 listReports 的 stale 字段）。
  const snapshot = card.raw == null ? null : String(card.raw);

  const open = db.prepare(
    "SELECT id, kind, note FROM card_reports WHERE card_id = ? AND status = 'open' LIMIT 1"
  ).get(cardId);

  let id, created;
  if (open) {
    id = open.id; created = false;
    // 重复标记 = 改原因/补说明，**不是**把已有的现场抹掉：
    //   · chosen / correct 用 COALESCE 保住旧值 —— 换个入口再标一次时可能没带这两项，
    //     直接覆盖会把「他当时选了哪项、标答是什么」弄丢，而那正是复核时要看的东西；
    //   · snapshot 故意**不动** —— 它记的是「第一次报卡时的题目」，这样 stale 判断
    //     （报卡后题目被改过）一直成立。真要新的基线，等这条修完再标记就是新的一行。
    db.prepare(
      'UPDATE card_reports SET kind = ?, note = ?,'
      + ' chosen = COALESCE(?, chosen), correct = COALESCE(?, correct),'
      + ' source = ?, question_id = ?, updated_at = ? WHERE id = ?'
    ).run(kind, note || null, chosen, correct, source, card.question_id, now, id);
  } else {
    const r = db.prepare(
      'INSERT INTO card_reports (card_id, question_id, kind, note, chosen, correct, snapshot,'
      + ' status, source, created_at, updated_at)'
      + " VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)"
    ).run(cardId, card.question_id, kind, note || null, chosen, correct, snapshot, source, now, now);
    id = Number(r.lastInsertRowid); created = true;
  }
  // 被删掉的卡又被标记（或标记后又删）时把卡放回来：用户的意思很明确——这题他要处理，
  // 不是要它消失。恢复后它照常参与组题，只是带着待修徽标。
  if (card.suspended) {
    try { db.prepare('UPDATE cards SET suspended = 0 WHERE id = ?').run(cardId); } catch (e) {}
  }
  return {
    ok: true, id: id, created: created, card_id: cardId, kind: kind,
    count: countOpen(db),
  };
}

/**
 * 列出标记（默认只看待修）。
 *
 * @param db   只读也行的 DatabaseSync
 * @param opts { status='open'|'fixed'|'dismissed'|'deleted'|'all', subject, card_id, limit }
 * @returns    数组，每条带上「现在库里长什么样」与「报卡时是什么样」（stale 对比用）
 */
function listReports(db, opts) {
  const o = opts || {};
  const status = String(o.status || 'open');
  const where = [], bind = [];
  if (status && status !== 'all') { where.push('r.status = ?'); bind.push(status); }
  if (o.subject) { where.push("COALESCE(t.subject, '') = ?"); bind.push(String(o.subject)); }
  if (o.card_id) { where.push('r.card_id = ?'); bind.push(String(o.card_id)); }
  const limit = Math.max(1, Math.min(MAX_LIST, parseInt(o.limit, 10) || MAX_LIST));

  const rows = db.prepare(
    'SELECT r.id, r.card_id, r.question_id, r.kind, r.note, r.chosen, r.correct, r.snapshot,'
    + ' r.status, r.source, r.fix_note, r.fixed_at, r.created_at, r.updated_at,'
    + ' COALESCE(c.suspended, 0) AS suspended, c.state AS card_state, c.lapses, c.due_date,'
    + ' q.type, q.topic_id, q.content AS raw,'
    + ' t.name AS topic_name, COALESCE(t.subject, \'\') AS subject'
    + ' FROM card_reports r'
    + ' LEFT JOIN cards c ON c.id = r.card_id'
    + ' LEFT JOIN questions q ON q.id = COALESCE(r.question_id, c.question_id)'
    + ' LEFT JOIN topics t ON t.id = q.topic_id'
    + (where.length ? ' WHERE ' + where.join(' AND ') : '')
    + ' ORDER BY r.id DESC LIMIT ?'
  ).all(...bind, limit);

  return rows.map(r => {
    const content = r.raw == null ? null : parseContent(r.raw);
    const snapshot = r.snapshot == null ? null : parseContent(r.snapshot);
    const out = {
      id: r.id, card_id: r.card_id, question_id: r.question_id,
      kind: r.kind, kind_label: kindLabel(r.kind), kind_hint: kindHint(r.kind),
      note: r.note || '', chosen: r.chosen || '', correct: r.correct || '',
      status: r.status, source: r.source, fix_note: r.fix_note || '', fixed_at: r.fixed_at || '',
      created_at: r.created_at, updated_at: r.updated_at || '',
      subject: r.subject || '', topic_id: r.topic_id || '', topic_name: r.topic_name || '',
      type: r.type || '', due_date: r.due_date || '', card_state: r.card_state,
      lapses: r.lapses || 0, suspended: r.suspended || 0,
      content: content, snapshot: snapshot,
      // 报卡之后题目被改过 → 这条标记可能已经过期（复核前先看它）
      stale: !!(r.snapshot && r.raw != null && String(r.snapshot) !== String(r.raw)),
    };
    out.stem = content ? String(content.stem || '') : '';
    return out;
  });
}

function getReport(db, id) {
  const n = parseInt(id, 10);
  if (!Number.isInteger(n)) return null;
  const rows = listReports(db, { status: 'all', limit: MAX_LIST });
  return rows.filter(r => r.id === n)[0] || null;
}

/** 待修数（可限定科目）。前端徽标/每日任务都用它。 */
function countOpen(db, subject) {
  try {
    if (subject) {
      return db.prepare(
        "SELECT COUNT(*) AS n FROM card_reports r LEFT JOIN questions q ON q.id = r.question_id"
        + " LEFT JOIN topics t ON t.id = q.topic_id"
        + " WHERE r.status = 'open' AND COALESCE(t.subject, '') = ?"
      ).get(String(subject)).n;
    }
    return db.prepare("SELECT COUNT(*) AS n FROM card_reports WHERE status = 'open'").get().n;
  } catch (e) { return 0; }
}

// ---------------------------------------------------------------------------
// 改题（复核的核心）：白名单字段 + 校验，宁可拒改也不能把题改坏
// ---------------------------------------------------------------------------

/** 去「A. 」这类字母前缀（渲染层自己画 A/B/C/D 徽章，留着会显示成「Ⓐ A. 甲」）。 */
function stripLetterPrefix(s) {
  return String(s == null ? '' : s).trim().replace(/^[A-Da-d]\s*[.、．)）:：]\s*/, '');
}

/** 只去装饰性标点做重复判定：数学里有意义的符号一个都不能去
 *  （"-"去掉会让 1 与 -1 判成重复；"()"去掉会把 (eˣ+C)/x 与 eˣ + C/x 判成同一个）。
 *  与 tools/card_quality.py 的 PUNCT_RE 同口径。 */
function dedupKey(s) {
  return String(s == null ? '' : s).replace(/[\s，。、；：“”"'’‘—]/g, '');
}

/**
 * 校验并归一化一条 patch（部分更新）。
 *
 * @param patch  { stem?, options?, answer?, explanation?, traps?, reference_answer? }
 * @param base   题目现有 content（对象）
 * @param type   题目类型 choice | judge | fill | short
 * @returns      { ok, content?, changes?, warnings?, error? }
 */
function validatePatch(patch, base, type) {
  const p = (patch && typeof patch === 'object') ? patch : null;
  if (!p) return { ok: false, error: 'patch 必须是对象' };
  const keys = Object.keys(p);
  if (!keys.length) return { ok: false, error: 'patch 是空的' };
  const bad = keys.filter(k => PATCH_FIELDS.indexOf(k) < 0);
  if (bad.length) {
    return { ok: false, error: '不认识的字段：' + bad.join(', ')
      + '（只允许 ' + PATCH_FIELDS.join(' / ') + '）' };
  }
  const t = String(type || 'choice');
  const next = Object.assign({}, base || {});
  const changed = [];
  const warnings = [];

  if ('stem' in p) {
    const stem = String(p.stem == null ? '' : p.stem).trim();
    if (!stem) return { ok: false, error: 'stem 不能为空' };
    if (stem.length > 1000) return { ok: false, error: 'stem 过长（>1000 字）' };
    next.stem = stem; changed.push('stem');
  }
  if ('explanation' in p) {
    const ex = String(p.explanation == null ? '' : p.explanation).trim();
    if (ex.length > 2000) return { ok: false, error: 'explanation 过长（>2000 字）' };
    next.explanation = ex; changed.push('explanation');
  }
  if ('traps' in p) {
    const traps = (Array.isArray(p.traps) ? p.traps : [])
      .map(x => String(x == null ? '' : x).trim()).filter(Boolean).slice(0, 4);
    next.traps = traps; changed.push('traps');
  }
  if ('reference_answer' in p) {
    const ra = String(p.reference_answer == null ? '' : p.reference_answer).trim().slice(0, 500);
    next.reference_answer = ra; changed.push('reference_answer');
  }

  if ('options' in p) {
    if (t !== 'choice' && t !== 'judge') {
      return { ok: false, error: t + ' 题没有选项，不能改 options' };
    }
    const opts = (Array.isArray(p.options) ? p.options : [])
      .map(stripLetterPrefix).filter(x => x !== '');
    if (opts.length !== 4) {
      return { ok: false, error: '选项必须恰好 4 条非空（收到 ' + opts.length + ' 条）' };
    }
    const seen = {};
    for (const op of opts) {
      const k = dedupKey(op);
      if (seen[k]) return { ok: false, error: '选项重复：' + clip(op, 24) };
      seen[k] = 1;
    }
    next.options = opts; changed.push('options');

    // 选项换了但没给 answer：位置可能已经对不上了，只提醒不改值——
    // 复核的模型自己决定要不要一并给 answer。
    if (!('answer' in p) && typeof base.answer === 'number') {
      warnings.push('只改了选项、没改 answer：正确答案仍指向第 '
        + String.fromCharCode(65 + base.answer) + ' 项，请确认它还成立');
    }
  }

  if ('answer' in p) {
    if (t === 'choice') {
      const opts = next.options || [];
      let a = p.answer;
      if (typeof a === 'string') {
        const s = a.trim();
        if (/^[A-Da-d]$/.test(s)) a = s.toUpperCase().charCodeAt(0) - 65;
        else if (/^\d$/.test(s)) a = parseInt(s, 10);
        else { const i = opts.indexOf(s); if (i >= 0) a = i; }
      }
      a = Number(a);
      if (!Number.isInteger(a) || a < 0 || a > 3) {
        return { ok: false, error: 'answer 对不上任何选项：' + JSON.stringify(p.answer) };
      }
      next.answer = a; changed.push('answer');
    } else if (t === 'judge') {
      const s = String(p.answer == null ? '' : p.answer).trim();
      const yes = /^(true|t|yes|y|正确|对|√|是|1)$/i.test(s);
      const no = /^(false|f|no|n|错误|错|×|x|否|0)$/i.test(s);
      if (!yes && !no) return { ok: false, error: '判断题 answer 只能是「正确」或「错误」' };
      next.answer = yes ? '正确' : '错误'; changed.push('answer');
    } else {
      const s = String(p.answer == null ? '' : p.answer).trim().slice(0, 500);
      if (!s) return { ok: false, error: 'answer 不能为空' };
      next.answer = s; changed.push('answer');
    }
  }

  if (!changed.length) return { ok: false, error: 'patch 里没有任何可改的字段' };
  return { ok: true, content: next, changes: changed, warnings: warnings };
}

/**
 * 复核一条标记。
 *
 * @param db   可写 DatabaseSync
 * @param id   标记 id
 * @param opts {
 *   action: 'fixed' | 'dismissed' | 'deleted',
 *   note,                   复核结论（fixed 记「改了什么」，dismissed 记「为什么不是问题」）
 *   patch,                  改题内容（只有 fixed 用；见 validatePatch 的白名单）
 *   dry_run: true,          只算不写（给 agent 自查；CLI --dry）
 *   now,
 * }
 * @returns { ok, id, status, card_id, changes?, content?, warnings?, error? }
 */
function resolveReport(db, id, opts) {
  const o = opts || {};
  const n = parseInt(id, 10);
  if (!Number.isInteger(n)) return { ok: false, error: '需要标记 id（数字）' };
  const row = db.prepare('SELECT * FROM card_reports WHERE id = ?').get(n);
  if (!row) return { ok: false, error: '标记不存在：# ' + n };
  if (row.status !== 'open') {
    return { ok: false, error: '这条标记已经是 ' + row.status + ' 状态了（# ' + n + '）' };
  }
  const action = String(o.action || 'fixed');
  if (['fixed', 'dismissed', 'deleted'].indexOf(action) < 0) {
    return { ok: false, error: 'action 只能是 fixed / dismissed / deleted' };
  }
  const note = clip(String(o.note == null ? '' : o.note).trim(), 1000);
  if (action !== 'fixed' && !note) {
    return { ok: false, error: '驳回或删卡必须写清理由（note）——这是留给以后看的唯一线索' };
  }

  const card = cardOf(db, row.card_id);
  if (!card) return { ok: false, error: '卡片不存在：' + row.card_id };

  let merged = null, changes = [], warnings = [];
  if (action === 'fixed' && o.patch) {
    const v = validatePatch(o.patch, card.content, card.type);
    if (!v.ok) return { ok: false, error: '改题被拒：' + v.error };
    merged = v.content; changes = v.changes; warnings = v.warnings || [];
  }
  if (action === 'fixed' && !note && !changes.length) {
    return { ok: false, error: 'fixed 要么给 patch（改题），要么给 note（说明核对结论）' };
  }

  const status = action === 'dismissed' ? 'dismissed' : (action === 'deleted' ? 'deleted' : 'fixed');
  if (o.dry_run) {
    return { ok: true, dry_run: true, id: n, action: action, status: status,
             card_id: row.card_id, changes: changes, content: merged, warnings: warnings };
  }

  const now = o.now || nowLocal();
  if (merged) {
    db.prepare('UPDATE questions SET content = ? WHERE id = ?')
      .run(JSON.stringify(merged), card.question_id);
  }
  if (action === 'deleted') {
    // 与🗑删卡同一个动作（软删）：review_log 关联不断，能在筛选页「已暂停」里找回来。
    db.prepare('UPDATE cards SET suspended = 1 WHERE id = ?').run(row.card_id);
  }
  db.prepare('UPDATE card_reports SET status = ?, fix_note = ?, fixed_at = ?, updated_at = ?'
    + ' WHERE id = ?').run(status, note || null, now, now, n);

  return { ok: true, id: n, action: action, status: status, card_id: row.card_id,
           changes: changes, content: merged, warnings: warnings, count: countOpen(db) };
}

// ---------------------------------------------------------------------------
// 展示 / CLI
// ---------------------------------------------------------------------------

/** 一行简报：`#12 [多个选项都对] C-STUDY-0BD… 数学一·微分方程 已知 y₁* 是…` */
function brief(r) {
  const parts = ['#' + r.id, '[' + kindLabel(r.kind) + ']', r.card_id];
  const where = [r.subject, r.topic_name].filter(Boolean).join('·');
  if (where) parts.push(where);
  if (r.stale && r.status === 'open') parts.push('(报卡后题目被改过，先看对比)');
  return parts.join(' ') + '\n     ' + clip(String(r.stem || '').replace(/\s+/g, ' '), 110)
    + (r.note ? '\n     他说：' + clip(r.note, 80) : '');
}

/** CLI 用：把一条标记摊开成人看得懂的文本（含改前/改后对照）。 */
function formatReport(r) {
  const L = [];
  L.push('===== #' + r.id + '  ' + kindLabel(r.kind) + '  [' + r.status + '] =====');
  L.push('卡号：' + r.card_id + '  题目：' + (r.question_id || '-') + '  类型：' + (r.type || '-'));
  L.push('考点：' + ([r.subject, r.topic_name].filter(Boolean).join(' · ') || '-')
    + '   到期：' + (r.due_date || '-') + '   报卡：' + (r.created_at || '-')
    + '   来源：' + (r.source || '-'));
  if (r.note) L.push('他说：' + r.note);
  if (r.chosen || r.correct) L.push('他选的：' + (r.chosen || '-') + '   当时的答案：' + (r.correct || '-'));
  const c = r.content || {};
  L.push('题干：' + String(c.stem || ''));
  const opts = Array.isArray(c.options) ? c.options : [];
  opts.forEach((op, i) => L.push('  ' + String.fromCharCode(65 + i) + '. ' + op
    + (typeof c.answer === 'number' && c.answer === i ? '   ← 标答' : '')));
  if (typeof c.answer !== 'number') L.push('答案：' + JSON.stringify(c.answer));
  if (c.explanation) L.push('解析：' + String(c.explanation));
  if (Array.isArray(c.traps) && c.traps.length) L.push('易错点：' + c.traps.join('；'));
  if (r.stale) {
    // 已复核的标记也常被翻出来看「到底改了什么」——那就不该再用「⚠ 题目被动过」这种口吻
    L.push(r.status === 'fixed'
      ? '改动对照（报卡时 → 现在）：'
      : '⚠ 报卡之后题目被改过，下面是对比（旧 → 新）：');
    const s = r.snapshot || {};
    if (String(s.stem || '') !== String(c.stem || '')) {
      L.push('  旧题干：' + String(s.stem || ''));
      L.push('  新题干：' + String(c.stem || ''));
    }
    const so = Array.isArray(s.options) ? s.options : [];
    so.forEach((op, i) => {
      if (String(op) !== String(opts[i])) L.push('  ' + String.fromCharCode(65 + i) + '：'
        + op + '  →  ' + (opts[i] == null ? '(删了)' : opts[i]));
    });
    if (s.answer !== c.answer) L.push('  标答：' + JSON.stringify(s.answer) + ' → ' + JSON.stringify(c.answer));
  }
  if (r.fix_note) L.push('复核结论：' + r.fix_note + '（' + (r.fixed_at || '') + '）');
  return L.join('\n');
}

/** CLI 打开库：认 DB_PATH（相对 src/ 解析），并设 busy_timeout —— 服务可能正开着同样的库。 */
function openDb(dbPath) {
  const p = dbPath ? path.resolve(__dirname, dbPath)
    : (process.env.DB_PATH ? path.resolve(__dirname, process.env.DB_PATH)
      : path.join(__dirname, 'question_bank.db'));
  const db = new DatabaseSync(p);
  try { db.exec('PRAGMA busy_timeout = 8000'); } catch (e) { /* 老版本忽略 */ }
  return db;
}

module.exports = {
  KINDS, KIND_IDS, MAX_NOTE, MAX_LIST, PATCH_FIELDS,
  kindLabel, kindHint, normalizeKind,
  parseContent, cardOf, stripLetterPrefix,
  addReport, listReports, getReport, countOpen,
  validatePatch, resolveReport,
  brief, formatReport, openDb,
};

// ---------------------------------------------------------------------------
// CLI —— 每日任务里 agent 就是靠这几条命令干活（不用开着网页、不用 HTTP）
// ---------------------------------------------------------------------------
if (require.main === module) {
  const argv = process.argv.slice(2);
  const cmd = argv[0] || 'list';
  const flag = (name) => {
    const i = argv.indexOf('--' + name);
    return i >= 0 ? (argv[i + 1] == null ? '' : argv[i + 1]) : '';
  };
  const has = (name) => argv.indexOf('--' + name) >= 0;

  let db;
  try {
    db = openDb(process.env.DB_PATH || '');
  } catch (e) {
    console.log('打不开数据库：' + e.message);
    process.exit(2);
  }

  const needId = () => {
    const id = parseInt(argv[1], 10);
    if (!Number.isInteger(id)) { console.log('需要标记 id：card_reports.js ' + cmd + ' <id>'); process.exit(2); }
    return id;
  };

  try {
    if (cmd === 'list' || cmd === 'count') {
      const status = flag('status') || 'open';
      const rows = listReports(db, { status: status, subject: flag('subject'), limit: 200 });
      if (cmd === 'count') {
        console.log(String(rows.length));
      } else if (has('json')) {
        console.log(JSON.stringify(rows, null, 2));
      } else if (!rows.length) {
        console.log(status === 'open' ? '没有待修的问题卡 ✓' : '没有 ' + status + ' 状态的标记');
      } else {
        console.log('共 ' + rows.length + ' 条（status=' + status + '）：\n');
        rows.forEach(r => console.log(brief(r) + '\n'));
      }
    } else if (cmd === 'show') {
      const id = needId();
      const r = getReport(db, id);
      if (!r) { console.log('没有这条标记：# ' + id); process.exit(1); }
      console.log(has('json') ? JSON.stringify(r, null, 2) : formatReport(r));
    } else if (cmd === 'report') {
      // 从命令行报卡：供 agent 自己发现的问题（人工审计、或用户在对话框里指出的）
      // 也走同一条链路——标记不是只有网页那一个入口。
      const cardId = argv[1];
      if (!cardId) { console.log('需要卡号：card_reports.js report <card_id> [--kind …] [--note …]'); process.exit(2); }
      const r = addReport(db, {
        card_id: cardId, kind: flag('kind') || 'other', note: flag('note'),
        chosen: flag('chosen'), correct: flag('correct'),
        source: flag('source') || 'agent',
      });
      console.log(JSON.stringify(r, null, 2));
      if (!r.ok) process.exit(1);
    } else if (cmd === 'fix' || cmd === 'dismiss' || cmd === 'deleted' || cmd === 'delete') {
      const id = needId();
      let patch = null;
      const patchFile = flag('patch-file');
      const patchText = flag('patch');
      if (patchFile) {
        patch = JSON.parse(require('fs').readFileSync(patchFile, 'utf-8'));
      } else if (patchText) {
        patch = JSON.parse(patchText);
      }
      const action = cmd === 'fix' ? 'fixed' : (cmd === 'dismiss' ? 'dismissed' : 'deleted');
      const r = resolveReport(db, id, {
        action: action, note: flag('note'), patch: patch,
        dry_run: has('dry'),
      });
      console.log(JSON.stringify(r, null, 2));
      if (!r.ok) process.exit(1);
    } else {
      console.log([
        'card_reports.js —— 闪卡「这道题有问题」标记的查看与复核',
        '',
        '用法：',
        '  node src/card_reports.js list [--status open|fixed|dismissed|all] [--subject 数学一] [--json]',
        '  node src/card_reports.js show <id> [--json]',
        '  node src/card_reports.js report <card_id> [--kind multi_correct] [--note "…"] [--chosen "…"] [--correct "…"]',
        '  node src/card_reports.js fix <id> --note "改了什么" [--patch \'{...}\' | --patch-file p.json] [--dry]',
        '  node src/card_reports.js dismiss <id> --note "为什么不是问题"',
        '  node src/card_reports.js delete <id> --note "为什么删"',
        '  node src/card_reports.js count [--status open]',
        '',
        'patch 只认这些字段：' + PATCH_FIELDS.join(' / '),
        '',
        '原因 id：' + KINDS.map(k => k.id + '(' + k.label + ')').join('  '),
      ].join('\n'));
    }
  } catch (e) {
    console.log('[FAIL] ' + e.message);
    process.exit(1);
  } finally {
    try { db.close(); } catch (e) {}
  }
}

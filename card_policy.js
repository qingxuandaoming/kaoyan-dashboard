/**
 * card_policy.js — 组题策略：没练过的优先、连对退役、追问过的保留、钉住（2026-09-19 用户要求）
 *
 * 用户原话：
 *   「这些闪卡有些我连对两次连对三次，其实就没必要再排了，因为可能那个闪卡对我来说就比较简单，
 *     没有意义。所以优先还是排我没有练过的卡。然后优先排新卡，新出的关于我最近的知识上的那些卡。
 *     然后就算我对的那些题，然后如果我对 AI 有过追问的话，那那个可以给我一个 pin 的一个键，
 *     我可以把它钉在那个卡的位置，下次我看到它的时候，我可以再看看它。像这种有过追问的可以
 *     就是出现 3 次出现 4 次，但是那就没有追问的，然后我连续对的，就可以降低优先级。」
 *
 * 落成四条规则（只作用于**智能组** mode=smart / extra；browse、ids 点名、筛选页都是他的手选，
 * 一律照给，绝不被这里过滤）：
 *
 *   R-pin    钉住的卡：永远进智能组、置顶，而且**不受连对退役影响**（他明确要「下次再看看它」）
 *   R-ask    对 AI 提过问的卡：不降权、不退役（那是他当场没想通的地方，允许反复出现）
 *            ⚠️ 但**必须有出口**（2026-09-19 用户指出的漏洞）：「你这样我问过的，那永远优先级都高了，
 *            但不应该是这样。如果后来我已经学会了、掌握了这个点，它应该有一定的退出机制。
 *            比如说我连续都对。」→ 见下面的 R-ask-exit。
 *   R-ask-exit 提问**之后**又连续答对 `askExitStreak`（默认 3）次 → 问过的身份作废，
 *            从此按普通卡走连对规则（该降权降权、该退役退役）。
 *            判据必须限定「提问之后」：提问之前的连对不能算（那时他还没搞懂）；
 *            提问之后只要又错过一次，计数清零（说明没掌握）。
 *   R-streak 连续答对、又没提过问的卡：连对 2 次降权，连对 ≥3 次**移出智能组**（筛选页仍可见）
 *   R-new    「没练过的卡」与「最近新出的、挂在他近日学的考点上的卡」抬到复习卡之前
 *
 * 唯一**永久**的例外是 📌 钉住（那是他显式说「我要一直看见它」，只有取消钉住才能退出）。
 *
 * 三条踩过的坑写在这里，改的时候别踩回去：
 *   ① ⚠️ explain_log 的 **card_id 列是空的**（46 行 user 记录里一个都没填），
 *      追问只能靠 **question_id** 关联；按 card_id 去找会得到「谁都没问过」。
 *   ② ⚠️ 「他打的字」不等于全部 user 行：服务端会自动写一条
 *      「我的选择：X」当首轮提示。判定口径是 `content LIKE '【我问】%' OR content NOT LIKE '我的选择：%'`
 *      —— 光认 `【我问】` 会漏掉在同一线索里追问的那些（例如「关中断之后不会阻塞吗？」）。
 *   ③ ⚠️ 连对要按 **id 倒序**取最近几条，不能按 review_date 字符串排序就完事
 *      （同一天可能答多次；id 才是真正的先后）。
 *
 * 这个模块是纯逻辑 + 只认传进来的 db，和 card_reports.js / study_cards.js 一个路子：
 * 服务端 require 它，测试也能直接喂一个副本库跑。
 */

const PIN_TABLE = 'card_pins';

// 默认阈值（可在 config 表里覆盖：smart_retire_streak / smart_demote_streak /
// smart_ask_exit_streak；0 = 关闭该档）
const DEFAULT_RETIRE_STREAK = 3;   // 连对达到几次 → 移出智能组
const DEFAULT_DEMOTE_STREAK = 2;   // 连对达到几次 → 降权（仍在智能组里，排在后面）
const DEFAULT_ASK_EXIT_STREAK = 3; // 提问之后又连对几次 → 「问过」身份作废（见 R-ask-exit）
const STREAK_WINDOW = 8;           // 只看最近这么多次作答（够判断了，不必扫全史）
const FRESH_NEW_DAYS = 14;         // 「最近新出的卡」= 这些天内入库的

// 优先级阶梯（数字越大越靠前）。放在一处，服务端排序与测试断言都读它。
const PRIO = {
  PINNED: 10,     // 📌 他钉住的
  ASKED: 9,       // 对 AI 提过问（他当场没想通）
  LEECH: 8,       // 水蛭卡：反复忘（练过很多次仍然错，必须继续排）
  FRESH_NEW: 7,   // 最近新出 + 挂在他近日学的考点上
  NEW: 6,         // 没练过的卡（state=0 / 从没作答过）
  WEAK: 5,        // 近 30 天错过 / lapses>0 / 大盘薄弱考点
  LEARNING: 4,    // 学习中且到期
  DUE: 3,         // 复习到期
  STREAK2: 2,     // 连对 2 次、没提过问 → 降权（还可能出现，但排在后面）
  REST: 1,
};

const PRIO_LABEL = {
  [PRIO.PINNED]: '钉住',
  [PRIO.ASKED]: '追问过',
  [PRIO.LEECH]: '反复忘',
  [PRIO.FRESH_NEW]: '新出的卡',
  [PRIO.NEW]: '没练过',
  [PRIO.WEAK]: '薄弱',
  [PRIO.LEARNING]: '学习中',
  [PRIO.DUE]: '到期复习',
  [PRIO.STREAK2]: '连对降权',
  [PRIO.REST]: '其他',
};

// ---------------------------------------------------------------------------
// 表
// ---------------------------------------------------------------------------

/**
 * 建卡钉表（幂等）。schema.sql / migrate.js 里有一份，这里是运行时兜底 ——
 * 老库（schema_version < 6）直接起服务也能用，不必等用户先跑迁移。
 */
function ensurePinTable(db) {
  db.exec(`CREATE TABLE IF NOT EXISTS ${PIN_TABLE} (
    card_id    TEXT PRIMARY KEY,
    note       TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT
  )`);
}

// ---------------------------------------------------------------------------
// 信号：连对 / 追问 / 钉住
// ---------------------------------------------------------------------------

function _num(v) { return parseInt(v, 10) || 0; }

/**
 * 一次把所有卡片的判据捞出来（避免每张卡三条子查询）。
 *
 * `recent` 保留每条记录的**时间**，因为「提问之后的连对」要按时间比较
 * （见 askExemptionHolds）——只留评分是判不出来的。
 *
 * @returns Map<card_id, {streak, recent:[{rating, date}], reviews, lastRating, lastReviewAt}>
 */
function loadReviewSignals(db) {
  const out = new Map();
  let rows = [];
  try {
    // 按 card_id 分组、组内按 id 倒序（近的在前）。只取最近 90 天，够判断连对。
    rows = db.prepare(`SELECT card_id, rating, review_date FROM review_log
                       WHERE review_date >= datetime('now','localtime','-90 days')
                       ORDER BY card_id, id DESC`).all();
  } catch (e) { rows = []; }

  const seen = new Map();
  for (const r of rows) {
    const cid = r.card_id;
    let sig = out.get(cid);
    if (!sig) {
      sig = { streak: 0, recent: [], reviews: 0, lastRating: 0, lastReviewAt: '' };
      out.set(cid, sig);
      seen.set(cid, 0);
    }
    sig.reviews += 1;
    if (!sig.lastReviewAt) { sig.lastReviewAt = r.review_date || ''; sig.lastRating = _num(r.rating); }
    const n = seen.get(cid);
    if (n < STREAK_WINDOW) {
      sig.recent.push({ rating: _num(r.rating), date: r.review_date || '' });
      seen.set(cid, n + 1);
    }
  }
  // 连对 = 从最近一次往回数，连续 rating>=3 的个数
  for (const sig of out.values()) sig.streak = _streak(sig.recent);
  return out;
}

/** recent（近→远）里从头开始连续答对的个数 */
function _streak(recent) {
  let s = 0;
  for (const r of recent || []) {
    if (_num(r.rating) >= 3) s += 1; else break;
  }
  return s;
}

/**
 * 他对哪道题「真正打过字提问」——按 question_id 聚合。
 *
 * ⚠️ 不能按 explain_log.card_id 找：那一列全是空。
 * ⚠️ 也不能只认 `【我问】` 前缀：同一线索里的后续追问是裸文本（没有前缀）。
 */
function loadAskSignals(db) {
  const out = new Map();   // question_id -> {asks, lastAsk}
  try {
    const rows = db.prepare(`
      SELECT question_id, COUNT(*) AS n, MAX(created_at) AS last_at
      FROM explain_log
      WHERE role = 'user'
        AND question_id IS NOT NULL AND question_id != ''
        AND (content LIKE '【我问】%' OR content NOT LIKE '我的选择：%')
      GROUP BY question_id
    `).all();
    for (const r of rows) out.set(r.question_id, { asks: _num(r.n), lastAsk: r.last_at || '' });
  } catch (e) { /* 老库没有 explain_log 时当没人问过 */ }
  return out;
}

function loadPinSignals(db) {
  const out = new Map();   // card_id -> {note, created_at}
  try {
    ensurePinTable(db);
    for (const r of db.prepare(`SELECT card_id, note, created_at FROM ${PIN_TABLE}`).all()) {
      out.set(r.card_id, { note: r.note || '', created_at: r.created_at || '' });
    }
  } catch (e) { /* 只读连接上建不了表：当作没有钉住 */ }
  return out;
}

/** 汇总成一张表：Map<card_id, signal>。三份信号合并，服务端组题时一次算完。 */
function loadSignals(db) {
  const rev = loadReviewSignals(db);
  const asks = loadAskSignals(db);
  const pins = loadPinSignals(db);
  const ids = new Set([...rev.keys(), ...pins.keys()]);
  const out = new Map();
  for (const cid of ids) {
    const r = rev.get(cid) || { streak: 0, reviews: 0, lastRating: 0, lastReviewAt: '', recent: [] };
    out.set(cid, {
      streak: r.streak, reviews: r.reviews,
      lastRating: r.lastRating, lastReviewAt: r.lastReviewAt,
      // ⚠️ `recent` 必须一起带过来：它是「提问之后的连对」的唯一依据
      // （按时间比），漏了它 afterAskCorrect 恒为 0 —— 于是「问过」又变回永久特权。
      // 这个漏字段是 E2E 抓出来的（单测手搓 sig 有 recent，服务端这条路径没有）。
      recent: r.recent || [],
      asks: 0, lastAsk: '',
      pinned: pins.has(cid), pinNote: pins.has(cid) ? pins.get(cid).note : '',
      pinAt: pins.has(cid) ? pins.get(cid).created_at : '',
    });
  }
  return { byCard: out, asksByQuestion: asks, pins };
}

/** 单张卡取信号（合并 question 维度的追问） */
function signalFor(signals, cardId, questionId) {
  const s = (signals.byCard && signals.byCard.get(cardId)) || {
    streak: 0, reviews: 0, lastRating: 0, lastReviewAt: '', recent: [],
    asks: 0, lastAsk: '', pinned: false, pinNote: '', pinAt: '',
  };
  const a = (signals.asksByQuestion && signals.asksByQuestion.get(questionId)) || null;
  return Object.assign({}, s, a ? { asks: a.asks, lastAsk: a.lastAsk } : {});
}

// ---------------------------------------------------------------------------
// 规则
// ---------------------------------------------------------------------------

function thresholds(opts) {
  const o = opts || {};
  return {
    retireStreak: o.retireStreak === undefined || o.retireStreak === null
      ? DEFAULT_RETIRE_STREAK : _num(o.retireStreak),
    demoteStreak: o.demoteStreak === undefined || o.demoteStreak === null
      ? DEFAULT_DEMOTE_STREAK : _num(o.demoteStreak),
    askExitStreak: o.askExitStreak === undefined || o.askExitStreak === null
      ? DEFAULT_ASK_EXIT_STREAK : _num(o.askExitStreak),
  };
}

/**
 * 提问**之后**连续答对了几次（近→远数，遇到错就停）。
 *
 * 语义是「从最近一次往回，提问之后那一串里的连对个数」——中间只要错过一次，
 * 更早的那些就不再计入（攒不到出口），这也是「没掌握」的自然表达。
 *
 * ⚠️ 必须限定「提问之后」：提问之前的连对不算——那时他还没搞懂，不能拿来证明「学会了」。
 * ⚠️ 同一天的先后靠 date 字符串比较够用（`2026-09-18 16:08` > `2026-09-18 16:07`），
 *    因为两边都是 `YYYY-MM-DD HH:MM:SS` 的定长本地时间。
 */
function afterAskCorrect(sig) {
  if (!sig || !_num(sig.asks)) return 0;
  const askAt = String(sig.lastAsk || '').trim();
  if (!askAt) return 0;
  let n = 0;
  for (const r of (sig.recent || [])) {
    const at = String(r.date || '');
    if (at && at > askAt) {
      if (_num(r.rating) >= 3) n += 1;
      else break;                          // 提问后错过 → 这一串到此为止，攒不到出口
    }
  }
  return n;
}

/**
 * 「问过 AI」这个身份还成立吗？（R-ask-exit）
 *
 * 成立 → 这张卡不降权、不退役（他当场没想通，值得反复看）。
 * 不成立 → 提问之后又连对了 askExitStreak 次，说明已经掌握了 → 从此按普通卡走连对规则。
 *
 * ⚠️ 这是用户 2026-09-19 指出的漏洞：原先只要 `asks > 0` 就永远豁免，
 *    于是「问过」变成永久的特权卡。「如果后来我已经学会了、掌握了这个点，
 *    它应该有一定的退出机制。比如说我连续都对。」
 */
function askExemptionHolds(sig, opts) {
  if (!sig || !_num(sig.asks)) return false;
  const t = thresholds(opts);
  if (t.askExitStreak <= 0) return true;         // 关掉出口 = 退回旧行为（永远豁免）
  return afterAskCorrect(sig) < t.askExitStreak;
}

/**
 * 这张卡该不该**移出智能组**？
 *
 * 顺序要说清：钉住 → 永久豁免（他显式要的）；
 * 问过且还没学会 → 豁免；其余按连对次数判。
 */
function isRetired(sig, opts) {
  const t = thresholds(opts);
  if (!sig) return false;
  if (sig.pinned) return false;
  if (askExemptionHolds(sig, opts)) return false;
  if (t.retireStreak <= 0) return false;
  return _num(sig.streak) >= t.retireStreak;
}

/** 连对但还没到退役线 → 降权档（返回 true 表示该用 STREAK2 档） */
function isDemoted(sig, opts) {
  const t = thresholds(opts);
  if (!sig) return false;
  if (sig.pinned) return false;
  if (askExemptionHolds(sig, opts)) return false;
  if (t.demoteStreak <= 0) return false;
  return _num(sig.streak) >= t.demoteStreak;
}

/**
 * 优先级档位。ctx（都由服务端算好传进来，保持本函数是纯函数）：
 *   { weak: 布尔, weakTopic: 布尔, recentTopic: 布尔, coldTopic: 布尔, freshCard: 布尔,
 *     isNew: 布尔, neverPracticed: 布尔, learning: 布尔, due: 布尔, leech: 布尔,
 *     opts: {retireStreak, demoteStreak} }
 *
 * ⚠️ 降权档必须排在 **due 之前**判：连对 2 次的卡往往正好"到期"，
 * 若先给 DUE，他说的「连对两次就没必要再排了」就永远不会生效（E2E 实测到 prio=3）。
 * 但降权只是**排在后面**，不是不许出现——它到期了仍然进组，只是让别的活先做。
 *
 * ⚠️ ASKED 档也要过 `askExemptionHolds`：提问后已经连对够多的卡不算"卡点"了，
 * 否则「问过」就是永久特权（用户 2026-09-19 指出的漏洞）。
 */
function priorityOf(sig, ctx) {
  const c = ctx || {};
  if (sig && sig.pinned) return { prio: PRIO.PINNED, why: PRIO_LABEL[PRIO.PINNED] };
  if (sig && _num(sig.asks) > 0 && askExemptionHolds(sig, c.opts)) {
    return { prio: PRIO.ASKED, why: PRIO_LABEL[PRIO.ASKED] };
  }
  if (c.leech) return { prio: PRIO.LEECH, why: PRIO_LABEL[PRIO.LEECH] };
  const neverPracticed = c.neverPracticed || (sig && _num(sig.reviews) === 0);
  if (neverPracticed && (c.freshCard || c.recentTopic || c.coldTopic)) {
    return { prio: PRIO.FRESH_NEW, why: PRIO_LABEL[PRIO.FRESH_NEW] };
  }
  if (neverPracticed || c.isNew) return { prio: PRIO.NEW, why: PRIO_LABEL[PRIO.NEW] };
  if (c.weak || c.weakTopic) return { prio: PRIO.WEAK, why: PRIO_LABEL[PRIO.WEAK] };
  if (isDemoted(sig, c.opts)) return { prio: PRIO.STREAK2, why: PRIO_LABEL[PRIO.STREAK2] };
  if (c.learning && c.due) return { prio: PRIO.LEARNING, why: PRIO_LABEL[PRIO.LEARNING] };
  if (c.due) return { prio: PRIO.DUE, why: PRIO_LABEL[PRIO.DUE] };
  return { prio: PRIO.REST, why: PRIO_LABEL[PRIO.REST] };
}

/** 卡片是否「最近新出」：questions.created_at 近 FRESH_NEW_DAYS 天 */
function isFreshCard(createdAt, nowMs) {
  if (!createdAt) return false;
  const t = Date.parse(String(createdAt).replace(' ', 'T'));
  if (isNaN(t)) return false;
  return (nowMs - t) <= FRESH_NEW_DAYS * 86400000;
}

// ---------------------------------------------------------------------------
// 钉住（pin）：数据读写
// ---------------------------------------------------------------------------

function normalizeCardId(id) {
  const s = String(id == null ? '' : id).trim();
  return /^[A-Za-z0-9_-]{1,40}$/.test(s) ? s : '';
}

/**
 * 钉住 / 取消钉住。幂等：重复钉 = 更新备注，重复取消 = 什么都不做的成功。
 * @returns {{ok:boolean, error?:string, card_id?:string, pinned?:boolean}}
 */
function setPin(db, cardId, pinned, note) {
  const cid = normalizeCardId(cardId);
  if (!cid) return { ok: false, error: 'card_id 不合法' };
  ensurePinTable(db);
  if (pinned === false) {
    db.prepare(`DELETE FROM ${PIN_TABLE} WHERE card_id = ?`).run(cid);
    return { ok: true, card_id: cid, pinned: false };
  }
  const exists = db.prepare('SELECT id FROM cards WHERE id = ?').get(cid);
  if (!exists) return { ok: false, error: '没有这张卡：' + cid };
  db.prepare(`INSERT INTO ${PIN_TABLE} (card_id, note, created_at) VALUES (?, ?, datetime('now','localtime'))
              ON CONFLICT(card_id) DO UPDATE SET note = excluded.note,
                updated_at = datetime('now','localtime')`).run(cid, note ? String(note) : '');
  return { ok: true, card_id: cid, pinned: true };
}

/** 钉住的卡清单（筛选页「📌 钉住」桶与学习页都用它） */
function listPins(db, opts) {
  const o = opts || {};
  const limit = Math.max(1, Math.min(200, _num(o.limit) || 100));
  const where = [], bind = [];
  if (o.subject) { where.push("COALESCE(t.subject,'') = ?"); bind.push(o.subject); }
  ensurePinTable(db);
  let rows = [];
  try {
    rows = db.prepare(`
      SELECT p.card_id, p.note, p.created_at,
             q.id AS question_id, q.type, q.topic_id, q.content,
             COALESCE(t.subject,'') AS subject, t.name AS topic_name,
             COALESCE(c.suspended,0) AS suspended
      FROM ${PIN_TABLE} p
      JOIN cards c ON c.id = p.card_id
      JOIN questions q ON q.id = c.question_id
      LEFT JOIN topics t ON q.topic_id = t.id
      ${where.length ? 'WHERE ' + where.join(' AND ') : ''}
      ORDER BY p.created_at DESC, p.card_id
      LIMIT ?
    `).all(...bind, limit);
  } catch (e) { rows = []; }
  return rows.map(r => {
    let content = {};
    try { content = JSON.parse(r.content); } catch (e) { content = { stem: r.content }; }
    return {
      card_id: r.card_id, question_id: r.question_id, type: r.type,
      topic_id: r.topic_id, topic_name: r.topic_name || '', subject: r.subject || '',
      note: r.note || '', created_at: r.created_at || '',
      suspended: r.suspended || 0,
      stem: content.stem || '',
    };
  });
}

function countPins(db) {
  try {
    ensurePinTable(db);
    return _num((db.prepare(`SELECT COUNT(*) AS n FROM ${PIN_TABLE}`).get() || {}).n);
  } catch (e) { return 0; }
}

module.exports = {
  PIN_TABLE, PRIO, PRIO_LABEL,
  DEFAULT_RETIRE_STREAK, DEFAULT_DEMOTE_STREAK, DEFAULT_ASK_EXIT_STREAK,
  STREAK_WINDOW, FRESH_NEW_DAYS,
  ensurePinTable, loadReviewSignals, loadAskSignals, loadPinSignals, loadSignals, signalFor,
  thresholds, afterAskCorrect, askExemptionHolds, isRetired, isDemoted, priorityOf, isFreshCard,
  normalizeCardId, setPin, listPins, countPins,
};

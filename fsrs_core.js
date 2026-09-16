// ============================================================
// FSRS 调度内核 —— 官方 ts-fsrs 的适配层（全项目唯一真相源）
//
// 算法本体：src/tools/ts-fsrs.cjs（官方 vendor，FSRS-6，MIT）
// 本文件职责：把 question_bank.db 的 cards 行 ↔ ts-fsrs 的 Card 结构互相映射，
//             并补上 ts-fsrs 不负责的部分（leech 水蛭卡、暂停、每日限额元数据）。
//
// 为什么不自己实现公式：
//   2026-09-13 之前这里是手写的 FSRS-5。核对 Anki 官方手册后发现两处语义偏差
//   （首步 Hard 应为前两步均值而非重复当前步；跨天步进要折算），且 FSRS-6 新增的
//   同日多次复习建模对本项目尤其相关（学习步进本身就产生同日多次复习）。
//   遂改用官方实现，只保留适配层。
//
// ⚠️ src/fsrs_scheduler.py 是**已废弃**的手写 FSRS-5，仅作历史参考，勿再使用。
//
// 字段名对照（库 ↔ DB）：
//   due              ↔ due_at / due_date     （库用绝对时间戳，DB 另有本地日期派生列）
//   scheduled_days   ↔ interval_days
//   learning_steps   ↔ learning_step / relearning_step
//   stability        ↔ stability
//   difficulty       ↔ difficulty
// ============================================================

const { fsrs, generatorParameters, createEmptyCard, Rating, State } = require('./tools/ts-fsrs.cjs');

// ---- 本地日期工具 --------------------------------------------------------
// ⚠️ 不要用 toISOString().slice(0,10) 取 due_date：那是 UTC 日期，在 UTC+8 下
//    会把本地零点算成前一天。due_at（绝对时刻）用 toISOString 是对的，
//    due_date（给人看的本地日期）必须走本地时区。
function localDateStr(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function localIso(d) {
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${localDateStr(d)}T${hh}:${mm}:${ss}`;
}

// ---- 默认配置 ------------------------------------------------------------
// 与 src/migrate.js 的 CONFIG_DEFAULTS 保持一致；DB 的 config 表是运行时来源。
const DEFAULT_CONFIG = {
  desired_retention: 0.85,
  learning_steps: [1, 10],      // 分钟
  relearning_steps: [10],       // 分钟
  graduating_interval: 1,       // 天（由 ts-fsrs 的学习毕业逻辑给出，此处仅作展示）
  easy_interval: 4,             // 天（同上，ts-fsrs 按 FSRS 公式计算）
  max_interval: 365,            // 天
  fuzz: true,
  new_per_day: 20,              // 每日新卡上限（Anki 默认）
  reviews_per_day: 200,         // 每日复习上限（Anki 默认）
  leech_threshold: 8,
  leech_action: 'mark',         // mark | suspend
  enable_short_term: true,      // FSRS-6 同日多次复习建模
};

/** 把 config 表的 {key: "value"} 文本映射转成带类型的配置对象 */
function parseConfig(raw) {
  const cfg = Object.assign({}, DEFAULT_CONFIG);
  if (!raw) return cfg;
  const num = (v, d) => { const n = parseFloat(v); return Number.isFinite(n) ? n : d; };
  const steps = (v, d) => {
    if (typeof v !== 'string' || !v.trim()) return d;
    const arr = v.split(',').map((s) => parseFloat(s.trim()))
      .filter((n) => Number.isFinite(n) && n > 0);
    return arr.length ? arr : d;
  };
  if (raw.desired_retention != null) cfg.desired_retention = num(raw.desired_retention, cfg.desired_retention);
  if (raw.learning_steps != null) cfg.learning_steps = steps(raw.learning_steps, cfg.learning_steps);
  if (raw.relearning_steps != null) cfg.relearning_steps = steps(raw.relearning_steps, cfg.relearning_steps);
  if (raw.graduating_interval != null) cfg.graduating_interval = num(raw.graduating_interval, cfg.graduating_interval);
  if (raw.easy_interval != null) cfg.easy_interval = num(raw.easy_interval, cfg.easy_interval);
  if (raw.max_interval != null) cfg.max_interval = num(raw.max_interval, cfg.max_interval);
  if (raw.fuzz != null) cfg.fuzz = String(raw.fuzz) === 'true';
  if (raw.new_per_day != null) cfg.new_per_day = num(raw.new_per_day, cfg.new_per_day);
  if (raw.reviews_per_day != null) cfg.reviews_per_day = num(raw.reviews_per_day, cfg.reviews_per_day);
  if (raw.leech_threshold != null) cfg.leech_threshold = num(raw.leech_threshold, cfg.leech_threshold);
  if (raw.leech_action != null) cfg.leech_action = String(raw.leech_action);
  if (raw.enable_short_term != null) cfg.enable_short_term = String(raw.enable_short_term) === 'true';
  return cfg;
}

/** 分钟数组 → ts-fsrs 的步进单位串： [1,10] → ["1m","10m"] */
function toStepUnits(mins) {
  return (mins || []).map((m) => `${m}m`);
}

// ---- ts-fsrs 实例缓存 ----------------------------------------------------
// 构造 fsrs 实例有开销，且 previewIntervals 需要"无抖动"版本，故按签名缓存。
const _instances = new Map();

function getFsrs(cfg, forceNoFuzz) {
  const sig = [
    cfg.desired_retention, (cfg.learning_steps || []).join(','),
    (cfg.relearning_steps || []).join(','), cfg.max_interval,
    forceNoFuzz ? 'nofuzz' : (cfg.fuzz ? 'fuzz' : 'nofuzz'),
    cfg.enable_short_term ? 'st' : 'nost',
  ].join('|');
  if (_instances.has(sig)) return _instances.get(sig);
  const inst = fsrs(generatorParameters({
    request_retention: cfg.desired_retention,
    maximum_interval: cfg.max_interval,
    enable_fuzz: forceNoFuzz ? false : !!cfg.fuzz,
    enable_short_term: !!cfg.enable_short_term,
    learning_steps: toStepUnits(cfg.learning_steps),
    relearning_steps: toStepUnits(cfg.relearning_steps),
  }));
  _instances.set(sig, inst);
  return inst;
}

// ---- 结构映射 ------------------------------------------------------------
/** DB 的 card 行 → ts-fsrs 的 Card */
function toTsCard(card, now) {
  const raw = card.due_at || (card.due_date ? card.due_date + 'T04:00:00' : null);
  let due = raw ? new Date(raw) : null;
  if (!due || isNaN(due)) due = new Date(now.getTime());

  let elapsed = 0;
  if (card.last_review) {
    const lr = new Date(card.last_review);
    if (!isNaN(lr)) elapsed = Math.max(0, Math.floor((now.getTime() - lr.getTime()) / 86400000));
  }

  const state = card.state || 0;
  const steps = card.learning_step || 0;

  // 新卡必须是干净的零值，否则 ts-fsrs 的参数校验会拒绝
  if (state === 0) {
    return {
      due, stability: 0, difficulty: 0, elapsed_days: 0,
      scheduled_days: 0, reps: card.reps || 0, lapses: card.lapses || 0,
      learning_steps: 0, state: 0,
    };
  }

  return {
    due,
    stability: card.stability || 0,
    difficulty: card.difficulty || 0,
    elapsed_days: elapsed,
    scheduled_days: Math.max(0, card.interval_days || 0),
    reps: card.reps || 0,
    lapses: card.lapses || 0,
    learning_steps: state === 1 || state === 3 ? steps : 0,
    state,
  };
}

/** ts-fsrs 的 Card → 回写 DB 的 card 行 */
function fromTsCard(tc, card) {
  const due = new Date(tc.due);
  card.state = tc.state;
  card.difficulty = tc.difficulty;
  card.stability = tc.stability;
  card.interval_days = tc.scheduled_days;
  card.reps = tc.reps;
  card.lapses = tc.lapses;
  card.learning_step = tc.learning_steps || 0;
  card.relearning_step = tc.state === State.Relearning ? (tc.learning_steps || 0) : 0;
  card.due_at = due.toISOString();
  card.due_date = localDateStr(due);          // 派生列，供 3 个既有消费者使用
  card.queue = card.suspended ? 3 : (tc.state === State.Review ? 2 : (tc.state === State.New ? 0 : 1));
  return card;
}

// ---- leech 水蛭卡（ts-fsrs 不负责，本项目自管）--------------------------
function applyLeech(card, cfg) {
  if (card.lapses >= cfg.leech_threshold && !card.leech) {
    card.leech = 1;
    if (cfg.leech_action === 'suspend') {
      card.suspended = 1;
      card.queue = 3;
    }
    return true;
  }
  return false;
}

// ---- 间隔显示 ------------------------------------------------------------
function formatDelta(dueAt, now) {
  const due = new Date(dueAt);
  if (isNaN(due)) return '';
  let ms = due.getTime() - now.getTime();
  if (ms < 0) ms = 0;
  const min = ms / 60000;
  if (min < 1) return '<1分';
  if (min < 60) return `${Math.round(min)}分`;
  const hr = min / 60;
  if (hr < 24) return `${Math.round(hr)}小时`;
  const day = hr / 24;
  if (day < 31) return `${Math.round(day)}天`;
  const mo = day / 30.44;
  if (mo < 12) return `${mo.toFixed(1)}个月`;
  return `${(day / 365.25).toFixed(1)}年`;
}

// ---- 对外 API ------------------------------------------------------------
const FSRS = {
  DEFAULT_CONFIG,
  parseConfig,
  formatDelta,
  localDateStr,
  localIso,
  Rating,
  State,

  /**
   * 评分并更新卡片（原地修改并返回 card）。
   * 若需要 review_log 的原始记录，用 scheduleWithLog。
   *
   * @param {object} card   DB 形态的卡片
   * @param {number} rating 1=Again 2=Hard 3=Good 4=Easy
   * @param {object} [opts] {today, nowMs, config}
   */
  schedule(card, rating, opts) {
    return this.scheduleWithLog(card, rating, opts).card;
  },

  /** 同 schedule，但同时返回 ts-fsrs 的 ReviewLog（供写入 review_log 表） */
  scheduleWithLog(card, rating, opts) {
    opts = opts || {};
    const cfg = Object.assign({}, DEFAULT_CONFIG, opts.config || {});
    const now = opts.nowMs ? new Date(opts.nowMs) : new Date();
    const r = Number(rating);
    if (!(r >= 1 && r <= 4)) throw new Error(`rating 必须是 1-4，收到 ${rating}`);

    const before = {
      state: card.state, difficulty: card.difficulty, stability: card.stability,
      lapses: card.lapses, interval_days: card.interval_days,
      due_at: card.due_at, queue: card.queue,
      learning_step: card.learning_step, relearning_step: card.relearning_step,
    };

    const inst = getFsrs(cfg, false);
    const tsCard = toTsCard(card, now);
    const { card: next, log } = inst.next(tsCard, now, r);

    if (!card.introduced_at) card.introduced_at = localIso(now);
    fromTsCard(next, card);
    applyLeech(card, cfg);
    card.last_review = localIso(now);

    return { card, log, before };
  },

  /**
   * 预览四个评分档的下次间隔（Anki 按钮副标题）。
   * 用无抖动实例，避免每次渲染数字都在跳。
   * @returns {{1:string,2:string,3:string,4:string}}
   */
  previewIntervals(card, opts) {
    opts = opts || {};
    const cfg = Object.assign({}, DEFAULT_CONFIG, opts.config || {});
    const now = opts.nowMs ? new Date(opts.nowMs) : new Date();
    const out = {};
    try {
      const inst = getFsrs(cfg, true);
      const rec = inst.repeat(toTsCard(card, now), now);
      for (const r of [1, 2, 3, 4]) {
        const item = rec[r];
        out[r] = item && item.card ? formatDelta(new Date(item.card.due).toISOString(), now) : '';
      }
    } catch (e) {
      out[1] = out[2] = out[3] = out[4] = '';
    }
    return out;
  },

  /** 该卡此刻是否到期（暂停的卡永不到期） */
  isDue(card, nowMs) {
    if (card.suspended) return false;
    const at = card.due_at || (card.due_date ? card.due_date + 'T04:00:00' : null);
    if (!at) return true;
    const d = new Date(at);
    if (isNaN(d)) return true;
    const now = nowMs ? new Date(nowMs) : new Date();
    return d.getTime() <= now.getTime();
  },

  /** 新建一张空的 ts-fsrs 卡（供需要预演的场景使用） */
  createEmptyCard(when) {
    return createEmptyCard(when || new Date());
  },
};

module.exports = { FSRS, DEFAULT_CONFIG, parseConfig, formatDelta };

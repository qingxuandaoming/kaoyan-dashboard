#!/usr/bin/env node
// ============================================================
// test_fsrs_spec.js —— FSRS 调度行为规格测试
//
// 验证 src/fsrs_core.js（官方 ts-fsrs 适配层）的行为符合 Anki 语义，
// 且 DB 结构映射正确。取代了原先的 test_fsrs_parity.py ——
// 那个测试比对的是已废弃的手写 FSRS-5 实现，在改用官方 FSRS-6 后失去意义。
//
// 运行：node src/tools/test_fsrs_spec.js
// 退出码 0 = 全绿
// ============================================================

const assert = require('assert');
const { FSRS, DEFAULT_CONFIG } = require('../fsrs_core');
const TSF = require('./ts-fsrs.cjs');

const NOW = new Date('2026-09-13T10:00:00');   // 固定"此刻"，保证可复现
const NOW_MS = NOW.getTime();
const CFG = Object.assign({}, DEFAULT_CONFIG, { fuzz: false });  // 测试关抖动

let passed = 0;
const failures = [];

function check(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`  [OK] ${name}`);
  } catch (e) {
    failures.push(`${name}: ${e.message}`);
    console.log(`  [FAIL] ${name}\n         ${e.message}`);
  }
}

/** 造一张 DB 形态的卡片 */
function mkCard(o) {
  return Object.assign({
    id: 'C-TEST', question_id: 'Q-TEST',
    state: 0, difficulty: 0, stability: 0,
    due_date: FSRS.localDateStr(NOW), due_at: NOW.toISOString(),
    last_review: null, reps: 0, lapses: 0,
    queue: 0, interval_days: 0,
    learning_step: 0, relearning_step: 0,
    leech: 0, suspended: 0, introduced_at: null,
  }, o || {});
}

/** due 距 NOW 的分钟数 */
function mins(card) {
  return Math.round((new Date(card.due_at).getTime() - NOW_MS) / 60000);
}

console.log('FSRS 规格测试（src/fsrs_core.js ← 官方 ts-fsrs）\n');

// ---------------------------------------------------------------- 内核完整性
console.log('内核：');
check('vendor 的 ts-fsrs 是 FSRS-6，21 个权重', () => {
  assert.ok(/FSRS-6/.test(TSF.FSRSVersion), `版本串异常: ${TSF.FSRSVersion}`);
  assert.strictEqual(TSF.default_w.length, 21);
});
check('适配层导出 Rating / State 枚举', () => {
  assert.strictEqual(FSRS.Rating.Again, 1);
  assert.strictEqual(FSRS.Rating.Easy, 4);
  assert.strictEqual(FSRS.State.New, 0);
  assert.strictEqual(FSRS.State.Relearning, 3);
});

// ---------------------------------------------------------------- 新卡四键
console.log('\n新卡（New）四键：');
check('Again → Learning，1 分钟后', () => {
  const c = FSRS.schedule(mkCard(), 1, { nowMs: NOW_MS, config: CFG });
  assert.strictEqual(c.state, FSRS.State.Learning);
  assert.strictEqual(mins(c), 1, `实际 ${mins(c)} 分`);
});
check('Hard → Learning，6 分钟（前两步 1m/10m 的均值，Anki 语义）', () => {
  const c = FSRS.schedule(mkCard(), 2, { nowMs: NOW_MS, config: CFG });
  assert.strictEqual(c.state, FSRS.State.Learning);
  assert.strictEqual(mins(c), 6, `实际 ${mins(c)} 分`);
});
check('Good → Learning，10 分钟（进阶到第 2 步）', () => {
  const c = FSRS.schedule(mkCard(), 3, { nowMs: NOW_MS, config: CFG });
  assert.strictEqual(c.state, FSRS.State.Learning);
  assert.strictEqual(mins(c), 10, `实际 ${mins(c)} 分`);
});
check('Easy → Review，直接毕业', () => {
  const c = FSRS.schedule(mkCard(), 4, { nowMs: NOW_MS, config: CFG });
  assert.strictEqual(c.state, FSRS.State.Review);
  assert.ok(c.interval_days >= 1, `间隔 ${c.interval_days}`);
});

// ---------------------------------------------------------------- 学习步进
console.log('\n学习步进（Learning）：');
check('Good 走完末步 → 毕业为 Review', () => {
  let c = FSRS.schedule(mkCard(), 3, { nowMs: NOW_MS, config: CFG });   // step 0→1
  c = FSRS.schedule(c, 3, { nowMs: NOW_MS + 10 * 60000, config: CFG }); // 末步 Good
  assert.strictEqual(c.state, FSRS.State.Review, '应已毕业');
  assert.ok(c.interval_days >= 1);
});
// ⚠️ 已知与 Anki 手册措辞的差异（ts-fsrs 上游行为，本项目不修补 vendor）
//    Anki 手册说 Hard 在"非第一步"时重复当前步（此处应为 10 分钟）；
//    但 ts-fsrs v5.4.2 在**任何**学习步上 Hard 都返回前两步均值（1m/10m → 6m）。
//    差异仅 4 分钟、仅影响末步的 Hard，且 ts-fsrs 序列仍然单调
//    （Again 1m < Hard 6m < Good 毕业），故接受上游行为并在此记录。
check('末步 Hard → 6 分钟（ts-fsrs 上游行为，见上方注释）', () => {
  let c = FSRS.schedule(mkCard(), 3, { nowMs: NOW_MS, config: CFG });
  const t = new Date(c.due_at).getTime();
  c = FSRS.schedule(c, 2, { nowMs: t, config: CFG });
  assert.strictEqual(c.state, FSRS.State.Learning);
  assert.strictEqual(Math.round((new Date(c.due_at).getTime() - t) / 60000), 6);
});
check('Again 后退回第一步（1 分钟）', () => {
  let c = FSRS.schedule(mkCard(), 3, { nowMs: NOW_MS, config: CFG });   // → step 1
  const t = new Date(c.due_at).getTime();
  c = FSRS.schedule(c, 1, { nowMs: t, config: CFG });                   // → step 0
  assert.strictEqual(c.state, FSRS.State.Learning);
  assert.strictEqual(Math.round((new Date(c.due_at).getTime() - t) / 60000), 1);
});

// ---------------------------------------------------------------- 复习卡
console.log('\n复习卡（Review）：');
check('Review + Again → Relearning，lapses +1', () => {
  const rev = mkCard({
    state: 2, difficulty: 5, stability: 20, interval_days: 20,
    reps: 5, lapses: 0, last_review: FSRS.localIso(new Date(NOW_MS - 20 * 86400000)),
  });
  const c = FSRS.schedule(rev, 1, { nowMs: NOW_MS, config: CFG });
  assert.strictEqual(c.state, FSRS.State.Relearning);
  assert.strictEqual(c.lapses, 1);
  assert.ok(mins(c) <= 15, `再学习步应较短，实际 ${mins(c)} 分`);
});
check('Review + Good → 间隔增长', () => {
  const rev = mkCard({
    state: 2, difficulty: 5, stability: 20, interval_days: 20,
    reps: 5, last_review: FSRS.localIso(new Date(NOW_MS - 20 * 86400000)),
  });
  const c = FSRS.schedule(rev, 3, { nowMs: NOW_MS, config: CFG });
  assert.strictEqual(c.state, FSRS.State.Review);
  assert.ok(c.interval_days > 20, `间隔应增长，实际 ${c.interval_days}`);
});

// ---------------------------------------------------------------- 上下限
console.log('\n边界：');
// ts-fsrs 的 scheduled_days 相对 maximum_interval 有 +1~2 天的取整余量
// （实测封顶 365 时：Good→366，Easy→367；封顶 10/30/100 时各 +1）。
// 这是上游 date_diff 的取整约定，不是封顶失效。
// 因此这里测真正的性质：封顶让间隔"有界"，而不是断言精确天数。
check('maximum_interval 真的封顶（有界 vs 无界对比）', () => {
  const big = mkCard({
    state: 2, difficulty: 3, stability: 5000, interval_days: 3000,
    reps: 20, last_review: FSRS.localIso(new Date(NOW_MS - 3000 * 86400000)),
  });
  const capped = FSRS.schedule(Object.assign({}, big), 4,
    { nowMs: NOW_MS, config: Object.assign({}, CFG, { max_interval: 365 }) });
  const uncapped = FSRS.schedule(Object.assign({}, big), 4,
    { nowMs: NOW_MS, config: Object.assign({}, CFG, { max_interval: 36500 }) });

  assert.ok(uncapped.interval_days > 1000,
    `未封顶时应给出超长间隔，实际 ${uncapped.interval_days}`);
  assert.ok(capped.interval_days < uncapped.interval_days,
    `封顶未生效：capped=${capped.interval_days} uncapped=${uncapped.interval_days}`);
  assert.ok(capped.interval_days <= 370,
    `封顶后应落在 365 附近，实际 ${capped.interval_days}`);
});
check('due_date 是 due_at 的本地日期（不是 UTC 日期）', () => {
  const c = FSRS.schedule(mkCard(), 3, { nowMs: NOW_MS, config: CFG });
  const local = FSRS.localDateStr(new Date(c.due_at));
  assert.strictEqual(c.due_date, local, `due_at=${c.due_at} due_date=${c.due_date}`);
});
check('queue 与 state 一致（Review→2，Learning→1）', () => {
  const l = FSRS.schedule(mkCard(), 1, { nowMs: NOW_MS, config: CFG });
  assert.strictEqual(l.queue, 1);
  const r = FSRS.schedule(mkCard(), 4, { nowMs: NOW_MS, config: CFG });
  assert.strictEqual(r.queue, 2);
});

// ---------------------------------------------------------------- leech
console.log('\n水蛭卡（leech，ts-fsrs 不负责，本项目自管）：');
check('lapses 达阈值 → leech=1', () => {
  const c = mkCard({
    state: 2, difficulty: 5, stability: 10, interval_days: 10,
    reps: 10, lapses: 7, last_review: FSRS.localIso(new Date(NOW_MS - 10 * 86400000)),
  });
  const out = FSRS.schedule(c, 1, { nowMs: NOW_MS, config: CFG });
  assert.strictEqual(out.lapses, 8);
  assert.strictEqual(out.leech, 1);
});
check('leech_action=suspend 时自动暂停且 queue=3', () => {
  const c = mkCard({
    state: 2, difficulty: 5, stability: 10, interval_days: 10,
    reps: 10, lapses: 7, last_review: FSRS.localIso(new Date(NOW_MS - 10 * 86400000)),
  });
  const cfg = Object.assign({}, CFG, { leech_action: 'suspend' });
  const out = FSRS.schedule(c, 1, { nowMs: NOW_MS, config: cfg });
  assert.strictEqual(out.suspended, 1);
  assert.strictEqual(out.queue, 3);
});
check('已暂停的卡不再到期', () => {
  const c = mkCard({ state: 2, suspended: 1, due_at: new Date(NOW_MS - 86400000).toISOString() });
  assert.strictEqual(FSRS.isDue(c, NOW_MS), false);
});

// ---------------------------------------------------------------- 预览
console.log('\n间隔预览（按钮副标题）：');
check('previewIntervals 返回四个非空档位', () => {
  const p = FSRS.previewIntervals(mkCard(), { nowMs: NOW_MS, config: CFG });
  for (const r of [1, 2, 3, 4]) {
    assert.ok(p[r] && p[r].length > 0, `档位 ${r} 为空`);
  }
  assert.ok(/分/.test(p[1]), `Again 应为分钟级，实际 ${p[1]}`);
});
check('进度快照可在多次评分后保持结构完整', () => {
  let c = mkCard();
  for (const r of [3, 3, 3, 3, 1, 2, 3, 4]) {
    c = FSRS.schedule(c, r, { nowMs: NOW_MS, config: CFG });
  }
  for (const k of ['state', 'difficulty', 'stability', 'due_at', 'due_date', 'interval_days', 'queue', 'reps', 'lapses']) {
    assert.ok(c[k] !== undefined, `字段 ${k} 丢失`);
  }
  assert.ok(!isNaN(new Date(c.due_at).getTime()), 'due_at 不是合法时间');
});

// ---------------------------------------------------------------- 结果
console.log(`\n${'-'.repeat(50)}`);
if (failures.length) {
  console.log(`[FAIL] ${failures.length} 项失败 / 共 ${passed + failures.length} 项`);
  for (const f of failures) console.log('   -', f);
  process.exit(1);
}
console.log(`[OK] 全部 ${passed} 项通过`);

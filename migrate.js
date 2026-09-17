#!/usr/bin/env node
// ============================================================
// migrate.js —— question_bank.db 幂等迁移器
//
// 设计要点
// --------
// * **幂等**：可反复运行。SQLite 的 ALTER TABLE ADD COLUMN 不支持
//   IF NOT EXISTS，因此每列都先用 PRAGMA table_info 探测再加。
// * **零破坏**：只加列/表/索引，不改类型、不删列、不重命名。
//   既有 3 个只认 cards.due_date 的消费者（generate_dashboard.py、
//   daily_planner.py、serve.js）不受影响。
// * **与 schema.sql 同源**：schema.sql 里的 cards / review_log 定义
//   必须包含此处新增的列。改一边就要改另一边。
//
// 用法：
//     node src/migrate.js          # 手动跑
//     require('./migrate').run()   # serve.js 启动时自动跑
// ============================================================

const path = require('path');
const { DatabaseSync } = require('node:sqlite');

// 与 serve.js 一致：支持 DB_PATH 环境变量覆盖（便于在副本库上验证）。
// ⚠️ serve.js 启动时会把自己的 DB_PATH 显式传给 run()，不要依赖这里的默认值。
const DB_PATH = process.env.DB_PATH
  ? path.resolve(__dirname, process.env.DB_PATH)
  : path.join(__dirname, 'question_bank.db');

// ---- Anki 风格默认配置 ---------------------------------------------------
// 依据：Anki 2.1.6x 默认值 + 2026-12-19 考试倒排
const CONFIG_DEFAULTS = {
  desired_retention: '0.85',   // 沿用原值，调高会整体拉长区间
  learning_steps: '1,10',      // 分钟；Anki 默认 1m/10m
  relearning_steps: '10',      // 分钟；Anki 默认
  graduating_interval: '1',    // 天；学习毕业后的首个间隔
  easy_interval: '4',          // 天；Easy 直接毕业
  new_per_day: '20',           // 每日新卡上限；Anki 默认
  reviews_per_day: '200',      // 每日复习上限；Anki 默认
  max_interval: '365',         // 天；保证考前至少再见一次（Anki 默认 36500 过大）
  fuzz: 'true',                // 区间随机抖动，防同批卡永远同日到期
  leech_threshold: '8',        // 累计遗忘多少次判为水蛭卡；Anki 默认
  leech_action: 'mark',        // mark=只标记置顶 | suspend=自动暂停
};

// ---- 小工具 --------------------------------------------------------------
function tableExists(db, table) {
  return !!db.prepare(
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?"
  ).get(table);
}

function columnNames(db, table) {
  return db.prepare(`PRAGMA table_info(${table})`).all().map((r) => r.name);
}

/** 幂等加列：已存在则跳过 */
function addCol(db, table, name, ddl, report) {
  if (columnNames(db, table).includes(name)) {
    report.skipped.push(`${table}.${name}`);
    return false;
  }
  db.exec(`ALTER TABLE ${table} ADD COLUMN ${ddl}`);
  report.added.push(`${table}.${name}`);
  return true;
}

function addIndex(db, name, sql, report) {
  const exists = !!db.prepare(
    "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?"
  ).get(name);
  if (exists) {
    report.skipped.push(`index ${name}`);
    return false;
  }
  db.exec(sql);
  report.added.push(`index ${name}`);
  return true;
}

// ---- 主迁移 --------------------------------------------------------------
/**
 * @param {string} [dbPath] 覆盖默认 DB 路径（测试用）
 * @returns {{ok:boolean, added:string[], skipped:string[], notes:string[]}}
 */
function run(dbPath) {
  const report = { ok: false, added: [], skipped: [], notes: [] };
  let db;
  try {
    db = new DatabaseSync(dbPath || DB_PATH);
  } catch (e) {
    report.notes.push(`打开数据库失败：${e.message}`);
    return report;
  }

  try {
    if (!tableExists(db, 'cards')) {
      report.notes.push('cards 表不存在，跳过迁移（请先执行 schema.sql 建库）');
      return report;
    }

    // --- 1. cards：Anki 调度所需新列 ---
    addCol(db, 'cards', 'due_at', 'due_at TEXT', report);
    addCol(db, 'cards', 'learning_step', 'learning_step INTEGER DEFAULT 0', report);
    addCol(db, 'cards', 'relearning_step', 'relearning_step INTEGER DEFAULT 0', report);
    addCol(db, 'cards', 'leech', 'leech INTEGER DEFAULT 0', report);
    addCol(db, 'cards', 'suspended', 'suspended INTEGER DEFAULT 0', report);
    addCol(db, 'cards', 'introduced_at', 'introduced_at TEXT', report);

    // --- 2. review_log：撤销所需的回滚快照列 ---
    addCol(db, 'review_log', 'difficulty_before', 'difficulty_before REAL', report);
    addCol(db, 'review_log', 'stability_before', 'stability_before REAL', report);
    addCol(db, 'review_log', 'lapses_before', 'lapses_before INTEGER', report);
    addCol(db, 'review_log', 'interval_before', 'interval_before REAL', report);
    addCol(db, 'review_log', 'due_at_before', 'due_at_before TEXT', report);
    addCol(db, 'review_log', 'queue_before', 'queue_before INTEGER', report);
    // 学习步进下标也要快照：撤销回 Learning/Relearning 态时需要精确还原
    addCol(db, 'review_log', 'learning_step_before', 'learning_step_before INTEGER', report);
    addCol(db, 'review_log', 'relearning_step_before', 'relearning_step_before INTEGER', report);
    // 学生**实际选的那一项**（选项字母或作答文本）。2026-09-14 补。
    // 此前只在生成 AI 解析时把错选随请求传过去、并没有落库，导致"答错了"有记录、
    // "错在哪"没有——而错选才是复盘时最有信息量的信号（概念混淆？计算失误？审题？）。
    addCol(db, 'review_log', 'chosen', 'chosen TEXT', report);
    // 解析线索的模式：wrong=针对错选讲解，correct=答对了但仍想追问（陷阱/边界/自查）。
    // 必须落库：多轮追问要沿用同一套 system 要求，否则模型会去纠正一个不存在的错选。
    addCol(db, 'explain_log', 'mode', "mode TEXT DEFAULT 'wrong'", report);

    // --- 3. questions：幂等导入用外部键 ---
    addCol(db, 'questions', 'ext_key', 'ext_key TEXT', report);

    // --- 4. config 表 ---
    db.exec(`
      CREATE TABLE IF NOT EXISTS config (
        key        TEXT PRIMARY KEY,
        value      TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime'))
      )
    `);
    let cfgInserted = 0;
    const insCfg = db.prepare(
      "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)"
    );
    for (const [k, v] of Object.entries(CONFIG_DEFAULTS)) {
      const r = insCfg.run(k, v);
      if (r.changes > 0) cfgInserted += 1;
    }
    report.notes.push(`config 新增 ${cfgInserted} 项，共 ${Object.keys(CONFIG_DEFAULTS).length} 项`);

    // --- 4b. daily_tasks 表（每日任务，2026-09-14）---
    // 用表而不是 JSON：要按「日期 × 来源 × 删除态」查历史，agent 和 UI 都要消费。
    // deleted 是软删除——用户删掉的任务必须留痕，agent 才能读到「他删了什么、
    // 删的是我布置的还是自己加的」，据此调整后续布置。
    // ext_key 只对 agent 生成的任务赋值（sha1(date|归一化text)）用于幂等重跑；
    // user 任务为 NULL。SQLite 的唯一索引允许多个 NULL，所以两类互不干扰。
    db.exec(`
      CREATE TABLE IF NOT EXISTS daily_tasks (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        task_date  TEXT NOT NULL,
        text       TEXT NOT NULL,
        source     TEXT NOT NULL DEFAULT 'agent',
        subject    TEXT,
        done       INTEGER NOT NULL DEFAULT 0,
        done_at    TEXT,
        deleted    INTEGER NOT NULL DEFAULT 0,
        deleted_at TEXT,
        ext_key    TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        updated_at TEXT
      )
    `);

    // --- 4c. explain_log 表（错题解析与追问留痕，2026-09-14）---
    // 留痕的三个用途：① 后续完善笔记的一手素材（错在哪、当时怎么问的）
    // ② 同题再错时可直接复用上次解析，不重复烧 token
    // ③ 周定时任务汇总本周错题的取数来源
    // thread_id 分组：追问是多轮的，同一道题不同次作答也分属不同线索。
    db.exec(`
      CREATE TABLE IF NOT EXISTS explain_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id   TEXT NOT NULL,
        question_id TEXT,
        card_id     INTEGER,
        subject     TEXT,
        topic_id    TEXT,
        chosen      TEXT,
        correct     TEXT,
        role        TEXT NOT NULL,
        content     TEXT NOT NULL,
        created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
      )
    `);

    // --- 4d. 早间回顾状态表（并入大盘 + 多设备同步，2026-09-20）---
    // 原先打卡与间隔重复状态都存在各设备自己的 localStorage 里，换设备就各算各的。
    // 并入大盘后统一收到服务端：平板/手机只是发起请求，进度全落在这台电脑上。
    // mr_sr 是早间回顾自带闪卡库（FC_LIB）的简易三步 SR，与 FSRS 的 cards 表无关，
    // 刻意不合并——两边调度模型不同，硬并会让 question_bank.db 混进两套语义。
    db.exec(`
      CREATE TABLE IF NOT EXISTS mr_checkin (
        check_date TEXT PRIMARY KEY,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
      )
    `);
    db.exec(`
      CREATE TABLE IF NOT EXISTS mr_sr (
        card_id    TEXT PRIMARY KEY,
        step       INTEGER NOT NULL DEFAULT -1,
        next_due   TEXT NOT NULL DEFAULT '',
        mastered   INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
      )
    `);

    // --- 4e. note_qa 表（读笔记时的提问与回答，2026-09-21）---
    // 场景：读笔记读到一半有疑问，把「我正在读的那一段」当上下文直接问 AI。
    // 留痕的价值：① 事后能回看「我当时在哪段卡住了、答案是什么」，
    // ② 同一篇笔记再问时把上下文说清楚，不用重新贴一遍，
    // ③ 攒起来就是笔记该补哪里的证据（和 explain_log 一个思路，但那个绑题目 ID，
    //    这个绑笔记路径 + 阅读位置，所以另起一张表而不是硬塞进去）。
    db.exec(`
      CREATE TABLE IF NOT EXISTS note_qa (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id   TEXT NOT NULL,
        note_path   TEXT,
        section     TEXT,
        context     TEXT,
        role        TEXT NOT NULL,
        content     TEXT NOT NULL,
        created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
      )
    `);
    db.exec(`
      CREATE INDEX IF NOT EXISTS idx_note_qa_thread ON note_qa(thread_id)
    `);
    db.exec(`
      CREATE INDEX IF NOT EXISTS idx_note_qa_path ON note_qa(note_path, id)
    `);

    // --- 4f. card_reports 表（「这道题有问题」标记，2026-09-17）---
    // 与 schema.sql 同源，改一边要改另一边。
    // 存在的理由：闪卡里有一类错不是学生记错，是题目本身错了——最典型的是选择题
    // 有两个正确选项（用户在练习里实测到：叠加原理那道 A、C 都对），他按对的项却被
    // 判错，越练越糊涂，还把 FSRS 的难度/稳定度带偏。练习时一键标记，每日任务里
    // 由 agent 逐张核对并修好。
    // 与🗑删卡（cards.suspended）的区别：删卡是当场判死刑、卡直接下架；标记是**待核实**，
    // 卡还在、还显示（前端画徽标），等复核后回写 status。
    db.exec(`
      CREATE TABLE IF NOT EXISTS card_reports (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        card_id     TEXT NOT NULL,
        question_id TEXT,
        kind        TEXT NOT NULL DEFAULT 'other',
        note        TEXT,
        chosen      TEXT,
        correct     TEXT,
        snapshot    TEXT,
        status      TEXT NOT NULL DEFAULT 'open',
        source      TEXT NOT NULL DEFAULT 'user',
        fix_note    TEXT,
        fixed_at    TEXT,
        created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        updated_at  TEXT
      )
    `);
    // 部分唯一索引：一张卡同时只有一条 open 标记（重复标记＝改内容）。
    // 已修/已驳回的历史行不受约束，所以同一张卡可以被标记很多次。
    db.exec(`
      CREATE UNIQUE INDEX IF NOT EXISTS idx_card_reports_open
        ON card_reports(card_id) WHERE status = 'open'
    `);
    db.exec(`
      CREATE INDEX IF NOT EXISTS idx_card_reports_status
        ON card_reports(status, created_at)
    `);

    // --- 5. schema_version ---
    db.exec(`
      CREATE TABLE IF NOT EXISTS schema_version (
        version    INTEGER PRIMARY KEY,
        applied_at TEXT DEFAULT (datetime('now','localtime'))
      )
    `);

    // --- 6. 索引 ---
    addIndex(db, 'idx_cards_due_at',
      'CREATE INDEX IF NOT EXISTS idx_cards_due_at ON cards(due_at)', report);
    addIndex(db, 'idx_cards_leech',
      'CREATE INDEX IF NOT EXISTS idx_cards_leech ON cards(leech)', report);
    addIndex(db, 'idx_cards_suspended',
      'CREATE INDEX IF NOT EXISTS idx_cards_suspended ON cards(suspended)', report);
    addIndex(db, 'idx_questions_ext_key',
      'CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_ext_key ON questions(ext_key)', report);
    addIndex(db, 'idx_daily_tasks_date',
      'CREATE INDEX IF NOT EXISTS idx_daily_tasks_date ON daily_tasks(task_date)', report);
    addIndex(db, 'idx_daily_tasks_ext_key',
      'CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_tasks_ext_key ON daily_tasks(ext_key)', report);
    addIndex(db, 'idx_explain_thread',
      'CREATE INDEX IF NOT EXISTS idx_explain_thread ON explain_log(thread_id)', report);
    addIndex(db, 'idx_explain_created',
      'CREATE INDEX IF NOT EXISTS idx_explain_created ON explain_log(created_at)', report);
    addIndex(db, 'idx_explain_qid',
      'CREATE INDEX IF NOT EXISTS idx_explain_qid ON explain_log(question_id)', report);

    // --- 7. 回填（仅空值，幂等）---
    // 存量卡没有 due_at：用 due_date 补，04:00 让它们当天即视为到期，行为不变。
    const b1 = db.prepare(
      "UPDATE cards SET due_at = due_date || 'T04:00:00' WHERE due_at IS NULL OR due_at = ''"
    ).run();
    // 已开始复习的卡补 introduced_at（用于每日新卡额度统计）
    const b2 = db.prepare(
      "UPDATE cards SET introduced_at = due_date || 'T04:00:00' " +
      "WHERE state <> 0 AND (introduced_at IS NULL OR introduced_at = '')"
    ).run();
    if (b1.changes || b2.changes) {
      report.notes.push(`回填 due_at ${b1.changes} 行，introduced_at ${b2.changes} 行`);
    }

    // --- 8. 版本登记 ---
    db.prepare('INSERT OR IGNORE INTO schema_version (version) VALUES (?)').run(1);
    // 2 = daily_tasks 表（每日任务）
    db.prepare('INSERT OR IGNORE INTO schema_version (version) VALUES (?)').run(2);
    // 3 = explain_log 表（错题解析与追问留痕）
    db.prepare('INSERT OR IGNORE INTO schema_version (version) VALUES (?)').run(3);
    // 4 = review_log.chosen（记录学生实际选的那一项）
    db.prepare('INSERT OR IGNORE INTO schema_version (version) VALUES (?)').run(4);
    // 5 = card_reports 表（「这道题有问题」标记 + 复核留痕）
    db.prepare('INSERT OR IGNORE INTO schema_version (version) VALUES (?)').run(5);

    report.ok = true;
  } catch (e) {
    report.notes.push(`迁移失败：${e.message}`);
  } finally {
    try { db.close(); } catch (_) { /* ignore */ }
  }
  return report;
}

module.exports = { run, CONFIG_DEFAULTS, DB_PATH };

// ---- CLI ----------------------------------------------------------------
if (require.main === module) {
  const r = run();
  console.log(`迁移目标：${DB_PATH}`);
  if (r.added.length) console.log(`  新增 ${r.added.length} 项：\n    ` + r.added.join('\n    '));
  if (r.skipped.length) console.log(`  已存在跳过 ${r.skipped.length} 项`);
  for (const n of r.notes) console.log(`  · ${n}`);
  console.log(r.ok ? '\n[OK] 迁移完成（幂等，可重复运行）' : '\n[FAIL] 迁移未完成');
  process.exit(r.ok ? 0 : 1);
}

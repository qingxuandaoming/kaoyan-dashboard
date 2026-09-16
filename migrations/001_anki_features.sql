-- ============================================================
-- 迁移 001：Anki 级复习机制所需的结构
--
-- 应用方式：由 src/migrate.js 幂等执行（不要手工跑本文件）。
--   node src/migrate.js
--
-- 注意：SQLite 的 ALTER TABLE ADD COLUMN 不支持 IF NOT EXISTS，
--       所以 migrate.js 用 PRAGMA table_info 逐列探测后再加。
--       本文件是可读的 DDL 清单，与 migrate.js 和 schema.sql 三者同源。
--
-- 兼容性承诺：只加列/表/索引，不改类型、不删列、不重命名。
--   cards.due_date 必须继续同步写入（3 个既有消费者只认它）：
--     - src/generate_dashboard.py（due_today 统计）
--     - src/daily_planner.py（到期判定）
--     - src/serve.js（/api/flashcards/today）
-- ============================================================

-- ------------------------------------------------------------
-- 1. cards：分钟级调度 + leech + 暂停
-- ------------------------------------------------------------
-- due_at 是新的权威调度时间（ISO 本地时间，精确到分钟），
-- due_date 降级为派生列 = due_at 的前 10 位。
ALTER TABLE cards ADD COLUMN due_at          TEXT;

-- 学习/再学习步进下标（对应 config.learning_steps / relearning_steps）
ALTER TABLE cards ADD COLUMN learning_step   INTEGER DEFAULT 0;
ALTER TABLE cards ADD COLUMN relearning_step INTEGER DEFAULT 0;

-- 水蛭卡：lapses 达到 config.leech_threshold 时置 1
ALTER TABLE cards ADD COLUMN leech           INTEGER DEFAULT 0;

-- 手动/自动暂停；queue=3(SUSPENDED) 与之一致
ALTER TABLE cards ADD COLUMN suspended       INTEGER DEFAULT 0;

-- 首次被评分的时间，用于「每日新卡额度」统计
ALTER TABLE cards ADD COLUMN introduced_at   TEXT;

-- ------------------------------------------------------------
-- 2. review_log：撤销所需的回滚快照
-- ------------------------------------------------------------
-- 原表只有 state_before/state_after，不足以还原 D/S/lapses/interval。
ALTER TABLE review_log ADD COLUMN difficulty_before REAL;
ALTER TABLE review_log ADD COLUMN stability_before  REAL;
ALTER TABLE review_log ADD COLUMN lapses_before     INTEGER;
ALTER TABLE review_log ADD COLUMN interval_before   REAL;
ALTER TABLE review_log ADD COLUMN due_at_before     TEXT;
ALTER TABLE review_log ADD COLUMN queue_before      INTEGER;
-- 学习步进下标也要快照：撤销回 Learning/Relearning 态时需精确还原
ALTER TABLE review_log ADD COLUMN learning_step_before   INTEGER;
ALTER TABLE review_log ADD COLUMN relearning_step_before INTEGER;

-- ------------------------------------------------------------
-- 3. questions：幂等导入用的外部键
-- ------------------------------------------------------------
-- ext_key = sha1(模块 + 归一化题干)，配合唯一索引实现 INSERT OR IGNORE。
-- SQLite 唯一索引允许多个 NULL，因此既有 213 行不受影响。
ALTER TABLE questions ADD COLUMN ext_key TEXT;

-- ------------------------------------------------------------
-- 4. config：调度参数（key/value 文本）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS config (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 默认值见 src/migrate.js 的 CONFIG_DEFAULTS：
--   desired_retention 0.85 | learning_steps 1,10 | relearning_steps 10
--   graduating_interval 1  | easy_interval 4     | new_per_day 20
--   reviews_per_day 200    | max_interval 365    | fuzz true
--   leech_threshold 8      | leech_action mark

-- ------------------------------------------------------------
-- 5. schema_version：迁移版本登记
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT DEFAULT (datetime('now','localtime'))
);

-- ------------------------------------------------------------
-- 6. 索引
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_cards_due_at    ON cards(due_at);
CREATE INDEX IF NOT EXISTS idx_cards_leech     ON cards(leech);
CREATE INDEX IF NOT EXISTS idx_cards_suspended ON cards(suspended);
CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_ext_key ON questions(ext_key);

-- ------------------------------------------------------------
-- 7. 回填（仅空值）
-- ------------------------------------------------------------
-- 存量卡统一补 04:00，使它们当天即视为到期，与迁移前行为一致。
UPDATE cards SET due_at = due_date || 'T04:00:00'
 WHERE due_at IS NULL OR due_at = '';

UPDATE cards SET introduced_at = due_date || 'T04:00:00'
 WHERE state <> 0 AND (introduced_at IS NULL OR introduced_at = '');

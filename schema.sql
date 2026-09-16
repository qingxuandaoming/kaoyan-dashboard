-- ============================================================
-- question_bank.db schema
-- FSRS-5 spaced repetition + question bank for 考研 (2026-12-19)
-- ============================================================

-- Topics / knowledge nodes
CREATE TABLE IF NOT EXISTS topics (
    id          TEXT PRIMARY KEY,
    subject     TEXT NOT NULL,
    name        TEXT NOT NULL,
    chapter     INTEGER,
    exam_weight REAL DEFAULT 1.0,
    difficulty  REAL DEFAULT 0.5
);

-- Questions
CREATE TABLE IF NOT EXISTS questions (
    id             TEXT PRIMARY KEY,
    topic_id       TEXT REFERENCES topics(id),
    type           TEXT NOT NULL,          -- choice | fill | short_answer | essay
    difficulty     REAL DEFAULT 0.5,
    source         TEXT,
    year           INTEGER,
    content        TEXT NOT NULL,
    times_asked    INTEGER DEFAULT 0,
    times_correct  INTEGER DEFAULT 0,
    avg_time_sec   REAL DEFAULT 0,
    last_asked     TEXT,
    created_at     TEXT DEFAULT (datetime('now')),
    ext_key        TEXT                   -- sha1(模块+归一化题干)，幂等导入用
);

-- Cards (FSRS state per question)
-- 注：due_at 是权威调度时间（ISO 本地时间，分钟精度）；due_date 是它的派生列
--     （= due_at 前 10 位），保留是因为有 3 个既有消费者只认 due_date。
--     新增列与 src/migrations/001_anki_features.sql 同源，改一边要改另一边。
CREATE TABLE IF NOT EXISTS cards (
    id              TEXT PRIMARY KEY,
    question_id     TEXT REFERENCES questions(id),
    state           INTEGER DEFAULT 0,    -- 0=New 1=Learning 2=Review 3=Relearning
    difficulty      REAL DEFAULT 0.0,
    stability       REAL DEFAULT 0.0,
    due_date        TEXT NOT NULL,
    last_review     TEXT,
    reps            INTEGER DEFAULT 0,
    lapses          INTEGER DEFAULT 0,
    queue           INTEGER DEFAULT 0,    -- 0=new 1=learning 2=review 3=suspended
    interval_days   REAL DEFAULT 0,
    due_at          TEXT,                 -- 权威到期时刻
    learning_step   INTEGER DEFAULT 0,    -- 学习步进下标
    relearning_step INTEGER DEFAULT 0,    -- 再学习步进下标
    leech           INTEGER DEFAULT 0,    -- 水蛭卡标记
    suspended       INTEGER DEFAULT 0,    -- 已暂停
    introduced_at   TEXT                  -- 首次评分时间（每日新卡额度用）
);

-- Review log (every rating event)
CREATE TABLE IF NOT EXISTS review_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id           TEXT REFERENCES cards(id),
    question_id       TEXT REFERENCES questions(id),
    rating            INTEGER NOT NULL,   -- 1=Again 2=Hard 3=Good 4=Easy
    elapsed_days      REAL,
    new_interval      REAL,
    review_date       TEXT DEFAULT (datetime('now','localtime')),
    state_before      INTEGER,
    state_after       INTEGER,
    -- 撤销用的回滚快照
    difficulty_before REAL,
    stability_before  REAL,
    lapses_before     INTEGER,
    interval_before   REAL,
    due_at_before     TEXT,
    queue_before      INTEGER,
    learning_step_before   INTEGER,
    relearning_step_before INTEGER,
    chosen            TEXT                -- 学生实际选的那一项（选项字母或作答文本）
);

-- 调度参数（key/value 文本，默认值见 src/migrate.js 的 CONFIG_DEFAULTS）
CREATE TABLE IF NOT EXISTS config (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 迁移版本登记
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 每日任务（大盘「活动」页 + agent 定时生成）。
-- source: 'agent' | 'user'；deleted 是软删除，保留「用户删了什么」供 agent 学习。
-- ext_key = sha1(task_date|归一化text)，只给 agent 任务赋值用于幂等重跑；user 任务为 NULL。
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
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_cards_due
    ON cards(due_date, queue);

CREATE INDEX IF NOT EXISTS idx_cards_due_at
    ON cards(due_at);

CREATE INDEX IF NOT EXISTS idx_cards_leech
    ON cards(leech);

CREATE INDEX IF NOT EXISTS idx_cards_suspended
    ON cards(suspended);

CREATE INDEX IF NOT EXISTS idx_questions_topic
    ON questions(topic_id);

-- 唯一索引允许多个 NULL，既有行不受影响
CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_ext_key
    ON questions(ext_key);

CREATE INDEX IF NOT EXISTS idx_review_log_date
    ON review_log(review_date);

CREATE INDEX IF NOT EXISTS idx_review_log_card
    ON review_log(card_id);

CREATE INDEX IF NOT EXISTS idx_daily_tasks_date
    ON daily_tasks(task_date);

-- 错题解析与追问留痕。thread_id 分组一轮问答（追问是多轮的，
-- 同一道题不同次作答也分属不同线索）。留痕用于完善笔记、复用解析、周报取数。
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
);

CREATE INDEX IF NOT EXISTS idx_explain_thread
    ON explain_log(thread_id);

CREATE INDEX IF NOT EXISTS idx_explain_created
    ON explain_log(created_at);

CREATE INDEX IF NOT EXISTS idx_explain_qid
    ON explain_log(question_id);

-- 同 idx_questions_ext_key：唯一索引允许多个 NULL，user 任务不受限
CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_tasks_ext_key
    ON daily_tasks(ext_key);

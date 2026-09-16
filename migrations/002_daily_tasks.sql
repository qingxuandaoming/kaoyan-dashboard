-- ============================================================================
-- 002_daily_tasks.sql — 每日任务表（2026-09-14）
--
-- ⚠️ 本文件是**可读文档**，不会被自动执行。
--    真正执行迁移的是 src/migrate.js 的 run()（幂等，serve.js 启动时调用）。
--    三处必须同源：migrations/*.sql（文档）+ migrate.js（执行）+ schema.sql（新库）。
--
-- 为什么用表而不是 JSON 文件：
--   需要按「日期 × 来源 × 删除态」查历史，且大盘 UI 与 agent 都要消费。
--   config 那种 key/value 结构装不下列表。
--
-- 为什么 deleted 是软删除：
--   用户删掉的任务必须留痕。agent 事后要能读到「他删了什么、删的是我布置的
--   还是自己加的」，据此调整后续布置——硬删除会让这个信号永久丢失。
--
-- ext_key 的作用：
--   sha1(task_date | 归一化text)，只对 agent 生成的任务赋值，配合唯一索引实现
--   「同一天重复生成不产生重复行」。user 任务为 NULL——SQLite 的唯一索引允许
--   多个 NULL，所以两类任务互不干扰。
--
-- 兼容性：只加表与索引，不改动任何既有列、类型或表名。
-- ============================================================================

CREATE TABLE IF NOT EXISTS daily_tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_date  TEXT NOT NULL,                  -- YYYY-MM-DD，本地日期（任务归属日）
    text       TEXT NOT NULL,                  -- 任务正文
    source     TEXT NOT NULL DEFAULT 'agent',  -- 'agent'（我布置的）| 'user'（他自己加的）
    subject    TEXT,                           -- 408/政治/数学一/英语一；NULL = 综合
    done       INTEGER NOT NULL DEFAULT 0,     -- 0/1，完成状态持久化
    done_at    TEXT,                           -- 完成时刻（本地时间）
    deleted    INTEGER NOT NULL DEFAULT 0,     -- 1 = 软删除，行仍保留
    deleted_at TEXT,
    ext_key    TEXT,                           -- agent 任务幂等键；user 任务为 NULL
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_daily_tasks_date
    ON daily_tasks(task_date);

CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_tasks_ext_key
    ON daily_tasks(ext_key);

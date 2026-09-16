-- ============================================================================
-- 003_explain_log.sql — 错题解析与追问留痕（2026-09-14）
--
-- ⚠️ 本文件是**可读文档**，不会被自动执行。
--    真正执行迁移的是 src/migrate.js 的 run()（幂等，serve.js 启动时调用）。
--    三处必须同源：migrations/*.sql（文档）+ migrate.js（执行）+ schema.sql（新库）。
--
-- 为什么要留痕而不是「生成完就丢」：
--   1. 这些问答是后续**完善笔记**的一手素材——错在哪、当时怎么想的、后来怎么问的，
--      比笔记里那句干巴巴的结论有用得多。文字不占地方。
--   2. 同一道题再次答错时，可以直接复用上次的解析，不必重复烧 token。
--   3. 周定时任务要汇总本周错题，靠这张表取数（刻意不每天做——生成时就已经
--      拿到了该拿的信息，再每天跑一遍是浪费）。
--
-- 为什么用 thread_id 而不是一次一行：
--   追问是多轮的，一次「答题 → 解析 → 我追问 → 再答」是一条线索。
--   同一道题不同次作答也分属不同线索，所以要显式分组，不能按 question_id 聚合。
--
-- 兼容性：只加表与索引，不改动任何既有列、类型或表名。
-- ============================================================================

CREATE TABLE IF NOT EXISTS explain_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   TEXT NOT NULL,              -- 一次问答线索（同一题同一次作答）
    question_id TEXT,                       -- 关联 questions.id
    card_id     INTEGER,                    -- 关联 cards.id（可能为空）
    subject     TEXT,                       -- 冗余存一份科目，周报按科聚合时不必再 join
    topic_id    TEXT,
    chosen      TEXT,                       -- 学生选的（选项字母 'C' 或作答文本）——解析要针对它
    correct     TEXT,                       -- 正确答案
    role        TEXT NOT NULL,              -- 'user' | 'assistant'
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_explain_thread  ON explain_log(thread_id);
CREATE INDEX IF NOT EXISTS idx_explain_created ON explain_log(created_at);
CREATE INDEX IF NOT EXISTS idx_explain_qid     ON explain_log(question_id);

# -*- coding: utf-8 -*-
# 补丁 2：四个 note-taking skill 飞书同步改本地管道 + math 去 SkillManage + english 补触发 + vocab-graph 定锚
S = r"C:\Users\92534\.qoderworkcn\skills"

EDITS = [
    # ---------- 408-note-taking ----------
    {
        "file": S + r"\408-note-taking\SKILL.md",
        "replacements": [
            {"old": "version: 2.1.0", "new": "version: 2.2.0"},
            {
                "old": "- → 飞书同步：[exam-note-dashboard Skill] — 草稿箱元数据自动汇总至飞书多维表格看板",
                "new": "- → 索引与大盘：[exam-note-dashboard Skill] — 草稿箱元数据汇总至 notes_index.json → 本地大盘",
            },
            {
                "old": "## 飞书看板同步\n\n- 每次整理笔记后运行 `build_index.py` 重建索引\n- 用 `lark-cli base +record-upsert` 增量同步到飞书\n- 详细流程见 [exam-note-dashboard Skill]",
                "new": "## 索引与本地大盘同步\n\n- 每次整理笔记后运行 `python C:\\Users\\92534\\Desktop\\考研\\src\\build_index.py` 重建索引（或一键 `run_pipeline.py`）\n- 本地大盘：`src/dashboard.html`（2026-08-03 起飞书大盘已迁移至本地，无需 lark 同步）\n- 详细流程见 [exam-note-dashboard Skill]",
            },
        ],
    },
    # ---------- math-one-note-taking ----------
    {
        "file": S + r"\math-one-note-taking\SKILL.md",
        "replacements": [
            {"old": "install_method: upload", "new": "version: 1.2.0"},
            {
                "old": "涵盖：公式速查、推导链记录、计算陷阱库、题型通法、错题归档、跨科综合、双链规则、先答后整机制。",
                "new": "涵盖：公式速查、推导链记录、计算陷阱库、题型通法、错题归档、跨科综合、双链规则、先答后整机制。当工作目录为 C:\\Users\\92534\\Desktop\\考研\\Math 时自动触发。",
            },
            {
                "old": "> 每次处理数一任务前，必须先调用 SkillManage 读取本 SKILL 完整内容，不可凭历史上下文执行。详细模板见 [reference-templates.md](reference-templates.md)。",
                "new": "> 每次处理数一任务前，必须先完整读取本 SKILL 内容，不可凭历史上下文执行。详细模板见 [reference-templates.md](reference-templates.md)。",
            },
            {
                "old": "→ 飞书同步：[exam-note-dashboard Skill] — 草稿箱元数据自动汇总至飞书多维表格看板",
                "new": "→ 索引与大盘：[exam-note-dashboard Skill] — 草稿箱元数据汇总至 notes_index.json → 本地大盘",
            },
            {
                "old": "### 飞书大盘同步\n\n- 每次整理笔记后运行 `build_index.py` 重建索引\n- 用 `lark-cli base +record-upsert` 增量同步到飞书多维表格\n- 详细流程见 exam-note-dashboard Skill",
                "new": "### 索引与本地大盘同步\n\n- 每次整理笔记后运行 `python C:\\Users\\92534\\Desktop\\考研\\src\\build_index.py` 重建索引（或一键 `run_pipeline.py`）\n- 本地大盘：`src/dashboard.html`（2026-08-03 起飞书大盘已迁移至本地）\n- 详细流程见 exam-note-dashboard Skill",
            },
        ],
    },
    # ---------- politics-note-taking ----------
    {
        "file": S + r"\politics-note-taking\SKILL.md",
        "replacements": [
            {"old": "version: 1.0.0", "new": "version: 1.1.0"},
            {
                "old": "## 飞书大盘同步\n\n- 每次整理笔记后运行 `build_index.py` 重建索引\n- 用 `lark-cli base +record-upsert` 增量同步到飞书多维表格\n- 详细流程见 **exam-note-dashboard** Skill",
                "new": "## 索引与本地大盘同步\n\n- 每次整理笔记后运行 `python C:\\Users\\92534\\Desktop\\考研\\src\\build_index.py` 重建索引（或一键 `run_pipeline.py`）\n- 本地大盘：`src/dashboard.html`（2026-08-03 起飞书大盘已迁移至本地）\n- 详细流程见 **exam-note-dashboard** Skill",
            },
        ],
    },
    # ---------- english-note-taking ----------
    {
        "file": S + r"\english-note-taking\SKILL.md",
        "replacements": [
            {"old": "install_method: upload", "new": "version: 1.1.0"},
            {
                "old": "description: 考研英语笔记整理工作流。当用户在英语项目（语法、词汇、词义辨析、作文句式等）中提问任何知识点时，自动回答并整理到对应笔记。涵盖语法笔记结构、词汇辨析格式、作文积累规则、双链规则、分级整理、汇报规范。",
                "new": "description: 考研英语笔记整理工作流。当用户在英语项目（语法、词汇、词义辨析、作文句式等）中提问任何知识点，或工作目录为C:\\Users\\92534\\Desktop\\考研\\English时，自动回答并整理到对应笔记。涵盖语法笔记结构、词汇辨析格式、作文积累规则、双链规则、分级整理、汇报规范。",
            },
            {
                "old": "→ 飞书同步：[exam-note-dashboard Skill] — 草稿箱元数据自动汇总至飞书多维表格看板",
                "new": "→ 索引与大盘：[exam-note-dashboard Skill] — 英语元数据暂走独立数据源（vocab_database.json + 辨析目录），本地大盘见 src/dashboard.html",
            },
            {
                "old": "## 飞书大盘同步\n\n### 英语笔记表（独立设计，预留拆分）\n- Base token: `IK92bxLZoa0pFysqYNzcwjANnLW`\n- Table: \"英语学习笔记\" (`tblUos6NIzNtrp3L`)\n- 该表设计为独立结构，日后可直接拆分为单独的 Bitable Base\n\n### 字段结构\n| 字段 | 类型 | 说明 |\n|------|------|------|\n| ID | text | ENG-{type}-{seq} 格式 |\n| 日期 | datetime | 学习/整理日期 |\n| 标题 | text | 条目标题 |\n| 类型 | select | 单词/词义辨析/词义辨析组/长难句/语法/作文批改/翻译练习/句型积累/俚语搭配/易混词/短语 |\n| 子类别 | text | 子分类 |\n| 来源文件 | text | 原始文件名 |\n| 内容摘要 | text | 100字以内摘要 |\n| 级别 | select | L1/L2/L3/L4 |\n| 状态 | select | 学习中/已整理/已掌握/待复习 |\n| 标签 | text | 逗号分隔标签 |\n| 考研关联 | checkbox | 是否考研专用 |\n| 备注 | text | 补充说明 |\n\n### ID 前缀规范\n| 前缀 | 类型 | 来源 |\n|------|------|------|\n| ENG-WORD | 单词 | vocab_database.json |\n| ENG-DIST | 词义辨析 | vocab_database.json |\n| ENG-CYB | 词义辨析组 | 辨析/第XX节_*.md |\n| ENG-LSN | 长难句 | 长难句笔记.md |\n| ENG-ESS | 作文批改 | *-批改记录.md |\n| ENG-TRN | 翻译练习 | 翻译真题精讲.md |\n| ENG-ACC | 句型积累 | 积累.md |\n| ENG-SLANG | 俚语搭配 | 俚语与固定搭配.md |\n| ENG-CONF | 易混词 | 易混词辨析.md |\n\n### 视图\n- 按类型分组 / 词汇库 / 长难句与语法 / 写作与翻译 / 考研专区\n\n### 独立拆分指南\n日后如需将英语笔记拆分为独立 Base：\n1. 在飞书创建新 Base\n2. 使用 `lark-cli base +table-create` 重建表结构\n3. 使用 `lark-cli base +record-list` 导出原表数据\n4. 使用 `lark-cli base +record-batch-create` 导入新表\n5. 重建视图和大盘组件",
                "new": "## 附录：飞书英语笔记表（已停用，历史备查）\n\n2026-08-03 起学习大盘迁移至本地（`src/dashboard.html`），飞书英语笔记表不再日常同步。\n\n- Base token: `IK92bxLZoa0pFysqYNzcwjANnLW`，Table \"英语学习笔记\" (`tblUos6NIzNtrp3L`)\n- ID 前缀：ENG-WORD/DIST/CYB/LSN/ESS/TRN/ACC/SLANG/CONF\n- 英语元数据暂不接入 notes_index.json 主管道，数据源为 `vocab_database.json` 与 `辨析/` 目录（见 vocab-graph skill）",
            },
        ],
    },
    # ---------- vocab-graph 定锚 ----------
    {
        "file": S + r"\vocab-graph\SKILL.md",
        "replacements": [
            {
                "old": "vocab-graph/                      ← 项目根目录（位于用户工作区）",
                "new": "vocab-graph/                      ← 项目根目录：C:\\Users\\92534\\Desktop\\考研\\English\\word&phrase\\vocab-graph\\",
            },
            {
                "old": "```bash\ncd vocab-graph\npython scripts/generate_obsidian.py\n```",
                "new": "```bash\ncd C:\\Users\\92534\\Desktop\\考研\\English\\word&phrase\\vocab-graph\npython scripts/generate_obsidian.py\n```",
            },
        ],
    },
]

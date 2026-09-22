---
name: exam-note-dashboard
description: 考研/考试笔记元数据管道与本地学习大盘构建。当用户需要：整理草稿箱为结构化索引、生成笔记统计数据、更新本地可视化大盘、维护笔记→索引→大盘的数据管道时触发。适用于任何有"草稿箱→章节笔记"体系的考试复习场景。
version: 2.0.0
---

# 考试笔记管道与本地大盘

将"草稿箱 → 章节笔记"的笔记体系升级为可量化、可视化的学习数据管道。

> **变更记录 2026-08-03**：学习大盘从飞书多维表格**迁移至本地**（`src/dashboard.html`），飞书同步不再是日常流程；temp 机制同期取消。飞书历史配置保留在文末附录备查。

## 数据管道总览

```
各科 notes_index.json ──build_index.py──▶ src/笔记索引.yaml
                                                │
knowledge_graph/*.json ─┐                       ▼
progress.json ──────────┴─▶ gap_analysis.py ──▶ 覆盖率/缺口
                                                ▼
                              daily_planner.py ──▶ schedule/daily/plan_YYYY-MM-DD.md
                                                ▼
                            generate_dashboard.py ──▶ src/dashboard.html（本地大盘）
```

一键运行：`python E:\NPEE\src\run_pipeline.py`

## 前置条件

- 各科 `notes_index.json` 已建立（`Math/`、`408/`、`Politics/` 各一份，嵌套 `subjects.{子科}.entries[]` 结构）
- Python 环境可用（PyYAML 已安装，`generate_dashboard.py` 依赖；缺失时 `pip install pyyaml`）

## Step 1：维护各科 notes_index.json（元数据单一事实源）

整理笔记后，把条目元数据追加到对应科目的 `notes_index.json`，结构：

```json
{
  "subjects": {
    "DS": {
      "sub": "数据结构",
      "entries": [
        {"id": "408-DS-001", "date": "2026-07-11", "title": "知识点名称",
         "chapter": "第1章", "level": "L2", "status": "已整理",
         "tags": ["标签1", "标签2"],
         "links": [{"text": "第1章_xxx.md > 小节", "path": "./第1章_xxx.md"}]}
      ]
    }
  }
}
```

新增条目追加到对应 `subjects.{子科}.entries[]` 末尾，id 续号（追加前必查当前最大值）。

**级别判定规则**：
- L3：错题/真题/证明题/综合题
- L2：概念辨析/题型通法/易混淆点
- L1：单一公式/简单技巧/基础概念

**英语**：目前不入主管道（无 notes_index.json），数据源为 `English/word&phrase/vocab-graph/data/vocab_database.json` 与 `辨析/` 目录，独立演进。

> **兼容回退**：无 JSON 的科目可在 notes.md 条目上用 HTML 注释元数据块（`build_index.py` 会解析），格式：

```html
<!-- note-meta:entry
id: {科目代码}-{序号}
date: YYYY-MM-DD
title: 条目标题
subject: 科目名
sub: 子科目名
chapter: 第X章
level: L1/L2/L3
status: 已整理/未整理/待整理
tags: [标签1, 标签2, ...]
-->
```

## Step 2：重建索引

```bash
python E:\NPEE\src\build_index.py
```

聚合三份 JSON 生成 `src/笔记索引.yaml`，含 stats（各科 total/organized/coverage/L1-L3 分布）、timeline、coverage、entries。运行后检查输出统计摘要与预期一致。

## Step 3：生成本地大盘

```bash
python E:\NPEE\src\gap_analysis.py
python E:\NPEE\src\generate_dashboard.py
```

- `gap_analysis.py`：知识图谱 + 笔记索引 + progress.json → 各科覆盖率与优先缺口排名
- `generate_dashboard.py`：生成 `E:\NPEE\src\dashboard.html`，浏览器直接打开

**验证**：dashboard.html 中各科掌握度、笔记统计、缺口排名与 progress.json / 笔记索引.yaml 一致。

## 日常维护节奏

| 时机 | 动作 |
|------|------|
| 每次整理笔记后 | notes_index.json 追加条目（note-taking skill 已内置此要求） |
| 每晚 23:00 晚间回顾 | kaoyan-evening-review 自动重跑管道并刷新本地大盘 |
| 用户主动同步进度 | kaoyan-progress-sync 更新 progress.json 后重跑管道 |
| 每周初 | 运行 `python src\tools\audit_notes.py` 体检笔记断链 |

## Pitfalls

1. **单一事实源**：元数据只写 notes_index.json，不要在 .md 里手写 note-meta 块（兼容回退仅供遗留数据）
2. **id 续号**：追加前必查最大编号，禁止中间插入（需插入则重排）
3. **管道顺序**：build_index → gap_analysis → daily_planner → generate_dashboard 有数据依赖，必须串行；推荐直接用 `run_pipeline.py`
4. **PyYAML 缺失**：generate_dashboard.py 会失败，`pip install pyyaml` 解决
5. **进度新鲜度**：progress.json 的 `updated` 若不是当天，daily_planner 的完成率会失真（先走 kaoyan-progress-sync 或晚间回顾 Step 0）

---

## 附录：飞书多维表格（已废弃，历史备查）

2026-08-03 起大盘迁移本地，以下飞书配置不再日常使用，仅供日后恢复或导出数据时参考：

- 笔记条目 Base token：见原飞书"考试复习大盘"
- 英语笔记表 Base：`IK92bxLZoa0pFysqYNzcwjANnLW`，Table `tblUos6NIzNtrp3L`（"英语学习笔记"，12 字段，ID 前缀 ENG-WORD/DIST/CYB/LSN/ESS/TRN/ACC/SLANG/CONF）
- 历史命令：`lark-cli base +record-upsert` / `+record-batch-create`（单次最多 200 行；`--json @file` 必须相对路径；datetime 格式 `YYYY-MM-DD HH:MM:SS`；rollup 只接受 sum）

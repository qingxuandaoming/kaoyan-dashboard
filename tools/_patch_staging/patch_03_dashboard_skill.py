# -*- coding: utf-8 -*-
# 更新 exam-note-dashboard skill：大盘定位收敛 + 闪卡集中 + 针对性出题
F = r"C:/Users/92534/.qoderworkcn/skills/exam-note-dashboard/SKILL.md"

EDITS = [
    {
        "file": F,
        "replacements": [
            {
                "old": "description: 考研/考试笔记元数据管道与本地学习大盘构建。当用户需要：整理草稿箱为结构化索引、生成笔记统计数据、更新本地可视化大盘、维护笔记→索引→大盘的数据管道时触发。适用于任何有\"草稿箱→章节笔记\"体系的考试复习场景。\nversion: 2.0.0",
                "new": "description: 考研/考试笔记元数据管道与本地学习大盘构建（含嵌入式闪卡练习）。当用户需要：整理草稿箱为结构化索引、生成笔记统计数据、更新本地可视化大盘、维护笔记→索引→大盘的数据管道、针对性生成闪卡时触发。适用于任何有\"草稿箱→章节笔记\"体系的考试复习场景。\nversion: 2.1.0",
            },
            {
                "old": "> **变更记录 2026-08-03**：学习大盘从飞书多维表格**迁移至本地**（`src/dashboard.html`），飞书同步不再是日常流程；temp 机制同期取消。飞书历史配置保留在文末附录备查。",
                "new": "> **变更记录 2026-08-03**：学习大盘从飞书多维表格**迁移至本地**（`src/dashboard.html`），飞书同步不再是日常流程；temp 机制同期取消。飞书历史配置保留在文末附录备查。\n> **变更记录 2026-08-06**：大盘定位收敛——只监测笔记数据，不再展示每日任务完成率（用户有自己的计划）；闪卡集中到大盘（嵌入式练习区，评分即时 FSRS 回写）；薄弱提醒改由真实答题错误驱动，无笔记但答对过的考点不再提醒（三态判定）；新增针对性出题脚本与桌面一键启动。",
            },
            {
                "old": "                            generate_dashboard.py ──▶ src/dashboard.html（本地大盘）\n```",
                "new": "                            generate_dashboard.py ──▶ src/dashboard.html（本地大盘）\n                                                │      + src/dashboard_data.json（选题辅助）\nquestion_bank.db（FSRS 题库）──serve.js API──▶ 大盘内嵌闪卡练习（评分即时回写）\ngenerate_targeted_cards.py ──▶ 按薄弱点/近日笔记/待验证考点针对性出题入库\n```",
            },
            {
                "old": "一键运行：`python C:\\Users\\92534\\Desktop\\考研\\src\\run_pipeline.py`",
                "new": "一键运行：`python C:\\Users\\92534\\Desktop\\考研\\src\\run_pipeline.py`（`--no-plan` 跳过每日计划生成）\n\n打开大盘：双击桌面 `启动考研大盘.bat`（刷新数据 → 启动 serve.js → 打开 http://localhost:8080/dashboard.html）",
            },
            {
                "old": "- `gap_analysis.py`：知识图谱 + 笔记索引 + progress.json → 各科覆盖率与优先缺口排名\n- `generate_dashboard.py`：生成 `C:\\Users\\92534\\Desktop\\考研\\src\\dashboard.html`，浏览器直接打开\n\n**验证**：dashboard.html 中各科掌握度、笔记统计、缺口排名与 progress.json / 笔记索引.yaml 一致。",
                "new": "- `gap_analysis.py`：知识图谱 + 笔记索引 + progress.json → 各科覆盖率与优先缺口排名\n- `generate_dashboard.py`：生成 `src/dashboard.html` + `src/dashboard_data.json`（serve.js 选题辅助：近日笔记前缀、薄弱知识点 ID）\n\n**大盘定位（2026-08-06 用户决策，务必遵守）**：\n- 只监测笔记数据：倒计时 / 笔记总数 / 覆盖率 / 热力图 / 增长趋势 / 级别分布\n- **不展示每日任务完成率**——用户有自己的计划，大盘监测不全每日成果，手动回报的进度也不可靠\n- 薄弱提醒由真实答题错误驱动（cards.lapses / review_log rating<=2），不做主观评判\n- 缺口三态判定：有笔记 → 正常；无笔记但闪卡答对过且近14天无错 → 已验证掌握，**不再提醒**；其余 → 真缺口\n\n## Step 4：闪卡练习与针对性出题\n\n大盘内嵌闪卡练习区，通过 `node src/serve.js` 的 API 工作（桌面一键启动脚本已包含）：\n- `GET /api/flashcards/session`：选题优先级 薄弱卡（lapses/近期错）> 到期卡 > 近日笔记相关新卡\n- `POST /api/flashcards/review`：评分（1忘记/2模糊/3记得/4简单）即时 FSRS 回写 cards + review_log\n\n针对性出题（盘活笔记，不求全面）：\n\n```bash\npython C:\\Users\\92534\\Desktop\\考研\\src\\generate_targeted_cards.py --count 12\n```\n\n素材三类：① 闪卡错误点 → 强化卡；② 近7天修改的笔记 → 盘活卡；③ 高分考点无卡 → 验证卡（答对即视为掌握，对应用户\"内容简单不整理笔记\"的场景）。依赖已登录的 `bl` CLI（qwen3.7-max 出题）。\n\n**验证**：大盘指标卡、薄弱列表、缺口排名与数据源一致；闪卡评分后 review_log 有新记录、cards.due_date 按 FSRS 更新。",
            },
            {
                "old": "| 每周初 | 运行 `python src\\tools\\audit_notes.py` 体检笔记断链 |",
                "new": "| 每周初 | 运行 `python src\\tools\\audit_notes.py` 体检笔记断链 |\n| 每次看大盘 | 顺手在闪卡练习区刷一组（薄弱优先），评分自动回写 |\n| 薄弱信号积累后 | 运行 `generate_targeted_cards.py` 针对性补题 |",
            },
            {
                "old": "5. **进度新鲜度**：progress.json 的 `updated` 若不是当天，daily_planner 的完成率会失真（先走 kaoyan-progress-sync 或晚间回顾 Step 0）",
                "new": "5. **进度新鲜度**：progress.json 的 `updated` 若不是当天，daily_planner 的完成率会失真（先走 kaoyan-progress-sync 或晚间回顾 Step 0）\n6. **大盘职责边界**：永远不要给大盘加\"每日任务完成率/打卡监督\"类模块（用户明确决策）；薄弱判定只来自笔记缺失 + 答题错误\n7. **闪卡评分走 API**：评分必须经 serve.js 的 `/api/flashcards/review` 回写，不要直接改 question_bank.db（FSRS 状态会不一致）\n8. **FSRS 双实现**：serve.js（JS）与 fsrs_scheduler.py（Python）公式参数相同，改调度逻辑时两边必须同步改",
            },
        ],
    },
]
DELETES = []

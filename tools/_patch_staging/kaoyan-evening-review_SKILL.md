---
name: kaoyan-evening-review
description: 考研每日晚间回顾完整工作流：先查飞书打卡同步进度、运行 daily_planner.py 回顾今日 + 预排明日、更新本地大盘、创建飞书可打钩任务、发任务给知墨、发送晚间摘要到飞书。当用户说"晚间回顾""今日总结""晚上回顾""晚间复盘"或每日23:00定时任务触发时使用。
version: 2.0.0
---

# 考研每日晚间回顾工作流

## 触发条件

- 用户手动说"晚间回顾""今日总结""晚上复盘"
- 定时任务 `每日回顾与预排`（每日 23:00）自动触发

## 前置条件

- `C:\Users\92534\Desktop\考研\src\daily_planner.py` 存在且支持 `--review` 和 `--date tomorrow`
- `C:\Users\92534\Desktop\考研\src\generate_dashboard.py` 存在（本地大盘）
- lark-cli 已登录（`lark-cli auth status` 验证）
- 飞书任务清单 guid: `3e86d27d-bf16-4a36-aadb-87f546fece88`
- 用户 open_id: `ou_74764c33e2de854fc37312dc1de44000`
- 用户飞书私聊 chat_id: `oc_63eab3003bd7ba6479812dfa882005ae`
- 知墨（OpenClaw）飞书 chat_id: `oc_47366f1ad42feb7ee2f36f331fd0441d`

> **变更记录 2026-08-03**：temp 文件夹机制已取消（read_temp.py 停用）；学习大盘已从飞书多维表格迁移至本地（`src/dashboard.html`）。本工作流随之精简。

## Step 0：检查飞书打卡并同步进度（最重要！）

**在运行 daily_planner.py 之前，必须先查飞书任务完成情况！**

1. 查询今日飞书任务完成状态：
   ```bash
   lark-cli task +get-my-tasks --due-start "YYYY-MM-DD" --due-end "YYYY-MM-DDT23:59:59+08:00" --as user
   ```

2. 解析返回的任务列表，提取每个任务的 `completed`、`completed_at`、`summary` 字段。

3. 根据已完成任务的标题，映射到 progress.json 的对应科目/章节，更新进度：
   - `【408】` 开头的任务 → 更新 408 相关章节（如 DS/CO/OS/CN 的 chapters_completed）
   - `【数学】` 开头的任务 → 更新数学相关章节（如高等数学的 lectures_completed）
   - `【政治】` 开头的任务 → 更新政治相关课程/刷题进度
   - `【英语】` 开头的任务 → 更新英语词汇量/阅读进度
   - `【闪卡】` 开头的任务 → 更新闪卡复习记录
   - `【词汇】` 开头的任务 → 更新 vocabulary_size

4. 更新 `progress.json` 中的 `updated` 字段为今天日期。

5. 将完成情况汇总，供后续步骤使用（哪些完成了、哪些没完成、各科实际完成率）。

**映射规则示例**：
- `【408】DS-04 树和二叉树` → subjects.408.subs.DS.chapters_completed 添加对应章节号
- `【数学】高等数学-02 一元函数微分学` → subjects.数学.subs.高等数学.lectures_completed +1
- `【词汇】新学50词` → subjects.英语.vocabulary_size +50

**注意**：
- 只更新 `completed: true` 的任务对应的进度，未完成的不动
- 如果飞书上没有今日任务（可能是第一次运行或任务未创建），跳过此步骤

## Step 1：生成回顾与明日计划

按顺序执行（串行，有数据依赖）：

```bash
python C:\Users\92534\Desktop\考研\src\daily_planner.py --review
python C:\Users\92534\Desktop\考研\src\daily_planner.py --date tomorrow
python C:\Users\92534\Desktop\考研\src\generate_dashboard.py
```

从 stdout 提取关键数据：
- 今日计划目标数 / 已完成数 / 完成率
- 各科掌握度（408/数学/政治/英语）
- 闪卡复习统计（今日复习数/正确率）
- 明日到期闪卡数

> 本地大盘输出：`C:\Users\92534\Desktop\考研\src\dashboard.html`（浏览器直接打开，无需飞书）

## Step 2：当日零散知识点归档（如对话中有）

当日对话中产生的知识点，应已在对话时由对应 note-taking skill 即时归档（408-note-taking / math-one-note-taking / politics-note-taking / english-note-taking）。

本步骤仅做核对：
1. 回顾今日对话中是否还有未归档的知识点/错题，有则调用对应 skill 补归档。
2. 有值得长期参考的图片（手写推导、教材截图），归档到对应科目 `assets/` 并在笔记中引用；纯文字截图无需保留。
3. 收集用户今日在对话中表达的「困惑/疑问」，供 Step 4 发给知墨（不再有 temp 文件夹投递渠道）。

## Step 3：创建明日飞书任务

读取明日计划文件：`C:\Users\92534\Desktop\考研\schedule\daily\plan_YYYY-MM-DD.md`

从计划中提取各科具体学习任务，为每个任务创建飞书任务：

```bash
lark-cli task +create \
  --summary "【科目】具体内容（时长）" \
  --description "详细描述" \
  --assignee ou_74764c33e2de854fc37312dc1de44000 \
  --due YYYY-MM-DD \
  --tasklist-id 3e86d27d-bf16-4a36-aadb-87f546fece88 \
  --as user
```

任务标题格式示例：
- 【数学】一元函数微分学（张宇30讲，3.4h）
- 【408】DS 树和二叉树 + 图（王道，1.7h）
- 【政治】毛泽东思想 + 世界的物质性（1.3h）
- 【英语】阅读真题精练 + 翻译英译汉（1.7h）
- 【闪卡】复习190张到期卡片
- 【词汇】新学50词 + 复习100词

**重要**：由于 Windows 下 Node.js spawnSync 对中文参数有编码问题，必须通过 Node.js 脚本调用 lark-cli：

```javascript
const { execFileSync } = require('child_process');
const npmDir = 'C:\\Users\\92534\\AppData\\Roaming\\npm';
const runScript = npmDir + '\\node_modules\\@larksuite\\cli\\scripts\\run.js';

execFileSync('node', [
  runScript,
  'task', '+create',
  '--summary', '任务标题',
  '--description', '任务描述',
  '--assignee', 'ou_74764c33e2de854fc37312dc1de44000',
  '--due', '2026-07-13',
  '--tasklist-id', '3e86d27d-bf16-4a36-aadb-87f546fece88',
  '--as', 'user'
], { encoding: 'utf-8', timeout: 30000 });
```

## Step 4：发送任务和建议给知墨

通过飞书发消息给知墨（chat_id: `oc_47366f1ad42feb7ee2f36f331fd0441d`，陈冠衡的飞书CLI）：

内容结构：
1. **明日学习任务清单**：列出所有已创建的飞书任务，知墨可据此督促用户
2. **今日困惑汇总**：Step 2 收集的疑问和困惑，知墨可帮助解答或提醒
3. **学习建议**：基于差距分析给出的优先学习建议
4. **督促提醒**：鼓励性语言，提醒用户保持节奏

语气友好、鼓励为主。知墨有心跳机制，收到后会自动督促用户。

## Step 5：发送飞书晚间摘要给用户

通过飞书 IM 发送消息给用户（chat_id: `oc_63eab3003bd7ba6479812dfa882005ae`）：

摘要内容结构：
1. **今日各科完成率**：列出 408/数学/政治/英语 各自的完成百分比
2. **闪卡统计**：今日复习张数 + 正确率
3. **今日归档回顾**：今日通过 note-taking skill 整理了哪些知识点到哪些科目
4. **明日重点预览**：各科主要任务和时间分配
5. **差距变化提醒**：哪科落后最多、需要加强的知识点
6. **连续未完成警告**：如果某科连续 2 天以上完成率为 0%，标注 ⚠️ 警告

同样通过 Node.js 脚本发送（使用 `im +messages-send`）。

## Step 6：汇报结果

向用户简要汇报：
- 已生成回顾文件路径
- 今日归档核对结果（是否有补归档）
- 已创建飞书任务数量
- 已发送任务给知墨
- 已发送飞书摘要
- 本地大盘路径 `src/dashboard.html`
- 关键提醒（如连续未完成科目）

## 与其他 Skill 的关系

- `kaoyan-progress-sync`：负责用户主动汇报进度时更新 progress.json，与本 skill 互补
- `generate_dashboard.py`：本地大盘生成（原飞书多维表格大盘已于 2026-08-03 迁移至本地）
- `408-note-taking` / `math-one-note-taking` / `politics-note-taking` / `english-note-taking`：对话中即时归档知识点，本 skill 只做当日核对
- `daily_planner.py`：核心脚本，本 skill 负责调用和解析输出

## Pitfalls

- **最重要：必须先查飞书打卡再跑 daily_planner.py！** 用户在飞书上勾选任务完成，但 progress.json 不会自动更新。如果跳过 Step 0，daily_planner.py 会算出 0% 完成率，导致晚间摘要和明日计划全部不准确。
- `daily_planner.py --date` 支持 `tomorrow`/`yesterday`/`YYYY-MM-DD` 三种格式
- Windows 下 lark-cli 带中文参数必须用 Node.js 脚本调用，不能直接用 Python subprocess
- 如果 progress.json 更新日期为今天，daily_planner.py --review 才能正确读取进度
- 飞书任务创建失败不影响其他步骤，记录错误继续执行
- **temp 机制已于 2026-08-03 取消**：不要再调用 read_temp.py 或创建 temp/ 目录；知识点归档走"对话即时整理"路线（note-taking skill 的先答后整机制）

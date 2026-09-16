# 架构说明

本文描述**考研学习仪表盘**的数据流、模块职责与关键设计决定。
运行方式与踩坑清单见仓库根 `README.md`。

---

## 1. 全局数据流

```
                  ┌─────────────────────────────────────────────┐
                  │  各科笔记目录（不在本仓库）                    │
                  │   408/  English/  Math/  Politics/          │
                  │   *.md  +  note-meta 元数据块                │
                  └───────────────┬─────────────────────────────┘
                                  │
       tools/gen_english_index.py │  (英语笔记是普通 md，没有 meta 块，要先扫)
                                  ▼
                        <科>/notes_index.json          ← 元数据「单一事实源」
                                  │
                    build_index.py│
                                  ▼
                        src/笔记索引.yaml               ← 派生产物（不入库）
                                  │
        ┌─────────────────────────┼──────────────────────────┐
        │                         │                          │
        ▼                         ▼                          ▼
  gap_analysis.py          daily_planner.py           (被大盘直接读取)
  知识图谱 + 索引            生成明日计划
  + progress.json           schedule/daily/*.md
        │
        ▼
  coverage_gap / 缺口排名
        │
        └──────────────┬───────────────────────────────────┘
                       ▼
              generate_dashboard.py
                       │
                       ▼
                 src/dashboard.html      ← 单文件大盘（产物，不入库）

  ─────────────────────────────────────────────────────────────────

  review_store 侧（错题复盘，文件式存储）
    Review/<科目>/sessions/<sid>/  ──build_review_patterns.py──▶ 错因画像
                                                                    │
  题库侧（SQLite）                                                    │
    question_bank.db ──▶ fsrs_scheduler.py / fsrs_core.js ──▶ 出卡 ──┤
                                                                    ▼
                                                            dashboard.html
```

**关键点：** 元数据的事实源是各科 `notes_index.json`，`笔记索引.yaml` 永远是派生物。
所以流水线里 `gen_english_index.py` 必须排在 `build_index.py` **之前** ——
否则英语读不到，大盘英语 15 格会重新变回全 0。

---

## 2. 模块职责

### 2.1 流水线（Python）

| 模块 | 职责 | 输入 → 输出 |
|---|---|---|
| `run_pipeline.py` | 按依赖顺序编排，失败不中断，末尾汇总 | — → 各产物 |
| `tools/gen_english_index.py` | 英语 md 扫成索引 | `English/**.md` → `English/notes_index.json` |
| `build_index.py` | 汇总各科索引 | `*/notes_index.json` → `笔记索引.yaml` |
| `gap_analysis.py` | 覆盖率与优先缺口排名 | 知识图谱 + 索引 + `progress.json` → 缺口数据 |
| `build_review_patterns.py` | 错因画像 | `Review/**/errors.json` → 画像（大盘复/学/首页都读它） |
| `daily_planner.py` | 明日计划 | 缺口 + 进度 + 日程 → `schedule/daily/plan_*.md` |
| `english_coverage.py` | **英语覆盖判定的唯一实现** | 题库 + 索引读数 |
| `generate_dashboard.py` | 生成大盘单文件 | 上述全部 → `dashboard.html` |

> `english_coverage.py` 被刻意独立出来：覆盖判定逻辑只能有一份，在多处重写必然对不上。

### 2.2 本地服务（Node）

| 模块 | 职责 |
|---|---|
| `serve.js` | HTTP 服务：静态白名单 + `/tools/katex/` 离线依赖 + 全部 `/api` |
| `grade_llm.js` | 简答题 AI 判分（Key 只在服务端，绝不下发、也不写进 `dashboard.html`） |
| `fsrs_core.js` | `cards` 行 ↔ `ts-fsrs` Card 结构互转 |
| `review_store.js` | 错题复盘的会话读写 |
| `migrate.js` | 建表与迁移 |

⚠️ `fsrs_core.js` 与 `fsrs_scheduler.py` 是同一套算法的两份实现，**必须同步改**。

### 2.3 前端

全部前端都编译进 `dashboard.html` 一个文件，由 `generate_dashboard.py` 里的模板常量拼装，
注入顺序固定：

```
FLASH → REVIVE → NOTEQ → TASK → SETTINGS → POMO → SHELL → MR → RV → NAV
```

- `__pageRenderers` 的 JS 必须在 `NAV_JS` 之前。
- 各模块是**独立 IIFE**，跨 IIFE 只能通过 `window` 传递（`escHtml` / `mdInline` / `richText` / `splitMath` /
  `maskMath` / `ensureKatex` 就是这么挂出去的）。忘搭桥会 `ReferenceError`，而调用处的 `catch` 会把它吞掉，
  表现为「功能没生效」而不是报错。

---

## 3. 数据库

`question_bank.db`（SQLite，`node:sqlite` 访问）。核心表：

| 表 | 说明 |
|---|---|
| `topics` | 知识点树 |
| `questions` | 题目（选择题 / 判断 / 填空 / 简答） |
| `cards` | FSRS 卡片状态（`state`/`difficulty`/`stability`/`due_date`/`reps`/`lapses`） |
| `review_log` | 复习流水。**跨设备共用**，日期是 `YYYY-MM-DD HH:MM:SS` 字符串 |
| `config` | 全部设置与运行状态（`pomo_run`/`pomo_log`/`flash_session`/`explain_quick` …） |
| `daily_tasks` | 每日任务 |
| `explain_log` | AI 讲解缓存（复用上次解析靠它，实测 1793ms → 30ms） |
| `schema_version` | 迁移版本 |

`migrate.js` 另建：`mr_checkin`（早间打卡）、`mr_sr`（早间间隔重复）、`note_qa`（读笔记提问记录）。

> ⚠️ 该库**不入 git**：每次刷闪卡都会写，属于数据而非代码。备份放 `backups/`。

---

## 4. API 分组

约 50 个 `/api/*` 端点，按域分组：

| 域 | 端点（举例） |
|---|---|
| 闪卡 | `/api/flashcards/{today,review,session,position,undo,suspend,stats,facets}`、`/api/flashcard-sync`、`/api/grade` |
| 笔记 | `/api/notes/{search,preview,asset,touch,ask,qa}` |
| 错题复盘 | `/api/review/{session,overview,errors,extract,upload,upload-pdf,chat}` |
| 番茄钟 | `/api/pomodoro/{state,credit}` |
| 早间回顾 | `/api/morning-review/{overview,day,queue,checkin,sr}` |
| 任务 | `/api/tasks{,/add,/done,/delete}` |
| 设置 | `/api/settings{,/apikey,/background…}`、`/api/lan-info` |
| AI | `/api/explain{,/followup}`、`/api/study/{chat,context}`、`/api/review/chat`、`/api/ark/chat`、`/api/v1/tts` |

### 闪卡进度同步（服务端为准）

进度存在 `config.flash_session`（cards + idx + filter + device + updated，**跨天作废**），
因为用户会在平板和电脑之间换着刷：

- `GET /api/flashcards/session?peek=1` —— 闸门问「要不要继续上次的进度」
- `GET /api/flashcards/session?resume=1` —— 取回进度，并**剔除今天已答的卡**、修正 idx
- `POST /api/flashcards/session` —— 整份存（新开组 / 切页 / 离开）
- `POST /api/flashcards/position` —— 每翻一张上报，**只前进不后退**

智能组选题会排除 `review_log` 里今天已答的卡 —— 这是「一天内重复问同一张」的堵口。
自选（browse）不过滤，也不顶掉当日进度。

### AI 讲解

- **提示词归用户**：配置项 `prompt_explain` / `prompt_note_qa` 由用户在「设置 → AI 提示词」自己写。
  **用户写了就原样用，不追加任何要求**；内置默认只剩「角色 + 公式格式」。
- **思考强度**：只有 `thinking:{type:'disabled'}` 真管用；`explain_quick`（默认 on）控制，
  面板上的「深想一遍」用 `deep:true` 单次覆盖。笔记提问不关思考。
- **复用上次解析**：`GET /api/explain?question_id&chosen&mode` —— 三个条件都要过滤，
  否则「先错 B → 后错 C → 又错 B」复用不上。
- 答对或看答案时只给**一个空的追问框**，不预先提问、不查缓存。

---

## 5. 安全模型

早期版本把 URL 直接拼成文件路径来读，于是：

- `GET /.secrets.json` → 拿到 API Key 明文
- `GET /question_bank.db` → 直接拖库

**现在的做法：**

1. **静态资源固定白名单**：只有 `/`、`/index.html`、`/dashboard.html` 三个页面。
2. **`/tools/katex/` 专用路由**：`path.resolve` 之后必须仍在 `tools/katex` 内，且扩展名在白名单
   （`js`/`mjs`/`css`/`woff2`/`woff`/`ttf`），其余一律 404。
3. **敏感端点限内网**：吃 Key 或能改配置的路径只放行内网来源（`isPrivateAddress`），
   公网 403（`ALLOW_PUBLIC_AI=1` 可关闭该闸门）。
4. **Key 不出服务端**：下发给前端的一律是 `****尾4位`。
5. **CORS 不再无条件 `*`**。

> 白名单的副作用要留意：它会**顺手打掉前端要用的静态资源**。
> 踩过一次 —— KaTeX 走 `/tools/katex/**` 全 404，闪卡题干与笔记正文的公式全变成源码。

---

## 6. 测试策略

`tools/` 下 11 个 `test_*.js`，用 `node tools/run_all_tests.js`（或 `npm test`）一次跑完。
各文件覆盖范围见 README 的表格。

### ⚠️ 已丢失：策略守卫探针（`check_inline.js`）

曾经有一个 `tools/check_inline.js`，内含约 125 个探针，其中一组是**读源码的策略守卫** ——
不跑行为，而是直接断言 `serve.js` / `grade_llm.js` 的**代码形态**：

- 公网闸门与 CORS 没被放开
- KaTeX 路由存在且有穿越防护
- 思考强度接线正确
- 闪卡进度存在服务端
- 「今天已答不再抽」
- 解析面板不动滚动条
- 音效不再是听不见的低频
- 笔记复用同一套公式逻辑

这类守卫的价值在于：上述性质**没有可观测的失败现象**，只能靠读代码守住；
一旦有人顺手把闸门放开，其余 11 个测试全绿，回归会静默上线。

**该文件目前在磁盘上已不存在**（全盘搜索无结果）。恢复它应列为待办。

### 探针三件套

1. **无头 dump**：`msedge --headless=new --dump-dom <url>`
2. **`win = globalThis` 桩里跑整段脚本**：能抓跨 IIFE 裸名缺失。⚠️ 把函数抽出来单跑会把作用域拍平，抓不到。
3. **真 DOM 探针页**：抓 `splitText` / 滚动 / 节点顺序这类只有真 DOM 才暴露的问题。

桩的常见坑：显式传 `location`；补 `addEventListener`（要能记录监听者）、`requestAnimationFrame`、
`getComputedStyle`、`insertBefore`、`firstChild`、`focus()`、`closest()`；`dispatch` 也要触发 `on<event>`；
滚动属性按浏览器默认给（`scrollTop=0 / scrollHeight=600 / clientHeight=300`），否则「贴底」判断会误判。
⚠️ **每次 boot 都是新实例**，断言前当场取（先捕获 `globalThis.__sfx` 会拿到上一轮）。

---

## 7. 已知的架构债

诚实记录，供后续决定是否偿还：

1. **路径写死绝对路径**。启动脚本、多个 Python 模块里都是 `C:\Users\92534\Desktop\考研\src\...`。
   仓库因此是「备份 / 版本控制」，不是可分发工程。
2. **顶层脚本平铺**。`src/` 下 ~60 个文件混着流水线、服务、导入导出、一次性脚本。
   由于 8 个自动化技能按 `src\xxx.py` 硬编码引用，**挪动他们会静默失效**，所以暂未重构。
3. **`generate_dashboard.py` 单文件 1 万行**，前端全靠模板常量字符串拼接，没有构建步骤。
4. **题库与复习历史不在版本控制内**（见 README「不入库的东西」）。
5. **`package.json` 命名遗留**（曾是 volcengine TTS 测试工程）。

# 考研学习仪表盘（本地大盘）

一个**离线优先**的考研复习仪表盘：把散落在 Obsidian 里的各科笔记，合成一张可交互的本地学习大盘，
并在同一个页面里提供闪卡练习（FSRS 间隔重复）、番茄钟、错题复盘、早间回顾打卡、笔记搜索与 AI 问答。

全程跑在本机 `localhost`，不依赖任何云端表格；只有一个 DeepSeek Key 用于 AI 讲解。

```
盘面一览：总览 / 笔记 / 闪卡 / 活动 / 错题复盘 / 薄弱点学习 / 早间回顾 / 设置
```

---

## 快速开始

### 一键启动（推荐）

```
双击  考研\启动考研大盘.bat
```

它会依次：跑数据流水线 → 起本地服务 → 用**实际端口**打开浏览器。

> 服务端口不一定是 8080。`serve.js` 会按候选列表挑一个可用端口，把结果写进 `.serve_port`，
> 启动脚本读它来打开正确地址（见下方「端口被系统保留」）。

### 手动启动

```bat
:: 1) 刷新索引与大盘（只刷数据，不排计划）
python src\run_pipeline.py --no-plan

:: 2) 起本地服务（闪卡 / AI 问答 / 设置需要它）
node src\serve.js

:: 3) 浏览器打开
::    http://localhost:<端口>/dashboard.html
```

### 环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Node.js | ≥ 22.5 | `serve.js` / `grade_llm.js` / `migrate.js` 使用内置 `node:sqlite` |
| Python | 3.11 | 流水线与索引脚本 |
| 依赖 | `npm install` | 主要用到 `qrcode`（设置页局域网二维码） |

本机实测路径（启动脚本里已内置绝对路径，PATH 里没有也能跑）：

```
node   C:\Program Files\nodejs\node.exe
python C:\Users\92534\AppData\Local\Programs\Python\Python311\python.exe
```

---

## 目录结构

仓库根就是 `考研\src`。**顶层脚本文件名被多个自动化技能按路径引用，不要随意改名或挪进子目录。**

```
src/
├── run_pipeline.py            ★ 一键流水线入口（下面「数据流水线」）
├── 启动考研大盘.bat             ★ 一键启动（与 考研\启动考研大盘.bat 内容相同）
│
├── 【流水线】─────────────────────────────────────────────
│   ├── build_index.py             各科 notes_index.json → 笔记索引.yaml
│   ├── gap_analysis.py            知识图谱 + 索引 + progress.json → 覆盖率与缺口
│   ├── build_review_patterns.py   错题复盘 errors.json → 错因画像
│   ├── daily_planner.py           生成明日计划 schedule/daily/plan_YYYY-MM-DD.md
│   └── english_coverage.py        英语覆盖判定（唯一实现，别在别处重写）
│
├── 【大盘生成】───────────────────────────────────────────
│   └── generate_dashboard.py      ★ 生成 dashboard.html（单文件大盘，含全部前端）
│
├── 【本地服务】───────────────────────────────────────────
│   ├── serve.js                   HTTP 服务 + 全部 /api 端点
│   ├── grade_llm.js               简答题 AI 判分
│   ├── fsrs_core.js               cards 行 ↔ ts-fsrs Card 映射
│   ├── review_store.js            错题复盘存储（文件式）
│   └── migrate.js                 建表 / 迁移
│
├── 【题库与闪卡】─────────────────────────────────────────
│   ├── fsrs_scheduler.py          出卡与复习调度（与 fsrs_core.js 必须同步改）
│   ├── generate_targeted_cards.py 针对性出卡
│   ├── insert_questions.py        题目入库
│   ├── schema.sql                 表结构
│   └── migrations/                迁移脚本
│
├── tools/                     工具、测试与离线依赖
│   ├── katex/ mermaid/ d3.min.js ts-fsrs.cjs   离线前端依赖（见「离线依赖」）
│   ├── test_*.js (11 个) + check_inline.js      测试套件
│   └── gen_english_index.py 等                 各科索引生成
│
├── flashcards/                闪卡相关数据
├── knowledge_graph/           各科知识图谱 JSON（gap_analysis 的输入）
├── assets/                    bg.jpg 等静态资源
├── integrations/volcengine/   火山引擎 TTS/Chat 协议参考实现（非运行路径）
├── archive/                   已退役的脚本
├── backups/                   数据库备份（⚠️ 被 .gitignore 忽略，不入库）
└── docs/ARCHITECTURE.md       架构、数据流与踩坑记录
```

---

## 数据流水线

`run_pipeline.py` 按**数据依赖顺序**执行，任何一步失败都不中断后续，最后汇总状态：

| 顺序 | 脚本 | 作用 |
|---|---|---|
| 1 | `tools/gen_english_index.py` | 英语 md → `English/notes_index.json` |
| 2 | `build_index.py` | 各科 `notes_index.json` → `src/笔记索引.yaml` |
| 3 | `gap_analysis.py` | 知识图谱 + 索引 + `progress.json` → 覆盖率 / 缺口排名 |
| 4 | `build_review_patterns.py` | 错题复盘 → 错因画像 |
| 5 | `daily_planner.py` | 明日计划（`--no-plan` 跳过） |
| 6 | `generate_dashboard.py` | 生成 `dashboard.html` |

```bat
python src\run_pipeline.py            :: 全量
python src\run_pipeline.py --no-plan  :: 只刷新索引与大盘
```

**元数据的单一事实源是各科 `notes_index.json`**（不在本仓库内，在各科笔记目录）。
本仓库里的 `笔记索引.yaml` 是它的派生产物，因此被忽略、每次重建。

> ⚠️ `dashboard.html` 是**产物**。直接改它会静默生效、然后被下一次流水线覆盖。
> 要改页面，改 `generate_dashboard.py` 里的模板常量，再跑流水线。

---

## 本地服务

`node src\serve.js` 提供三层东西：

- **静态页**（固定白名单）：`/`、`/index.html`、`/dashboard.html`
- **离线依赖**：`/tools/katex/**`（公式渲染，专用路由 + 穿越防护）
- **`/api/*`**：约 50 个端点，覆盖闪卡、笔记、错题复盘、番茄钟、早间回顾、设置、AI

安全模型（重要，改 `serve.js` 前先读）：

- 静态资源走**固定白名单**，不是把 URL 拼成路径读文件 —— 早期那种写法会让 `GET /.secrets.json` 拿到 Key 明文、`GET /question_bank.db` 拖库。
- **吃 Key / 能改配置的端点只放行内网来源**（`isPrivateAddress`）；公网来源 403（`ALLOW_PUBLIC_AI=1` 可关）。
- Key 只在服务端存在，下发给前端的一律是 `****尾4位`。
- CORS 不再无条件 `*`。

### 端口被系统保留（8080 可能起不来）

Windows 的 Hyper-V / WSL / Docker 会动态保留一段端口（例：`7500~8300`）。
落在保留段里的端口 `listen` 直接返回 `EACCES`，表现为「一闪就关、浏览器白页」。

```powershell
# 查看当前保留段
netsh interface ipv4 show excludedportrange protocol=tcp
```

`serve.js` 已按 `[PORT, 8080, 8088, 8888, 9090, 18080, 28080, 38080]` 依次尝试，并把**实际**端口写进 `.serve_port`。
想拿回 8080：管理员执行 `net stop winnat && net start winnat`，或直接重启。

> 平板 / 书签用的是**带端口**的地址；换端口后需要重新扫码或改书签（设置页二维码按 `location.origin` 生成，会自动跟着变）。

---

## 测试

```bat
:: 一次跑完全部（推荐）
node src\tools\run_all_tests.js
npm test

:: 或者单跑某一个
node src\tools\test_flash_keyboard.js
```

`tools/` 下共 **11 个** `test_*.js`：

| 文件 | 覆盖 |
|---|---|
| `test_flash_keyboard.js` | 闪卡键盘操作（选项 / 自评 / 撤销 / 简答） |
| `test_pomodoro.js` | 番茄钟状态机与跨端同步 |
| `test_grade_llm.js` | 简答判分（含 LLM 路径） |
| `test_note_render.js` | 笔记渲染 |
| `test_noteq.js` | 笔记搜索 + 打开定位 + 读笔记提问 |
| `test_net_guard.js` | 内网闸门与静态白名单 |
| `test_shell.js` | 应用外壳 / 导航 |
| `test_math_render.js` | 公式渲染（KaTeX 路径） |
| `test_dashboard_layout.js` | 大盘布局与 D3 渲染器注册 |
| `test_nav.js` | 页面路由 |
| `test_fsrs_spec.js` | FSRS 算法一致性（对照 `ts-fsrs.cjs`） |

> ⚠️ 多数测试会自己拉起**无头 Edge** 或临时 HTTP 服务，整套跑完需要几分钟。
> 在没有 Edge 的环境里会失败 —— 那是环境问题，不是回归。

改完代码的固定动作：

```
py_compile  →  run_pipeline.py --no-plan  →  node tools\run_all_tests.js
→  无头打开 #/flash #/notes #/study #/settings #/overview #/review
   确认零控制台报错、零 404
```

无头浏览器参考（Edge）：

```powershell
& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
  --headless=new --disable-gpu --virtual-time-budget=9000 `
  --enable-logging=stderr --dump-dom "http://localhost:8888/dashboard.html"
```

`--enable-logging=stderr` 才能抓到页面报错；顺便 grep `Failed to load resource|404`
能抓到「静态资源被白名单打掉」这类回归。

---

## 离线依赖

`tools/katex`、`tools/mermaid`、`tools/d3.min.js`、`tools/ts-fsrs.cjs` 是**随仓库携带**的第三方库，
为的是断网也能渲染公式 / 图表。它们不在 `package.json` 里，**不要**改成从 npm 加载：
`serve.js` 的 `/tools/katex/` 路由直接指向这个目录。

---

## 不入库的东西

见 `.gitignore`，三类：

1. **密钥** —— `.secrets.json`（模板见 `.secrets.example.json`）。
2. **生成产物** —— `dashboard.html`、`dashboard_data.json`、`笔记索引.yaml`、`flashcard_ready.html`、`.serve_port`。跑一次流水线即可重建。
3. **活数据 / 本机状态** —— `question_bank.db`（题库 + 复习日志）、`sync_state.json`、`note_reviews.json`、`backups/`。

> ⚠️ 也就是说：**题库与复习历史不在这个仓库里**。它属于「数据」而非「代码」，
> 每次刷闪卡都会写、进 git 只会制造噪音。需要备份请自行拷贝 `question_bank.db` 到 `backups/`。

---

## 开发约定（血泪版）

这些是踩过的坑，改代码前扫一眼能省几小时。

**`generate_dashboard.py`（1 万行，前端全是模板常量）**

- 主 HTML 骨架是 **f-string**（`{}` 必须写成 `{{}}`）；其余常量是普通 `'''`，两者规则不同。
- 普通 `'''` 常量里的 JS：反斜杠要双写、**注释里也别出现单反斜杠**。
- `__pageRenderers` 的 JS 必须在 `NAV_JS` 之前。
- 新图标用 `\uXXXX`，别在 `old_string` 里带 emoji。
- 加子页要改三处。
- 跨 IIFE 只能走 `window`；忘搭桥 → `ReferenceError` 被 `catch` 吞掉，表现为「功能没生效」（这种最难查）。

**`serve.js`**

- ⚠️ 加函数前**先查重名**：同名 `function` 后声明者胜，撞过一次导致端点恒返回 0 条。
- ⚠️ `sendJson` / `readBody` / `cfgGet` **只能在其之后使用**，前面引用会踩 TDZ（现象：请求挂死）。
  模块级工具用 `sendJsonRaw` / `readCfgValue`。
- ⚠️ `db.close()` 之后再读配置 = 静默取默认值（`get()` 内部是 `db.prepare`，抛错被自己的 catch 吞掉）。

**渲染：公式只有一套逻辑**

- `splitMath(raw)` 是唯一的公式切段器，认 `$$…$$`、`\[…\]`、`$…$`、`\(…\)`，**也认没有定界的裸 LaTeX**。
- `richText()` / `texWrap()` / `mdTex()` 共用它；笔记渲染器用 `maskMath()`。
- ⚠️ 正则字符串里匹配「反斜杠+括号」要写**三个**反斜杠（`\\\(`）；少一层会把 `(` 当分组括号。
- ⚠️ JS 字符串里写 `[\s\S]` 到正则手里只剩 `[sS]` → 用 `String.fromCharCode` 拼。

**前端铁律**

- **pointer capture 会把 click 改派走**：先 `closest("button,input,a,select")` 就 return，移动 5px 才抢，`up` 时 release，不要 `preventDefault`。
- 提示词由用户自己在「设置 → AI 提示词」写；**用户写了就原样用，不追加任何要求**，也不要写死字数。
- 闪卡计时（失焦必须停）与番茄钟（继续走）**语义不同，别合并**。
- 移动端软键盘回车常是「确认候选词」（`isComposing`），8 处「回车=提交」都要就地判 `!(e.isComposing || e.keyCode === 229)`。

**运行环境**

- ⚠️ **`.bat` 必须 CRLF 行尾**，别写带中文的括号块、别放 emoji（65001 代码页下多字节字符会让整段错位）。
- `pwsh` 里跑 node/python 输出常丢 → `Start-Process -RedirectStandardOutput` 落文件再读；中文乱码就写 UTF-8 文件再读。
- 验证一律用 `DB_PATH` 指向副本，**别动真库**；用户的大盘服务可能正在这台机器上跑（别覆盖 `config` 里的 `pomo_run`）。
- 改了 `serve.js` / `migrate.js` / `grade_llm.js` 之后要**重启服务**才生效。

---

## 相关文档

- `docs/ARCHITECTURE.md` —— 数据流、模块职责、数据库表、API 分组

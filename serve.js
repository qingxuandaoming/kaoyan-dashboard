// 本地复习服务器 + 火山 API 代理
// 使用方法：node serve.js  然后访问 http://localhost:8080

const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { DatabaseSync } = require('node:sqlite');

// PORT / DB_PATH 支持环境变量覆盖：便于在副本库上做验证，不动真实复习数据。
//   例：PORT=8081 DB_PATH=./_test.db node src/serve.js
const PORT = parseInt(process.env.PORT || '8080', 10);

// 获取本机所有 IPv4 地址（含局域网），供平板/手机扫码连接时展示。
// 优先返回常见家庭/办公网段（192.168.x、10.x、172.16-31.x），
// 过滤掉 Docker 默认网桥（172.17.x）和其他虚拟网段的已知前缀。
function getLanIps() {
  const all = [];
  try {
    const ifaces = require('os').networkInterfaces();
    for (const name of Object.keys(ifaces)) {
      for (const addr of ifaces[name] || []) {
        if (addr.family === 'IPv4' && !addr.internal) {
          // 跳过常见虚拟网卡：Docker 默认网桥、VirtualBox Host-Only、Hyper-V 内部虚拟交换机
          const skipName = /(docker|vbox|vmware|virtual|hyper-v|wsl)/i.test(name);
          const ip = addr.address;
          const isDocker = /^172\.17\./.test(ip);
          const isVBox = /^192\.168\.56\./.test(ip);
          if (skipName || isDocker || isVBox) continue;
          all.push({ ip, name });
        }
      }
    }
  } catch (e) { /* ignore */ }
  // 优先家庭/办公网段：192.168.x > 10.x > 172.16-31.x > 其他
  const score = (ip) => {
    if (/^192\.168\./.test(ip)) return 3;
    if (/^10\./.test(ip)) return 2;
    if (/^172\.(1[6-9]|2\d|3[01])\./.test(ip)) return 1;
    return 0;
  };
  all.sort((a, b) => score(b.ip) - score(a.ip));
  return all.map(x => x.ip);
}
const DB_PATH = process.env.DB_PATH
  ? path.resolve(__dirname, process.env.DB_PATH)
  : path.join(__dirname, 'question_bank.db');
const DASH_DATA_PATH = path.join(__dirname, 'dashboard_data.json');
const NOTE_TOUCH_PATH = path.join(__dirname, 'note_reviews.json');
const ROOT_DIR = path.resolve(__dirname, '..');   // 考研知识库根目录
let targetedJob = null;   // 盘活出题任务（单任务串行）

// 安全解析相对路径：必须落在根目录内且为 .md 文件
function resolveNotePath(rel) {
  if (!rel || typeof rel !== 'string') return null;
  const decoded = decodeURIComponent(rel).replace(/\\/g, '/');
  if (decoded.includes('..')) return null;
  const abs = path.resolve(ROOT_DIR, decoded);
  if (!abs.startsWith(ROOT_DIR + path.sep)) return null;
  if (!abs.toLowerCase().endsWith('.md')) return null;
  return abs;
}

// 笔记里的插图（<img src="./assets/x.png">）。与 resolveNotePath 同样的越权防护，
// 但放行图片扩展名而不是 .md——笔记正文里的相对路径全都要靠它取回来。
const IMG_MIME = {
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.gif': 'image/gif', '.svg': 'image/svg+xml', '.webp': 'image/webp',
  '.bmp': 'image/bmp', '.avif': 'image/avif',
};
function resolveAssetPath(rel) {
  if (!rel || typeof rel !== 'string') return null;
  const decoded = decodeURIComponent(rel).replace(/\\/g, '/');
  if (decoded.includes('..')) return null;
  const abs = path.resolve(ROOT_DIR, decoded);
  if (!abs.startsWith(ROOT_DIR + path.sep)) return null;
  if (!(path.extname(abs).toLowerCase() in IMG_MIME)) return null;
  return abs;
}

// ============================================================
// 笔记全文搜索索引（2026-09-21）
//
// 两个能力：① 按标题或**正文**模糊搜索（空格分隔多个关键词 = 全都要命中）；
// ② 按科目 / 子科 / 章节 / 级别 / 标签筛选，筛出来的笔记能直接打开。
//
// 语料很小（408 / Math / Politics / English / Re-examination 合起来 ~760 个 .md、6MB），
// 所以正文直接全量读进内存缓存，按 mtime 增量刷新；命中查询只做字符串匹配。
// ⚠️ 不要在这里引 YAML 解析器（项目没装 js-yaml）：元数据只读各科的
//    notes_index.json —— 那也是「笔记元数据的单一事实源」。
// ============================================================
const NOTE_SUBJECTS = [
  { key: '408', label: '408', dir: '408' },
  { key: '数学一', label: '数学一', dir: 'Math' },
  { key: '政治', label: '政治', dir: 'Politics' },
  { key: '英语一', label: '英语一', dir: 'English' },
  { key: '复试', label: '复试', dir: 'Re-examination' },
];
const NOTE_SKIP_DIRS = new Set(['src', 'PDF', 'assets', '.obsidian', '.qoder', '.uploads',
                                'node_modules', '.git', '0参考教材']);
const NOTE_MAX_BYTES = 400 * 1024;      // 单文件上限：再大就不进正文索引（只看标题）
const NOTE_INDEX_TTL = 15000;           // 目录/元数据扫描节流：15 秒内复用上一次

const noteTextCache = new Map();        // rel -> { mtime, text }
let noteIdx = { at: 0, files: [], byPath: new Map(), orphan: [] };

/** 把 "[a, b, c]" 这种字符串形式的标签解析成数组（索引里就是这么存的）。 */
function parseTagString(v) {
  if (Array.isArray(v)) return v.map(x => String(x).trim()).filter(Boolean);
  const s = String(v == null ? '' : v).trim();
  if (!s) return [];
  return s.replace(/^\[|\]$/g, '').split(',').map(x => x.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean);
}

function scanNoteFiles() {
  const files = [];
  for (const s of NOTE_SUBJECTS) {
    const root = path.join(ROOT_DIR, s.dir);
    const stack = [''];
    let guard = 0;
    while (stack.length && guard++ < 6000) {
      const rel = stack.pop();
      const abs = rel ? path.join(root, rel) : root;
      let ents;
      try { ents = fs.readdirSync(abs, { withFileTypes: true }); } catch (e) { continue; }
      for (const ent of ents) {
        if (ent.name.startsWith('.')) continue;
        const r = rel ? rel + '/' + ent.name : ent.name;
        if (ent.isDirectory()) {
          if (!NOTE_SKIP_DIRS.has(ent.name)) stack.push(r);
        } else if (/\.md$/i.test(ent.name) && !ent.name.startsWith('_')) {
          files.push(s.dir + '/' + r);
        }
      }
    }
  }
  return files;
}

/** 读各科 notes_index.json：entry 里的 links[].path 相对**子科目录**写
 *  （`./第1章_绪论.md`）；政治那种一科一文件的，路径直接落在科目根下。
 *  两种形态都试一遍，命中就用（和 Python 侧 note_files_for 的口径一致）。 */
function loadNoteMeta() {
  const byPath = new Map();
  const orphan = [];
  for (const s of NOTE_SUBJECTS) {
    let d = null;
    try { d = JSON.parse(fs.readFileSync(path.join(ROOT_DIR, s.dir, 'notes_index.json'), 'utf-8')); }
    catch (e) { continue; }
    const subs = (d && d.subjects) || {};
    for (const code of Object.keys(subs)) {
      const subName = (subs[code] && subs[code].sub) || code;
      for (const e of ((subs[code] && subs[code].entries) || [])) {
        const rec = {
          id: e.id || '', title: e.title || '', level: e.level || '', chapter: e.chapter || '',
          status: e.status || '', tags: parseTagString(e.tags), code: code, subName: subName,
          subject: s.key, dir: s.dir,
        };
        let linked = null;
        for (const lk of (e.links || [])) {
          const p = lk && lk.path ? String(lk.path).replace(/^\.\//, '') : '';
          if (!p) continue;
          const cand = [s.dir + '/' + code + '/' + p, s.dir + '/' + p];
          const hit = cand.find(x => { try { return fs.statSync(path.join(ROOT_DIR, x)).isFile(); } catch (err) { return false; } });
          if (hit) { linked = hit; break; }
        }
        if (linked) {
          if (!byPath.has(linked)) byPath.set(linked, rec);
        } else if (rec.title) {
          orphan.push(rec);      // 只有标题、没链到文件：仍可参与标题搜索
        }
      }
    }
  }
  return { byPath, orphan };
}

function ensureNoteIndex(force) {
  const now = Date.now();
  if (!force && noteIdx.at && now - noteIdx.at < NOTE_INDEX_TTL) return noteIdx;
  const meta = loadNoteMeta();
  noteIdx = { at: now, files: scanNoteFiles(), byPath: meta.byPath, orphan: meta.orphan };
  return noteIdx;
}

function noteTextOf(rel) {
  const abs = path.join(ROOT_DIR, rel);
  let st;
  try { st = fs.statSync(abs); } catch (e) { return null; }
  const hit = noteTextCache.get(rel);
  if (hit && hit.mtime === st.mtimeMs) return hit.text;
  if (st.size > NOTE_MAX_BYTES) return null;
  let text = '';
  try { text = fs.readFileSync(abs, 'utf-8'); } catch (e) { return null; }
  noteTextCache.set(rel, { mtime: st.mtimeMs, text: text });
  return text;
}

function classifyNote(rel) {
  const parts = String(rel).split('/');
  const subj = NOTE_SUBJECTS.filter(s => s.dir === parts[0])[0] || { key: parts[0], label: parts[0] };
  // 子科：目录形态取第二段（408/DS/xxx.md）；单文件形态（Politics/马原.md）没有子科段
  const sub = parts.length > 2 ? parts[1] : '';
  return { subject: subj.key, subjectLabel: subj.label, sub };
}

const noteTokens = (q) => String(q || '').toLowerCase().split(/\s+/).map(x => x.trim())
  .filter(Boolean).slice(0, 6);

// 中文里的连接词/助词：整句搜不到时，按这些切开当关键词再搜一轮。
// （用户会写「旋转和平衡」，而笔记里写的是「旋转与平衡」或分散在两段——
//   整句 substring 必然搜不到，拆成「旋转 / 平衡」就对了。分级降级，不一眼返回空。）
const NOTE_SPLIT_RE = /[的和与及或是在有为对把被了着之其、，,。；;：:\s/]+/;
function noteQueryPlan(raw) {
  const q = String(raw || '').trim().toLowerCase();
  const hard = q ? q.split(/\s+/).map(s => s.trim()).filter(Boolean).slice(0, 6) : [];
  const soft = q ? q.split(NOTE_SPLIT_RE).map(s => s.trim()).filter(s => s.length >= 2).slice(0, 6) : [];
  return { hard: hard, soft: soft };
}

/** 一轮匹配：tokens 都命中（requireAll）或命中任意一个；返回结果数组。 */
function matchNoteTier(idx, tokens, f, requireAll) {
  const out = [];
  for (const rel of idx.files) {
    const info = classifyNote(rel);
    if (f.subject && info.subject !== f.subject) continue;
    if (f.sub && info.sub !== f.sub) continue;
    const meta = idx.byPath.get(rel) || null;
    if (f.level && (!meta || meta.level !== f.level)) continue;
    if (f.chapter && (!meta || meta.chapter !== f.chapter)) continue;
    if (f.tag && (!meta || meta.tags.indexOf(f.tag) < 0)) continue;

    const base = rel.slice(rel.lastIndexOf('/') + 1).replace(/\.md$/i, '');
    const titleText = ((meta && meta.title) ? meta.title + ' ' : '') + base;
    const titleLow = titleText.toLowerCase();
    const titleHit = tokens.every(t => titleLow.indexOf(t) >= 0);

    let hits = 0, snippet = '', firstAt = -1, text = null, matched = 0, anchor = '';
    if (tokens.length) {
      text = noteTextOf(rel);
      const low = text == null ? '' : text.toLowerCase();
      for (const t of tokens) {
        const at = low.indexOf(t);
        const inTitle = titleLow.indexOf(t) >= 0;
        if (at < 0) {
          if (!inTitle && requireAll) { matched = -1; break; }
          continue;
        }
        matched += 1;
        let c = 0, from = 0;
        while (c < 60) { const k = low.indexOf(t, from); if (k < 0) break; c++; from = k + t.length; }
        hits += c;
        if (firstAt < 0 || at < firstAt) firstAt = at;
      }
      if (matched === -1) continue;
      if (requireAll && matched < tokens.length && !titleHit) {
        // 正文里没全命中，但标题里可能全有；逐个再确认一次
        const allInTitle = tokens.every(t => titleLow.indexOf(t) >= 0);
        if (!allInTitle) continue;
      }
      if (!requireAll && matched === 0) continue;
      if (text == null && !titleHit) {
        continue;      // 没进正文索引的大文件：标题没命中就不算结果
      }
      if (firstAt >= 0) {
        snippet = text.slice(Math.max(0, firstAt - 70), firstAt + 140).replace(/\s+/g, ' ').trim();
        // 命中处往上找最近的标题：前端万一没能在正文里找到这个词（被 markdown
        // 拆进不同标签之类），至少还能滚到正确的那一节
        const before = text.slice(0, firstAt).split('\n');
        for (let i = before.length - 1; i >= 0; i--) {
          const m = /^#{1,4}\s+(.+?)\s*$/.exec(before[i]);
          if (m) { anchor = m[1].replace(/[*_`]/g, '').trim(); break; }
        }
      }
    }
    out.push({
      path: rel, name: base, indexed: text != null,
      title: meta ? meta.title : '', subject: info.subject, subjectLabel: info.subjectLabel,
      sub: info.sub, subName: (meta && meta.subName) || info.sub,
      chapter: meta ? meta.chapter : '', level: meta ? meta.level : '',
      status: meta ? meta.status : '', tags: meta ? meta.tags : [],
      titleHit: titleHit, hits: hits, matched: matched, snippet: snippet, anchor: anchor,
    });
  }
  // 排序：标题命中 > 命中次数 > 命中关键词个数 > 路径短（更「主干」的排前面）
  out.sort((a, b) => (b.titleHit - a.titleHit) || (b.hits - a.hits)
    || (b.matched - a.matched) || (a.path.length - b.path.length));
  return out;
}

/** 搜索总入口：整句 → 拆词(全命中) → 拆词(任一命中)，逐级降级；
 *  返回 { results, how } ，how 让前端能说清「是按整句还是按拆词找到的」。 */
function searchNoteDocs(q, f) {
  f = f || {};
  const idx = ensureNoteIndex(false);
  const plan = noteQueryPlan(q);
  const hasFilter = !!(f.subject || f.sub || f.level || f.chapter || f.tag);
  let results = [], how = 'none';
  if (plan.hard.length) {
    results = matchNoteTier(idx, plan.hard, f, true);
    how = 'exact';
    if (!results.length && plan.soft.length && plan.soft.join(' ') !== plan.hard.join(' ')) {
      results = matchNoteTier(idx, plan.soft, f, true);
      if (results.length) how = 'split';
      else {
        results = matchNoteTier(idx, plan.soft, f, false);
        if (results.length) how = 'any';
      }
    }
  } else if (hasFilter) {
    results = matchNoteTier(idx, [], f, true);     // 只看筛选：列出这一类的全部笔记
    how = 'browse';
  }
  const orphans = plan.hard.length ? orphanHits(idx.orphan, plan.hard, f) : [];
  return { results: results.concat(orphans), how: how, soft: plan.soft, tokens: plan.hard };
}

/** 只有标题、没有链到文件的索引条目：仍可被「按名字搜」找到（但没有正文片段）。 */
function orphanHits(orphan, tokens, f) {
  if (!tokens.length || !orphan.length) return [];
  const out = [];
  for (const e of orphan) {
    if (f.subject && e.subject !== f.subject) continue;
    if (f.level && e.level !== f.level) continue;
    if (f.chapter && e.chapter !== f.chapter) continue;
    if (f.tag && e.tags.indexOf(f.tag) < 0) continue;
    if (f.sub && e.code !== f.sub) continue;
    const hay = (e.title + ' ' + e.tags.join(' ')).toLowerCase();
    if (!tokens.every(t => hay.indexOf(t) >= 0)) continue;
    out.push({
      path: '', name: e.title, indexed: false, title: e.title, subject: e.subject,
      subjectLabel: (NOTE_SUBJECTS.filter(s => s.key === e.subject)[0] || {}).label || e.subject,
      sub: e.code, subName: e.subName, chapter: e.chapter, level: e.level,
      status: e.status, tags: e.tags, titleHit: true, hits: 0, snippet: '',
      orphan: true,   // 前端据此提示「这条只在索引里，没有对应文件」
    });
  }
  return out;
}

function noteFacets() {
  const idx = ensureNoteIndex(false);
  const bump = (m, k) => { if (k) m[k] = (m[k] || 0) + 1; };
  const subjects = {}, subs = new Map(), chapters = {}, levels = {}, tags = {}, statuses = {};
  for (const rel of idx.files) {
    const info = classifyNote(rel);
    bump(subjects, info.subject);
    const meta = idx.byPath.get(rel) || null;
    const subLabel = (meta && meta.subName) || info.sub || '（未分类）';
    if (info.sub) {
      const k = info.subject + '\u0000' + info.sub;
      const cur = subs.get(k) || { subject: info.subject, value: info.sub, label: subLabel, n: 0 };
      cur.n += 1; subs.set(k, cur);
    }
    bump(chapters, meta ? meta.chapter : '');
    bump(levels, meta ? meta.level : '');
    bump(statuses, meta ? meta.status : '');
    if (meta) meta.tags.forEach(t => bump(tags, t));
  }
  const topList = (o, n) => Object.keys(o).sort((a, b) => (o[b] - o[a]) || (a < b ? -1 : 1))
    .slice(0, n).map(k => ({ value: k, n: o[k] }));
  const chapterKeys = Object.keys(chapters).filter(Boolean);
  chapterKeys.sort((a, b) => (parseInt(a.replace(/\D/g, ''), 10) || 99) - (parseInt(b.replace(/\D/g, ''), 10) || 99));
  return {
    subjects: NOTE_SUBJECTS.filter(s => subjects[s.key]).map(s => ({ value: s.key, label: s.label, n: subjects[s.key] })),
    subs: Array.from(subs.values()).sort((a, b) => (b.n - a.n) || a.label.localeCompare(b.label, 'zh')),
    chapters: chapterKeys.map(k => ({ value: k, n: chapters[k] })),
    levels: ['L1', 'L2', 'L3'].filter(k => levels[k]).map(k => ({ value: k, n: levels[k] })),
    tags: topList(tags, 30),
    statuses: topList(statuses, 6),
    total: idx.files.length,
  };
}

/** 读笔记提问时的 system prompt（与 flashcard 解析那套分工：那个绑题目，这个绑笔记）。 */
/**
 * 读笔记提问的内置 system prompt —— 同样**刻意极简**（2026-09-21 用户要求）。
 * 之前在里塞过「讲机制/举例/易混点/最后给一行『建议补进笔记』」这种要求，
 * 用户说**有误导性**、要自己写，所以内置只留角色 + 输出格式；
 * 想怎么讲去「设置 → AI 提示词 → 读笔记提问」里写（`prompt_note_qa`）。
 */
const NOTE_QA_SYSTEM = [
  '你在给一个考研学生讲他正在读的笔记。',
  '用中文和 Markdown；公式用 $...$（行内）或 $$...$$（独立行）。',
].join('\n');

/**
 * 这一次解析/追问要不要「快」（关掉思考）。
 *
 * 优先级：客户端显式要求（deep:true 的「深想一遍」按钮）> 设置页的 explain_quick
 * （默认 on = 关思考，交互更跟手；想让它每次都细想就在设置页改成深度）。
 */
const explainWantQuick = (p, cfgVal) => {
  if (p && (p.deep === true || p.quick === false)) return false;
  if (p && p.quick === true) return true;
  return String(cfgVal == null ? 'on' : cfgVal) !== 'off';
};

/**
 * 读 config 表（模块级版本）。
 * ⚠️ 为什么不用 handler 里那个 cfgGet：它在 2700 多行才定义，而 /api/explain 在 1700 多行
 *    就执行了 —— 在那个位置引用它会踩 TDZ（ReferenceError），和 readBody 是同一个坑。
 */
const readCfgValue = (db, k, dflt) => {
  try {
    const r = db.prepare('SELECT value FROM config WHERE key = ?').get(k);
    return r && r.value != null ? r.value : dflt;
  } catch (e) { return dflt; }
};

// ============================================================
// FSRS 调度核心 —— 唯一真相源见 src/fsrs_core.js（官方 ts-fsrs 适配层）
// ============================================================
const { FSRS, parseConfig } = require('./fsrs_core');
const gradeLlm = require('./grade_llm');

// ---- 闪卡调度辅助 --------------------------------------------------------

/** 本地日期 YYYY-MM-DD。⚠️ 不要用 toISOString().slice(0,10)，那是 UTC 日期，
 *  在 UTC+8 下早 8 点前会算成前一天，导致"今日到期/已复习"计数错位。 */
function localToday() {
  return FSRS.localDateStr(new Date());
}

/** 从 config 表读调度参数（迁移器已灌入默认值）；失败时回落默认配置 */
function loadConfig(db) {
  try {
    const rows = db.prepare('SELECT key, value FROM config').all();
    const raw = {};
    for (const r of rows) raw[r.key] = r.value;
    return parseConfig(raw);
  } catch (e) {
    return parseConfig(null);
  }
}

/**
 * 今日额度用量（Anki 口径）
 *  - 新卡：今天首次被评分的卡数（introduced_at 落在今天）
 *  - 复习：今天评过分的"复习状态"卡数（按卡去重，学习/再学习不计入复习额度）
 */
function todayUsage(db, today) {
  let newDone = 0, reviewDone = 0;
  try {
    newDone = db.prepare(
      "SELECT COUNT(*) c FROM cards WHERE introduced_at IS NOT NULL AND date(introduced_at) = ?"
    ).get(today).c;
  } catch (e) { /* 迁移未跑时忽略 */ }
  try {
    reviewDone = db.prepare(
      "SELECT COUNT(DISTINCT card_id) c FROM review_log WHERE date(review_date) = ? AND state_before = 2"
    ).get(today).c;
  } catch (e) { /* ignore */ }
  return { newDone, reviewDone };
}

/** 组装今日额度信息 */
function buildLimits(db, cfg, today) {
  const { newDone, reviewDone } = todayUsage(db, today);
  return {
    new_per_day: cfg.new_per_day,
    reviews_per_day: cfg.reviews_per_day,
    new_done: newDone,
    review_done: reviewDone,
    remaining_new: Math.max(0, cfg.new_per_day - newDone),
    remaining_review: Math.max(0, cfg.reviews_per_day - reviewDone),
  };
}

// ---------------------------------------------------------------------------
// 科目均衡配额（2026-09-14）
//
// 背景：选题只有优先级排序、没有任何按科目的配额，某一科卡多就会占满整轮。
// 实测政治 184 / 408 166 / 数学一 141 / 英语一 86 个考点，卡量悬殊时
// 少的科目长期刷不到——大盘「活动」页只显示两科就是这个现象的侧面。
//
// 口径：配额只做**减法**——它是叠在 remaining_new / remaining_review 之上的
// 额外上限（AND），只会更严、绝不放宽。练得越少的科目分到的弹性名额越多。
// ---------------------------------------------------------------------------

/** 各科科目名（与 topics.subject 原值一致） */
const QUOTA_SUBJECTS = ['408', '政治', '数学一', '英语一'];

/**
 * 近 N 天各科的练习量分布（含昨日）。
 * 窗口一律走 localtime——review_date 存的是本地时间，用 UTC 比会差 8 小时。
 * @returns {Object} {subject: count}
 */
function recentSubjectCounts(db, days) {
  const out = {};
  try {
    const rows = db.prepare(`
      SELECT COALESCE(t.subject, '') AS s, COUNT(*) AS n
        FROM review_log rl
        JOIN questions q ON q.id = rl.question_id
        LEFT JOIN topics t ON t.id = q.topic_id
       WHERE rl.review_date >= datetime('now', 'localtime', ?)
       GROUP BY s
    `).all('-' + days + ' days');
    for (const r of rows) out[r.s] = r.n;
  } catch (e) { /* 表缺失时按「全都没练」处理 */ }
  return out;
}

/**
 * 按近期练习分布算各科配额。
 *
 * 保底 floor = clamp(round(limit*0.15), 2, 6)；剩余名额按权重分配，
 * 权重 w_i = 1 - (r_i+1)/(Σr+4)（Laplace 平滑）——练得越少权重越高，
 * 完全没碰过的科目（r_i=0）拿满保底还有额外倾斜。
 *
 * 刻意**不使用** rating/错题信号：这一版只做科目均衡。
 */
function computeSubjectQuotas(limit, recent) {
  const total = QUOTA_SUBJECTS.reduce((a, s) => a + (recent[s] || 0), 0);
  const floor = Math.min(6, Math.max(2, Math.round(limit * 0.15)));
  const flex = Math.max(0, limit - floor * QUOTA_SUBJECTS.length);
  const raw = {};
  let sumW = 0;
  for (const s of QUOTA_SUBJECTS) {
    const w = 1 - ((recent[s] || 0) + 1) / (total + QUOTA_SUBJECTS.length);
    raw[s] = w;
    sumW += w;
  }
  const cap = Math.max(floor, Math.ceil(limit * 0.4));
  const quotas = {};
  for (const s of QUOTA_SUBJECTS) {
    const extra = sumW > 0 ? Math.round(flex * raw[s] / sumW) : 0;
    quotas[s] = Math.min(cap, Math.max(floor, floor + extra));
  }
  return quotas;
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.gif': 'image/gif',
  '.ico': 'image/x-icon',
  '.svg': 'image/svg+xml',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
};

// 代理火山 TTS API（HTTP REST v1）
function proxyVolcano(bodyData, callback) {
  const postData = JSON.stringify(bodyData);
  const auth = bodyData.headers?.Authorization || bodyData.headers?.authorization || `Bearer;${bodyData.app?.token || ''}`;

  const options = {
    hostname: 'openspeech.bytedance.com',
    port: 443,
    path: '/api/v1/tts',
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': auth,
      'Content-Length': Buffer.byteLength(postData)
    }
  };

  const req = https.request(options, (res) => {
    let data = '';
    res.on('data', (chunk) => { data += chunk; });
    res.on('end', () => {
      console.log('[Proxy] Volcano status:', res.statusCode);
      console.log('[Proxy] Body preview:', data.substring(0, 300));
      try {
        const json = JSON.parse(data);
        callback(null, json);
      } catch (e) {
        callback(null, { code: -1, message: 'Invalid JSON', raw: data });
      }
    });
  });

  req.on('error', (e) => { callback(e); });
  req.write(postData);
  req.end();
}

// 代理火山方舟大模型 API
function proxyArk(bodyData, apiKey, callback) {
  const postData = JSON.stringify(bodyData);

  const options = {
    hostname: 'ark.cn-beijing.volces.com',
    port: 443,
    path: '/api/v3/chat/completions',
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`,
      'Content-Length': Buffer.byteLength(postData)
    }
  };

  console.log('[Ark] Requesting model:', bodyData.model);

  const req = https.request(options, (res) => {
    let data = '';
    res.on('data', (chunk) => { data += chunk; });
    res.on('end', () => {
      console.log('[Ark] Status:', res.statusCode);
      console.log('[Ark] Body preview:', data.substring(0, 500));
      try {
        const json = JSON.parse(data);
        callback(null, json);
      } catch (e) {
        callback(null, { error: { message: 'Invalid JSON from Ark', raw: data.substring(0, 200) } });
      }
    });
  });

  req.on('error', (e) => {
    console.error('[Ark] Request error:', e.message);
    callback(e);
  });
  req.write(postData);
  req.end();
}

/**
 * 早期端点用的 JSON 响应。
 * ️ 不能用 sendJson：它定义在 1800 行之后，而闪卡这些端点在 1300 行就执行了 ——
 *    引用它会踩 TDZ（`Cannot access 'sendJson' before initialization`，表现为请求挂死）。
 */
const sendJsonRaw = (res, code, obj) => {
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(obj));
};

/**
 * 闪卡「当日这一组」的服务端状态（2026-09-21）。
 *
 * 原先 cards/idx 只存在各设备自己的 localStorage，于是：
 *   · 换设备各刷各的 → **同一道题一天被问好几遍**（用户报的）
 *   · 浏览器存储被清 / `setItem` 抛错（被 catch 吞掉）→ 进度看起来"凭空重置"（用户报的偶发）
 * 现在这一组放服务端 config（`flash_session`），所有端共用同一份；localStorage 只当离线缓存。
 * 取回时还会**剔掉「今天已经答过」的卡**（别的设备答的也算），从根上堵住一天重复问。
 */
const FLASH_SESSION_KEY = 'flash_session';
const FLASH_CARDS_MAX = 400;          // 一组最多这么多张（防配置表被塞爆）

const readFlashSession = (db) => {
  let s = null;
  try { s = JSON.parse(readCfgValue(db, FLASH_SESSION_KEY, 'null')); } catch (e) { s = null; }
  if (!s || typeof s !== 'object' || !Array.isArray(s.cards) || !s.cards.length) return null;
  if (s.date !== localToday()) return null;                 // 跨天作废
  return {
    date: s.date,
    cards: s.cards,
    idx: Math.max(0, Math.min(Number(s.idx) || 0, s.cards.length)),
    filter: s.filter || null,
    updated: s.updated || '',
    device: s.device || '',
  };
};

const writeFlashSession = (db, sess) => {
  try {
    db.prepare('INSERT INTO config (key, value, updated_at) VALUES (?, ?, datetime(\'now\',\'localtime\')) '
      + 'ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at')
      .run(FLASH_SESSION_KEY, JSON.stringify(sess));
    return true;
  } catch (e) {
    console.warn('[Flash] 存闪卡进度失败（不影响本机继续刷）:', e.message);
    return false;
  }
};

/** 今天已经答过的 card_id 集合（跨设备：都写在同一张 review_log 里） */
const answeredToday = (db) => {
  const set = new Set();
  try {
    const rows = db.prepare("SELECT DISTINCT card_id FROM review_log WHERE review_date >= ?")
      .all(localToday() + ' 00:00:00');
    for (const r of rows) set.add(String(r.card_id));
  } catch (e) {}
  return set;
};
 /**
 * 这个来源地址算不算「局域网 / 本机」。
 *
 * 服务要监听 0.0.0.0 才能让平板连上来，可一旦路由把这台机器的 8080 转发出去，
 * 任何公网来客都能白用 AI 额度、甚至改写 API Key。所以：**吃 key 的接口**
 * （AI 相关 + 设置写入）只放行内网来源，其余（页面、静态资源、只读接口）照旧。
 * ALLOW_PUBLIC_AI=1 可显式关掉这道闸（真要在公网用的时候）。
 */
const isPrivateAddress = (ip) => {
  let s = String(ip == null ? '' : ip).trim();
  if (!s) return false;
  if (s === '::1' || s === '::ffff:127.0.0.1' || s === 'localhost') return true;
  // IPv4-mapped IPv6：::ffff:192.168.1.5
  const m = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/i.exec(s);
  if (m) s = m[1];
  if (s.indexOf(':') >= 0) {
    // IPv6：唯一本地地址 fc00::/7、链路本地 fe80::/10
    const low = s.toLowerCase();
    return low.startsWith('fc') || low.startsWith('fd') || low.startsWith('fe8')
      || low.startsWith('fe9') || low.startsWith('fea') || low.startsWith('feb');
  }
  const p = s.split('.').map(x => parseInt(x, 10));
  if (p.length !== 4 || p.some(x => !Number.isFinite(x) || x < 0 || x > 255)) return false;
  if (p[0] === 10) return true;                             // 10/8
  if (p[0] === 127) return true;                            // 本机回环
  if (p[0] === 172 && p[1] >= 16 && p[1] <= 31) return true; // 172.16/12
  if (p[0] === 192 && p[1] === 168) return true;            // 192.168/16
  if (p[0] === 169 && p[1] === 254) return true;            // 链路本地
  if (p[0] === 100 && p[1] >= 64 && p[1] <= 127) return true; // 100.64/10（CGNAT / Tailscale）
  return false;
};

/** 吃 AI 额度或能改配置的路径：只让内网来源碰。 */
const SENSITIVE_PATHS = [
  '/api/explain', '/api/notes/ask', '/api/study/chat', '/api/grade',
  '/api/review/chat', '/api/ark', '/api/settings',
];
const isSensitivePath = (u) => SENSITIVE_PATHS.some(p => String(u || '').indexOf(p) === 0);

const server = http.createServer((req, res) => {
  // ⚠️ 以前这里无条件回 Access-Control-Allow-Origin: *（2026-09-21 收紧）：
  //    那等于允许**任意网页**跨域调用本机的 AI 接口（用户访问的随便哪个站点
  //    都能在后台拿着这台机器的额度问 AI）。现在只对**同源**回这个头，
  //    同源本来也不需要它，所以对大盘毫无影响；命令行工具不走 CORS，也不受影响。
  const reqOrigin = String(req.headers.origin || '');
  if (reqOrigin) {
    const host = String(req.headers.host || '');
    if (reqOrigin === 'http://' + host || reqOrigin === 'https://' + host) {
      res.setHeader('Access-Control-Allow-Origin', reqOrigin);
    }
  }
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  // 吃 key / 能改配置的接口只服务内网来源（见 isPrivateAddress 的说明）
  const remoteIp = (req.socket && (req.socket.remoteAddress || '')) || '';
  if (isSensitivePath(req.url) && !isPrivateAddress(remoteIp)
      && process.env.ALLOW_PUBLIC_AI !== '1') {
    console.warn('[Guard] 拒绝来自公网地址的敏感接口调用: ' + remoteIp + ' → ' + req.url);
    res.writeHead(403, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({
      ok: false,
      error: '这个接口只允许局域网/本机访问（当前来源 ' + (remoteIp || '未知') + '）。'
        + '如果确实需要公网访问，请在启动服务前设置环境变量 ALLOW_PUBLIC_AI=1。',
    }));
    return;
  }

  const url = req.url;

  // 代理火山 TTS API
  if (url === '/api/volcano/tts' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const parsed = JSON.parse(body);
        proxyVolcano(parsed, (err, data) => {
          if (err) {
            res.writeHead(500, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ code: -1, message: err.message }));
            return;
          }
          res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify(data));
        });
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ code: -1, message: 'Invalid JSON body' }));
      }
    });
    return;
  }

  // 闪卡复习数据回写端点 — 将浏览器 localStorage 中的复习记录同步到本地 JSON 文件
  // daily_planner.py --review 会读取此文件作为 review_log 的补充数据源
  if (url === '/api/flashcard-sync' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const parsed = JSON.parse(body);
        const syncPath = path.join(__dirname, 'flashcard_session_export.json');

        // 读取已有数据并合并新 reviews
        let existing = { reviews: [], last_sync: null };
        try {
          existing = JSON.parse(fs.readFileSync(syncPath, 'utf-8'));
          if (!Array.isArray(existing.reviews)) existing.reviews = [];
        } catch (e) {}

        // 合并：追加新 reviews，去重（按 question_id + timestamp）
        const newReviews = Array.isArray(parsed.reviews) ? parsed.reviews : [];
        const existingKeys = new Set(existing.reviews.map(r => `${r.question_id}|${r.timestamp}`));
        for (const review of newReviews) {
          const key = `${review.question_id}|${review.timestamp}`;
          if (!existingKeys.has(key)) {
            existing.reviews.push(review);
            existingKeys.add(key);
          }
        }
        existing.last_sync = new Date().toISOString();
        if (parsed.stats) existing.stats = parsed.stats;

        fs.writeFileSync(syncPath, JSON.stringify(existing, null, 2), 'utf-8');

        console.log(`[Flashcard Sync] ${newReviews.length} reviews received, total: ${existing.reviews.length}`);
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, total_reviews: existing.reviews.length }));
      } catch (e) {
        console.error('[Flashcard Sync] Error:', e.message);
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: e.message }));
      }
    });
    return;
  }

  if (url === '/api/ark/chat' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const parsed = JSON.parse(body);
        const apiKey = parsed._ark_api_key || '';
        if (!apiKey) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: { message: 'Missing _ark_api_key in request body' } }));
          return;
        }
        // 移除内部字段，不发送给方舟
        delete parsed._ark_api_key;
        proxyArk(parsed, apiKey, (err, data) => {
          if (err) {
            res.writeHead(500, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: { message: err.message } }));
            return;
          }
          res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify(data));
        });
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: { message: 'Invalid JSON body' } }));
      }
    });
    return;
  }

  // ============================================================
  // 简答题批改：POST /api/grade
  //   body: { question_id, answer_text?, image? }
  //     image 是 data URL（data:image/jpeg;base64,...），由前端 canvas 压缩后上传
  //   密钥只在服务端读（src/.secrets.json 或 DEEPSEEK_API_KEY），不下发给浏览器。
  //   返回 { ok, score, verdict, transcription, hits[], missed[], feedback,
  //          suggested_rating, reference_answer }
  // ============================================================
  if (url === '/api/grade' && req.method === 'POST') {
    const MAX_BODY = 12 * 1024 * 1024;   // 压缩后的图 + 文本，12MB 足够
    let body = '', overflow = false;
    req.on('data', chunk => {
      body += chunk;
      if (body.length > MAX_BODY && !overflow) {
        overflow = true;
        res.writeHead(413, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: '请求体过大（图片请压到 12MB 以内）' }));
        req.destroy();
      }
    });
    // ⚠️ req.destroy() 之后 socket 会发 'error'，没人接就是 uncaught 'error' event → 直接杀进程
    req.on('error', (e) => { console.error('[Grade] request error:', e.message); });
    res.on('error', (e) => { console.error('[Grade] response error:', e.message); });
    req.on('end', async () => {
      if (overflow) return;
      let payload;
      try {
        payload = JSON.parse(body);
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: '请求体不是合法 JSON' }));
        return;
      }
      const { question_id: qid, answer_text: answerText, image } = payload || {};
      if (!qid) {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: '缺少 question_id' }));
        return;
      }
      if (image && !/^data:image\/(png|jpe?g|webp|gif);base64,/i.test(image)) {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: 'image 必须是 data:image/... 形式的 base64' }));
        return;
      }
      if (!gradeLlm.hasKey()) {
        res.writeHead(503, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({
          ok: false,
          error: '未配置 DeepSeek API key：请在 src/.secrets.json 里填 deepseek.api_key，或设置环境变量 DEEPSEEK_API_KEY',
        }));
        return;
      }
      try {
        const db = new DatabaseSync(DB_PATH, { readOnly: true });
        const row = db.prepare('SELECT id, type, content FROM questions WHERE id = ?').get(qid);
        db.close();
        if (!row) {
          res.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: '题目不存在：' + qid }));
          return;
        }
        let content = {};
        try { content = JSON.parse(row.content); } catch (e) { content = { stem: row.content }; }

        // 非简答题没有 reference_answer：按题型现推一个，否则会把 answer 字段
        // （填空的答案字符串、选择题的序号）当成参考答案丢给模型，判分毫无意义。
        if (!content.reference_answer) {
          if (row.type === 'fill') {
            const holes = [...String(content.stem || '').matchAll(/\{\{c\d+::([\s\S]*?)\}\}/g)].map(m => m[1]);
            if (holes.length) content.reference_answer = holes.join(' / ');
          } else if (row.type === 'choice' && Array.isArray(content.options)
                     && typeof content.answer === 'number' && content.options[content.answer] != null) {
            content.reference_answer = String(content.options[content.answer]);
          } else if (typeof content.answer === 'boolean') {
            content.reference_answer = content.answer ? '正确' : '错误';
          }
          if (!content.reference_answer) {
            content.reference_answer = String(content.answer == null ? '' : content.answer);
          }
        }

        const t0 = Date.now();
        const result = await gradeLlm.gradeAnswer({ content }, answerText || '', image || null);
        const ms = Date.now() - t0;
        console.log(`[Grade] ${qid} type=${row.type} img=${image ? 'Y' : 'N'} ` +
                    `text=${(answerText || '').length}字 → ${result.ok ? 'score=' + result.score : 'FAIL ' + result.error} (${ms}ms)`);

        res.writeHead(result.ok ? 200 : 502, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({
          ...result,
          reference_answer: content.reference_answer || content.answer || '',
          explanation: content.explanation || '',
        }));
      } catch (e) {
        console.error('[Grade] error:', e.message);
        res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: e.message }));
      }
    });
    return;
  }

  // ============================================================
  // 闪卡练习 API（大盘嵌入式刷题）
  // ============================================================

  // GET /api/flashcards/session?limit=30
  // 选题策略：薄弱卡（lapses>0 / 近期答错 / 薄弱知识点）> 到期卡 > 近日笔记相关新卡 > 其他新卡
  //
  // 2026-09-13 筛选改造：新增 subject / bucket / mode 三个**白名单**参数，供大盘的
  // 「闪卡筛选页」用。白名单以外的值一律落回默认；SQL 片段只拼常量，值一律 ?
  // 绑定，所以外面传什么都不可能拼进语句里。
  if (url.startsWith('/api/flashcards/session') && req.method === 'GET') {
    const params = new URL('http://x' + url).searchParams;

    // ---- 当日这一组的读取 / 续刷（2026-09-21）----
    // peek：只回「有没有没刷完的那一组」+ 进度，供闸门按钮显示「继续本组（第 N/共 M 张）」。
    // resume：把那一组还回来，并**剔掉今天已经答过的卡**（另一台设备答的也算）。
    if (params.get('peek') === '1' || params.get('resume') === '1') {
      const wantCards = params.get('resume') === '1';
      const rdb = new DatabaseSync(DB_PATH, { readOnly: true });
      const sess = readFlashSession(rdb);
      if (!sess) {
        rdb.close();
        sendJsonRaw(res, 200, { ok: true, saved: null, cards: [] });
        return;
      }
      let cards = sess.cards;
      let idx = sess.idx;
      if (wantCards) {
        const done = answeredToday(rdb);
        // 剔掉今天答过的：这是「同一题一天问好几遍」的堵口
        const keep = [];
        let droppedBefore = 0;
        cards.forEach((c, i) => {
          const cid = String((c && c.card_id) || '');
          if (cid && done.has(cid)) { if (i < idx) droppedBefore += 1; return; }
          keep.push(c);
        });
        cards = keep;
        idx = Math.max(0, Math.min(idx - droppedBefore, cards.length));
      }
      rdb.close();
      if (wantCards) {
        // 剔完可能就空了 / 已经刷完 → 顺手把服务端那份也更新掉，别让别的端又拿到旧列表
        const wdb = new DatabaseSync(DB_PATH);
        writeFlashSession(wdb, { date: sess.date, cards: cards, idx: idx, filter: sess.filter,
                                 updated: new Date().toISOString(), device: sess.device });
        wdb.close();
        console.log('[Flash] 续刷：共 ' + cards.length + ' 张，从第 ' + (idx + 1) + ' 张继续'
          + (sess.device ? '（上次由 ' + sess.device + ' 更新）' : ''));
      }
      sendJsonRaw(res, 200, {
        ok: true,
        saved: cards.length ? { total: cards.length, idx: idx, updated: sess.updated, device: sess.device } : null,
        cards: wantCards ? cards : [],
        idx: idx,
        filter: sess.filter,
      });
      return;
    }

    // limit 只是「一次最多取多少张」的保护上限；智能组题下真正的数量由
    // 每日额度（remaining_new / remaining_review）与到期情况收敛。
    // 原先上限 120 且前端写死 30，导致用户把每日额度调到 100+ 时一组仍只有 30 张。
    let limit = Math.min(200, parseInt(params.get('limit') || '30', 10) || 30);

    // 科目取 topics.subject 的原值，顺手容错「数学」「英语」这种简写
    const SUBJECT_ALIAS = { '数学': '数学一', '英语': '英语一' };
    const rawSubject = (params.get('subject') || '').trim();
    const subject = Object.prototype.hasOwnProperty.call(SUBJECT_ALIAS, rawSubject)
      ? SUBJECT_ALIAS[rawSubject] : rawSubject;

    // 状态桶。leech / suspended 是**叠加标签**（水蛭卡同时算在它所在的 state 桶里），
    // 其余四个互斥且穷尽。「已掌握」沿用 /stats 的 mature 口径（interval >= 21 天），
    // 不另立标准——两处口径必须一致，否则筛选页的数字和统计面板会对不上。
    const BUCKETS = {
      new: 'c.state = 0',
      learning: 'c.state IN (1, 3)',
      review: 'c.state = 2 AND COALESCE(c.interval_days, 0) < 21',
      mature: 'c.state = 2 AND COALESCE(c.interval_days, 0) >= 21',
      leech: 'COALESCE(c.leech, 0) = 1',
      suspended: 'COALESCE(c.suspended, 0) = 1',
    };
    const rawBucket = (params.get('bucket') || '').trim();
    const bucket = Object.keys(BUCKETS).includes(rawBucket) ? rawBucket : '';
    // browse = 自由选题：不受每日新卡/复习限额约束（「我就想专刷政治的复习中卡片」）
    // extra  = 今日额度刷完后的「再来一组」（2026-09-21 用户要求）：数量取设置里的
    //          flash_extra_count（默认 10），同样不受额度约束，但**到期/学习中的卡优先**，
    //          所以「有未复习的就能接着复习」。
    const rawMode = params.get('mode');
    const mode = rawMode === 'browse' ? 'browse' : (rawMode === 'extra' ? 'extra' : 'smart');

    const where = [], bind = [];
    if (bucket === 'suspended') {
      where.push(BUCKETS.suspended);   // 单看暂停卡时不能再叠加 suspended=0，否则自相矛盾
    } else {
      where.push('COALESCE(c.suspended, 0) = 0');
      if (bucket) where.push(BUCKETS[bucket]);
    }
    if (subject) { where.push("COALESCE(t.subject, '') = ?"); bind.push(subject); }
    // topic：按考点前缀筛（复盘页「去练这些考点」用）。只允许 字母/数字/连字符，
    // 长度受限，且始终走 ? 绑定，不拼进 SQL。
    const rawTopic = (params.get('topic') || '').trim();
    const topic = /^[0-9A-Za-z\u4e00-\u9fa5-]{1,40}$/.test(rawTopic) ? rawTopic : '';
    if (topic) { where.push('t.id LIKE ?'); bind.push(topic + '%'); }
    const whereSql = 'WHERE ' + where.join(' AND ');   // 恒非空：至少有一条 suspended 条件
      // 今天已经答过的卡不再进**智能组**（review_log 是所有端共同的）→ 同一题一天只问一次。
      // 自选（browse）不过滤：用户就是想专门再刷一遍那个范围。
      const noRepeatSql = (mode === 'browse')
        ? '' : " AND c.id NOT IN (SELECT card_id FROM review_log WHERE review_date >= ?)";

    try {
      const db = new DatabaseSync(DB_PATH, { readOnly: true });
      const today = localToday();
      if (noRepeatSql) bind.push(today + ' 00:00:00');   // 对应 noRepeatSql 里的那个 ?
      // extra（今日额度刷完后的「再来一组」）：数量由设置决定，默认 10。
      // ⚠️ 必须在这里读（db 打开之后）：上面那段还不能碰 cfgGet/readCfgValue 之外的配置读取，
      //    而 cfgGet 本身定义在 3000 行外，引用会踩 TDZ。
      if (mode === 'extra') {
        limit = Math.max(1, Math.min(100,
          parseInt(readCfgValue(db, 'flash_extra_count', '10'), 10) || 10));
      }
      const cfg = loadConfig(db);
      const limits = buildLimits(db, cfg, today);
      const nowMs = Date.now();

      // 薄弱信号：历史 lapses + review_log 中 rating<=2 的题目
      const weakQids = new Set();
      for (const r of db.prepare('SELECT question_id FROM cards WHERE lapses > 0').all()) weakQids.add(r.question_id);
      try {
        for (const r of db.prepare(
          "SELECT question_id FROM review_log WHERE rating <= 2 AND review_date >= datetime('now','localtime','-30 days')"
        ).all()) weakQids.add(r.question_id);
      } catch (e) {}

      // 大盘生成时写入的辅助数据：近日笔记前缀 + 冷笔记前缀 + 薄弱知识点
      let recentPrefixes = [], coldPrefixes = [], weakTopicIds = new Set();
      try {
        const dd = JSON.parse(fs.readFileSync(DASH_DATA_PATH, 'utf-8'));
        recentPrefixes = Array.isArray(dd.recent_prefixes) ? dd.recent_prefixes : [];
        coldPrefixes = Array.isArray(dd.cold_prefixes) ? dd.cold_prefixes : [];
        weakTopicIds = new Set(Array.isArray(dd.weak_topic_ids) ? dd.weak_topic_ids : []);
      } catch (e) {}

      const rows = db.prepare(`
        SELECT c.id AS card_id, c.state, c.difficulty AS fs_d, c.stability, c.due_date,
               c.due_at, c.reps, c.lapses, c.last_review,
               c.learning_step, c.relearning_step, c.leech, c.suspended, c.interval_days,
               q.id AS qid, q.type, q.topic_id, q.content,
               t.name AS topic_name, COALESCE(t.subject, '') AS subject, t.exam_weight
        FROM cards c
        JOIN questions q ON c.question_id = q.id
        LEFT JOIN topics t ON q.topic_id = t.id
        ${whereSql}${noRepeatSql}
      `).all(...bind);

      // 配额要用的近期各科分布必须在 close 之前取（含昨日）
      const recentDist = recentSubjectCounts(db, 7);
      db.close();

      // 到期判定走 due_at（分钟级精度）；缺 due_at 时回落 due_date
      const isDueNow = (r) => {
        const at = r.due_at || (r.due_date ? r.due_date + 'T04:00:00' : null);
        if (!at) return true;
        const d = new Date(at);
        return isNaN(d) ? true : d.getTime() <= nowMs;
      };
      const dueKey = (r) => r.due_at || r.due_date || '';

      const items = rows.map(r => {
        const isNew = r.state === 0;
        const learning = r.state === 1 || r.state === 3;
        const due = isDueNow(r);
        let prio = 0;
        // 水蛭卡置顶 > 薄弱 > 学习/到期 > 近日笔记新卡 > 其他
        if (r.leech) prio = 5;
        else if (weakQids.has(r.qid) || weakTopicIds.has(r.topic_id)) prio = 4;
        else if (learning && due) prio = 3;
        else if (!isNew && due) prio = 2;
        else if (isNew && (recentPrefixes.some(p => (r.topic_id || '').startsWith(p))
            || coldPrefixes.some(p => (r.topic_id || '').startsWith(p)))) prio = 1;
        let content = {};
        try { content = JSON.parse(r.content); } catch (e) { content = { stem: r.content }; }
        const cardObj = {
          card_id: r.card_id, question_id: r.qid, type: r.type,
          topic_id: r.topic_id, topic_name: r.topic_name || '', subject: r.subject || '',
          state: r.state, reps: r.reps, lapses: r.lapses,
          due_date: r.due_date, due_at: r.due_at,
          interval_days: r.interval_days,
          learning_step: r.learning_step || 0,
          relearning_step: r.relearning_step || 0,
          leech: r.leech || 0,
          suspended: r.suspended || 0,
          due_now: due,
          content,
        };
        // Anki 风格：每张卡下发四档间隔预览（按钮副标题）
        try {
          cardObj.previews = FSRS.previewIntervals(
            { state: r.state, difficulty: r.fs_d, stability: r.stability, due_at: r.due_at,
              due_date: r.due_date, last_review: r.last_review, reps: r.reps, lapses: r.lapses,
              interval_days: r.interval_days, learning_step: r.learning_step, queue: r.state },
            { config: cfg, nowMs }
          );
        } catch (e) { cardObj.previews = null; }
        return { prio, due, isNew, learning, dueKey: dueKey(r), card: cardObj };
      });

      // 每日额度：复习卡与学习卡受限额约束；新卡受新卡限额约束。
      // 学习/再学习卡不占额度（Anki 口径），但到期即优先推送。
      //
      // ⚠️ 两个曾经的坑（2026-09-13 修复）：
      //  1) 新卡的 due_at 只是导入日期、没有调度含义，参与 dueKey 排序会让先导入的
      //     科目永久压制后导入的（实测政治卡被英语卡长期挤占）→ 新卡不参与 dueKey。
      //  2) `Math.random() - 0.5` 写在比较函数里是**不自洽的比较器**，V8 的 TimSort
      //     会给出系统性偏斜的结果，不是均匀随机（实测 17 个新卡位里数学独占 13 个）
      //     → 改为先给每项算一次随机键（decorate-sort-undecorate）。
      const byPrio = items.map((it) => ({ it, rand: Math.random() }))
        .sort((a, b) =>
          b.it.prio - a.it.prio ||
          b.it.card.lapses - a.it.card.lapses ||
          (a.it.isNew || b.it.isNew ? 0 : a.it.dueKey.localeCompare(b.it.dueKey)) ||
          a.rand - b.rand
        )
        .map((x) => x.it);
      // mode=browse（筛选页发起的自由选题）不受每日限额约束——用户明确点了
      // 「复习中 + 政治」，就该给他这个范围的卡，而不是被 reviews_per_day 拦下。
      // 注意 limits 的**统计口径不变**：评分照常计入今日用量，刷爆后 remaining_*
      // 会钳到 0，下次智能组显示 0 剩余。这是真实反映，不能为了数字好看而失真。
      const quotaExempt = (mode === 'browse' || mode === 'extra');
      // 科目配额只在「默认智能组」启用：用户一旦显式指定了科目、状态桶或自由选题，
      // 他要的就是那个范围，再按科目配额裁剪等于直接违背意图。
      const useQuota = !quotaExempt && !subject && !bucket;
      const quotas = useQuota ? computeSubjectQuotas(limit, recentDist) : null;

      // 新卡日额度的**按科目切分** —— 这才是「科目均衡配额」真正起作用的地方。
      //
      // 不加这一层的话，新卡额度是先到先得：408/英语一 的到期卡优先级高，会把
      // 20 张额度吃光；而政治(167张)/数学一(144张) 的卡 **100% 是新卡**，
      // 于是永远轮不到，表现就是「大盘只有 408 和英一」。
      // 单纯给科目加配额拦不住这个——卡不是被配额挡下的，是被全局新卡额度挡下的。
      //
      // 按配额权重把 remaining_new 切成各科子额度；不在配额名单里的科目
      // （如未分类）不设子额度，避免被误伤。
      const newBudget = {};
      if (useQuota && limits.remaining_new > 0) {
        const qsum = QUOTA_SUBJECTS.reduce((a, s) => a + quotas[s], 0) || 1;
        let left = limits.remaining_new;
        for (const s of QUOTA_SUBJECTS) {
          const share = Math.min(left, Math.round(limits.remaining_new * quotas[s] / qsum));
          newBudget[s] = share;
          left -= share;
        }
      }
      const newUsedBySubj = {};
      const newCapHit = (subj) => {
        const cap = newBudget[subj];
        return useQuota && cap !== undefined && (newUsedBySubj[subj] || 0) >= cap;
      };
      const takeNew = (it, subj) => {
        newUsed += 1;
        newUsedBySubj[subj] = (newUsedBySubj[subj] || 0) + 1;
        perSubj[subj] = (perSubj[subj] || 0) + 1;
        admitted.push(it);
      };

      let reviewUsed = 0, newUsed = 0;
      const perSubj = {};
      const admitted = [];
      const deferred = [];   // 被科目配额挡下的，第二遍用来补满

      for (const it of byPrio) {
        // 必须在 limit 处停下。早先这里一路收满再 slice(0, limit)，
        // 等于「按优先级取前 N 张」——而政治/数学一 的卡几乎全是 prio 0 的新卡，
        // 排在 byPrio 末尾，正好被 slice 砍掉，配额形同虚设。
        if (admitted.length >= limit) break;
        if (quotaExempt) { admitted.push(it); continue; }
        const subj = it.card.subject || '';
        // 学习/再学习卡照 Anki 口径必须出，但**要计入该科的均衡份额**：
        // 否则某一科在学卡一多就把别的科目挤光（实测英语一 10 张在学卡
        // 直接占掉三分之一轮次，政治/数学一 再也进不来）。
        // ⚠️ 但只出**已到点**的：学习卡是分钟/小时级间隔，没到点就推等于反复刷同一张，
        //    会直接破坏 FSRS 的同日调度（2026-09-20 修）。
        if (it.learning) {
          if (!it.due) continue;
          admitted.push(it);
          perSubj[subj] = (perSubj[subj] || 0) + 1;
          continue;
        }
        const overQuota = useQuota && (perSubj[subj] || 0) >= (quotas[subj] || 0);
        if (it.isNew) {
          if (newUsed >= limits.remaining_new) continue;
          if (newCapHit(subj) || overQuota) { deferred.push(it); continue; }
          takeNew(it, subj);
          continue;
        }
        // 复习卡只出**已到期**的。原先这里不查 due，「到期 0」时仍会从未来几天的
        // 队列里提前抽卡：既白占每日额度，又把该四天后再见的卡提前刷掉（2026-09-20 修）。
        if (!it.due) continue;
        if (overQuota) { deferred.push(it); continue; }
        if (reviewUsed < limits.remaining_review) {
          reviewUsed += 1; admitted.push(it); perSubj[subj] = (perSubj[subj] || 0) + 1;
        }
      }

      // 第二遍：某科候选不足会让总数缩水，用被配额挡下的补满 limit。
      // 这一遍放宽**科目配额**（否则补不进来），但仍受每日额度**和新卡子额度**约束；
      // 按「离配额缺口最大的科目优先」排序，尽量不把均衡意图完全抹掉。
      if (useQuota && admitted.length < limit) {
        deferred.sort((a, b) =>
          ((perSubj[a.card.subject] || 0) - (quotas[a.card.subject] || 0)) -
          ((perSubj[b.card.subject] || 0) - (quotas[b.card.subject] || 0)));
        for (const it of deferred) {
          if (admitted.length >= limit) break;
          const subj = it.card.subject || '';
          if (it.isNew) {
            if (newUsed >= limits.remaining_new) continue;
            if (newCapHit(subj)) continue;   // 子额度是硬的，第二遍也不放宽
            takeNew(it, subj);
            continue;
          }
          if (reviewUsed < limits.remaining_review) { reviewUsed += 1; admitted.push(it); }
        }
      }

      const picked = admitted.slice(0, limit).map(x => x.card);
      // 同优先级段内轻度洗牌，练习顺序不单调
      for (let i = picked.length - 1; i > 0; i--) {
        if (Math.random() < 0.35) {
          const j = Math.floor(Math.random() * (i + 1));
          [picked[i], picked[j]] = [picked[j], picked[i]];
        }
      }

      // ---- 记下「今天这一组」（服务端，多端共享）----
      // 智能组才记：自选（browse）是临时挑的，不该顶掉今天的正经进度。
      let savedSession = null;
      try {
        const sdb = new DatabaseSync(DB_PATH);
        if (mode !== 'browse') {
          savedSession = { date: today, cards: picked.slice(0, FLASH_CARDS_MAX), idx: 0,
                           filter: { subject, bucket, topic, mode },
                           updated: new Date().toISOString(), device: String(req.headers['x-device'] || '').slice(0, 40) };
          writeFlashSession(sdb, savedSession);
        }
        sdb.close();
      } catch (e) { console.warn('[Flash] 记录当日分组失败:', e.message); }

      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({
        ok: true, today,
        total_cards: rows.length,
        filter: { subject, bucket, topic, mode },   // 回显，便于前端确认服务端理解一致
        recent_prefixes: recentPrefixes,
        cold_prefixes: coldPrefixes,
        limits,
        // 科目配额（仅在默认智能组生效时非 null）+ 近期各科练习分布，便于核对均衡效果
        quota: quotas,
        recent_by_subject: recentDist,
        counts: {
          new: items.filter(x => x.isNew && x.due).length,
          due: items.filter(x => !x.isNew && !x.learning && x.due).length,
          learning: items.filter(x => x.learning && x.due).length,
          leech: items.filter(x => x.card.leech).length,
        },
        cards: picked,
      }));
    } catch (e) {
      console.error('[Flashcards] session error:', e.message);
      res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: e.message }));
    }
    return;
  }

  // POST /api/flashcards/position {idx, total?} —— 轻量上报「刷到第几张了」。
  // 每翻一张就调一次（几百字节），让别的端/下次打开能接上；也可以顺带带 device 便于排查。
  if (url === '/api/flashcards/position' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const p = JSON.parse(body || '{}');
        const idx = parseInt(p.idx, 10);
        if (!Number.isFinite(idx) || idx < 0) { sendJsonRaw(res, 400, { ok: false, error: 'idx 非法' }); return; }
        const db = new DatabaseSync(DB_PATH);
        const sess = readFlashSession(db);
        let ok = false;
        if (sess) {
          const next = Math.min(idx, sess.cards.length);
          // 只前进不后退：别的端刚刷过时，别被一台落后设备的旧位置拖回去
          if (next >= sess.idx) {
            writeFlashSession(db, { date: sess.date, cards: sess.cards, idx: next, filter: sess.filter,
                                    updated: new Date().toISOString(),
                                    device: String(p.device || sess.device || '').slice(0, 40) });
          }
          ok = true;
        }
        db.close();
        sendJsonRaw(res, 200, { ok: true, saved: ok, idx: sess ? Math.max(idx, sess.idx) : idx });
      } catch (e) {
        sendJsonRaw(res, 500, { ok: false, error: e.message });
      }
    });
    return;
  }

  // POST /api/flashcards/session {cards, idx, filter?, device?} —— 把「当日这一组」整份存到服务端。
  // 用在新开一组、页面隐藏/离开时（平时翻页只走上一条轻量的 position）。
  if (url === '/api/flashcards/session' && req.method === 'POST') {
    let sbody = '';
    req.on('data', chunk => sbody += chunk);
    req.on('end', () => {
      try {
        const body = sbody;
        const p = JSON.parse(body || '{}');
        const cards = Array.isArray(p.cards) ? p.cards.slice(0, FLASH_CARDS_MAX) : [];
        if (!cards.length) { sendJsonRaw(res, 400, { ok: false, error: 'cards 为空' }); return; }
        const idx = Math.max(0, Math.min(parseInt(p.idx, 10) || 0, cards.length));
        const db = new DatabaseSync(DB_PATH);
        const ok = writeFlashSession(db, {
          date: localToday(), cards: cards, idx: idx,
          filter: p.filter && typeof p.filter === 'object' ? p.filter : null,
          updated: new Date().toISOString(),
          device: String(p.device || '').slice(0, 40),
        });
        db.close();
        sendJsonRaw(res, 200, { ok: true, saved: ok, idx: idx, total: cards.length });
      } catch (e) {
        sendJsonRaw(res, 500, { ok: false, error: e.message });
      }
    });
    return;
  }

  // POST /api/flashcards/review  {card_id, rating}  rating: 1=Again 2=Hard 3=Good 4=Easy
  // 即时 FSRS 更新 + 写入 review_log
  if (url === '/api/flashcards/review' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const { card_id, rating, chosen } = JSON.parse(body);
        // 学生实际选的那一项（选择题的 'A'-'D' 或选项文本）。此前只在生成 AI 解析时
        // 随请求传给模型、并不落库，导致复盘只能看到「答错了」而看不到「错在哪」。
        // 这里只存一个短字符串，截断只是防御性上限，正常值远小于它。
        const chosenText = chosen == null ? null : String(chosen).trim().slice(0, 120) || null;
        const r = parseInt(rating, 10);
        if (!card_id || ![1, 2, 3, 4].includes(r)) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'need card_id and rating 1-4' }));
          return;
        }
        const db = new DatabaseSync(DB_PATH);
        const row = db.prepare('SELECT * FROM cards WHERE id = ?').get(card_id);
        if (!row) {
          db.close();
          res.writeHead(404, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'card not found' }));
          return;
        }
        const cfg = loadConfig(db);
        const card = {
          state: row.state, difficulty: row.difficulty, stability: row.stability,
          due_date: row.due_date, due_at: row.due_at, last_review: row.last_review,
          reps: row.reps, lapses: row.lapses, interval_days: row.interval_days,
          learning_step: row.learning_step, relearning_step: row.relearning_step,
          leech: row.leech, suspended: row.suspended,
          introduced_at: row.introduced_at, queue: row.queue,
        };
        const nowMs = Date.now();
        const { before } = FSRS.scheduleWithLog(card, r, { config: cfg, nowMs });
        const elapsed = row.last_review
          ? Math.max(0, (nowMs - new Date(row.last_review).getTime()) / 86400000) : 0;

        db.prepare(`
          UPDATE cards SET state=?, difficulty=?, stability=?, due_date=?, due_at=?, last_review=?,
                 reps=?, lapses=?, queue=?, interval_days=?,
                 learning_step=?, relearning_step=?, leech=?, suspended=?, introduced_at=?
           WHERE id=?
        `).run(
          card.state, card.difficulty, card.stability, card.due_date, card.due_at, card.last_review,
          card.reps, card.lapses, card.queue, card.interval_days,
          card.learning_step, card.relearning_step, card.leech, card.suspended, card.introduced_at,
          card_id
        );
        // 回滚快照：撤销端点据此还原（见 POST /api/flashcards/undo）
        db.prepare(`
          INSERT INTO review_log (card_id, question_id, rating, elapsed_days, new_interval, review_date,
                 state_before, state_after,
                 difficulty_before, stability_before, lapses_before, interval_before, due_at_before, queue_before,
                 learning_step_before, relearning_step_before, chosen)
          VALUES (?, ?, ?, ?, ?, datetime('now','localtime'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        `).run(
          card_id, row.question_id, r, elapsed, card.interval_days,
          before.state, card.state,
          before.difficulty, before.stability, before.lapses, before.interval_days,
          before.due_at || row.due_at, before.queue,
          before.learning_step || 0, before.relearning_step || 0,
          chosenText
        );
        db.close();

        console.log(`[Flashcards] review ${card_id} rating=${r} -> due ${card.due_at} (S=${card.stability.toFixed(2)}, state=${card.state})`);
        let previews = null;
        try { previews = FSRS.previewIntervals(card, { config: cfg, nowMs }); } catch (e) {}
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({
          ok: true,
          due_date: card.due_date, due_at: card.due_at,
          interval: card.interval_days, state: card.state, queue: card.queue,
          lapses: card.lapses, leech: card.leech, suspended: card.suspended,
          previews,
        }));
      } catch (e) {
        console.error('[Flashcards] review error:', e.message);
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: e.message }));
      }
    });
    return;
  }

  // GET /api/flashcards/today — 今日复习数/到期数（进度保持显示用，轻量）
  if (url === '/api/flashcards/today') {
    try {
      const db = new DatabaseSync(DB_PATH, { readOnly: true });
      const today = localToday();
      const cfg = loadConfig(db);
      const limits = buildLimits(db, cfg, today);
      const nowIso = new Date().toISOString();
      let reviewed = 0;
      let dueReview = 0, dueLearning = 0, dueNew = 0;
      try { reviewed = db.prepare('SELECT COUNT(*) c FROM review_log WHERE date(review_date) = ?').get(today).c; } catch (e) {}
      try {
        // 到期判定统一走 due_at（缺列时回落 due_date）
        const rows = db.prepare(`
          SELECT state, COALESCE(due_at, due_date || 'T04:00:00') AS at
            FROM cards WHERE COALESCE(suspended, 0) = 0
        `).all();
        for (const r of rows) {
          if (String(r.at) > nowIso) continue;
          if (r.state === 0) dueNew += 1;
          else if (r.state === 2) dueReview += 1;
          else dueLearning += 1;
        }
      } catch (e) {}
      db.close();
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({
        ok: true,
        reviewed_today: reviewed,
        due_today: dueReview + dueLearning + dueNew,
        due_review: dueReview, due_learning: dueLearning, due_new: dueNew,
        limits,
      }));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: e.message }));
    }
    return;
  }

  // POST /api/flashcards/undo  {card_id?} — 撤销最近一次评分
  // 依据 review_log 的回滚快照列还原卡片，并删除该条日志。
  if (url === '/api/flashcards/undo' && req.method === 'POST') {
    let body = '';
    req.on('data', (chunk) => { body += chunk; });
    req.on('end', () => {
      try {
        let cardId = null;
        try { cardId = (JSON.parse(body || '{}') || {}).card_id || null; } catch (e) {}
        const db = new DatabaseSync(DB_PATH);
        const log = cardId
          ? db.prepare('SELECT * FROM review_log WHERE card_id = ? ORDER BY id DESC LIMIT 1').get(cardId)
          : db.prepare('SELECT * FROM review_log ORDER BY id DESC LIMIT 1').get();
        if (!log) {
          db.close();
          res.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: '没有可撤销的复习记录' }));
          return;
        }
        if (log.due_at_before == null) {
          db.close();
          res.writeHead(409, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: '该记录缺少回滚快照（迁移前的历史数据），无法撤销' }));
          return;
        }
        // 上一条日志的时间作为还原后的 last_review
        const prev = log.card_id
          ? db.prepare('SELECT review_date FROM review_log WHERE card_id = ? AND id < ? ORDER BY id DESC LIMIT 1')
              .get(log.card_id, log.id)
          : null;

        db.prepare(`
          UPDATE cards SET state=?, difficulty=?, stability=?, interval_days=?,
                 due_at=?, due_date=?, queue=?, lapses=?, reps=MAX(0, reps - 1), last_review=?,
                 learning_step=?, relearning_step=?
           WHERE id=?
        `).run(
          log.state_before, log.difficulty_before, log.stability_before, log.interval_before,
          log.due_at_before, FSRS.localDateStr(new Date(log.due_at_before)), log.queue_before,
          log.lapses_before, prev ? prev.review_date : null,
          log.learning_step_before || 0, log.relearning_step_before || 0,
          log.card_id
        );
        db.prepare('DELETE FROM review_log WHERE id = ?').run(log.id);
        db.close();

        console.log(`[Flashcards] undo ${log.card_id} (撤销 rating=${log.rating})`);
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({
          ok: true, card_id: log.card_id, undone_rating: log.rating,
          restored: { state: log.state_before, due_at: log.due_at_before, lapses: log.lapses_before },
        }));
      } catch (e) {
        console.error('[Flashcards] undo error:', e.message);
        res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: e.message }));
      }
    });
    return;
  }

  // POST /api/flashcards/suspend  {card_id} — 暂停（软删除）一张卡
  // 把卡片标记为 suspended=1，之后所有智能组题/到期统计都不会再出现。
  // 用软删而不是 DELETE：review_log 关联不断、可在筛选页「已暂停」桶里找回复原。
  if (url === '/api/flashcards/suspend' && req.method === 'POST') {
    let body = '';
    req.on('data', (chunk) => { body += chunk; });
    req.on('end', () => {
      const fail = (code, msg) => {
        res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: msg }));
      };
      try {
        const p = JSON.parse(body || '{}');
        const cardId = p.card_id;
        if (!cardId) { fail(400, '需要 card_id'); return; }
        const db = new DatabaseSync(DB_PATH);
        const row = db.prepare('SELECT id FROM cards WHERE id = ?').get(cardId);
        if (!row) { db.close(); fail(404, '卡片不存在'); return; }
        db.prepare('UPDATE cards SET suspended = 1 WHERE id = ?').run(cardId);
        db.close();
        console.log('[Flashcards] suspend ' + cardId + '（卡片已从复习队列移除）');
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, card_id: cardId, suspended: true }));
      } catch (e) {
        console.error('[Flashcards] suspend error:', e.message);
        fail(500, e.message);
      }
    });
    return;
  }

  // GET /api/flashcards/stats — 统计与预测（Anki 风格面板数据）
  if (url.startsWith('/api/flashcards/stats')) {
    try {
      const db = new DatabaseSync(DB_PATH, { readOnly: true });
      const today = localToday();
      const cfg = loadConfig(db);
      const nowIso = new Date().toISOString();

      // 状态 / 成熟度分布（young < 21 天，mature >= 21 天）
      const cards = db.prepare(`
        SELECT state, COALESCE(interval_days, 0) AS ivl,
               COALESCE(leech, 0) AS leech, COALESCE(suspended, 0) AS suspended,
               COALESCE(due_at, due_date || 'T04:00:00') AS at
          FROM cards
      `).all();

      const maturity = { new: 0, learning: 0, young: 0, mature: 0, suspended: 0 };
      let leechCount = 0;
      const forecastMap = {};
      for (const c of cards) {
        if (c.suspended) { maturity.suspended += 1; continue; }
        if (c.leech) leechCount += 1;
        if (c.state === 0) maturity.new += 1;
        else if (c.state === 1 || c.state === 3) maturity.learning += 1;
        else if (c.ivl < 21) maturity.young += 1;
        else maturity.mature += 1;

        // 未来 30 天到期预测
        const d = String(c.at).slice(0, 10);
        if (d) forecastMap[d] = (forecastMap[d] || 0) + 1;
      }

      const base = new Date(today + 'T00:00:00');
      const forecast = [];
      for (let i = 0; i < 30; i++) {
        const d = new Date(base.getTime() + i * 86400000);
        const key = FSRS.localDateStr(d);
        forecast.push({ date: key, count: forecastMap[key] || 0 });
      }

      // 近 30 天复习量与正确率（rating>=3 视为通过）
      const daily = [];
      const logRows = db.prepare(`
        SELECT date(review_date) AS d, COUNT(*) AS n,
               SUM(CASE WHEN rating >= 3 THEN 1 ELSE 0 END) AS ok
          FROM review_log
         WHERE review_date >= datetime('now','localtime','-30 days')
         GROUP BY d ORDER BY d
      `).all();
      const byDate = {};
      for (const r of logRows) byDate[r.d] = { n: r.n, ok: r.ok };
      for (let i = 29; i >= 0; i--) {
        const key = FSRS.localDateStr(new Date(base.getTime() - i * 86400000));
        const v = byDate[key] || { n: 0, ok: 0 };
        daily.push({ date: key, reviews: v.n, correct: v.ok, accuracy: v.n ? v.ok / v.n : null });
      }

      const totalReviews = logRows.reduce((s, r) => s + r.n, 0);
      const totalCorrect = logRows.reduce((s, r) => s + r.ok, 0);
      const limits = buildLimits(db, cfg, today);

      // Anki 口径：新卡不属"到期"，是独立队列，故 here 分开计数
      const isNow = (c) => !c.suspended && String(c.at) <= nowIso;
      const dueReviewLearning = cards.filter((c) => isNow(c) && c.state !== 0).length;
      const newAvailable = cards.filter((c) => isNow(c) && c.state === 0).length;
      db.close();

      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({
        ok: true, today,
        total_cards: cards.length,
        maturity,
        leech: leechCount,
        due_now: dueReviewLearning,
        new_available: newAvailable,
        forecast,
        daily,
        accuracy_30d: totalReviews ? totalCorrect / totalReviews : null,
        reviews_30d: totalReviews,
        limits,
      }));
    } catch (e) {
      console.error('[Flashcards] stats error:', e.message);
      res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: e.message }));
    }
    return;
  }

  // GET /api/flashcards/facets — 筛选页的数量来源：科目 × 状态桶 交叉计数
  // /stats 只有全局成熟度、没有科目维度，做不了这个矩阵，所以单独开一个端点。
  // 桶的判定与 session 端点**逐字一致**（改一处必须改两处，否则筛选页数字和实际
  // 抽到的卡会对不上）。
  if (url.startsWith('/api/flashcards/facets')) {
    try {
      const db = new DatabaseSync(DB_PATH, { readOnly: true });
      const today = localToday();
      const nowIso = new Date().toISOString();
      const rows = db.prepare(`
        SELECT COALESCE(t.subject, '(未分类)') AS subject,
               COUNT(*) AS total,
               SUM(CASE WHEN c.state = 0 THEN 1 ELSE 0 END) AS new,
               SUM(CASE WHEN c.state IN (1, 3) THEN 1 ELSE 0 END) AS learning,
               SUM(CASE WHEN c.state = 2 AND COALESCE(c.interval_days, 0) < 21  THEN 1 ELSE 0 END) AS review,
               SUM(CASE WHEN c.state = 2 AND COALESCE(c.interval_days, 0) >= 21 THEN 1 ELSE 0 END) AS mature,
               SUM(CASE WHEN COALESCE(c.leech, 0) = 1     THEN 1 ELSE 0 END) AS leech,
               SUM(CASE WHEN COALESCE(c.suspended, 0) = 1 THEN 1 ELSE 0 END) AS suspended,
               SUM(CASE WHEN COALESCE(c.suspended, 0) = 0
                        AND COALESCE(c.due_at, c.due_date || 'T04:00:00') <= ? THEN 1 ELSE 0 END) AS due
        FROM cards c
        JOIN questions q ON c.question_id = q.id
        LEFT JOIN topics t ON q.topic_id = t.id
        GROUP BY COALESCE(t.subject, '(未分类)')
        ORDER BY total DESC
      `).all(nowIso);
      db.close();

      // 合计由服务端算，前端不重复一遍逻辑
      const totals = rows.reduce((a, r) => {
        for (const k of ['total', 'new', 'learning', 'review', 'mature', 'leech', 'suspended', 'due']) {
          a[k] = (a[k] || 0) + (r[k] || 0);
        }
        return a;
      }, {});
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: true, today, subjects: rows, totals }));
    } catch (e) {
      console.error('[Flashcards] facets error:', e.message);
      res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: e.message }));
    }
    return;
  }

  // ============================================================
  // 笔记盘活 API（读笔记 / 标记盘活 / 一键盘活出题）
  // ============================================================

  // GET /api/notes/preview?path=408/OS/第1章_xxx.md — 读取笔记原文（限根目录内 .md）
  if (url.startsWith('/api/notes/preview')) {
    const q = new URL('http://x' + url).searchParams;
    const abs = resolveNotePath(q.get('path') || '');
    if (!abs) {
      res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: '非法路径（仅限知识库内 .md 文件）' }));
      return;
    }
    try {
      const content = fs.readFileSync(abs, 'utf-8');
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: true, path: q.get('path'), content }));
    } catch (e) {
      res.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: '文件不存在: ' + e.message }));
    }
    return;
  }

  // GET /api/notes/asset?path=408/DS/assets/avl_rotations.png — 笔记插图
  // 笔记正文里的 <img src="./assets/..."> 是相对笔记所在目录写的，浏览器按页面
  // 所在位置解析会指到 src/ 下面去，所以前端把相对路径换算成知识库相对路径后
  // 走这个端点取图。
  if (url.startsWith('/api/notes/asset')) {
    const q = new URL('http://x' + url).searchParams;
    const abs = resolveAssetPath(q.get('path') || '');
    if (!abs) {
      res.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('非法路径（仅限知识库内的图片）');
      return;
    }
    fs.readFile(abs, (err, buf) => {
      if (err) {
        res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
        res.end('图片不存在');
        return;
      }
      res.writeHead(200, {
        'Content-Type': IMG_MIME[path.extname(abs).toLowerCase()],
        'Content-Length': buf.length,
        'Cache-Control': 'no-cache',   // 本地文件，改了要立刻看到
      });
      res.end(buf);
    });
    return;
  }

  // POST /api/notes/touch {path} — 标记笔记已读盘活（写 note_reviews.json）
  if (url === '/api/notes/touch' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const { path: rel } = JSON.parse(body);
        const abs = resolveNotePath(rel || '');
        if (!abs || !fs.existsSync(abs)) {
          res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: '非法或不存在的路径' }));
          return;
        }
        let data = { touched: {} };
        try { data = JSON.parse(fs.readFileSync(NOTE_TOUCH_PATH, 'utf-8')); } catch (e) {}
        if (!data.touched) data.touched = {};
        data.touched[rel.replace(/\\/g, '/')] = new Date().toISOString();
        fs.writeFileSync(NOTE_TOUCH_PATH, JSON.stringify(data, null, 2), 'utf-8');
        console.log('[Notes] touched:', rel);
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: e.message }));
      }
    });
    return;
  }

  // ==========================================================================
  // 每日任务（2026-09-14）
  //
  // 表 daily_tasks 见 src/migrations/002_daily_tasks.sql，由 migrate.js 幂等建出。
  // 两条写入路径共用同一张表：
  //   用户在大盘「活动」页点选 → 这里
  //   agent 每天 00:00 生成      → daily_tasks.py 直连 SQLite（不依赖服务端在跑）
  //
  // 只用 GET/POST：文件上方的 CORS 头只声明了 GET/POST/OPTIONS，
  // 用 PUT/DELETE 会被浏览器预检直接挡下。
  // ==========================================================================
  const TASK_SUBJECTS = ['408', '政治', '数学一', '英语一'];
  const TASK_TEXT_MAX = 200;
  const TASK_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

  // 正则只保证「4-2-2 位数字」，2026-13-99 也能过。这里再回环校验一次真实日历日期，
  // 否则前端传个非法日期会静默返回空列表，看起来像「今天没有任务」。
  const isValidDateStr = (s) => {
    if (!TASK_DATE_RE.test(s)) return false;
    const [y, m, d] = s.split('-').map(Number);
    if (m < 1 || m > 12 || d < 1 || d > 31) return false;
    const dt = new Date(y, m - 1, d);
    return dt.getFullYear() === y && dt.getMonth() === m - 1 && dt.getDate() === d;
  };

  const sendJson = (code, payload) => {
    res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(payload));
  };

  // GET /api/tasks?date=YYYY-MM-DD&include_deleted=1
  if (url.startsWith('/api/tasks') && req.method === 'GET') {
    const q = new URL('http://x' + url).searchParams;
    const dateStr = (q.get('date') || '').trim() || localToday();
    if (!isValidDateStr(dateStr)) {
      sendJson(400, { ok: false, error: '日期应为 YYYY-MM-DD 且必须是真实存在的日期' });
      return;
    }
    const includeDeleted = q.get('include_deleted') === '1';
    try {
      const db = new DatabaseSync(DB_PATH, { readOnly: true });
      const rows = db.prepare(
        'SELECT id, task_date, text, source, subject, done, done_at, deleted, deleted_at, created_at ' +
        'FROM daily_tasks WHERE task_date = ?' + (includeDeleted ? '' : ' AND deleted = 0') +
        ' ORDER BY deleted ASC, done ASC, id ASC'
      ).all(dateStr);
      // 已删除数单独查：rows 在默认（不含已删除）时里面根本没有这些行，
      // 用 rows.length - live.length 会恒等于 0，这个计数就废了。
      const deletedCount = db.prepare(
        'SELECT COUNT(*) AS c FROM daily_tasks WHERE task_date = ? AND deleted = 1'
      ).get(dateStr).c;
      db.close();
      const live = rows.filter(r => !r.deleted);
      sendJson(200, {
        ok: true, date: dateStr, tasks: rows,
        summary: {
          total: live.length,
          done: live.filter(r => r.done).length,
          user_added: live.filter(r => r.source === 'user').length,
          agent_added: live.filter(r => r.source === 'agent').length,
          deleted: deletedCount,
        },
      });
    } catch (e) {
      sendJson(500, { ok: false, error: e.message });
    }
    return;
  }

  // POST /api/tasks/add {date?, text, subject?}
  if (url === '/api/tasks/add' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const p = JSON.parse(body || '{}');
        const text = String(p.text == null ? '' : p.text).trim();
        if (!text) { sendJson(400, { ok: false, error: '任务内容不能为空' }); return; }
        if (text.length > TASK_TEXT_MAX) {
          sendJson(400, { ok: false, error: '任务内容不能超过 ' + TASK_TEXT_MAX + ' 字' });
          return;
        }
        const dateStr = String(p.date || '').trim() || localToday();
        if (!isValidDateStr(dateStr)) {
          sendJson(400, { ok: false, error: '日期应为 YYYY-MM-DD 且必须是真实存在的日期' });
          return;
        }
        const rawSubj = p.subject == null ? '' : String(p.subject).trim();
        const subject = rawSubj === '' ? null : rawSubj;
        if (subject !== null && TASK_SUBJECTS.indexOf(subject) < 0) {
          sendJson(400, { ok: false, error: '科目只能是 408 / 政治 / 数学一 / 英语一' });
          return;
        }
        const db = new DatabaseSync(DB_PATH);
        // source 恒为 'user'，不吃客户端传的值——否则前端可以伪造「这是 AI 布置的」，
        // 而来源正是 agent 事后判断「用户自己加过什么」的依据。
        const info = db.prepare(
          "INSERT INTO daily_tasks (task_date, text, source, subject) VALUES (?, ?, 'user', ?)"
        ).run(dateStr, text, subject);
        const id = Number(info.lastInsertRowid);
        const task = db.prepare(
          'SELECT id, task_date, text, source, subject, done, done_at, deleted, created_at ' +
          'FROM daily_tasks WHERE id = ?'
        ).get(id);
        db.close();
        sendJson(200, { ok: true, task });
      } catch (e) {
        sendJson(500, { ok: false, error: e.message });
      }
    });
    return;
  }

  // POST /api/tasks/done {id, done}
  if (url === '/api/tasks/done' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const p = JSON.parse(body || '{}');
        const id = Number(p.id);
        if (!Number.isInteger(id) || id <= 0) {
          sendJson(400, { ok: false, error: 'id 必须是正整数' });
          return;
        }
        const done = p.done ? 1 : 0;
        const db = new DatabaseSync(DB_PATH);
        // done_at 用 SQL 的 localtime 生成，和 created_at 口径一致（不要用 toISOString，那是 UTC）
        const info = db.prepare(
          "UPDATE daily_tasks SET done = ?, " +
          "done_at = CASE WHEN ? = 1 THEN datetime('now','localtime') ELSE NULL END, " +
          "updated_at = datetime('now','localtime') WHERE id = ? AND deleted = 0"
        ).run(done, done, id);
        db.close();
        if (!info.changes) { sendJson(404, { ok: false, error: '任务不存在或已删除' }); return; }
        sendJson(200, { ok: true, id, done: !!done });
      } catch (e) {
        sendJson(500, { ok: false, error: e.message });
      }
    });
    return;
  }

  // POST /api/tasks/delete {id} — 软删除，行保留供 agent 事后读取
  if (url === '/api/tasks/delete' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const p = JSON.parse(body || '{}');
        const id = Number(p.id);
        if (!Number.isInteger(id) || id <= 0) {
          sendJson(400, { ok: false, error: 'id 必须是正整数' });
          return;
        }
        const db = new DatabaseSync(DB_PATH);
        const info = db.prepare(
          "UPDATE daily_tasks SET deleted = 1, deleted_at = datetime('now','localtime'), " +
          "updated_at = datetime('now','localtime') WHERE id = ? AND deleted = 0"
        ).run(id);
        db.close();
        if (!info.changes) { sendJson(404, { ok: false, error: '任务不存在或已删除' }); return; }
        sendJson(200, { ok: true, id });
      } catch (e) {
        sendJson(500, { ok: false, error: e.message });
      }
    });
    return;
  }

  // ==========================================================================
  // 错题针对性解析与追问（2026-09-14）
  //
  // 与 /api/grade 的分工：批改是「判断对错并给分」；这里是「针对学生选的那个
  // 错项讲清楚为什么错」。题库自带的 explanation 通常只讲正确项为什么对，
  // 不讲错选为什么不行——而错选恰恰暴露真正的理解偏差。
  //
  // 所有问答落 explain_log 留痕（只增不删，文字不占地方）：
  //   ① 后续完善笔记的一手素材  ② 同题再错可直接复用，不重复烧 token
  //   ③ 周定时任务汇总本周错题时取数
  // ==========================================================================
  const newThreadId = () =>
    Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);

  /**
   * 读题目并推导「正确答案」的可读文本。
   * 选择题的 content.answer 是**序号**，直接丢给模型毫无意义，必须翻成
   * 「C. 选项原文」；判断题是布尔；填空/简答才用 reference_answer。
   */
  const loadQuestionForExplain = (db, qid) => {
    const row = db.prepare('SELECT id, type, content FROM questions WHERE id = ?').get(qid);
    if (!row) return null;
    let content = {};
    try { content = JSON.parse(row.content); } catch (e) { content = { stem: row.content }; }
    let correct = '';
    if (typeof content.answer === 'number' && Array.isArray(content.options)
        && content.options[content.answer] != null) {
      correct = String.fromCharCode(65 + content.answer) + '. ' + content.options[content.answer];
    } else if (typeof content.answer === 'boolean') {
      correct = content.answer ? '正确' : '错误';
    } else {
      correct = content.reference_answer || String(content.answer == null ? '' : content.answer);
    }
    return { row, content, correct };
  };

  // POST /api/explain {question_id, card_id?, chosen?, subject?, topic_id?, mode?, question?, deep?}
  //
  // question：学生**自己输入**的问题（答对/看答案后「我有具体想问的」）。
  // 用户明确要求：答对的场合给一个空的追问框、不要预设问题清单，所以这个字段
  // 由前端在提交时才带上；带上它就以他的问题为中心回答（见 grade_llm 的 ASK_CENTERED）。
  if (url === '/api/explain' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', async () => {
      try {
        const p = JSON.parse(body || '{}');
        const qid = String(p.question_id || '').trim();
        if (!qid) { sendJson(400, { ok: false, error: '缺少 question_id' }); return; }
        if (!gradeLlm.hasKey()) {
          sendJson(503, { ok: false, error: '未配置 DeepSeek API key：请在 src/.secrets.json 里填 deepseek.api_key，或设置环境变量 DEEPSEEK_API_KEY' });
          return;
        }
        const db = new DatabaseSync(DB_PATH, { readOnly: true });
        const info = loadQuestionForExplain(db, qid);
        // 设置页的 explain_quick 决定默认要不要思考（客户端 deep:true 可单次覆盖）
        const quick = explainWantQuick(p, readCfgValue(db, 'explain_quick', 'on'));
        const sysPrompt = String(readCfgValue(db, 'prompt_explain', '') || '').trim();
        db.close();
        if (!info) { sendJson(404, { ok: false, error: '题目不存在：' + qid }); return; }

        const chosen = p.chosen == null ? '' : String(p.chosen);
        const correct = String(p.correct || info.correct || '');
        // mode=correct：答对了但仍想追问（不确定/想深挖考点）。此时没有"错选"可讲，
        // 换成讲陷阱、边界与自查——否则模型会去纠正一个根本不存在的错误。
        const mode = p.mode === 'correct' ? 'correct' : 'wrong';
        const ask = String(p.question || '').trim().slice(0, 1000);   // 学生自己输入的问题
        const t0 = Date.now();
        const r = await gradeLlm.explainAnswer({ content: info.content }, chosen, correct,
          { mode, quick, ask, system: sysPrompt });
        console.log('[Explain] ' + qid + ' mode=' + mode + ' chosen=' + (chosen || '-')
          + (ask ? ' 自带问题「' + ask.slice(0, 20) + '…」' : '') + ' → ' +
                    (r.ok ? r.text.length + '字' : 'FAIL ' + r.error) + ' (' + (Date.now() - t0) + 'ms)');
        if (!r.ok) { sendJson(502, { ok: false, error: r.error }); return; }

        const threadId = newThreadId();
        const cardId = p.card_id != null && Number.isInteger(Number(p.card_id)) ? Number(p.card_id) : null;
        const subject = p.subject ? String(p.subject) : null;
        const topicId = p.topic_id ? String(p.topic_id) : null;
        const wdb = new DatabaseSync(DB_PATH);
        const ins = wdb.prepare(
          'INSERT INTO explain_log (thread_id, question_id, card_id, subject, topic_id, ' +
          'chosen, correct, role, content, mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
        );
        // 首轮日志：学生自己提问的，就把问题本身记下来（前缀【我问】让 followup 能重建同样的口径）
        const headText = ask
          ? ('【我问】' + ask)
          : (mode === 'correct'
              ? ('（答对了，但仍有疑问）我的选择：' + (chosen || correct || '正确项'))
              : (chosen ? ('我的选择：' + chosen) : '（未作答，直接看了答案）'));
        ins.run(threadId, qid, cardId, subject, topicId, chosen, correct, 'user', headText, mode);
        ins.run(threadId, qid, cardId, subject, topicId, chosen, correct, 'assistant', r.text, mode);
        wdb.close();
        sendJson(200, { ok: true, thread_id: threadId, text: r.text, correct, mode });
      } catch (e) {
        console.error('[Explain] error:', e.message);
        sendJson(500, { ok: false, error: e.message });
      }
    });
    return;
  }

  // POST /api/explain/followup {thread_id, message}
  if (url === '/api/explain/followup' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', async () => {
      try {
        const p = JSON.parse(body || '{}');
        const threadId = String(p.thread_id || '').trim();
        const message = String(p.message || '').trim();
        if (!threadId) { sendJson(400, { ok: false, error: '缺少 thread_id' }); return; }
        if (!message) { sendJson(400, { ok: false, error: '追问内容不能为空' }); return; }
        if (message.length > 1000) { sendJson(400, { ok: false, error: '追问最多 1000 字' }); return; }
        if (!gradeLlm.hasKey()) {
          sendJson(503, { ok: false, error: '未配置 DeepSeek API key' });
          return;
        }
        const db = new DatabaseSync(DB_PATH, { readOnly: true });
        const rows = db.prepare(
          'SELECT role, content, question_id, chosen, correct, mode FROM explain_log ' +
          'WHERE thread_id = ? ORDER BY id ASC'
        ).all(threadId);
        if (!rows.length) { db.close(); sendJson(404, { ok: false, error: '找不到该线索' }); return; }
        const head = rows[0];
        const q = loadQuestionForExplain(db, head.question_id);
        const quick = explainWantQuick(p, readCfgValue(db, 'explain_quick', 'on'));
        const sysPrompt = String(readCfgValue(db, 'prompt_explain', '') || '').trim();
        db.close();
        if (!q) { sendJson(404, { ok: false, error: '原题已不存在：' + head.question_id }); return; }

        // rows[0] 是首轮那条提示（「我的选择：X」/「【我问】问题」），explainFollowup 内部会
        // 按同样的口径重建它，所以历史从 rows[1] 开始（首轮解析 + 之后的往来）。
        const history = rows.slice(1).map(r => ({ role: r.role, content: r.content }));
        const headAsk = /^【我问】/.test(String(head.content || ''))
          ? String(head.content).replace(/^【我问】/, '') : '';
        const t0 = Date.now();
        const r = await gradeLlm.explainFollowup(
          { content: q.content }, head.chosen, head.correct, history, message,
          { mode: head.mode === 'correct' ? 'correct' : 'wrong', quick, ask: headAsk, system: sysPrompt });
        console.log('[Explain] followup ' + threadId + ' → ' +
                    (r.ok ? r.text.length + '字' : 'FAIL ' + r.error) + ' (' + (Date.now() - t0) + 'ms)');
        if (!r.ok) { sendJson(502, { ok: false, error: r.error }); return; }

        const wdb = new DatabaseSync(DB_PATH);
        const ins = wdb.prepare(
          'INSERT INTO explain_log (thread_id, question_id, card_id, subject, topic_id, ' +
          'chosen, correct, role, content, mode) VALUES (?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?)'
        );
        const keepMode = head.mode === 'correct' ? 'correct' : 'wrong';
        ins.run(threadId, head.question_id, head.chosen, head.correct, 'user', message, keepMode);
        ins.run(threadId, head.question_id, head.chosen, head.correct, 'assistant', r.text, keepMode);
        wdb.close();
        sendJson(200, { ok: true, text: r.text });
      } catch (e) {
        console.error('[Explain] followup error:', e.message);
        sendJson(500, { ok: false, error: e.message });
      }
    });
    return;
  }

  // GET /api/explain?question_id=X&chosen=Y&mode=Z — 取「同题且同错选」的上一次解析
  //
  // ⚠️ 必须带 chosen 一起过滤（2026-09-21 修）：
  //   原先只按 question_id 取**最新一条**线索，于是「先错选 B → 后来错选 C → 又错选 B」
  //   时查到的是 C 那条，前端一比 chosen 不相等就放弃复用、**又去问一遍 AI**——
  //   可 B 的解析其实早就生成过。现在按 (题 + 错选 + 模式) 精确取最近一条，
  //   不管中间穿插了多少别的错选。
  if (url.startsWith('/api/explain') && req.method === 'GET') {
    const q = new URL('http://x' + url).searchParams;
    const qid = (q.get('question_id') || '').trim();
    if (!qid) { sendJson(400, { ok: false, error: '缺少 question_id' }); return; }
    try {
      const chosen = String(q.get('chosen') || '').trim();
      const mode = String(q.get('mode') || '').trim();
      let sql = "SELECT thread_id, chosen, correct, mode, created_at FROM explain_log "
        + "WHERE question_id = ? AND role = 'user'";
      const args = [qid];
      if (chosen) { sql += ' AND chosen = ?'; args.push(chosen); }
      if (mode === 'wrong' || mode === 'correct') {
        sql += " AND COALESCE(mode, 'wrong') = ?"; args.push(mode);
      }
      sql += ' ORDER BY id DESC LIMIT 1';
      const db = new DatabaseSync(DB_PATH, { readOnly: true });
      const head = db.prepare(sql).get(...args);
      let replies = [];
      if (head) {
        replies = db.prepare(
          "SELECT role, content, created_at FROM explain_log " +
          "WHERE thread_id = ? ORDER BY id ASC"
        ).all(head.thread_id);
        console.log('[Explain] 命中历史解析（题 ' + qid + ' 错选「' + (head.chosen || '-')
          + '」mode=' + (head.mode || 'wrong') + '，' + head.created_at + '）→ 复用，不再问模型');
      }
      db.close();
      sendJson(200, {
        ok: true,
        thread_id: head ? head.thread_id : null,
        chosen: head ? head.chosen : null,
        // mode 要回传：答错讲解与答对追问是两套要求，复用错模式的历史会让模型
        // 去纠正一个根本不存在的错选
        mode: head ? (head.mode || 'wrong') : null,
        created_at: head ? head.created_at : null,
        messages: replies,
      });
    } catch (e) {
      sendJson(500, { ok: false, error: e.message });
    }
    return;
  }

  // ==========================================================================
  // 早间回顾（并入大盘，2026-09-20）
  //
  // 原先它是独立 HTML，打卡与间隔重复都存各设备自己的 localStorage——换设备就各算
  // 各的。并入大盘后：内容读 src/morning_review.json（由 morning-review 工作流维护），
  // 状态存 SQLite，队列也在服务端算。平板只是发请求，所有进度都留在这台电脑上。
  // ==========================================================================
  const MR_PATH = path.join(__dirname, 'morning_review.json');
  let mrCache = { mtime: 0, data: null };
  function loadMorningReview() {
    try {
      const st = fs.statSync(MR_PATH);
      if (mrCache.data && mrCache.mtime === st.mtimeMs) return mrCache.data;
      const data = JSON.parse(fs.readFileSync(MR_PATH, 'utf-8'));
      mrCache = { mtime: st.mtimeMs, data };
      return data;
    } catch (e) { return null; }
  }

  const FC_INTERVALS = [1, 2, 4, 7, 15, 30];
  const FC_SUBJECT_LABEL = { '408': '408', math: '数学一', politics: '政治' };
  const dayNum = (d) => Math.floor(new Date(d + 'T00:00:00').getTime() / 86400000);
  function addDaysStr(d, n) {
    const x = new Date(d + 'T00:00:00');
    x.setDate(x.getDate() + n);
    return x.getFullYear() + '-' + String(x.getMonth() + 1).padStart(2, '0')
      + '-' + String(x.getDate()).padStart(2, '0');
  }
  const isDateStr = (s) => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s);

  function mrSrMap(db) {
    const out = {};
    try {
      for (const r of db.prepare('SELECT card_id, step, next_due, mastered FROM mr_sr').all()) {
        out[r.card_id] = { step: r.step, nextDue: r.next_due, mastered: !!r.mastered };
      }
    } catch (e) { /* 表未迁移时按无状态处理 */ }
    return out;
  }
  function mrCheckins(db) {
    try { return db.prepare('SELECT check_date FROM mr_checkin ORDER BY check_date').all().map(r => r.check_date); }
    catch (e) { return []; }
  }
  // 连续天数：打卡记录 + 有学习内容的日期合并后，从最新一天往回数连续段
  function mrStreak(dates) {
    const uniq = [...new Set(dates)].sort();
    if (!uniq.length) return 0;
    let streak = 1;
    for (let i = uniq.length - 1; i > 0; i--) {
      if (dayNum(uniq[i]) - dayNum(uniq[i - 1]) === 1) streak++;
      else break;
    }
    return streak;
  }
  // 队列在服务端算，保证多设备看到同一批卡（原逻辑照搬，只是换了数据源）
  function mrBuildQueue(cards, sr, startDate, today) {
    const dayInCycle = dayNum(today) - dayNum(startDate) + 1;
    const newCards = cards.filter(c => c.day <= dayInCycle && !sr[c.id]);
    const reviewCards = cards.filter(c => {
      const s = sr[c.id];
      return s && !s.mastered && s.step >= 0 && s.nextDue <= today;
    }).sort((a, b) => {
      const sa = sr[a.id], sb = sr[b.id];
      return sa.nextDue < sb.nextDue ? -1 : sa.nextDue > sb.nextDue ? 1 : sa.step - sb.step;
    }).slice(0, 10);
    return { newCards, reviewCards, all: reviewCards.concat(newCards) };
  }

  // GET /api/morning-review/overview — 日历/统计（轻量，不含正文）
  if (url === '/api/morning-review/overview' && req.method === 'GET') {
    try {
      const mr = loadMorningReview();
      if (!mr) { sendJson(503, { ok: false, error: 'morning_review.json 不存在或不可读' }); return; }
      const db = new DatabaseSync(DB_PATH, { readOnly: true });
      const checkins = mrCheckins(db);
      db.close();
      const set = new Set(checkins);
      const days = Object.keys(mr.days).sort().map(d => {
        const day = mr.days[d] || {};
        return {
          date: d, weekday: day.weekday || '', subject: day.subject || '',
          studied: !!day.studied, checked: set.has(d),
          review_count: (day.review || []).length, has_english: !!day.english,
        };
      });
      const fc = mr.flashcards || { subjects: {}, cards: [], startDate: '' };
      sendJson(200, {
        ok: true, today: localToday(), days,
        checkins,
        streak: mrStreak([...checkins, ...days.filter(d => d.studied).map(d => d.date)]),
        total_days: new Set([...checkins, ...days.filter(d => d.studied).map(d => d.date)]).size,
        flashcards: { start_date: fc.startDate, subjects: fc.subjects, total: (fc.cards || []).length },
      });
    } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    return;
  }

  // GET /api/morning-review/day?date=YYYY-MM-DD — 某天完整内容
  if (url.startsWith('/api/morning-review/day') && req.method === 'GET') {
    try {
      const q = new URL('http://x' + url).searchParams;
      const date = q.get('date') || '';
      const mr = loadMorningReview();
      if (!mr) { sendJson(503, { ok: false, error: 'morning_review.json 不可读' }); return; }
      const day = mr.days[date];
      if (!day) { sendJson(404, { ok: false, error: '该日期暂无复习内容：' + date }); return; }
      const db = new DatabaseSync(DB_PATH, { readOnly: true });
      const checked = mrCheckins(db).includes(date);
      db.close();
      sendJson(200, { ok: true, date, checked, day });
    } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    return;
  }

  // GET /api/morning-review/queue?date= — 早间回顾闪卡队列（服务端算）
  if (url.startsWith('/api/morning-review/queue') && req.method === 'GET') {
    try {
      const q = new URL('http://x' + url).searchParams;
      const date = isDateStr(q.get('date')) ? q.get('date') : localToday();
      const mr = loadMorningReview();
      if (!mr || !mr.flashcards) { sendJson(503, { ok: false, error: '闪卡库不可读' }); return; }
      const fc = mr.flashcards;
      const db = new DatabaseSync(DB_PATH, { readOnly: true });
      const sr = mrSrMap(db);
      db.close();
      const queues = {}, tabs = [];
      for (const key of Object.keys(fc.subjects || {})) {
        const cards = (fc.cards || []).filter(c => c.subject === key);
        const qq = mrBuildQueue(cards, sr, fc.startDate, date);
        queues[key] = { label: FC_SUBJECT_LABEL[key] || fc.subjects[key].name || key,
          newCards: qq.newCards, reviewCards: qq.reviewCards, all: qq.all };
        tabs.push({ key, label: FC_SUBJECT_LABEL[key] || key,
          new: qq.newCards.length, due: qq.reviewCards.length, total: qq.all.length });
      }
      sendJson(200, { ok: true, date, tabs, queues, sr });
    } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    return;
  }

  // POST /api/morning-review/checkin {date, on}
  if (url === '/api/morning-review/checkin' && req.method === 'POST') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', () => {
      try {
        const p = JSON.parse(body || '{}');
        if (!isDateStr(p.date)) { sendJson(400, { ok: false, error: 'date 需为 YYYY-MM-DD' }); return; }
        const on = p.on !== false;
        const db = new DatabaseSync(DB_PATH);
        if (on) db.prepare('INSERT OR IGNORE INTO mr_checkin (check_date) VALUES (?)').run(p.date);
        else db.prepare('DELETE FROM mr_checkin WHERE check_date = ?').run(p.date);
        const checkins = mrCheckins(db);
        db.close();
        sendJson(200, { ok: true, date: p.date, checked: on, checkins });
      } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    });
    return;
  }

  // POST /api/morning-review/sr {card_id, mark:'ok'|'no', date} — 服务端推进间隔重复
  if (url === '/api/morning-review/sr' && req.method === 'POST') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', () => {
      try {
        const p = JSON.parse(body || '{}');
        if (!p.card_id) { sendJson(400, { ok: false, error: '需要 card_id' }); return; }
        if (['ok', 'no'].indexOf(p.mark) < 0) { sendJson(400, { ok: false, error: "mark 只能是 ok 或 no" }); return; }
        const date = isDateStr(p.date) ? p.date : localToday();
        const db = new DatabaseSync(DB_PATH);
        const row = db.prepare('SELECT step, mastered FROM mr_sr WHERE card_id = ?').get(p.card_id);
        const step = row ? row.step : -1;
        let next;
        if (p.mark === 'ok') {
          next = { step, next_due: '', mastered: 1 };
        } else {
          const ns = Math.min(Math.max(0, step + 1), FC_INTERVALS.length - 1);
          next = { step: ns, next_due: addDaysStr(date, FC_INTERVALS[ns]), mastered: 0 };
        }
        db.prepare(`
          INSERT INTO mr_sr (card_id, step, next_due, mastered, updated_at)
          VALUES (?, ?, ?, ?, datetime('now','localtime'))
          ON CONFLICT(card_id) DO UPDATE SET
            step = excluded.step, next_due = excluded.next_due,
            mastered = excluded.mastered, updated_at = excluded.updated_at
        `).run(p.card_id, next.step, next.next_due, next.mastered);
        db.close();
        sendJson(200, { ok: true, card_id: p.card_id, step: next.step,
          next_due: next.next_due, mastered: !!next.mastered });
      } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    });
    return;
  }

  // ==========================================================================
  // 错题复盘 & 薄弱点学习（2026-09-20）
  //
  // 设计取舍：
  //   · 复盘数据存文件（Review/<科目>/…），不塞进 SQLite——用户要按科目文件夹、
  //     要能自己放资料，而且别的 agent 的定时任务靠 grep 这些文件取数。
  //   · 检索一律分层限量（L1 索引/画像 → L2 单会话错题 → L3 对话全文），
  //     喂给模型前都有条数与字数上限，避免数据攒多了把 token 吃光。
  //   · 专项练习不重造轮子：给 /api/flashcards/session 加 topic 前缀筛选，
  //     复盘页把考点传过去并切到「闪」页，于是手写、简答、AI 批改、FSRS 全部继承。
  // ==========================================================================
  const reviewStore = require('./review_store');

  // PDF 转图片依赖 PyMuPDF。本机只有部分 Python 装了它（miniconda3 就没有），
  // 所以不能像 targeted-cards 那样只写 'python'——那会随机命中一个没有 fitz 的解释器。
  // 按候选顺序试，并区分「缺 PyMuPDF / python 不存在」（继续试下一个）
  // 与「PDF 本身有问题」（加密、损坏，直接返回，不浪费尝试）。
  const PDF_PY_CANDS = [
    process.env.KAORYAN_PYTHON,
    process.env.PYTHON_PATH,
    'C:\\Users\\92534\\AppData\\Local\\Programs\\Python\\Python311\\python.exe',
    'python',
  ].filter(Boolean);

  function runPdfWith(py, pdfAbs, upDir, maxPages) {
    return new Promise((resolve) => {
      const args = [path.join(__dirname, 'pdf_to_images.py'), pdfAbs, upDir,
        '--max-pages', String(maxPages)];
      let out = '', err = '';
      let proc;
      try {
        proc = spawn(py, args, { cwd: __dirname });
      } catch (e) {
        resolve({ ok: false, error: e.message, spawnFailed: true });
        return;
      }
      proc.stdout.on('data', d => { out += d; });
      proc.stderr.on('data', d => { err += d; });
      proc.on('error', e => {
        resolve({ ok: false, error: `${py}: ${e.message}`, spawnFailed: e.code === 'ENOENT' });
      });
      proc.on('close', code => {
        // 脚本约定 stdout 末行是 JSON；拿不到就当错误并带上 stderr
        const line = out.trim().split('\n').filter(Boolean).pop();
        try {
          const j = JSON.parse(line);
          if (!j.ok) j.error = (j.error || '') + (err ? ' | ' + err.slice(0, 200) : '');
          resolve(j);
        } catch (e) {
          resolve({ ok: false, error: `退出码 ${code}，无 JSON 输出：${String(err || out).slice(0, 300)}` });
        }
      });
    });
  }

  async function runPdfToImages(pdfAbs, upDir, maxPages) {
    const tried = [];
    for (const py of PDF_PY_CANDS) {
      if (tried.includes(py)) continue;
      tried.push(py);
      const r = await runPdfWith(py, pdfAbs, upDir, maxPages);
      if (r.ok) return r;
      const noInterpreter = r.spawnFailed;
      const noFitz = /PyMuPDF|fitz|No module named/i.test(String(r.error || ''));
      if (!noInterpreter && !noFitz) return r;   // PDF 本身的问题，换 Python 也没用
      console.warn('[Review] PDF 转换换下一个 Python：', py, '→', String(r.error).slice(0, 120));
    }
    return {
      ok: false,
      error: '没找到带 PyMuPDF 的 Python（试过：' + tried.join('、') + '）。'
        + '可设环境变量 KAORYAN_PYTHON 指向带 fitz 的解释器，或 pip install pymupdf',
    };
  }

  // 各科笔记索引（JSON，Node 可直接读）。只用于「标题/标签命中 → 给出路径」，
  // 正文由前端按需走 /api/notes/preview 取，绝不在这里拼进 prompt。
  const NOTES_INDEX_FILES = {
    '408': ['408/notes_index.json'],
    '数学一': ['Math/notes_index.json'],
    '政治': ['Politics/notes_index.json'],
    '英语一': ['English/notes_index.json'],
  };
  const notesCache = new Map();
  function loadNotesIndex(subject) {
    if (notesCache.has(subject)) return notesCache.get(subject);
    const flat = [];
    for (const rel of (NOTES_INDEX_FILES[subject] || [])) {
      try {
        const abs = path.join(ROOT_DIR, rel);
        const doc = JSON.parse(fs.readFileSync(abs, 'utf-8'));
        const dir = rel.split('/')[0];
        for (const [code, blk] of Object.entries(doc.subjects || {})) {
          for (const e of (blk.entries || [])) {
            const link = (e.links || [])[0] || {};
            let p = String(link.path || '').replace(/^\.\//, '');
            flat.push({
              id: e.id || '', title: String(e.title || '').slice(0, 160),
              chapter: e.chapter || '', level: e.level || '', status: e.status || '',
              tags: Array.isArray(e.tags) ? e.tags.slice(0, 8).map(String) : [],
              // 归一成相对 ROOT 的路径，直接能喂给 /api/notes/preview
              path: p ? (dir + '/' + code + '/' + p).replace(/\/+/g, '/') : '',
            });
          }
        }
      } catch (err) { /* 缺文件就少一路检索，不影响主流程 */ }
    }
    notesCache.set(subject, flat);
    return flat;
  }
  // 关键词命中：把查询切成 2 字以上的片段，命中 title 或 tags 即算，按命中数排序。
  // 命中数相同时「有直链的优先」——很多条目是「已整理到第X章」的问答，links 为空数组，
  // 给出来也点不开，不如让能打开的排前面。
  function searchNotes(subject, query, limit) {
    const toks = String(query || '').toLowerCase().split(/[\s,，、/]+/).filter(t => t.length >= 2);
    if (!toks.length) return [];
    const scored = [];
    for (const e of loadNotesIndex(subject)) {
      const hay = (e.title + ' ' + e.tags.join(' ')).toLowerCase();
      let hit = 0;
      for (const t of toks) if (hay.includes(t)) hit++;
      if (hit) scored.push({ e, hit });
    }
    return scored.sort((a, b) =>
      (b.hit - a.hit) || ((b.e.path ? 1 : 0) - (a.e.path ? 1 : 0))
    ).slice(0, limit || 6).map(x => x.e);
  }
  // 考点检索：topics 表按名称模糊匹配，带现有卡数与正确率（判断"练过没有"）
  function searchTopics(db, subject, query, limit) {
    const toks = String(query || '').split(/[\s,，、/]+/).filter(t => t.length >= 2).slice(0, 6);
    if (!toks.length) return [];
    const like = [], bind = [];
    for (const t of toks) { like.push('t.name LIKE ?'); bind.push('%' + t + '%'); }
    let sql = `
      SELECT t.id, t.subject, t.name, t.exam_weight,
             (SELECT COUNT(*) FROM cards c JOIN questions q ON c.question_id = q.id
                WHERE q.topic_id = t.id AND COALESCE(c.suspended,0) = 0) AS cards,
             (SELECT COUNT(*) FROM review_log rl JOIN questions q ON q.id = rl.question_id
                WHERE q.topic_id = t.id) AS answered,
             (SELECT COUNT(*) FROM review_log rl JOIN questions q ON q.id = rl.question_id
                WHERE q.topic_id = t.id AND rl.rating >= 3) AS correct
        FROM topics t
       WHERE (${like.join(' OR ')})`;
    if (subject && subject !== 'all') { sql += ' AND t.subject = ?'; bind.push(subject); }
    sql += ' ORDER BY t.exam_weight DESC LIMIT ?';
    bind.push(limit || 8);
    try {
      return db.prepare(sql).all(...bind).map(r => ({
        id: r.id, subject: r.subject, name: r.name, exam_weight: r.exam_weight,
        cards: r.cards, answered: r.answered,
        acc: r.answered ? Math.round(r.correct * 100 / r.answered) : null,
      }));
    } catch (e) { console.warn('[Study] topic 检索失败:', e.message); return []; }
  }
  // 笔记前缀：考点 ID 取前两段（408-OS-03-01 → 408-OS），与大盘选题口径一致
  const topicPrefix = (tid) => String(tid || '').split('-').slice(0, 2).join('-');
  // 喂给模型的笔记行：没存直链的要如实标注，否则模型会编一个路径给学生
  const noteLine = (n) => '- ' + n.title + (n.path ? ' → ' + n.path : '（未存直链，按章节名定位）');

  // 把画像里的错因压成几行喂给模型（限量，避免 prompt 膨胀）
  function causeDigest(subject, max) {
    const p = reviewStore.readPatterns();
    if (!p || !p.subjects) return [];
    const rows = (p.subjects[subject] && p.subjects[subject].top_causes) || [];
    return rows.slice(0, max || 8).map(c =>
      '- ' + c.cause + '（' + c.count + ' 次，权重 ' + c.score
      + (c.has_cards ? '，已出卡' : '，尚无卡')
      + (c.resolved ? '，已闭环' : '') + '）');
  }

  const REVIEW_SYSTEM = [
    '你是考研错题复盘教练，正陪学生复盘他刚做完的一套题。',
    '',
    '工作方式（重要）：',
    '1. **一次只推进一小步**。先看上传的卷子/照片，让学生说他的思路，不要一口气输出长篇解析。',
    '2. 追问要具体：问他在哪一步开始不确定、当时怎么想的、排除了哪个选项——错因藏在这些地方。',
    '3. 判因时区分：概念混淆 / 计算失误 / 审题漏条件 / 记忆模糊 / 方法不熟。说清依据，别贴万能标签。',
    '4. 学生答对但靠猜的，也要记下来——那是最危险的一类。',
    '5. 公式用 $...$；回复控制在 200 字以内，除非学生在要完整讲解。',
  ];
  const STUDY_SYSTEM = [
    '你是考研辅导老师。学生直接说出他觉得自己薄弱的知识点，你要帮他巩固。',
    '',
    '工作方式：',
    '1. 先用下面的检索结果确认范围，**不要凭空编考点**；命中信息不足时先问清是哪一章/哪类题。',
    '2. 讲清原理后，立刻给 1-2 道小题让他做（或指出该去刷哪类卡），不要只讲不练。',
    '3. 如果他有相关历史错因，明确点出来：「你上次就栽在这」。',
    '4. 需要他重读笔记时，给出笔记标题与路径，让他点开看，不要替他大段抄笔记原文。',
    '5. 公式用 $...$；回复控制在 250 字以内。',
  ];

  // GET /api/review/overview — L1 热数据：会话索引 + 错因画像摘要
  if (url === '/api/review/overview' && req.method === 'GET') {
    try {
      const patterns = reviewStore.readPatterns();
      const bySubject = {};
      for (const s of reviewStore.SUBJECTS) bySubject[s] = reviewStore.listSessions(s);
      sendJson(200, {
        ok: true, subjects: reviewStore.SUBJECTS, sessions: bySubject,
        patterns: patterns ? {
          generated_at: patterns.generated_at, half_life_days: patterns.half_life_days,
          subjects: patterns.subjects,
        } : null,
        patterns_stale: !patterns || !patterns.generated_at,
      });
    } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    return;
  }

  // GET /api/review/session?subject=&sid= — 单会话详情（L2/L3，按需才读）
  if (url.startsWith('/api/review/session') && req.method === 'GET') {
    try {
      const q = new URL('http://x' + url).searchParams;
      const subject = q.get('subject') || '', sid = q.get('sid') || '';
      const s = reviewStore.loadSession(subject, sid);
      if (!s) { sendJson(404, { ok: false, error: '会话不存在' }); return; }
      sendJson(200, { ok: true, meta: s.meta, errors: s.errors, turns: s.turns, transcript: s.transcript });
    } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    return;
  }

  // POST /api/review/session {subject, title?, source_kind?, note?}
  if (url === '/api/review/session' && req.method === 'POST') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', () => {
      try {
        const p = JSON.parse(body || '{}');
        const meta = reviewStore.createSession({
          subject: p.subject, title: p.title, sourceKind: p.source_kind, note: p.note,
        });
        sendJson(200, { ok: true, meta });
      } catch (e) { sendJson(400, { ok: false, error: e.message }); }
    });
    return;
  }

  // POST /api/review/upload {subject, sid, image: dataURL, filename?}
  // 图片落盘到会话目录，只回相对路径：前端要显示时走 /api/notes/asset（同样的越权防护）。
  if (url === '/api/review/upload' && req.method === 'POST') {
    const MAX_IMG = 8 * 1024 * 1024;
    let body = '';
    req.on('data', c => body += c);
    req.on('end', () => {
      try {
        const p = JSON.parse(body || '{}');
        const m = /^(data:image\/(png|jpeg|jpg|webp|gif);base64,)([\s\S]+)$/.exec(String(p.image || ''));
        if (!m) { sendJson(400, { ok: false, error: 'image 必须是 data:image/...;base64, 形式' }); return; }
        const buf = Buffer.from(m[3], 'base64');
        if (!buf.length) { sendJson(400, { ok: false, error: '图片内容为空' }); return; }
        if (buf.length > MAX_IMG) { sendJson(413, { ok: false, error: '图片过大（限 8MB，请先压缩）' }); return; }
        const sid = String(p.sid || '');
        const subject = String(p.subject || '');
        if (!reviewStore.SUBJECTS.includes(subject)) { sendJson(400, { ok: false, error: '科目不合法' }); return; }
        const dir = reviewStore.sessionDir(subject, sid);
        if (!fs.existsSync(dir)) { sendJson(404, { ok: false, error: '会话不存在' }); return; }
        const ext = m[2] === 'jpeg' ? '.jpg' : '.' + m[2];
        const name = 'img-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6) + ext;
        fs.writeFileSync(path.join(dir, 'uploads', name), buf);
        const rel = 'Review/' + subject + '/sessions/' + sid + '/uploads/' + name;
        const meta = reviewStore.attachFile(subject, sid, rel, 'image');
        sendJson(200, { ok: true, path: rel, bytes: buf.length, meta });
      } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    });
    return;
  }

  // POST /api/review/upload-pdf {subject, sid, pdf: dataURL, filename?, max_pages?}
  // 手机扫描整沓卷子导出的是 PDF，而模型端点只收图片——所以本地先转页图。
  // 页图落到会话 uploads/ 并登记进 meta.files：PDF 只上传一次，之后多轮对话
  // 用 page_paths 引用，不必反复把几 MB base64 搬来搬去。
  if (url === '/api/review/upload-pdf' && req.method === 'POST') {
    const MAX_PDF = 40 * 1024 * 1024;   // 扫描卷 PDF 动辄十几 MB
    let body = '', overflow = false;
    req.on('data', c => {
      body += c;
      if (body.length > MAX_PDF * 1.4 && !overflow) {
        overflow = true;
        sendJson(413, { ok: false, error: 'PDF 过大（限 40MB，可先压缩或分页上传）' });
        req.destroy();
      }
    });
    req.on('end', async () => {
      if (overflow) return;
      try {
        const p = JSON.parse(body || '{}');
        const m = /^data:application\/pdf;base64,([\s\S]+)$/.exec(String(p.pdf || ''));
        if (!m) { sendJson(400, { ok: false, error: 'pdf 必须是 data:application/pdf;base64,... 形式' }); return; }
        const buf = Buffer.from(m[1], 'base64');
        if (buf.length < 1024) { sendJson(400, { ok: false, error: 'PDF 内容为空' }); return; }
        const subject = String(p.subject || ''), sid = String(p.sid || '');
        if (!reviewStore.SUBJECTS.includes(subject)) { sendJson(400, { ok: false, error: '科目不合法' }); return; }
        const dir = reviewStore.sessionDir(subject, sid);
        if (!fs.existsSync(dir)) { sendJson(404, { ok: false, error: '会话不存在' }); return; }

        const upDir = path.join(dir, 'uploads');
        fs.mkdirSync(upDir, { recursive: true });
        // 文件名压成安全串，避免空格/中文在 URL 与命令行里出岔子（中文本身没问题，
        // 但空格和 & 会让参数解析变得易错）
        const stem = String(p.filename || 'scan').replace(/\.pdf$/i, '')
          .replace(/[^\w\u4e00-\u9fa5.-]+/g, '-').slice(0, 48) || 'scan';
        const pdfName = stem + '.pdf';
        const pdfAbs = path.join(upDir, pdfName);
        fs.writeFileSync(pdfAbs, buf);

        const maxPages = Math.max(1, Math.min(20, parseInt(p.max_pages, 10) || 8));
        let conv;
        try {
          conv = await runPdfToImages(pdfAbs, upDir, maxPages);
        } catch (e) {
          conv = { ok: false, error: e.message };
        }
        if (!conv.ok) {
          // 转换失败要如实报出来，别只留一个打不开的 PDF 在目录里
          sendJson(502, {
            ok: false,
            error: 'PDF 转图片失败：' + conv.error,
            pdf_path: 'Review/' + subject + '/sessions/' + sid + '/uploads/' + pdfName,
          });
          return;
        }

        const pages = [];
        for (const pg of conv.pages) {
          const rel = 'Review/' + subject + '/sessions/' + sid + '/uploads/' + pg.file;
          reviewStore.attachFile(subject, sid, rel, 'pdf-page');
          pages.push({ path: rel, page: pg.page, bytes: pg.bytes, width: pg.width, height: pg.height });
        }
        reviewStore.attachFile(subject, sid,
          'Review/' + subject + '/sessions/' + sid + '/uploads/' + pdfName, 'pdf');
        console.log(`[Review] PDF ${pdfName} → ${pages.length} 页（共 ${conv.total_pages} 页）`);
        sendJson(200, {
          ok: true, pages, total_pages: conv.total_pages,
          converted: conv.converted, skipped: conv.skipped || 0,
        });
      } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    });
    return;
  }

  // POST /api/review/errors {subject, sid, errors[]}  —— 覆盖式保存结构化错题
  if (url === '/api/review/errors' && req.method === 'POST') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', () => {
      try {
        const p = JSON.parse(body || '{}');
        if (!Array.isArray(p.errors)) { sendJson(400, { ok: false, error: 'errors 需为数组' }); return; }
        const list = reviewStore.saveErrors(String(p.subject || ''), String(p.sid || ''), p.errors);
        sendJson(200, { ok: true, count: list.length, errors: list });
      } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    });
    return;
  }

  // POST /api/review/chat {subject, sid, message, images?[]}
  // 上下文注入：本会话错题摘要 + 该科历史错因 + 命中笔记（都限量），图片走多模态。
  if (url === '/api/review/chat' && req.method === 'POST') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const p = JSON.parse(body || '{}');
        const subject = String(p.subject || ''), sid = String(p.sid || '');
        const message = String(p.message || '').slice(0, 4000);
        if (!subject || !sid) { sendJson(400, { ok: false, error: '需要 subject 与 sid' }); return; }

        // 允许引用会话里已落盘的页图（PDF 转出来的）：服务端读盘转 dataURL，
        // 这样 PDF 只上传一次，之后多轮对话反复引用，不必每次搬几 MB base64。
        // 路径一律过 resolveAssetPath，越权与非图片扩展名都进不来。
        const diskImgs = [];
        for (const rel of (Array.isArray(p.page_paths) ? p.page_paths.slice(0, 10) : [])) {
          const abs = resolveAssetPath(String(rel));
          if (!abs || !fs.existsSync(abs)) continue;
          const ext = path.extname(abs).slice(1).toLowerCase();
          const mime = ext === 'png' ? 'image/png' : ext === 'webp' ? 'image/webp' : 'image/jpeg';
          diskImgs.push('data:' + mime + ';base64,' + fs.readFileSync(abs).toString('base64'));
        }
        const images = diskImgs.concat(
          (Array.isArray(p.images) ? p.images : []).filter(x => typeof x === 'string').slice(0, 10));

        if (!message && !images.length) {
          sendJson(400, { ok: false, error: '消息与图片不能同时为空' }); return;
        }
        const sess = reviewStore.loadSession(subject, sid);
        if (!sess) { sendJson(404, { ok: false, error: '会话不存在' }); return; }
        if (!gradeLlm.hasKey()) { sendJson(503, { ok: false, error: '未配置 DeepSeek API key' }); return; }

        const errLines = sess.errors.slice(0, 12).map(e =>
          '- ' + (e.title || e.topic_hint || '未命名') + '｜错因:' + e.cause
          + '｜' + (e.resolved ? '已闭环' : '未闭环'));
        const causes = causeDigest(subject, 8);
        const notes = searchNotes(subject,
          sess.errors.map(e => e.topic_hint || e.title).join(' ') + ' ' + message, 5);

        const ctx = [REVIEW_SYSTEM.join('\n'), ''].join('\n')
          + '\n【本次会话已登记的错题】' + (errLines.length ? '\n' + errLines.join('\n') : '（还没有）')
          + '\n\n【该科历史高频错因（来自错因画像）】'
          + (causes.length ? '\n' + causes.join('\n') : '（暂无画像，可先跑 build_review_patterns.py）')
          + '\n\n【可引用的笔记（需要时让学生点开，别抄原文）】'
          + (notes.length ? '\n' + notes.map(noteLine).join('\n') : '（无）');

        const r = await gradeLlm.coachChat(
          [{ role: 'user', content: message, images }], ctx, { maxTokens: 3500 });
        if (!r.ok) { sendJson(502, { ok: false, error: r.error }); return; }
        reviewStore.appendTurn(subject, sid, 'user', message
          + (images.length ? '\n\n（附 ' + images.length + ' 张图'
            + (diskImgs.length ? '，含 ' + diskImgs.length + ' 页 PDF' : '') + '）' : ''));
        reviewStore.appendTurn(subject, sid, 'assistant', r.text);
        sendJson(200, { ok: true, text: r.text });
      } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    });
    return;
  }

  // POST /api/review/extract {subject, sid} — 从对话全文里提炼结构化错题
  // 单独一步而不是每轮都提：提炼要读 transcript（L3），只在复盘收尾时做一次。
  if (url === '/api/review/extract' && req.method === 'POST') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const p = JSON.parse(body || '{}');
        const subject = String(p.subject || ''), sid = String(p.sid || '');
        const sess = reviewStore.loadSession(subject, sid);
        if (!sess) { sendJson(404, { ok: false, error: '会话不存在' }); return; }
        if (!gradeLlm.hasKey()) { sendJson(503, { ok: false, error: '未配置 DeepSeek API key' }); return; }
        const transcript = String(sess.transcript || '').slice(-9000);   // 只取尾部，防超长
        const sys = [
          '从下面这段复盘对话里提炼出**学生真正出过错的知识点**，输出 JSON 数组，不要任何多余文字。',
          '每项字段：topic_hint(考点，尽量用教材章节措辞)、title(一句话概括这道题)、',
          'what_wrong(学生当时错在哪)、cause(错因，归类到：概念混淆/计算失误/审题漏条件/记忆模糊/方法不熟)、',
          'cause_kind(concept|calc|reading|memory|method 之一)、severity(1-5)、evidence(原文里的关键句)。',
          '学生其实答对但靠猜的，也算一条，cause 写「猜对的」。没有就返回 []。',
        ].join('\n');
        const r = await gradeLlm.coachChat([{ role: 'user', content: transcript }], sys, { maxTokens: 3000 });
        if (!r.ok) { sendJson(502, { ok: false, error: r.error }); return; }
        const parsed = gradeLlm.extractJson(r.text);
        const arr = Array.isArray(parsed) ? parsed : (parsed && Array.isArray(parsed.errors) ? parsed.errors : null);
        if (!arr) { sendJson(502, { ok: false, error: '模型没返回可解析的 JSON 数组', raw: String(r.text).slice(0, 400) }); return; }
        sendJson(200, { ok: true, errors: arr.slice(0, 20) });
      } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    });
    return;
  }

  // GET /api/study/context?q=&subject= — 学习区检索（L1：只给索引，正文按需再取）
  if (url.startsWith('/api/study/context') && req.method === 'GET') {
    try {
      const q = new URL('http://x' + url).searchParams;
      const query = String(q.get('q') || '').slice(0, 120);
      const subject = String(q.get('subject') || 'all');
      const db = new DatabaseSync(DB_PATH, { readOnly: true });
      const topics = searchTopics(db, subject, query, 8);
      db.close();
      const subjList = (subject && subject !== 'all') ? [subject] : reviewStore.SUBJECTS;
      const notes = [];
      for (const s of subjList) for (const n of searchNotes(s, query, 3)) notes.push({ ...n, subject: s });
      const causes = [];
      for (const s of subjList) for (const line of causeDigest(s, 5)) causes.push(s + ' ' + line);
      sendJson(200, {
        ok: true, query,
        topics: topics.map(t => ({ ...t, prefix: topicPrefix(t.id) })),
        notes: notes.slice(0, 8),
        causes: causes.slice(0, 10),
      });
    } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    return;
  }

  // POST /api/study/chat {message, subject?, history?[{role,content}]}
  if (url === '/api/study/chat' && req.method === 'POST') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const p = JSON.parse(body || '{}');
        const message = String(p.message || '').slice(0, 4000);
        if (!message) { sendJson(400, { ok: false, error: '消息不能为空' }); return; }
        if (!gradeLlm.hasKey()) { sendJson(503, { ok: false, error: '未配置 DeepSeek API key' }); return; }
        const subject = String(p.subject || 'all');
        const db = new DatabaseSync(DB_PATH, { readOnly: true });
        const topics = searchTopics(db, subject, message, 8);
        db.close();
        const subjList = subject !== 'all' ? [subject] : reviewStore.SUBJECTS;
        const notes = [];
        for (const s of subjList) for (const n of searchNotes(s, message, 3)) notes.push(n);
        const causes = [];
        for (const s of subjList) for (const line of causeDigest(s, 4)) causes.push(s + ' ' + line);

        const ctx = [STUDY_SYSTEM.join('\n'), ''].join('\n')
          + '\n【题库里对得上的考点（含现有卡数与正确率）】'
          + (topics.length ? '\n' + topics.map(t => '- ' + t.id + ' ' + t.name
              + '｜权重' + (t.exam_weight ?? '?') + '｜卡' + t.cards
              + (t.acc != null ? '｜正确率' + t.acc + '%(' + t.answered + '次)' : '｜没练过')).join('\n') : '（无）')
          + '\n\n【可引用的笔记】'
          + (notes.length ? '\n' + notes.map(noteLine).join('\n') : '（无）')
          + '\n\n【该生历史错因】' + (causes.length ? '\n' + causes.slice(0, 8).join('\n') : '（暂无）');

        const history = Array.isArray(p.history) ? p.history.slice(-12) : [];
        const r = await gradeLlm.coachChat(
          history.concat([{ role: 'user', content: message }]), ctx, { maxTokens: 3500 });
        if (!r.ok) { sendJson(502, { ok: false, error: r.error }); return; }
        sendJson(200, {
          ok: true, text: r.text,
          topics: topics.map(t => ({ ...t, prefix: topicPrefix(t.id) })),
          notes: notes.slice(0, 6),
        });
      } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    });
    return;
  }

  // ==========================================================================
  // 平板/手机连接（2026-09-20）
  //
  // 服务端 listen 0.0.0.0 后同一局域网设备可直接访问。这个端点返回本机所有
  // 局域网 IP，并以 dataURL 形式给一张二维码，前端设置页直接展示。
  // ==========================================================================
  if (url === '/api/lan-info' && req.method === 'GET') {
    (async () => {
      try {
        const ips = getLanIps();
        const urls = ips.map(ip => `http://${ip}:${PORT}`);
        // 主地址优先选首个（Windows 通常是 WiFi/以太网排在前面），前端二维码用它
        const primary = urls[0] || `http://localhost:${PORT}`;
        let qrDataUrl = null;
        try {
          const QRCode = require('qrcode');
          qrDataUrl = await QRCode.toDataURL(primary, { margin: 1, width: 220, errorCorrectionLevel: 'M' });
        } catch (e) { console.warn('[LanInfo] qrcode generation failed:', e.message); }
        sendJson(200, { ok: true, port: PORT, ips, urls, primary, qr: qrDataUrl });
      } catch (e) {
        sendJson(500, { ok: false, error: e.message });
      }
    })();
    return;
  }

  // ==========================================================================
  // 设置（2026-09-14）
  //
  // 三类配置分开存，各归其位：
  //   · API Key / 模型 → src/.secrets.json。**永不回传浏览器**，只回显「是否已设置」
  //                      和尾 4 位，避免密钥经前端泄露或被写进快照。
  //   · 每日闪卡额度    → config 表（与 FSRS 参数同源，loadConfig 直接读）
  //   · 主题 / 背景图    → config 表（纯 UI 偏好）
  // 背景图落盘 src/assets/，经 /api/notes/asset 提供——复用它的越权防护。
  // ==========================================================================
  // 允许用 SECRETS_PATH 指向副本——否则测试只能改真钥，一失误就把密钥覆盖掉。
const SECRETS_PATH = process.env.SECRETS_PATH
  ? path.resolve(__dirname, process.env.SECRETS_PATH)
  : path.join(__dirname, '.secrets.json');
  const BG_DIR = path.join(__dirname, 'assets');
  const BG_MIME_EXT = {
    'image/png': '.png', 'image/jpeg': '.jpg',
    'image/webp': '.webp', 'image/gif': '.gif',
  };

  // ---- 番茄钟轮播背景（2026-09-21）------------------------------------
  // 图片落 src/assets/pomo/，清单存 config 表的 ui_pomo_bg（JSON 数组，元素是
  // 'src/assets/pomo/xxx.jpg' 这种知识库相对路径）。取图仍走 /api/notes/asset，
  // 复用它的越权防护——服务端不新开设文件口子，前端也不给任意路径的机会。
  const POMO_DIR = path.join(BG_DIR, 'pomo');
  const POMO_REL = 'src/assets/pomo/';        // 清单里统一用这个前缀
  const POMO_MAX = 12;                        // 轮播上限：再多也没人看完，还拖慢首屏
  const POMO_EXT_OK = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif']);
  // 清单元素必须长这样：固定前缀 + 安全文件名 + 图片扩展名
  const POMO_PATH_RE = /^src\/assets\/pomo\/[A-Za-z0-9_\-.]{1,80}$/;
  const pomoExtOk = (p) => POMO_EXT_OK.has(path.extname(String(p)).toLowerCase());
  const pomoNameOk = (p) => typeof p === 'string' && !p.includes('..')
    && POMO_PATH_RE.test(p) && pomoExtOk(p);
  const llmPublic = () => {
    const c = gradeLlm.loadConfig();
    return {
      model: c.model, has_key: !!c.apiKey, base_url: c.baseUrl,
      key_hint: c.apiKey ? ('****' + c.apiKey.slice(-4)) : '',
    };
  };
  const readBgPath = () => {
    for (const ext of Object.values(BG_MIME_EXT)) {
      const p = path.join(BG_DIR, 'bg' + ext);
      if (fs.existsSync(p)) return p;
    }
    return null;
  };

  // 番茄钟设置：清单以 config 为准（数组顺序即轮播顺序）。
  // rawPomoList = 存了什么；readPomo = 存了什么 **且文件还在**。给前端的一律是后者
  // ——手动删过 assets/pomo/ 里的文件时，前端不该再收到一个必然 404 的路径。
  const clamp = (v, lo, hi, dflt) => {
    const n = Number(v);
    return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : dflt;
  };
  // config 表读值（缺失/异常都回落到默认）。GET 与三个写端点都要读一遍，
  // 所以抽成函数，不复制四份。
  const cfgGet = (db) => (k, d) => {
    try {
      const r = db.prepare('SELECT value FROM config WHERE key = ?').get(k);
      return r && r.value != null ? r.value : d;
    } catch (e) { return d; }
  };
  const rawPomoList = (get) => {
    let list = [];
    try { list = JSON.parse(get('ui_pomo_bg', '[]')); } catch (e) { list = []; }
    if (!Array.isArray(list)) list = [];
    return list.filter(pomoNameOk);
  };
  const readPomo = (get) => {
    const images = rawPomoList(get).filter(p => fs.existsSync(path.join(ROOT_DIR, p)));
    const rawShow = String(get('pomo_bg_show', 'both'));
    return {
      images,
      interval: Math.round(clamp(get('pomo_bg_interval', '20'), 5, 600, 20)),
      dim: clamp(get('pomo_bg_dim', '0.35'), 0, 0.9, 0.35),
      // full=只在全屏轮播 · both=卡片里也铺 · off=完全不使用
      show: ['full', 'both', 'off'].indexOf(rawShow) >= 0 ? rawShow : 'both',
      sound: get('pomo_sound', 'on') === 'off' ? 'off' : 'on',
    };
  };
  const writePomoList = (db, list) => {
    const st = db.prepare(
      "INSERT INTO config (key, value, updated_at) VALUES (?, ?, datetime('now','localtime')) "
      + "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"
    );
    st.run('ui_pomo_bg', JSON.stringify(list));
  };
  const delPomoFile = (rel) => {
    try { fs.unlinkSync(path.join(ROOT_DIR, rel)); return true; } catch (e) { return false; }
  };

  // ---- 番茄钟运行状态（2026-09-21 晚：从各设备 localStorage 搬到服务端）------
  // 为什么搬：番茄钟是「墙钟 + 跨设备」的东西——电脑上开一轮 45 分钟，人走到
  // 平板前接着看，表得是同一只。原先状态只存在发起设备的 localStorage 里，
  // 平板打开永远显示「待开始」。
  //
  // 存储用 config 表的两个 JSON 键（pomo_run / pomo_log），不新建表：这是纯
  // UI 会话状态，不值得动 schema.sql + migrate.js 那条链。
  //
  // ️ end_at 一律按**服务端时钟**存。两台设备的系统时间可能差几分钟，
  //    直接存发起端的 epoch，另一台算出来的剩余时间就是错的；客户端拿到
  //    server_now 后自己换算 skew，写回时再换算回去。
  const POMO_LOG_KEEP = 180;      // 日志保留天数：够看趋势就行，别无限长
  const POMO_CLAMP = { work: [1, 600], brk: [0, 120], rounds: [1, 24] };
  const readPomoRun = (get) => {
    let o = null;
    try { o = JSON.parse(get('pomo_run', 'null')); } catch (e) { o = null; }
    if (!o || typeof o !== 'object' || Array.isArray(o) || !o.plan) return null;
    return o;
  };
  const readPomoLog = (get) => {
    let o = null;
    try { o = JSON.parse(get('pomo_log', 'null')); } catch (e) { o = null; }
    if (!o || typeof o !== 'object' || Array.isArray(o)) return {};
    const out = {};
    for (const k of Object.keys(o)) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(k)) continue;
      const v = o[k] || {};
      out[k] = { pomos: Math.max(0, parseInt(v.pomos, 10) || 0),
                 min: Math.max(0, parseInt(v.min, 10) || 0) };
    }
    return out;
  };
  // 日志只留最近 N 天，读的时候顺手裁掉旧的（写的时候也裁，双保险）
  const pomoDays = (log, n) => Object.keys(log).sort().slice(-n)
    .map(d => ({ date: d, pomos: log[d].pomos, min: log[d].min }));
  const prunePomoLog = (log) => {
    const keys = Object.keys(log).sort();
    if (keys.length <= POMO_LOG_KEEP) return log;
    const out = {};
    for (const k of keys.slice(-POMO_LOG_KEEP)) out[k] = log[k];
    return out;
  };
  const putCfg = (db, kv) => {
    const st = db.prepare(
      "INSERT INTO config (key, value, updated_at) VALUES (?, ?, datetime('now','localtime')) "
      + "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"
    );
    for (const [k, v] of kv) st.run(k, v);
  };
  // 运行快照只接受这些字段，且逐个夹紧：前端传什么脏数据，都不该让另一台设备
  // 打开后看到「剩余 -8000 秒」或者一个不存在的第 99 段。
  const normPomoRun = (raw) => {
    if (!raw || typeof raw !== 'object' || !raw.plan) return null;
    const p = raw.plan || {};
    const segs = [];
    const work = Math.round(clamp(p.work, POMO_CLAMP.work[0], POMO_CLAMP.work[1], 45));
    const brk = Math.round(clamp(p.brk, POMO_CLAMP.brk[0], POMO_CLAMP.brk[1], 10));
    const rounds = Math.round(clamp(p.rounds, POMO_CLAMP.rounds[0], POMO_CLAMP.rounds[1], 1));
    for (let r = 1; r <= rounds; r++) {
      segs.push({ kind: 'work', min: work, round: r });
      if (brk > 0) segs.push({ kind: 'brk', min: brk, round: r });
    }
    const pos = Math.min(segs.length, Math.max(0, parseInt(raw.pos, 10) || 0));
    const seg = pos < segs.length ? segs[pos] : null;
    const segMs = seg ? seg.min * 60000 : 0;
    const now = Date.now();
    const endAt = Math.round(Number(raw.end_at) || 0);
    // 「跑完了」的状态不该还挂着 running：另一台设备读到会以为要补一段
    if (!seg) {
      return { plan: { id: String(p.id || 'custom').slice(0, 40), work, brk, rounds,
                       label: String(p.label || '自定义').slice(0, 60), note: String(p.note || '').slice(0, 120) },
               pos, running: false, started_once: !!raw.started_once,
               end_at: 0, remain_ms: 0, server_updated: now };
    }
    // running 却没有结束时刻、或结束时刻离谱（早于 1 分钟前 / 晚于 12 小时后）
    // → 退化成「暂停在这一段的开头」。存一个会让另一台设备瞬间跳完成的时间戳
    //   比老老实实暂停危险得多。
    const sane = endAt > now - 60000 && endAt < now + 12 * 3600 * 1000;
    const running = !!raw.running && sane;
    const remain = running ? Math.max(0, Math.min(segMs, endAt - now))
                           : Math.max(0, Math.min(segMs, Math.round(Number(raw.remain_ms) || 0) || segMs));
    return {
      plan: { id: String(p.id || 'custom').slice(0, 40), work, brk, rounds,
              label: String(p.label || '自定义').slice(0, 60), note: String(p.note || '').slice(0, 120) },
      pos, running, started_once: !!raw.started_once,
      end_at: running ? endAt : 0,
      remain_ms: running ? 0 : remain,
      server_updated: now,
    };
  };

  // GET /api/settings
  if (url === '/api/settings' && req.method === 'GET') {
    try {
      const db = new DatabaseSync(DB_PATH, { readOnly: true });
      const cfg = loadConfig(db);
      const get = (k, d) => {
        try {
          const r = db.prepare('SELECT value FROM config WHERE key = ?').get(k);
          return r && r.value != null ? r.value : d;
        } catch (e) { return d; }
      };
      const ui = {
        theme: get('ui_theme', 'dark'),
        bg_opacity: get('ui_bg_opacity', '0.35'),
        background: readBgPath() ? 'src/assets/' + path.basename(readBgPath()) : '',
        // 鼠标粒子光效：默认开（用户要的），关掉后前端整段不跑
        mouse_fx: get('ui_mouse_fx', 'on') === 'off' ? 'off' : 'on',
        // 答错/答对追问默认要不要思考：默认 on = 关掉思考（交互跟手），可改成深度
        explain_quick: get('explain_quick', 'on') === 'off' ? 'off' : 'on',
        // 用户自己写的 system prompt（空 = 用内置的极简默认）。同时把默认值回传，
        // 设置页据此做「恢复默认」按钮，不用在前端另抄一份文案。
        prompt_explain: get('prompt_explain', ''),
        prompt_note_qa: get('prompt_note_qa', ''),
        prompt_default_explain: gradeLlm.explainSystem('wrong', ''),
        prompt_default_note_qa: NOTE_QA_SYSTEM,
      };
      const pomo = readPomo(get);
      // ⚠️ 必须**在 db.close() 之前**把值取出来：get 内部是 db.prepare(...)，
      //    关库之后再调会抛错、被它自己的 catch 吞掉 → 静默返回默认值（踩过一次：
      //    设置里明明写进去了 3，读回来却永远是 10）。
      const flashExtraCount = parseInt(get('flash_extra_count', '10'), 10) || 10;
      db.close();
      sendJson(200, {
        ok: true,
        llm: llmPublic(),
        review: {
        new_per_day: cfg.new_per_day,
        reviews_per_day: cfg.reviews_per_day,
        // 今日额度刷完后「再来一组」的数量（默认 10）
        flash_extra_count: flashExtraCount,
      },
        ui,
        pomo,
      });
    } catch (e) {
      sendJson(500, { ok: false, error: e.message });
    }
    return;
  }

  // POST /api/settings/apikey {api_key?, model?, clear_key?}
  // 密钥**只写不读**：传空串表示不改，clear_key=true 才清除。
  if (url === '/api/settings/apikey' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const p = JSON.parse(body || '{}');
        let secrets = {};
        try { secrets = JSON.parse(fs.readFileSync(SECRETS_PATH, 'utf-8')) || {}; } catch (e) {}
        if (!secrets.deepseek) secrets.deepseek = {};

        const key = p.api_key == null ? '' : String(p.api_key).trim();
        if (key) {
          if (/\s/.test(key)) { sendJson(400, { ok: false, error: 'API Key 不应包含空白字符' }); return; }
          if (key.length < 8 || key.length > 200) {
            sendJson(400, { ok: false, error: 'API Key 长度不合法' });
            return;
          }
          secrets.deepseek.api_key = key;
        } else if (p.clear_key === true) {
          delete secrets.deepseek.api_key;
        }

        const model = p.model == null ? '' : String(p.model).trim();
        if (model) {
          // 模型名会被拼进上游 URL 的请求体，这里严格白名单字符，不给注入留口子
          if (!/^[A-Za-z0-9._-]{1,80}$/.test(model)) {
            sendJson(400, { ok: false, error: '模型名只能含字母、数字、点、下划线、连字符' });
            return;
          }
          secrets.deepseek.model = model;
        }
        fs.writeFileSync(SECRETS_PATH, JSON.stringify(secrets, null, 2), 'utf-8');
        console.log('[Settings] LLM 配置已更新（model=' + (model || '不变') + '）');
        sendJson(200, { ok: true, llm: llmPublic() });
      } catch (e) {
        sendJson(500, { ok: false, error: e.message });
      }
    });
    return;
  }

  // POST /api/settings {new_per_day?, reviews_per_day?, theme?, bg_opacity?}
  if (url === '/api/settings' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const p = JSON.parse(body || '{}');
        const sets = [];
        // 每日额度：各有上下界。给 0 是合法需求（今天不想被推新卡），
        // 但不能为负或大到把库刷爆。
        const RANGES = { new_per_day: [0, 500], reviews_per_day: [0, 2000], flash_extra_count: [1, 100] };
        for (const k of Object.keys(RANGES)) {
          if (p[k] == null) continue;
          const v = parseInt(p[k], 10);
          const [lo, hi] = RANGES[k];
          if (!Number.isFinite(v) || v < lo || v > hi) {
            sendJson(400, { ok: false, error: k + ' 应在 ' + lo + '~' + hi + ' 之间' });
            return;
          }
          sets.push([k, String(v)]);
        }
        if (p.theme != null) {
          const t = String(p.theme);
          if (t !== 'dark' && t !== 'light') {
            sendJson(400, { ok: false, error: "theme 只能是 dark 或 light" });
            return;
          }
          sets.push(['ui_theme', t]);
        }
        if (p.bg_opacity != null) {
          const o = Number(p.bg_opacity);
          if (!Number.isFinite(o) || o < 0 || o > 1) {
            sendJson(400, { ok: false, error: 'bg_opacity 应在 0~1 之间' });
            return;
          }
          sets.push(['ui_bg_opacity', o.toFixed(2)]);
        }
        // 番茄钟：轮播间隔 / 遮罩浓度 / 显示范围 / 提示音。都是纯 UI 偏好，
        // 与主题同层存 config 表；数字一律夹紧，前端传什么不至于把界面配坏。
        if (p.pomo_interval != null) sets.push(['pomo_bg_interval', String(Math.round(clamp(p.pomo_interval, 5, 600, 20)))]);
        if (p.pomo_dim != null) sets.push(['pomo_bg_dim', clamp(p.pomo_dim, 0, 0.9, 0.35).toFixed(2)]);
        if (p.pomo_show != null) {
          const s = String(p.pomo_show);
          if (['full', 'both', 'off'].indexOf(s) < 0) {
            sendJson(400, { ok: false, error: 'pomo_show 只能是 full / both / off' });
            return;
          }
          sets.push(['pomo_bg_show', s]);
        }
        if (p.pomo_sound != null) {
          const s = String(p.pomo_sound);
          if (s !== 'on' && s !== 'off') {
            sendJson(400, { ok: false, error: 'pomo_sound 只能是 on 或 off' });
            return;
          }
          sets.push(['pomo_sound', s]);
        }
        if (p.mouse_fx != null) {
          const s = String(p.mouse_fx);
          if (s !== 'on' && s !== 'off') {
            sendJson(400, { ok: false, error: 'mouse_fx 只能是 on 或 off' });
            return;
          }
          sets.push(['ui_mouse_fx', s]);
        }
        // 解析/追问的思考强度默认值（on = 关掉思考求快；off = 每次都深度思考）
        if (p.explain_quick != null) {
          const s = String(p.explain_quick);
          if (s !== 'on' && s !== 'off') {
            sendJson(400, { ok: false, error: 'explain_quick 只能是 on 或 off' });
            return;
          }
          sets.push(['explain_quick', s]);
        }
        // 自己写的 system prompt（空串 = 恢复内置默认）。长度放宽，但别让配置表被塞爆。
        if (p.prompt_explain != null) {
          const s = String(p.prompt_explain).slice(0, 4000);
          sets.push(['prompt_explain', s.trim()]);
        }
        if (p.prompt_note_qa != null) {
          const s = String(p.prompt_note_qa).slice(0, 4000);
          sets.push(['prompt_note_qa', s.trim()]);
        }
        if (!sets.length) { sendJson(400, { ok: false, error: '没有可更新的字段' }); return; }

        const db = new DatabaseSync(DB_PATH);
        const st = db.prepare(
          "INSERT INTO config (key, value, updated_at) VALUES (?, ?, datetime('now','localtime')) "
          + "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"
        );
        for (const [k, v] of sets) st.run(k, v);
        db.close();
        console.log('[Settings] 已更新：' + sets.map(s => s[0] + '=' + s[1]).join(', '));
        sendJson(200, { ok: true, updated: sets.map(s => s[0]) });
      } catch (e) {
        sendJson(500, { ok: false, error: e.message });
      }
    });
    return;
  }

  // POST /api/settings/background {image: 'data:image/png;base64,...'}
  // 存服务端而不是 localStorage：后者通常只有 5MB，未压缩的手机照片会直接超限。
  if (url === '/api/settings/background' && req.method === 'POST') {
    const MAX_BG = 8 * 1024 * 1024;
    let body = '', overflow = false;
    req.on('data', chunk => {
      body += chunk;
      if (body.length > MAX_BG && !overflow) {
        overflow = true;
        sendJson(413, { ok: false, error: '图片过大（请压到 8MB 以内）' });
        req.destroy();
      }
    });
    req.on('error', e => console.error('[Settings] bg request error:', e.message));
    res.on('error', e => console.error('[Settings] bg response error:', e.message));
    req.on('end', () => {
      if (overflow) return;
      try {
        const p = JSON.parse(body || '{}');
        const m = /^data:(image\/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=]+)$/.exec(String(p.image || ''));
        if (!m) {
          sendJson(400, { ok: false, error: 'image 必须是 data:image/png|jpeg|webp|gif;base64,... 形式' });
          return;
        }
        const mime = m[1];
        const buf = Buffer.from(m[2], 'base64');
        if (!buf.length) { sendJson(400, { ok: false, error: '图片内容为空' }); return; }
        if (!fs.existsSync(BG_DIR)) fs.mkdirSync(BG_DIR, { recursive: true });
        // 先清掉其它扩展名的旧图，避免 bg.png 与 bg.jpg 同时存在、读到哪张看运气
        for (const ext of Object.values(BG_MIME_EXT)) {
          const old = path.join(BG_DIR, 'bg' + ext);
          if (fs.existsSync(old)) fs.unlinkSync(old);
        }
        const name = 'bg' + BG_MIME_EXT[mime];
        fs.writeFileSync(path.join(BG_DIR, name), buf);
        console.log('[Settings] 背景图已更新：' + name + '（' + Math.round(buf.length / 1024) + 'KB）');
        sendJson(200, { ok: true, background: 'src/assets/' + name, bytes: buf.length });
      } catch (e) {
        sendJson(500, { ok: false, error: e.message });
      }
    });
    return;
  }

  // POST /api/settings/background/clear
  if (url === '/api/settings/background/clear' && req.method === 'POST') {
    try {
      let removed = 0;
      for (const ext of Object.values(BG_MIME_EXT)) {
        const p = path.join(BG_DIR, 'bg' + ext);
        if (fs.existsSync(p)) { fs.unlinkSync(p); removed += 1; }
      }
      console.log('[Settings] 背景图已移除（' + removed + ' 个文件）');
      sendJson(200, { ok: true, removed });
    } catch (e) {
      sendJson(500, { ok: false, error: e.message });
    }
    return;
  }

  // ==========================================================================
  // 番茄钟轮播背景（2026-09-21）
  //   POST /api/settings/pomo/background   {image: dataURL}   追加一张
  //   POST /api/settings/pomo/background/remove {path:'src/assets/pomo/x.jpg'} 删一张
  //   POST /api/settings/pomo/background/clear                全清（文件 + 清单）
  // 与整页背景图分开存：番茄钟要的是「一叠轮换」，整页背景是「一张铺底」，
  // 混在一个键里会导致改一边把另一边带坏。
  // ==========================================================================
  const readBody = (maxBytes, cb) => {
    let body = '', overflow = false;
    req.on('data', chunk => {
      body += chunk;
      if (body.length > maxBytes && !overflow) {
        overflow = true;
        sendJson(413, { ok: false, error: '图片过大（请压到 ' + Math.round(maxBytes / 1024 / 1024) + 'MB 以内）' });
        req.destroy();
      }
    });
    req.on('error', e => console.error('[Pomo] 请求中断：', e.message));
    res.on('error', e => console.error('[Pomo] 响应中断：', e.message));
    req.on('end', () => { if (!overflow) cb(body); });
  };
  const pomoPublic = (list) => ({ ok: true, backgrounds: list });
  // 清单增删一律基于 rawPomoList（含已消失的条目也照样能清），
  // 而不是 readPomo.images —— 后者已经把文件不存在的条目滤掉了，
  // 用它做「删一张」会导致那张永远留在 config 里。
  const pomoListFrom = (db) => rawPomoList(cfgGet(db));

  if (url === '/api/settings/pomo/background' && req.method === 'POST') {
    readBody(8 * 1024 * 1024, (body) => {
      try {
        const p = JSON.parse(body || '{}');
        const m = /^data:(image\/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=]+)$/.exec(String(p.image || ''));
        if (!m) {
          sendJson(400, { ok: false, error: 'image 必须是 data:image/png|jpeg|webp|gif;base64,... 形式' });
          return;
        }
        const buf = Buffer.from(m[2], 'base64');
        if (!buf.length) { sendJson(400, { ok: false, error: '图片内容为空' }); return; }
        if (!fs.existsSync(POMO_DIR)) fs.mkdirSync(POMO_DIR, { recursive: true });
        const ext = BG_MIME_EXT[m[1]];
        // 名字带时间戳：换图后浏览器不用强刷就能看到新的（固定文件名会被缓存住）
        const name = 'pomo_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 6) + ext;
        fs.writeFileSync(path.join(POMO_DIR, name), buf);

        const db = new DatabaseSync(DB_PATH);
        const all = pomoListFrom(db).concat([POMO_REL + name]);
        const next = all.slice(-POMO_MAX);
        // 超出上限被挤掉的最早几张，文件一并删掉，别让 assets/ 里堆孤儿图
        for (const gone of all.slice(0, all.length - next.length)) delPomoFile(gone);
        writePomoList(db, next);
        db.close();
        console.log('[Pomo] 轮播背景 +1：' + name + '（' + Math.round(buf.length / 1024) + 'KB，共 ' + next.length + ' 张）');
        sendJson(200, pomoPublic(next));
      } catch (e) {
        sendJson(500, { ok: false, error: e.message });
      }
    });
    return;
  }

  if (url === '/api/settings/pomo/background/remove' && req.method === 'POST') {
    readBody(64 * 1024, (body) => {
      try {
        const p = JSON.parse(body || '{}');
        const rel = String(p.path || '');
        if (!pomoNameOk(rel)) {
          sendJson(400, { ok: false, error: 'path 必须是 src/assets/pomo/ 下的图片' });
          return;
        }
        const db = new DatabaseSync(DB_PATH);
        const next = pomoListFrom(db).filter(x => x !== rel);
        writePomoList(db, next);
        db.close();
        const gone = delPomoFile(rel);
        console.log('[Pomo] 轮播背景 -1：' + rel + (gone ? '' : '（文件本就不存在，仅清掉清单）'));
        sendJson(200, pomoPublic(next));
      } catch (e) {
        sendJson(500, { ok: false, error: e.message });
      }
    });
    return;
  }

  if (url === '/api/settings/pomo/background/clear' && req.method === 'POST') {
    try {
      const db = new DatabaseSync(DB_PATH);
      const cur = pomoListFrom(db);
      cur.forEach(delPomoFile);
      writePomoList(db, []);
      db.close();
      console.log('[Pomo] 已清空轮播背景（' + cur.length + ' 张）');
      sendJson(200, pomoPublic([]));
    } catch (e) {
      sendJson(500, { ok: false, error: e.message });
    }
    return;
  }

  // ==========================================================================
  // 番茄钟运行状态（跨设备同步，2026-09-21 晚）
  //   GET  /api/pomodoro/state         → 当前这一轮 + 今日成绩 + 近 14 天
  //   POST /api/pomodoro/state {run}   → 覆盖写入（各端每次动作后调用）
  //   POST /api/pomodoro/credit {min}  → 记一个完成的番茄（服务端自增，两端不互相覆盖）
  // 状态放服务端而不是 localStorage，就是为了「电脑上开着、走到平板接着看」。
  // ==========================================================================
  if (url === '/api/pomodoro/state' && req.method === 'GET') {
    try {
      const db = new DatabaseSync(DB_PATH, { readOnly: true });
      const get = cfgGet(db);
      const run = readPomoRun(get);
      const log = readPomoLog(get);
      db.close();
      const today = localToday();
      sendJson(200, {
        ok: true, server_now: Date.now(), today,
        run,
        today_stat: log[today] || { pomos: 0, min: 0 },
        days: pomoDays(log, 14),
      });
    } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    return;
  }

  if (url === '/api/pomodoro/state' && req.method === 'POST') {
    readBody(64 * 1024, (body) => {
      try {
        const p = JSON.parse(body || '{}');
        const run = normPomoRun(p.run);
        if (!run) { sendJson(400, { ok: false, error: 'run.plan 缺失或非法' }); return; }
        const db = new DatabaseSync(DB_PATH);
        putCfg(db, [['pomo_run', JSON.stringify(run)]]);
        db.close();
        sendJson(200, { ok: true, server_now: Date.now(), run });
      } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    });
    return;
  }

  if (url === '/api/pomodoro/credit' && req.method === 'POST') {
    readBody(16 * 1024, (body) => {
      try {
        const p = JSON.parse(body || '{}');
        const min = Math.round(clamp(p.min, 1, 600, 45));
        // 日期由发起端给（它的“今天”才是用户眼里的今天），非法就退回服务端本地日期
        const date = isDateStr(p.date) ? p.date : localToday();
        const db = new DatabaseSync(DB_PATH);
        const log = readPomoLog(cfgGet(db));
        const cur = log[date] || { pomos: 0, min: 0 };
        log[date] = { pomos: cur.pomos + 1, min: cur.min + min };
        const pruned = prunePomoLog(log);
        putCfg(db, [['pomo_log', JSON.stringify(pruned)]]);
        db.close();
        const today = localToday();
        console.log('[Pomo] 记一个番茄：' + date + ' → ' + log[date].pomos + ' 个 / ' + log[date].min + ' 分钟');
        sendJson(200, { ok: true, server_now: Date.now(), today,
                        today_stat: pruned[today] || { pomos: 0, min: 0 },
                        days: pomoDays(pruned, 14) });
      } catch (e) { sendJson(500, { ok: false, error: e.message }); }
    });
    return;
  }

  // ==========================================================================
  // 笔记搜索 + 读笔记时的提问（2026-09-21）
  //   GET  /api/notes/search?q=&subject=&sub=&level=&chapter=&tag=&limit=
  //   POST /api/notes/ask {path, section, context, question, thread_id?}
  //   GET  /api/notes/qa?path=&limit=        —— 这篇笔记以前的提问（含上下文）
  // ==========================================================================
  if (url.startsWith('/api/notes/search') && req.method === 'GET') {
    try {
      const q = new URL('http://x' + url).searchParams;
      const t0 = Date.now();
      const wantFacets = q.get('facets') !== '0';
      const limit = Math.min(200, Math.max(1, parseInt(q.get('limit') || '40', 10) || 40));
      const filters = {
        subject: String(q.get('subject') || '').slice(0, 20),
        sub: String(q.get('sub') || '').slice(0, 30),
        level: String(q.get('level') || '').slice(0, 4),
        chapter: String(q.get('chapter') || '').slice(0, 20),
        tag: String(q.get('tag') || '').slice(0, 40),
      };
      const r0 = searchNoteDocs(q.get('q') || '', filters);
      const all = r0.results;
      const payload = {
        ok: true, q: String(q.get('q') || ''), total: all.length,
        results: all.slice(0, limit), took_ms: Date.now() - t0,
        how: r0.how, soft: r0.soft, tokens: r0.tokens,
        filters,
      };
      if (wantFacets) payload.facets = noteFacets();
      sendJson(200, payload);
    } catch (e) {
      console.error('[Notes] search error:', e.message);
      sendJson(500, { ok: false, error: e.message });
    }
    return;
  }

  if (url === '/api/notes/ask' && req.method === 'POST') {
    readBody(512 * 1024, async (body) => {
      try {
        const p = JSON.parse(body || '{}');
        const question = String(p.question || '').trim().slice(0, 2000);
        const context = String(p.context || '').trim().slice(0, 8000);
        const section = String(p.section || '').trim().slice(0, 200);
        const abs = resolveNotePath(String(p.path || ''));
        if (!question) { sendJson(400, { ok: false, error: '问题不能为空' }); return; }
        if (!abs) { sendJson(400, { ok: false, error: '笔记路径非法（仅限知识库内 .md）' }); return; }
        if (!gradeLlm.hasKey()) {
          sendJson(503, { ok: false, error: '未配置 DeepSeek API key：设置 → 模型与 API Key，或填 src/.secrets.json' });
          return;
        }
        const rel = path.relative(ROOT_DIR, abs).split(path.sep).join('/');

        // 续问：把这一轮的历史读出来（首轮那条已经带了笔记片段，后面只带问题）
        let threadId = String(p.thread_id || '').trim();
        const history = [];
        const rdb = new DatabaseSync(DB_PATH, { readOnly: true });
        // 「设置 → AI 提示词 → 读笔记提问」里他自己写的那份（空则用内置极简默认）
        const sysPrompt = String(readCfgValue(rdb, 'prompt_note_qa', '') || '').trim();
        if (threadId) {
          let rows = [];
          try {
            rows = rdb.prepare('SELECT role, content FROM note_qa WHERE thread_id = ? ORDER BY id ASC').all(threadId);
          } catch (e) { rows = []; }
          if (!rows.length) { rdb.close(); sendJson(404, { ok: false, error: '找不到这轮对话，请重新提问' }); return; }
          rows.forEach(r => history.push({ role: r.role === 'assistant' ? 'assistant' : 'user', content: r.content }));
        }
        rdb.close();

        const head = '【笔记】' + rel + (section ? ('（正在读：' + section + '）') : '')
          + (context ? ('\n【我正在读的片段】\n' + context) : '');
        const userMsg = (history.length ? '' : head + '\n\n') + '【我的问题】' + question;
        const msgs = history.concat([{ role: 'user', content: userMsg }]);

        const t0 = Date.now();
        // ⚠️ 笔记提问**不关思考**（用户要求）：他问的正是自己不会的点，要的是更细致的讲解，
        //    慢一点也值。所以不传 quick，并给足 token 预算。
        //    提示词优先用「设置 → AI 提示词 → 读笔记提问」里他自己写的那份。
        const r = await gradeLlm.coachChat(msgs, sysPrompt || NOTE_QA_SYSTEM, { maxTokens: 4000 });
        console.log('[NoteQA] ' + rel + ' 「' + question.slice(0, 18) + '…」→ ' +
                    (r.ok ? r.text.length + '字' : 'FAIL ' + r.error) + ' (' + (Date.now() - t0) + 'ms)');
        if (!r.ok) { sendJson(502, { ok: false, error: r.error }); return; }

        if (!threadId) threadId = newThreadId();
        const wdb = new DatabaseSync(DB_PATH);
        const ins = wdb.prepare(
          'INSERT INTO note_qa (thread_id, note_path, section, context, role, content) VALUES (?, ?, ?, ?, ?, ?)'
        );
        ins.run(threadId, rel, section || null, context || null, 'user', question);
        ins.run(threadId, rel, section || null, null, 'assistant', r.text);
        wdb.close();
        sendJson(200, { ok: true, thread_id: threadId, path: rel, section: section, text: r.text });
      } catch (e) {
        console.error('[NoteQA] error:', e.message);
        sendJson(500, { ok: false, error: e.message });
      }
    });
    return;
  }

  if (url.startsWith('/api/notes/qa') && req.method === 'GET') {
    try {
      const q = new URL('http://x' + url).searchParams;
      const abs = resolveNotePath(q.get('path') || '');
      if (!abs) { sendJson(400, { ok: false, error: '笔记路径非法' }); return; }
      const rel = path.relative(ROOT_DIR, abs).split(path.sep).join('/');
      const limit = Math.min(20, Math.max(1, parseInt(q.get('limit') || '8', 10) || 8));
      const db = new DatabaseSync(DB_PATH, { readOnly: true });
      let rows = [];
      try {
        rows = db.prepare('SELECT id, thread_id, section, role, content, created_at FROM note_qa '
          + 'WHERE note_path = ? ORDER BY id DESC LIMIT 200').all(rel);
      } catch (e) { rows = []; }     // 表还没迁移时按「没有历史」处理
      db.close();
      const byThread = new Map();
      rows.forEach(r => {
        if (!byThread.has(r.thread_id)) byThread.set(r.thread_id, []);
        byThread.get(r.thread_id).push(r);
      });
      const threads = Array.from(byThread.entries()).slice(0, limit).map(([tid, list]) => {
        const asc = list.slice().reverse();      // 查询是倒序，展示要正序
        const sec = (asc.filter(x => x.section)[0] || {}).section || '';
        const last = asc[asc.length - 1] || {};
        return {
          thread_id: tid, section: sec, at: last.created_at || '',
          first_question: ((asc.filter(x => x.role === 'user')[0] || {}).content || '').slice(0, 80),
          messages: asc.map(x => ({ role: x.role, content: x.content })),
        };
      });
      sendJson(200, { ok: true, path: rel, threads });
    } catch (e) {
      sendJson(500, { ok: false, error: e.message });
    }
    return;
  }

  // POST /api/targeted-cards/generate {count, prefixes[]} — 异步跑盘活出题脚本
  // GET  /api/targeted-cards/status?id=JOBID — 查询任务状态
  if (url === '/api/targeted-cards/generate' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const parsed = JSON.parse(body);
        if (targetedJob && targetedJob.status === 'running') {
          res.writeHead(409, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({ ok: false, error: '已有出题任务在跑，请稍后' }));
          return;
        }
        const count = Math.min(30, Math.max(1, parseInt(parsed.count, 10) || 8));
        const prefixes = Array.isArray(parsed.prefixes) ? parsed.prefixes
          .map(String).filter(p => /^[A-Z0-9]{2,6}-[A-Z]{2}$/.test(p)) : [];
        const args = [path.join(__dirname, 'generate_targeted_cards.py'), '--count', String(count)];
        prefixes.forEach(p => { args.push('--prefix', p); });

        const jobId = 'job-' + Date.now().toString(36);
        targetedJob = {
          id: jobId, status: 'running', prefixes, count,
          started_at: new Date().toISOString(), output: '', inserted: 0, error: null,
        };
        console.log('[TargetedCards] start', jobId, 'prefixes=', prefixes, 'count=', count);

        const proc = spawn('python', args, { cwd: __dirname });
        proc.stdout.on('data', d => {
          targetedJob.output += d.toString();
          const m = targetedJob.output.match(/新增 (\d+) 张闪卡/);
          if (m) targetedJob.inserted = parseInt(m[1], 10);
        });
        proc.stderr.on('data', d => { targetedJob.output += d.toString(); });
        proc.on('close', code => {
          targetedJob.status = code === 0 ? 'done' : 'error';
          targetedJob.exit_code = code;
          if (code !== 0) targetedJob.error = targetedJob.output.slice(-800);
          console.log('[TargetedCards]', jobId, targetedJob.status, 'inserted=', targetedJob.inserted);
        });
        proc.on('error', e => {
          targetedJob.status = 'error';
          targetedJob.error = e.message;
        });

        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: true, job_id: jobId }));
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ ok: false, error: e.message }));
      }
    });
    return;
  }

  if (url.startsWith('/api/targeted-cards/status')) {
    if (!targetedJob) {
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: true, status: 'none' }));
      return;
    }
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({
      ok: true,
      id: targetedJob.id,
      status: targetedJob.status,
      inserted: targetedJob.inserted,
      prefixes: targetedJob.prefixes,
      error: targetedJob.error,
      tail: targetedJob.output.slice(-400),
    }));
    return;
  }

  // 静态文件服务（先剥离查询串：闪卡引擎靠 ?embed=1 / ?theme=dark 切主题，不能当文件名）
  //
  // ⚠️ 2026-09-20 安全收口：服务监听 0.0.0.0，同网段任意设备都能访问。
  //   原先把 URL 直接拼成文件路径读取，等于把整个 src/ 目录公开——实测
  //   GET /.secrets.json 能拿到 DeepSeek API Key 明文，GET /question_bank.db
  //   能把整个题库拖走。现在改成固定白名单：只放行大盘与其内嵌页，
  //   文件名取自常量表，用户传什么都无法越界读文件。
  const pathname = decodeURIComponent(url.split('?')[0]).replace(/\/+$/, '') || '/';
  const STATIC_PAGES = {
    '/': 'dashboard.html',                 // 根路径 = 大盘
    '/index.html': 'dashboard.html',
    '/dashboard.html': 'dashboard.html',
    // 早间回顾已并入大盘「早」页（内容走 /api/morning-review/*），
    // 那份独立 HTML 不再对外暴露，避免同一内容出现两个入口、两套进度。
  };
  const pageFile = STATIC_PAGES[pathname];

  // ---- 公式渲染组件 KaTeX（2026-09-21 修）----
  // ️ 它加载的是相对路径 `tools/katex/dist/katex.min.{js,css}`，而白名单收紧后
  //    /tools/** 一律 404 → **公式全变源码**（闪卡题干、笔记正文都中招，用户报的就是这个）。
  //    这里只放行 katex dist 下的 js/css/mjs/字体，并做两道校验：
  //      ① 解析后的绝对路径必须仍在 tools/katex 之内（挡 ../ 穿越）
  //      ② 扩展名必须在白名单里（挡 .json/.db/.secrets 之类）
  const KATEX_ROOT = path.join(__dirname, 'tools', 'katex');
  const KATEX_MIME = {
    '.js': 'application/javascript; charset=utf-8',
    '.mjs': 'application/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.woff2': 'font/woff2',
    '.woff': 'font/woff',
    '.ttf': 'font/ttf',
  };
  if (pathname.indexOf('/tools/katex/') === 0) {
    const abs = path.resolve(KATEX_ROOT, pathname.slice('/tools/katex/'.length));
    const inside = abs === KATEX_ROOT || abs.startsWith(KATEX_ROOT + path.sep);
    const mime = KATEX_MIME[path.extname(abs).toLowerCase()];
    if (!inside || !mime) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('404');
      return;
    }
    try {
      const buf = fs.readFileSync(abs);
      res.writeHead(200, { 'Content-Type': mime, 'Cache-Control': 'public, max-age=86400' });
      res.end(buf);
    } catch (e) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('404');
    }
    return;
  }

  if (!pageFile) {
    res.writeHead(404, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end('<h1>404 Not Found</h1>');
    return;
  }
  try {
    const content = fs.readFileSync(path.join(__dirname, pageFile));
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-cache' });
    res.end(content);
  } catch (e) {
    res.writeHead(404, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end('<h1>404 Not Found</h1>');
  }
  return;
});

// 启动前跑一次幂等迁移：新增 Anki 调度所需的列/表。
// 用 try/catch 包住——迁移失败不能让服务起不来（否则大盘练习区会连不上）。
let migrationNote = '';
try {
  const rep = require('./migrate').run(DB_PATH);
  if (!rep.ok) {
    migrationNote = `⚠️ 迁移未完成：${rep.notes.join('; ')}`;
  } else if (rep.added.length) {
    migrationNote = `✅ 迁移已应用 ${rep.added.length} 项`;
  }
} catch (e) {
  migrationNote = `⚠️ 迁移异常（已忽略）：${e.message}`;
}

// ---- 端口选择：挨个候选试，别一上来就死（2026-09-21）----
// ⚠️ 8080 未必能用：Windows 有一条**TCP 保留端口段**（Hyper-V / WSL / Docker 会动态预留
//    7500~8300 那一片，用 `netsh interface ipv4 show excludedportrange protocol=tcp` 可查），
//    落在里面的端口 bind 会直接返回 **EACCES**（不是"被占用"）。
//    表现就是：服务起不来 → 启动脚本的最小化窗口一闪就关（用户描述为"闪退"），
//    而且因为没有提示，完全看不出原因。所以这里依次尝试候选端口，
//    成功就把端口写进 src/.serve_port，启动脚本读它来打开正确地址。
const PORT_CANDIDATES = (function () {
  const want = parseInt(process.env.PORT || '8080', 10) || 8080;
  const list = [want, 8080, 8088, 8888, 9090, 18080, 28080, 38080];
  return list.filter(function (x, i) { return x > 0 && x < 65536 && list.indexOf(x) === i; });
})();
const PORT_FILE = path.join(__dirname, '.serve_port');
let portIdx = 0;
let listening = false;

function announce(port) {
  const lanIps = getLanIps();
  console.log(`\n🚀 本地复习服务器已启动（PID ${process.pid}）`);
  console.log(`📖 本机访问: http://localhost:${port}`);
  if (lanIps.length) {
    console.log(`📱 局域网访问（平板/手机扫码用）:`);
    for (const ip of lanIps) console.log(`     http://${ip}:${port}`);
  }
  console.log(`🎙 火山 TTS 代理: http://localhost:${port}/api/volcano/tts`);
  console.log(`🤖 方舟 AI 代理: http://localhost:${port}/api/ark/chat`);
  console.log(`🔄 闪卡同步端点: http://localhost:${port}/api/flashcard-sync`);
  console.log(`📝 简答批改端点: http://localhost:${port}/api/grade`);
  console.log(`📖 笔记盘活端点: /api/notes/preview · /api/notes/asset · /api/notes/touch · /api/targeted-cards/generate`);
  console.log(`⏹ 按 Ctrl+C 停止\n`);
  if (migrationNote) console.log(migrationNote + '\n');
  // 让启动脚本（.bat）知道实际端口，否则它会去开 8080 的白页
  try {
    fs.writeFileSync(PORT_FILE, String(port));
    console.log(`（端口已写入 ${PORT_FILE}）\n`);
  } catch (err) { console.warn('[Port] 写端口文件失败（不影响使用）:', err.message); }
}

// ⚠️ 监听器只注册**一次**：早先的写法是每次尝试都 `server.once('listening', ...)`，
//    前面失败的监听器不会自己消失 —— 最终 listen 成功时先跑的是第一次（8080）那个，
//    于是**公告的端口是错的**（实测：真的在 8888，却打印 8080 并写进端口文件）。
//    现在端口一律取 `server.address().port`，就是内核实际绑上的那个。
server.on('listening', () => {
  listening = true;
  announce(server.address().port);
});
server.on('error', (e) => {
  if (listening) {
    // 已经在服务了，运行中出错只记录，不要让服务消失
    console.error('[Fatal] 服务运行中出错：', e && e.message ? e.message : e);
    return;
  }
  {
    const port = PORT_CANDIDATES[portIdx];
    const why = e.code === 'EACCES'
      ? '被 Windows 的保留端口段占了（Hyper-V/WSL/Docker 会占 7500~8300 那一片）'
      : (e.code === 'EADDRINUSE' ? '已被占用（多半是上一次的服务还在跑）' : e.message);
    console.error(`\n⚠️ 端口 ${port} 起不来：${why}`);
    portIdx += 1;
    if (portIdx < PORT_CANDIDATES.length) {
      console.error(`   → 换 ${PORT_CANDIDATES[portIdx]} 再试…`);
      setImmediate(startListening);
      return;
    }
    console.error(`\n❌ 候选端口全试过了，服务没能启动：${PORT_CANDIDATES.join(', ')}`);
    console.error('   查哪些端口被系统保留：netsh interface ipv4 show excludedportrange protocol=tcp');
    console.error('   换个端口启动：set PORT=28888 && node serve.js');
    console.error('   本窗口 30 秒后自动关闭（留着方便你复制上面的信息）。\n');
    setTimeout(() => process.exit(1), 30000);
  }
});

function startListening() {
  server.listen(PORT_CANDIDATES[portIdx], '0.0.0.0');
}
startListening();

// ---- 本地工具，别因为一个请求出错就整个服务消失 ----
// 这些错误如果没人接，Node 会直接杀进程；服务一没，大盘的闪卡/批改全部报"无法连接本地服务"，
// 而且窗口是最小化的，根本看不到发生了什么。这里一律记日志、继续服务。
process.on('uncaughtException', (e) => {
  console.error('[Fatal] 未捕获异常（服务继续运行）:', e && e.stack ? e.stack : e);
});
process.on('unhandledRejection', (e) => {
  console.error('[Fatal] 未处理的 Promise 拒绝（服务继续运行）:', e && e.stack ? e.stack : e);
});

/*
 * review_store.js — 错题复盘 / 薄弱点学习 的文件存储层
 *
 * 为什么用文件而不是 SQLite：
 *   1. 用户要求「按科目建文件夹，平时也会往里放资料」——数据必须人可读、可手改；
 *   2. 整个知识库是 Obsidian 库，文件能直接被笔记软件与 agent 检索；
 *   3. 别的 agent 的定时任务（每周闪卡补充、每周错题复盘）靠 grep 这些文件取数，
 *      这是把它们纳入同一工作流最稳的方式——不需要谁去改谁的任务；
 *   4. 量级很小（每周几个会话），没有性能诉求。
 * 闪卡本身仍在 question_bank.db，这里只存「复盘会话 + 错因」。
 *
 * 目录契约（ROOT = 考研知识库根目录）：
 *   Review/_patterns.json                     派生：错因画像（build_review_patterns.py 生成）
 *   Review/<科目>/index.json                  该科会话索引（轻量，默认只读这层）
 *   Review/<科目>/sessions/<sid>/meta.json    会话元数据
 *   Review/<科目>/sessions/<sid>/uploads/     上传原件（图片/PDF）
 *   Review/<科目>/sessions/<sid>/errors.json  结构化错题（出卡依据的热数据）
 *   Review/<科目>/sessions/<sid>/transcript.md 对话全文（人读 + agent grep）
 *
 * ⚠️ 防死数据/防 token 浪费：调用方默认只读 index 与 _patterns（L1）；
 *    单会话的 errors/transcript（L2/L3）只在明确要深挖那一条时才读。
 */

const fs = require('fs');
const path = require('path');

// 2026-09-25 代码/笔记分离：复盘数据仍在笔记库（Review/），代码在项目根下。
const ROOT = require('./paths').NOTES_ROOT;   // 路径单一事实源（env NOTES_ROOT > paths.json > 默认）
const REVIEW_DIR = path.join(ROOT, 'Review');
const SUBJECTS = ['408', '数学一', '政治', '英语一'];

// 会话 id：日期 + 短随机，肉眼可排顺序
function newSessionId(subject) {
  const d = new Date();
  const ymd = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
    + '-' + String(d.getDate()).padStart(2, '0');
  const rand = Math.random().toString(36).slice(2, 6);
  return 'r' + ymd + '-' + subject.replace(/[^0-9A-Za-z\u4e00-\u9fa5]/g, '') + '-' + rand;
}

function ensureSubjectDir(subject) {
  const dir = path.join(REVIEW_DIR, subject);
  fs.mkdirSync(path.join(dir, 'sessions'), { recursive: true });
  return dir;
}

// 原子写：先写 .tmp 再 rename，避免半截文件被 agent 读到
function writeJson(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = file + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2), 'utf-8');
  fs.renameSync(tmp, file);
}
function readJson(file, fallback) {
  try { return JSON.parse(fs.readFileSync(file, 'utf-8')); } catch (e) { return fallback; }
}

function subjectDir(subject) { return path.join(REVIEW_DIR, subject); }
function sessionDir(subject, sid) { return path.join(subjectDir(subject), 'sessions', sid); }

function loadIndex(subject) {
  return readJson(path.join(subjectDir(subject), 'index.json'),
    { subject, updated_at: null, sessions: [] });
}
function saveIndex(subject, idx) {
  idx.updated_at = new Date().toISOString();
  writeJson(path.join(subjectDir(subject), 'index.json'), idx);
}

/** 建会话：落 meta.json + 占位 transcript + 更新索引 */
function createSession({ subject, title, sourceKind, note }) {
  if (!SUBJECTS.includes(subject)) throw new Error('科目必须是 ' + SUBJECTS.join(' / '));
  ensureSubjectDir(subject);
  const sid = newSessionId(subject);
  const dir = sessionDir(subject, sid);
  fs.mkdirSync(path.join(dir, 'uploads'), { recursive: true });
  const now = new Date().toISOString();
  const meta = {
    id: sid, subject,
    date: now.slice(0, 10),
    title: String(title || now.slice(0, 10) + ' 复盘').slice(0, 120),
    source_kind: sourceKind || 'upload',        // upload | verbal | mixed
    note: String(note || '').slice(0, 2000),
    status: 'open',                              // open | archived
    files: [],                                   // uploads 里的相对路径清单
    error_count: 0, top_causes: [],
    created_at: now, updated_at: now,
  };
  writeJson(path.join(dir, 'meta.json'), meta);
  writeJson(path.join(dir, 'errors.json'), { id: sid, errors: [] });
  fs.writeFileSync(path.join(dir, 'transcript.md'),
    '# ' + meta.title + '\n\n- 科目：' + subject + '\n- 日期：' + meta.date + '\n- 来源：' + meta.source_kind + '\n',
    'utf-8');
  const idx = loadIndex(subject);
  idx.sessions = idx.sessions.filter(s => s.id !== sid)
    .concat([{ id: sid, date: meta.date, title: meta.title, status: 'open',
      error_count: 0, top_causes: [] }])
    .sort((a, b) => (a.date < b.date ? 1 : -1));
  saveIndex(subject, idx);
  return meta;
}

/**
 * transcript.md 是唯一事实源（用户能直接读、别的 agent 能 grep），
 * 但前端要按气泡渲染，所以这里把它解析回结构化 turns——不另存一份 JSON，
 * 免得两处不同步。格式由 appendTurn 自己写死，解析按同一约定。
 */
function parseTranscript(md) {
  const turns = [];
  const re = /\*\*(我|AI)\*\*：\n\n([\s\S]*?)(?=\n\n\*\*(?:我|AI)\*\*：|\s*$)/g;
  let m;
  while ((m = re.exec(String(md || ''))) !== null) {
    const body = m[2].trim();
    if (body) turns.push({ role: m[1] === '我' ? 'user' : 'assistant', content: body });
  }
  return turns;
}

function loadSession(subject, sid) {
  const dir = sessionDir(subject, sid);
  const meta = readJson(path.join(dir, 'meta.json'), null);
  if (!meta) return null;
  const transcript = (() => { try { return fs.readFileSync(path.join(dir, 'transcript.md'), 'utf-8'); } catch (e) { return ''; } })();
  return {
    meta,
    errors: readJson(path.join(dir, 'errors.json'), { id: sid, errors: [] }).errors || [],
    transcript,
    turns: parseTranscript(transcript),
  };
}

/** 保存错题并回写索引摘要（保持 index 与 errors 一致） */
function saveErrors(subject, sid, errors) {
  const dir = sessionDir(subject, sid);
  const list = (errors || []).map(e => ({
    id: String(e.id || ('e' + Math.random().toString(36).slice(2, 8))),
    topic_hint: String(e.topic_hint || '').slice(0, 200),
    topic_id: String(e.topic_id || '').slice(0, 60),
    title: String(e.title || '').slice(0, 300),
    what_wrong: String(e.what_wrong || '').slice(0, 2000),
    cause: String(e.cause || '未归类').slice(0, 300),          // 错因：这是跨会话归并的键
    cause_kind: String(e.cause_kind || 'concept').slice(0, 40),// concept|calc|reading|memory|method
    evidence: String(e.evidence || '').slice(0, 1500),
    severity: Math.max(1, Math.min(5, Number(e.severity) || 3)),
    card_ids: Array.isArray(e.card_ids) ? e.card_ids.slice(0, 20).map(String) : [],
    resolved: !!e.resolved,                                     // 已出卡并答对/已确认掌握
  }));
  writeJson(path.join(dir, 'errors.json'), { id: sid, updated_at: new Date().toISOString(), errors: list });

  const meta = readJson(path.join(dir, 'meta.json'), null);
  if (meta) {
    const causes = {};
    for (const e of list) causes[e.cause] = (causes[e.cause] || 0) + 1;
    meta.error_count = list.length;
    meta.top_causes = Object.entries(causes).sort((a, b) => b[1] - a[1]).slice(0, 5).map(x => x[0]);
    meta.updated_at = new Date().toISOString();
    writeJson(path.join(dir, 'meta.json'), meta);
    const idx = loadIndex(subject);
    const row = idx.sessions.find(s => s.id === sid);
    if (row) { row.error_count = meta.error_count; row.top_causes = meta.top_causes; row.status = meta.status; }
    saveIndex(subject, idx);
  }
  return list;
}

/** 追加一轮对话：同时写 transcript.md（人读）与 meta 时间戳 */
function appendTurn(subject, sid, role, content) {
  const dir = sessionDir(subject, sid);
  const file = path.join(dir, 'transcript.md');
  let text = '';
  try { text = fs.readFileSync(file, 'utf-8'); } catch (e) { text = '# 复盘对话\n'; }
  const who = role === 'user' ? '我' : 'AI';
  text = text.replace(/\s+$/, '') + '\n\n**' + who + '**：\n\n' + String(content || '').trim() + '\n';
  fs.writeFileSync(file, text, 'utf-8');
  const meta = readJson(path.join(dir, 'meta.json'), null);
  if (meta) { meta.updated_at = new Date().toISOString(); writeJson(path.join(dir, 'meta.json'), meta); }
}

/** 登记上传文件（相对 ROOT 的路径，便于 resolveAssetPath 复用） */
function attachFile(subject, sid, relPath, kind) {
  const dir = sessionDir(subject, sid);
  const meta = readJson(path.join(dir, 'meta.json'), null);
  if (!meta) throw new Error('会话不存在');
  meta.files = (meta.files || []).filter(f => f.path !== relPath)
    .concat([{ path: relPath, kind: kind || 'image', at: new Date().toISOString() }]);
  meta.updated_at = new Date().toISOString();
  writeJson(path.join(dir, 'meta.json'), meta);
  return meta;
}

/** 取消登记上传文件（划掉某页 / 移除原件徽标）。只改 meta.files，不碰磁盘；
 *  删本地文件由调用方（serve.js 的 detach-file 端点）在越权校验后自行 unlink。 */
function detachFile(subject, sid, relPath) {
  const dir = sessionDir(subject, sid);
  const meta = readJson(path.join(dir, 'meta.json'), null);
  if (!meta) throw new Error('会话不存在');
  meta.files = (meta.files || []).filter(f => f.path !== relPath);
  meta.updated_at = new Date().toISOString();
  writeJson(path.join(dir, 'meta.json'), meta);
  return meta;
}

function listSessions(subject) {
  if (subject && subject !== 'all') {
    if (!SUBJECTS.includes(subject)) return [];
    return loadIndex(subject).sessions || [];
  }
  const all = [];
  for (const s of SUBJECTS) for (const row of (loadIndex(s).sessions || [])) all.push({ ...row, subject: s });
  return all.sort((a, b) => (a.date < b.date ? 1 : -1));
}

/** 遍历全部错题（供画像脚本与 API 聚合用）。只读 errors.json，不读 transcript。 */
function iterErrors() {
  const out = [];
  for (const subject of SUBJECTS) {
    const sdir = path.join(subjectDir(subject), 'sessions');
    let entries = [];
    try { entries = fs.readdirSync(sdir); } catch (e) { continue; }
    for (const sid of entries) {
      const bag = readJson(path.join(sdir, sid, 'errors.json'), null);
      const meta = readJson(path.join(sdir, sid, 'meta.json'), null);
      if (!bag || !meta) continue;
      for (const e of (bag.errors || [])) out.push({ subject, sid, date: meta.date, status: meta.status, ...e });
    }
  }
  return out;
}

function readPatterns() {
  return readJson(path.join(REVIEW_DIR, '_patterns.json'), null);
}

module.exports = {
  REVIEW_DIR, ROOT, SUBJECTS,
  createSession, loadSession, saveErrors, appendTurn, attachFile, detachFile,
  listSessions, loadIndex, iterErrors, readPatterns, sessionDir, subjectDir,
  writeJson, readJson,
};

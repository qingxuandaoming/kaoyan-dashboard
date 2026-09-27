#!/usr/bin/env node
/*
 * ai_attribute_notes.js —— 「学习材料 → 知识单元」的 AI 归因引擎（2026-09-27）
 *
 * 要解决的问题（用户原话：「似乎对我笔记的监控不是很全面，有很多它都没有扫出来」）：
 *   覆盖判定原先靠三套人肉规则（note_prefix 别名表 / gen_english_index 的 glob RULES /
 *   generate_dashboard 的「前缀+章节号」粗匹配）。它们的共同缺陷是
 *   **必须有人先把它写进表里才会生效**，于是用户新写的一类笔记会静默掉出监控，
 *   而且没有任何一处会报错 —— 页面上只表现为「这一格 0%」，
 *   分不清是「真没学」还是「管道没看见」。实测代价：政治 78 条条目里 42 条
 *   因 chapter 无编号而完全不计入；英语磁盘 688 个 md 只有 35 个进了索引。
 *
 * 分工（这条是本文件存在的理由，别搞反）：
 *   模型负责**判断**：这段材料是什么类型、属于图谱里哪些考点、有多确定、凭什么。
 *   代码负责**校验与记账**：考点 id 必须真实存在（模型编的 id 一律丢弃并计数）、
 *     置信度阈值决定计不计覆盖、按内容指纹缓存避免重复花钱、全程可 --dry-run。
 *
 * 泛化：本引擎不认「考研」这个概念，只认 attribution_spec.json 里的 domain ——
 *   一个 domain = 一部知识图谱 + 一个语料目录。换任何学习领域都不用改这个文件。
 *
 * 用法：
 *   node tools/ai_attribute_notes.js --domain kaoyan-politics --dry-run
 *   node tools/ai_attribute_notes.js --domain kaoyan-politics --limit 40
 *   node tools/ai_attribute_notes.js --all --report-only
 * 参数：--domain <id>  --all  --limit N  --force（忽略缓存重问）
 *       --dry-run（只列计划与 token 估算，不打 API）  --report-only（只出对照报告）
 *       --db <path>（默认 src/question_bank.db；测试请指副本）
 */
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { DatabaseSync } = require('node:sqlite');

const P = require('../paths');
const providers = require('../ai_providers');
const llm = require('../grade_llm');

const SPEC_PATH = path.join(P.SRC_DIR, 'attribution_spec.json');
const GRAPH_DIR = path.join(P.SRC_DIR, 'knowledge_graph');

// ---------------------------------------------------------------------------
// 规格与图谱
// ---------------------------------------------------------------------------

function loadSpec() {
  const s = JSON.parse(fs.readFileSync(SPEC_PATH, 'utf-8'));
  s.__hash = sha1(stableStringify(s));
  return s;
}

function sha1(s) { return crypto.createHash('sha1').update(String(s), 'utf8').digest('hex'); }

function stableStringify(v) {
  if (Array.isArray(v)) return '[' + v.map(stableStringify).join(',') + ']';
  if (v && typeof v === 'object') {
    return '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + stableStringify(v[k])).join(',') + '}';
  }
  return JSON.stringify(v);
}

/** 读图谱，摊平成归因候选表。只取图谱粒度的考点（三段式 id），
 *  子考点（四段）不进候选：让模型在 200 个细粒度条目里选，错得比它对得还多。 */
function loadTaxonomy(domain) {
  const gp = path.join(GRAPH_DIR, domain.graph_file);
  if (!fs.existsSync(gp)) throw new Error(`图谱不存在: ${gp}`);
  const g = JSON.parse(fs.readFileSync(gp, 'utf-8'));
  const topics = [];
  for (const [subKey, sub] of Object.entries(g.subs || {})) {
    for (const t of (sub.topics || [])) {
      const id = String(t.id || '');
      if (!id) continue;
      if (id.split('-').length !== 3) continue;      // 只要图谱粒度
      topics.push({
        id, name: String(t.name || ''), sub: sub.name || subKey,
        chapter: (t.note_chapter !== undefined ? t.note_chapter : t.chapter),
      });
    }
  }
  return { graph: g, topics, hash: sha1(stableStringify(topics)) };
}

// ---------------------------------------------------------------------------
// 语料扫描：文件 → 材料单元
// ---------------------------------------------------------------------------

function walkCorpus(rootDir, spec) {
  const out = [];
  const ignDir = new Set(spec.corpus.ignore_dirs || []);
  const ignPath = (spec.corpus.ignore_paths || []).map(p => p.replace(/[\\/]/g, path.sep));
  (function rec(dir) {
    let items;
    try { items = fs.readdirSync(dir, { withFileTypes: true }); } catch (e) { return; }
    for (const it of items) {
      const full = path.join(dir, it.name);
      if (it.isDirectory()) {
        if (ignDir.has(it.name)) continue;
        if (ignPath.some(p => full.startsWith(p))) continue;
        rec(full);
      } else if (it.name.toLowerCase().endsWith('.md') && !it.name.startsWith('_')) {
        out.push(full);
      }
    }
  })(rootDir);
  return out.sort();
}

function stripNoise(text) {
  return text
    .replace(/<!--[\s\S]*?-->/g, '')          // note-meta 块：那是登记信息，不是知识正文
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')      // 图片引用
    .replace(/\n{3,}/g, '\n\n');
}

/** 按标题切成材料单元。一个单元 = 一个标题 + 它下面到下一个同级/更高级标题之前的正文。 */
function splitUnits(relPath, raw, spec) {
  const cfg = spec.unit;
  const levels = new Set(cfg.split_heading_levels || [2, 3]);
  const lines = stripNoise(raw).split('\n');
  const units = [];
  let cur = { heading: '', h1: '', body: [] };
  const flush = () => {
    const body = cur.body.join('\n').trim();
    if (body.length >= cfg.min_chars || (cur.heading && body.length > 0)) {
      const text = (cur.h1 ? cur.h1 + '\n' : '') + (cur.heading ? cur.heading + '\n' : '') + body;
      if (text.trim().length >= cfg.min_chars) {
        units.push({
          rel: relPath,
          heading: [cur.h1, cur.heading].filter(Boolean).join(' › ') || '(无标题)',
          text: text.slice(0, cfg.max_chars),
          chars: text.length,
        });
      }
    }
    cur = { heading: cur.heading, h1: cur.h1, body: [] };
  };
  for (const ln of lines) {
    const m = /^(#{1,6})\s+(.*)$/.exec(ln);
    if (m) {
      const lv = m[1].length;
      const title = m[2].trim();
      if (lv === 1) { flush(); cur.h1 = title; cur.heading = ''; continue; }
      if (levels.has(lv)) { flush(); cur.heading = title; continue; }
    }
    cur.body.push(ln);
  }
  flush();
  return units;
}

function unitKey(domain, u, spec, tax, cfg) {
  return sha1([
    domain.id, u.rel, u.heading,
    sha1(u.text), tax.hash, spec.prompt_version, cfg.model,
  ].join('\u0001'));
}

// ---------------------------------------------------------------------------
// 存储
// ---------------------------------------------------------------------------

function ensureSchema(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS note_attributions (
      unit_key     TEXT PRIMARY KEY,
      domain       TEXT NOT NULL,
      rel_path     TEXT NOT NULL,
      heading      TEXT,
      chars        INTEGER,
      kind         TEXT,
      topic_ids    TEXT,
      confidence   REAL,
      evidence     TEXT,
      method       TEXT,
      model        TEXT,
      prompt_ver   TEXT,
      taxonomy_hash TEXT,
      updated_at   TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_note_attr_domain ON note_attributions(domain, rel_path);
    CREATE INDEX IF NOT EXISTS idx_note_attr_topic  ON note_attributions(domain, confidence);

    -- 完整性台账。没有这张表，「跑到一半」和「跑完了」在数据上长得一模一样，
    -- 而消费方会把半截结果当成完整归因用 —— 实测这样会把英语覆盖率从 96.9% 误砸到 87.6%。
    CREATE TABLE IF NOT EXISTS note_attr_runs (
      domain         TEXT PRIMARY KEY,
      units_total    INTEGER,
      units_pending  INTEGER,
      taxonomy_hash  TEXT,
      prompt_ver     TEXT,
      model          TEXT,
      files_total    INTEGER,
      finished_at    TEXT
    );
  `);
}

// ---------------------------------------------------------------------------
// 提示词
// ---------------------------------------------------------------------------

function buildSystem(domain, tax, spec) {
  const kinds = spec.kinds.map(k => `- ${k.id}：${k.title}。${k.desc}`).join('\n');
  const catalog = tax.topics.map(t => `${t.id}\t${t.sub}\t${t.name}`).join('\n');
  return [
    '你在为一个学习知识库做「材料 → 知识单元」的归因判定。任务不是总结、不是改写，是分类与归属。',
    '',
    `学习领域：${domain.title}`,
    '',
    '可选的材料类型（kind，只能取这些 id 之一）：',
    kinds,
    '',
    `该领域的知识单元清单（格式：ID<TAB>所属部分<TAB>名称）。topics 字段**只能**填这里出现过的 ID：`,
    catalog,
    '',
    '判定规则：',
    '1. kind 看的是「这段材料本身是什么」。批量生成的词条罗列、原文、书单、目录都不是知识点笔记 —— 它们存在，但不证明学习者掌握了对应知识。',
    '2. topics 填这段材料真正讲到的知识单元，1~3 个；宁缺毋滥，牵强的不要填。清单里没有合适的就填空数组。',
    '3. confidence 是「归属判断有多确定」，0~1：直接讲这个知识点给 0.8 以上；顺带提及、或只在多个单元之间摇摆给 0.4~0.6；说不清给 0.2 以下。',
    '4. evidence 用不超过 25 字指出凭据（材料里的原话片段或特征），不许写套话。',
    '5. 只输出一个 JSON 对象，形如 {"items":[...]}，不要解释、不要代码围栏。每个输入单元一条，i 用给定的单元序号：',
    '{"items":[{"i":0,"kind":"knowledge_note","topics":["XX-YY-01"],"confidence":0.8,"evidence":"..."}]}',
    '⚠️ 顶层必须是对象、判定必须放在 items 数组里 —— 顶层直接给数组会解析失败。',
  ].join('\n');
}

function buildUser(units) {
  return units.map((u, i) => [
    `【单元 ${i}】文件：${u.rel}`,
    `小节：${u.heading}`,
    '正文：',
    u.text,
  ].join('\n')).join('\n\n========\n\n')
    + '\n\n按 system 要求的 {"items":[...]} 输出这 ' + units.length + ' 个单元的判定。';
}

// ---------------------------------------------------------------------------
// 兜底解析与「不守约」留证
// ---------------------------------------------------------------------------

/** 从文本里抠出第一个平衡的 JSON **数组**。grade_llm.extractJson 只认对象，
 *  顶层数组会被它抠成数组里的第一个对象（静默丢掉其余全部判定），所以这里自备一份。 */
function extractJsonArray(text) {
  const raw = String(text == null ? '' : text);
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const body = fenced ? fenced[1] : raw;
  const start = body.indexOf('[');
  if (start < 0) return null;
  let depth = 0, inStr = false, esc = false;
  for (let i = start; i < body.length; i++) {
    const ch = body[i];
    if (inStr) {
      if (esc) esc = false;
      else if (ch === '\\') esc = true;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') inStr = true;
    else if (ch === '[') depth++;
    else if (ch === ']') {
      depth--;
      if (depth === 0) {
        try {
          const v = JSON.parse(body.slice(start, i + 1));
          return Array.isArray(v) ? v : null;
        } catch (e) { return null; }
      }
    }
  }
  return null;
}

/** 解析不出来的原文必须留下证据，否则这类失败只会表现为「AI 什么都没判出来」。 */
function saveBadResponse(domain, offset, text) {
  try {
    const dir = path.join(P.SRC_DIR, 'logs');
    fs.mkdirSync(dir, { recursive: true });
    const f = path.join(dir, `ai_attr_bad_${domain.id}_${Date.now()}_${offset}.txt`);
    fs.writeFileSync(f, String(text || ''), 'utf-8');
    return path.relative(P.CODE_ROOT, f);
  } catch (e) { return '(存不下来: ' + e.message + ')'; }
}

// ---------------------------------------------------------------------------
// 校验：模型输出不可信，逐条过闸
// ---------------------------------------------------------------------------

function normalizeVerdict(v, units, tax, spec) {
  const valid = new Set(tax.topics.map(t => t.id));
  const kinds = new Set(spec.kinds.map(k => k.id));
  const idx = Number(v.i);
  if (!Number.isInteger(idx) || idx < 0 || idx >= units.length) return null;
  const kind = kinds.has(v.kind) ? v.kind : 'other';
  const raw = Array.isArray(v.topics) ? v.topics : [];
  const topics = [];
  let invented = 0;
  for (const t of raw) {
    const id = String(t).trim();
    if (valid.has(id)) { if (!topics.includes(id)) topics.push(id); }
    else invented++;
  }
  let conf = Number(v.confidence);
  if (!Number.isFinite(conf)) conf = 0;
  conf = Math.max(0, Math.min(1, conf));
  return { unit_index: idx, kind, topics, confidence: conf,
           evidence: String(v.evidence || '').slice(0, 120), invented };
}

// ---------------------------------------------------------------------------
// 对照：现有规则判为「有笔记」的考点（只为量化 AI 补了多少，不是真值来源）
// ---------------------------------------------------------------------------

function ruleCoveredTopics(domain, tax) {
  const ip = path.join(P.NOTES_ROOT, domain.registry_index);
  if (!fs.existsSync(ip)) return new Set();
  let idx;
  try { idx = JSON.parse(fs.readFileSync(ip, 'utf-8')); } catch (e) { return new Set(); }
  const chapters = new Map();     // 前缀 -> Set(章号)
  for (const sub of Object.values(idx.subjects || {})) {
    for (const e of (sub.entries || [])) {
      const id = String(e.id || '');
      const parts = id.split('-');
      if (!parts[0]) continue;
      const alias = { MY: 'POL-MY', MZT: 'POL-MZ', SG: 'POL-SG', XSX: 'POL-XX', '思修': 'POL-SX' };
      let pfx = alias[parts[0]] || (parts.length >= 2 ? parts.slice(0, 2).join('-') : parts[0]);
      if (parts[0] === 'ZT' || parts[0] === 'QT') continue;   // 跨科目专题
      if (pfx === 'ENG-TRN') pfx = 'ENG-TRAN';
      const m = /(\d+)/.exec(String(e.chapter || ''));
      if (!m) continue;
      if (!chapters.has(pfx)) chapters.set(pfx, new Set());
      chapters.get(pfx).add(parseInt(m[1], 10));
    }
  }
  const covered = new Set();
  for (const t of tax.topics) {
    const pfx = t.id.split('-').slice(0, 2).join('-');
    const ch = Number(t.chapter);
    if (Number.isFinite(ch) && chapters.has(pfx) && chapters.get(pfx).has(ch)) covered.add(t.id);
  }
  return covered;
}

// ---------------------------------------------------------------------------
// 主流程
// ---------------------------------------------------------------------------

async function main() {
  const argv = process.argv.slice(2);
  const arg = (name, dflt) => {
    const i = argv.indexOf('--' + name);
    return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : dflt;
  };
  const flag = (name) => argv.includes('--' + name);

  const spec = loadSpec();
  const cfg = providers.activeConfig();
  const dbPath = arg('db', path.join(P.SRC_DIR, 'question_bank.db'));
  const db = new DatabaseSync(dbPath);
  ensureSchema(db);

  let domains = spec.domains;
  if (!flag('all')) {
    const want = arg('domain', null);
    if (!want) {
      console.log('需要一个 --domain <id> 或 --all。可用：' + spec.domains.map(d => d.id).join(', '));
      db.close(); return;
    }
    domains = spec.domains.filter(d => d.id === want);
    if (!domains.length) { console.log('未知 domain: ' + want); db.close(); return; }
  }

  const limit = parseInt(arg('limit', '0'), 10) || 0;
  const dry = flag('dry-run');
  const force = flag('force');
  const reportOnly = flag('report-only');

  const gotRow = db.prepare('SELECT unit_key FROM note_attributions WHERE unit_key = ?');
  const putRow = db.prepare(`INSERT OR REPLACE INTO note_attributions
    (unit_key,domain,rel_path,heading,chars,kind,topic_ids,confidence,evidence,method,model,prompt_ver,taxonomy_hash)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`);

  let grand = { files: 0, units: 0, cached: 0, asked: 0, failed: 0, invented: 0 };

  for (const domain of domains) {
    const tax = loadTaxonomy(domain);
    const rootDir = path.join(P.NOTES_ROOT, domain.corpus_root);
    if (!fs.existsSync(rootDir)) { console.log(`[${domain.id}] 语料目录不存在，跳过 ${rootDir}`); continue; }

    const files = walkCorpus(rootDir, spec);
    let units = [];
    for (const f of files) {
      let raw;
      try { raw = fs.readFileSync(f, 'utf-8'); } catch (e) { continue; }
      const rel = path.relative(P.NOTES_ROOT, f).replace(/\\/g, '/');
      for (const u of splitUnits(rel, raw, spec)) units.push(u);
    }
    grand.files += files.length;
    grand.units += units.length;

    // 回收：正文被删、改名、或落进新加的 ignore 之后，旧判定不许继续冒充覆盖。
    // 判据用「文件 + 小节」而不是整条 unit_key —— unit_key 含内容指纹，改一个字就变了，
    // 拿它做差集会把所有改过的段落都误删掉。
    const liveKeys = new Set(units.map(u => u.rel + '\u0001' + u.heading));
    let pruned = 0;
    for (const row of db.prepare('SELECT rel_path, heading FROM note_attributions WHERE domain = ?').all(domain.id)) {
      if (!liveKeys.has(row.rel_path + '\u0001' + (row.heading || ''))) {
        db.prepare('DELETE FROM note_attributions WHERE domain = ? AND rel_path = ? AND heading IS ?')
          .run(domain.id, row.rel_path, row.heading);
        pruned++;
      }
    }
    if (pruned) console.log(`  回收失效判定 ${pruned} 段（文件已删/改名/被 ignore 挡住）`);

    // 缓存分流
    const todo = [];
    for (const u of units) {
      u.key = unitKey(domain, u, spec, tax, cfg);
      if (!force && gotRow.get(u.key)) { grand.cached++; u.cached = true; }
      else todo.push(u);
    }
    const queue = limit > 0 ? todo.slice(0, limit) : todo;

    console.log(`\n===== ${domain.id} 「${domain.title}」 =====`);
    console.log(`  图谱考点 ${tax.topics.length} 个 | md ${files.length} 个 | 材料单元 ${units.length} 段`);
    console.log(`  已缓存 ${grand.cached} 段 | 本次待判定 ${queue.length} 段（未处理队列剩 ${Math.max(0, todo.length - queue.length)}）`);

    if (dry) {
      const estIn = queue.reduce((a, u) => a + u.text.length, 0) + queue.length * 40;
      console.log(`  [dry-run] 不打 API。估算输入 ≈${Math.round(estIn / 1000)}K 字符 ≈${Math.round(estIn / 1.6 / 1000)}K token（中文约 1.6 字/token）`);
      console.log(`  [dry-run] 按每批 ${spec.batch.units_per_call} 段，共 ${Math.ceil(queue.length / spec.batch.units_per_call)} 次请求`);
      continue;
    }
    if (reportOnly) { /* 只出下面的对照报告 */ }

    const system = buildSystem(domain, tax, spec);
    const B = spec.batch.units_per_call;
    for (let i = 0; i < queue.length; i += B) {
      const batch = queue.slice(i, i + B);
      const messages = [{ role: 'system', content: system }, { role: 'user', content: buildUser(batch) }];
      let resp;
      try {
        resp = await llm.callLLM(messages, cfg, null, spec.batch.max_tokens, { quick: true });
      } catch (e) {
        console.log(`  ! 批次 ${Math.floor(i / B) + 1} 调用异常：${e.message}`); grand.failed += batch.length; continue;
      }
      if (!resp || !resp.ok) {
        console.log(`  ! 批次 ${Math.floor(i / B) + 1} 失败：${(resp && resp.error) || '无响应'}`);
        grand.failed += batch.length; continue;
      }
      const parsed = llm.extractJson(resp.text);
      let arr = Array.isArray(parsed) ? parsed
              : (parsed && Array.isArray(parsed.items)) ? parsed.items : null;
      // 兜底：模型偶尔无视契约直接吐顶层数组，而共用的 extractJson 只认第一个 {...}
      // （它会把数组里的第一条当成整个结果 —— 实测踩过，所以这里自己抠数组）。
      if (!arr) arr = extractJsonArray(resp.text);
      if (!arr) {
        const bad = saveBadResponse(domain, i, resp.text);
        console.log(`  ! 批次 ${Math.floor(i / B) + 1} 解析不出判定，整批作废（不写库，下次会重问）。原文已存 ${bad}`);
        grand.failed += batch.length; continue;
      }
      for (const v of arr) {
        const n = normalizeVerdict(v, batch, tax, spec);
        if (!n) continue;
        grand.invented += n.invented;
        grand.asked++;
        const u = batch[n.unit_index];
        putRow.run(u.key, domain.id, u.rel, u.heading, u.chars, n.kind,
                   JSON.stringify(n.topics), n.confidence, n.evidence,
                   'ai', cfg.model, spec.prompt_version, tax.hash);
      }
      process.stdout.write(`  批次 ${Math.floor(i / B) + 1}/${Math.ceil(queue.length / B)} ok\n`);
    }

    // 完整性台账：还剩多少段没判出来，写进库让消费方自己判能不能信这个口径。
    let pending = 0;
    for (const u of units) if (!gotRow.get(u.key)) pending++;
    db.prepare(`INSERT OR REPLACE INTO note_attr_runs
      (domain, units_total, units_pending, taxonomy_hash, prompt_ver, model, files_total, finished_at)
      VALUES (?,?,?,?,?,?,?,datetime('now','localtime'))`)
      .run(domain.id, units.length, pending, tax.hash, spec.prompt_version, cfg.model, files.length);

    // ---- 对照报告 ----
    const rows = db.prepare(`SELECT kind, topic_ids, confidence, rel_path, heading
                             FROM note_attributions WHERE domain = ?`).all(domain.id);
    const accept = new Set(); const review = new Map(); const kindCount = {};
    for (const r of rows) {
      kindCount[r.kind] = (kindCount[r.kind] || 0) + 1;
      let tp = []; try { tp = JSON.parse(r.topic_ids || '[]'); } catch (e) {}
      const counting = (spec.kinds.find(k => k.id === r.kind) || {}).counts_coverage;
      if (!counting) continue;
      for (const t of tp) {
        if (r.confidence >= spec.thresholds.accept) accept.add(t);
        else if (r.confidence >= spec.thresholds.review) {
          if (!review.has(t)) review.set(t, []);
          review.get(t).push(`${r.rel_path}#${r.heading} (${r.confidence})`);
        }
      }
    }
    const ruleCov = ruleCoveredTopics(domain, tax);
    const onlyAI = [...accept].filter(t => !ruleCov.has(t));
    const onlyRule = [...ruleCov].filter(t => !accept.has(t));
    console.log(`  材料类型分布：${JSON.stringify(kindCount)}`);
    console.log(`  规则判有笔记 ${ruleCov.size} 个考点 | AI 判有笔记 ${accept.size} 个`);
    console.log(`  AI 补出来的 ${onlyAI.length} 个：${onlyAI.slice(0, 25).join(', ')}`);
    console.log(`  规则说有、AI 不认的 ${onlyRule.length} 个：${onlyRule.slice(0, 15).join(', ')}`);
    console.log(`  待确认（置信度在队列里，不计覆盖）${review.size} 个考点`);
    console.log(`  完整性：${pending === 0
      ? '全部段落已判定，该科目按归因口径判覆盖'
      : `还差 ${pending} 段未判（占 ${(pending / Math.max(1, units.length) * 100).toFixed(0)}%），消费方将回退章节号口径`}`);
  }

  console.log('\n----- 合计 -----');
  console.log(`  文件 ${grand.files} | 单元 ${grand.units} | 命中缓存 ${grand.cached} | 本次判定 ${grand.asked} | 失败 ${grand.failed} | 模型编造的考点 id ${grand.invented} 个（已丢弃）`);
  db.close();
}

// 直接跑才执行；被测试 require 时只导出纯函数（切段/校验/兜底解析都不碰 API，可离线断言）
if (require.main === module) {
  main().catch(e => { console.error('FATAL', e); process.exit(1); });
}

module.exports = {
  loadSpec, loadTaxonomy, walkCorpus, splitUnits, stripNoise, unitKey,
  normalizeVerdict, extractJsonArray, buildSystem, buildUser, ruleCoveredTopics,
};

/*
 * ai_providers.js — 多供应商模型配置注册表（2026-09-26）
 *
 * 为什么有它：设置页原来只认一个 DeepSeek（.secrets.json 的 deepseek 段），
 * 用户要「多个 API 多个来源的模型、自由切换」（参照 Cherry Studio 的供应商管理）。
 * 本模块只负责**配置的存取与校验**；真正的调用协议适配在 grade_llm.js。
 *
 * 存储（src/.secrets.json，gitignored，密钥永不回传浏览器）：
 *   {
 *     "providers": [
 *       { "id": "pv-xxx", "name": "DeepSeek", "protocol": "openai",
 *         "base_url": "https://api.deepseek.com", "api_key": "sk-...",
 *         "model": "deepseek-flash", "nothink": true }
 *     ],
 *     "active_provider": "pv-xxx",
 *     "deepseek": { ... }        ← 旧段，保存时按「启用中的 openai 供应商」同步，
 *                                    让仓外脚本/旧代码继续能读（没有就不写坏）
 *   }
 *
 * 兼容：文件里没有 providers（老配置）时，从 deepseek 段**合成**一个供应商，
 * 不写盘——第一次在设置页改动后才落成新的形状。
 *
 * protocol 两种：
 *   openai    —— OpenAI 兼容 chat/completions（DeepSeek/OpenAI/Moonshot/GLM/Ollama/vLLM…）
 *   anthropic —— Anthropic Messages API（/v1/messages）
 *
 * nothink：quick 模式下要不要发 DeepSeek 的 thinking:{type:'disabled'}。
 * 这个参数是 DeepSeek 系的私有扩展，别的 OpenAI 兼容端点可能直接 400，
 * 所以按供应商存：base_url 含 deepseek 默认开，其余默认关，界面上可勾。
 */

const fs = require('fs');
const path = require('path');

// 与 grade_llm.js 同款：允许 SECRETS_PATH 指向副本，测试隔离密钥（踩过真钥被覆盖的坑）。
const SECRETS_PATH = process.env.SECRETS_PATH
  ? path.resolve(__dirname, process.env.SECRETS_PATH)
  : path.join(__dirname, '.secrets.json');

const PROTOCOLS = ['openai', 'anthropic'];
// 模型名会拼进上游请求体（JSON 字符串，不进 URL）：白名单字符。
// 比初版多认 冒号（Ollama tag）、斜杠与括号（聚合网关的 openai/gpt-4o、
// qwen3-vl-30b-a3b-thinking(free) 这类模型 id，2026-09-26 导入 Cherry 供应商时放开）
const MODEL_RE = /^[A-Za-z0-9._:/()-]{1,120}$/;
const URL_RE = /^https?:\/\/[^\s]+$/i;

function newId() {
  return 'pv-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
}

function readSecrets() {
  try { return JSON.parse(fs.readFileSync(SECRETS_PATH, 'utf-8')) || {}; }
  catch (e) { return {}; }
}

function writeSecrets(obj) {
  fs.writeFileSync(SECRETS_PATH, JSON.stringify(obj, null, 2), 'utf-8');
}

/**
 * 校验并归一化一个供应商字段集。
 * @param {object} raw 界面/请求传来的字段
 * @param {string} [keepKey] 更新时「api_key 留空 = 不修改」所保留的旧钥
 * @param {boolean} [requireKey] 新增时必须有钥
 */
function normalizeProvider(raw, keepKey, requireKey) {
  const r = raw || {};
  const p = {};
  p.id = String(r.id || '').trim() || newId();
  p.name = String(r.name || '').trim().slice(0, 40) || '未命名供应商';
  p.protocol = PROTOCOLS.indexOf(r.protocol) >= 0 ? r.protocol : 'openai';
  const base = String(r.base_url || '').trim().replace(/\/+$/, '');
  if (!URL_RE.test(base)) throw new Error('Base URL 必须是 http(s) 地址');
  if (base.length > 200) throw new Error('Base URL 过长');
  p.base_url = base;
  const model = String(r.model || '').trim();
  if (!MODEL_RE.test(model)) {
    throw new Error('模型名只能含字母、数字、点、下划线、连字符、冒号（1~80 字符）');
  }
  p.model = model;
  const key = r.api_key == null ? null : String(r.api_key).trim();
  if (key === null || key === '') {
    if (requireKey && !keepKey) throw new Error('需要 API Key');
    p.api_key = keepKey || '';
  } else {
    if (/\s/.test(key)) throw new Error('API Key 不应包含空白字符');
    if (key.length < 8 || key.length > 200) throw new Error('API Key 长度不合法');
    p.api_key = key;
  }
  // 没显式给就按域名猜：DeepSeek 系端点才默认发关思考参数
  p.nothink = r.nothink == null ? /deepseek/i.test(p.base_url) : !!r.nothink;
  return p;
}

/** 读注册表。老配置（无 providers）从 deepseek 段合成一个，不写盘。 */
function loadRegistry() {
  const s = readSecrets();
  let providers = null;
  if (Array.isArray(s.providers) && s.providers.length) {
    providers = s.providers
      .filter(x => x && typeof x === 'object')
      .map(x => ({
        id: String(x.id || '').trim() || newId(),
        name: String(x.name || '未命名供应商').slice(0, 40),
        protocol: PROTOCOLS.indexOf(x.protocol) >= 0 ? x.protocol : 'openai',
        base_url: String(x.base_url || 'https://api.deepseek.com').replace(/\/+$/, ''),
        model: String(x.model || 'deepseek-flash'),
        api_key: String(x.api_key || ''),
        nothink: !!x.nothink,
      }));
  }
  if (!providers || !providers.length) {
    const d = (s.deepseek && typeof s.deepseek === 'object') ? s.deepseek : {};
    providers = [{
      id: 'legacy-deepseek',
      name: 'DeepSeek',
      protocol: 'openai',
      base_url: String(d.base_url || 'https://api.deepseek.com').replace(/\/+$/, ''),
      model: String(d.model || 'deepseek-flash'),
      api_key: String(d.api_key || ''),
      nothink: true,
    }];
  }
  let active = String(s.active_provider || '');
  if (!providers.some(p => p.id === active)) active = providers[0].id;
  return { providers, active };
}

/** 落盘。顺手把旧 deepseek 段同步成「启用中的 openai 供应商」，仓外旧读者不受影响。 */
function saveRegistry(reg) {
  const s = readSecrets();
  const act = reg.providers.find(p => p.id === reg.active) || reg.providers[0];
  const out = Object.assign({}, s, {
    providers: reg.providers,
    active_provider: reg.active,
  });
  if (act && act.protocol === 'openai') {
    out.deepseek = { api_key: act.api_key, base_url: act.base_url, model: act.model };
  }
  writeSecrets(out);
  return out;
}

function findOrThrow(reg, id) {
  const p = reg.providers.find(x => x.id === id);
  if (!p) throw new Error('供应商不存在：' + id);
  return p;
}

function addProvider(reg, input) {
  const p = normalizeProvider(input, '', true);
  if (reg.providers.some(x => x.id === p.id)) p.id = newId();
  const next = { providers: reg.providers.concat([p]), active: reg.active };
  if (!next.active || !next.providers.some(x => x.id === next.active)) next.active = p.id;
  return { reg: next, provider: p };
}

function updateProvider(reg, id, input) {
  const old = findOrThrow(reg, id);
  const p = normalizeProvider(Object.assign({}, input, { id: id }), old.api_key, false);
  const providers = reg.providers.map(x => (x.id === id ? p : x));
  return { reg: { providers, active: reg.active }, provider: p };
}

function removeProvider(reg, id) {
  findOrThrow(reg, id);
  const providers = reg.providers.filter(x => x.id !== id);
  let active = reg.active;
  if (active === id) active = providers.length ? providers[0].id : '';
  return { reg: { providers, active } };
}

function setActive(reg, id) {
  findOrThrow(reg, id);
  return { reg: { providers: reg.providers, active: id } };
}

/** 给前端的清单：密钥只回显尾 4 位（与旧版 llmPublic 同口径）。 */
function publicList(reg) {
  return reg.providers.map(p => ({
    id: p.id,
    name: p.name,
    protocol: p.protocol,
    base_url: p.base_url,
    model: p.model,
    nothink: !!p.nothink,
    has_key: !!p.api_key,
    key_hint: p.api_key ? ('****' + p.api_key.slice(-4)) : '',
    active: p.id === reg.active,
  }));
}

/**
 * 当前启用供应商的调用配置（grade_llm.loadConfig 的唯一事实源）。
 * 环境变量 DEEPSEEK_* 仍是最优先覆盖（测试隔离 / 应急改指向都靠它）。
 */
function activeConfig(env) {
  const e = env || process.env;
  const reg = loadRegistry();
  const p = reg.providers.find(x => x.id === reg.active) || reg.providers[0];
  const cfg = {
    providerId: p.id,
    providerName: p.name,
    protocol: p.protocol,
    apiKey: p.api_key || '',
    baseUrl: p.base_url,
    model: p.model,
    nothink: !!p.nothink,
    timeoutMs: parseInt(e.DEEPSEEK_TIMEOUT_MS || '60000', 10),
  };
  if (e.DEEPSEEK_API_KEY) cfg.apiKey = e.DEEPSEEK_API_KEY;
  if (e.DEEPSEEK_BASE_URL) cfg.baseUrl = String(e.DEEPSEEK_BASE_URL).replace(/\/+$/, '');
  if (e.DEEPSEEK_MODEL) cfg.model = e.DEEPSEEK_MODEL;
  return cfg;
}

module.exports = {
  SECRETS_PATH, PROTOCOLS,
  newId, readSecrets, writeSecrets,
  normalizeProvider, loadRegistry, saveRegistry,
  addProvider, updateProvider, removeProvider, setActive,
  publicList, activeConfig,
};

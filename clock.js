'use strict';
// clock.js —— 时间校准（2026-09-30）
//
// 为什么需要它：番茄钟的 end_at 是「墙钟」——写进去的是**绝对时刻**，另一台设备
// 读出来算剩余时间。两端的系统时钟只要差几分钟，表就是错的（平板比电脑慢 7 秒
// 那种）。原先只做了「以服务端为准」的相对对齐，服务端自己要是错了，全体一起错。
//
// 分两层，正好对应两种网络状态：
//   1. **联网**：向外部授时源取标准时间，用 NTP 那套「往返中点」补偿单程延迟，
//      算出「标准时间 − 本机时钟」的偏差 offset，落盘 src/time_cal.json。
//      电脑时钟快/慢都被这一步拉正，其它端读同一个值也就一起正了。
//   2. **离线**：没什么外部东西可问——直接用**这台电脑自己的系统时钟**（offset = 0）。
//      这不是「退化到不可用」，是「退化成相对一致」：所有端都读服务端这一个 now()，
//      彼此之间仍然严丝合缝，只是整条时间轴可能和标准时间差一点。
//      兜底之所以成立，是因为服务端一定跑在某台有系统时钟的机器上（就是用户的电脑）。
//
// 对外只有一个入口：now()。serve.js 的 server_now、normPomoRun 的合理性窗口、
// 前端 skew 换算，全都以它为基准；App 端读同一份 time_cal.json。

const fs = require('fs');
const path = require('path');
const { SRC_DIR } = require('./paths');

const CAL_FILE = path.join(SRC_DIR, 'time_cal.json');

// 单次校正在 24 小时内 → 偏差仍然可信（石英钟一天漂不到 1 秒）；超过就退回本机时钟。
// 取 24 小时而不是「永久有效」，是因为「上次联网是半个月前」时，那份 offset 的
// 参考价值还不如本机时钟——本机时钟至少还在被系统自身同步（Windows 默认每周对时）。
const FRESH_MS = 24 * 3600 * 1000;
// 单次校正量超过一天 → 一定是解析错/授时源抽风，宁可不用。
const OFFSET_LIMIT_MS = 24 * 3600 * 1000;
// 单源超时。授时源都在墙外，2.5 秒还没回就当它不可用——番茄钟不值得等它。
const TIMEOUT_MS = 2500;
// 自动重校间隔：6 小时。石英钟漂移是每天几秒的量级，这个频率绰绰有余。
const SYNC_INTERVAL_MS = 6 * 3600 * 1000;

// ---------------------------------------------------------------------------
// 授时源。parse 收到 { text, date }（date = HTTP 响应头的 Date，精度 1 秒）。
// 多源不是为了「更准」，是为了「有备胎」：国内网络下这几个源总有能通的。
// ---------------------------------------------------------------------------
const SOURCES = [
  {
    name: 'taobao',
    url: 'https://acs.m.taobao.com/gw/mtop.common.getTimestamp/',
    parse: (r) => {
      const j = JSON.parse(r.text);
      return Number(j && j.data && j.data.t);
    },
  },
  {
    name: 'suning',
    url: 'https://quan.suning.com/getSysTime.do',
    parse: (r) => {
      const m = /"sysTime2"\s*:\s*"([^"]+)"/.exec(r.text);
      // 苏宁给的是北京时间字符串，没有时区标记，手工补 +08:00
      return m ? Date.parse(m[1].replace(' ', 'T') + '+08:00') : NaN;
    },
  },
  {
    name: 'cloudflare',
    url: 'https://www.cloudflare.com/cdn-cgi/trace',
    parse: (r) => {
      const m = /^ts=([\d.]+)/m.exec(r.text);
      return m ? Math.round(parseFloat(m[1]) * 1000) : NaN;
    },
  },
  {
    // 万能备胎：任何 HTTP 响应都带 Date 头。精度只到秒，但番茄钟不需要更细。
    name: 'date-header',
    url: 'https://www.baidu.com/',
    parse: (r) => (r && r.date ? Date.parse(r.date) : NaN),
  },
];

// ---------------------------------------------------------------------------
// 纯函数（好测）：从一次往返里算偏差、从多源采样里挑最可信的那份
// ---------------------------------------------------------------------------

/**
 * 由「发出请求的时刻 t0、收到响应的时刻 t1、响应里带的服务器时刻 serverMs」
 * 估算「服务器时钟 − 本机时钟」。
 * 假设上下行延迟对称：服务器那一刻大约是 t0 与 t1 的中点，
 * 所以它标的时间实际对应本机的 t1 − rtt/2，偏差要**补回半个往返**。
 * 不补的话，偏差里会掺进 RTT/2 的固定误差（墙外源 RTT 常有几百毫秒）。
 */
function offsetFromSample(t0, serverMs, t1) {
  const rtt = Math.max(0, t1 - t0);
  return Math.round(serverMs + rtt / 2 - t1);
}

/**
 * 多源采样里挑一份：只留偏差合理的，再取 RTT 最小的那份
 * （延迟越小，中点假设越准，这跟 NTP 挑最小延迟样本是同一个道理）。
 */
function pickSample(samples) {
  const ok = (samples || []).filter((s) => s && Number.isFinite(s.offset)
    && Math.abs(s.offset) < OFFSET_LIMIT_MS);
  if (!ok.length) return null;
  ok.sort((a, b) => a.rtt - b.rtt);
  return ok[0];
}

/**
 * 当前该用的偏差。两条前提缺一不可，任一条不满足都退回本机时钟（offset = 0）：
 *   ① 校准过且还在保鲜期内；② 本机时间没有倒退（用户手改过系统时间的话，
 *   旧那份 synced_at 就成了「未来」，此时的 offset 没有意义）。
 */
function effectiveOffset(state, nowMs) {
  if (!state || !Number.isFinite(state.synced_at) || !state.synced_at) return 0;
  const age = nowMs - state.synced_at;
  if (age < 0 || age > FRESH_MS) return 0;
  const off = Number(state.offset_ms);
  return Number.isFinite(off) ? Math.round(off) : 0;
}

// ---------------------------------------------------------------------------
// 可注入的实例（测试用假 fetcher / 假时钟；生产用下面那个默认单例）
// ---------------------------------------------------------------------------
function createClock(opts) {
  const o = opts || {};
  const file = o.file || CAL_FILE;
  const nowFn = o.now || Date.now;
  const fetchText = o.fetchText || defaultFetchText;
  const log = o.log || ((...a) => console.log(...a));
  const warn = o.warn || ((...a) => console.warn(...a));
  const sources = o.sources || SOURCES;

  // 已落盘的校准记录；读不出来（首次运行）就用「纯本机时钟」
  let state = { offset_ms: 0, synced_at: 0, source: 'system', rtt_ms: 0 };
  let lastError = '';
  let lastLoggedError = '';
  let syncing = null;

  try {
    const raw = JSON.parse(fs.readFileSync(file, 'utf-8'));
    if (raw && typeof raw === 'object') {
      state = {
        offset_ms: Number(raw.offset_ms) || 0,
        synced_at: Number(raw.synced_at) || 0,
        source: String(raw.source || 'system').slice(0, 32),
        rtt_ms: Number(raw.rtt_ms) || 0,
      };
    }
  } catch (e) { /* 首次运行 / 坏 JSON：静默走本机时钟 */ }

  function persist() {
    try {
      fs.writeFileSync(file, JSON.stringify(Object.assign({}, state, {
        samples: state.samples || 0, updated_at: nowFn(),
      }), null, 2), 'utf-8');
    } catch (e) { warn('[Clock] 写校准文件失败（不影响本次校准）:', e.message); }
  }

  /** 校准后的当前时刻（epoch 毫秒）。全项目的时间基准就是它。 */
  function now() { return nowFn() + effectiveOffset(state, nowFn()); }

  function status() {
    const sys = nowFn();
    const off = effectiveOffset(state, sys);
    const fresh = !!(state.synced_at && sys - state.synced_at >= 0 && sys - state.synced_at <= FRESH_MS);
    return {
      calibrated: fresh,                // true = 正在用外部标准时钟（false = 本机时钟）
      now: sys + off,
      sys_now: sys,
      offset_ms: off,
      source: fresh ? state.source : 'system',
      synced_at: state.synced_at || 0,
      age_ms: state.synced_at ? sys - state.synced_at : 0,
      fresh_ms: FRESH_MS,
      rtt_ms: state.rtt_ms || 0,
      syncing: !!syncing,
      last_error: lastError,
    };
  }

  /** 向所有源并发问一次，取最优样本落盘。失败保留旧记录，只更新 last_error。 */
  function sync() {
    if (syncing) return syncing;
    syncing = (async () => {
      const samples = await Promise.all(sources.map(async (s) => {
        const t0 = nowFn();
        try {
          const r = await fetchText(s.url, TIMEOUT_MS);
          const t1 = nowFn();
          const serverMs = s.parse(r);
          if (!Number.isFinite(serverMs) || serverMs <= 0) throw new Error('响应无法解析');
          return { name: s.name, rtt: t1 - t0, offset: offsetFromSample(t0, serverMs, t1) };
        } catch (e) {
          return { name: s.name, error: String((e && e.message) || e) };
        }
      }));
      const best = pickSample(samples);
      const failed = samples.filter((s) => s.error).map((s) => s.name + ':' + s.error);
      if (!best) {
        lastError = failed.join(' / ') || '没有可用的授时源';
        // 离线是常态（笔记本没网、平板在飞行模式），不值得每次都刷一条错误日志。
        // 只在「错误内容变了」时打一行，便于排查又不吵。
        if (lastError !== lastLoggedError) {
          lastLoggedError = lastError;
          log('[Clock] 外部授时不可用，改用本机时钟：' + lastError);
        }
        syncing = null;                 // 先把「正在校准」摘掉，status 才不骗人
        return status();
      }
      const drift = best.offset - (effectiveOffset(state, nowFn()) || 0);
      state = {
        offset_ms: best.offset,
        synced_at: nowFn(),
        source: best.name,
        rtt_ms: best.rtt,
        samples: samples.filter((s) => !s.error).length,
      };
      persist();
      lastError = failed.length ? failed.join(' / ') : '';
      log('[Clock] 已校准：源=' + best.name + ' 偏差=' + best.offset + 'ms RTT=' + best.rtt + 'ms'
        + '（本机时钟' + (drift >= 0 ? '慢' : '快') + Math.abs(drift) + 'ms）');
      syncing = null;
      return status();
    })().finally(() => { syncing = null; });
    return syncing;
  }

  /** 启动时校准一次，之后每 6 小时一次。定时器 unref，不拖住进程退出。 */
  function start() {
    sync();
    const t = setInterval(() => { sync(); }, SYNC_INTERVAL_MS);
    if (t.unref) t.unref();
    return t;
  }

  return { now, status, sync, start, _state: () => state };
}

/** 默认取数：只要能拿到文本 + 响应头的 Date 就够（见 SOURCES 的 date-header）。 */
async function defaultFetchText(url, timeoutMs) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const r = await fetch(url, {
      signal: ac.signal,
      redirect: 'follow',
      headers: { 'User-Agent': 'kaoyan-dashboard-clock/1.0' },
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const text = await r.text();
    return { text, date: r.headers.get('date') || '' };
  } finally {
    clearTimeout(timer);
  }
}

module.exports = createClock();
module.exports.createClock = createClock;
module.exports.SOURCES = SOURCES;
module.exports.offsetFromSample = offsetFromSample;
module.exports.pickSample = pickSample;
module.exports.effectiveOffset = effectiveOffset;
module.exports.FRESH_MS = FRESH_MS;
module.exports.OFFSET_LIMIT_MS = OFFSET_LIMIT_MS;
module.exports.CAL_FILE = CAL_FILE;
#!/usr/bin/env node
// ============================================================
// fsrs_cli.js —— FSRS 调度内核的跨语言桥
//
// 用途：让 Python 侧脚本（如 src/daily_planner.py 若将来需要调度）
//       无需重写算法即可复用生产调度逻辑。生产真相源是 src/fsrs_core.js。
//
// 用法（stdin 传 JSON，stdout 出 JSON）：
//   node src/tools/fsrs_cli.js schedule < in.json
//     入：{"config":{...可选...},"cases":[{"card":{...},"rating":3,"nowMs":1757...}]}
//     出：{"results":[{"state":..,"due_at":..,"due_date":..,"interval_days":..,
//                      "difficulty":..,"stability":..,"lapses":..,"reps":..,
//                      "queue":..,"leech":..,"suspended":..}, ...]}
//
//   node src/tools/fsrs_cli.js preview < in.json
//     入：{"config":{...},"cases":[{"card":{...},"nowMs":...}]}
//     出：{"results":[{"1":"<1分","2":"6分","3":"10分","4":"16天"}, ...]}
//
// 注：nowMs 省略时用当前时刻，结果将不可复现。测试务必显式传。
// ============================================================

const { FSRS } = require('../fsrs_core');

function readStdin() {
  return new Promise((resolve, reject) => {
    let buf = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (c) => { buf += c; });
    process.stdin.on('end', () => resolve(buf));
    process.stdin.on('error', reject);
  });
}

function pick(card) {
  return {
    state: card.state,
    due_at: card.due_at,
    due_date: card.due_date,
    interval_days: card.interval_days,
    difficulty: card.difficulty,
    stability: card.stability,
    lapses: card.lapses,
    reps: card.reps,
    queue: card.queue,
    learning_step: card.learning_step,
    relearning_step: card.relearning_step,
    leech: card.leech,
    suspended: card.suspended,
  };
}

(async function main() {
  const mode = process.argv[2];
  const raw = await readStdin();
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch (e) {
    process.stderr.write(`fsrs_cli: 入参 JSON 解析失败: ${e.message}\n`);
    process.exit(2);
  }

  const cases = payload.cases || [];
  const config = payload.config || undefined;
  let results;

  if (mode === 'schedule') {
    results = cases.map(({ card, rating, nowMs }) => {
      const c = Object.assign({}, card);
      FSRS.schedule(c, rating, { config, nowMs });
      return pick(c);
    });
  } else if (mode === 'preview') {
    results = cases.map(({ card, nowMs }) => FSRS.previewIntervals(card, { config, nowMs }));
  } else {
    process.stderr.write('fsrs_cli: 未知模式，应为 schedule 或 preview\n');
    process.exit(2);
    return;
  }

  process.stdout.write(JSON.stringify({ results }));
})();

// paths.js —— 路径的单一事实源（JS 侧；与 paths.py 同源同规则，2026-09-25 分离后建立）。
//
//   SRC_DIR    = 本文件所在目录（src/）
//   CODE_ROOT  = 项目根（src 的上级；番茄钟背景图 src/assets/pomo/** 的清单前缀按它解析）
//   NOTES_ROOT = 笔记库根：process.env.NOTES_ROOT > src/paths.json 的 notes_root > 内置默认
//
// ⚠️ 两处默认值（这里与 paths.py 的 DEFAULT_NOTES_ROOT）必须一致；
//    tools/check_metrics_drift.py 会把它们与 paths.json 三方对拍。
'use strict';
const path = require('path');
const fs = require('fs');

const SRC_DIR = __dirname;
const CODE_ROOT = path.resolve(__dirname, '..');
const DEFAULT_NOTES_ROOT = 'E:\\NPEE';

function resolveNotesRoot() {
  const env = process.env.NOTES_ROOT;
  if (env && env.trim()) return path.resolve(env.trim());
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(SRC_DIR, 'paths.json'), 'utf-8'));
    if (cfg && typeof cfg.notes_root === 'string' && cfg.notes_root.trim()) {
      return path.resolve(cfg.notes_root.trim());
    }
  } catch (e) { /* 配置缺失/坏 JSON 都回退内置默认 */ }
  return DEFAULT_NOTES_ROOT;
}

const NOTES_ROOT = resolveNotesRoot();

module.exports = { SRC_DIR, CODE_ROOT, NOTES_ROOT, DEFAULT_NOTES_ROOT };

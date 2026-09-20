#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
generate_dashboard.py — Local HTML Dashboard Generator

Generates C:\Users\92534\Desktop\考研\src\dashboard.html — a single-file analytics dashboard
with inline D3.js visualizations for the 考研 study project.

Usage:
    python generate_dashboard.py
"""

import io
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path

# Force UTF-8 on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml")
    sys.exit(1)

# 标签页图标。图形定义在 tools/icon_design.py，与桌面快捷方式的 .ico 同源。
# 用 data URI 内联而不是外链 favicon.ico：大盘既可能走 http://localhost:8080，
# 也可能被直接 file:// 打开，内联两边都不用额外部署。
# 取不到就退化成 data:,（浏览器视为「没有图标」），不能因为一个图标让大盘生成失败。
try:
    sys.path.append(str(Path(__file__).resolve().parent / "tools"))
    from icon_design import favicon_href as _favicon_href
    FAVICON_HREF = _favicon_href()
except Exception as _e:                                   # noqa: BLE001
    print("WARN: favicon 生成失败（%s），本次不写入图标" % _e)
    FAVICON_HREF = "data:,"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR      = Path(r"C:\Users\92534\Desktop\考研")
INDEX_PATH    = BASE_DIR / "src" / "笔记索引.yaml"
GRAPH_DIR     = BASE_DIR / "src" / "knowledge_graph"
DB_PATH       = BASE_DIR / "src" / "question_bank.db"
STATE_PATH    = BASE_DIR / "src" / "sync_state.json"
DASH_DATA_OUT = BASE_DIR / "src" / "dashboard_data.json"
DECKS_DIR     = BASE_DIR / "src" / "flashcards" / "decks"
OUTPUT_PATH   = BASE_DIR / "src" / "dashboard.html"

EXAM_DATE = date(2026, 12, 19)

# 学习日起点：凌晨 4 点前算前一天（2026-09-17）。
# 下发给页面里的 POMO_JS（todayKey）与 FLASH_JS（todayStr），让前端算出来的
# 「今天」跟服务端 localToday() 是同一个。两边对不上会出真 bug：POMO_JS 拿
# 本地 todayKey 去和服务端返回的 today 做跨设备合并，差一天就会把刚记的成绩覆盖成 0。
# ⚠️ serve.js 里有一份**同值**常量 DAY_START_HOUR，改这里必须一起改那边。
#    tools/test_day_start.js 会盯着这两个值是否还相等。
DAY_START_HOUR = 4

# 下发给页面的那段 JS（占位符 // __DAY_START__）。studyDay() 是前端版的
# localToday()，跟 serve.js 的实现逐行对应，别只改一边。
DAY_START_JS = """
// ── 学习日起点（generate_dashboard.py 注入，值来自那边的 DAY_START_HOUR）──
// 凌晨 4 点前算前一天。跟服务端 localToday() / studyDayStart() 是同一套规则：
// 前端算出的「今天」必须和服务端一致，否则 POMO_JS 跨设备合并时会把成绩覆盖成 0。
// ⚠️ 这段必须排在 FLASH_JS / POMO_JS 前面（占位符在脚本块最开头）。
const DAY_START_HOUR = %d;
function studyDay(d) {
    const t = d ? new Date(d.getTime()) : new Date();
    if (t.getHours() < DAY_START_HOUR) t.setDate(t.getDate() - 1);   // 还没到起点 → 算前一天
    return t.getFullYear() + "-" + String(t.getMonth() + 1).padStart(2, "0")
         + "-" + String(t.getDate()).padStart(2, "0");
}
""" % DAY_START_HOUR

# ============================================================
# 快捷键总表（占位符 // __KEYS_JS__，2026-09-17）
#
# 为什么要有这一层：以前每个模块各自写死 ev.key === "u"，键位散在六七个地方，
# 设置页想说清楚「现在有哪些快捷键」只能靠手抄，改一个键得翻遍全文，而且改完
# 提示文案还留在旧键上教人按错。这里收成一张表 —— **表是唯一真相源**：模块只问
# 「这个事件是不是这个动作」，设置页直接读表渲染，提示文案也从表里生成。
#
# 三条约定，改键盘前先读：
#  ① **空格在 ev.key 里是 " "（一个空格字符），不是 "Space"** —— 直接写
#     ev.key === "Space" 永远不成立（闪卡那边一直用的是 ev.code，两套写法混着
#     最容易出「改了键没反应」这种查半天的 bug）。所有键名先进 canonKey() 归一化。
#  ② **Scope 决定撞键算不算冲突**：闪卡练习区和笔记阅读弹窗互斥出现，同一个 F
#     各管各的，不算冲突；同一个 scope 内撞了才是真冲突，设置页会拦住。
#  ③ **Shift 不参与匹配**：F 和 Shift+F 是同一个物理键，浏览器给的 ev.key 一个是
#     "f" 一个是 "F"，要求 shift 精确相等的话，大写状态按 F 就没反应了 —— 这是
#     原代码 `ev.key !== "f" && ev.key !== "F"` 在防的坑，别在匹配器里重新踩回去。
# ============================================================
# 运行时错误日志（2026-09-18）：最先注入（marker // __LOG_JS__ 在主脚本最顶部，
# 早于 const D）。
#   - 捕获 window 'error' / 'unhandledrejection' / console.error，带 时间+页面+堆栈。
#   - 环形缓冲进 localStorage(kaoyan_runtime_log, 上限 300 条)，并批量 POST 到
#     /api/logs 落服务端 error.log（[web] 前缀），出问题时敢 GET /api/logs 拉。
#   - 全部 try/catch 静默，绝不反向打断页面。
#   - 暴露 window.__webLog({kind,message,stack}) 供关键动作埋点复用。
LOG_JS = '''
(function () {
    var KEY = 'kaoyan_runtime_log';
    var pending = [];
    var timer = null;
    function page() { return (location.hash || '').replace(/^#\\/?/, '') || 'root'; }
    function push(kind, message, stack) {
        var e = { ts: Date.now(), page: page(), kind: kind, message: message, stack: stack || '' };
        try {
            var arr = [];
            try { arr = JSON.parse(window.localStorage.getItem(KEY) || '[]'); } catch (_) {}
            if (!Array.isArray(arr)) arr = [];
            arr.push(e);
            if (arr.length > 300) arr = arr.slice(arr.length - 300);
            try { window.localStorage.setItem(KEY, JSON.stringify(arr)); } catch (_) {}
        } catch (_) {}
        pending.push(e);
        if (pending.length >= 5) flush();
        else if (!timer) { timer = setTimeout(function () { timer = null; flush(); }, 12000); }
    }
    function flush() {
        if (!pending.length) return;
        var batch = pending; pending = [];
        try {
            fetch('/api/logs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source: 'web', entries: batch }) }).catch(function () {});
        } catch (_) {}
    }
    if (window.addEventListener) {
        window.addEventListener('error', function (ev) {
            try {
                var msg = ev && ev.message ? String(ev.message) : '';
                var st = ev && ev.error && ev.error.stack ? String(ev.error.stack) : '';
                if (!msg && ev && ev.filename) msg = String(ev.filename) + ':' + (ev.lineno || '');
                push('error', msg, st);
            } catch (_) {}
        }, true);
        window.addEventListener('unhandledrejection', function (ev) {
            try {
                var r = (ev && ev.reason) || {};
                var msg = r && r.message ? String(r.message) : String(r);
                push('reject', msg, (r && r.stack ? String(r.stack) : ''));
            } catch (_) {}
        });
    }
    try {
        var oe = console.error.bind(console);
        console.error = function () {
            oe.apply(null, arguments);
            try {
                push('console-error', Array.prototype.slice.call(arguments).map(function (a) {
                    if (typeof a === 'string') return a;
                    try { var s = JSON.stringify(a); return s === undefined ? String(a) : s; } catch (_) { return String(a); }
                }).join(' '), '');
            } catch (_) {}
        };
    } catch (_) {}
    window.__webLog = function (o) { push(o.kind || 'info', o.message || '', o.stack || ''); };
    window.__webLogDump = function () {
        try { return JSON.parse(window.localStorage.getItem(KEY) || '[]'); } catch (_) { return []; }
    };
})();
'''

# ============================================================
KEYS_JS = '''
(function () {
    const LS_KEY = "kaoyan.keys.v1";

    // 可改键的动作。spec = 主键，aliases = 常驻别名（不可改，界面上标出来）。
    const DEFS = [
        { id: "flash.reveal", group: "闪卡练习区", scope: "flash",
          label: "显示答案 / 下一张",
          desc: "未作答时揭晓答案；答错时翻下一张；自评阶段＝「记得」",
          spec: "Space", aliases: ["Enter"] },
        { id: "flash.undo", group: "闪卡练习区", scope: "flash",
          label: "撤销上一次评分",
          desc: "任何阶段都能按，回到上一张重新作答",
          spec: "u" },
        { id: "flash.pin", group: "闪卡练习区", scope: "flash",
          label: "📌 钉住 / 取消钉住这张卡",
          desc: "这道题我想留着。钉住的卡永远排在智能组前面，也不会因为「连对几次」被收起来",
          spec: "p" },
        { id: "flash.full", group: "闪卡练习区", scope: "flash",
          label: "全屏练习",
          desc: "练习区铺满屏幕，再按一次退出",
          spec: "f" },
        { id: "read.full", group: "笔记阅读弹窗", scope: "read",
          label: "弹窗内全屏",
          desc: "读笔记时铺满屏幕，左侧会出现章节大纲",
          spec: "f" },
    ];

    // 固定键位：只展示不给改。两条理由 ——
    //  ① 一组键（选项 A–D、自评 1–4）要改就得改成另一组，界面得做成「键位组
    //     编辑器」；而这组键还靠「有没有显示答案」消歧（同一个数字键在未作答时
    //     是选选项、揭晓后是自评），随便改容易把那个状态机绕晕。
    //  ② Esc：原生全屏、各层弹窗关闭都吃它。改掉指不定把人锁在全屏里出不来。
    const FIXED = [
        { group: "闪卡练习区", label: "选择选项", keys: "A–D / 1–4",
          desc: "字母或数字都行，按选项序号；当前题库最多 A–D" },
        { group: "闪卡练习区", label: "自评熟练度", keys: "1–4",
          desc: "1 忘记 · 2 模糊 · 3 记得 · 4 简单。和「选择选项」共用数字键，靠是否已显示答案区分" },
        { group: "闪卡练习区", label: "提交简答批改", keys: "Ctrl+Enter",
          desc: "在作答框里提交给 AI 批改；单独按回车是换行" },
        { group: "各处输入框", label: "发送 / 搜索", keys: "Enter",
          desc: "追问框、聊天框、任务框、搜索框通用；Shift+Enter 换行" },
        { group: "退出 / 关闭", label: "退出全屏或关闭弹层", keys: "Esc",
          desc: "闪卡全屏、笔记阅读弹窗、手写板、番茄钟全屏，按最上面那一层逐层退" },
    ];

    // ---- 读写 ----
    function readOverrides() {
        try {
            const o = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
            return (o && typeof o === "object") ? o : {};
        } catch (e) { return {}; }   // 隐私模式 / 坏值都退回默认，不能带崩整页
    }
    let overrides = readOverrides();

    function def(id) {
        for (let i = 0; i < DEFS.length; i++) if (DEFS[i].id === id) return DEFS[i];
        return null;
    }
    function specOf(id) {
        const d = def(id);
        if (!d) return null;
        const o = overrides[id];
        return (typeof o === "string" && o) ? o : d.spec;
    }
    function isCustom(id) {
        const d = def(id);
        if (!d) return false;
        const o = overrides[id];
        return !!(typeof o === "string" && o && o !== d.spec);
    }
    function save() {
        try { localStorage.setItem(LS_KEY, JSON.stringify(overrides)); } catch (e) {}
    }
    function emit() {
        // 用事件而不是回调列表：模块之间本来就互不认识，谁想跟着更新谁自己听。
        try { document.dispatchEvent(new CustomEvent("kaoyan:keys-changed")); } catch (e) {}
    }

    // ---- 键名归一化与匹配 ----
    // ⚠️ 空格在 ev.key 里是 " "，不是 "Space"（见文件头约定①）。
    function canonKey(ev) {
        const k = ev.key;
        if (k === " " || k === "Spacebar" || ev.code === "Space") return "Space";
        if (k && k.length === 1) return k.toLowerCase();
        return k || "";
    }
    // "Ctrl+Enter" → { ctrl: true, key: "Enter" }。Shift 刻意不参与（约定③）。
    function parseSpec(spec) {
        const parts = String(spec == null ? "" : spec).split("+")
            .map(function (s) { return s.trim(); })
            .filter(Boolean);
        if (!parts.length) return { ctrl: false, alt: false, key: "" };
        const raw = parts[parts.length - 1];
        const mods = parts.slice(0, -1).map(function (m) { return m.toLowerCase(); });
        return {
            ctrl: mods.indexOf("ctrl") >= 0 || mods.indexOf("cmd") >= 0 || mods.indexOf("meta") >= 0,
            alt: mods.indexOf("alt") >= 0,
            key: raw.length === 1 ? raw.toLowerCase() : raw,
        };
    }
    // 这个事件是不是这个动作？模块只该问这一句，别自己比 ev.key。
    function matches(id, ev) {
        const d = def(id);
        if (!d) return false;
        const list = [specOf(id)].concat(d.aliases || []);
        const k = canonKey(ev);
        if (!k) return false;
        for (let i = 0; i < list.length; i++) {
            const p = parseSpec(list[i]);
            if (p.key !== k) continue;
            if (p.ctrl !== !!(ev.ctrlKey || ev.metaKey)) continue;
            if (p.alt !== !!ev.altKey) continue;
            return true;
        }
        return false;
    }

    // ---- 展示 ----
    const NAMES = {
        Space: "空格", Escape: "Esc", Enter: "回车", Tab: "Tab", Backspace: "退格",
        ArrowUp: "↑", ArrowDown: "↓", ArrowLeft: "←", ArrowRight: "→",
    };
    function pretty(spec) {
        const p = parseSpec(spec);
        if (!p.key) return "—";
        const base = NAMES[p.key] || (p.key.length === 1 ? p.key.toUpperCase() : p.key);
        const mods = [];
        if (p.ctrl) mods.push("Ctrl");
        if (p.alt) mods.push("Alt");
        return mods.concat(base).join(" + ");
    }

    // 主键 + 别名一起排出来（"空格 / 回车"）。底部提示条要显示全部能用的键，
    // 只写主键的话用户会以为回车不管用了。
    function prettyAll(id) {
        const d = def(id);
        if (!d) return "";
        return [specOf(id)].concat(d.aliases || []).map(pretty).join(" / ");
    }
    function keysOf(id) { return pretty(specOf(id)); }

    // 同 scope 内撞键检测（约定②）。返回撞上的那个动作定义，没撞返回 null。
    function conflicts(id, spec) {
        const d = def(id);
        if (!d) return null;
        const p = parseSpec(spec);
        if (!p.key) return null;
        for (let i = 0; i < DEFS.length; i++) {
            const o = DEFS[i];
            if (o.id === id || o.scope !== d.scope) continue;
            const list = [specOf(o.id)].concat(o.aliases || []);
            for (let j = 0; j < list.length; j++) {
                const q = parseSpec(list[j]);
                if (q.key === p.key && q.ctrl === p.ctrl && q.alt === p.alt) return o;
            }
        }
        return null;
    }

    // ---- 改键 ----
    function set(id, spec) {
        const d = def(id);
        if (!d) return { ok: false, msg: "没有这个动作" };
        if (spec === d.spec) delete overrides[id];    // 改回默认就当没改过，不落盘
        else overrides[id] = spec;
        save(); emit();
        return { ok: true };
    }
    function reset(id) { if (def(id)) { delete overrides[id]; save(); emit(); } }
    function resetAll() { overrides = {}; save(); emit(); }

    // ---- 设置页 UI ----
    // 点一下按键芯片就进录制态：按任意键 → 存；Esc → 取消。
    // 录制用 capture 且 stopPropagation，否则按 U / F 会被闪卡那边顺手接走。
    let recording = false;

    function status(msg, kind) {
        const el = document.getElementById("set-keys-status");
        if (!el) return;
        el.textContent = msg || "";
        el.className = "set-status" + (kind ? " " + kind : "");
    }

    function chipFor(d) {
        const btn = document.createElement("button");
        btn.className = "key-chip";
        btn.type = "button";
        btn.textContent = pretty(specOf(d.id));
        if (isCustom(d.id)) {
            btn.classList.add("custom");
            btn.title = "已自定义，默认是「" + pretty(d.spec) + "」。点击改键，Esc 取消";
        } else {
            btn.title = "点击改键，Esc 取消";
        }
        btn.onclick = function () { record(d, btn); };
        return btn;
    }

    function record(d, btn) {
        if (recording) return;
        recording = true;
        btn.classList.add("rec");
        btn.textContent = "按下新键…";
        status("按下要绑定的键，Esc 取消。只认单键（可带 Ctrl / Alt）。");

        const onKey = function (ev) {
            ev.preventDefault();
            // 拦住这一下，别让它冒到闪卡 / 弹窗那边去（正录着键呢）
            if (ev.stopPropagation) ev.stopPropagation();
            if (ev.key === "Escape") { stop(); status("已取消"); renderUI(); return; }
            // 纯修饰键不是键位，等真正的那个键
            if (["Control", "Shift", "Alt", "Meta"].indexOf(ev.key) >= 0) return;
            const k = canonKey(ev);
            if (!k) return;
            const parts = [];
            if (ev.ctrlKey || ev.metaKey) parts.push("Ctrl");
            if (ev.altKey) parts.push("Alt");
            parts.push(k);
            const spec = parts.join("+");
            const other = conflicts(d.id, spec);
            if (other) {
                // 撞了就留在录制态让人直接换一个，别一脚踢出去重来
                status("「" + pretty(spec) + "」已经被「" + other.label + "」占了，换一个。", "warn");
                return;
            }
            set(d.id, spec);
            stop();
            status("「" + d.label + "」已改为 " + pretty(spec), "ok");
            renderUI();
        };
        const stop = function () {
            recording = false;
            document.removeEventListener("keydown", onKey, true);
        };
        document.addEventListener("keydown", onKey, true);
    }

    function bindableRow(d) {
        const row = document.createElement("div");
        row.className = "key-row";

        const info = document.createElement("div");
        info.className = "key-info";
        const name = document.createElement("div");
        name.className = "key-name";
        name.textContent = d.label;
        // 别名（回车等价于空格）标出来，否则用户会以为「回车怎么没用」
        (d.aliases || []).forEach(function (a) {
            const tag = document.createElement("span");
            tag.className = "key-alias";
            tag.textContent = pretty(a) + " 也可";
            name.appendChild(tag);
        });
        const desc = document.createElement("div");
        desc.className = "key-desc";
        desc.textContent = d.desc;
        info.appendChild(name);
        info.appendChild(desc);
        row.appendChild(info);

        row.appendChild(chipFor(d));

        const undo = document.createElement("button");
        undo.className = "key-mini";
        undo.type = "button";
        undo.textContent = "↺";
        undo.title = "恢复默认（" + pretty(d.spec) + "）";
        undo.disabled = !isCustom(d.id);
        undo.onclick = function () {
            reset(d.id);
            status("「" + d.label + "」已恢复默认 " + pretty(d.spec));
            renderUI();
        };
        row.appendChild(undo);
        return row;
    }

    function fixedRow(f) {
        const row = document.createElement("div");
        row.className = "key-row fixed";
        const info = document.createElement("div");
        info.className = "key-info";
        const name = document.createElement("div");
        name.className = "key-name";
        name.textContent = f.label;
        const desc = document.createElement("div");
        desc.className = "key-desc";
        desc.textContent = f.desc;
        info.appendChild(name); info.appendChild(desc);
        row.appendChild(info);
        const chip = document.createElement("span");
        chip.className = "key-chip fixed";
        chip.textContent = f.keys;
        chip.title = "固定键位，不开放修改";
        row.appendChild(chip);
        const pad = document.createElement("span");
        pad.className = "key-mini-pad";
        row.appendChild(pad);
        return row;
    }

    function renderUI() {
        const box = document.getElementById("set-keys");
        if (!box) return;
        // 重渲染会把状态行一起冲掉（改完键要显示「已改为 …」），先记下再补回去。
        // 换来的好处是调用方不用关心「先报状态还是先重渲染」的顺序。
        const prev = document.getElementById("set-keys-status");
        const prevText = prev ? prev.textContent : "";
        const prevCls = prev ? prev.className : "set-status";
        box.innerHTML = "";
        // 分组顺序：先按 DEFS 里出现的顺序，再把只在 FIXED 里出现的组补在后面
        const groups = [];
        DEFS.forEach(function (d) { if (groups.indexOf(d.group) < 0) groups.push(d.group); });
        FIXED.forEach(function (f) { if (groups.indexOf(f.group) < 0) groups.push(f.group); });

        groups.forEach(function (g) {
            const wrap = document.createElement("div");
            wrap.className = "key-group";
            const h = document.createElement("div");
            h.className = "key-group-name";
            h.textContent = g;
            wrap.appendChild(h);
            DEFS.filter(function (d) { return d.group === g; }).forEach(function (d) {
                wrap.appendChild(bindableRow(d));
            });
            FIXED.filter(function (f) { return f.group === g; }).forEach(function (f) {
                wrap.appendChild(fixedRow(f));
            });
            box.appendChild(wrap);
        });

        const foot = document.createElement("div");
        foot.className = "set-row key-foot";
        const all = document.createElement("button");
        all.className = "fs-btn";
        all.type = "button";
        all.textContent = "全部恢复默认";
        all.disabled = !DEFS.some(function (d) { return isCustom(d.id); });
        all.onclick = function () {
            resetAll();
            status("已全部恢复默认");
            renderUI();
        };
        foot.appendChild(all);
        const st = document.createElement("span");
        st.className = prevCls;
        st.id = "set-keys-status";
        st.textContent = prevText;
        foot.appendChild(st);
        box.appendChild(foot);
    }

    globalThis.__keys = {
        DEFS: DEFS, FIXED: FIXED,
        matches: matches, specOf: specOf, pretty: pretty, prettyAll: prettyAll,
        keysOf: keysOf, isCustom: isCustom,
        set: set, reset: reset, resetAll: resetAll, conflicts: conflicts,
        renderUI: renderUI,
        recording: function () { return recording; },
    };
})();

'''

# 四科的规范名（与 topics.subject 的原值一致）。
# 活动页的「各科正确率」要**固定**按这个顺序输出四个，缺的补 0——
# 早先用 GROUP BY t.subject 直接从答题记录取，没练过的科目干脆不出现，
# 用户看到的是「缺失」而不是「0%」，会以为数据坏了。
# 2026-09-19 起科目清单由 subjects.json 派生（subjects_conf.py），
# 与 serve.js 的 NOTE_SUBJECTS / QUOTA_SUBJECTS 同口径，改学科只动配置文件。
import subjects_conf  # noqa: E402
ALL_SUBJECTS = subjects_conf.task_subjects()                  # graph_key 列表
NOTE_PREFIX_MAP = subjects_conf.note_prefix_map()             # 笔记目录 → 知识点前缀
SUBJECT_ALL_PREFIXES = subjects_conf.subject_prefixes()       # 短名 → 前缀列表

# 政治笔记的短编号（MY-001 / 思修-001 / ZT-001）与图谱前缀（POL-MY）不是一套体系，
# 映射表抽到 note_prefix.py 与 gap_analysis.py 共用——两个脚本各存一份正是
# 「XSX 被错映射到 POL-SX」和「思修笔记无人认领」两个 bug 的来源（2026-09-17 修）。
from note_prefix import note_entry_prefix, topic_note_chapter  # noqa: E402

def note_files_for(rel: str) -> list:
    """列出某个笔记前缀对应的 .md 文件。

    ⚠️ 必须同时支持两种形态（2026-08-07 起 Politics 改为一科一文件）：
      * 目录形态：408/DS/*.md      —— 408、数学、英语仍是这种
      * 单文件形态：Politics/马原.md —— 政治已改成一科一个文件
    历史上这里只判断 is_dir()，导致政治笔记永远匹配不上，
    「近日笔记加权」对政治完全失效（2026-09-13 修复）。
    """
    base = BASE_DIR / rel
    try:
        if base.is_dir():
            return [f for f in base.iterdir()
                    if f.suffix.lower() == ".md" and not f.name.startswith("_")]
        single = base.with_suffix(".md")
        if single.is_file():
            return [single]
    except OSError:
        pass
    return []


# 图谱文件名：key 用**短名**（与 load_graphs 的 graphs key、coverage_by_subject
# 的 key 一致，前端直接拿它当科目名显示）。学科/图谱文件名来自 subjects.json。
GRAPH_FILES = {s.get("short", s["id"]): s["graph_file"]
               for s in subjects_conf.load_subjects() if s.get("graph_file")}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_index() -> dict:
    """Load the notes index."""
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_graphs() -> dict:
    """Load all knowledge graph JSON files."""
    graphs = {}
    for subj, fname in GRAPH_FILES.items():
        fpath = GRAPH_DIR / fname
        if fpath.exists():
            with open(fpath, "r", encoding="utf-8") as f:
                graphs[subj] = json.load(f)
    return graphs


def load_db_stats() -> dict:
    """Load flashcard statistics from SQLite."""
    stats = {
        "card_states": {"New": 0, "Learning": 0, "Review": 0, "Relearning": 0},
        "total_cards": 0,
        "due_today": 0,
        "reviewed_today": 0,
        "accuracy_trend": [],
    }

    if not DB_PATH.exists():
        return stats

    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()

    # Card state distribution (FSRS states: 0=New, 1=Learning, 2=Review, 3=Relearning)
    state_map = {0: "New", 1: "Learning", 2: "Review", 3: "Relearning"}
    rows = c.execute("SELECT state, COUNT(*) FROM cards GROUP BY state").fetchall()
    for state_val, count in rows:
        label = state_map.get(state_val, "New")
        stats["card_states"][label] = count
    stats["total_cards"] = sum(stats["card_states"].values())

    # Due today
    today_str = date.today().isoformat()
    try:
        due = c.execute(
            "SELECT COUNT(*) FROM cards WHERE due_date <= ?", (today_str,)
        ).fetchone()
        stats["due_today"] = due[0] if due else 0
    except Exception:
        stats["due_today"] = 0

    # Reviewed today
    try:
        reviewed = c.execute(
            "SELECT COUNT(*) FROM review_log WHERE date(review_date) = ?",
            (today_str,),
        ).fetchone()
        stats["reviewed_today"] = reviewed[0] if reviewed else 0
    except Exception:
        stats["reviewed_today"] = 0

    # Accuracy trend (last 14 days)
    try:
        rows = c.execute("""
            SELECT date(review_date) as d,
                   COUNT(*) as total,
                   SUM(CASE WHEN rating >= 3 THEN 1 ELSE 0 END) as correct
            FROM review_log
            WHERE review_date >= date('now', '-14 days')
            GROUP BY date(review_date)
            ORDER BY d
        """).fetchall()
        for d, total, correct in rows:
            stats["accuracy_trend"].append({
                "date": d,
                "accuracy": round(correct / total * 100, 1) if total > 0 else 0,
                "total": total,
            })
    except Exception:
        pass

    conn.close()
    return stats


# 低于这个练习次数就标「样本少」：正确率本身没问题，但 1/3 和 60/90 的说服力
# 差得远，得让用户知道这个百分比是靠几次题算出来的。
WEAK_LOW_SAMPLE = 5


def load_weak_topics() -> dict:
    """从闪卡数据库提取薄弱知识点，按「正确率」排序。

    正确率 = 答对次数(rating>=3) / 练习次数。早先按「错误次数」排有两个毛病：
    练得多的考点天然错得多（刷了 90 次的必然比只刷 3 次的错得多），而只错一次
    的考点又会靠运气挤进榜单——两个方向的偏差都让榜单失真。
    近 30 天正确率单独算一份，用来区分「一直不会」和「最近才掉下来」。

    返回 {"weak": [...], "uncovered_weighty": [...]}。大盘只负责提醒薄弱处，
    不评判每日任务完成情况（用户有自己的计划）。
    """
    result = {"weak": [], "uncovered_weighty": []}
    if not DB_PATH.exists():
        return result

    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()

    # 1) 薄弱知识点：按 topic 汇总练习次数 / 答对次数，只统计真正练过的考点。
    #    用 INNER JOIN review_log（练过才进榜）；不再 JOIN cards——cards 与
    #    review_log 同时展开是笛卡尔积，会让 SUM(cd.lapses) 按答题记录条数翻倍
    #    （全库真实 lapses 合计只有 1，之前却有考点显示「遗忘 8 次」）。
    try:
        rows = c.execute("""
            SELECT t.id, t.name, t.subject,
                   COUNT(rl.id) AS total,
                   SUM(CASE WHEN rl.rating >= 3 THEN 1 ELSE 0 END) AS correct,
                   SUM(CASE WHEN rl.review_date >= datetime('now', 'localtime', '-30 days')
                            THEN 1 ELSE 0 END) AS recent_total,
                   SUM(CASE WHEN rl.rating <= 2
                             AND rl.review_date >= datetime('now', 'localtime', '-30 days')
                            THEN 1 ELSE 0 END) AS recent_wrong
            FROM topics t
            JOIN questions  q  ON q.topic_id = t.id
            JOIN review_log rl ON rl.question_id = q.id
            GROUP BY t.id
        """).fetchall()
        for tid, name, subject, total, correct, recent_total, recent_wrong in rows:
            total = int(total or 0)
            correct = int(correct or 0)
            if total <= 0 or correct >= total:
                continue          # 没练过、或一次没错的考点不算薄弱
            recent_total = int(recent_total or 0)
            recent_wrong = int(recent_wrong or 0)
            result["weak"].append({
                "id": tid, "name": name, "subject": subject,
                "total": total,
                "correct": correct,
                "accuracy": round(correct / total * 100),
                "recent_total": recent_total,
                "recent_wrong": recent_wrong,
                "recent_accuracy": (round((recent_total - recent_wrong) / recent_total * 100)
                                    if recent_total else None),
                "low_sample": total < WEAK_LOW_SAMPLE,
            })
        # 正确率越低越薄弱；正确率相同的，练得多的更可信、排前面
        result["weak"].sort(key=lambda w: (w["accuracy"], -w["total"]))
        result["weak"] = result["weak"][:8]
    except Exception:
        pass

    # 2) 高分考点但尚未出卡（提醒针对性生成）
    #    ⚠️ 两个坑都会让这份清单**静默为空**（看着像"全都出过卡了"）：
    #    ① `t.id NOT IN (SELECT DISTINCT topic_id FROM questions)` 被 13 条 topic_id
    #       为空的题污染——`x NOT IN (…NULL…)` 是 NULL 不是 true → 结果集清空。
    #    ② 题目同时打在父考点与子考点上（父级 398 / 叶子 179），只比 t.id 会把
    #       「树和二叉树」（子考点已有 10 题）误判成零覆盖 → 出卡任务重复造卡。
    #    改用 NULL 安全且认子考点的 NOT EXISTS，并只取图谱粒度（三段）的考点。
    try:
        rows = c.execute("""
            SELECT t.id, t.name, t.subject, t.exam_weight
            FROM topics t
            WHERE t.exam_weight >= 2
              AND (length(t.id) - length(replace(t.id, '-', ''))) = 2
              AND NOT EXISTS (SELECT 1 FROM questions q
                              WHERE q.topic_id = t.id OR q.topic_id LIKE t.id || '-%')
            ORDER BY t.exam_weight DESC
            LIMIT 5
        """).fetchall()
        for tid, name, subject, weight in rows:
            result["uncovered_weighty"].append({
                "id": tid, "name": name, "subject": subject, "weight": weight,
            })
    except Exception:
        pass

    conn.close()
    return result


# 知识图谱的考点 ID 是两段前缀 + 章节号（408-OS-02）；题库会把同一考点再拆成
# 子考点（408-OS-02-04 / 408-OS-02-05）。两边段数不同，直接拿题库 ID 去比对图谱
# 会全部落空——2026-09-14 前 45 条答题记录里有 42 条因此被判成「无数据」。
GRAPH_TOPIC_SEGMENTS = 3


def normalize_topic_id(tid) -> str:
    """把题库考点 ID 截到知识图谱粒度（前 3 段）。

    ENG-VOC-01-03 -> ENG-VOC-01
    408-OS-02-04  -> 408-OS-02
    408-DS-03     -> 408-DS-03   （已是图谱粒度，原样返回）
    """
    if not tid:
        return ""
    return "-".join(str(tid).split("-")[:GRAPH_TOPIC_SEGMENTS])


def load_topic_review_stats() -> dict:
    """按归一化考点 ID 聚合答题记录。

    返回 {topic_id: {"total": n, "correct": n, "recent_wrong": n}}，
    recent_wrong 只统计近 14 天 rating <= 2 的次数。
    """
    stats = {}
    if not DB_PATH.exists():
        return stats

    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute("""
            SELECT q.topic_id, rl.rating,
                   CASE WHEN rl.review_date >= datetime('now', 'localtime', '-14 days')
                        THEN 1 ELSE 0 END
            FROM review_log rl
            JOIN questions q ON q.id = rl.question_id
            WHERE q.topic_id IS NOT NULL
        """).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    for tid, rating, is_recent in rows:
        key = normalize_topic_id(tid)
        if not key:
            continue
        s = stats.setdefault(key, {"total": 0, "correct": 0, "recent_wrong": 0})
        s["total"] += 1
        if rating is not None and rating >= 3:
            s["correct"] += 1
        elif is_recent:
            s["recent_wrong"] += 1
    return stats


def load_topic_accuracy() -> dict:
    """topic_id → 正确率(0-100)，key 已归一化到知识图谱粒度。"""
    return {
        tid: round(s["correct"] / s["total"] * 100)
        for tid, s in load_topic_review_stats().items()
        if s["total"]
    }


def compute_recent_prefixes(timeline: dict, days: int = 7) -> list:
    """扫描近 N 天有修改的笔记目录，映射为闪卡知识点前缀（针对性选题用）。

    若近 N 天无文件修改（如刚重建索引），回退到时间线最近一天涉及的科目。
    """
    cutoff = time.time() - days * 86400
    found = set()
    for rel, prefix in NOTE_PREFIX_MAP.items():
        for f in note_files_for(rel):
            try:
                if f.stat().st_mtime >= cutoff:
                    found.add(prefix)
                    break
            except OSError:
                continue

    if not found and timeline:
        last_date = sorted(timeline.keys())[-1]
        for subj, cnt in timeline[last_date].items():
            if subj == "total" or not cnt:
                continue
            found.update(SUBJECT_ALL_PREFIXES.get(subj, []))

    return sorted(found)


def load_sync_state() -> dict:
    """Load the sync state."""
    if STATE_PATH.exists():
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------------------
# 笔记盘活（强化阶段核心）：新鲜度扫描 / 闪卡联动 / 练习活动
# ---------------------------------------------------------------------------

NOTE_TOUCH_PATH = BASE_DIR / "src" / "note_reviews.json"

FRESH_HOT = 7      # ≤7 天：热（活跃）
FRESH_WARM = 21    # ≤21 天：温（正常）
FRESH_COLD = 60    # ≤60 天：冷（需盘活）
# >60 天：冰冻（高危）


def load_note_touch() -> dict:
    """读取大盘内「读过并标记盘活」记录 {相对路径: ISO 时间}。"""
    if NOTE_TOUCH_PATH.exists():
        try:
            with open(NOTE_TOUCH_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("touched", {}) if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _parse_iso_ts(s):
    try:
        return datetime.fromisoformat(str(s)[:19]).timestamp()
    except Exception:
        return 0.0


def _chapter_weights(graphs: dict) -> dict:
    """构建 (前缀, 章节号) → 考点权重和 的映射，用于衡量笔记重要性。"""
    weights = {}
    for subj, graph in graphs.items():
        for sub_key, sub_data in graph.get("subs", {}).items():
            for topic in sub_data.get("topics", []):
                tid = topic.get("id", "")
                parts = tid.split("-")
                if len(parts) < 3:
                    continue
                pfx = "-".join(parts[:2])
                # 笔记是按「章」编号的，数学考点按张宇「讲」建，故取 note_chapter
                ch = topic_note_chapter(topic)
                if ch is None:
                    continue
                weights[(pfx, int(ch))] = weights.get((pfx, int(ch)), 0) + float(
                    topic.get("exam_weight", 1) or 1)
    return weights


def scan_note_freshness(graphs: dict, touched: dict) -> dict:
    """扫描各科笔记文件的闲置天数，分级并生成盘活目标清单。

    闲置天数 = 今天 - max(文件修改时间, 大盘内标记盘活时间)。
    分级：热(≤7) / 温(≤21) / 冷(≤60) / 冰冻(>60)。
    """
    ch_weights = _chapter_weights(graphs)
    by_prefix = {}
    all_files = []

    for rel, prefix in NOTE_PREFIX_MAP.items():
        files = note_files_for(rel)
        if not files:
            continue
        counts = {"hot": 0, "warm": 0, "cold": 0, "frozen": 0}
        for f in files:
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            relpath = f"{rel}/{f.name}".replace("\\", "/")
            last_active = max(mtime, _parse_iso_ts(touched.get(relpath, "")))
            days_idle = max(0, int((time.time() - last_active) // 86400))
            if days_idle <= FRESH_HOT:
                cat = "hot"
            elif days_idle <= FRESH_WARM:
                cat = "warm"
            elif days_idle <= FRESH_COLD:
                cat = "cold"
            else:
                cat = "frozen"
            counts[cat] += 1

            # 章节权重（文件名形如「第N章_xxx.md」）
            m = __import__("re").match(r"第(\d+)章", f.name)
            ch_w = ch_weights.get((prefix, int(m.group(1))), 0) if m else 0
            all_files.append({
                "prefix": prefix, "file": relpath, "name": f.stem,
                "days_idle": days_idle, "cat": cat, "weight": round(ch_w, 1),
            })
        total = sum(counts.values())
        if total > 0:
            by_prefix[prefix] = {
                "total": total,
                **counts,
                # 活跃分：热=1 温=0.6 冷=0.2 冰冻=0
                "alive_score": round(
                    (counts["hot"] + 0.6 * counts["warm"] + 0.2 * counts["cold"]) / total * 100),
            }

    summary = {"hot": 0, "warm": 0, "cold": 0, "frozen": 0, "total": len(all_files)}
    for f in all_files:
        summary[f["cat"]] += 1
    summary["alive_score"] = round(
        (summary["hot"] + 0.6 * summary["warm"] + 0.2 * summary["cold"])
        / summary["total"] * 100) if summary["total"] else 100

    # 盘活目标清单：冷/冰冻笔记，按 章节权重 × 闲置程度 排序
    import math
    targets = [f for f in all_files if f["cat"] in ("cold", "frozen")]
    for t in targets:
        idle_factor = min(t["days_idle"] / 60.0, 2.0)
        t["urgency"] = round((t["weight"] + 1) * (1 + math.log1p(idle_factor * 3)), 1)
    targets.sort(key=lambda x: x["urgency"], reverse=True)

    return {"summary": summary, "by_prefix": by_prefix, "targets": targets[:15]}


def load_card_linkage() -> dict:
    """笔记→闪卡联动：每个前缀的卡片数与近30天真实练习次数。"""
    linkage = {}
    if not DB_PATH.exists():
        return linkage
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute("""
            SELECT t.id,
                   COUNT(DISTINCT cd.id) AS cards,
                   COUNT(DISTINCT CASE WHEN rl.review_date >= datetime('now', 'localtime', '-30 days')
                                       THEN rl.id END) AS reviews_30d
            FROM topics t
            LEFT JOIN questions q  ON q.topic_id = t.id
            LEFT JOIN cards cd     ON cd.question_id = q.id
            LEFT JOIN review_log rl ON rl.question_id = q.id
            GROUP BY t.id
        """).fetchall()
        for tid, cards, reviews in rows:
            pfx = "-".join(tid.split("-")[:2])
            d = linkage.setdefault(pfx, {"cards": 0, "reviews_30d": 0})
            d["cards"] += cards or 0
            d["reviews_30d"] += reviews or 0
    except Exception:
        pass
    conn.close()
    return linkage


def load_review_activity() -> dict:
    """真实答题活动：近84天日历、连续练习天数、各科正确率。"""
    result = {"calendar": [], "streak": 0, "total_reviews": 0, "by_subject": []}
    if not DB_PATH.exists():
        return result
    conn = sqlite3.connect(str(DB_PATH))
    today = date.today()
    try:
        counts = {}
        for d, n in conn.execute("""
            SELECT date(review_date) AS d, COUNT(*) FROM review_log
            WHERE review_date >= datetime('now', 'localtime', '-84 days')
            GROUP BY date(review_date)
        """):
            counts[d] = n
        for i in range(83, -1, -1):
            ds = (today - timedelta(days=i)).isoformat()
            result["calendar"].append({"date": ds, "n": counts.get(ds, 0)})

        result["total_reviews"] = conn.execute(
            "SELECT COUNT(*) FROM review_log").fetchone()[0]

        # 连续练习天数（今天没练则从昨天起算）
        streak = 0
        offset = 0 if counts.get(today.isoformat(), 0) > 0 else 1
        while counts.get((today - timedelta(days=offset + streak)).isoformat(), 0) > 0:
            streak += 1
        result["streak"] = streak

        # 各科正确率（rating>=3 为对）。先聚合进 dict，再按固定顺序输出四科：
        # 没练过的科目补 0 行，而不是让它从列表里消失。
        agg = {}
        for subject, total, correct in conn.execute("""
            SELECT t.subject, COUNT(*),
                   SUM(CASE WHEN rl.rating >= 3 THEN 1 ELSE 0 END)
            FROM review_log rl
            JOIN questions q ON q.id = rl.question_id
            JOIN topics t ON t.id = q.topic_id
            WHERE t.subject IS NOT NULL
            GROUP BY t.subject
        """):
            if subject:
                agg[subject] = (total or 0, correct or 0)
        for subject in ALL_SUBJECTS:
            total, correct = agg.get(subject, (0, 0))
            result["by_subject"].append({
                "subject": subject,
                "total": total,
                "accuracy": round(correct / total * 100, 1) if total else 0.0,
            })
    except Exception:
        pass
    conn.close()
    return result


# ---------------------------------------------------------------------------
# Statistics computation
# ---------------------------------------------------------------------------

def compute_stats(index: dict, graphs: dict, db_stats: dict) -> dict:
    """Compute all dashboard statistics."""
    stats_data = index.get("stats", {})
    entries = index.get("entries", [])
    timeline = index.get("timeline", {})

    # --- Countdown ---
    today = date.today()
    days_left = (EXAM_DATE - today).days
    if days_left < 0:
        phase = "考试已结束"
    elif days_left <= 30:
        phase = "冲刺阶段"
    elif days_left <= 140:
        phase = "强化阶段"
    else:
        phase = "基础阶段"

    # --- Note totals ---
    total_notes = sum(s.get("total", 0) for s in stats_data.values())
    today_str = today.isoformat()
    today_new = 0
    if today_str in timeline:
        today_new = timeline[today_str].get("total", 0)

    # --- 笔记覆盖判定（topic 级）---
    # 优先用 graph 的 linked_notes；若为空则按「笔记条目前缀+章节号」粗匹配
    covered_topic_ids = set()
    for subj, graph in graphs.items():
        for sub_key, sub_data in graph.get("subs", {}).items():
            for topic in sub_data.get("topics", []):
                if topic.get("linked_notes"):
                    covered_topic_ids.add(topic.get("id", ""))

    if not covered_topic_ids:
        entry_chapters = {}   # "MATH-GS" -> {4, 5, ...}
        no_chapter_notes = {}   # 前缀 -> 无章节号（跨章汇总）的条目数
        for e in entries:
            pfx = note_entry_prefix(e.get("id", ""))
            if not pfx:
                continue
            m = __import__("re").search(r"\d+", str(e.get("chapter", "")))
            if m:
                entry_chapters.setdefault(pfx, set()).add(int(m.group()))
            else:
                # 「专题」这类无章节号条目跨章汇总，无法归属到某一格
                no_chapter_notes[pfx] = no_chapter_notes.get(pfx, 0) + 1

        graph_chapters = {}
        for subj, graph in graphs.items():
            for sub_key, sub_data in graph.get("subs", {}).items():
                for topic in sub_data.get("topics", []):
                    tid = topic.get("id", "")
                    pfx = "-".join(tid.split("-")[:2])
                    match_ch = topic_note_chapter(topic)
                    graph_chapters.setdefault(pfx, set()).add(match_ch)
                    chs = entry_chapters.get(pfx)
                    if chs and match_ch in chs:
                        covered_topic_ids.add(tid)

        # 笔记章节号超出图谱章节范围（如史纲第 9 章 vs 图谱只到第 7 章）会被
        # 静默丢掉——这正是「政治整片 0%」最难发现的一层，所以显式报出来。
        # 与「无章节号」分开报：前者是图谱缺内容（真问题），后者是跨章专题（正常）。
        out_of_range = {}
        for pfx, chs in entry_chapters.items():
            extra = sorted(chs - graph_chapters.get(pfx, set()))
            if extra:
                out_of_range[pfx] = extra
        if out_of_range:
            print("  WARN: 笔记章节号超出图谱范围（图谱缺章节）-> "
                  + ", ".join(f"{k} 第{v}章" for k, v in sorted(out_of_range.items())))
        if no_chapter_notes:
            print("  INFO: 无章节号笔记（跨章专题，不计入章节覆盖）-> "
                  + ", ".join(f"{k}={v} 处" for k, v in sorted(no_chapter_notes.items())))

    # --- 英语题型证据补覆盖（与 gap_analysis 共用 english_coverage，避免两边口径打架）---
    # 大盘原本只认「笔记索引里有没有条目」，但用户的作文批改、阅读专题讲义、完形讲义都躺在
    # English/ 目录下而没进笔记索引 → 小作文/大作文/翻译长期被误报成真缺口。
    # 反过来 Part B（七选五/小标题/排序）一份专项材料都没有，却曾因「外刊篇数」被判已覆盖。
    # 两边都读同一个模块，报告与大盘才不会互相矛盾。
    try:
        import english_coverage as _ec
        _pp = BASE_DIR / "src" / "progress.json"
        _prog = json.loads(_pp.read_text(encoding="utf-8")) if _pp.exists() else {}
        _cov, _eng_metrics, _eng_warns = _ec.english_type_coverage(
            str(BASE_DIR), _prog, _ec.english_review_counts(str(DB_PATH)))
        _added = [t for t, (ok, _w) in _cov.items() if ok and t not in covered_topic_ids]
        covered_topic_ids.update(_added)
        if _added:
            print(f"  英语题型证据补覆盖 {len(_added)} 个考点：" + "、".join(sorted(_added)))
        for _w in _eng_warns:
            print(f"  WARN(英语): {_w}")
    except Exception as _e:
        print(f"  WARN: 英语题型覆盖读不到（{_e}），缺口列表可能偏保守")

    # --- 闪卡验证状态：答对过且近 14 天无错 → 视为已掌握 ---
    # 用户可能因内容简单而不整理笔记，答对即证明掌握，不再提醒薄弱。
    # 这里同样走归一化，否则四段式考点的答题记录在验证环节也会被漏掉。
    topic_stats = load_topic_review_stats()
    verified_topic_ids = {
        tid for tid, s in topic_stats.items()
        if s["correct"] > 0 and s["recent_wrong"] == 0
    }

    # --- Coverage：笔记覆盖或已验证掌握都算覆盖 ---
    # Compute coverage from knowledge graphs: what fraction of topics have linked notes
    total_topics = 0
    covered_topics = 0
    coverage_by_subject = {}

    for subj, graph in graphs.items():
        subj_topics = 0
        subj_covered = 0
        for sub_key, sub_data in graph.get("subs", {}).items():
            for topic in sub_data.get("topics", []):
                subj_topics += 1
                tid = topic.get("id", "")
                if tid in covered_topic_ids or tid in verified_topic_ids:
                    subj_covered += 1
        total_topics += subj_topics
        covered_topics += subj_covered
        if subj_topics > 0:
            coverage_by_subject[subj] = round(subj_covered / subj_topics * 100, 1)
        else:
            coverage_by_subject[subj] = 0

    overall_coverage = round(covered_topics / total_topics * 100, 1) if total_topics > 0 else 0

    # --- Heatmap data: subject x chapter mastery ---
    # 掌握度口径（2026-09-14 重做）：
    #   有答题记录 → 真实正确率（唯一反映「练得怎么样」的信号）
    #   只有笔记   → 100，但打 note_only 标记，前端用低饱和色标「已整理·未练」
    #   两者都无   → 0
    # 旧口径把「有笔记」直接钉成 100，加上答题记录因 ID 段数不同被整片丢弃，
    # 结果绝大多数格子只剩 0/100 两档，热力图退化成一张清单。
    topic_acc = load_topic_accuracy()
    heatmap = []
    for subj, graph in graphs.items():
        for sub_key, sub_data in graph.get("subs", {}).items():
            sub_name = sub_data.get("name", sub_key)
            # Group topics by chapter
            chapters = {}
            for topic in sub_data.get("topics", []):
                ch = topic.get("chapter", 0)
                if ch not in chapters:
                    chapters[ch] = {"total": 0, "sum": 0, "covered": 0,
                                    "practiced": 0, "note_only": 0}
                chapters[ch]["total"] += 1
                tid = topic.get("id", "")
                if tid in topic_acc:
                    score = topic_acc[tid]
                    chapters[ch]["practiced"] += 1
                elif tid in covered_topic_ids or tid in verified_topic_ids:
                    score = 100
                    chapters[ch]["note_only"] += 1
                else:
                    score = 0
                chapters[ch]["sum"] += score
                if score > 0:
                    chapters[ch]["covered"] += 1

            for ch, counts in sorted(chapters.items()):
                pct = round(counts["sum"] / counts["total"]) if counts["total"] > 0 else 0
                heatmap.append({
                    "subject": subj,
                    "sub": sub_name,
                    "chapter": ch,
                    "coverage": int(pct),
                    "total": counts["total"],
                    "covered": counts["covered"],
                    "practiced": counts["practiced"],
                    "note_only": counts["note_only"],
                })

    # --- Timeline data（全量日粒度）---
    # 以前只发近 14 天，前端没得聚合，所以只能画日视图。现在把整条时间线交给
    # 前端，由它按日/周/月分桶——76 个点对页面体积可以忽略。
    timeline_data = []
    for d in sorted(timeline.keys()):
        t = timeline[d]
        timeline_data.append({
            "date": d,
            "total": t.get("total", 0),
            "408": t.get("408", 0),
            "数学": t.get("数学", 0),
            "政治": t.get("政治", 0),
            "英语": t.get("英语", 0),
        })

    # --- Level distribution ---
    level_dist = []
    for subj, s in stats_data.items():
        level_dist.append({
            "subject": subj,
            "L1": s.get("L1", 0),
            "L2": s.get("L2", 0),
            "L3": s.get("L3", 0),
        })

    # --- Gap analysis top 5 ---
    # 真缺口：无笔记且未通过闪卡验证的考点（已验证掌握的不再提醒）
    gaps = []
    for subj, graph in graphs.items():
        for sub_key, sub_data in graph.get("subs", {}).items():
            sub_name = sub_data.get("name", sub_key)
            for topic in sub_data.get("topics", []):
                tid = topic.get("id", "")
                if tid in covered_topic_ids or tid in verified_topic_ids:
                    continue
                gaps.append({
                    "id": tid,
                    "topic": topic.get("name", ""),
                    "subject": subj,
                    "sub": sub_name,
                    "weight": topic.get("exam_weight", 1),
                    "chapter": topic.get("chapter", 0),
                })

    # Sort by weight descending, take top 5
    gaps.sort(key=lambda x: x["weight"], reverse=True)
    top_gaps = []
    for i, g in enumerate(gaps[:5]):
        top_gaps.append({
            "rank": i + 1,
            "id": g["id"],
            "topic": g["topic"],
            "subject": g["subject"],
            "sub": g["sub"],
            "weight": g["weight"],
        })

    return {
        "countdown": {"days": days_left, "phase": phase, "exam_date": EXAM_DATE.isoformat()},
        "notes": {"total": total_notes, "today_new": today_new},
        "flashcard": {
            "due": db_stats["due_today"],
            "reviewed": db_stats["reviewed_today"],
            "total": db_stats["total_cards"],
        },
        "coverage": {"overall": overall_coverage, "by_subject": coverage_by_subject},
        "heatmap": heatmap,
        "timeline": timeline_data,
        "level_dist": level_dist,
        "card_states": db_stats["card_states"],
        "accuracy_trend": db_stats["accuracy_trend"],
        "top_gaps": top_gaps,
        "gap_count": len(gaps),
        "verified_count": len(verified_topic_ids),
        "weak_topics": load_weak_topics(),
        "deck_library": load_deck_library(),
        "recent_prefixes": compute_recent_prefixes(timeline),
        "revival": compute_revival(graphs),
        "sync": {
            "last_sync": load_sync_state().get("last_incremental", "N/A"),
        },
    }


def load_deck_library() -> list:
    """扫描 flashcards/decks/*.json（html-flashcard-builder --register 登记的卡组）。

    每个卡组注入：标题/副标题/storageKey/题型分布/构建时间，
    大盘「闪卡库」子页据此渲染画廊，点开用 iframe 覆盖层加载同名 html（embed=1 莫兰迪深色）。
    进度存 localStorage（按 storageKey），与独立打开的产物共享。
    """
    decks = []
    if not DECKS_DIR.is_dir():
        return decks
    for jf in sorted(DECKS_DIR.glob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            cards = data.get("cards", [])
            if not cards:
                continue
            meta = data.get("meta", {})
            types = {}
            chs = set()
            for c in cards:
                t = c.get("type", "short")
                types[t] = types.get(t, 0) + 1
                if c.get("ch"):
                    chs.add(c["ch"])
            html_file = jf.stem + ".html"
            decks.append({
                "id": jf.stem,
                "title": meta.get("title", jf.stem),
                "subtitle": meta.get("subtitle", ""),
                "storageKey": meta.get("storageKey", ""),
                "total": len(cards),
                "types": types,
                "chs": len(chs),
                "built": datetime.fromtimestamp(jf.stat().st_mtime).strftime("%m-%d %H:%M"),
                "src": "flashcards/decks/" + html_file,
                "hasHtml": (DECKS_DIR / html_file).exists(),
            })
        except Exception as e:
            print(f"[deck-library] skip {jf.name}: {e}")
    decks.sort(key=lambda d: d["built"], reverse=True)
    return decks


def compute_revival(graphs: dict) -> dict:
    """笔记盘活数据：新鲜度 + 闪卡联动 + 练习活动（强化阶段核心指标）。"""
    touched = load_note_touch()
    fresh = scan_note_freshness(graphs, touched)
    linkage = load_card_linkage()
    # 为盘活目标附上闪卡联动信息（有卡/近30天是否练过）
    for t in fresh["targets"]:
        lk = linkage.get(t["prefix"], {})
        t["cards"] = lk.get("cards", 0)
        t["practiced_30d"] = lk.get("reviews_30d", 0) > 0
    for pfx, info in fresh["by_prefix"].items():
        lk = linkage.get(pfx, {})
        info["cards"] = lk.get("cards", 0)
        info["reviews_30d"] = lk.get("reviews_30d", 0)
    return {
        "freshness": fresh,
        "activity": load_review_activity(),
        "thresholds": {"hot": FRESH_HOT, "warm": FRESH_WARM, "cold": FRESH_COLD},
    }


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 闪卡练习区（样式与交互脚本为普通字符串，避免 f-string 大括号转义）
# ---------------------------------------------------------------------------
FLASH_CSS = '''
        /* overflow-anchor：出答案时 #fs-feedback 会一次性插入大段解析，
           Chrome 的滚动锚定为了「保持可见内容不动」会自己改 scrollTop，
           表现出来就是整页跳一下。关掉锚定，改由 showFeedback 主动平滑滚动。 */
        .fs-box { min-height: 220px; overflow-anchor: none; }
        .fs-loading, .fs-empty { color: var(--text-secondary); text-align: center; padding: 48px 0; font-size: 0.9rem; }
        .fs-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
        .fs-progress { font-size: 0.85rem; color: var(--text-secondary); margin-right: auto; }
        .fs-badge { font-size: 0.7rem; padding: 2px 8px; border-radius: 10px; background: var(--bg-primary); color: var(--text-secondary); border: 1px solid var(--border-color); }
        .fs-badge.weak { background: rgba(var(--zhusha-rgb),.15); color: var(--zhusha-lt); border-color: rgba(var(--zhusha-rgb),.4); }
        .fs-badge.due { background: rgba(var(--xiang-rgb),.15); color: var(--xiang-lt); border-color: rgba(var(--xiang-rgb),.4); }
        .fs-badge.recent { background: rgba(var(--zhuqing-rgb),.15); color: var(--zhuqing-lt); border-color: rgba(var(--zhuqing-rgb),.4); }
        /* 本地卡组（早间回顾）：头部只有一个「出处」徽标，没有额度条 */
        .fs-badge.local { background: rgba(var(--dianqing-rgb),.15); color: var(--dianqing-lt); border-color: rgba(var(--dianqing-rgb),.4); }
        .fs-local-title { margin-left: auto; }
        /* 翻卡后的「原文」：正文是原样插入的 HTML（可能带 <p>/<ul>/<strong>），
           所以这里只给排版容器，不设字号覆盖（表格/列表要能正常显示）。 */
        .fs-local-back { border-left-color: var(--dianqing); }
        .fs-local-back p:first-child { margin-top: 0; }
        .fs-local-back .mr-concl { margin-top: 10px; }
        .fs-local-sec { font-size: 0.7rem; color: var(--text-muted); margin-bottom: 6px; }
        .fs-local-finish { margin-top: 14px; padding: 10px 13px; border-radius: var(--border-radius);
            font-size: 0.82rem; line-height: 1.8; color: var(--dianqing-lt);
            background: rgba(var(--dianqing-rgb),.1); }
        .fs-topic { font-size: 0.75rem; color: var(--text-muted); }
        .fs-topic-btn { font: inherit; font-size: 0.75rem; cursor: pointer; padding: 0 2px; background: none;
                        border: none; border-bottom: 1px dashed var(--border-color); color: var(--text-secondary); }
        .fs-topic-btn:hover { color: var(--dianqing-lt); border-bottom-color: var(--dianqing); }
        .fs-stem { font-size: 1.05rem; font-weight: 600; margin: 10px 0 16px; line-height: 1.7; }
        .fs-opt { display: block; width: 100%; text-align: left; background: var(--bg-primary); color: var(--text-primary); border: 1px solid var(--border-color); border-radius: 6px; padding: 10px 14px; margin-bottom: 8px; font-size: 0.9rem; cursor: pointer; transition: border-color .15s; }
        .fs-opt:hover { border-color: var(--accent-blue); }
        .fs-opt.correct { border-color: var(--accent-green); background: rgba(16,185,129,.12); }
        .fs-opt.wrong { border-color: var(--accent-red); background: rgba(239,68,68,.12); }
        .fs-opt:disabled { cursor: default; }
        .fs-explain { margin-top: 14px; padding: 12px 14px; background: var(--bg-primary); border-left: 3px solid var(--accent-blue); border-radius: 4px; font-size: 0.88rem; color: var(--text-secondary); line-height: 1.7; }
        .fs-traps { margin-top: 10px; font-size: 0.82rem; color: var(--accent-orange); }
        .fs-actions { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
        .fs-btn { border: 1px solid var(--border-color); border-radius: 6px; padding: 8px 18px; font-size: 0.85rem; cursor: pointer; background: var(--bg-primary); color: var(--text-primary); }
        .fs-btn:hover { border-color: var(--accent-blue); }
        .fs-rate1 { border-color: var(--zhusha); color: var(--zhusha-lt); }
        .fs-rate2 { border-color: var(--xiang); color: var(--xiang-lt); }
        .fs-rate3 { border-color: var(--zhuqing); color: var(--zhuqing-lt); }
        .fs-rate4 { border-color: var(--dianqing); color: var(--dianqing-lt); }
        .fs-hint { margin-top: 10px; font-size: 0.72rem; color: var(--text-muted); }
        .fs-summary { text-align: center; padding: 30px 0; }
        .fs-summary h3 { margin-bottom: 12px; }
        .fs-summary p { color: var(--text-secondary); font-size: 0.9rem; margin-bottom: 16px; }
        /* --- 「开始」闸门 + 学习计时（2026-09-21）--- */
        .fs-gate { text-align: center; padding: 44px 12px 40px; }
        .fs-gate-icon { font-size: 2rem; opacity: .55; margin-bottom: 6px; }
        .fs-gate-title { font-family: var(--font-serif); font-size: 1.2rem; color: var(--text-primary);
            margin-bottom: 10px; }
        .fs-gate-line { font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 22px;
            line-height: 1.9; }
        .fs-gate-go { border-color: var(--zhuqing); color: var(--zhuqing-lt);
            font-size: 1rem; padding: 12px 40px; letter-spacing: .05em; }
        .fs-gate-go:hover { border-color: var(--zhuqing-lt); background: rgba(var(--zhuqing-rgb),.12); }
        .fs-gate-alt { display: block; margin: 14px auto 0; font-size: 0.78rem; padding: 5px 14px;
            opacity: .7; }
        .fs-gate-alt:hover { opacity: 1; }
        .fs-gate-hint { margin-top: 20px; font-size: 0.72rem; color: var(--text-muted); }
        .fs-gate-back { margin-top: 18px; }
        .fs-summary-time { font-size: 0.82rem !important; color: var(--text-secondary); }
        .fs-summary-time b { color: var(--zhuqing-lt); font-variant-numeric: tabular-nums; }
        .fs-muted { color: var(--text-muted); font-size: 0.85em; }
        /* 计时徽标：小圆点是状态的第二信道——扫一眼就知道表在不在走 */
        .fs-timer { display: inline-flex; align-items: center; gap: 6px; font-size: 0.78rem;
            color: var(--text-secondary); border: 1px solid var(--border-color); border-radius: 12px;
            padding: 2px 11px; white-space: nowrap; font-variant-numeric: tabular-nums; }
        .fs-timer b { color: var(--text-primary); font-weight: 600; }
        .fs-timer-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--text-muted); flex: none; }
        .fs-timer.run .fs-timer-dot { background: var(--zhuqing); animation: pmBeat 1.6s ease-in-out infinite; }
        .fs-timer.pause { color: var(--xiang-lt); border-color: rgba(var(--xiang-rgb),.45); }
        .fs-timer.pause .fs-timer-dot { background: var(--xiang); }
        .fs-timer.off { opacity: .72; }
        .fs-timer-tag { font-size: 0.68rem; color: var(--xiang-lt); }
        .fs-timer-day { font-size: 0.68rem; color: var(--text-muted); margin-left: 2px; }
        .fs-timer-idle { font-size: 0.74rem; }
        @keyframes pmBeat { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: .35; transform: scale(.82); } }
        @media (prefers-reduced-motion: reduce) {
            .fs-timer.run .fs-timer-dot { animation: none; }
        }
        /* --- Anki 化补充（2026-09-13）--- */
        .fs-badge.leech { background: rgba(var(--zi-rgb),.18); color: var(--zi-lt); border-color: rgba(var(--zi-rgb),.5); }
        .fs-badge.learn { background: rgba(var(--dianqing-rgb),.15); color: var(--dianqing-lt); border-color: rgba(var(--dianqing-rgb),.4); }
        .fs-limits { font-size: 0.7rem; color: var(--text-muted); }
        .fs-rate { display: flex; flex-direction: column; align-items: center; gap: 2px; min-width: 76px; }
        .fs-rate-label { font-size: 0.85rem; }
        .fs-rate-pv { font-size: 0.68rem; opacity: .8; }
        .fs-undo-row { margin-top: 10px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
        .fs-undo { font-size: 0.75rem; padding: 5px 12px; opacity: .8; }
        /* 删除卡（软删除）：低调但清晰，两步确认防误触 */
        .fs-del { font-size: 0.72rem; padding: 5px 12px; opacity: .55; margin-left: auto;
            border-color: var(--zhusha); color: var(--zhusha-lt); }
        .fs-del:hover { opacity: 1; background: rgba(var(--zhusha-rgb), 0.12); }
        .fs-del-confirm { font-size: 0.72rem; padding: 5px 12px;
            background: var(--zhusha); color: #fff; border-color: var(--zhusha); }
        .fs-del-confirm:hover { background: var(--zhusha-lt); }
        /* --- 「这道题有问题」标记（2026-09-17）---
           练习时一键标记题目本身错了（多个选项都对/答案有误…），每日任务里由 AI 核对修复。
           配色统一走告警橘（--accent-orange），和「删卡」的朱砂、水蛭卡的紫刻意分开——
           这是在说「别信这题」，不是在说「这题我不会」。 */
        .fs-flag { font-size: 0.72rem; padding: 5px 12px; opacity: .7;
            border-color: var(--accent-orange); color: var(--accent-orange); }
        .fs-flag:hover { opacity: 1; background: rgba(245,158,11,.12); }
        .fs-flag.on { opacity: 1; border-color: var(--zhusha); color: var(--zhusha-lt);
            background: rgba(var(--zhusha-rgb),.10); }
        .fs-report-badge { font-size: 0.7rem; padding: 2px 8px; border-radius: 10px;
            background: rgba(245,158,11,.15); color: var(--accent-orange);
            border: 1px solid rgba(245,158,11,.45); }
        .fs-report-note { margin: 0 0 12px; padding: 8px 12px; font-size: 0.78rem;
            border-left: 3px solid var(--accent-orange); border-radius: 4px;
            background: rgba(245,158,11,.08); color: var(--text-secondary); line-height: 1.6; }
        .fs-flag-panel { margin-top: 10px; padding: 10px 12px; border-radius: 6px;
            background: var(--bg-primary); border: 1px solid var(--border-color); }
        .fs-flag-panel:empty { display: none; }
        .fs-flag-title { font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 8px; }
        .fs-flag-kinds { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
        .fs-flag-kind { font-size: 0.75rem; padding: 4px 10px; border-radius: 12px; cursor: pointer;
            background: none; color: var(--text-secondary); border: 1px solid var(--border-color); }
        .fs-flag-kind:hover { border-color: var(--accent-orange); }
        .fs-flag-kind[aria-pressed="true"] { border-color: var(--accent-orange);
            color: var(--accent-orange); background: rgba(245,158,11,.12); }
        .fs-flag-note { width: 100%; box-sizing: border-box; min-height: 46px; resize: vertical;
            font: inherit; font-size: 0.8rem; padding: 6px 8px; border-radius: 6px;
            background: var(--bg-secondary); color: var(--text-primary);
            border: 1px solid var(--border-color); }
        .fs-flag-hint { font-size: 0.72rem; color: var(--text-muted); margin-top: 6px; line-height: 1.55; }
        .fs-flag-actions { display: flex; gap: 8px; align-items: center; margin-top: 8px; }
        .fs-report-list { border-top: 1px dashed var(--border-color); margin-top: 4px; padding-top: 6px; }
        .fs-report-item { font-size: 0.76rem; color: var(--text-secondary); padding: 3px 0;
            line-height: 1.6; }
        .fs-report-item b { color: var(--accent-orange); }
        .fs-report-item .fs-report-stem { color: var(--text-muted); }
        /* --- 📌 钉住（2026-09-19 用户要求）---
           「就算我对的那些题，如果我对 AI 有过追问，那可以给我一个 pin 的键，我可以把它钉在
            那个卡的位置，下次我看到它的时候，我可以再看看它。」
           与 ⚑（题目有问题，橘）和 🗑（删卡，朱砂）刻意用不同的颜色：金色 = 「留着它」，
           不是告警也不是否定。 */
        .fs-pin { font-size: 0.72rem; padding: 5px 12px; opacity: .7;
            border-color: var(--accent-gold, #c8a24a); color: var(--accent-gold, #c8a24a); }
        .fs-pin:hover { opacity: 1; background: rgba(200,162,74,.12); }
        .fs-pin.on { opacity: 1; background: rgba(200,162,74,.16); }
        .fs-pin-badge { font-size: 0.7rem; padding: 2px 8px; border-radius: 10px;
            background: rgba(200,162,74,.15); color: var(--accent-gold, #c8a24a);
            border: 1px solid rgba(200,162,74,.45); }
        .fs-ask-badge { font-size: 0.7rem; padding: 2px 8px; border-radius: 10px;
            background: rgba(127,168,217,.15); color: var(--accent-blue-lt, #7fa8d9);
            border: 1px solid rgba(127,168,217,.4); }
        .fs-policy-hint { font-size: 0.72rem; color: var(--text-muted); margin: 0 0 10px;
            line-height: 1.55; }
        .fs-pin-note { margin: 0 0 12px; padding: 8px 12px; font-size: 0.78rem;
            border-left: 3px solid var(--accent-gold, #c8a24a); border-radius: 4px;
            background: rgba(200,162,74,.08); color: var(--text-secondary); line-height: 1.6; }
        .fs-toast { position: fixed; left: 50%; bottom: 32px; transform: translateX(-50%); background: rgba(var(--mo-rgb),.96); color: var(--xuan); border: 1px solid var(--border-color); border-radius: var(--border-radius); padding: 8px 18px; font-size: 0.82rem; z-index: 9999; }
        .fs-stats-panel { margin-bottom: 12px; }
        .fs-stats-panel:empty { display: none; }
        .fs-stat-row { display: flex; gap: 16px; flex-wrap: wrap; font-size: 0.78rem; color: var(--text-secondary); padding: 8px 0; }
        .fs-stat-row b { color: var(--text-primary); }
        .fs-maturity { border-top: 1px dashed var(--border-color); }
        .fs-forecast-label { font-size: 0.72rem; color: var(--text-muted); margin: 6px 0 4px; }
        .fs-forecast { display: flex; align-items: flex-end; gap: 2px; height: 56px; padding: 4px 0; border-bottom: 1px solid var(--border-color); }
        .fs-fbar { flex: 1; height: 100%; display: flex; align-items: flex-end; }
        .fs-fbar-fill { width: 100%; background: var(--accent-blue); border-radius: 2px 2px 0 0; min-height: 1px; opacity: .75; }
        /* --- 键盘交互（2026-09-13）：选项键位徽章 + 选错后的翻页按钮 --- */
        .fs-key { display: inline-block; min-width: 1.5em; margin-right: 9px; padding: 0 5px; border: 1px solid var(--border-color); border-radius: 3px; font-size: 0.72rem; line-height: 1.6; text-align: center; color: var(--text-muted); }
        .fs-opt:hover:not(:disabled) .fs-key { color: var(--text-primary); border-color: var(--accent-blue); }
        .fs-opt.correct .fs-key, .fs-opt.wrong .fs-key { color: inherit; border-color: currentColor; }
        .fs-next { border-color: var(--zhuqing); color: var(--zhuqing-lt); padding: 8px 22px; }
        .fs-next:hover { border-color: var(--zhuqing-lt); }
        /* --- 按需 KaTeX（2026-09-13）：题面/选项/解析里的公式 --- */
        .tex-host { display: inline; }
        .fs-stem .katex, .fs-opt .katex, .fs-explain .katex, .fs-traps .katex,
        .deck-title .katex, .deck-sub .katex { font-size: 1.02em; }
        /* 独立行公式（$$...$$）可能超宽，给横向滚动而不是撑破卡片 */
        .fs-stem .katex-display, .fs-explain .katex-display { margin: 8px 0; overflow-x: auto; overflow-y: hidden; }
        /* KaTeX 未就绪时的兜底：以等宽字体原样显示 TeX 源码，加载完成后自动替换 */
        .tex-fallback { font-family: Consolas, "Courier New", monospace; font-size: 0.86em; opacity: .85; }
        /* --- 简答题：文字 + 手写照片批改（2026-09-13）--- */
        .fs-textarea { width: 100%; box-sizing: border-box; min-height: 92px; resize: vertical;
            padding: 10px 12px; background: var(--bg-primary); color: var(--text-primary);
            border: 1px solid var(--border-color); border-radius: 6px;
            font-family: inherit; font-size: 0.9rem; line-height: 1.65; }
        .fs-textarea:focus { outline: none; border-color: var(--accent-blue); }
        .fs-textarea:disabled { opacity: .7; }
        .fs-imgrow { display: flex; align-items: center; gap: 10px; margin-top: 8px; flex-wrap: wrap; }
        .fs-imgrow .fs-hint-img { font-size: 0.72rem; color: var(--text-muted); }
        .fs-imgbox { margin-top: 8px; }
        .fs-imgbox img { max-width: 280px; max-height: 210px; border: 1px solid var(--border-color);
            border-radius: 6px; display: block; }
        .fs-imgbox .fs-img-meta { display: flex; align-items: center; gap: 8px; margin-top: 5px;
            font-size: 0.72rem; color: var(--text-muted); }
        .fs-short-drop { margin-top: 8px; padding: 12px; border: 1px dashed var(--border-color);
            border-radius: 6px; text-align: center; font-size: 0.8rem; color: var(--text-muted); }
        .fs-short-drop.on { border-color: var(--accent-blue); color: var(--accent-blue); }
        .fs-grade { margin-top: 12px; padding: 12px 14px; border-radius: 6px; background: var(--bg-primary);
            border-left: 3px solid var(--accent-blue); font-size: 0.86rem; line-height: 1.7;
            color: var(--text-secondary); }
        .fs-grade-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; flex-wrap: wrap; }
        .fs-score { font-size: 1.12rem; font-weight: 700; color: var(--text-primary); }
        .fs-verdict { padding: 1px 8px; border-radius: 10px; font-size: 0.74rem; border: 1px solid currentColor; }
        .fs-verdict.ok { color: #6FCF97; } .fs-verdict.mid { color: var(--accent-orange); }
        .fs-verdict.bad { color: #EB5757; }
        .fs-grade ul { margin: 4px 0 6px 18px; padding: 0; }
        .fs-grade li { margin: 2px 0; }
        .fs-trans { margin-top: 6px; padding: 8px 10px; background: rgba(255,255,255,.03);
            border-radius: 4px; font-size: 0.82rem; white-space: pre-wrap; }
        .fs-btn { position: relative; }
        .fs-rate-ai { position: absolute; top: -9px; right: -6px; background: var(--accent-blue);
            color: #fff; font-size: 0.58rem; line-height: 1.5; padding: 0 4px; border-radius: 6px; }
        .fs-btn.primary { border-color: var(--accent-blue); color: var(--accent-blue); }
        .fs-short-err { margin-top: 8px; font-size: 0.8rem; color: var(--accent-orange); }

        /* 手写板（2026-09-20）：覆盖层 + 白底画布，给平板/触屏用笔书写 */
        .fs-ink-pad { position: fixed; inset: 0; z-index: 950; background: rgba(0,0,0,.55);
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            padding: 16px; animation: fsFullIn .18s ease; }
        .fs-ink-paper { background: #fff; border-radius: 8px; box-shadow: 0 8px 40px rgba(0,0,0,.4);
            max-width: 900px; width: 100%; max-height: 85vh; display: flex; flex-direction: column; }
        .fs-ink-bar { display: flex; gap: 8px; align-items: center; padding: 8px 12px;
            border-bottom: 1px solid #ddd; background: #f8f8f8; border-radius: 8px 8px 0 0; flex-wrap: wrap; }
        .fs-ink-bar .fs-ink-title { font-size: 0.8rem; color: #555; margin-right: auto; font-weight: 600; }
        .fs-ink-btn { font: inherit; font-size: 0.78rem; padding: 5px 14px; border: 1px solid #ccc;
            background: #fff; border-radius: 5px; cursor: pointer; color: #333; }
        .fs-ink-btn:hover { background: #f0f0f0; }
        .fs-ink-btn.active { background: var(--zhuqing); color: #fff; border-color: var(--zhuqing); }
        .fs-ink-btn.danger { color: var(--zhusha); border-color: var(--zhusha-lt); }
        .fs-ink-btn.primary { background: var(--zhuqing); color: #fff; border-color: var(--zhuqing); }
        .fs-ink-canvas-wrap { flex: 1; overflow: hidden; position: relative; background: #fff; min-height: 320px; }
        .fs-ink-canvas { display: block; width: 100%; height: 100%; touch-action: none; cursor: crosshair; }
        .fs-ink-hint { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
            color: #ccc; font-size: 1.1rem; pointer-events: none; user-select: none; }
        /* --- 闪卡筛选页（2026-09-13）--- */
        .ff-group { display: flex; align-items: baseline; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
        .ff-label { flex: none; width: 42px; font-size: 0.78rem; color: var(--text-muted); letter-spacing: .08em; }
        .ff-chips { display: flex; gap: 8px; flex-wrap: wrap; }
        .ff-chip { font: inherit; font-size: 0.82rem; cursor: pointer; padding: 5px 12px; border-radius: 14px;
                   background: var(--bg-primary); color: var(--text-secondary); border: 1px solid var(--border-color);
                   display: inline-flex; align-items: center; gap: 7px; transition: all .14s; }
        .ff-chip:hover:not(:disabled) { border-color: var(--dianqing); color: var(--text-primary); }
        .ff-chip.on { border-color: var(--zhusha); color: var(--xuan); background: rgba(var(--zhusha-rgb),.14); }
        .ff-chip.empty { opacity: .38; cursor: not-allowed; }
        .ff-chip .ff-n { font-size: 0.72rem; color: var(--text-muted); }
        .ff-chip.on .ff-n { color: var(--zhusha-lt); }
        .ff-foot { display: flex; align-items: center; gap: 12px; margin-top: 14px; padding-top: 12px;
                   border-top: var(--rule); flex-wrap: wrap; }
        .ff-summary { font-size: 0.8rem; color: var(--text-secondary); margin-right: auto; }
        .ff-summary b { color: var(--xuan); }
        /* --- 闪卡范围筛选：练习区下方的收起条（2026-09-18）--- */
        .fl-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
        .fl-label { font-size: 0.82rem; color: var(--text-secondary); letter-spacing: .04em; }
        .fl-scope { font-size: 0.84rem; color: var(--text-secondary); margin-right: auto; min-width: 0;
                    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .fl-scope b { color: var(--xuan); }
        /* 浮窗里知识点：直接换行铺开，不做横向滚动（用户要求）；要滚也是整个
           浮窗上下滚（.sk-body 自带 overflow-y:auto） */
        .ff-topics .ff-chips { flex-wrap: wrap; }
        .ff-topics .ff-chip { flex: none; }
        .ff-nothing { font-size: 0.76rem; color: var(--text-muted); padding: 6px 2px; }

        /* --- 全屏练习（2026-09-14）---
           练习区脱离文档流铺满视口、自己滚动。这样出答案时新增的解析只在
           内部撑开，不会再顶动整页——这正是「学习时界面来回动」的根源。 */
        /* 工具组允许换行：多了「学习计时 + 结束」两项后，窄屏（平板竖屏）不换行
           就会把右边的按钮挤出卡片边界。 */
        .fs-tools { display: flex; gap: 8px; flex: none; flex-wrap: wrap; justify-content: flex-end; }
        .fs-full-toggle { font: inherit; font-size: 0.75rem; flex: none; cursor: pointer;
            background: transparent; border: 1px solid var(--border-color); border-radius: 4px;
            color: var(--text-secondary); padding: 3px 12px; transition: all .15s; }
        .fs-full-toggle:hover { color: var(--text-primary); border-color: var(--dianqing);
                                background: var(--bg-secondary); }
        /* 静音态：整颗按钮压暗，一眼能看出音效是关着的 */
        .fs-full-toggle.is-off { opacity: .5; }
        .fs-full-toggle.is-off:hover { opacity: .8; }
        .section.is-full {
            position: fixed; inset: 0; z-index: 900; margin: 0; border: none; border-radius: 0;
            background: var(--bg-primary); overflow-y: auto; overscroll-behavior: contain;
            /* 顶部留一点呼吸空间，贴顶会显得很挤（2026-09-20） */
            padding: 36px clamp(16px, 7vw, 96px) 56px;
            animation: fsFullIn .18s ease;
        }
        @keyframes fsFullIn {
            from { opacity: 0; transform: scale(.995); }
            to   { opacity: 1; transform: none; }
        }
        /* 全屏后行宽会拉得很长，反而难读，给内容一个阅读宽度上限 */
        .section.is-full .fs-box,
        .section.is-full .chart-head { max-width: 900px; margin-left: auto; margin-right: auto; }
        .section.is-full .chart-head { margin-bottom: 16px; }
        body.fs-lock { overflow: hidden; }

        /* --- AI 针对性解析 + 追问（2026-09-14）--- */
        /* :empty 用于「答对/看答案」时不显示这个块——只有答错才会挂载内容 */
        .fs-exp-wrap:empty { display: none; }
        .fs-exp-wrap { margin-top: 14px; border: 1px solid var(--border-color);
            border-left: 3px solid var(--zhuqing); border-radius: 4px;
            background: var(--bg-primary); overflow: hidden; }
        .fs-exp-head { display: flex; align-items: center; gap: 10px; padding: 9px 14px;
            border-bottom: 1px solid var(--border-color); font-size: 0.8rem;
            color: var(--zhuqing-lt); }
        .fs-exp-retry { margin-left: auto; font: inherit; font-size: 0.72rem; cursor: pointer;
            background: none; border: 1px solid var(--border-color); border-radius: 4px;
            color: var(--text-secondary); padding: 2px 10px; }
        .fs-exp-retry:hover { color: var(--text-primary); border-color: var(--dianqing); }
        .fs-exp-body { padding: 10px 14px; font-size: 0.88rem; line-height: 1.75;
            color: var(--text-secondary); max-height: 420px; overflow-y: auto; }
        /* 全屏时给解析更多可视高度，别让追问框被挤出视野 */
        .section.is-full .fs-exp-body { max-height: 46vh; }
        .fs-exp-turn + .fs-exp-turn { margin-top: 10px; padding-top: 10px;
            border-top: 1px dashed var(--border-color); }
        .fs-exp-mine { color: var(--text-secondary); font-size: 0.82rem; }
        .fs-exp-mine::before { content: "我："; color: var(--text-muted); }
        .fs-exp-loading { color: var(--text-muted); font-size: 0.82rem; }
        .fs-exp-err { color: var(--accent-orange); font-size: 0.82rem; line-height: 1.7; }
        .fs-exp-hint { color: var(--text-muted); font-size: 0.75rem; }
        .fs-exp-ask { display: flex; gap: 8px; padding: 10px 14px;
            border-top: 1px solid var(--border-color); }
        .fs-exp-input { flex: 1; min-width: 0; box-sizing: border-box; font-family: inherit;
            font-size: 0.84rem; padding: 7px 10px; background: var(--bg-secondary);
            color: var(--text-primary); border: 1px solid var(--border-color); border-radius: 6px; }
        .fs-exp-input:focus { outline: none; border-color: var(--dianqing); }
        .fs-exp-send { flex: none; padding: 7px 16px; font-size: 0.82rem; }
        /* Markdown 子集（mdTex 输出），只覆盖 AI 实际会用的那几种 */
        .fs-exp-body .md-p { margin: 0 0 8px; }
        .fs-exp-body .md-p:last-child { margin-bottom: 0; }
        .fs-exp-body .md-h { margin: 12px 0 6px; font-size: 0.92rem; color: var(--text-primary); }
        .fs-exp-body .md-ul { margin: 4px 0 8px 18px; padding: 0; }
        .fs-exp-body .md-ul li { margin: 2px 0; }
        .fs-exp-body .md-pre { margin: 8px 0; padding: 9px 11px; background: var(--bg-secondary);
            border-radius: 4px; overflow-x: auto; font-size: 0.8rem; line-height: 1.6;
            font-family: Consolas, "Courier New", monospace; }
        .fs-exp-body .md-code { padding: 1px 5px; background: var(--bg-secondary);
            border-radius: 3px; font-family: Consolas, "Courier New", monospace; font-size: 0.85em; }
        .fs-exp-body .md-quote { margin: 6px 0; padding: 4px 10px;
            border-left: 2px solid var(--border-color); color: var(--text-muted); }
        .fs-exp-body .md-hr { margin: 10px 0; border: 0; border-top: 1px solid var(--border-color); }
        .fs-exp-body .md-tex { margin: 8px 0; overflow-x: auto; overflow-y: hidden; }
        /* 复用上次解析时的说明条（2026-09-21）：让人知道这是旧结果，并能一键重生成 */
        .fs-exp-reuse { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
            margin: 0 0 10px; padding: 7px 11px; border-radius: 6px; font-size: 0.74rem;
            color: var(--text-secondary); background: rgba(var(--xiang-rgb), .10);
            border: 1px dashed rgba(var(--xiang-rgb), .45); }
        .fs-exp-reuse span { flex: 1; min-width: 0; }
        .fs-exp-reuse button { font: inherit; font-size: 0.72rem; cursor: pointer;
            padding: 3px 10px; border-radius: 6px; background: var(--bg-primary);
            color: var(--text-secondary); border: 1px solid var(--border-color); }
        .fs-exp-reuse button:hover { color: var(--text-primary); border-color: var(--xiang); }
        /* 卡片正文（题干/选项/题库自带解析）现在也认行内 Markdown 了：
           <code> 若不给样式就是浏览器默认等宽裸字，跟解析区观感不一致 */
        .fs-stem .md-code, .fs-opt .md-code, .fs-explain .md-code, .fs-traps .md-code {
            padding: 1px 5px; background: var(--bg-secondary); border-radius: 3px;
            font-family: Consolas, "Courier New", monospace; font-size: 0.9em; }
        .fs-stem strong, .fs-opt strong, .fs-explain strong { color: var(--text-primary); }
        /* 表格：AI 很爱用 | a | b | 讲对比，不渲染就成了一堆竖线（2026-09-20 补） */
        .fs-exp-body .md-table { width: 100%; border-collapse: collapse; margin: 8px 0;
            font-size: 0.82rem; line-height: 1.6; display: block; overflow-x: auto; }
        .fs-exp-body .md-table th,
        .fs-exp-body .md-table td { border: 1px solid var(--border-color); padding: 5px 10px;
            text-align: left; vertical-align: top; }
        .fs-exp-body .md-table th { background: var(--bg-secondary); color: var(--text-primary);
            font-weight: 600; white-space: nowrap; }
        .fs-exp-body strong { color: var(--text-primary); }

        /* --- 闪卡浮窗（2026-09-22）---
           「别的页也能直接刷闪卡」：练习区**整个节点**被搬进这一层浮窗（同番茄钟的
           节点搬家，不是复制一套 UI），所以全屏、音效、学习计时、键盘、评分、
           AI 解析、进度同步全部照旧，一行都不用重写（见 FLASH_JS 的浮窗段）。
           z-index 860：高于正文与侧边栏，低于右上角整页全屏(880)与番茄钟(1200)。
           ⚠️ 这一层**绝不能加 transform / filter / backdrop-filter / contain**：
              那会给内部的 .section.is-full（position:fixed）换一个包含块，
              全屏就从「铺满视口」缩成「铺满浮窗」，功能直接残掉。
           位置与尺寸由 FLASH_JS 的 applyFloatGeo() 设内联样式（带视口钳制）。 */
        .fs-float { position: fixed; left: 0; top: 0; z-index: 860; display: flex;
            flex-direction: column; width: 720px; height: 560px;
            min-width: 320px; min-height: 240px; overflow: hidden; resize: both;
            background: var(--bg-primary); border: 1px solid var(--border-color);
            border-radius: var(--border-radius); box-shadow: 0 14px 48px rgba(0,0,0,.45); }
        .fs-float[hidden] { display: none; }
        .fs-float-bar { flex: none; display: flex; align-items: center; gap: 8px;
            padding: 6px 10px; background: var(--bg-secondary);
            border-bottom: 1px solid var(--border-color);
            cursor: move; touch-action: none; user-select: none; }
        .fs-float-title { font-size: 0.8rem; font-weight: 600; color: var(--text-primary);
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .fs-float-tag { flex: 1; min-width: 0; font-size: 0.7rem; color: var(--text-muted);
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .fs-float-btn { flex: none; font: inherit; font-size: 0.75rem; cursor: pointer;
            padding: 2px 10px; border-radius: 4px; color: var(--text-secondary);
            background: transparent; border: 1px solid var(--border-color); transition: all .15s; }
        .fs-float-btn:hover { color: var(--text-primary); border-color: var(--dianqing);
            background: var(--bg-primary); }
        .fs-float-body { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; }
        /* 搬进来的练习区：在窗口里就贴着排，不再重复一层卡片边框与内边距。
           ⚠️ 必须排除 .is-full——两者同特异度，写在后面会把全屏那层的背景刷成透明，
              于是全屏只剩一个能看见底下页面的空壳。 */
        .fs-float-body > .section:not(.is-full) { margin: 0; border: none;
            border-radius: 0; background: transparent; box-shadow: none;
            padding: 12px 14px 18px; }
        .fs-float-body .chart-head { margin-bottom: 10px; }
        /* 小屏（平板竖屏）：浮窗默认就铺到快满，别让人开了却看不见题目。
           用 !important 压过 JS 写的内联尺寸——窄屏上「记住上次位置」没有意义。 */
        @media (max-width: 820px) {
            .fs-float { width: calc(100vw - 16px) !important;
                        height: calc(100vh - 96px) !important; left: 8px !important; }
        }
'''

FLASH_JS = '''
// ============================================================
// 全局按需 KaTeX：内容里的 $...$（行内）与 $$...$$（独立行）渲染成公式，其余原样输出
// 供闪卡练习区、闪卡库、笔记预览等所有展示题面/解析的地方共用。
//   1. 先按公式切段再转义——文本段走 escHtml()，公式段走 KaTeX。绝不能先整串转义，
//      否则 \\frac 的反斜杠、a<b 的尖括号会先变成实体，KaTeX 收到的是坏源码。
//   2. 真正按需：只有文本里出现 $ 才触发 KaTeX 懒加载（本地 tools/katex/，不内联，
//      断网可用），页面本身不加载这 3MB。
//   3. 未就绪时先渲染成兜底样式，并把原始文本登记进 texStore；加载完成后 flushMath()
//      只重写这些元素的 innerHTML，不重画整张卡，避免丢掉作答状态。
// ============================================================
(function() {
    let katexPromise = null;

    function escHtml(s) {
        const d = document.createElement("div");
        d.textContent = s == null ? "" : String(s);
        return d.innerHTML;
    }

    function ensureKatex() {
        if (katexPromise) return katexPromise;
        katexPromise = new Promise(resolve => {
            if (window.katex) { resolve(true); return; }
            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.href = "tools/katex/dist/katex.min.css";
            document.head.appendChild(link);
            const s = document.createElement("script");
            s.src = "tools/katex/dist/katex.min.js";
            s.onload = () => { flushMath(); resolve(true); };
            s.onerror = () => resolve(false);
            document.head.appendChild(s);
        });
        return katexPromise;
    }

    function katexHtml(tex, display) {
        if (window.katex) {
            try {
                return window.katex.renderToString(tex, {
                    displayMode: !!display, throwOnError: false, strict: false, output: "html",
                });
            } catch (e) { /* 落到下面的纯文本兜底 */ }
        }
        return '<code class="tex-fallback">' + escHtml(tex) + '</code>';
    }

    // 纯文本 → HTML：只把公式交给 KaTeX，其余一律转义。
    // ⚠️ 必须用 exec 逐段扫描，不能 split() 之后再判断"这段像不像公式"：
    //    split 出来的**纯文本段**也会落进判断里——“$$$$”、以及跨行未闭合的
    //    “$a … b$”都匹配不到公式（正则要求同一行闭合），
    //    公式（正则要求同一行闭合），整段原样传下去反而被当成行内公式渲染。
    //    （2026-09-13 由 tools/test_math_render.js 抓出）
    /**
     * 行内 Markdown（粗体 / 斜体 / 行内代码）。
     *
     * 卡片题干、选项、**题库自带的 explanation 与 traps** 都走 richText，
     * 而 AI 和题库作者都习惯写 **重点**。原先这里只认 $公式$、不认 Markdown，
     * 页面上就是两颗裸露的星号（用户截图反馈：「却**是**拐点」）。
     * 注意：先转义再认标记（标记都是 ASCII，转义不影响）。
     * 行内代码与 ASCII 上下标的处理见下面 asciiMath。
     */
    // 行内代码的占位符：SOH + 序号 + SOH，正文里不会出现这两个字符
    const AS_SOH = String.fromCharCode(1);
    const AS_CODE_RE = new RegExp(AS_SOH + "([0-9]+)" + AS_SOH, "g");
    function mdInline(t) {
        let s = escHtml(t);
        // 行内代码先摘出来、最后再放回：
        //   ① 代码里的 _ ^ 不该被当成上下标（写在代码里的 a_i 就该原样显示）；
        //   ② 摘出来之后，代码里的 ** 也不会被下一行的粗体规则吃掉。
        const codes = [];
        s = s.replace(/`([^`]+)`/g, function (m, c) {
            codes.push(c);
            return AS_SOH + (codes.length - 1) + AS_SOH;
        });
        s = s.replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>");
        // 单个星号当斜体，但别把 2*3 这种算式吃掉：要求有配对的第二个星号
        s = s.replace(/(^|[^*])\\*([^*\\n]+)\\*/g, "$1<em>$2</em>");
        s = asciiMath(s);
        return s.replace(AS_CODE_RE, function (m, i) {
            return '<code class="md-code">' + codes[+i] + "</code>";
        });
    }

    // ============================================================
    // 「ASCII 上下标」兜底：把 _(x) / _i / ^{n} / ^n 渲染成 <sub>/<sup>。
    //
    // 【为什么需要】题库和笔记里的公式大量是这么写的：O(n^2)、∬_D、e^(x²/2)、
    // W_T、log_(a) x。它们既不是 Unicode 上下标（x²、a₁₄、C₁ 那种能直接显示），
    // 也没有 $...$ 定界；而 splitMath 只认 $...$ 与带反斜杠命令的裸 LaTeX，
    // 于是整串原样漏出，页面上就是「F_(x)」这种带下划线的源码
    // （2026-09-17 用户截图反馈）。实测题库 38 张卡 129 处、笔记 200 余处。
    //
    // 【与 $公式$ 的分工】$...$ 里的 _ ^ 是 LaTeX，由 KaTeX 负责，这里**绝不能碰**——
    // asciiMath 只作用在 splitMath 切出来的**文本段**上（richText / mdTex 都是这个走法）。
    //
    // 【规则：保守优先，宁漏不误】改规则前先跑 tools/audit_asciimath.js（拿**这份实现**
    // 扫全部笔记，逐条列出会改哪些片段）——下面两条都是它抓出来的误判：
    //   ① 底数约束（挡文件名，如 CO_6.4 / 第5章_IO管理.md / book_id / scan_pdf.py）：
    //      · 括号式 _(...) ^{...} 是**自定界**的，只要求前面粘着一个非中文的符号——
    //        于是 log_(a) x、F_(x)、Qe^(∫Pdx) 都能转（语料里这类只有 1 处，全是真公式）；
    //      · 单词式 _x 没有右边界，收紧：底数前面**再往前一个**不能还是字母数字，
    //        且只吃 1 个字符、后面不能再跟字母数字。
    //   ② 括号配平、不超 34 字符、不跨行；内容里出现反斜杠就整体放弃
    //      （那是 LaTeX 命令的残段，交给 KaTeX/原样显示，别插手）。
    //   ③ Pandoc 配对写法 ^x^ 要把收尾的 ^ 一起吃掉，否则页面上留下一个孤立的 ^
    //      （笔记里 O(n^2^) 与 O(n^2) 两种写法并存）。
    //   ④ ~x~ **故意不处理**：中文里 ~ 是区间号（60~70分钟、第 1~11 题），
    //      转了就是洋相（审计时抓到的）。下标要写就写 _x 或 $x_{i}$。
    // ============================================================
    const AS_NL = String.fromCharCode(10);
    const AS_TAB = String.fromCharCode(9);
    const AS_BS = String.fromCharCode(92);
    function isAlnum(c) {
        return !!c && ((c >= "a" && c <= "z") || (c >= "A" && c <= "Z") || (c >= "0" && c <= "9"));
    }
    function isDigit(c) { return !!c && c >= "0" && c <= "9"; }
    /**
     * 单词式上下标能吃的**单个字符**：字母数字，外加希腊字母。
     *
     * ⚠️ 2026-09-22 补希腊字母：早间回顾里的 `(1+x)^α` 因为 α 不是 ASCII 字母数字，
     *    整个 `^α` 原样漏出，页面上就是「(1+x)^α」带一个裸露的尖号（用户截图）。
     *    模型/agent 写 α β γ 这类记号很常见，认下来比让它们漏出来强。
     */
    function isScriptUnit(c) {
        if (isAlnum(c)) return true;
        if (!c) return false;
        const n = c.charCodeAt(0);
        return n >= 0x0391 && n <= 0x03c9;      // 希腊字母：Α(0391) … ω(03c9)
    }
    /**
     * Unicode 上下标字符（`²` `ₙ` `ⁿ` 这类）。
     *
     * `e^uₙ` 里 `uₙ` 是**一个**上标：只吃 1 个字符会得到 `e^(u)ₙ` —— 下标挂到外面，
     * 意思直接错了（早间回顾的真实语料里就有这一条）。所以单词式后面若有这些字符，
     * 一起收进来。
     */
    function isUniScript(c) {
        if (!c) return false;
        const n = c.charCodeAt(0);
        return (n >= 0x2070 && n <= 0x209c)                 // 上标区 2070-207F + 下标区 2080-209C
            || n === 0x00b2 || n === 0x00b3 || n === 0x00b9;   // ² ³ ¹（在 Latin-1 里）
    }
    function isCJK(c) {
        if (!c) return false;
        const n = c.charCodeAt(0);
        return (n >= 0x3000 && n <= 0x303f) || (n >= 0x4e00 && n <= 0x9fff)
            || (n >= 0xff00 && n <= 0xffef);
    }
    /** ① 底数约束：src[i] 这个 _ 或 ^ 能不能当上下标标记（bracketed = 后面跟的是括号式） */
    function isScriptBase(src, i, bracketed) {
        const prev = src.charAt(i - 1);
        if (i < 1 || !prev || prev === " " || prev === AS_TAB
            || prev === "(" || prev === "[") return false;
        if (bracketed) return !isCJK(prev);                // 自定界，只要不粘在中文后面
        // 右括号收尾的底数照收：(x+y)^2
        if (prev === ")" || prev === "]" || prev === "}") return true;
        if (isCJK(prev)) return false;                     // 中文永远不是数学底数：字段_8路
        const prev2 = src.charAt(i - 2);
        if (!isAlnum(prev2)) return true;                  // 独立符号：x_j、∬_D、∞^0
        // 数字底数：10^6、2^5。往回跳过整串数字后不能还粘着字母，
        // 否则 int64_t / float32_t / uint8_t 这些 C 类型名会被啃掉一截（全库审计抓到的）。
        if (!isDigit(prev)) return false;
        let k = i - 1;
        while (k >= 0 && isDigit(src.charAt(k))) k--;
        return !isAlnum(src.charAt(k));
    }
    /** ② 读出上/下标的内容，返回 {body, next}；读不到返回 null */
    function readScript(src, j) {
        const open = src.charAt(j);
        if (open === "(" || open === "{") {
            const close = open === "(" ? ")" : "}";
            let depth = 0, k = j;
            while (k < src.length && k - j <= 34) {
                const c = src.charAt(k);
                if (c === AS_NL) return null;              // 不跨行，免得把正文吞进公式
                if (c === open) depth++;
                else if (c === close) { depth--; if (!depth) break; }
                k++;
            }
            if (depth !== 0 || k >= src.length || k - j > 34) return null;
            const body = src.slice(j + 1, k);
            if (!body || body.indexOf(AS_BS) >= 0) return null;   // LaTeX 命令残段，不插手
            return { body: body, next: k + 1 };
        }
        // 单词式：只吃 1 个字符，后面不能还跟着字母数字/希腊字母或下划线
        const after = src.charAt(j + 1);
        if (isScriptUnit(open) && !isScriptUnit(after) && after !== "_") {
            // 紧跟的 Unicode 上下标一起收：e^uₙ 的 uₙ 是一个整体（只吃 1 个字符会
            // 变成 e^(u)ₙ，下标挂到外面，意思就错了）
            let k = j + 1, body = open;
            while (isUniScript(src.charAt(k))) { body += src.charAt(k); k++; }
            return { body: body, next: k };
        }
        return null;
    }
    function asciiMath(src) {
        let out = "", i = 0;
        while (i < src.length) {
            const ch = src.charAt(i);
            const open0 = src.charAt(i + 1);
            const bracketed = (open0 === "(" || open0 === "{");
            if ((ch === "_" || ch === "^") && isScriptBase(src, i, bracketed)) {
                const got = readScript(src, i + 1);
                if (got) {
                    const tag = ch === "_" ? "sub" : "sup";
                    out += "<" + tag + ">" + got.body + "</" + tag + ">";
                    i = got.next;
                    if (ch === "^" && src.charAt(i) === "^") i++;   // ③ 配对的收尾 ^
                    continue;
                }
            }
            out += ch;
            i++;
        }
        return out;
    }

    /**
     * 只对 **HTML 标签之外**的文本段做上下标，标签原样保留。
     *
     * 【为什么单独一版】早间回顾（`morning_review.json`）的正文本来就是 HTML 片段
     * （`<strong>` / `<code>` / `<span class="hl-red">`），整段丢给 `richText` 会先转义、
     * 把标签变成可见源码；可它的字段里又确实有 `2^n`、`(−1)^{n−1}`、`(1+x)^α`
     * 这类写法（数据自己的习惯是 Unicode 上下标，写漏了就漏出来了）。
     * 于是：按标签切开，只把中间的文本交给 asciiMath。
     *
     * ⚠️ 标签**不参与**替换，所以属性里出现 `_` / `^` 也不会被啃（`class="hl-red"` 之类）。
     * ⚠️ `<code>` / `<pre>` 的**内容**也跳过：与笔记/闪卡那边「代码里的 _ ^ 不当上下标」
     *    同一条规矩（否则 `lim_{x→0}` 这种写在代码里的记号会被啃一半，两边不一致）。
     */
    function asciiMathHtml(html) {
        const s = html == null ? "" : String(html);
        if (s.indexOf("_") < 0 && s.indexOf("^") < 0) return s;   // 绝大多数条目直接跳过
        let codeDepth = 0;
        return s.split(/(<[^>]*>)/).map(function (seg, i) {
            if (i % 2 === 1) {                                    // 奇数项是标签本身
                const t = seg.toLowerCase();
                if (t.slice(0, 5) === "<code" || t.slice(0, 4) === "<pre") codeDepth++;
                else if (t.slice(0, 7) === "</code>" || t.slice(0, 6) === "</pre>") {
                    codeDepth = Math.max(0, codeDepth - 1);
                }
                return seg;
            }
            return codeDepth ? seg : asciiMath(seg);
        }).join("");
    }

    function richText(s) {
        const raw = s == null ? "" : String(s);
        const parts = splitMath(raw);
        if (!parts.some(p => p.tex)) return mdInline(raw);   // 没有公式也照样认 Markdown
        if (!window.katex) ensureKatex();   // 按需加载；就绪后 flushMath 会原地重渲染
        return parts.map(p => p.tex
            ? katexHtml(p.src, p.display)
            : mdInline(p.text)).join("");
    }

    /**
     * 把原文切成「文本 / 公式」片段（两个渲染器共用，所以挂在 window 上）。
     *
     * ① 显式定界：$$..$$ 与 \[..\]（独立行）、$..$ 与 \(..\)（行内）；
     * ② ️ **没有定界的裸 LaTeX**：模型经常把 \sum a_n、\Rightarrow、\frac{...}
     *    直接写在句子中间——尤其用户在设置里自己写了提示词、没要求用 $ 包裹时。
     *    不认的话，页面上就是一堆裸露的 \sum \frac{(-1)^{n+1}}{n}（用户截图反馈过）。
     *
     * 裸 LaTeX 的判定刻意保守：整段必须只由「公式字符」组成、含 \命令，
     * 遇到中文/中文标点就截断 —— 宁可漏判，也不能把中文正文卷进公式里。
     * 返回 [{tex:false,text} | {tex:true,src,display}]。
     */
    // ️ 这里是**字符类正则**，不是字符串 indexOf —— 踩过：写成 "A-Za-z0-9…" 再用 indexOf，
    //    只能匹配字面字符，`n`/`5` 都不在里面，于是 \sum a_n 被切成 `\sum a_` + `n`。
    const TEX_BS = String.fromCharCode(92);
    const TEX_NL = String.fromCharCode(10);
    const TEX_CH = (n) => String.fromCharCode(n);
    // 「非中文」字符类（用字符码拼，避免在普通 Python 字符串里写 \\uXXXX 那一堆转义）
    const TEX_NOT_CJK = "^" + TEX_CH(0x3000) + "-" + TEX_CH(0x303f)      // 中文标点
        + TEX_CH(0x4e00) + "-" + TEX_CH(0x9fff)                          // 汉字
        + TEX_CH(0xff00) + "-" + TEX_CH(0xffef)                          // 全角
        + TEX_CH(10) + TEX_CH(13);                                       // 换行
    /**
     * 裸 LaTeX（没有 $ 定界）：**按中文切开，非中文的片段里只要含 \命令 就整段当公式**。
     *
     * ⚠️ 为什么不用「找到命令再左右扩张」：那种写法要在字符串上算区间，一条句子里出现
     *    两个命令时极易算错（踩了两次：把 `\sum a_n 和 \sum b` 并成一段、丢掉开头的反斜杠、
     *    还把中文「和」卷进公式）。改成「先按中文切块」之后，块与块之间没有共享索引，
     *    逻辑一眼可验。代价：非中文块里若还夹着英文散文，会一起当公式——
     *    中文笔记里几乎不会出现，换来的是确定不会算错。
     */
    const TEX_CJK_SPLIT = new RegExp("([" + TEX_CH(0x3000) + "-" + TEX_CH(0x303f)
        + TEX_CH(0x4e00) + "-" + TEX_CH(0x9fff) + TEX_CH(0xff00) + "-" + TEX_CH(0xffef) + "]+)");
    const TEX_HAS_CMD = new RegExp(TEX_BS + TEX_BS + "[a-zA-Z]+");
    function collectBareTex(text, out) {
        // ⚠️ 只**登记公式区间**，正文按原样整段输出 —— 不能把正文也按中文切块，
        //    否则 `却**是**拐点` 的 `**` 会被切到两个块里，行内 Markdown 就配不上对了
        //    （踩过：粗体渲染回归）。切块只用来定公式的边界。
        const spans = [];
        const chunks = text.split(TEX_CJK_SPLIT);
        let pos = 0;
        chunks.forEach(function (chunk, idx) {
            const start = pos;
            pos += chunk.length;
            if (idx % 2 === 1 || !chunk) return;         // 奇数项是中文块（split 带捕获组）
            let a = 0, b = chunk.length;
            while (b > a && (chunk.charAt(b - 1) === " " || chunk.charCodeAt(b - 1) === 9)) b--;
            while (b > a && ".,;:".indexOf(chunk.charAt(b - 1)) >= 0) b--;
            while (a < b && (chunk.charAt(a) === " " || chunk.charCodeAt(a) === 9)) a++;
            if (b - a < 2 || b - a > 160) return;        // 太短没意义、太长多半不是公式
            const body = chunk.slice(a, b);
            if (!TEX_HAS_CMD.test(body)) return;         // 没有 \命令 → 不是公式
            // 前面若只是个英文单词（"the \alpha"），把它留给正文，公式从命令开始
            const mLead = /^[A-Za-z0-9]+ /.exec(body);
            spans.push([start + a + (mLead ? mLead[0].length : 0), start + b]);
        });
        let last = 0;
        spans.forEach(function (sp) {
            if (sp[0] > last) out.push({ tex: false, text: text.slice(last, sp[0]) });
            out.push({ tex: true, src: text.slice(sp[0], sp[1]), display: false });
            last = sp[1];
        });
        if (last < text.length) out.push({ tex: false, text: text.slice(last) });
    }
    // 定界符：$$..$$ 与 \[..\]（独立行）、$..$ 与 \(..\)（行内）。
    // ⚠️ 层数极易少写一层（踩过两次）：要匹配「反斜杠 + 左括号」这个组合，
    //    pattern 必须是 \\( —— `\\` 是字面反斜杠、`\(` 才是字面左括号，
    //    也就是**三个** TEX_BS 再拼括号。写成两个时正则把 `(` 当成**分组左括号**，
    //    最后那条就成了「字面反斜杠 … 字面反斜杠」，于是 `\sum a_n 和 \sum b` 被从
    //    两个反斜杠处切开、中间的中文和半截公式全被当成公式内容（查了很久的 bug）。
    const TEX_D = TEX_BS + "$";                                  // 匹配字面 $（无需反斜杠）
    const TEX_OB = TEX_BS + TEX_BS + TEX_BS + "[", TEX_CB = TEX_BS + TEX_BS + TEX_BS + "]";
    const TEX_OP = TEX_BS + TEX_BS + TEX_BS + "(", TEX_CP = TEX_BS + TEX_BS + TEX_BS + ")";
    // 原文里这两个字符组合的真身（判断是不是独立行公式、以及要剥几个字符时用）
    const TEX_OB_TXT = TEX_BS + "[";
    // 「任意字符（含换行）」：同样用字符码拼 —— 直接写 \s\S 会被 Python 与 JS 各吃一层，
    // 到正则手里变成 [sS]（只匹配 s/S），`$$..$$` 就再也匹配不上了。
    const TEX_ANY = "[" + TEX_BS + "s" + TEX_BS + "S]";
    const TEX_OP_TXT = TEX_BS + "(";
    // ⚠️ 行内 `\(..\)` 的内容**不能**写成 `[^)]*?`（那是「除右括号外的任意字符」）：
    //    模型写的 `\(P(x)Q(y)\)`、`\(F(x,y)\)` 里本来就有圆括号，于是整个匹配失败，
    //    定界符连同 `\frac` 一起原样摊在正文里（2026-09-22 用 explain_log 全量语料
    //    跑真浏览器探针抓出来：51 条回复里 10 条中招）。
    //    改成「本行内任意字符、惰性到第一个 `\)`」：里面有多少括号都无所谓，
    //    真正的收尾符是两字符的 `\)`，不会跟内容里的 `)` 混。
    const TEX_DELIM = new RegExp("(" + TEX_D + TEX_D + TEX_ANY + "+?" + TEX_D + TEX_D
        + "|" + TEX_OB + TEX_ANY + "+?" + TEX_CB
        + "|" + TEX_D + "[^$" + TEX_NL + "]+?" + TEX_D
        + "|" + TEX_OP + "[^" + TEX_NL + "]*?" + TEX_CP + ")", "g");
    /**
     * 把原文里的公式**换成占位符**，其余原样返回（笔记渲染器用：先把公式抠出来，
     * 免得后面按行处理 Markdown 时把 `$$...$$` 拆坏）。
     *
     * 复用同一个 splitMath：所以笔记也自动获得「`\[..\]`、`\(..\)`、以及没有定界的裸 LaTeX」
     * 的识别能力。以前 mdRender 里是自己两行正则，于是**笔记里的裸 LaTeX 全是源码**
     * （用户问「你公式渲染的逻辑要一个一写吗？为什么不复用」）。
     */
    function maskMath(raw, maths) {
        let out = "";
        splitMath(raw).forEach(function (p) {
            if (p.tex) {
                maths.push({ tex: p.src, display: p.display });
                out += "@@MATH" + (maths.length - 1) + "@@";
            } else {
                out += p.text;
            }
        });
        return out;
    }
    window.maskMath = maskMath;

    function splitMath(raw) {
        const s = raw == null ? "" : String(raw);
        const out = [];
        TEX_DELIM.lastIndex = 0;
        let last = 0, m;
        while ((m = TEX_DELIM.exec(s)) !== null) {
            if (m.index > last) collectBareTex(s.slice(last, m.index), out);
            const seg = m[0];
            const head2 = seg.slice(0, 2);
            // ️ 定界符长度不一样：$$ 与 \[ ( 都是**两个字符**，$ 是一个。
            //    一律按 1 剥会把 `\(a+b\)` 剥成 `(a+b\`（探针抓到过）。
            const display = head2 === "$$" || head2 === TEX_OB_TXT;
            const dlen = (head2 === "$$" || head2 === TEX_OB_TXT || head2 === TEX_OP_TXT) ? 2 : 1;
            out.push({ tex: true, src: seg.slice(dlen, -dlen), display: display });
            last = m.index + seg.length;
        }
        if (last < s.length) collectBareTex(s.slice(last), out);
        return out;
    }

    // 登记待渲染元素：texWrap 返回带 data-texid 的容器，KaTeX 就绪后原地重渲染
    let texSeq = 0;
    const texStore = new Map();
    /**
     * 登记一个「KaTeX 就绪后原地重渲染」的容器，返回要写进 data-texid 的编号。
     *
     * texWrap 登记的是**原文**（重渲染时再切一次公式，走 richText）；
     * AI 回复（mdTex）登记的是**函数** —— 它得连 Markdown 一起重渲染，
     * 退回 richText 会把标题/表格/粗体全丢掉。两种值 flushMath 都认。
     */
    function registerTex(rerender) {
        const id = ++texSeq;
        texStore.set(id, rerender);
        return id;
    }
    function texWrap(raw) {
        const id = ++texSeq;
        texStore.set(id, raw == null ? "" : String(raw));
        return '<span class="tex-host" data-texid="' + id + '">' + richText(raw) + '</span>';
    }
    // 换卡时清空登记（上一张的元素已从 DOM 移除，留着只会涨内存）
    function resetTexStore() { texStore.clear(); }
    function flushMath() {
        if (!window.katex) return;
        document.querySelectorAll("[data-texid]").forEach(el => {
            const raw = texStore.get(+el.getAttribute("data-texid"));
            if (raw == null) return;
            // 登记值可能是字符串（richText 那条路）或函数（mdTex：整块连 Markdown 一起重渲染）
            el.innerHTML = typeof raw === "function" ? raw() : richText(raw);
        });
    }

    window.ensureKatex = ensureKatex;
    window.katexHtml = katexHtml;
    window.richText = richText;
    window.texWrap = texWrap;
    window.resetTexStore = resetTexStore;
    window.flushMath = flushMath;
    // mdTex（AI 回复渲染器，在另一个 IIFE 里）要把整块登记进来，供 KaTeX 就绪后重渲染
    window.registerTex = registerTex;
    // escHtml 也要导出：闪卡区新增的 mdTex（Markdown+LaTeX 子集渲染）在另一个
    // IIFE 里，需要它做转义。原先只导出了 richText/texWrap，escHtml 是私有的。
    window.escHtml = escHtml;
    // ⚠️ mdInline 也要过这道桥：mdTex 在**后面另一个 IIFE** 里，跨 IIFE 只能走 window
    //    （浏览器里裸名就是全局，所以 mdTex 里直接写 mdInline(t) 即可）。
    //    忘了桥接的后果很实在：mdTex 一调用就 ReferenceError，AI 回复整块渲染不出来，
    //    而 catch 把它吞了，表现成「复用没生效，又问了一遍 AI」。测试抓到的。
    window.mdInline = mdInline;
    // asciiMath 同样过桥：笔记预览走的是 mdRender 里**另一套** inline()（它有自己的
    // 代码/表格/图片处理链，不经过 mdInline），但 ASCII 上下标是同一类问题，
    // 必须共用同一套规则，否则笔记和闪卡会呈现出两种结果。
    window.asciiMath = asciiMath;
    // 带 HTML 标签的字段（早间回顾那种）用这一版：只转标签外的文本
    window.asciiMathHtml = asciiMathHtml;
    // splitMath 也要过桥：mdTex 在后面那个 IIFE 里，且它同样需要认「裸 LaTeX」
    window.splitMath = splitMath;
})();

// ============================================================
// 答题音效（2026-09-14）
// 用 Web Audio 现场合成，不引入任何音频文件：大盘是单文件、离线可用的，
// 塞 mp3 会破坏这个前提，转成 base64 内联又会让 HTML 白白胖几百 KB。
// AudioContext 必须在用户手势里创建/恢复，所以这里只在首次触发时惰性建。
// ============================================================
const SFX = (function () {
    const KEY = "kaoyan.sfx.muted";
    let ctx = null;
    let muted = false;
    try { muted = localStorage.getItem(KEY) === "1"; } catch (e) {}

    function ac() {
        if (!ctx) {
            const AC = window.AudioContext || window.webkitAudioContext;
            if (!AC) return null;              // 老浏览器：静默降级，不影响答题
            ctx = new AC();
        }
        if (ctx.state === "suspended") ctx.resume();
        return ctx;
    }

    // 一个音符 = 振荡器 + 指数衰减包络。
    // 用 exponentialRamp 而不是 linear：人耳对响度是对数感知的，
    // 线性衰减听起来像被硬切断，指数衰减才像自然的余音。
    //
    // 2026-09-20：整体增益上调约 70%，并加一个主音量 MASTER，
    // 之后统一调音量只改一个地方。
    // 2026-09-21：再上调一档 —— 用户反馈「答错和非选择题好像缺音效」。
    // 实测原因不是没播，而是**低频 + 低增益在小喇叭上基本听不见**：
    // correct 是 659~1318Hz（听得见），wrong 却是 220/185Hz、reveal 只是一声 440Hz/0.10s，
    // 笔记本/平板喇叭在那一档几乎不出声。所以把低沉的几个往上抬频+抬增益。
    const MASTER = 0.42;
    function g(v) { return v * MASTER; }
    function tone(freq, delay, dur, gain, type) {
        const c = ac();
        if (!c) return;
        const t0 = c.currentTime + delay;
        const osc = c.createOscillator();
        const gn = c.createGain();
        osc.type = type || "sine";
        osc.frequency.setValueAtTime(freq, t0);
        gn.gain.setValueAtTime(0.0001, t0);
        gn.gain.exponentialRampToValueAtTime(g(gain), t0 + 0.012);
        gn.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
        osc.connect(gn).connect(c.destination);
        osc.start(t0);
        osc.stop(t0 + dur + 0.03);
    }

    const PATTERNS = {
        // 答对：明亮大三度上行 + 高八度装饰音，清脆肯定
        correct: function () {
            tone(659.25, 0,     0.14, 0.22);
            tone(987.77, 0.075, 0.22, 0.20);
            tone(1318.5, 0.16,  0.18, 0.13, "triangle");
        },
        // 答错：下行小三度，低沉但不刺耳——是提示，不是惩罚。
        // ⚠️ 原来用 220/185Hz，笔记本与平板喇叭那一档几乎不出声（用户反馈"没音效"）。
        //    抬到 311/262Hz 并加大增益：还是"低"，但听得见。
        wrong:   function () {
            tone(311.13, 0,    0.20, 0.34);
            tone(261.63, 0.11, 0.26, 0.30);
        },
        // 放弃看答案 / 翻卡：中性轻响，比 reveal 稍亮一点，给个"翻过去"的触感
        flip:    function () {
            tone(523.25, 0,    0.09, 0.20, "triangle");
            tone(783.99, 0.05, 0.11, 0.16, "sine");
        },
        // 看答案按钮（不判对错）：一声短促中频，不抢戏。
        // 0.10s/0.12 增益太轻了，几乎听不到 → 加长一点、抬亮一点。
        reveal:  function () {
            tone(587.33, 0,    0.12, 0.26, "triangle");
            tone(880.00, 0.07, 0.12, 0.18, "sine");
        },
        // 评分按钮（1-4 自评）：按档位给不同音高，越低沉代表记得越差、越高代表越轻松
        //   1 忘记 → 低；2 模糊 → 中低；3 记得 → 中；4 简单 → 高
        rate: function (r) {
            const map = { 1: [293.66, 0.20], 2: [369.99, 0.16], 3: [493.88, 0.14], 4: [622.25, 0.12] };
            const pair = map[r] || map[3];
            tone(pair[0], 0, pair[1], 0.24, "triangle");
        },
        // 一组刷完：主三和弦琶音，明亮收尾
        done:    function () {
            [523.25, 659.25, 783.99, 1046.50].forEach(function (f, i) {
                tone(f, i * 0.08, 0.28, i === 3 ? 0.26 : 0.18, i < 2 ? "sine" : "triangle");
            });
        }
    };

    // 记录下来「播放过什么」：测试据此断言「答错确实响了 wrong」这类链路，
    // 而不是只看代码里有没有那一行（名字写错时是**静默无声**的，最难查）。
    const playedLog = [];
    return {
        play: function (name /* , ...args */) {
            playedLog.push(String(name));
            if (playedLog.length > 60) playedLog.shift();
            if (muted) return;
            const p = PATTERNS[name];
            if (!p) { console.warn("[SFX] 没有这个音效名:", name); return; }
            try { p.apply(null, Array.prototype.slice.call(arguments, 1)); } catch (e) { /* 音频不可用不该影响答题 */ }
        },
        names: function () { return Object.keys(PATTERNS); },
        played: function () { return playedLog.slice(); },
        isMuted: function () { return muted; },
        setMuted: function (v) {
            muted = !!v;
            try { localStorage.setItem(KEY, muted ? "1" : "0"); } catch (e) {}
        },
        // 首次手势时预热，免得第一声因为 AudioContext 还 suspended 而被吞掉
        unlock: function () { try { ac(); } catch (e) {} }
    };
})();

// ============================================================
// 闪卡练习区：看大盘时顺便刷题。选题由服务端完成
// （薄弱卡 > 到期卡 > 近日笔记相关新卡），评分即时 FSRS 回写。
// ============================================================
(function() {
    // API 基址：用当前页面 origin，平板/手机经局域网访问时才能正常调接口；
    // 本地以 file:// 直开时回落到 localhost:8080
    const API = location.protocol.startsWith('http') ? location.origin : "http://localhost:8080";
    const box = document.getElementById("flash-studio");
    const LABELS = {1: "忘记", 2: "模糊", 3: "记得", 4: "简单"};
    // lastRatedIdx：最近一次评分落在哪张卡上。撤销要靠它定位——「选错即判」的卡不会
    //   立刻翻页，此时 idx 仍停在该卡，沿用旧的「idx-1」假设会把撤销打到上一张去。
    // autoWrong：本张已按「选错即判」自动回写为忘记，只等翻页，不再要求自评。
    // filter：筛选页选的 {subject, bucket}。为 null 时走原来的「智能选题」，
    //   一旦有值就走 mode=browse 且不受每日限额约束（服务端见 session 端点）。
    const state = { cards: [], idx: 0, revealed: false, answered: false, sending: false,
                    stats: {1: 0, 2: 0, 3: 0, 4: 0}, reviewedToday: 0,
                    limits: null, counts: null, lastRating: null,
                    lastRatedIdx: null, autoWrong: false, saveFailed: false,
                    // groupMode：这一组从哪条通道来的（smart / browse / extra / local）。
                    // extra＝「今日刷完后再来 N 张」那一组，local＝早间回顾的本地卡组。
                    filter: null, groupMode: null,
                    // local：本地卡组（早间回顾）的现场。非 null 时本模块只翻卡自评，
                    // 不碰主闪卡库（见「本地卡组」那一段）。
                    local: null };
    // 自动判错的提交句柄：翻页前要等它落地，否则本地推进了而服务端没记账
    let pendingSubmit = null;
    const LS_KEY = "kaoyan_flash_session_v1";
    const LS_FILTER = "kaoyan_flash_filter_v1";   // 筛选条件单独存，跨「重开一组」保留
    // 筛选页上**待确认**的选择。与 state.filter（正在生效的）分开，否则点一下 chip
    // 就会让筛选条件和场上正在刷的卡对不上。
    let pending = { subject: "", bucket: "", topic: "" };

    function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
    // ⚠️ 本地日期，不要用 toISOString().slice(0,10)（那是 UTC，UTC+8 早 8 点前会差一天）。
    // 走 studyDay()：凌晨 4 点前算前一天，跟服务端 localToday() 同一套规则。
    function todayStr() { return studyDay(); }

    // ---- 进度保持（2026-09-21 改成服务端为准）----
    // 以前本组会话只存 localStorage：换设备各刷各的（同一题一天问好几遍），
    // 浏览器存储被清/写入失败（还被 catch 吞了）时进度就"凭空重置"——用户两个都报过。
    // 现在：**服务端 `flash_session` 是唯一事实源**，localStorage 只当离线兜底缓存。
    const DEVICE = (function () {
        // 只是个便于排查的标签，不参与任何判断
        try {
            const ua = navigator.userAgent || "";
            if (/iPad|Tablet/i.test(ua)) return "平板";
            if (/Mobile|Android|iPhone/i.test(ua)) return "手机";
            return "电脑";
        } catch (e) { return ""; }
    })();
    let pushPosTimer = 0, pushedOnce = false;
    function pushPosition() {
        // 本地卡组（早间回顾）不进主闪卡库的当日进度：那一位是他的闪卡库进度
        if (state.local) return;
        // 轻量：只报「刷到第几张」。每翻一张调一次也无所谓；失败就算了（本地缓存还在）。
        clearTimeout(pushPosTimer);
        pushPosTimer = setTimeout(function () {
            try {
                fetch(API + "/api/flashcards/position", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ idx: state.idx, device: DEVICE }),
                }).catch(function () {});
            } catch (e) {}
        }, 400);
    }
    function pushSessionNow() {
        // ⚠️ 本地卡组（早间回顾）绝对不能推给服务端：服务端的 flash_session 是
        //    「主闪卡库当日这一组」，被早间回顾的卡顶掉，他回闪卡页就会接着刷回顾内容。
        if (state.local) return;
        // 整份存服务端：新开一组 / 页面隐藏离开时用
        try {
            fetch(API + "/api/flashcards/session", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ cards: state.cards, idx: state.idx, filter: state.filter,
                                       device: DEVICE }),
            }).catch(function () {});
        } catch (e) {}
    }
    function saveSession() {
        // 本地卡组只落自己的那份（不进主闪卡库的 localStorage，也不推服务端）
        if (state.local) return saveLocalSession();
        try {
            localStorage.setItem(LS_KEY, JSON.stringify({
                date: todayStr(), cards: state.cards, idx: state.idx, stats: state.stats,
                lastRatedIdx: state.lastRatedIdx, filter: state.filter, mode: state.groupMode
            }));
        } catch (e) {}   // 本地写失败不再等于进度丢失（服务端有）
        if (!pushedOnce) { pushedOnce = true; pushSessionNow(); }
        pushPosition();
    }
    function clearSession() { try { localStorage.removeItem(LS_KEY); } catch (e) {} }
    // 只读、不改 state：「开始」闸门要先知道有没有没刷完的本组，
    // 才能决定按钮上写「继续本组」还是「开始学习」。
    function readSaved() {
        try {
            const raw = localStorage.getItem(LS_KEY);
            if (!raw) return null;
            const s = JSON.parse(raw);
            if (s.date !== todayStr() || !Array.isArray(s.cards) || s.idx >= s.cards.length) return null;
            return s;
        } catch (e) { return null; }
    }
    function tryResume() {
        const s = readSaved();
        if (!s) return false;
        state.cards = s.cards; state.idx = s.idx;
        state.stats = s.stats || {1: 0, 2: 0, 3: 0, 4: 0};
        state.lastRatedIdx = typeof s.lastRatedIdx === "number" ? s.lastRatedIdx : null;
        // ⚠️ 恢复的 filter 必须过 normalizeFilter：旧版本/异常值可能写入 {subject:"",bucket:""}
        // 或 {} 这类空对象，直接用会被 if(state.filter) 判真、误加 &mode=browse，
        // 于是每日新卡/复习额度整个失效（服务端 browse 是豁免额度的）。
        state.filter = (s.filter && typeof s.filter === "object")
            ? normalizeFilter(s.filter) : readFilter();
        // 这一组是哪条通道来的（「再来一组」的说明文字靠它）
        state.groupMode = s.mode || null;
        return true;
    }
    function fetchToday() {
        fetch(API + "/api/flashcards/today").then(r => r.json()).then(d => {
            if (!d.ok) return;
            state.reviewedToday = d.reviewed_today;
            state.todayInfo = d;
            // 闸门上的「待复习 N 张」也要等这个回来才有数；回来了就补画一次
            if (gateShowing) renderGate();
        }).catch(() => {});
    }

    // ============================================================
    // 本地卡组（2026-09-20）：早间回顾把**当天的回顾内容**翻成卡，就在这套练习区里刷。
    //
    // 用户原话：「早间的这个闪卡的作用，不是再去学一学闪卡库里的闪卡，而就是用来学
    // 早间回顾的。就是把早间回顾的内容变成翻转的闪卡。这样正好我把这个闪卡读完之后，
    // 就自动打卡，早间回顾就可以了。」
    //
    // 所以这条通道与智能组/自选组的根本区别是：**完全不碰主闪卡库** ——
    //   · 不向服务端组题（不查 session，也就不受每日额度、待修卡、退役策略影响）
    //   · 不写 review_log、不动 FSRS、不推服务端进度（不会顶掉主库「当日这一组」）
    //   · 卡片内容由调用方（早间回顾页）用已有数据拼好，这里只负责翻卡与自评
    // 进度只存本机 localStorage —— 早间回顾是「当天一次」的事，不需要多端续刷。
    // ============================================================
    const LOCAL_KEY = "kaoyan_mr_flash_v1";        // 没刷完的那一组（可「继续本组」）
    const LOCAL_DONE_KEY = "kaoyan_mr_flash_done_v1";  // 哪几天已经整组刷完过
    // 练习区里现在停着的是不是「早间回顾那一组」的总结屏：切回闪卡页时要把它换回闸门，
    // 否则闪卡页看起来像还停在早间回顾里（2026-09-20）。
    let localSummary = false;
    // 自评四档的本地口径（不显示 FSRS 的「下次间隔」——这些卡不进调度）
    const LOCAL_LABELS = { 1: "没想起来", 2: "有点糊", 3: "想起来了", 4: "很熟" };

    /** 卡片正文是**已经拼好的 HTML**（数据里自带 <strong>/<br>/<span>），不能转义。 */
    function rawHtml(s) {
        const f = (typeof window !== "undefined" && window.asciiMathHtml) || null;
        const t = s == null ? "" : String(s);
        return f ? f(t) : t;
    }
    /** 调用方给的卡：{id, front, back, sec, secLabel} → 本模块认的卡形状。 */
    function normalizeLocalCards(cards) {
        const out = [];
        (Array.isArray(cards) ? cards : []).forEach(function (c, i) {
            if (!c) return;
            const front = String(c.front == null ? "" : c.front);
            const back = String(c.back == null ? "" : c.back);
            if (!front && !back) return;
            out.push({
                card_id: String(c.id || ("local-" + (i + 1))).slice(0, 60),
                type: "fill",
                raw: true,                    // front/back 是 HTML，渲染时别再转义
                sec: String(c.sec || ""),
                sec_label: String(c.secLabel || ""),
                content: { stem: front, answer: back },
            });
        });
        return out.slice(0, 80);              // 一组最多 80 张（防误传一个巨大的数组进来）
    }
    function saveLocalSession() {
        if (!state.local) return;
        try {
            localStorage.setItem(LOCAL_KEY, JSON.stringify({
                date: state.local.date, kind: state.local.kind, title: state.local.title,
                idx: state.idx, stats: state.stats, cards: state.cards,
            }));
        } catch (e) {}
    }
    function readLocalSession(date, kind) {
        try {
            const s = JSON.parse(localStorage.getItem(LOCAL_KEY) || "null");
            if (!s || !Array.isArray(s.cards) || !s.cards.length) return null;
            if (date && s.date !== date) return null;
            if (kind && s.kind !== kind) return null;
            if (s.idx >= s.cards.length) return null;      // 已经刷完
            return s;
        } catch (e) { return null; }
    }
    function clearLocalSession() { try { localStorage.removeItem(LOCAL_KEY); } catch (e) {} }
    function markLocalDone(date) {
        if (!date) return;
        try {
            const arr = JSON.parse(localStorage.getItem(LOCAL_DONE_KEY) || "[]");
            if (arr.indexOf(date) < 0) arr.push(date);
            localStorage.setItem(LOCAL_DONE_KEY, JSON.stringify(arr.slice(-40)));
        } catch (e) {}
    }
    function isLocalDone(date) {
        try { return (JSON.parse(localStorage.getItem(LOCAL_DONE_KEY) || "[]")).indexOf(date) >= 0; }
        catch (e) { return false; }
    }
    // 早间回顾页要这两件事：还有几张没刷完、这个日期是不是已经整组刷完过
    globalThis.__mrFlashState = function (date) {
        const s = readLocalSession(date, "mr-day");
        return { left: s ? Math.max(0, s.cards.length - s.idx) : 0, done: isLocalDone(date) };
    };
    // 浮窗收起（练习区要回闪卡页）时调：把留在里面的早间回顾总结屏换回闪卡页的闸门。
    globalThis.__flashResetLocalView = function () {
        if (!localSummary) return;
        localSummary = false;
        state.cards = []; state.idx = 0;
        state.revealed = false; state.answered = false; state.autoWrong = false;
        gateShowing = true;
        renderGate();
    };

    /**
     * 开一组本地卡（走练习区本身，所以翻转/键盘/音效/计时/撤销全都照旧）。
     * opts: {kind, date, title, record:'mr-sr'|'', onFinish:fn, resume, idx, stats}
     */
    function startLocalGroup(list, title, opts) {
        opts = opts || {};
        state.local = {
            kind: opts.kind || "local",
            date: opts.date || "",
            title: title || opts.title || "本地卡组",
            record: opts.record || "",     // "mr-sr" → 自评回写早间回顾自己的间隔重复
            onFinish: typeof opts.onFinish === "function" ? opts.onFinish : null,
        };
        state.cards = list;
        state.idx = Math.max(0, Math.min(parseInt(opts.idx, 10) || 0, list.length - 1));
        state.stats = opts.stats || { 1: 0, 2: 0, 3: 0, 4: 0 };
        state.limits = null; state.counts = null; state.policy = null;
        state.filter = null;                  // 本地组不参与任何筛选（更不该带点名）
        state.groupMode = "local";
        state.revealed = false; state.answered = false;
        state.autoWrong = false; state.saveFailed = false;
        state.lastRating = null; state.lastRatedIdx = null;
        pendingSubmit = null; state.short = null; state.chosen = null;
        gateShowing = false;
        startStudy();                         // 明确点了「开始」= 同时开始计时
        saveLocalSession();
        renderCard();
    }

    // ============================================================
    // 学习计时（2026-09-21）：点了「开始学习」才走表，窗口失焦自动停。
    //
    // 为什么不用「blur 停表 / focus 起表」的事件配对：切标签页、Alt+Tab、弹出
    // 系统对话框时，focus 并不总能等到的——漏一次配对，计时就永远停在那儿，
    // 而用户看到的是「我明明在学，表却不动」。所以改成每 250ms 问一次
    // 「此刻算不算在学」（document.hidden 与 document.hasFocus() 双条件），
    // 焦点状态自己会恢复，不需要事件成对。
    //
    // 长空档（>2s）不计入：后台标签页的定时器会被浏览器限流甚至挂起，
    // 回到页面那一下如果按墙钟补，离开的一小时会全被算成学习时长。
    // ============================================================
    const TIMER_KEY = "kaoyan_flash_study_ms_v1";
    const STUDY = { ms: 0, dayMs: 0, started: false, last: 0, iv: null };
    function pageFocused() {
        if (typeof document === "undefined") return false;
        // 测试桩里没有 hidden / hasFocus，缺省按「有焦点」处理，不能把计时判死
        if (document.hidden === true) return false;
        if (typeof document.hasFocus === "function" && !document.hasFocus()) return false;
        return true;
    }
    function fmtClock(ms) {
        const t = Math.max(0, Math.floor(ms / 1000));
        const pad = n => (n < 10 ? "0" : "") + n;
        const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
        return h > 0 ? (h + ":" + pad(m) + ":" + pad(s)) : (pad(m) + ":" + pad(s));
    }
    function loadDayMs() {
        try {
            const o = JSON.parse(localStorage.getItem(TIMER_KEY) || "null");
            if (o && o.date === todayStr()) STUDY.dayMs = Number(o.ms) || 0;
        } catch (e) {}
    }
    function saveDayMs() {
        try { localStorage.setItem(TIMER_KEY, JSON.stringify({ date: todayStr(), ms: Math.round(STUDY.dayMs) })); } catch (e) {}
    }
    let lastTimerSig = "", lastTimerSave = 0;
    function paintStudy() {
        const live = STUDY.started && pageFocused();
        // 秒级签名：没变化就不碰 DOM（每 250ms 一次重绘没必要）
        const sig = (STUDY.started ? 1 : 0) + "" + (live ? 1 : 0) + fmtClock(STUDY.ms) + "/" + fmtClock(STUDY.dayMs);
        if (sig === lastTimerSig) return;
        lastTimerSig = sig;
        const t = document.getElementById("fs-timer");
        if (t) {
            t.className = "fs-timer " + (!STUDY.started ? "off" : (live ? "run" : "pause"));
            t.innerHTML = '<span class="fs-timer-dot"></span>'
                + (STUDY.started
                    ? (live ? "" : '<span class="fs-timer-tag">已暂停</span>')
                      + '<b>' + fmtClock(STUDY.ms) + '</b>'
                      + '<span class="fs-timer-day">今日 ' + fmtClock(STUDY.dayMs) + '</span>'
                    : '<span class="fs-timer-idle">未开始 · 今日 ' + fmtClock(STUDY.dayMs) + '</span>');
            t.title = STUDY.started
                ? (live ? "学习中 · 本组 " + fmtClock(STUDY.ms) + "，今日累计 " + fmtClock(STUDY.dayMs)
                        : "窗口失焦，计时已暂停；回到本窗口自动继续")
                : "学习计时：点「开始学习」后才计时，切到别的窗口或标签页会自动暂停";
        }
        const stop = document.getElementById("fs-stop");
        if (stop) stop.hidden = !STUDY.started;
        if (STUDY.started && Date.now() - lastTimerSave > 5000) { lastTimerSave = Date.now(); saveDayMs(); }
    }
    function tickStudy() {
        const now = Date.now();
        let d = now - STUDY.last;
        STUDY.last = now;
        if (d > 2000) d = 0;
        if (STUDY.started && d > 0 && pageFocused()) { STUDY.ms += d; STUDY.dayMs += d; }
        paintStudy();
    }
    function startStudy() {
        if (!STUDY.started) { STUDY.started = true; STUDY.ms = 0; }
        STUDY.last = Date.now();
        if (STUDY.iv == null) STUDY.iv = setInterval(tickStudy, 250);
        paintStudy();
    }
    function endStudy() {
        if (!STUDY.started) return;
        tickStudy();
        STUDY.started = false;
        if (STUDY.iv != null) { clearInterval(STUDY.iv); STUDY.iv = null; }
        saveDayMs();
        paintStudy();
    }
    // 焦点变化时立刻归零 last 并重绘，让「已暂停」在 1 帧内出现，
    // 不用等下一个 tick（也顺手切断挂起期间的长空档）。
    if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
        ["focus", "blur", "visibilitychange", "pageshow", "pagehide"].forEach(ev =>
            window.addEventListener(ev, () => { STUDY.last = Date.now(); paintStudy(); }));
    }

    // ---- 筛选条件 ----
    const SUBJECTS = ["政治", "408", "数学一", "英语一"];
    // flagged = 「这道题有问题」的待修卡（2026-09-17）。它跟 leech/suspended 一样是
    // **叠加标签**，不参与智能组的互斥分桶；智能组还会主动把待修卡排除掉，
    // 唯一能看到它们的地方就是这里（服务端 bucket=flagged 走的也是自选通道）。
    const BUCKETS = ["", "new", "learning", "review", "mature", "leech", "suspended", "flagged", "pinned"];
    function readFilter() {
        try {
            const f = JSON.parse(localStorage.getItem(LS_FILTER) || "null");
            if (!f || typeof f !== "object") return null;
            // ⚠️ 旧版本把「点名的那几张」（ids）也写进了 localStorage。这里**直接丢掉**：
            //    ids 是「这一次就练这几张」的一次性意图，一旦落盘，之后每次打开闪卡页
            //    都变成再刷那几张——2026-09-20 用户报的「点开还是昨天刷的那组、
            //    重开一组还是那 9 张」就是这么来的。写在读的地方，旧脏值自己也好了。
            const hadIds = Array.isArray(f.ids) && f.ids.length > 0;
            delete f.ids;
            const clean = normalizeFilter(f);
            // 顺手把清理结果写回去：他不用去清浏览器缓存，下次读到的就是干净的。
            if (hadIds) {
                try {
                    if (clean && (clean.subject || clean.bucket || clean.topic)) {
                        localStorage.setItem(LS_FILTER, JSON.stringify({
                            subject: clean.subject, bucket: clean.bucket, topic: clean.topic,
                        }));
                    } else {
                        localStorage.removeItem(LS_FILTER);
                    }
                } catch (e) {}
            }
            return clean;
        } catch (e) { return null; }
    }
    function normalizeFilter(f) {
        const subject = SUBJECTS.includes(f.subject) ? f.subject : "";
        const bucket = BUCKETS.includes(f.bucket) ? f.bucket : "";
        // topic：考点前缀（如 408-OS），供复盘页「按错因去专项练习」用。
        // ⚠️ 本文件是普通 Python 字符串，正则里的 \\u、\\d 必须双写，
        //    否则 Python 会先把 \\u4e00 解释成汉字「一」，正则就废了。
        const raw = String(f.topic || "");
        const topic = /^[0-9A-Za-z\\u4e00-\\u9fa5-]{1,40}$/.test(raw) ? raw : "";
        // ids：精确点名几张卡（2026-09-22）。薄弱点学习页让 AI 手写的题先在服务端
        // 归卡，拿到 card_id 后用这一条**只练这几张**——不走智能组题，也就不受
        // 每日新卡/复习额度影响（服务端按 mode=browse 处理，与筛选页自选同一条通道）。
        // 只认卡号形态、最多 40 个：这段最后会拼进 URL，不能放任何来路不明的字符。
        const ids = (Array.isArray(f.ids) ? f.ids : [])
            .map(function (x) { return String(x == null ? "" : x); })
            .filter(function (x) { return /^[A-Za-z0-9_-]{1,40}$/.test(x); })
            .slice(0, 40);
        return (subject || bucket || topic || ids.length)
            ? { subject: subject, bucket: bucket, topic: topic, ids: ids } : null;
    }
    // 只有「范围型」条件（科目 / 状态桶 / 考点）值得跨组、跨天保留。
    // ids 是点名，**不落盘** —— 理由见 readFilter 里那段。
    function persistFilter() {
        try {
            const f = state.filter;
            if (f && (f.subject || f.bucket || f.topic)) {
                localStorage.setItem(LS_FILTER, JSON.stringify({
                    subject: f.subject, bucket: f.bucket, topic: f.topic,
                }));
            } else {
                localStorage.removeItem(LS_FILTER);
            }
        } catch (e) {}
    }
    function applyFilter(f) {
        state.filter = f ? normalizeFilter(f) : null;
        persistFilter();
        renderFilterBar();
    }
    /**
     * 把「就练这几张」的点名（ids）从当前筛选里摘掉，其余范围条件留着。
     *
     * 「新开一组」的语义是**真的新挑一组**，不是把上一次点名的那几张再端回来。
     * 不摘的话，昨天点名的 10 张会一直挂在 state.filter 上：之后每次
     * 「开始学习 / 重开一组 / 再来一组」都是同一批卡——既不出新卡，
     * 设置里那个「今日刷完后再来 N 张」也永远轮不到它管（用户 2026-09-20 报的 bug）。
     */
    function dropIdFilter() {
        if (!state.filter || !state.filter.ids || !state.filter.ids.length) return false;
        state.filter = normalizeFilter({
            subject: state.filter.subject, bucket: state.filter.bucket, topic: state.filter.topic,
        });
        persistFilter();
        renderFilterBar();
        return true;
    }
    // 筛选页点「开始」走这里；也供卡片头部的科目快捷入口复用
    function startWithFilter(f) {
        applyFilter(f);
        clearSession();
        gateShowing = false;
        startStudy();          // 明确点了「开始刷题」= 同时开始计时
        loadSession(true);
    }
    // ---- 「再来一组」的张数（设置 → 每日闪卡额度 → 今日刷完后再来 N 张）----
    // 头部 🔁 按钮上直接把这个数写出来：不然「我改了设置到底生效没有」只能靠猜。
    let extraCount = 0;
    // 标签要短：卡片头部本来就挤（统计 / 重开一组），窄窗口下多两个字就换行了。
    function extraBtnLabel() {
        return extraCount > 0 ? ("🔁 再来 " + extraCount + " 张") : "🔁 再来一组";
    }
    function syncExtraLabel() {
        const b = document.getElementById("fs-extra-now");
        if (b) b.textContent = extraBtnLabel();
    }
    function loadExtraCount() {
        fetch(API + "/api/settings").then(r => r.json()).then(function (d) {
            const n = parseInt((d && d.review && d.review.flash_extra_count) || 0, 10);
            if (n > 0) { extraCount = n; syncExtraLabel(); }
        }).catch(function () {});
    }
    // 设置页保存后立刻跟着改口径（跨 IIFE 走自定义事件，与 keys-changed 一个套路）：
    // 他在设置里把 10 改成 20，回到闪卡页不用刷新，头部按钮就该写 20 张。
    document.addEventListener("kaoyan:extra-count", function (ev) {
        const n = parseInt(ev && ev.detail, 10);
        if (n > 0) { extraCount = n; syncExtraLabel(); }
    });
    // 暴露到 globalThis 而不是 window：test_flash_keyboard.js 用
    // new Function("document","localStorage","fetch", js) 注入执行，
    // 那个作用域里没有 window，写 window.xxx 会让 47 项测试全炸。
    globalThis.__sfx = SFX;   // 给测试盯着「该响的地方真的响了吗」
    globalThis.__flashApplyFilter = applyFilter;
    globalThis.__flashStart = startWithFilter;
    // ---- 离开闪卡时把整份进度推给服务端（2026-09-21）----
    // 翻页只走轻量的 position；**切子页 / 切到后台 / 关页面**时推整份，
    // 这样另一台设备（平板↔电脑）下次打开能接到同一组的同一位置。
    function wireProgressSync() {
        if (globalThis.__flashSyncWired) return;
        globalThis.__flashSyncWired = true;
        document.addEventListener("visibilitychange", function () {
            if (document.hidden && state.cards.length) pushSessionNow();
        });
        window.addEventListener("beforeunload", function () {
            if (state.cards.length) pushSessionNow();
        });
        window.addEventListener("hashchange", function () {
            let h = "";
            try { h = String(location.hash || ""); } catch (e) {}
            if (h.indexOf("flash") < 0 && state.cards.length) pushSessionNow();
        });
    }
    wireProgressSync();
    // 复盘页/学习区的 AI 回复同样是 Markdown+LaTeX，直接复用本区已调好的
    // mdTex（表格、KaTeX、代码块都认），不再写第二套渲染器。
    globalThis.__mdTex = mdTex;
    // 卡片正文/题库自带 explanation 走的是 richText（只认 $公式$，不认 Markdown），
    // 暴露出来给测试盯着——用户截图反馈过「却**是**拐点」这种裸露星号。
    globalThis.__richText = richText;

    // 一组为空绝大多数情况是「今日额度用完」或「没有到期的卡」，而不是题库没卡。
    // 原先一律显示「题库暂无卡片」，会误导人往库里加卡（2026-09-20）。
    function emptySessionHtml(d) {
        const L = d.limits || {}, C = d.counts || {};
        const bits = [];
        if (L.remaining_new === 0 && L.new_done != null) bits.push('新卡 ' + L.new_done + '/' + L.new_per_day + ' 已用完');
        if (L.remaining_review === 0 && L.review_done != null) bits.push('复习 ' + L.review_done + '/' + L.reviews_per_day + ' 已用完');
        if (C.due === 0 && L.remaining_review > 0) bits.push('暂无到期的复习卡');
        const why = bits.length ? ('（' + bits.join('；') + '）') : '';
        return '<div class="fs-empty">今日计划已完成 ' + why
            + '<br><span style="font-size:0.75rem;color:var(--text-muted);line-height:1.8;">'
            + '想接着练就点下面的「再来一组」：数量在「设置 → 每日闪卡额度 → 今日刷完后再来」里调'
            + '（默认 10 张，现在设的是 ' + (extraCount > 0 ? extraCount + ' 张' : '10 张') + '），'
            + '<b>优先级还是薄弱/到期/学习中的卡，所以有没复习完的会先复习</b>；'
            + '也可以去「闪卡库」按科目/状态自选。</span>'
            + '<div style="margin-top:12px;"><button class="fs-btn primary" id="fs-extra">'
            + '▶ 再来一组（优先没复习完的）</button></div></div>';
    }

    // ============================================================
    // 「开始」闸门（2026-09-21）
    // 打开大盘就自动组题、自动计时的老做法有两个毛病：一是页面一挂上就向服务端
    // 拉一整组卡（哪怕人不打算刷），二是人还没开始、计时已经在跑。改成进页面先
    // 停在闸门上：看得见今日额度与未完成的本组，点一下才组题 + 起表。
    // ============================================================
    let gateShowing = false;
    function gateLine() {
        const info = state.todayInfo || {};
        const bits = [];
        const f = state.filter ? normalizeFilter(state.filter) : null;
        bits.push("范围 " + (f
            ? [f.subject, f.bucket, f.topic && ("考点 " + f.topic)].filter(Boolean).join(" · ")
            : "智能组题（薄弱 > 到期 > 冷笔记）"));
        if (info.due_today != null) bits.push("到期 " + info.due_today + " 张");
        if (info.reviewed_today != null) bits.push("今日已复习 " + info.reviewed_today + " 张");
        bits.push("今日专注 " + fmtClock(STUDY.dayMs));
        return bits.join(" · ");
    }
    function renderGate() {
        gateShowing = true;
        // 先按本地缓存画一版（离线也能用），同时问服务端「有没有没刷完的那一组」——
        // 服务端那份是所有端共用的，比本机缓存更可信（换设备 / 清过缓存都能接上）。
        const saved = readSaved();
        paintGate(saved, false, null);
        fetch(API + "/api/flashcards/session?peek=1").then(r => r.json()).then(d => {
            if (!gateShowing) return;
            const s = d && d.ok ? d.saved : null;
            if (s && s.total) paintGate({ cards: new Array(s.total), idx: s.idx }, true, s);
            else if (!saved) paintGate(null, false, null);
        }).catch(function () {});
    }
    /**
     * 画闸门。saved 为 null 就是「今天还没开始」。
     * fromServer=true 表示这份进度来自服务端（多端共享），文案里说明一下。
     */
    function paintGate(saved, fromServer, serverSaved) {
        const total = saved ? saved.cards.length : 0;
        const left = saved ? (total - saved.idx) : 0;
        box.innerHTML = '<div class="fs-gate">'
            + '<div class="fs-gate-icon"></div>'
            + '<div class="fs-gate-title">' + (saved ? "继续本组？" : "开始这一组闪卡") + '</div>'
            + '<div class="fs-gate-line">' + esc(gateLine()) + '</div>'
            + (saved
                // 不再报「从第 N 张起」：服务端 resume 交回来的就是「剩下的那些」，
                // 位置恒为第 1 张（见 serve.js 的 remainingCards）。以前那句数字是
                // 按没剔过的列表算的，报出来还会虚高，反而让人以为要跳过几张没答的。
                ? '<button class="fs-btn fs-gate-go" id="fs-gate-resume">▶ 继续本组（还剩 ' + left + ' 张）</button>'
                  + '<button class="fs-btn fs-gate-alt" id="fs-gate-new">↺ 放弃，重新挑一组</button>'
                : '<button class="fs-btn fs-gate-go" id="fs-gate-start">▶ 开始学习</button>')
            + (fromServer && serverSaved && serverSaved.updated
                ? '<div class="fs-gate-hint">进度存在服务端、多设备共用'
                  + (serverSaved.device ? '（上次由' + esc(serverSaved.device) + '更新）' : '') + '。</div>'
                : '')
            + '<div class="fs-gate-hint">点了开始才计时；切到别的窗口或标签页会自动暂停，回来自动继续。</div>'
            + '</div>';
        const rs = document.getElementById("fs-gate-resume");
        const st = document.getElementById("fs-gate-start");
        const nw = document.getElementById("fs-gate-new");
        const goOn = () => { gateShowing = false; startStudy(); renderCard(); };
        const goNew = () => startNewGroup();
        if (rs) rs.onclick = () => {
            SFX.unlock();
            // 服务端那份优先：它剔掉了「今天已经答过」的卡（别的设备答的也算）
            resumeFromServer().then(function (okResume) {
                if (okResume) goOn();
                else if (tryResume()) goOn();
                else goNew();
            });
        };
        if (st) st.onclick = () => { SFX.unlock(); goNew(); };
        // 「放弃，重新挑一组」也要摘掉点名：不然「重新挑」挑回来的还是刚才那几张
        if (nw) nw.onclick = () => { SFX.unlock(); clearSession(); goNew(); };
        paintStudy();
    }
    /** 从服务端取回当日那一组（已剔除今天答过的卡）。成功返回 true。 */
    async function resumeFromServer() {
        try {
            const r = await fetch(API + "/api/flashcards/session?resume=1");
            const d = await r.json();
            if (!d || !d.ok || !Array.isArray(d.cards) || !d.cards.length) return false;
            state.cards = d.cards;
            state.idx = Math.max(0, Math.min(Number(d.idx) || 0, d.cards.length));
            state.stats = { 1: 0, 2: 0, 3: 0, 4: 0 };
            state.lastRating = null;
            state.lastRatedIdx = null;
            state.autoWrong = false; state.saveFailed = false; pendingSubmit = null;
            state.filter = (d.filter && typeof d.filter === "object") ? normalizeFilter(d.filter) : readFilter();
            state.groupMode = (d.filter && d.filter.mode) || null;
            pushedOnce = false;        // 这一组重新提交一次，服务端与本地保持一致
            saveSession();
            return true;
        } catch (e) { return false; }
    }
    // 组题失败/额度用尽 → 把表停下来，别让人替「今天没有卡可刷」付时间。
    function renderBlocked(html) {
        gateShowing = false;
        endStudy();
        box.innerHTML = html
            + '<div class="fs-gate-back"><button class="fs-btn" id="fs-gate-back">返回</button></div>';
        const b = document.getElementById("fs-gate-back");
        if (b) b.onclick = renderGate;
    }

    /**
     * 「新开一组」：先摘掉点名（ids），再真的新挑一组。
     *
     * 三个入口共用同一条：闸门的「开始学习 / 放弃·重新挑一组」、卡片头的「重开一组」、
     * 组末总结的「再来一组」。以前它们各自调 loadSession(true)，而 loadSession 会照着
     * state.filter 组题——点名还在，于是「新」开出来的还是那几张。
     *
     * autoExtra：智能组因为今日额度用完而空时，直接接上「再来一组」（设置里那个张数）。
     * 这是他**自己点了**「重开一组 / 再来一组」的动作，不该停在一个空屏上让他再点第二次；
     * 首次「开始学习」不带这个开关，还会照旧把「今日计划已完成」说清楚。
     */
    function startNewGroup(opts) {
        dropIdFilter();
        gateShowing = false;
        startStudy();
        loadSession(true, null, opts);
    }
    /** 「再来一组」：走 extra 通道，张数由服务端按设置里的 flash_extra_count 发。 */
    function startExtra() {
        SFX.unlock();
        gateShowing = false;
        startStudy();
        loadSession(true, "extra");
    }

    async function loadSession(fresh, mode, opts) {
        if (fresh) clearSession();
        // 走主闪卡库这条路 = 退出本地卡组身份（早间回顾那一组没刷完的话仍在
        // localStorage 里，回「早」页还能「继续本组」）。
        state.local = null;
        // 「再来一组」是今日额度之外的一组，点名的那几张更不该跟过来
        // （否则这一组会被推给服务端当 filter 存下来，下次「继续本组」又把它捞回来）
        if (mode === "extra") dropIdFilter();
        box.innerHTML = '<div class="fs-loading">正在为你挑选针对性闪卡…</div>';
         // 防御性 normalize：任何入口残留的异常 filter（空对象/残缺值）都在此收敛为 null，
        // 避免误触发 browse 模式导致每日额度完全不生效。
        const effectiveFilter = state.filter ? normalizeFilter(state.filter) : null;
        if (effectiveFilter !== state.filter) state.filter = effectiveFilter;
        // 指定卡号（AI 手写题归卡后马上练）：一次最多 40 张，limit 得跟着抬起来，
        // 否则服务端按 limit 截断，人点「练这几张」却只拿到前 30 张。
        const idFilter = (effectiveFilter && Array.isArray(effectiveFilter.ids))
            ? effectiveFilter.ids : [];
        // 智能组题：一次把「今日额度内」的卡全部取回，一组=当日计划量，
        // 不再写死 30（额度才是决定数量的因素，服务端会自动收敛到剩余额度）。
        // 自选（browse）模式不受额度约束，50 张足够挑。
        let url = API + "/api/flashcards/session?limit="
            + (effectiveFilter ? Math.max(50, idFilter.length) : 200);
        if (mode === "extra") {
            // 今日额度刷完后的「再来一组」：数量取设置里的 flash_extra_count（默认 10），
            // 同样不受额度限制，但优先级排序不变 → 有没复习完的会先复习。
            url = API + "/api/flashcards/session?mode=extra";
        } else if (effectiveFilter) {
            if (effectiveFilter.subject) url += "&subject=" + encodeURIComponent(effectiveFilter.subject);
            if (effectiveFilter.bucket) url += "&bucket=" + encodeURIComponent(effectiveFilter.bucket);
            // 考点前缀：复盘页「按错因去专项练习」靠它把范围收到一个考点上
            if (effectiveFilter.topic) url += "&topic=" + encodeURIComponent(effectiveFilter.topic);
            // 点名要哪几张卡（学习页「练这几张」）：与服务端 ?ids= 一一对应
            if (idFilter.length) url += "&ids=" + encodeURIComponent(idFilter.join(","));
            url += "&mode=browse";
        }
        try {
            const resp = await fetch(url);
            const data = await resp.json();
            if (!data.ok) {
                renderBlocked('<div class="fs-empty">⚠ 选题失败：' + esc(data.error || ('HTTP ' + resp.status)) + '</div>');
                return;
            }
            if (!data.cards || data.cards.length === 0) {
                // 今日额度用完时智能组必然为空。用户点的若是「重开一组 / 再来一组」，
                // 他要的就是「再来一组」那一组（张数＝设置里那个数）——直接接上，
                // 别再让他对着一个空屏点第二次（见 startNewGroup 的 autoExtra）。
                if (opts && opts.autoExtra && mode !== "extra") { loadSession(true, "extra"); return; }
                renderBlocked(emptySessionHtml(data));
                // 「再来一组」：走 extra 模式（不受每日额度限制，数量取设置里的 flash_extra_count）
                const ex = document.getElementById("fs-extra");
                if (ex) ex.onclick = startExtra;
                return;
            }
            state.cards = data.cards; state.idx = 0;
            // 这一组是哪条通道来的（服务端会回显）：extra 组要在卡片上写明来路——
            // 他设了 20 张，得让他看得见「就是 20 张」。
            state.groupMode = (data.filter && data.filter.mode) || (effectiveFilter ? "browse" : "smart");
            state.stats = {1: 0, 2: 0, 3: 0, 4: 0};
            state.limits = data.limits || null;
            state.counts = data.counts || null;
            // 组题策略回执（2026-09-19）：今天因「连对够多」被收起来几张。
            // 用户问过「那张卡怎么不见了」，答案就在这儿——不报出来他就会以为卡丢了。
            state.policy = data.policy || null;
            state.lastRating = null;
            state.lastRatedIdx = null;
            state.autoWrong = false; state.saveFailed = false; pendingSubmit = null;
            saveSession();
            renderCard();
        } catch (e) {
            renderBlocked('<div class="fs-empty">⚠️ 无法连接本地复习服务。<br>请关闭本页，改用桌面上的「考研大盘」快捷方式打开（它会自动启动服务）。</div>');
        }
    }

    function badge(c) {
        if (c.leech) return '<span class="fs-badge leech">水蛭卡 · 错' + c.lapses + '次</span>';
        if (c.state === 1 || c.state === 3) {
            const step = (c.learning_step || c.relearning_step || 0) + 1;
            const tag = c.state === 3 ? "再学习" : "学习中";
            return '<span class="fs-badge learn">' + tag + ' · 第' + step + '步</span>';
        }
        if (c.lapses > 0) return '<span class="fs-badge weak">薄弱 · 错' + c.lapses + '次</span>';
        if (c.state === 0) return '<span class="fs-badge recent">新卡</span>';
        return '<span class="fs-badge due">待复习</span>';
    }

    // 头部额度条：新卡 a/b · 待复习 c/d · 学习中 e
    function limitsHtml() {
        const L = state.limits, C = state.counts;
        if (!L) return "";
        const parts = [];
        if (C) parts.push('新卡 ' + (L.new_done) + '/' + L.new_per_day + '（可抽 ' + C.new + '）');
        if (C) parts.push('待复习 ' + L.review_done + '/' + L.reviews_per_day + '（到期 ' + C.due + '）');
        if (C && C.learning) parts.push('学习中 ' + C.learning);
        if (C && C.leech) parts.push('水蛭 ' + C.leech);
        return '<span class="fs-limits">' + parts.map(esc).join(' · ') + '</span>';
    }

    // 一张卡的可选项：只有选择/判断有。判断题库里 answer 是布尔，选项由前端补成 正确/错误。
    function optionsOf(c) {
        const ct = (c || {}).content || {};
        if (c.type === "choice" && Array.isArray(ct.options) && ct.options.length > 0) return ct.options;
        if (c.type === "judge") return (Array.isArray(ct.options) && ct.options.length) ? ct.options : ["正确", "错误"];
        return [];
    }

    function optionButtons() {
        return Array.prototype.slice.call(document.querySelectorAll("#fs-body .fs-opt"));
    }

    // 底部快捷键提示随作答阶段变化：未答 → 选选项；答对 → 自评；答错 → 只等翻页
    function updateHint() {
        const el = box.querySelector(".fs-hint");
        if (!el) return;
        const c = state.cards[state.idx] || {};
        const opts = optionsOf(c);
        // 键名一律从快捷键总表取（见 KEYS_JS）：用户在设置里改了键，这六条提示
        // 自动跟着变。写死「空格 / U / F」的话，一改键提示就开始教人按错的。
        // ⚠️ 这三个必须在 if 外面取——下面 autoWrong / 自评两个分支也要用。
        const KS = __keys;
        const SP = KS.prettyAll("flash.reveal");   // 空格 / 回车
        const UD = KS.keysOf("flash.undo");        // U
        const FS = KS.keysOf("flash.full");        // F
        if (!state.revealed) {
            if (state.local) {
                // 本地卡组（早间回顾）：只有「翻卡看原文 → 自评」两步
                el.textContent = "快捷键：" + SP + " 翻卡看原文 · " + UD + " 撤销 · " + FS + " 全屏";
            } else if (c.type === "short") {
                el.textContent = "快捷键：Ctrl+Enter 提交批改 · " + SP + " 先看参考答案 · "
                    + UD + " 撤销 · " + FS + " 全屏";
            } else if (!opts.length) {
                el.textContent = "快捷键：" + SP + " 显示答案 · " + UD + " 撤销上一张 · " + FS + " 全屏";
            } else if (c.type === "judge") {
                el.textContent = "快捷键：1 正确 · 2 错误 · " + SP + " 显示答案 · " + FS + " 全屏";
            } else {
                el.textContent = "快捷键：A–D 或 1–4 选选项 · " + SP + " 显示答案 · " + FS + " 全屏";
            }
        } else if (state.autoWrong) {
            el.textContent = "已记「忘记」· " + SP + " 下一张 · " + UD + " 撤销重答 · " + FS + " 全屏";
        } else {
            el.textContent = "自评：2 模糊 · 3 记得 · 4 简单（" + SP + " = 记得）· "
                + UD + " 撤销 · " + FS + " 全屏";
        }
    }

    // 键位总表改了（设置页里点按键改的）就重算一次提示文案。不刷新的话，
    // 用户改完键回到闪卡页，底部还在教他按旧键——比没有提示更坑。
    // 总表派发的是 document 上的自定义事件，这边不依赖 KEYS_JS 的任何内部状态。
    document.addEventListener("kaoyan:keys-changed", function () {
        try { updateHint(); } catch (e) {}
    });

    function renderCard() {
        if (state.idx >= state.cards.length) { renderSummary(); return; }
        const c = state.cards[state.idx];
        const ct = c.content || {};
        state.revealed = false; state.answered = false;
        state.autoWrong = false; state.saveFailed = false; pendingSubmit = null;
        resetTexStore();   // 上一张的 tex 登记随 DOM 一起作废，避免无限累积
        state.short = null;   // 简答作答/批改结果随卡走
        state.chosen = null;  // 本卡学生选的那一项，评分时随 review_log 落库

        let html = '';
        if (state.local) {
            // 本地卡组（早间回顾）：头部只留「第几张 + 出处」，其余全是主闪卡库的东西
            // （额度条 / 统计 / 重开一组 / 再来 N 张 / 待修与问过徽标）——这里一个都不该有。
            html = '<div class="fs-head">'
                + '<span class="fs-progress">第 ' + (state.idx + 1) + ' / ' + state.cards.length + ' 张</span>'
                + '<span class="fs-badge local">' + esc(c.sec_label || "早间回顾") + '</span>'
                + '<span class="fs-topic fs-local-title">' + esc(state.local.title || "") + '</span>'
                + '</div>';
        } else {
            html = '<div class="fs-head">'
            + '<span class="fs-progress">第 ' + (state.idx + 1) + ' / ' + state.cards.length + ' 张</span>'
            + '<span class="fs-badge due">今日已复习 ' + (state.reviewedToday || 0) + '</span>'
            + badge(c)
            + limitsHtml()
            + '<span class="fs-topic">' + (c.subject
                ? '<button class="fs-topic-btn" id="fs-topic-btn" title="只看 ' + esc(c.subject) + '">'
                  + esc(c.subject) + '</button> · '
                : '') + esc(c.topic_name) + '</span>'
            // 待修徽标：服务端在 session 里带下来的 open 标记（只有自选/指定通道才取得到
            // 带标记的卡，智能组已经把这类卡排除了）。放在「统计」左边，别挤走右边的按钮。
            + (c.report ? '<span class="fs-report-badge" title="'
                + esc('你标记过：' + c.report.kind_label
                      + (c.report.note ? '（' + c.report.note + '）' : '')
                      + '｜AI 会在每日任务里核对修复')
                + '">⚑ 待修 · ' + esc(c.report.kind_label) + '</span>' : '')
            // 📌 钉住徽标（2026-09-19 用户要求）：钉住的卡会一直排在智能组前面，
            // 所以这个徽标同时也是「为什么这张卡我答对了还老是见到它」的答案。
            + (c.pin ? '<span class="fs-pin-badge" title="'
                + esc('你钉住了这张卡｜它会一直排在智能组前面，也不会因为连对几次被收起来'
                      + (c.pin.note ? '（' + c.pin.note + '）' : ''))
                + '">📌 已钉住</span>' : '')
            // 追问过 AI 的卡：标出来（这类卡不会因连对被收起来）。
            // ⚠️ 但它**有出口**：提问之后又连对够多次，「问过」的身份就作废
            // （用户 2026-09-19：「如果没有退出机制，我问过的就永远优先级都高了」）。
            // 所以徽标副标题把进度写出来，让他看得见「再连对一次它就退出」。
            + ((c.ask_count || 0) > 0 && !c.pin ? '<span class="fs-ask-badge" title="'
                + esc('你对这张卡向 AI 追问过 ' + c.ask_count + ' 次｜这类卡不会因为连对几次被收起来，'
                      + '但提问之后只要又连对 ' + ((state.policy && state.policy.ask_exit_streak) || 3)
                      + ' 次就会退出这个待遇（当前已连对 ' + (c.ask_after_correct || 0) + ' 次）')
                + '">💬 问过 ' + c.ask_count
                + ((c.ask_after_correct || 0) > 0
                   ? ' · 已连对 ' + c.ask_after_correct + '/'
                     + ((state.policy && state.policy.ask_exit_streak) || 3) : '')
                + '</span>' : '')
            + '<button class="fs-btn" id="fs-stats-btn" style="margin-left:auto;padding:2px 10px;font-size:0.7rem;">📊 统计</button>'
            // 🔁 再来一组：把设置里那个张数直接写在按钮上（2026-09-20）。
            // 以前只有「今日额度用完」的空状态里才有这个按钮，他设了 20 张也看不到、
            // 点不到，只能怀疑「设置没用」。现在随时可点，张数一目了然。
            + '<button class="fs-btn" id="fs-extra-now" title="'
                + esc('「今日刷完后再来 N 张」那一组（设置 → 每日闪卡额度）。'
                      + '不受每日额度限制，排序不变——薄弱 / 到期 / 学习中的卡先来。')
                + '" style="padding:2px 10px;font-size:0.7rem;">' + esc(extraBtnLabel()) + '</button>'
            + '<button class="fs-btn" id="fs-restart" title="'
                + esc('按当前范围、在今日额度内再挑一组（点名的卡不会跟过来）。'
                      + '今日额度用完了，它会自动接上左边的「🔁 再来 N 张」。')
                + '" style="padding:2px 10px;font-size:0.7rem;">重开一组</button>'
            + '</div>'
            // 「这一组是哪来的」：extra 组要说清楚，否则他只会觉得「我设了 20，
            // 怎么还是这么几张」——设置生效了，得让他看得见。
            + (state.groupMode === "extra"
                ? '<div class="fs-policy-hint">🔁 这一组是「今日刷完后再来'
                  + (extraCount > 0 ? " " + extraCount + " 张" : "一组")
                  + '」（设置 → 每日闪卡额度）：<b>不受每日额度限制</b>，'
                  + '优先级不变——薄弱 / 到期 / 学习中的卡先来。</div>'
                : '')
            + '<div id="fs-stats-panel" class="fs-stats-panel" data-open="0"></div>'
            // 组题策略说明：让他知道「本组为什么是这些卡」。只有真的收起了卡才显示，
            // 且计数为 0 时一个字都不提（不喊狼来了）。
            + ((state.policy && state.policy.applied && state.policy.retired_count > 0)
                ? '<div class="fs-policy-hint">本组已收起 '
                  + state.policy.retired_count + ' 张「连对 ' + state.policy.retire_streak
                  + ' 次以上、又没问过我」的卡（📌 钉住的和 💬 问过的不收）；'
                  + '想看它们去「闪卡库 → 📌 钉住 / 已掌握」。</div>'
                : '');
        }
        // 填空题挖空（未揭晓时 {{c1::X}} → ______），揭晓后由 showFeedback 填回
        const stemShown = hasCloze(ct.stem) ? clozeText(ct.stem, false) : (ct.stem || ct.question || "");
        // 本地卡组（早间回顾）的正面是**数据里自带的 HTML 片段**（含 <strong>/<br>），
        // 只能原样插入 + 只做 ASCII 上下标；texWrap 会把它转义成源码。
        html += '<div class="fs-stem">' + (c.raw ? rawHtml(stemShown) : texWrap(stemShown)) + '</div>';
        // 已标记的卡：把「为什么标它」摆在题面下面。用户标记的初衷就是「别再让我按错的
        // 答案作答」，所以这里要说清三件事：标记了什么原因、AI 会处理、本组之后不再考它。
        if (c.report) {
            html += '<div class="fs-report-note">⚑ 这题你标记过有问题（'
                + esc(c.report.kind_label) + '）'
                + (c.report.note ? '：' + esc(c.report.note) : '')
                + '，已排进每日任务等 AI 核对修复；修好之前它不再进智能组。'
                + '<br>先按你自己的判断来的话，点下面的「⚑」可以改原因或补一句说明。</div>';
        }
        html += '<div id="fs-body"></div><div id="fs-feedback"></div>';
        box.innerHTML = html;
        const rs = document.getElementById("fs-restart");
        // 「重开一组」= 摘掉点名再新挑（智能组空了会自动接上「再来一组」）
        if (rs) rs.onclick = () => startNewGroup({ autoExtra: true });
        const exb = document.getElementById("fs-extra-now");
        if (exb) exb.onclick = startExtra;
        const sb = document.getElementById("fs-stats-btn");
        if (sb) sb.onclick = () => showStats();
        // 点头部的科目名 = 只看这一科（单科复习的快捷入口）
        const tb = document.getElementById("fs-topic-btn");
        if (tb) tb.onclick = () => startWithFilter({ subject: c.subject, bucket: "" });

        const body = document.getElementById("fs-body");
        const isJudge = c.type === "judge";
        const opts = optionsOf(c);

        // 简答题：作答区（文字 + 手写照片）替代选项按钮
        if (c.type === "short") {
            body.innerHTML = shortHtml();
            bindShort(body, c, ct);
            const hint = document.createElement("div");
            hint.className = "fs-hint";
            box.appendChild(hint);
            updateHint();
            return;
        }

        if (opts.length) {
            opts.forEach((opt, i) => {
                const btn = document.createElement("button");
                btn.className = "fs-opt";
                // 键位徽章：选择显示 A/B/C/D，判断显示 1/2（判断无字母，数字更好按）
                const key = isJudge ? String(i + 1) : String.fromCharCode(65 + i);
                btn.innerHTML = '<span class="fs-key">' + key + '</span>' + texWrap(opt);
                btn.onclick = () => answerChoice(i, opts, ct);
                body.appendChild(btn);
            });
        } else {
            const btn = document.createElement("button");
            btn.className = "fs-btn";
            // 本地卡组是「翻转卡」：说「翻卡」比「显示答案」贴切
            btn.textContent = c.raw ? "翻卡看原文（空格）" : "显示答案（空格）";
            btn.onclick = reveal;
            body.appendChild(btn);
        }
        const hint = document.createElement("div");
        hint.className = "fs-hint";
        box.appendChild(hint);
        updateHint();
    }

    // 判断题的 answer 在库中是**布尔**（true=正确 / false=错误），对应 opts 为 ["正确","错误"]。
    // ⚠️ 2026-09-13 修复：此前布尔会掉进字符串分支 → String(false)="false" → 既不是 A-D
    //    也匹配不到任何选项 → 返回 -1，连锁导致：正确项永不高亮、用户点任何选项都被标红、
    //    答案文本显示成字面量 "false"。政治卡以判断/填空为主，此 bug 必须先修。
    function correctIndex(ct, opts) {
        if (typeof ct.answer === "boolean") return ct.answer ? 0 : 1;
        if (typeof ct.answer === "number") return ct.answer;
        const a = String(ct.answer == null ? "" : ct.answer).trim();
        // 兼容历史数据里以字符串形式存的布尔答案
        if (/^(true|false)$/i.test(a)) return /^true$/i.test(a) ? 0 : 1;
        if (a === "正确" || a === "对") return 0;
        if (a === "错误" || a === "错") return 1;
        const single = /^[A-Da-d]$/.test(a) ? a.toUpperCase().charCodeAt(0) - 65 : -1;
        if (single >= 0 && single < opts.length) return single;
        const found = opts.findIndex(o => String(o).trim() === a);
        if (found >= 0) return found;
        return -1;
    }

    // 给选项上色并锁死。picked 传 -1 表示「没作答，直接看了答案」。
    function paintOptions(ci, picked) {
        document.querySelectorAll("#fs-body .fs-opt").forEach((b, j) => {
            b.disabled = true;
            if (j === ci) b.classList.add("correct");
            if (j === picked && picked !== ci) b.classList.add("wrong");
        });
    }

    // 选择/判断的作答。答对 → 交给用户自评（2/3/4）；答错 → 直接判「忘记」，不再要求自评。
    function answerChoice(i, opts, ct) {
        if (state.answered) return;
        state.answered = true; state.revealed = true;
        const ci = correctIndex(ct, opts);
        paintOptions(ci, i);

        if (ci >= 0 && i === ci) { SFX.play("correct"); showFeedback(ct); return; }

        // 选错：立即按 Again(1) 回写，但卡片停在本页让用户看清正确答案与解析，
        // 空格/回车才翻页。回写失败则退回手动评分（见 showFeedback 的 saveFailed 分支）。
        SFX.play("wrong");
        state.autoWrong = true;
        showFeedback(ct);
        // 按**实际的错选**生成针对性解析（异步，不阻塞上面的反馈展示）。
        // 只在答错时触发：答对没有「错在哪」可讲，空跑既费 token 又没信息量。
        const pickedText = (opts && opts[i] != null)
            ? String.fromCharCode(65 + i) + ". " + opts[i] : "";
        state.chosen = pickedText;
        mountExplain(state.cards[state.idx] || {}, pickedText);
        pendingSubmit = submitRating(1).then(ok => {
            pendingSubmit = null;
            if (!ok) { state.autoWrong = false; state.saveFailed = true; showFeedback(ct); }
        });
    }

    // 空格直接看答案（放弃作答）。选择/判断也要把正确项标出来，否则只看得到解析。
    function reveal() {
        if (state.revealed) return;
        state.revealed = true;
        const c = state.cards[state.idx] || {};
        const ct = c.content || {};
        const opts = optionsOf(c);
        if (opts.length) paintOptions(correctIndex(ct, opts), -1);
        SFX.play("reveal");
        showFeedback(ct);
    }

    // ============================================================
    // 简答题（type === "short"）：文字作答 + 手写照片 → DeepSeek 批改
    //   批改走服务端 /api/grade（密钥只在服务端，不下发到浏览器）。
    //   图片先在本地 canvas 压缩：手机原图 3-5MB，直接 base64 上传会让请求体
    //   和模型 token 都爆掉，压到最长边 1280 / JPEG q0.82 后通常 <300KB。
    // ============================================================
    const SHORT_MAX_SIDE = 1280;
    const SHORT_QUALITY = 0.82;

    function shortState() {
        if (!state.short) state.short = { text: "", image: null, meta: "", grading: false, grade: null, error: "" };
        return state.short;
    }

    function downscaleImage(file) {
        return new Promise((resolve, reject) => {
            if (!file || !/^image\//.test(file.type || "")) { reject(new Error("不是图片文件")); return; }
            const fr = new FileReader();
            fr.onerror = () => reject(new Error("读取图片失败"));
            fr.onload = () => {
                const img = new Image();
                img.onerror = () => reject(new Error("图片解码失败（换一张试试）"));
                img.onload = () => {
                    const scale = Math.min(1, SHORT_MAX_SIDE / Math.max(img.width, img.height));
                    const w = Math.max(1, Math.round(img.width * scale));
                    const h = Math.max(1, Math.round(img.height * scale));
                    const cv = document.createElement("canvas");
                    cv.width = w; cv.height = h;
                    cv.getContext("2d").drawImage(img, 0, 0, w, h);
                    resolve({ dataUrl: cv.toDataURL("image/jpeg", SHORT_QUALITY), w: w, h: h });
                };
                img.src = fr.result;
            };
            fr.readAsDataURL(file);
        });
    }

    // 贴图 / 选图 / 拖图都汇到这里
    async function attachShortImage(file) {
        const st = shortState();
        try {
            const r = await downscaleImage(file);
            st.image = r.dataUrl;
            st.meta = r.w + "×" + r.h + " · " + Math.round(r.dataUrl.length / 1365) + "KB";
            st.error = "";
        } catch (e) {
            st.error = e.message;
        }
        renderShortImage();
    }

    function renderShortImage() {
        const box = document.getElementById("fs-imgbox");
        if (!box) return;
        const st = shortState();
        box.innerHTML = st.image
            ? '<img src="' + st.image + '" alt="手写答案预览">'
              + '<div class="fs-img-meta"><span>' + esc(st.meta) + '</span>'
              + '<button class="fs-btn" id="fs-img-del" style="padding:1px 8px;font-size:0.7rem;">移除图片</button></div>'
            : "";
        const del = document.getElementById("fs-img-del");
        if (del) del.onclick = () => { shortState().image = null; shortState().meta = ""; renderShortImage(); };
        const err = document.getElementById("fs-short-err");
        if (err) err.textContent = st.error || "";
    }

    function shortHtml() {
        return '<div class="fs-short">'
            + '<textarea id="fs-answer" class="fs-textarea" placeholder="在这里作答（支持 $...$ 公式）…也可以直接 Ctrl+V 粘贴手写答案照片"></textarea>'
            + '<div class="fs-imgrow">'
            + '<input type="file" id="fs-file" accept="image/*" capture="environment" style="display:none">'
            + '<button class="fs-btn" id="fs-pick">📷 上传照片</button>'
            + '<button class="fs-btn" id="fs-ink">✍️ 手写</button>'
            + '<span class="fs-hint-img">支持拍照 / 手写板 / 粘贴 / 拖拽</span>'
            + '</div>'
            + '<div id="fs-imgbox" class="fs-imgbox"></div>'
            + '<div class="fs-actions">'
            + '<button class="fs-btn primary" id="fs-grade-btn">提交批改（Ctrl+Enter）</button>'
            + '<button class="fs-btn" id="fs-skip">先看参考答案</button>'
            + '</div>'
            + '<div class="fs-short-err" id="fs-short-err"></div>'
            + '</div>';
    }

    function bindShort(body, card, ct) {
        const st = shortState();
        const ta = document.getElementById("fs-answer");
        const file = document.getElementById("fs-file");
        const pick = document.getElementById("fs-pick");
        const gradeBtn = document.getElementById("fs-grade-btn");
        const skip = document.getElementById("fs-skip");

        ta.value = st.text || "";
        ta.oninput = () => { st.text = ta.value; };
        // Ctrl+Enter 提交（单独 Enter 留给换行）
        ta.addEventListener("keydown", (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && !(e.isComposing || e.keyCode === 229)) { e.preventDefault(); gradeShort(card, ct); }
        });
        // 粘贴图片
        ta.addEventListener("paste", (e) => {
            const items = (e.clipboardData && e.clipboardData.items) || [];
            for (const it of items) {
                if (it.kind === "file" && /^image\//.test(it.type)) {
                    e.preventDefault();
                    attachShortImage(it.getAsFile());
                    return;
                }
            }
        });
        // 拖拽图片到作答区
        const zone = body.querySelector(".fs-short");
        if (zone) {
            zone.addEventListener("dragover", (e) => { e.preventDefault(); });
            zone.addEventListener("drop", (e) => {
                const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
                if (f) { e.preventDefault(); attachShortImage(f); }
            });
        }
        pick.onclick = () => file.click();
        const inkBtn = document.getElementById("fs-ink");
        if (inkBtn) inkBtn.onclick = () => openInkPad();
        file.onchange = () => { if (file.files && file.files[0]) attachShortImage(file.files[0]); };
        gradeBtn.onclick = () => gradeShort(card, ct);
        skip.onclick = () => { st.text = ta.value; reveal(); };
        renderShortImage();
    }

    // AI 批改结果面板
    function gradeHtml(g) {
        const cls = g.score >= 75 ? "ok" : (g.score >= 45 ? "mid" : "bad");
        let h = '<div class="fs-grade"><div class="fs-grade-head">'
            + '<span class="fs-score">' + (g.score == null ? "—" : g.score) + '</span><span>分</span>'
            + '<span class="fs-verdict ' + cls + '">' + esc(g.verdict || "已批改") + '</span>'
            + '<span style="font-size:0.72rem;color:var(--text-muted);">AI 批改 · 建议 '
            + g.suggested_rating + ' ' + LABELS[g.suggested_rating] + '</span>'
            + '</div>';
        if (g.transcription) h += '<div class="fs-trans"><b>手写辨认：</b>' + esc(g.transcription) + '</div>';
        if (g.hits && g.hits.length)
            h += '<div><b>命中得分点</b><ul>' + g.hits.map(x => '<li>' + esc(x) + '</li>').join("") + '</ul></div>';
        if (g.missed && g.missed.length)
            h += '<div><b>遗漏 / 答错</b><ul>' + g.missed.map(x => '<li>' + esc(x) + '</li>').join("") + '</ul></div>';
        if (g.feedback) h += '<div style="margin-top:4px;">' + esc(g.feedback) + '</div>';
        h += '</div>';
        return h;
    }

    // ---- 手写板：触屏/笔在画布上书写，导出为图片交给 AI 批改 ----
    function openInkPad() {
        const overlay = document.createElement("div");
        overlay.className = "fs-ink-pad";
        overlay.innerHTML =
            '<div class="fs-ink-paper">'
            + '<div class="fs-ink-bar">'
            + '  <span class="fs-ink-title">✍️ 手写答案</span>'
            + '  <button class="fs-ink-btn active" data-tool="pen">🖊 画笔</button>'
            + '  <button class="fs-ink-btn" data-tool="eraser">🧽 橡皮</button>'
            + '  <button class="fs-ink-btn" data-act="clear">🗑 清除</button>'
            + '  <button class="fs-ink-btn danger" data-act="cancel">取消</button>'
            + '  <button class="fs-ink-btn primary" data-act="ok">✓ 使用</button>'
            + '</div>'
            + '<div class="fs-ink-canvas-wrap">'
            + '  <canvas class="fs-ink-canvas"></canvas>'
            + '  <div class="fs-ink-hint">在这里写字（支持笔 / 手指 / 鼠标）</div>'
            + '</div>'
            + '</div>';
        document.body.appendChild(overlay);
        // fs-lock 可能已被全屏练习占用，退出时不能无脑移除
        const addedLock = !document.body.classList.contains("fs-lock");
        if (addedLock) document.body.classList.add("fs-lock");

        const canvas = overlay.querySelector("canvas");
        const wrap = overlay.querySelector(".fs-ink-canvas-wrap");
        const hint = overlay.querySelector(".fs-ink-hint");
        const ctx = canvas.getContext("2d");
        let tool = "pen";
        let drawing = false;
        let lastX = 0, lastY = 0;
        let empty = true;

        // 高 DPI：canvas 内部分辨率按 devicePixelRatio 放大，笔迹才不糊
        function resize() {
            const rect = wrap.getBoundingClientRect();
            const dpr = window.devicePixelRatio || 1;
            canvas.width = Math.max(100, Math.floor(rect.width * dpr));
            canvas.height = Math.max(100, Math.floor(rect.height * dpr));
            canvas.style.width = rect.width + "px";
            canvas.style.height = rect.height + "px";
            ctx.scale(dpr, dpr);
            ctx.lineCap = "round";
            ctx.lineJoin = "round";
            // 白底：导出 JPEG 时透明区会变黑，必须先铺白
            ctx.fillStyle = "#fff";
            ctx.fillRect(0, 0, canvas.width / dpr, canvas.height / dpr);
        }
        resize();
        window.addEventListener("resize", resize);

        function getPos(e) {
            const rect = canvas.getBoundingClientRect();
            return { x: e.clientX - rect.left, y: e.clientY - rect.top };
        }
        function setTool(t) {
            tool = t;
            overlay.querySelectorAll("[data-tool]").forEach(b =>
                b.classList.toggle("active", b.dataset.tool === t));
            canvas.style.cursor = (t === "eraser") ? "cell" : "crosshair";
        }
        overlay.querySelectorAll("[data-tool]").forEach(b => {
            b.onclick = () => setTool(b.dataset.tool);
        });
        overlay.querySelector('[data-act="clear"]').onclick = () => {
            const dpr = window.devicePixelRatio || 1;
            ctx.fillStyle = "#fff";
            ctx.fillRect(0, 0, canvas.width / dpr, canvas.height / dpr);
            empty = true;
            if (hint) hint.style.display = "";
        };

        function doClose() {
            window.removeEventListener("resize", resize);
            document.removeEventListener("keydown", onKey);
            if (addedLock) document.body.classList.remove("fs-lock");
            overlay.remove();
        }
        const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); doClose(); } };
        document.addEventListener("keydown", onKey);

        overlay.querySelector('[data-act="cancel"]').onclick = doClose;
        overlay.querySelector('[data-act="ok"]').onclick = () => {
            if (empty) { doClose(); return; }
            // 导出 JPEG dataURL，复用 attachShortImage 的图片通道（后端按图片识别手写）
            const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
            const st = shortState();
            st.image = dataUrl;
            const dpr = window.devicePixelRatio || 1;
            st.meta = Math.round(canvas.width / dpr) + "×" + Math.round(canvas.height / dpr)
                + " · 手写板 · " + Math.round(dataUrl.length / 1365) + "KB";
            st.error = "";
            renderShortImage();
            doClose();
        };

        // Pointer Events 统一处理鼠标/触控笔/手指
        canvas.addEventListener("pointerdown", (e) => {
            e.preventDefault();
            try { canvas.setPointerCapture(e.pointerId); } catch (err) {}
            drawing = true;
            const p = getPos(e);
            lastX = p.x; lastY = p.y;
            // 点一下也留一个墨点
            ctx.beginPath();
            ctx.arc(p.x, p.y, tool === "eraser" ? 12 : 1.8, 0, Math.PI * 2);
            ctx.fillStyle = tool === "eraser" ? "#fff" : "#111";
            ctx.fill();
            if (hint) hint.style.display = "none";
            empty = false;
        });
        canvas.addEventListener("pointermove", (e) => {
            if (!drawing) return;
            e.preventDefault();
            const p = getPos(e);
            ctx.beginPath();
            ctx.moveTo(lastX, lastY);
            ctx.lineTo(p.x, p.y);
            ctx.strokeStyle = tool === "eraser" ? "#fff" : "#111";
            ctx.lineWidth = tool === "eraser" ? 24 : 2.8;
            ctx.stroke();
            lastX = p.x; lastY = p.y;
        });
        const endDraw = (e) => {
            if (!drawing) return;
            drawing = false;
            try { canvas.releasePointerCapture(e.pointerId); } catch (err) {}
        };
        canvas.addEventListener("pointerup", endDraw);
        canvas.addEventListener("pointercancel", endDraw);
        // 画布上禁掉默认触摸滚动，否则写字会把页面带着滑
        canvas.addEventListener("touchstart", e => e.preventDefault(), { passive: false });
        canvas.addEventListener("touchmove", e => e.preventDefault(), { passive: false });
    }

    async function gradeShort(card, ct) {
        const st = shortState();
        if (st.grading || state.answered) return;
        if (!st.text.trim() && !st.image) {
            st.error = "请先写点答案，或上传手写照片";
            renderShortImage();
            return;
        }
        st.grading = true; st.error = "";
        const btn = document.getElementById("fs-grade-btn");
        if (btn) { btn.disabled = true; btn.textContent = "批改中…（识别手写 + 判分，约 5-20 秒）"; }
        try {
            const resp = await fetch(API + "/api/grade", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ question_id: card.question_id, answer_text: st.text, image: st.image }),
            });
            const d = await resp.json();
            st.grading = false;
            if (!d.ok) {
                st.error = d.error || "批改失败";
                if (btn) { btn.disabled = false; btn.textContent = "提交批改（Ctrl+Enter）"; }
                renderShortImage();
                return;
            }
            st.grade = d;
            // 简答题的反馈走同一套音型，阈值与 gradeHtml 的配色分档保持一致：
            // ≥75 答对音、≥45 中性音、其余低沉音
            SFX.play(d.score >= 75 ? "correct" : (d.score >= 45 ? "reveal" : "wrong"));
            state.revealed = true; state.answered = true;
            const ta = document.getElementById("fs-answer");
            if (ta) ta.disabled = true;
            if (btn) { btn.disabled = true; btn.textContent = "已批改"; }
            showFeedback(ct);
        } catch (e) {
            st.grading = false;
            st.error = "无法连接本地服务：批改需要 serve.js 在运行（用桌面「考研大盘」快捷方式启动即可）";
            if (btn) { btn.disabled = false; btn.textContent = "提交批改（Ctrl+Enter）"; }
            renderShortImage();
        }
    }

    // 填空题的完形（cloze）语法：{{c1::答案}}
    // ⚠️ 之前直接把 stem 原样渲染，`{{c1::that}}` 会**连答案一起显示**在题面上，
    //    「先回忆再翻转」就废了。这里未揭晓时挖成下划线，揭晓后才填回答案。
    const CLOZE_RE = /\{\{c\d+::([\s\S]*?)\}\}/g;

    function clozeText(s, reveal) {
        return String(s == null ? "" : s).replace(CLOZE_RE, (m, ans) => (reveal ? ans : "______"));
    }

    function hasCloze(s) { CLOZE_RE.lastIndex = 0; return CLOZE_RE.test(String(s == null ? "" : s)); }

    // 把答案规范成可读文本（布尔 → 正确/错误）
    function answerText(ct) {
        if (typeof ct.answer === "boolean") return ct.answer ? "正确" : "错误";
        const a = ct.answer != null ? String(ct.answer).trim() : "";
        if (/^true$/i.test(a)) return "正确";
        if (/^false$/i.test(a)) return "错误";
        return a;
    }

    // ============================================================
    // AI 针对性解析 + 追问（2026-09-14）
    //
    // 题库自带的 explanation 往往只讲「正确答案为什么对」，不讲「你选的那个为什么
    // 不行」——而错选才暴露真正的理解偏差。这里在答错时按**你实际的错选**现生成
    // 一段解析，并留一个追问框继续问。
    //
    // 全部问答由服务端落 explain_log：既是后续完善笔记的一手素材，也能在重刷同一
    // 张卡时直接复用上次结果（不重复烧 token）。
    // ============================================================

    // AI 返回的是 Markdown + LaTeX。项目里的 richText 只认 $...$，不认 Markdown，
    // 直接塞进去会把 ## 和 ** 原样显示。这里补一个够用的子集渲染器：
    // 代码块 / 标题 / 列表 / 引用 / 分隔线 / 粗体 / 行内代码 / 公式。
    // 只服务这一个用途，不追求完整 CommonMark。
    //
    // ⚠️ 本块所有反斜杠都写成双写形式（这段代码在普通 Python 字符串里）。单写的转义
    //    序列会被 Python 先解释成真实控制字符，写进 JS 就成了残句（注释里也一样）。
    //    要拿「一个反斜杠」这个字符本身，用 String.fromCharCode(92) 拼 —— 别写字符串
    //    字面量，写不对就是语法错（同文件上面 TEX_BS 那段也是这个道理）。
    const MD_BS = String.fromCharCode(92);
    const MD_OB = MD_BS + "[";    // 反斜杠 + [
    const MD_CB = MD_BS + "]";    // 反斜杠 + ]

    /** AI 回复里有没有公式？（决定要不要为它加载 KaTeX、要不要登记重渲染） */
    function hasTex(s) {
        if (typeof splitMath !== "function") return false;
        try { return splitMath(s).some(p => p.tex); } catch (e) { return false; }
    }

    /**
     * 独立成行的显示公式：$$…$$ 或 \[…\]，返回里面那截 LaTeX（不是就返回 null）。
     */
    function displayTexLine(s) {
        const t = String(s == null ? "" : s).trim();
        const op = t.slice(0, 2);
        if (op !== "$$" && op !== MD_OB) return null;
        const cl = op === "$$" ? "$$" : MD_CB;
        const inner = t.slice(2);
        if (t.length > 4 && t.slice(-2) === cl && inner.slice(0, -2).indexOf(cl) < 0) {
            return inner.slice(0, -2).trim();
        }
        // 收尾定界符压根没写（多半是 AI 输出被 maxTokens 截断在公式中间）：
        // 剩下的也照样当公式渲染 —— 半截公式总比 `$$…\to…` 的源码摊在正文里好看。
        if (inner && inner.indexOf(cl) < 0 && inner.indexOf(op) < 0) return inner.trim();
        return null;
    }

    /**
     * 把**跨行**的独立公式并回一行。
     *
     * ⚠️ 模型写独立公式几乎总是三行（`\[` / 公式 / `\]`），而下面是**逐行**渲染的：
     *    不先并起来，那三行就各自成段 —— 定界符裸露成正文，公式只能按「裸 LaTeX」
     *    降级成行内源码，于是解析区里躺着 `\[`、一段 LaTeX 源码、`\]`（用户
     *    2026-09-22 截图反馈的就是这个；用 explain_log 里那段真实解析跑真浏览器
     *    复现过，见 tools/e2e_math_render.py）。
     * 找不到闭合定界符时**原样返回**：宁可少并，也不能把后面的正文吞进公式。
     */
    function foldDisplayLines(lines) {
        const out = [];
        for (let i = 0; i < lines.length; i++) {
            const t = lines[i].trim();
            const op = t.slice(0, 2);
            if (op !== "$$" && op !== MD_OB) { out.push(lines[i]); continue; }
            const cl = op === "$$" ? "$$" : MD_CB;
            if (t.slice(2).indexOf(cl) >= 0) { out.push(t); continue; }   // 同一行就闭合了
            const buf = [t];
            let j = i + 1, closed = false;
            for (; j < lines.length; j++) {
                buf.push(lines[j].trim());
                if (lines[j].indexOf(cl) >= 0) { closed = true; break; }
            }
            if (!closed) { out.push(lines[i]); continue; }
            out.push(buf.join(" "));
            i = j;
        }
        return out;
    }

    /**
     * Markdown + LaTeX 子集渲染（AI 回复用）。
     *
     * ⚠️ 它常常是**页面上第一处带公式的地方**：题面/选项里没有公式时 richText 就不会
     *    触发 KaTeX 懒加载，AI 解析里的公式于是永远停在源码兜底上（2026-09-22
     *    用户截图：整段 LaTeX 源码摊在解析区）。所以这里自己喊一声 ensureKatex()。
     * ⚠️ 刚喊完的那一刻 window.katex 还没到，只喊不登记的话这一屏就定格在源码了
     *    —— 所以把整块登记进 texStore，KaTeX 就绪后 flushMath 会调回来重渲染。
     */
    function mdTex(raw) {
        const src = String(raw == null ? "" : raw).replace(/\\r\\n/g, "\\n");
        if (!window.katex && typeof window.ensureKatex === "function") {
            window.ensureKatex();
            if (hasTex(src) && typeof window.registerTex === "function") {
                const id = window.registerTex(() => mdTex(src));
                return '<div class="md-tex-host" data-texid="' + id + '">'
                    + mdTexBlock(src) + "</div>";
            }
        }
        return mdTexBlock(src);
    }

    function mdTexBlock(src) {
        // 行内：先转义，再认标记（标记都是 ASCII，转义不影响），最后把 $..$ 交给 KaTeX
        const inline = (t) => {
            // 行内标记统一走 mdInline（与卡片正文同一套，由前一个 IIFE 挂在 window 上）。
            // 公式走 splitMath：既认 $..$ / \(..\) / \[..\]，也认**没有定界的裸 LaTeX**
            // （模型常把 \sum a_n 直接写在句子里，用户自己写了提示词时尤其常见）。
            // 注意顺序：先按原文切段，再分别处理——先转义会把 & < 变成实体，喂给 KaTeX 就错了。
            const raw = String(t == null ? "" : t);
            if (typeof splitMath !== "function") {
                // 桥没搭上（脚本被裁/顺序变了）时的兜底：退回只认 $..$
                let s = mdInline(raw).replace(/^\\s*[-*]\\s+/, "");
                return s.replace(/\\$([^$\\n]+)\\$/g, (m, tex) => katexHtml(tex, false));
            }
            let stripped = false;
            return splitMath(raw).map(function (p) {
                if (p.tex) return katexHtml(p.src, p.display);
                let s = mdInline(p.text);
                if (!stripped) { s = s.replace(/^\\s*[-*]\\s+/, ""); stripped = true; }
                return s;
            }).join("");
        };
        const out = [];
        // --- 表格支持（2026-09-20 补）---
        // AI 讲对比时几乎必用 Markdown 表格。原先没认表格，整块会退化成
        // 一堆 "| 功能 | 例子 |" 的竖线文本，可读性很差。
        // 判定：本行含 |，且下一行是 |---|---| 这类分隔行。
        const isTableSep = (s) => {
            const x = String(s == null ? "" : s).trim();
            return x.indexOf("|") >= 0 && x.indexOf("-") >= 0
                && /^\\|?[\\s:|-]+\\|?$/.test(x);
        };
        const splitRow = (s) => {
            let x = String(s).trim();
            if (x.charAt(0) === "|") x = x.slice(1);
            if (x.charAt(x.length - 1) === "|") x = x.slice(0, -1);
            return x.split("|").map(c => c.trim());
        };
        const tableHtml = (header, rows) => {
            let h = '<table class="md-table"><thead><tr>'
                + header.map(c => "<th>" + inline(c) + "</th>").join("")
                + "</tr></thead><tbody>";
            for (const r of rows) {
                let tds = "";
                // 按表头列数对齐，AI 偶尔会漏列
                for (let i = 0; i < header.length; i++) {
                    tds += "<td>" + inline(r[i] == null ? "" : r[i]) + "</td>";
                }
                h += "<tr>" + tds + "</tr>";
            }
            return h + "</tbody></table>";
        };
        const blocks = src.split(/```/);
        blocks.forEach((blk, bi) => {
            if (bi % 2 === 1) {   // 奇数段是代码块
                const nl = blk.indexOf("\\n");
                const body = nl >= 0 ? blk.slice(nl + 1) : blk;
                out.push('<pre class="md-pre">' + escHtml(body.replace(/\\n$/, "")) + "</pre>");
                return;
            }
            let listBuf = [];
            const flushList = () => {
                if (listBuf.length) {
                    out.push('<ul class="md-ul">' + listBuf.map(x => "<li>" + x + "</li>").join("") + "</ul>");
                    listBuf = [];
                }
            };
            const lines = foldDisplayLines(blk.split("\\n"));
            for (let li = 0; li < lines.length; li++) {
                const line = lines[li];
                const t = line.trim();
                if (!t) { flushList(); continue; }
                let m, dt;
                // 表格：本行含 |，且下一行是 |---|---| 分隔行
                if (t.indexOf("|") >= 0 && li + 1 < lines.length && isTableSep(lines[li + 1])) {
                    flushList();
                    const header = splitRow(t);
                    const rows = [];
                    li += 2;   // 跳过表头行与分隔行
                    while (li < lines.length && lines[li].trim().indexOf("|") >= 0) {
                        rows.push(splitRow(lines[li]));
                        li++;
                    }
                    li--;      // for 会自增，回退一格
                    out.push(tableHtml(header, rows));
                    continue;
                }
                if ((m = t.match(/^(#{1,6})\\s+(.*)$/))) {
                    flushList();
                    const lv = Math.min(6, m[1].length + 2);   // 别盖过卡片标题
                    out.push("<h" + lv + ' class="md-h">' + inline(m[2]) + "</h" + lv + ">");
                } else if (/^(-{3,}|\\*{3,})$/.test(t)) {
                    flushList(); out.push('<hr class="md-hr">');
                } else if ((m = t.match(/^>\\s?(.*)$/))) {
                    flushList(); out.push('<blockquote class="md-quote">' + inline(m[1]) + "</blockquote>");
                } else if ((m = t.match(/^\\s*(?:[-*+]|\\d+\\.)\\s+(.*)$/))) {
                    listBuf.push(inline(m[1]));
                } else if ((dt = displayTexLine(t)) != null) {
                    // 独立成行的显示公式：$$…$$ 或 \[…\]（后者可能刚被 foldDisplayLines 并起来）
                    flushList();
                    out.push('<div class="md-tex">' + katexHtml(dt, true) + "</div>");
                } else {
                    flushList(); out.push('<p class="md-p">' + inline(t) + "</p>");
                }
            }
            flushList();
        });
        return out.join("");
    }

    // 防竞态：上一张卡的异步结果不能覆盖当前卡
    let explainSeq = 0;

    function explainShellHtml(loading) {
        return '<div class="fs-exp-head"><span>🤖 AI 针对性解析</span>'
            // 单次要求深度思考：默认关思考是为了跟手，但想细想一遍时得够得着
            + '<button class="fs-exp-retry" id="fs-exp-deep"'
            + ' title="这一次让它多想一会儿，讲得更细（慢几秒）">🧠 深想一遍</button>'
            + '<button class="fs-exp-retry" id="fs-exp-retry" hidden>重试</button></div>'
            + '<div class="fs-exp-body" id="fs-exp-body">'
            + (loading ? '<span class="fs-exp-loading">正在按你的错选生成解析…</span>' : "")
            + "</div>"
            + '<div class="fs-exp-ask" id="fs-exp-ask" hidden>'
            + '<input class="fs-exp-input" id="fs-exp-input" maxlength="1000"'
            + ' enterkeyhint="send" autocapitalize="off" autocorrect="off"'
            + ' placeholder="输入你的问题（想问什么就写什么）">'
            + '<button class="fs-btn fs-exp-send" id="fs-exp-send">发送</button></div>';
    }

    /**
     * 往解析区追加一条消息。
     *
     * ⚠️ 默认**不动滚动条**（2026-09-21 用户要求）：
     *   · 首次解析完 → 停在**最上面**（以前无条件滚到底，用户还得多翻上去看）
     *   · 追问的回答回来 → 保持用户当前的阅读位置，别把人拽走
     * 只有「用户自己发的那条 + 他本来就贴着底部」时才跟随到底（stick=true）。
     */
    function appendExplainMsg(role, text, stick) {
        const body = document.getElementById("fs-exp-body");
        if (!body) return;
        const d = document.createElement("div");
        d.className = role === "user" ? "fs-exp-turn fs-exp-mine" : "fs-exp-turn";
        d.innerHTML = mdTex(text);
        body.appendChild(d);
        if (stick) body.scrollTop = body.scrollHeight;
    }
    /** 面板现在是不是贴着底部（差 24px 以内就算） */
    function atExplainBottom() {
        const body = document.getElementById("fs-exp-body");
        if (!body) return true;
        return (body.scrollHeight - body.scrollTop - body.clientHeight) < 24;
    }

    function showExplainError(msg, onRetry) {
        const body = document.getElementById("fs-exp-body");
        const retry = document.getElementById("fs-exp-retry");
        if (!body) return;
        body.innerHTML = '<div class="fs-exp-err">⚠ ' + escHtml(msg)
            + '<br><span class="fs-exp-hint">解析需要本地服务在运行（用桌面「考研大盘」快捷方式启动）。</span></div>';
        if (retry) {
            retry.hidden = false;
            retry.onclick = () => { retry.hidden = true; onRetry(); };
        }
    }

    async function askExplain(qid, payload) {
        const r = await fetch(API + "/api/explain", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || "生成失败");
        return d;
    }

    /**
     * 挂载解析区。优先复用该题上一次的解析（同一错选才复用），
     * 没有才现生成——重刷同一张卡不必重复烧 token。
     */
    // mode='correct'：答对了但想追问（不确定/想深挖）。服务端换一套讲陷阱与边界的要求。
    async function mountExplain(card, chosenText, mode, askFirst) {
        const box = document.getElementById("fs-explain");
        if (!box) return;
        const qid = card.question_id;
        if (!qid) return;
        const seq = ++explainSeq;
        const isCorrectAsk = mode === 'correct';
        const st = { threadId: null, qid, chosen: chosenText || "", mode: isCorrectAsk ? 'correct' : 'wrong' };
        state.explain = st;
        box.innerHTML = explainShellHtml(!askFirst);

        const generate = async (deep) => {
            const body = document.getElementById("fs-exp-body");
            const deepBtn = document.getElementById("fs-exp-deep");
            if (deepBtn) deepBtn.disabled = true;
            if (body) body.innerHTML = '<span class="fs-exp-loading">'
                + (deep ? '正在深想一遍（会多想几秒）…'
                        : (isCorrectAsk ? '正在整理这题的陷阱与判断依据…' : '正在按你的错选生成解析…'))
                + '</span>';
            try {
                const d = await askExplain(qid, {
                    question_id: qid, card_id: card.card_id,
                    chosen: st.chosen, subject: card.subject, topic_id: card.topic_id,
                    mode: st.mode,
                    // 只有点了「深想一遍」才显式要求深度思考；否则交给设置页的默认值
                    deep: deep === true ? true : undefined,
                });
                if (seq !== explainSeq) return;
                st.threadId = d.thread_id;
                const b = document.getElementById("fs-exp-body");
                if (b) b.innerHTML = "";
                appendExplainMsg("assistant", d.text);
                const ask = document.getElementById("fs-exp-ask");
                if (ask) ask.hidden = false;
                wireAsk();
            } catch (e) {
                if (seq !== explainSeq) return;
                showExplainError(e.message, generate);
            } finally {
                const db2 = document.getElementById("fs-exp-deep");
                if (db2) db2.disabled = false;
            }
        };

        // 「深想一遍」：这一次显式要求深度思考（不受设置页默认值影响）
        const deepBtn0 = document.getElementById("fs-exp-deep");
        if (deepBtn0) deepBtn0.onclick = () => generate(true);

        const wireAsk = () => {
            const input = document.getElementById("fs-exp-input");
            const send = document.getElementById("fs-exp-send");
            if (!input || !send || send.dataset.wired === "1") return;
            send.dataset.wired = "1";
            const submit = async () => {
                const msg = input.value.trim();
                if (!msg) return;
                input.value = "";
                send.disabled = true;
                const b0 = document.getElementById("fs-exp-body");
                // 用户自己发的那条：他本来就贴着底部时才跟随到底，否则别动他的位置
                const stick = atExplainBottom();
                // 首次提问（答对/看答案后直接问）：先把「有具体想问的…」那句提示清掉
                if (!st.threadId && b0) b0.innerHTML = "";
                appendExplainMsg("user", msg, stick);
                const waiting = document.createElement("div");
                waiting.className = "fs-exp-turn fs-exp-loading";
                waiting.textContent = "思考中…";
                if (b0) b0.appendChild(waiting);
                try {
                    let d;
                    if (!st.threadId) {
                        // 还没有解析 → 这一次把他的问题一起带上（服务端会把首轮记成【我问】…，
                        // 之后同一线索的追问沿用同一口径，不会退回泛讲陷阱/边界）
                        d = await askExplain(qid, {
                            question_id: qid, card_id: card.card_id, chosen: st.chosen,
                            subject: card.subject, topic_id: card.topic_id,
                            mode: st.mode, question: msg,
                        });
                    } else {
                        const r = await fetch(API + "/api/explain/followup", {
                            method: "POST", headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ thread_id: st.threadId, message: msg }),
                        });
                        d = await r.json();
                    }
                    if (seq !== explainSeq) return;
                    waiting.remove();
                    if (!d.ok) { appendExplainMsg("assistant", "⚠ " + (d.error || "回答失败")); }
                    else {
                        if (d.thread_id) st.threadId = d.thread_id;
                        appendExplainMsg("assistant", d.text);
                        const dbtn = document.getElementById("fs-exp-deep");
                        if (dbtn) dbtn.hidden = false;   // 有解析了，「深想一遍」可以用了
                    }
                } catch (e) {
                    if (seq === explainSeq) {
                        waiting.remove();
                        appendExplainMsg("assistant", "⚠ 提问失败：" + e.message
                            + "（本地服务在跑吗？用桌面「启动考研大盘.bat」启动）");
                    }
                } finally {
                    send.disabled = false;
                    input.focus();
                }
            };
            send.onclick = submit;
            // ️ 移动端输入法的回车不能当提交（用户报「平板上用不了追问」的真凶）：
            //    软键盘按回车往往是在**确认候选词**，那一刻 keydown 也是 Enter，但输入法
            //    还没把字交给页面（isComposing=true；老安卓是 keyCode 229）。不判它，
            //    表现就是「打了字按回车没反应」，或把半截拼音当问题发出去。
            //    就地判、不用跨 IIFE 的 helper（裸名引用会在脚本顺序变化时炸）。
            input.onkeydown = (e) => {
                if (e.key === "Enter" && !(e.isComposing || e.keyCode === 229)) {
                    e.preventDefault(); submit();
                }
            };
            // 软键盘弹出来会盖住底部：聚焦时把输入框滚进可视区（等键盘动画完再滚）
            input.addEventListener("focus", () => {
                setTimeout(() => {
                    try { input.scrollIntoView({ block: "center", behavior: "smooth" }); } catch (e2) {}
                }, 320);
            });
        };

        // ---- 空的追问框（答对 / 直接看答案走这条）----
        // 用户明确说过：他要问的肯定是针对性的问题，不要预设问题清单，也不要先烧 token
        // 生成那种「陷阱/边界/判断依据」的泛讲。所以这里什么都不生成，等他自己输入。
        // ️ 这段必须放在 wireAsk 定义之后（它是 const，提前调用会踩 TDZ）。
        if (askFirst) {
            const ab = document.getElementById("fs-exp-body");
            if (ab) {
                ab.innerHTML = '<span class="fs-exp-hint">'
                    + '有具体想问的，直接在下面输入；没有就继续下一张。</span>';
            }
            const askRow = document.getElementById("fs-exp-ask");
            if (askRow) askRow.hidden = false;
            const deep0 = document.getElementById("fs-exp-deep");
            if (deep0) deep0.hidden = true;    // 还没有解析可「深想」，先藏起来
            wireAsk();
            // ️ 不自动聚焦输入框（2026-09-21 用户反馈）：一聚焦就把键盘抢过去，
            //    按 1/2/3/4 标熟练度全被输入框吃掉。等他自己点进去再输入。
            return;
        }

        // 先看有没有可复用的历史解析：**同一题 + 同一个错选 + 同一种模式**才复用
        // （服务端按这三个条件精确查，所以中间穿插过别的错选也不影响）
        try {
            const r = await fetch(API + "/api/explain?question_id=" + encodeURIComponent(qid)
                + "&chosen=" + encodeURIComponent(st.chosen)
                + "&mode=" + encodeURIComponent(st.mode));
            const d = await r.json();
            if (seq !== explainSeq) return;
            const canReuse = d.ok && d.thread_id
                && String(d.chosen || "") === st.chosen
                // 模式也要一致：答错的讲解不能拿来回答答对的追问（反之亦然）
                && String(d.mode || "wrong") === st.mode
                && (d.messages || []).some(m => m.role === "assistant");

            if (canReuse) {
                st.threadId = d.thread_id;
                const b = document.getElementById("fs-exp-body");
                if (b) b.innerHTML = "";
                d.messages.forEach(m => appendExplainMsg(m.role, m.content));
                // 说清楚这是上次的结果，并留一个「重新生成」的口子——
                // 不然想换个角度再听一遍就没路了。
                // 用 createElement 拼而不是 innerHTML：省一层转义，也让测试桩能顺着
                // children 找到这个按钮（桩的 innerHTML 只登记 id、不建子元素）。
                if (b) {
                    const bar = document.createElement("div");
                    bar.className = "fs-exp-reuse";
                    const txt = document.createElement("span");
                    txt.textContent = "⚡ 上次这题你也是选这一项，下面是上次的解析"
                        + (d.created_at ? "（" + String(d.created_at).slice(0, 16) + "）" : "")
                        + "，没有重新问 AI";
                    const regen = document.createElement("button");
                    regen.type = "button";
                    regen.textContent = "重新生成";
                    regen.onclick = () => { bar.remove(); generate(); };
                    bar.appendChild(txt);
                    bar.appendChild(regen);
                    b.insertBefore(bar, b.firstChild || null);
                }
                const ask = document.getElementById("fs-exp-ask");
                if (ask) ask.hidden = false;
                wireAsk();
                return;
            }
        } catch (e) {
            // ⚠️ 这里吞掉的只能是**真异常**（「没有历史」不是异常，服务端回的是 ok:true）。
            // 悄悄吞掉会让人以为「复用没生效」，实际是渲染复用结果时炸了（踩过一次：
            // 桩里 insertBefore 缺失 → 报错被吞 → 又去问了一遍 AI）。留个 warn。
            console.warn("[Explain] 复用历史解析失败，改为现生成：", e);
        }
        if (seq === explainSeq) generate();
    }

    // 本地卡组（早间回顾）翻卡后的反馈：只给「原文」+ 自评四档。
    // 不挂 AI 追问框、不给 📌 钉住 / ⚑ 报卡 / 🗑 删卡——那些动作都是针对**主闪卡库**的卡，
    // 早间回顾的卡不在库里（钉住了它也不会再出现，标了也没人复核）。
    function showLocalFeedback(ct) {
        const fb = document.getElementById("fs-feedback");
        if (!fb) return;
        const card = state.cards[state.idx] || {};
        fb.innerHTML = '<div class="fs-explain fs-local-back">'
            + (card.sec_label ? '<div class="fs-local-sec">' + esc(card.sec_label) + '</div>' : '')
            + rawHtml(ct.answer) + '</div>'
            + '<div class="fs-actions">'
            + [1, 2, 3, 4].map(r => '<button class="fs-btn fs-rate' + r + '" data-rate="' + r + '">'
                + '<span class="fs-rate-label">' + r + ' ' + LOCAL_LABELS[r] + '</span>'
                + '</button>').join("")
            + '</div>'
            + '<div class="fs-undo-row">'
            + '<button class="fs-btn fs-undo" id="fs-undo">↶ 撤销上一次评分（U）</button>'
            + '<button class="fs-btn" id="fs-local-close">✕ 收起练习区</button>'
            + '</div>';
        fb.querySelectorAll("[data-rate]").forEach(b => {
            b.onclick = () => rate(parseInt(b.dataset.rate, 10));
        });
        const ub = document.getElementById("fs-undo");
        if (ub) ub.onclick = () => undo();
        const cb = document.getElementById("fs-local-close");
        if (cb) cb.onclick = () => {
            const api = globalThis.__flashFloat;
            if (api && typeof api.close === "function") api.close();
        };
    }

    function showFeedback(ct) {
        if (state.local) return showLocalFeedback(ct);
        const fb = document.getElementById("fs-feedback");
        const card = state.cards[state.idx] || {};
        let html = "";

        // 答案行：文本型答案（判断/填空/简答）直接显示；选择题库里 answer 是序号，
        // 单靠 answerText 会得到空串，这里补成「正确答案：A. 选项原文」。
        // 简答题优先展示 AI 批改结果（含手写辨认），再给参考答案
        if (state.short && state.short.grade) html += gradeHtml(state.short.grade);

        const ans = answerText(ct);
        const refAns = typeof ct.reference_answer === "string" && ct.reference_answer.trim()
            ? ct.reference_answer : "";
        const isTextAnswer = typeof ct.answer === "boolean"
            || (typeof ct.answer === "string" && !/^[A-Da-d]$/.test(String(ct.answer).trim()));
        if (refAns) {
            html += '<div class="fs-explain"><b>参考答案：</b>' + texWrap(refAns) + '</div>';
        } else if (isTextAnswer && ans) {
            html += '<div class="fs-explain"><b>答案：</b>' + texWrap(ans) + '</div>';
        } else {
            const opts = optionsOf(card);
            const ci = correctIndex(ct, opts);
            if (ci >= 0 && opts[ci] != null) {
                html += '<div class="fs-explain"><b>正确答案：</b>'
                    + texWrap(String.fromCharCode(65 + ci) + ". " + opts[ci]) + '</div>';
            }
        }
        // 填空题揭晓后把挖空填回，给出完整句子
        if (hasCloze(ct.stem)) {
            html += '<div class="fs-explain"><b>完整原文：</b>' + texWrap(clozeText(ct.stem, true)) + '</div>';
        }
        if (ct.explanation) html += '<div class="fs-explain">' + texWrap(ct.explanation) + '</div>';
        if (Array.isArray(ct.traps) && ct.traps.filter(Boolean).length)
            html += '<div class="fs-traps">⚠ 易错点：' + ct.traps.filter(Boolean).map(texWrap).join("；") + '</div>';

        // AI 解析容器。答错时自动挂载解析；答对/直接看答案时**不自动生成任何东西**——
        // 用户明确要求：答对的场合给一个**空的追问框**，他要问的肯定是自己针对性的问题，
        // 所以既不给预设问题清单、也不预先烧 token 生成「陷阱/边界/判断依据」那种泛讲。
        // 输入框由 mountExplain(..., askFirst=true) 挂上，提交时才带着他的问题去问。
        html += '<div class="fs-exp-wrap" id="fs-explain"></div>';

        if (state.autoWrong) {
            // 选错已自动记为「忘记」，不再给四档按钮，只留翻页
            html += '<div class="fs-actions">'
                + '<button class="fs-btn fs-next" id="fs-next">下一张（空格 / 回车）</button>'
                + '</div>';
        } else {
            if (state.saveFailed)
                html += '<div class="fs-traps">⚠ 评分未保存（无法连接本地服务），请手动选一档重试：</div>';
            // Anki 风格：评分按钮副标题显示各档下次间隔
            const pv = card.previews || {};
            html += '<div class="fs-actions">'
                + (function () {
                    const aiR = (state.short && state.short.grade) ? state.short.grade.suggested_rating : null;
                    return [1, 2, 3, 4].map(r => '<button class="fs-btn fs-rate' + r + '" data-rate="' + r + '">'
                        + (r === aiR ? '<span class="fs-rate-ai">AI 建议</span>' : '')
                        + '<span class="fs-rate-label">' + r + ' ' + LABELS[r] + '</span>'
                        + (pv[r] ? '<span class="fs-rate-pv">' + esc(pv[r]) + '</span>' : '')
                        + '</button>').join("");
                })()
                + '</div>';
        }
        html += '<div class="fs-undo-row"><button class="fs-btn fs-undo" id="fs-undo">'
            + (state.autoWrong ? '↶ 撤销，重新作答（U）' : '↶ 撤销上一次评分（U）')
            + '</button>'
            // 📌 钉住（2026-09-19 用户要求）：与⚑标记、🗑删卡是三件事——
            // 「题目没问题、我也答对了，但我想留着它，下次再看看」。
            + '<button class="fs-btn fs-pin' + (card.pin ? ' on' : '') + '" id="fs-pin" title="'
            + esc('把这张卡钉住：它会一直排在智能组前面，而且不会因为「连对几次」被收起来'
                  + '（快捷键 ' + __keys.pretty(__keys.specOf("flash.pin")) + '）')
            + '">' + (card.pin ? '📌 已钉住' : '📌 钉住这张卡') + '</button>'
            // 标记「这题本身有问题」（与🗑删卡是两件事：删掉是当场判死刑，标记是等 AI 复核）
            + '<button class="fs-btn fs-flag' + (card.report ? ' on' : '') + '" id="fs-flag" title="'
            + esc('题目本身有问题（多个选项都对 / 答案有误 / 题干有误…）→ 记下来，每日任务里由 AI 核对修复')
            + '">' + (card.report ? '⚑ 已标记：' + esc(card.report.kind_label) : '⚑ 这题有问题')
            + '</button>'
            + '<button class="fs-btn fs-del" id="fs-del" title="永久移除此卡，之后不再出现">🗑 这题没用，删掉</button>'
            + '</div>'
            + '<div class="fs-flag-panel" id="fs-flag-panel"></div>';
        fb.innerHTML = html;

        fb.querySelectorAll("[data-rate]").forEach(b => {
            b.onclick = () => rate(parseInt(b.dataset.rate, 10));
        });
        const nb = document.getElementById("fs-next");
        if (nb) nb.onclick = () => goNext();
        const ub = document.getElementById("fs-undo");
        if (ub) ub.onclick = () => undo();
        // 📌 钉住按钮：与报卡一样**不重绘整张卡**（重绘会冲掉已挂的解析与追问框）。
        const pinBtn = document.getElementById("fs-pin");
        if (pinBtn) pinBtn.onclick = () => togglePin(card);
        // 答对 / 直接看答案：不生成任何东西，直接给一个**空的追问框**（有想问的再问）。
        // 把正确项当作「学生选的」带过去，模型才知道他在纠结哪一项。
        if (!state.autoWrong) {
            const opts = optionsOf(card);
            const ci = correctIndex(ct, opts);
            const mine = (ci >= 0 && opts[ci] != null)
                ? String.fromCharCode(65 + ci) + ". " + opts[ci] : "";
            mountExplain(card, state.chosen || mine, 'correct', true);
        }
        // 删除卡（软删除）：两步确认防误触——第一次点变红「确认删除」，再点才调 API。
        // 删完把卡从当前组移除并跳到下一张，避免再看到它。
        const delBtn = document.getElementById("fs-del");
        if (delBtn) {
            let confirming = false;
            const resetDel = () => {
                if (!delBtn) return;
                confirming = false;
                delBtn.className = "fs-btn fs-del";
                delBtn.textContent = "🗑 这题没用，删掉";
            };
            delBtn.onclick = async () => {
                if (!confirming) {
                    confirming = true;
                    delBtn.className = "fs-btn fs-del-confirm";
                    delBtn.textContent = "⚠ 确认删除？（再点一次）";
                    return;
                }
                delBtn.disabled = true;
                delBtn.textContent = "删除中…";
                try {
                    const r0 = await fetch(API + "/api/flashcards/suspend", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ card_id: card.card_id })
                    });
                    const rj = await r0.json();
                    if (!rj.ok) {
                        toast("删除失败：" + (rj.error || ""));
                        resetDel(); delBtn.disabled = false;
                        return;
                    }
                    SFX.play("wrong");   // 低沉提示音表示"移除"
                    toast("已删除，后续不会再出现");
                    state.cards.splice(state.idx, 1);
                    saveSession();
                    if (state.idx >= state.cards.length) { renderSummary(); return; }
                    renderCard();
                } catch (e) {
                    toast("删除失败：无法连接本地服务");
                    resetDel(); delBtn.disabled = false;
                }
            };
        }
        // 标记「这题有问题」：点一下展开原因清单 + 备注框，提交后按钮变「已标记」。
        // 只就地改这一小块 DOM（不重绘整张卡）——重绘会把已经挂上的 AI 解析/追问框冲掉。
        const flagBtn = document.getElementById("fs-flag");
        if (flagBtn) flagBtn.onclick = () => toggleFlagPanel(card, ct);
        updateHint();

        // 解析一出来内容会突然变长。交给 scrollIntoView({block:"nearest"})：
        // 只在没露出来时才滚，而且是平滑的——把被动抽搐换成一次有意的归位。
        if (typeof fb.scrollIntoView === "function") {
            try { fb.scrollIntoView({ behavior: "smooth", block: "nearest" }); } catch (e) {}
        }
    }

    // ============================================================
    // 「这道题有问题」标记（2026-09-17）
    //
    // 用户的原话：有些闪卡**本身**是错的（他实测到那道叠加原理的题 A、C 都对），
    // 他照着正确答案点反而被判错。所以要能在练习时就地标出来，并且我（AI）要能
    // 拿到这个数据、在每日任务里把它修掉。
    //
    // 三件事分开做，别混：
    //   · 报卡 = 这里（一键 + 原因 + 可选一句话），只管记下来，不改题、不判对错；
    //   · 复核 = 每日任务里的 agent（改选项/答案/解析，或者驳回、删卡）；
    //   · 展示 = 徽标 + 统计面板的待修清单（让用户看得见「我的标记没丢」）。
    // 原因清单从服务端下发（GET /api/flashcards/reports 的 kinds），这里不抄一份——
    // 那份话术是给复核的 AI 看的，两边漂移就会出现「前端标了 A、后端不认识 A」。
    // ============================================================
    let FLAG_KINDS = null;      // 取回来就缓存（一次会话只问一次）
    let flagPanelOpen = false;

    async function ensureFlagKinds() {
        if (FLAG_KINDS) return FLAG_KINDS;
        try {
            const d = await (await fetch(API + "/api/flashcards/reports?limit=1")).json();
            if (d && d.ok && Array.isArray(d.kinds) && d.kinds.length) FLAG_KINDS = d.kinds;
        } catch (e) { /* 服务端连不上，走下面的退化分支 */ }
        // 退路：连不上服务端也得能标记（标记本身最重要，原因回头在每日任务里补）。
        // 只给「其它」一项，不编造一套本地标签表。
        if (!FLAG_KINDS) FLAG_KINDS = [{ id: "other", label: "其它", hint: "看备注" }];
        return FLAG_KINDS;
    }

    async function toggleFlagPanel(card, ct) {
        const panel = document.getElementById("fs-flag-panel");
        if (!panel) return;
        if (flagPanelOpen) { panel.innerHTML = ""; flagPanelOpen = false; return; }
        flagPanelOpen = true;
        panel.innerHTML = '<div class="fs-flag-title">正在准备原因清单…</div>';
        const kinds = await ensureFlagKinds();
        // 默认选中：已经标过就选它原来那条（改原因时不用重新找），否则选最常见的那个。
        const cur = (card.report && card.report.kind) || "multi_correct";
        const picked = kinds.some(k => k.id === cur) ? cur : kinds[0].id;
        panel.innerHTML = '<div class="fs-flag-title">这道题哪里有问题？</div>'
            + '<div class="fs-flag-kinds">' + kinds.map(k =>
                '<button type="button" class="fs-flag-kind" data-kind="' + esc(k.id) + '" aria-pressed="'
                + (k.id === picked ? "true" : "false") + '" title="' + esc(k.hint || "") + '">'
                + esc(k.label) + '</button>').join("") + '</div>'
            + '<textarea class="fs-flag-note" id="fs-flag-note" rows="2" placeholder="补一句（可选）：比如「A、C 都对」"></textarea>'
            + '<div class="fs-flag-hint">标记后这题暂时不再进智能组；每日任务里 AI 会核对——能改的就改'
            + '（选项/答案/解析），需要你拿主意的会来问你。你的原话会一起存下来。</div>'
            + '<div class="fs-flag-actions">'
            +   '<button class="fs-btn primary" id="fs-flag-save">标记有问题</button>'
            +   '<button class="fs-btn" id="fs-flag-cancel">取消</button>'
            + '</div>';
        const noteEl = document.getElementById("fs-flag-note");
        // 选中的原因用**局部变量**记着（不只靠 aria-pressed 那个属性）：
        // 提交时直接拿它，不依赖 DOM 属性的读回——样式状态与提交内容是两件事，
        // 混在一起以后改样式就会顺手改坏提交。
        let pickedKind = picked;
        // 已有标记时把上次那句话填回去（改原因不用重打一遍）
        if (noteEl && card.report && card.report.note) noteEl.value = card.report.note;
        panel.querySelectorAll(".fs-flag-kind").forEach(b => {
            b.onclick = () => {
                pickedKind = b.dataset.kind;
                panel.querySelectorAll(".fs-flag-kind").forEach(x =>
                    x.setAttribute("aria-pressed", x === b ? "true" : "false"));
            };
        });
        const save = document.getElementById("fs-flag-save");
        if (save) save.onclick = () => {
            submitReport(card, ct, pickedKind, noteEl ? String(noteEl.value || "") : "");
        };
        const cancel = document.getElementById("fs-flag-cancel");
        if (cancel) cancel.onclick = () => {
            panel.innerHTML = ""; flagPanelOpen = false;
            toast("已取消（这题还没被标记）");
        };
        if (typeof panel.scrollIntoView === "function") {
            try { panel.scrollIntoView({ behavior: "smooth", block: "nearest" }); } catch (e) {}
        }
    }

    // 标完立刻把徽标与提示条补上 —— **就地插入，不重绘整张卡**（重绘会把已经挂上的
    // AI 解析与追问框冲掉，用户刚问的话就没了）。幂等：先摘旧的再插，重复标记不会叠两个。
    // 位置与 renderCard 里那两处保持一致（徽标在头部「统计」左边、提示条紧贴题面下方）。
    function paintReportMarks(card) {
        try {
            const head = box.querySelector(".fs-head");
            if (head) {
                const old = head.querySelector(".fs-report-badge");
                if (old && old.parentNode) old.parentNode.removeChild(old);
                if (card.report) {
                    const span = document.createElement("span");
                    span.className = "fs-report-badge";
                    span.title = "你标记过：" + card.report.kind_label
                        + (card.report.note ? "（" + card.report.note + "）" : "")
                        + "｜AI 会在每日任务里核对修复";
                    span.textContent = "⚑ 待修 · " + card.report.kind_label;
                    const anchor = head.querySelector("#fs-stats-btn");
                    if (anchor) head.insertBefore(span, anchor); else head.appendChild(span);
                }
            }
            const stem = box.querySelector(".fs-stem");
            if (stem) {
                const oldN = box.querySelector(".fs-report-note");
                if (oldN && oldN.parentNode) oldN.parentNode.removeChild(oldN);
                if (card.report) {
                    const div = document.createElement("div");
                    div.className = "fs-report-note";
                    div.textContent = "⚑ 这题你标记过有问题（" + card.report.kind_label + "）"
                        + (card.report.note ? "：" + card.report.note : "")
                        + "，已排进每日任务等 AI 核对修复；修好之前它不再进智能组。";
                    if (stem.parentNode) stem.parentNode.insertBefore(div, stem.nextSibling);
                }
            }
        } catch (e) { console.warn("[flash] 标记状态补画失败", e); }
    }

    // 📌 钉住 / 取消钉住（2026-09-19）。与报卡的纪律一致：**只就地改这一小块 DOM**，
    // 不重绘整张卡——重绘会把已经挂上的 AI 解析与追问框冲掉（他刚问的话就没了）。
    // 服务端语义见 src/card_policy.js 的 R-pin：钉住 = 智能组置顶 + 不受连对退役影响。
    function paintPinMarks(card) {
        try {
            const head = box.querySelector(".fs-head");
            if (head) {
                const old = head.querySelector(".fs-pin-badge");
                if (old && old.parentNode) old.parentNode.removeChild(old);
                if (card.pin) {
                    const span = document.createElement("span");
                    span.className = "fs-pin-badge";
                    span.title = "你钉住了这张卡｜它会一直排在智能组前面，也不会因为连对几次被收起来";
                    span.textContent = "📌 已钉住";
                    const anchor = head.querySelector("#fs-stats-btn");
                    if (anchor) head.insertBefore(span, anchor); else head.appendChild(span);
                }
            }
            const stem = box.querySelector(".fs-stem");
            if (stem) {
                const oldN = box.querySelector(".fs-pin-note");
                if (oldN && oldN.parentNode) oldN.parentNode.removeChild(oldN);
                if (card.pin) {
                    const div = document.createElement("div");
                    div.className = "fs-pin-note";
                    div.textContent = "📌 这张卡你钉住了——它会一直在智能组前面出现，"
                        + "就算连续答对也不会被收起来；不想要了再按一次取消。";
                    if (stem.parentNode) stem.parentNode.insertBefore(div, stem.nextSibling);
                }
            }
        } catch (e) { console.warn("[flash] 钉住状态补画失败", e); }
    }

    async function submitPin(card, want) {
        const btn = document.getElementById("fs-pin");
        const restore = () => {
            if (!btn) return;
            btn.disabled = false;
            btn.className = "fs-btn fs-pin" + (card.pin ? " on" : "");
            btn.textContent = card.pin ? "📌 已钉住" : "📌 钉住这张卡";
        };
        if (btn) { btn.disabled = true; btn.textContent = want ? "钉住中…" : "取消中…"; }
        try {
            const r0 = await fetch(API + "/api/flashcards/pin", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ card_id: card.card_id, pinned: want }),
            });
            let d = null;
            try { d = await r0.json(); } catch (e) { d = null; }
            // ⚠️ 旧版服务（改了 serve.js 没重启）会对这个 POST 回 404、body 不是 JSON。
            //    必须**明说**「重启大盘后生效」，不能含混成「无法连接本地服务」——
            //    报卡那边上线当天就踩过，人会去反复刷新。
            if (!r0.ok || !d || !d.ok) {
                restore();
                toast(r0.status === 404
                    ? "本地服务还是旧版，重启大盘后钉住才能用"
                    : ("钉住失败：" + ((d && d.error) || r0.status)));
                return;
            }
            card.pin = d.pinned ? { note: "", created_at: "" } : null;
            if (btn) {
                btn.disabled = false;
                btn.className = "fs-btn fs-pin" + (card.pin ? " on" : "");
                btn.textContent = card.pin ? "📌 已钉住" : "📌 钉住这张卡";
            }
            paintPinMarks(card);
            toast(card.pin ? "已钉住：这张卡会一直排在前面" : "已取消钉住");
        } catch (e) {
            restore();
            toast("钉住失败：无法连接本地服务");
        }
    }

    function togglePin(card) {
        // 本地卡组（早间回顾）的卡不在主闪卡库里，钉住没有意义（它本来就不会再出现）
        if (state.local) { toast("早间回顾的卡不在闪卡库里，不用钉"); return; }
        if (!card || !card.card_id) return;
        submitPin(card, !card.pin);
    }

    // 提交标记。**只就地改这一小块 DOM**（按钮 + 面板 + 徽标/提示条），不重绘整张卡——
    // 重绘会把已经挂上的 AI 解析与追问框冲掉，用户刚问的话就没了。
    async function submitReport(card, ct, kind, note) {
        const btn = document.getElementById("fs-flag-save");
        if (btn) { btn.disabled = true; btn.textContent = "标记中…"; }
        const restore = () => { if (btn) { btn.disabled = false; btn.textContent = "标记有问题"; } };
        try {
            const r0 = await fetch(API + "/api/flashcards/reports", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    card_id: card.card_id, kind: kind, note: String(note || "").trim(),
                    // chosen / correct 一起存：复核时能看出「他是按哪一项被判错的」，
                    // 光有「这题有问题」四个字，AI 还得重推一遍他的现场。
                    chosen: state.chosen || null,
                    // 正确答案存**可读形式**（「B. 乙」）而不是下标：复核的 AI 要一眼看出
                    // 标答是哪一项，光存个 "1" 还得回头查选项表（选择题才有下标，
                    // 判断题/填空题 answerText 本来就是文本）。
                    correct: (function () {
                        try {
                            const opts = optionsOf(card);
                            const ci = correctIndex(ct, opts);
                            if (opts.length && ci >= 0 && opts[ci] != null) {
                                return String.fromCharCode(65 + ci) + ". " + opts[ci];
                            }
                            return answerText(ct) || null;
                        } catch (e) { return null; }
                    })(),
                })
            });
            // ⚠️ 服务端还是**旧版**（改了 serve.js 却没重启）时这里回 404，body 也不是 JSON。
            //    这种情况必须说清是「重启大盘后生效」——含混成「无法连接本地服务」的话，
            //    人会以为是服务没开，去反复刷新甚至重装（首次上线当天就撞到一次）。
            if (!r0.ok) {
                toast(r0.status === 404
                    ? "标记失败：本地服务还是旧版 —— 双击桌面「启动考研大盘」重启后生效"
                    : ("标记失败：本地服务返回 HTTP " + r0.status));
                restore();
                return false;
            }
            const d = await r0.json();
            if (!d.ok) { toast("标记失败：" + (d.error || "")); restore(); return false; }
            // 本地也记上：撤销回头、下一组再遇到它时，徽标和提示还在。
            card.report = { id: d.id, kind: d.kind, kind_label: d.kind_label, note: String(note || "").trim() };
            const fb = document.getElementById("fs-flag");
            if (fb) { fb.className = "fs-btn fs-flag on"; fb.textContent = "⚑ 已标记：" + d.kind_label; }
            const panel = document.getElementById("fs-flag-panel");
            if (panel) panel.innerHTML = "";
            flagPanelOpen = false;
            paintReportMarks(card);   // 徽标 + 题面下的提示条立刻出现（就地插，不重绘）
            SFX.play("reveal");
            toast("已标记「" + d.kind_label + "」· 待修 " + d.count + " 张，AI 会在每日任务里核对");
            return true;
        } catch (e) {
            toast("标记失败：无法连接本地服务");
            restore();
            return false;
        }
    }

    /**
     * 本地卡组（早间回顾）的自评：**不写主闪卡库**。
     *
     * 唯一可能的回写是早间回顾自己的间隔重复（record === "mr-sr"，目前只有固卡组用），
     * 走 /api/morning-review/sr —— 那是它自己那张表（mr_sr），与 FSRS / review_log 无关。
     * 「没想起来 / 有点糊」的卡会**在本组末尾再问一遍**（只回炉一次，免得刷不完）。
     */
    async function submitLocalRating(r, c) {
        state.sending = true;
        let ok = true, resp = null;
        const L = state.local || {};
        if (L.record === "mr-sr") {
            try {
                const r0 = await fetch(API + "/api/morning-review/sr", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ card_id: c.card_id, mark: r >= 3 ? "ok" : "no", date: L.date || "" }),
                });
                resp = await r0.json();
                ok = !!(resp && resp.ok);
            } catch (e) { ok = false; }
        }
        state.sending = false;
        if (!ok) {
            toast("评分未保存：" + ((resp && resp.error) || "无法连接本地服务"));
            return false;
        }
        state.lastRating = r;
        state.stats[r] = (state.stats[r] || 0) + 1;
        state.lastRatedIdx = state.idx;
        state.saveFailed = false;
        if (r <= 2 && !c._requeued) {
            c._requeued = true;
            state.cards.push(c);        // 本组末尾再来一遍（答案他已经看过了）
        }
        return true;
    }

    /** 本地卡组的撤销：只回退本地指针与计数，没有服务端要还原的东西。 */
    function undoLocal() {
        const ti = (typeof state.lastRatedIdx === "number") ? state.lastRatedIdx : state.idx - 1;
        const c = state.cards[ti];
        if (!c) { toast("没有可撤销的评分"); return; }
        if (state.stats[state.lastRating]) state.stats[state.lastRating] -= 1;
        state.idx = ti;
        state.lastRatedIdx = null; state.lastRating = null;
        state.autoWrong = false; state.saveFailed = false;
        saveLocalSession();
        renderCard();
        toast("已撤销");
    }

    // 撤销上一次评分：服务端按 review_log 快照还原卡片，本地回到那张卡重新作答。
    // 目标卡由 lastRatedIdx 决定（不是 idx-1）：「选错即判」时 idx 还没翻页。
    async function undo() {
        if (state.sending) return;
        if (state.local) return undoLocal();
        const ti = (typeof state.lastRatedIdx === "number") ? state.lastRatedIdx : state.idx - 1;
        const c = state.cards[ti];
        if (!c) { toast("没有可撤销的评分"); return; }
        state.sending = true;
        try {
            const resp = await fetch(API + "/api/flashcards/undo", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({ card_id: c.card_id })
            });
            const d = await resp.json();
            if (d.ok) {
                state.idx = ti;
                state.lastRatedIdx = null;
                state.autoWrong = false; state.saveFailed = false; pendingSubmit = null;
                if (state.stats[state.lastRating]) state.stats[state.lastRating] -= 1;
                state.reviewedToday = Math.max(0, (state.reviewedToday || 0) - 1);
                state.lastRating = null;
                saveSession();
                renderCard();
                toast("已撤销");
            } else {
                toast(d.error || "撤销失败");
            }
        } catch (e) {
            toast("撤销失败：无法连接本地服务");
        }
        state.sending = false;
    }

    function toast(msg) {
        const t = document.createElement("div");
        t.className = "fs-toast";
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 1800);
    }

    // 只提交评分，不翻页。自动判错要先回写、再等用户翻页，所以拆出来。
    async function submitRating(r) {
        if (state.sending) return false;
        const c = state.cards[state.idx];
        if (!c) return false;
        if (state.local) return submitLocalRating(r, c);
        state.sending = true;
        let ok = false, resp = null;
        try {
            const r0 = await fetch(API + "/api/flashcards/review", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                // chosen 一并回写：复盘要能看出「错在哪」而不只是「错了」。
                // 填空/简答与「直接看答案」时为 null，服务端会存 NULL。
                body: JSON.stringify({ card_id: c.card_id, rating: r, chosen: state.chosen || null })
            });
            resp = await r0.json();
            ok = !!resp.ok;
        } catch (e) { ok = false; }

        state.sending = false;
        if (!ok) {
            toast("评分未保存：" + ((resp && resp.error) || "无法连接本地服务"));
            return false;
        }
        state.lastRating = r;
        state.stats[r] = (state.stats[r] || 0) + 1;
        state.reviewedToday = (state.reviewedToday || 0) + 1;
        state.lastRatedIdx = state.idx;
        state.saveFailed = false;
        return true;
    }

    // 翻到下一张。若自动判错的回写还在路上，等它落地再翻，失败则不翻。
    function goNext() {
        const go = () => { state.idx += 1; saveSession(); renderCard(); };
        SFX.play("flip");
        if (pendingSubmit) { pendingSubmit.then(() => { if (!state.saveFailed) go(); }); return; }
        if (state.saveFailed) return;
        go();
    }

    async function rate(r) {
        if (!state.revealed || state.sending) return;
        SFX.play("rate", r);
        if (await submitRating(r)) goNext();
    }

    /**
     * 本地卡组（早间回顾）刷完：报成绩 + **自动打卡**（早间回顾页给的回调）。
     * 「刷完这一组 = 今天的早间回顾做完了」——这正是用户要的那条链。
     */
    function renderLocalSummary(total) {
        const L = state.local || {};
        const s = state.stats;
        const cards = state.cards.slice();
        clearLocalSession();          // 这一组已经做完，不再提供「继续本组」
        markLocalDone(L.date);        // 哪几天整组刷完过（早间回顾页据此写「已刷完」）
        state.local = null;           // 身份立刻退掉：否则下一组普通闪卡会被当成本地卡组
        localSummary = true;          // 练习区里现在留的是「早间回顾那一组」的总结屏
        box.innerHTML = '<div class="fs-summary"><h3>'
            + esc(L.title || "本组") + ' 完成 🎉</h3>'
            + '<p>共 ' + total + ' 张 · 没想起来 ' + (s[1] || 0) + ' · 有点糊 ' + (s[2] || 0)
            + ' · 想起来了 ' + (s[3] || 0) + ' · 很熟 ' + (s[4] || 0) + '</p>'
            + '<p class="fs-summary-time">本组用时 <b>' + fmtClock(STUDY.ms) + '</b></p>'
            + (L.onFinish ? '<div class="fs-local-finish" id="fs-local-finish">正在打卡…</div>' : '')
            + '<div class="fs-actions">'
            + '<button class="fs-btn" id="fs-local-again">↺ 再刷一遍</button>'
            + '<button class="fs-btn" id="fs-local-close">✕ 收起练习区</button>'
            + '</div></div>';
        const again = document.getElementById("fs-local-again");
        if (again) again.onclick = () => {
            clearLocalSession();
            startLocalGroup(cards, L.title, {
                kind: L.kind, date: L.date, record: L.record, onFinish: L.onFinish,
            });
        };
        const cb = document.getElementById("fs-local-close");
        if (cb) cb.onclick = () => {
            const api = globalThis.__flashFloat;
            if (api && typeof api.close === "function") api.close();
        };
        if (L.onFinish) {
            // 打卡是网络活，不阻塞总结屏；回来把结果写在那一行上。
            Promise.resolve().then(L.onFinish).then(function (msg) {
                const el = document.getElementById("fs-local-finish");
                if (el) el.textContent = String(msg || "");
            }).catch(function (e) {
                const el = document.getElementById("fs-local-finish");
                if (el) el.textContent = "⚠ 打卡失败：" + ((e && e.message) || e);
            });
        }
    }

    function renderSummary() {
        const s = state.stats;
        const total = s[1] + s[2] + s[3] + s[4];
        endStudy();            // 本组刷完 = 这一段的表停下来，用时留在 summary 上
        SFX.play("done");
        if (state.local) return renderLocalSummary(total);
        clearSession();
        box.innerHTML = '<div class="fs-summary"><h3>本组完成 🎉</h3>'
            + '<p>共 ' + total + ' 张 · 忘记 ' + s[1] + ' · 模糊 ' + s[2] + ' · 记得 ' + s[3] + ' · 简单 ' + s[4] + '</p>'
            + '<p class="fs-summary-time">本组用时 <b>' + fmtClock(STUDY.ms) + '</b>'
            + ' · 今日累计专注 <b>' + fmtClock(STUDY.dayMs) + '</b>'
            + '<span class="fs-muted">（失焦期间不计时）</span></p>'
            + '<p style="font-size:0.75rem;color:var(--text-muted);">今日累计已复习 ' + (state.reviewedToday || 0) + ' 张</p>'
            + '<button class="fs-btn" id="fs-again">再来一组</button></div>';
        const again = document.getElementById("fs-again");
        // 组末「再来一组」：同样先摘点名；智能组空了（今日额度用完）直接接上
        // 「今日刷完后再来 N 张」那一组——省掉「空屏上再点一次」这一步。
        if (again) again.onclick = () => startNewGroup({ autoExtra: true });
    }

    // ---- 统计面板：30 天到期预测 + 成熟度分布 + 正确率（数据来自服务端聚合）----
    async function showStats() {
        const panel = document.getElementById("fs-stats-panel");
        if (!panel) return;
        if (panel.dataset.open === "1") { panel.innerHTML = ""; panel.dataset.open = "0"; return; }
        panel.dataset.open = "1";
        panel.innerHTML = '<div class="fs-loading">正在加载统计…</div>';
        try {
            const d = await (await fetch(API + "/api/flashcards/stats")).json();
            if (!d.ok) throw new Error(d.error || "stats failed");
            const m = d.maturity || {};
            const maxF = Math.max(1, ...(d.forecast || []).map(x => x.count));
            const bars = (d.forecast || []).map(x =>
                '<div class="fs-fbar" title="' + x.date + '：' + x.count + ' 张">'
                + '<div class="fs-fbar-fill" style="height:' + Math.round(100 * x.count / maxF) + '%"></div>'
                + '</div>').join("");
            const acc = d.accuracy_30d == null ? "—" : Math.round(d.accuracy_30d * 100) + "%";
            panel.innerHTML = ''
                + '<div class="fs-stat-row">'
                +   '<span>总卡 <b>' + d.total_cards + '</b></span>'
                +   '<span>待复习 <b>' + d.due_now + '</b></span>'
                +   '<span>可学新卡 <b>' + d.new_available + '</b></span>'
                +   '<span>水蛭 <b>' + d.leech + '</b></span>'
                +   '<span>近30天正确率 <b>' + acc + '</b>（' + d.reviews_30d + ' 次）</span>'
                + '</div>'
                + '<div class="fs-stat-row fs-maturity">'
                +   '<span>新卡 <b>' + (m.new || 0) + '</b></span>'
                +   '<span>学习中 <b>' + (m.learning || 0) + '</b></span>'
                +   '<span>年轻(&lt;21天) <b>' + (m.young || 0) + '</b></span>'
                +   '<span>成熟(≥21天) <b>' + (m.mature || 0) + '</b></span>'
                +   '<span>已暂停 <b>' + (m.suspended || 0) + '</b></span>'
                + '</div>'
                + '<div class="fs-forecast-label">未来 30 天到期预测</div>'
                + '<div class="fs-forecast">' + bars + '</div>';
            // 待修清单（「这道题有问题」标记）：用户得看得见「我的标记还在等处理」，
            // 否则标完就没了下文，他会以为白标了。数据与每日任务里 agent 看的是同一份。
            try {
                const rep = await (await fetch(API + "/api/flashcards/reports?status=open&limit=20")).json();
                const n = (rep && rep.ok) ? (rep.open_count || 0) : 0;
                if (n > 0) {
                    panel.insertAdjacentHTML("beforeend",
                        '<div class="fs-report-list">'
                        + '<div class="fs-report-item">⚑ 待修的问题卡 <b>' + n + '</b> 张'
                        + '<span class="fs-muted">（已排进每日任务，AI 会核对修复；筛选页「⚑ 待修」可直接复看）</span></div>'
                        + rep.reports.map(r => '<div class="fs-report-item">· <b>' + esc(r.kind_label) + '</b> '
                            + esc([r.subject, r.topic_name].filter(Boolean).join("·")) + ' '
                            + '<span class="fs-report-stem">'
                            + esc(String(r.stem || "").slice(0, 46)) + '</span></div>').join("")
                        + '</div>');
                }
            } catch (e) { /* 清单取不到不影响统计本身 */ }
        } catch (e) {
            panel.innerHTML = '<div class="fs-empty">统计加载失败：' + esc(e.message) + '</div>';
        }
    }

    // ============================================================
    // 筛选页：状态桶 × 科目 + 数量（数量来自 /api/flashcards/facets）
    // 桶的判定与服务端 session/facets 端点同源，「已掌握」= interval_days >= 21。
    // 数量为 0 的桶显示成**禁用灰态而不是隐藏**——用户要的就是看见「水蛭 0 /
    // 已暂停 0」这种真实状态，藏起来反而像在骗人。
    // ============================================================
    const fbox = document.getElementById("flash-filter");
    const BUCKET_META = [
        { key: "", label: "全部" }, { key: "new", label: "未学习" },
        { key: "learning", label: "学习中" }, { key: "review", label: "复习中" },
        { key: "mature", label: "已掌握" }, { key: "leech", label: "水蛭" },
        { key: "suspended", label: "已暂停" }, { key: "flagged", label: "⚑ 待修" },
        // 📌 钉住（2026-09-19）：他自己钉的卡。智能组里它们永远排最前，
        // 这里给他一个「我钉过哪些」的总览入口。
        { key: "pinned", label: "📌 钉住" },
    ];
    let facets = null;

    async function loadFacets() {
        try {
            const d = await (await fetch(API + "/api/flashcards/facets")).json();
            facets = (d && d.ok) ? d : null;
        } catch (e) { facets = null; }
        renderFilterBar();
    }

    // 桶计数：选了科目就看该科目的，否则看全局合计
    function bucketCount(key) {
        if (!facets) return null;
        let src;
        if (pending.subject) {
            src = (facets.subjects || []).filter(s => s.subject === pending.subject)[0];
        } else {
            src = facets.totals;
        }
        if (!src) return 0;
        return key ? (src[key] || 0) : (src.total || 0);
    }
    // 科目计数：选了桶就只数那个桶
    function subjectCount(subject) {
        if (!facets) return null;
        let n = 0, seen = false;
        for (const row of (facets.subjects || [])) {
            if (subject && row.subject !== subject) continue;
            seen = true;
            n += pending.bucket ? (row[pending.bucket] || 0) : (row.total || 0);
        }
        return seen ? n : 0;
    }

    function chip(label, value, n, active, attr) {
        const empty = (n === 0);
        return '<button class="ff-chip' + (active ? " on" : "") + (empty ? " empty" : "") + '" '
            + attr + '="' + esc(value) + '"' + (empty && !active ? " disabled" : "") + '>'
            + esc(label) + (n == null ? "" : '<span class="ff-n">' + n + "</span>") + "</button>";
    }

    // 知识点粒度：按「科目-章节」两段前缀归并（408-OS / MATH-GS / POL-XX …），
    // 口径与薄弱点模块 topicPrefix() 一致。session 端点按 t.id LIKE '<前缀>%' 匹配，
    // 所以选中 408-OS 会把下面的所有子主题一起划进来。
    function topicCount(prefix) {
        if (!facets || !prefix) return null;
        for (const t of (facets.topics || [])) {
            if (t.prefix !== prefix) continue;
            if (pending.subject && t.subject !== pending.subject) continue;
            return pending.bucket ? (t[pending.bucket] || 0) : (t.total || 0);
        }
        return 0;
    }
    function visibleTopics() {
        const list = facets && Array.isArray(facets.topics) ? facets.topics : [];
        if (!pending.subject) return list;
        return list.filter(t => t.subject === pending.subject);
    }
    // 把筛选对象翻译成一句话，浮窗底栏「当前范围」与收起态说明共用。
    function scopeParts(f) {
        const parts = [];
        if (!f) return parts;
        if (f.subject) parts.push(f.subject);
        const bm = BUCKET_META.filter(b => b.key === f.bucket)[0];
        if (bm && bm.key) parts.push(bm.label);
        if (f.topic) parts.push("考点 " + f.topic);
        if (Array.isArray(f.ids) && f.ids.length) parts.push("指定 " + f.ids.length + " 张");
        return parts;
    }

    // 浮窗关闭后不要丢掉已选范围：应用后的 state.filter 才是「正在生效」的，
    // pending 是浮窗里待确认的选择。二者在「开始刷题」后经 startWithFilter 收敛。
    let filterOv = null;
    // 当前待确认筛选精确命中的卡数：科目/考点各自已内部再叠加 bucket，
    // 所以按「考点 > 科目 > 状态桶 > 全部」取最窄一层即可对齐当前范围。
    function pendingCount() {
        if (!facets) return null;
        if (pending.topic) return topicCount(pending.topic);
        if (pending.subject) return subjectCount(pending.subject);
        if (pending.bucket) return bucketCount(pending.bucket);
        return facets.totals ? (facets.totals.total || 0) : 0;
    }
    function renderFilterBar() {
        if (!fbox) return;
        const f = state.filter ? normalizeFilter(state.filter) : null;
        const parts = (f && Array.isArray(f.ids) && f.ids.length)
            ? scopeParts(f)
            : scopeParts({ subject: pending.subject, bucket: pending.bucket, topic: pending.topic });
        const scope = parts.length ? parts.join(" · ") : "全部闪卡";
        const n = pendingCount();
        // 平时收成一行说明 + 一个按钮，整张筛选表点开变成浮窗
        fbox.innerHTML =
            '<div class="fl-row"><span class="fl-label">📚 范围</span>'
            + '<span class="fl-scope">' + esc(scope) + (n == null ? "" : " · <b>" + n + "</b> 张") + "</span>"
            + '<button class="fs-btn" id="fl-open">🔎 打开闪卡筛选</button>'
            + '</div>';
        const ob = fbox.querySelector("#fl-open");
        if (ob) ob.onclick = openFilterModal;
    }

    function buildFilterModal(ov) {
        const buckets = BUCKET_META.map(b =>
            chip(b.label, b.key, bucketCount(b.key), pending.bucket === b.key, "data-bucket")).join("");
        const subs = [""].concat(SUBJECTS).map(s =>
            chip(s || "全部", s, subjectCount(s), pending.subject === s, "data-subject")).join("");
        const vt = visibleTopics();
        const tops = vt.length
            ? vt.map(t =>
                chip(t.prefix, t.prefix, topicCount(t.prefix), pending.topic === t.prefix, "data-topic")).join("")
            : '<span class="ff-nothing">（先选科目，或当前没有带章节的知识点卡片）</span>';
        const n = pendingCount();
        const parts = scopeParts({ subject: pending.subject, bucket: pending.bucket, topic: pending.topic });
        const scope = parts.length ? parts.join(" · ") : "全部闪卡";
        const body = ov.querySelector(".fl-body");
        body.innerHTML =
            '<div class="ff-group"><div class="ff-label">状态</div><div class="ff-chips">' + buckets + '</div></div>'
            + '<div class="ff-group"><div class="ff-label">科目</div><div class="ff-chips">' + subs + '</div></div>'
            + '<div class="ff-group ff-topics"><div class="ff-label">知识点</div>'
            +   '<div class="ff-chips">' + tops + '</div></div>'
            + '<div class="ff-foot">'
            +   '<span class="ff-summary">当前范围：' + esc(scope)
            +     (n == null ? "" : ' · <b>' + n + '</b> 张') + '</span>'
            +   '<button class="fs-btn fs-next" id="ff-start">开始刷题</button>'
            +   '<button class="fs-btn" id="ff-all">全部闪卡</button>'
            + '</div>';
        body.querySelectorAll("[data-bucket]").forEach(b => {
            b.onclick = () => { pending.bucket = b.dataset.bucket; buildFilterModal(ov); };
        });
        body.querySelectorAll("[data-subject]").forEach(b => {
            b.onclick = () => {
                pending.subject = b.dataset.subject;
                pending.topic = "";      // 换了科目，上一科的考点不再适用，一起清掉
                buildFilterModal(ov);
            };
        });
        body.querySelectorAll("[data-topic]").forEach(b => {
            b.onclick = () => {
                pending.topic = b.dataset.topic;
                buildFilterModal(ov);
            };
        });
        const sb = body.querySelector("#ff-start");
        if (sb) sb.onclick = () => { closeFilterModal(); startWithFilter(pending); };
        const ab = body.querySelector("#ff-all");
        if (ab) ab.onclick = () => {
            pending = { subject: "", bucket: "", topic: "" };
            closeFilterModal();
            startWithFilter(null);
        };
    }

    function openFilterModal() {
        if (!filterOv) {
            const page = fbox.closest('.page[data-page="flash"]') || document.body;
            filterOv = document.createElement("div");
            filterOv.className = "sk-overlay";      // 复用快捷键浮窗同款遮罩/容器样式
            filterOv.innerHTML =
                '<div class="sk-box">'
                + '<div class="sk-head"><span class="sk-title">🔎 闪卡范围筛选</span>'
                + '<button class="rev-modal-close" id="fl-close" type="button" '
                + 'title="关闭（Esc）">✕</button></div>'
                + '<div class="sk-body fl-body"></div>'
                + '</div>';
            page.appendChild(filterOv);
            filterOv.querySelector("#fl-close").onclick = closeFilterModal;
            // 点遮罩空白处关掉；同样用 capture、先于其它层注册，避免录键等冲突
            filterOv.addEventListener("click", ev => { if (ev.target === filterOv) closeFilterModal(); });
            document.addEventListener("keydown", function (ev) {
                if (!filterOv.hidden && ev.key === "Escape") closeFilterModal();
            }, true);
        }
        buildFilterModal(filterOv);
        filterOv.hidden = false;
    }
    function closeFilterModal() {
        if (filterOv) filterOv.hidden = true;
    }

    // 键盘分四个阶段，互不重叠（数字键在不同阶段含义不同，靠状态消歧）：
    //   ① 未作答的选择/判断 → A–D / 1–4 选选项，空格看答案
    //   ② 已自动判错        → 空格/回车 下一张，U 撤销重答
    //   ③ 已显示答案        → 1–4 自评（空格 = 记得，Anki 惯例）
    //   ④ 未显示答案的填空/简答 → 空格显示答案
    document.addEventListener("keydown", (e) => {
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        // 闸门态（还没点开始）不接键盘：那时按数字键是在翻网页，不是在答题。
        // ⚠️ 必须显式看 STUDY.started——结束本组后 state.cards 可能还留着上一组的
        //    数组（LS 里的进度要留给「继续本组」），只看长度会串台。
        if (!STUDY.started) return;
        if (!state.cards.length || state.idx >= state.cards.length) return;
        const k = e.key;
        // 键位一律问总表（见 KEYS_JS），这里不再自己比 ev.key / ev.code：
        // 用户在「设置 → 快捷键」里改了键，这两行自动跟着变，提示文案也是同一份。
        // ⚠️ 别再写回 e.code === "Space" —— 空格在 ev.key 里是 " "，两套写法混着
        //    最容易出「改了键没反应」这种查半天的 bug（KEYS_JS 的 canonKey 在收口）。
        const isShowAnswer = __keys.matches("flash.reveal", e);
        if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) {
            // ⚠️ 追问框（#fs-exp-input）会吃掉数字键：用户点过它、或它刚被挂上时，
            //    按 1/2/3/4 本想标熟练度，结果打进了输入框（2026-09-21 用户反馈）。
            //    判据：**空输入框里第一个字符不可能是有意义的提问** —— 数字与空格一律放行给闪卡快捷键。
            //    只在「空 + 数字/空格」时放行，所以正常打字、以及简答题作答区完全不受影响。
            //    数字与空格放行给闪卡快捷键（空框里第一个字符不可能是提问）。
            //
            // ⚠️ 回车**不在放行名单里**（2026-09-17 用户反馈：「在输入框里回车就是发送的意思，
            //    下一张交给空格」）。以前回车也放行，于是空框按回车会翻页——可人的直觉是
            //    「回车=发送」，而发送一条空消息本来就该什么都不发生。翻页请按空格。
            const empty = !String(e.target.value || "");
            // 「回车」是显示答案键的别名，所以这里要显式把它摘掉——光看
            // isShowAnswer 的话回车也会被放行，正好和上面那条 2026-09-17 的决定相反。
            const shortcutKey = /^[1-9]$/.test(k) || (isShowAnswer && k !== "Enter");
            if (!(e.target.id === "fs-exp-input" && empty && shortcutKey)) return;
        }
        // 读笔记弹层 / 卡组覆盖层打开时不要评分——那时数字键是在翻笔记，不是在答题
        if (document.querySelector(".rev-modal, .deck-overlay")) return;
        // 番茄钟全屏时同样要闭嘴：它盖在最上面，但按键事件还是会打到这一层来
        if (typeof globalThis.__pomoFullscreen === "function" && globalThis.__pomoFullscreen()) return;

        // 撤销在哪个阶段都管用，先拦下来。选项键只占 A–I / 1–9，不会和它撞。
        if (__keys.matches("flash.undo", e)) { e.preventDefault(); undo(); return; }
        // 📌 钉住也任何阶段都能按（对错都能钉，这正是用户要的：「就算我对的那些题」）。
        // 默认键 P，不与选项键 A–I / 数字键 / 空格冲突；在输入框里打字不受影响
        // （上面那段 INPUT/TEXTAREA 守卫已经先 return 了）。
        // 注意：**按钮**在反馈区（要揭晓后才出现），快捷键不受这个限制——它是文档层的。
        if (__keys.matches("flash.pin", e)) {
            e.preventDefault();
            togglePin(state.cards[state.idx] || {});
            return;
        }

        // ① 选选项
        const opts = optionButtons();
        if (opts.length && !state.answered && !state.revealed) {
            let i = -1;
            if (/^[a-iA-I]$/.test(k)) i = k.toUpperCase().charCodeAt(0) - 65;
            else if (/^[1-9]$/.test(k)) i = parseInt(k, 10) - 1;
            if (i >= 0) {
                if (i < opts.length) { e.preventDefault(); opts[i].click(); }
                return;   // 超出范围的键位不落到下面的分支去
            }
            if (isShowAnswer) { e.preventDefault(); reveal(); }
            return;
        }

        // ② 已自动判错：只等翻页
        if (state.autoWrong) {
            if (isShowAnswer) { e.preventDefault(); goNext(); }
            return;
        }

        // ③ 自评
        if (state.revealed) {
            if (["1", "2", "3", "4"].includes(k)) { e.preventDefault(); rate(parseInt(k, 10)); }
            else if (isShowAnswer) { e.preventDefault(); rate(3); }
            return;
        }

        // ④ 填空/简答：先看答案
        if (isShowAnswer) {
            e.preventDefault();
            const skipBtn = document.getElementById("fs-skip");   // 简答卡：空格 = 先看参考答案
            const bodyBtn = skipBtn || document.querySelector("#fs-body .fs-btn");
            if (bodyBtn) bodyBtn.click();
        }
    });

    // ============================================================
    // 闪卡浮窗（2026-09-22）：让别的页也能「直接调用闪卡模块」
    //
    // 做法是**节点搬家**：打开浮窗时把练习区（#flash-practice 整块）搬进 body 层的
    // #fs-float，关掉再放回「闪卡」页。为什么不复制一套练习 UI —— 全屏、音效、
    // 学习计时、键盘、AI 解析、评分回写、进度同步全都挂在练习区自己那套 DOM 与
    // 闭包上，复制等于把这些逻辑重写一遍，之后每改一处都要改两遍。
    //
    // ⚠️ 这条路上唯一必须守的规矩：**别给 .fs-float 加 transform / filter /
    //    backdrop-filter / contain**（拖动只能改 left/top）。那会给里面的
    //    .section.is-full（position:fixed）换一个包含块，全屏就从「铺满视口」
    //    缩成「铺满浮窗」，功能直接残掉——而且现象看着像「全屏坏了」，很难查。
    //
    // 对外只开一个接口 window.__flashFloat（学习页/复盘页都只碰这一个）：
    //   open(title?)               打开浮窗（顺带把练习区搬进来）
    //   close()                    收起浮窗并放回闪卡页
    //   isOpen()
    //   practice(cardIds, title?)  「就练这几张」：按卡号精确组题并直接开练
    // ============================================================
    const FLOAT_GEO_KEY = "kaoyan_flash_float_geo_v1";
    const floatBox = document.getElementById("fs-float");
    const floatBody = document.getElementById("fs-float-body");
    const floatTitleEl = document.getElementById("fs-float-title");
    const practiceEl = document.getElementById("flash-practice");
    // 老家：关浮窗要放回去。.page 容器是 HTML 里写死的、永不重建，所以这里记一次就够。
    const practiceHome = practiceEl ? practiceEl.parentNode : null;
    const practiceHomeNext = practiceEl ? practiceEl.nextSibling : null;
    let floatShowing = false;

    function saveFloatGeo() {
        if (!floatBox) return;
        try {
            localStorage.setItem(FLOAT_GEO_KEY, JSON.stringify({
                x: floatBox.offsetLeft, y: floatBox.offsetTop,
                w: floatBox.offsetWidth, h: floatBox.offsetHeight,
            }));
        } catch (e) {}
    }
    function readFloatGeo() {
        try {
            const g = JSON.parse(localStorage.getItem(FLOAT_GEO_KEY) || "null");
            return (g && typeof g === "object") ? g : {};
        } catch (e) { return {}; }
    }
    // 把浮窗摆到视口内的合法位置。缺省尺寸 720×560、右上偏移 24/72。
    // ⚠️ 只设 left/top/width/height —— 设 transform 会破全屏（见上面那段）。
    function applyFloatGeo(g) {
        if (!floatBox) return;
        const vw = (typeof window !== "undefined" && window.innerWidth) || 1280;
        const vh = (typeof window !== "undefined" && window.innerHeight) || 800;
        const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : null);
        const w = Math.max(320, Math.min(num(g.w) || 720, Math.max(320, vw - 16)));
        const h = Math.max(240, Math.min(num(g.h) || 560, Math.max(240, vh - 16)));
        const gx = num(g.x), gy = num(g.y);
        const x = Math.max(0, Math.min(gx == null ? (vw - w - 24) : gx, Math.max(0, vw - w)));
        const y = Math.max(0, Math.min(gy == null ? 72 : gy, Math.max(0, vh - h)));
        floatBox.style.width = w + "px";
        floatBox.style.height = h + "px";
        floatBox.style.left = x + "px";
        floatBox.style.top = y + "px";
    }

    function openFloat(title) {
        if (!floatBox || !floatBody || !practiceEl) return;
        if (!floatShowing) {
            applyFloatGeo(readFloatGeo());
            floatBody.appendChild(practiceEl);   // 搬家（同一个节点，状态与计时都不重置）
            floatBox.hidden = false;
            floatShowing = true;
        }
        if (title && floatTitleEl) floatTitleEl.textContent = title;
    }
    function closeFloat() {
        if (!floatShowing || !floatBox) return;
        saveFloatGeo();
        // 先退全屏再搬：全屏态下 body 上有 fs-lock（overflow:hidden），
        // 直接收起来会留一个「页面锁死滚不动」的壳。
        if (typeof globalThis.__flashSetFull === "function") {
            try { globalThis.__flashSetFull(false); } catch (e) {}
        }
        if (practiceHome) practiceHome.insertBefore(practiceEl, practiceHomeNext);
        floatBox.hidden = true;
        floatShowing = false;
        // 练习区回闪卡页了：要是它里面停着「早间回顾那一组」的总结屏，就换回闪卡页的闸门
        if (typeof globalThis.__flashResetLocalView === "function") {
            try { globalThis.__flashResetLocalView(); } catch (e) {}
        }
    }
    globalThis.__flashFloat = {
        open: openFloat,
        close: closeFloat,
        isOpen: function () { return floatShowing; },
        // 精确到卡号开练：AI 手写的题先在服务端归卡（/api/study/cards），拿着
        // card_id 回来走这一条。组题仍走闪卡自己那条 filter 通道（ids），
        // 所以评分、撤销、进度存档、多端续刷全都照旧，不是一套「简化版练习」。
        practice: function (ids, title) {
            const list = (Array.isArray(ids) ? ids : [])
                .map(function (x) { return String(x == null ? "" : x); })
                .filter(function (x) { return /^[A-Za-z0-9_-]{1,40}$/.test(x); })
                .slice(0, 40);
            if (!list.length) return false;
            openFloat(title);
            startWithFilter({ ids: list });
            return true;
        },
        // 本地卡组（2026-09-20，早间回顾用）：把调用方拼好的 {id, front, back, secLabel}
        // 直接翻卡自评，**不碰主闪卡库**（不组题、不写 review_log、不占额度、不推进度）。
        // opts: {kind, date, title, record:'mr-sr'|'', onFinish:fn, resume}
        //   resume=true → 接着这个 date+kind 没刷完的那一组（从 localStorage 取回）。
        local: function (cards, title, opts) {
            opts = opts || {};
            if (opts.resume) {
                const saved = readLocalSession(opts.date, opts.kind);
                if (saved) {
                    openFloat(title);
                    startLocalGroup(saved.cards, saved.title || title,
                        Object.assign({}, opts, { idx: saved.idx, stats: saved.stats }));
                    return true;
                }
            }
            const list = normalizeLocalCards(cards);
            if (!list.length) return false;
            openFloat(title);
            startLocalGroup(list, title, opts);
            return true;
        },
    };

    (function wireFloat() {
        const bar = document.getElementById("fs-float-bar");
        const closeBtn = document.getElementById("fs-float-close");
        const homeBtn = document.getElementById("fs-float-home");
        if (closeBtn) closeBtn.onclick = closeFloat;
        if (homeBtn) homeBtn.onclick = function () {
            // 收起 + 跳回闪卡页：练习区回了老家，那边看上去才是完整的
            closeFloat();
            try { location.hash = "#/flash"; } catch (e) {}
        };
        if (!bar || !floatBox) return;

        // 拖动：pointerdown 只记起点，走够 5px 才认为是在拖。
        // ⚠️ 先放行按钮（pointer capture 会把 click 改派走），且不 preventDefault
        //    ——防滚动靠标题栏上的 touch-action: none，不是靠吃掉默认行为。
        let drag = null;
        bar.addEventListener("pointerdown", function (ev) {
            if (ev.target && ev.target.closest && ev.target.closest("button,input,a,select")) return;
            if (ev.button != null && ev.button !== 0) return;
            const r = floatBox.getBoundingClientRect();
            drag = { px: ev.clientX, py: ev.clientY, left: r.left, top: r.top, moved: false };
        });
        bar.addEventListener("pointermove", function (ev) {
            if (!drag) return;
            const dx = ev.clientX - drag.px, dy = ev.clientY - drag.py;
            if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 5) return;
            if (!drag.moved) {
                drag.moved = true;
                try { if (bar.setPointerCapture) bar.setPointerCapture(ev.pointerId); } catch (e) {}
            }
            applyFloatGeo({ x: drag.left + dx, y: drag.top + dy,
                            w: floatBox.offsetWidth, h: floatBox.offsetHeight });
        });
        const endDrag = function (ev) {
            if (!drag) return;
            const moved = drag.moved;
            drag = null;
            try {
                if (ev && bar.hasPointerCapture && bar.hasPointerCapture(ev.pointerId)) {
                    bar.releasePointerCapture(ev.pointerId);
                }
            } catch (e) {}
            if (moved) saveFloatGeo();
        };
        bar.addEventListener("pointerup", endDrag);
        bar.addEventListener("pointercancel", endDrag);

        if (typeof window === "undefined" || typeof window.addEventListener !== "function") return;
        // 窗口尺寸变了（横屏↔竖屏、缩放）要重新钳制，否则浮窗会留在屏幕外
        window.addEventListener("resize", function () {
            if (!floatShowing) return;
            applyFloatGeo({ x: floatBox.offsetLeft, y: floatBox.offsetTop,
                            w: floatBox.offsetWidth, h: floatBox.offsetHeight });
        });
        // 切回闪卡页 = 练习区该回家了。留着浮窗的话，闪卡页看上去像「练习区没了」。
        window.addEventListener("hashchange", function () {
            let h = "";
            try { h = String(location.hash || ""); } catch (e) {}
            if (floatShowing && h.indexOf("flash") >= 0) closeFloat();
        });
    })();

    // 启动时把上次的筛选选择也恢复出来，让筛选页显示的选择和场上正在刷的卡一致
    const savedFilter = readFilter();
    if (savedFilter) {
        pending = { subject: savedFilter.subject, bucket: savedFilter.bucket, topic: savedFilter.topic || "" };
        state.filter = savedFilter;
    }
    renderFilterBar();
    loadDayMs();
    // 「⏹ 结束」：把表停下来、回到闸门。localStorage 里没刷完的本组照旧留着，
    // 所以回来还能「继续本组」——但时间不会再偷偷往上走。
    const stopBtn = document.getElementById("fs-stop");
    if (stopBtn) stopBtn.onclick = () => {
        endStudy();
        // 场上必须清空：闸门态下若还留着上一组的 cards，键盘评分与空格翻页会
        // 打到已经看不见的卡上去（LS 里那份才是留给「继续本组」的）。
        state.cards = []; state.idx = 0; state.revealed = false; state.answered = false;
        state.local = null;    // 本地卡组（早间回顾）身份也一起退掉
        renderGate();
    };
    fetchToday();
    loadExtraCount();      // 「再来一组」的张数（头部按钮上写出来）
    loadFacets();
    // 2026-09-21：不再一加载就自动组题。以前脚本一跑就拉一整组卡并渲染第一张，
    // 于是「还没打算刷」也被算进学习时长，人走开表照转。现在一律先停在闸门。
    renderGate();
})();

// ============================================================
// 闪卡全屏练习（2026-09-14）
// 学习时最烦的是出答案把整页顶来顶去——内容一长，滚动位置就跟着跑。
// 全屏后练习区 position:fixed 铺满视口并自己滚动，解析只在内部撑开。
// ESC 退出；切走页面时自动退出，否则 body 上的 overflow:hidden 会留着，
// 把「笔记盘活」「练习活动」也一起锁死滚不动。
// ============================================================
(function () {
    const KEY = "kaoyan.flash.fullscreen";
    const section = document.getElementById("flash-practice");
    const btn = document.getElementById("fs-full-toggle");
    if (!section || !btn) return;

    const isFull = () => section.classList.contains("is-full");
    const onFlashPage = () => (location.hash || "").replace(/^#\/?/, "") === "flash";

    // 按钮上的文字/提示单独抽出来：改键位时要能只刷新文案，不能顺手再
    // setFull 一次（那会在已经全屏时又 requestFullscreen，白挨一次浏览器告警）。
    function syncLabel() {
        const on = isFull();
        // 键名从总表读，用户改了键这儿自动跟着变（别再写死 "F"）
        const fk = __keys.pretty(__keys.specOf("flash.full"));
        const label = on ? ("退出全屏（" + fk + " / Esc）") : ("全屏练习（" + fk + "，Esc 退出）");
        btn.textContent = on ? "✕ 退出全屏" : "⛶ 全屏";
        btn.title = label;
        btn.setAttribute("aria-label", label);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
    }

    function setFull(on) {
        section.classList.toggle("is-full", on);
        document.body.classList.toggle("fs-lock", on);
        syncLabel();
        try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (e) {}
        // 2026-09-21 起和番茄钟一套做法：进全屏时向浏览器申请**对这个模块**的真全屏，
        // 铺满物理屏幕、不再需要用户自己按 F11。拿不到（没有用户手势 / 浏览器不支持）
        // 就退回 .is-full 的 CSS 铺满，功能不降级。
        if (on) {
            try {
                const pr = section.requestFullscreen
                    ? section.requestFullscreen() : Promise.reject(new Error("no fullscreen api"));
                if (pr && pr.catch) pr.catch(function () { /* 被拦就用 CSS 铺满，够用 */ });
            } catch (e) {}
        } else if (document.fullscreenElement && document.exitFullscreen) {
            try { const pr = document.exitFullscreen(); if (pr && pr.catch) pr.catch(function () {}); } catch (e) {}
        }
    }

    // 浮窗收起时要连全屏一起退——否则浮窗关了，body 上的 fs-lock（overflow:hidden）
    // 还留着，整页滚不动。浮窗模块在另一个 IIFE 里，只能走 globalThis 搭桥。
    globalThis.__flashSetFull = setFull;

    // 原生那层被 Esc 退掉时，CSS 这层要跟着退——否则会剩一个「铺满但已经不是全屏」的壳，
    // 用户得再按一次 Esc 才出得去（番茄钟那边同理）。
    document.addEventListener("fullscreenchange", function () {
        if (!document.fullscreenElement && isFull()) setFull(false);
    });

    let saved = false;
    try { saved = localStorage.getItem(KEY) === "1"; } catch (e) {}
    // 上次退出时是全屏就恢复——但仅限当前就在闪卡页，否则一进大盘就被盖住
    if (saved && onFlashPage()) setFull(true);

    btn.addEventListener("click", () => setFull(!isFull()));

    // 键盘：Esc 退出，F 进/出全屏（2026-09-17 用户要求给闪卡加个进全屏的快捷键）。
    // ⚠️ 为什么不是 U —— U 在闪卡里是「撤销上一次评分」（见 FLASH_JS 的键盘段），
    //    抢过来会让人按错一下就把评分撤掉，代价太大。F 与笔记阅读弹窗的全屏键一致。
    document.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape" && isFull()) { setFull(false); return; }
        if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
        if (!__keys.matches("flash.full", ev)) return;
        // 正在输入框里打字时 F 就是字母 f，不能抢（追问框、填空作答区都算）
        if (ev.target && (/INPUT|TEXTAREA|SELECT/.test(ev.target.tagName) || ev.target.isContentEditable)) return;
        // 笔记阅读弹窗 / 卡组覆盖层自带 F（切弹窗自己的全屏），让给它们
        if (document.querySelector(".rev-modal, .deck-overlay")) return;
        // 番茄钟全屏盖在最上面，键盘归它管（同 FLASH_JS 键盘段的口径）
        if (typeof globalThis.__pomoFullscreen === "function" && globalThis.__pomoFullscreen()) return;
        // 练习区不可见时（人在别的子页、浮窗也关着）不响应——否则会把整页
        // 锁进一个看不见的全屏态里，只能靠 Esc 摸黑出来。
        if (!section.getClientRects().length) return;
        // ⚠️ 这条要在最后：FLASH_JS 的键盘段（本监听器之前注册，先跑）在选择题阶段
        //    会把字母键当选项用（A–I）。它认领了就会 preventDefault —— 那种时候 F
        //    是「选项 F」不是「全屏」，我们不抢。现在题库最多 A–D，撞不上，但留着兜底。
        if (ev.defaultPrevented) return;
        ev.preventDefault();
        setFull(!isFull());
    });

    // 改了键位就刷新按钮文案（tooltip 与 aria-label 里都带着键名）
    document.addEventListener("kaoyan:keys-changed", syncLabel);

    window.addEventListener("hashchange", () => {
        if (isFull() && !onFlashPage()) setFull(false);
    });

    // ---- 答题音效开关 ----
    // 浏览器只允许在用户手势里启动音频，这里趁首次指针按下预热 AudioContext，
    // 否则第一声「答对」会因为上下文还是 suspended 而被吞掉。
    const sfxBtn = document.getElementById("fs-sfx-toggle");
    document.addEventListener("pointerdown", function () { SFX.unlock(); }, { once: true });

    function applySfx(m) {
        SFX.setMuted(m);
        if (!sfxBtn) return;
        sfxBtn.textContent = m ? "🔇 音效" : "🔊 音效";
        sfxBtn.classList.toggle("is-off", m);
        const label = m ? "答题音效：已关闭" : "答题音效：已开启";
        sfxBtn.title = label;
        sfxBtn.setAttribute("aria-label", label);
        sfxBtn.setAttribute("aria-pressed", m ? "false" : "true");
    }

    if (sfxBtn) {
        applySfx(SFX.isMuted());
        sfxBtn.addEventListener("click", function () {
            const next = !SFX.isMuted();
            applySfx(next);
            // 刚开启时放一声，顺便让人确认音量合不合适
            if (!next) SFX.play("correct");
        });
    }
})();
'''


# ---------------------------------------------------------------------------
# 闪卡库（html-flashcard-builder --register 登记的专题卡组，莫兰迪配色）
# 画廊 + 点开 iframe 覆盖层练习；进度按 storageKey 存 localStorage，与独立产物共享
# ---------------------------------------------------------------------------
DECK_CSS = '''
        .deck-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
        .deck-card { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--border-radius); padding: 16px; cursor: pointer; transition: all .15s; display: flex; flex-direction: column; gap: 8px; }
        .deck-card:hover { border-color: var(--dianqing); background: var(--bg-card-hover); transform: translateY(-2px); }
        .deck-card.disabled { cursor: default; opacity: .55; }
        .deck-card.disabled:hover { border-color: var(--border-color); background: var(--bg-card); transform: none; }
        .deck-title { font-weight: 600; font-size: 0.95rem; color: var(--xuan); }
        .deck-sub { font-size: 0.75rem; color: var(--text-muted); min-height: 1.2em; }
        .deck-chips { display: flex; gap: 6px; flex-wrap: wrap; }
        .deck-chip { font-size: 0.68rem; padding: 1px 8px; border-radius: 9px; background: rgba(var(--dianqing-rgb),.12); color: var(--dianqing-lt); border: 1px solid rgba(var(--dianqing-rgb),.3); }
        .deck-meta { font-size: 0.7rem; color: var(--text-muted); display: flex; justify-content: space-between; }
        .deck-bar { height: 5px; border-radius: 3px; background: var(--bg-primary); overflow: hidden; }
        .deck-bar-fill { height: 100%; background: linear-gradient(90deg, var(--dianqing), var(--zhuqing)); border-radius: 3px; transition: width .3s; }
        .deck-bar-label { font-size: 0.7rem; color: var(--text-secondary); display: flex; justify-content: space-between; }
        .deck-empty { color: var(--text-muted); text-align: center; padding: 30px 0; font-size: 0.85rem; line-height: 1.8; }
        .deck-overlay { position: fixed; inset: 0; background: rgba(var(--mo-rgb),.9); -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px); z-index: 1000; display: flex; flex-direction: column; padding: 22px; }
        .deck-overlay-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
        .deck-overlay-title { color: var(--xuan); font-weight: 600; font-size: 1rem; font-family: var(--font-serif); }
        .deck-overlay-tip { color: var(--text-muted); font-size: 0.72rem; }
        .deck-overlay-close { margin-left: auto; border: 1px solid var(--border-color); background: var(--bg-card); color: var(--text-primary); border-radius: 6px; padding: 6px 16px; font-size: 0.85rem; cursor: pointer; }
        .deck-overlay-close:hover { border-color: var(--dianqing); }
        .deck-overlay iframe { flex: 1; width: 100%; border: none; border-radius: var(--border-radius); background: var(--mo-2); }
'''

DECK_JS = '''
// ============================================================
// 闪卡库：html-flashcard-builder --register 登记的专题卡组。
// 画廊展示进度（localStorage 按 storageKey 共享）；点开用 iframe
// 覆盖层加载卡组页（?embed=1 自动莫兰迪深色）；Esc / postMessage 关闭。
// ============================================================
(function() {
    const wrap = document.getElementById("deck-library");
    const decks = (D.deck_library || []);
    const TYPE_CN = {choice: "选择", tf: "判断", fill: "填空", short: "简答"};
    let overlay = null;

    function deckProgress(key, total) {
        if (!key) return { mastered: 0, practiced: 0 };
        try {
            const s = JSON.parse(localStorage.getItem(key) || "{}");
            return {
                mastered: Array.isArray(s.mastered) ? s.mastered.length : 0,
                practiced: Array.isArray(s.practiced) ? s.practiced.length : 0
            };
        } catch (e) { return { mastered: 0, practiced: 0 }; }
    }

    function renderGallery() {
        if (!decks.length) {
            wrap.innerHTML = '<div class="deck-empty">闪卡库还是空的。<br>让 AI 用 html-flashcard-builder 从复习资料出题，构建时加 <b>--register</b> 即可出现在这里。</div>';
            return;
        }
        wrap.innerHTML = '<div class="deck-grid">' + decks.map((d, i) => {
            const p = deckProgress(d.storageKey, d.total);
            const pct = d.total > 0 ? Math.round(p.mastered / d.total * 100) : 0;
            const chips = Object.entries(d.types || {}).map(([t, n]) =>
                '<span class="deck-chip">' + (TYPE_CN[t] || t) + "×" + n + '</span>').join("");
            const click = d.hasHtml ? ' onclick="window.__openDeck(' + i + ')"' : '';
            return '<div class="deck-card' + (d.hasHtml ? '' : ' disabled') + '"' + click + '>'
                + '<div class="deck-title">' + richTitle(d.title) + '</div>'
                + '<div class="deck-sub">' + richTitle(d.subtitle || "") + '</div>'
                + '<div class="deck-chips">' + chips + '</div>'
                + '<div class="deck-bar-label"><span>已掌握 ' + p.mastered + '/' + d.total + '</span><span>已练 ' + p.practiced + '</span></div>'
                + '<div class="deck-bar"><div class="deck-bar-fill" style="width:' + pct + '%"></div></div>'
                + '<div class="deck-meta"><span>构建 ' + d.built + '</span><span>' + (d.hasHtml ? "点击开练 →" : "缺 html 产物") + '</span></div>'
                + '</div>';
        }).join("") + '</div>';
    }

    function escDeck(s) {
        const d = document.createElement("div");
        d.textContent = s == null ? "" : String(s);
        return d.innerHTML;
    }

    // 卡组名/副标题也走全局按需 KaTeX（名字里可能带公式）。
    // 带兜底：本块要能脱离 FLASH_JS 单独跑，拿不到全局实现时退回纯转义。
    function richTitle(s) {
        return (window.richText || escDeck)(s);
    }

    function openDeck(i) {
        const d = decks[i];
        if (!d || !d.hasHtml || overlay) return;
        overlay = document.createElement("div");
        overlay.className = "deck-overlay";
        overlay.innerHTML = '<div class="deck-overlay-head">'
            + '<span class="deck-overlay-title">' + richTitle(d.title) + '</span>'
            + '<span class="deck-overlay-tip">Esc 退出 · 进度自动保存并与独立打开的产物共享</span>'
            + '<button class="deck-overlay-close">✕ 退出 (Esc)</button>'
            + '</div>'
            + '<iframe src="' + d.src + '?embed=1" title="' + escDeck(d.title) + '"></iframe>';
        overlay.querySelector(".deck-overlay-close").onclick = closeDeck;
        document.body.appendChild(overlay);
        overlay.querySelector("iframe").focus();
    }

    function closeDeck() {
        if (!overlay) return;
        overlay.remove();
        overlay = null;
        renderGallery();  // 刷新画廊进度条
    }

    window.__openDeck = openDeck;
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && overlay) closeDeck();
    });
    window.addEventListener("message", (e) => {
        if (e.data && e.data.type === "flashcard-close") closeDeck();
    });

    renderGallery();
})();
'''


def generate_html(data: dict) -> str:
    """Generate the complete dashboard HTML."""

    # Serialize data for JavaScript
    data_json = json.dumps(data, ensure_ascii=False)

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/svg+xml" href="{FAVICON_HREF}">
    <title>改造我们的学习</title>
    <script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
    <style>
        /* ============================================================
           新中式 · 墨色 色板（2026-09-13 重构）

           约定：**所有颜色只在这里定义**。下面的"基础色"是新中式取色，
           其后的"语义别名"把旧变量名指过去，这样既收拢了散落各处的硬编码色，
           又不会出现「--accent-blue 却是绿色」这种名实不符。
           要换肤只改这一块。
           ============================================================ */
        :root {{
            /* ---- 基础色（新中式·青墨）----
               底色统一走「青墨」：色相偏青（G≈B > R），饱和度压到 ~10%（哑），
               明度保持很暗。2026-09-13 从暖赭墨调整为青墨——原来的底色是
               R>G>B 的暖调，叠加朱砂强调色后整体发红，长时间看有刺激感。
               若还想微调：只改这一组，且保证 R 是最小的那个通道。 */
            --mo:        #151A1A;   /* 青墨    — 页面底 */
            --mo-2:      #1A2020;   /* 次青墨  — 次级底 */
            --mo-light:  #1F2626;   /* 淡青墨  — 卡片 */
            --mo-hover:  #273030;   /* 青墨醒  — 悬停 */
            --xuan:      #E5E9E7;   /* 月白    — 主文字 */
            --tao:       #97A5A3;   /* 青灰    — 次文字 */
            --hui:       #66726F;   /* 灰      — 弱文字 */
            --zhusha:    #B84A42;   /* 朱砂  — 强调 / 错误 */
            --zhuqing:   #6F9A8D;   /* 竹青  — 成功 / 主色 */
            --dianqing:  #5B7C99;   /* 靛青  — 信息 / 蓝 */
            --xiang:     #C89B4A;   /* 缃    — 警告 / 黄 */
            --zi:        #8A6FA8;   /* 紫    — 特殊标记 */
            --bian:      #2C3636;   /* 青墨边  — 边框 */

            /* 亮调用色：深底上做小字/图标时的提亮版本 */
            --zhusha-lt:   #E08A80;
            --zhuqing-lt:  #9CC4B6;
            --dianqing-lt: #92B4D0;
            --xiang-lt:    #E0C07E;
            --zi-lt:       #BCA3D6;

            /* rgb 三元组：配合 rgba(var(--x-rgb), .15) 写半透明底，
               避免各处再散落一遍硬编码色值 */
            --zhusha-rgb:   184,74,66;
            --zhuqing-rgb:  111,154,141;
            --dianqing-rgb: 91,124,153;
            --xiang-rgb:    200,155,74;
            --zi-rgb:       138,111,168;
            --xuan-rgb:     229,233,231;
            --mo-rgb:       21,26,26;

            /* ---- 语义别名（旧名保留，全部指向基础色）---- */
            --bg-primary: var(--mo);
            --bg-secondary: var(--mo-2);
            --bg-card: var(--mo-light);
            --bg-card-hover: var(--mo-hover);
            --text-primary: var(--xuan);
            --text-secondary: var(--tao);
            --text-muted: var(--hui);
            --accent-blue: var(--dianqing);
            --accent-green: var(--zhuqing);
            --accent-red: var(--zhusha);
            --accent-orange: var(--xiang);
            --accent-purple: var(--zi);
            --border-color: var(--bian);

            /* ---- 质感：新中式偏克制——小圆角、细边框、衬线标题 ---- */
            --border-radius: 4px;
            --font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
            --font-serif: "Songti SC", "STSong", "SimSun", "Noto Serif SC", "Source Han Serif SC", Georgia, serif;
            --rule: 1px solid var(--border-color);
            --rule-strong: 2px solid var(--border-color);
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: var(--font-family);
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            min-height: 100vh;
            padding: 20px;
        }}

        .dashboard {{
            max-width: 1200px;
            margin: 0 auto;
        }}

        header {{
            text-align: center;
            padding: 16px 0 24px;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 24px;
        }}

        header h1 {{
            font-size: 1.75rem;
            font-weight: 700;
            margin-bottom: 6px;
        }}

        header .subtitle {{
            color: var(--text-secondary);
            font-size: 0.85rem;
        }}

        /* --- Metric Cards --- */
        .metrics {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 16px;
            margin-bottom: 24px;
        }}

        .metric-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--border-radius);
            padding: 20px;
            text-align: center;
        }}

        .metric-card .label {{
            font-size: 0.8rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }}

        .metric-card .value {{
            font-size: 1.75rem;
            font-weight: 700;
            font-family: var(--font-serif);
            color: var(--accent-blue);
        }}

        .metric-card .detail {{
            font-size: 0.8rem;
            color: var(--text-secondary);
            margin-top: 4px;
        }}

        .metric-card .value.green {{ color: var(--accent-green); }}
        .metric-card .value.orange {{ color: var(--accent-orange); }}
        .metric-card .value.red {{ color: var(--accent-red); }}

        /* --- Section --- */
        .section {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--border-radius);
            padding: 20px;
            margin-bottom: 20px;
        }}

        .section h2 {{
            font-size: 1.1rem;
            font-weight: 600;
            font-family: var(--font-serif);
            letter-spacing: .04em;
            margin-bottom: 16px;
            color: var(--text-primary);
            border-left: 3px solid var(--zhusha);
            padding-left: 10px;
        }}

        /* --- Two-column layout --- */
        .row {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }}

        /* --- Heatmap --- */
        .heatmap-grid {{
            display: flex;
            flex-wrap: wrap;
            gap: 3px;
        }}

        .heatmap-cell {{
            width: 36px;
            height: 36px;
            border-radius: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.65rem;
            color: var(--text-primary);
            cursor: pointer;
            position: relative;
        }}

        .heatmap-cell:hover {{
            outline: 2px solid var(--accent-blue);
        }}

        /* 有笔记但没刷过闪卡：虚线圈出，避免和「练过的实心格」混淆 */
        .heatmap-cell.is-note-only {{
            box-shadow: inset 0 0 0 1px var(--text-muted);
            color: var(--text-secondary);
        }}

        .heatmap-tooltip {{
            display: none;
            position: absolute;
            bottom: 110%;
            left: 50%;
            transform: translateX(-50%);
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            padding: 6px 10px;
            border-radius: 4px;
            font-size: 0.75rem;
            white-space: nowrap;
            z-index: 10;
            color: var(--text-primary);
        }}

        .heatmap-cell:hover .heatmap-tooltip {{
            display: block;
        }}

        .heatmap-legend {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 12px;
            font-size: 0.75rem;
            color: var(--text-secondary);
        }}

        .heatmap-legend-bar {{
            width: 120px;
            height: 12px;
            border-radius: 2px;
            background: linear-gradient(to right, var(--bg-secondary), var(--dianqing));
        }}

        .heatmap-legend-note {{
            margin-left: 8px;
            color: var(--text-muted);
        }}

        .heatmap-row-label {{
            font-size: 0.8rem;
            color: var(--text-secondary);
            width: 40px;
            text-align: right;
            padding-right: 8px;
            flex-shrink: 0;
        }}

        .heatmap-row {{
            display: flex;
            align-items: center;
            margin-bottom: 4px;
        }}

        /* --- Gap list --- */
        .gap-list {{
            list-style: none;
        }}

        .gap-item {{
            display: flex;
            align-items: center;
            padding: 10px 0;
            border-bottom: 1px solid var(--border-color);
        }}

        .gap-item:last-child {{
            border-bottom: none;
        }}

        .gap-rank {{
            width: 28px;
            height: 28px;
            border-radius: 50%;
            background: var(--accent-blue);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.8rem;
            font-weight: 700;
            margin-right: 12px;
            flex-shrink: 0;
        }}

        .gap-info {{
            flex: 1;
        }}

        .gap-topic {{
            font-weight: 600;
            font-size: 0.9rem;
        }}

        .gap-meta {{
            font-size: 0.75rem;
            color: var(--text-secondary);
        }}

        .gap-weight {{
            font-size: 0.85rem;
            color: var(--accent-orange);
            font-weight: 600;
            margin-left: 12px;
        }}

        /* --- Chart containers --- */
        .chart-container {{
            width: 100%;
            min-height: 200px;
        }}

        /* ⚠️ 只作用于**直接子** svg。写成 `.chart-container svg` 的话，容器里嵌套的
           svg（环形图、趋势图）也会被拉成整行宽——环形图圆心还钉在 x=130，
           右边就是一片空白。分栏布局必须让嵌套 svg 用自己的 width 属性。 */
        .chart-container > svg {{
            width: 100%;
        }}

        /* --- 图表入场动效（2026-09-21）---
           分工：柱/线的**形变**（从 0 长起来、线被画出来、饼图扫开）交给 d3 补间，
           「整张图的出现」（坐标轴、图例、热力图格子、指标卡）用 CSS 动画最省事，
           还能被 prefers-reduced-motion 一键关掉。STYLE 上刻意克制：位移不超过 8px，
           时长 0.3~0.6s，只缓出不回弹——这是学习工具，不是展厅。 */
        ⚠️ fill-mode 一律用 `backwards` 而不是 `both`：
           `.metric-card` / `.heatmap-cell` 在 FX_CSS 里都有 :hover 的 transform
           （卡片上浮 2px、格子放大 1.1）。CSS 动画的填充值优先级**高于**普通声明，
           用 `both` 的话动画结束后那层 transform:none 会一直压着 :hover，
           悬停反馈就永久失效了。`backwards` 只在延迟期占位，跑完就交还给基样式。 */
        @keyframes chartFade {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
        @keyframes chartRise {{
            from {{ opacity: 0; transform: translateY(8px); }}
            to   {{ opacity: 1; transform: none; }}
        }}
        @keyframes cellIn {{
            from {{ opacity: 0; transform: scale(.86); }}
            to   {{ opacity: 1; transform: none; }}
        }}
        .chart-container > svg {{ animation: chartFade .55s ease-out backwards; }}
        .chart-legend {{ animation: chartRise .5s ease-out backwards; animation-delay: .26s; }}
        .metric-card {{ animation: chartRise .5s ease-out backwards; }}
        .heatmap-cell {{ animation: cellIn .34s ease-out backwards; }}
        .heatmap-row-label {{ animation: chartRise .42s ease-out backwards; }}
        .heatmap-legend {{ animation: chartFade .5s ease-out backwards; animation-delay: .3s; }}
        @media (prefers-reduced-motion: reduce) {{
            .chart-container > svg, .chart-legend, .metric-card, .heatmap-cell,
            .heatmap-row-label, .heatmap-legend {{ animation: none !important; }}
        }}

        /* 图表标题行：左标题右切换按钮，切按钮不会把图挤矮 */
        .chart-head {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 16px;
        }}

        .chart-head h2 {{
            margin-bottom: 0;
        }}

        .range-toggle {{
            display: flex;
            border: 1px solid var(--border-color);
            border-radius: 4px;
            overflow: hidden;
            flex-shrink: 0;
        }}

        .range-btn {{
            background: transparent;
            border: 0;
            color: var(--text-secondary);
            font-family: inherit;
            font-size: 0.75rem;
            line-height: 1.6;
            padding: 3px 12px;
            cursor: pointer;
        }}

        .range-btn:hover {{
            color: var(--text-primary);
            background: var(--bg-secondary);
        }}

        .range-btn.is-active {{
            background: var(--dianqing);
            color: #fff;
        }}

        /* --- Axis styles --- */
        .axis text {{
            fill: var(--text-secondary);
            font-size: 0.7rem;
        }}

        .axis line, .axis path {{
            stroke: var(--border-color);
        }}

        .chart-legend {{
            display: flex;
            gap: 16px;
            justify-content: center;
            margin-top: 8px;
            flex-wrap: wrap;
        }}

        .chart-legend-item {{
            display: flex;
            align-items: center;
            gap: 4px;
            font-size: 0.75rem;
            color: var(--text-secondary);
        }}

        .chart-legend-dot {{
            width: 10px;
            height: 10px;
            border-radius: 2px;
        }}

        /* --- 闪卡记忆状态：左右分栏（环形图 | 正确率趋势）---
           原来环形图独占整行、趋势图横在下面 80px：右半边全是空的，趋势线
           被压成一条看不出起伏的细线。分栏后左边定宽放环形图+图例，右边自适应
           放趋势图（带坐标轴和悬停读数）。 */
        .fc-layout {{
            display: flex;
            align-items: center;
            gap: 28px;
            flex-wrap: wrap;
        }}
        .fc-donut {{
            position: relative;
            flex: 0 0 220px;
            width: 220px;
        }}
        /* 220px 的窄栏里横向排会折成「New (464) Learning (10) / Review (95)」这种
           半截换行，竖排一行一项更好认，鼠标也更好停在上面 */
        .fc-donut .chart-legend {{
            flex-direction: column;
            align-items: flex-start;
            gap: 3px;
            margin-top: 6px;
            padding-left: 46px;
        }}
        .fc-trend {{
            position: relative;
            flex: 1 1 320px;
            min-width: 280px;
        }}
        .fc-trend-head {{
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 10px;
            margin-bottom: 2px;
        }}
        .fc-trend-title {{ font-size: 0.8rem; color: var(--text-secondary); }}
        .fc-trend-now {{
            font-size: 0.72rem;
            color: var(--text-muted);
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
        }}
        .fc-trend-now b {{ color: var(--zhuqing-lt); font-size: 0.9rem; }}
        .fc-trend-empty {{
            color: var(--text-muted);
            font-size: 0.8rem;
            padding: 40px 0;
            text-align: center;
        }}
        .fc-donut-svg, .fc-trend-svg {{ animation: chartFade .55s ease-out backwards; }}

        /* --- Responsive --- */
        @media (max-width: 768px) {{
            .metrics {{
                grid-template-columns: repeat(2, 1fr);
            }}
            .row {{
                grid-template-columns: 1fr;
            }}
        }}

        @media (max-width: 480px) {{
            .metrics {{
                grid-template-columns: 1fr;
            }}
        }}

        /* --- Alert Card --- */
        .alert-card {{
            background: rgba(var(--zhusha-rgb),.18);
            border-left: 3px solid var(--zhusha);
            padding: 12px 16px;
            margin: 8px 0;
            border-radius: var(--border-radius);
        }}
        .alert-card strong {{
            color: var(--zhusha-lt);
            display: block;
            margin-bottom: 6px;
            font-size: 0.95rem;
        }}
        .alert-card p {{
            color: var(--text-secondary);
            font-size: 0.85rem;
            margin: 0;
        }}
        .alert-card .alert-detail {{
            margin-top: 8px;
            font-size: 0.8rem;
            color: var(--text-muted);
        }}

        footer {{
            text-align: center;
            padding: 16px 0;
            color: var(--text-muted);
            font-size: 0.75rem;
            border-top: 1px solid var(--border-color);
            margin-top: 20px;
        }}

        /* --- 薄弱提醒 --- */
        .weak-item {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 9px 0;
            border-bottom: 1px solid var(--border-color);
            font-size: 0.88rem;
        }}
        .weak-item:last-child {{ border-bottom: none; }}
        .weak-main {{ flex: 1; min-width: 0; }}
        .weak-head {{ display: flex; align-items: baseline; gap: 8px; }}
        .weak-name {{ font-weight: 600; }}
        .weak-acc {{
            font-size: 0.8rem;
            font-weight: 700;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
        }}
        .weak-acc.bad {{ color: var(--zhusha-lt); }}
        .weak-acc.warn {{ color: var(--xiang-lt); }}
        .weak-acc.ok {{ color: var(--tao); }}
        /* 正确率条：一眼看出谁真的低，而不是谁错得多 */
        .weak-bar {{
            height: 3px;
            max-width: 200px;
            margin: 5px 0 4px;
            border-radius: 2px;
            background: rgba(var(--xuan-rgb), .08);
            overflow: hidden;
        }}
        .weak-bar-fill {{ height: 100%; border-radius: 2px; }}
        .weak-tag {{
            font-size: 0.64rem;
            color: var(--text-muted);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 0 5px;
            margin-left: 6px;
            white-space: nowrap;
        }}
        .weak-meta {{ font-size: 0.72rem; color: var(--text-muted); }}
        .weak-count {{
            font-size: 0.78rem;
            color: var(--accent-red);
            background: rgba(239,68,68,.12);
            border-radius: 10px;
            padding: 2px 10px;
            white-space: nowrap;
            margin-left: 10px;
        }}
        .weak-empty {{
            color: var(--text-muted);
            text-align: center;
            padding: 30px 0;
            font-size: 0.85rem;
        }}
        .weak-note {{
            margin-top: 10px;
            font-size: 0.72rem;
            color: var(--text-muted);
            line-height: 1.6;
        }}
        /* --- 薄弱提醒 / 真缺口折叠（2026-09-18：平时只展示前 3 项，展开 + 浮窗查看）--- */
        .weak-item.over-cap {{ display: none; }}
        .gap-item.over-cap {{ display: none; }}
        .cap-foot {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 9px 0 2px;
            margin-top: 6px; border-top: 1px dashed var(--border-color); font-size: 0.76rem;
            color: var(--text-muted); }}
        .cap-info {{ margin-right: auto; }}
        .cap-btn {{ font-size: 0.76rem; color: var(--xuan-lt); background: rgba(var(--xuan-rgb), .10);
            border: 1px solid var(--border-color); border-radius: 8px; padding: 3px 10px; cursor: pointer;
            font-family: var(--font-serif); }}
        .cap-btn:hover {{ background: rgba(var(--xuan-rgb), .18); }}
        .cap-float {{ color: var(--accent-red); background: rgba(239,68,68,.10); }}
        .cap-float:hover {{ background: rgba(239,68,68,.18); }}
        .cap-body .weak-item, .cap-body .gap-item {{ padding: 10px 0; }}
        /* __FLASH_CSS__ */
        /* __DECK_CSS__ */
        /* __REVIVE_CSS__ */
        /* __NOTEQ_CSS__ */
        /* __NAV_CSS__ */
        /* __FX_CSS__ */
        /* __TASK_CSS__ */
        /* __THEME_CSS__ */
        /* __SETTINGS_CSS__ */
        /* __POMO_CSS__ */
        /* __SHELL_CSS__ */
        /* __MR_CSS__ */
        /* __RV_CSS__ */
        /* __BOOT_CSS__ */
    </style>
</head>
<body>
<!-- 自定义背景图的承载层。独立一层而不是设到 body 上：这样它能用 z-index:-1
     落在内容之下、body 背景之上，同时 ::after 那层遮罩可以单独调透明度。 -->
<div id="bg-layer"></div>
<!-- 鼠标粒子光效的画布（2026-09-21，升级为「离子消散」）：整页铺满、不吃指针事件，
     每个子页都生效（不再只做总览）。压在内容之上但用混合模式当「光」用，所以不会
     糊住文字；关掉开关或系统开了「减弱动效」时整段不跑（见 SHELL_JS mouseFx）。 -->
<canvas id="mouse-fx" aria-hidden="true"></canvas>
<!-- 整页全屏（右上角常驻，2026-09-21）：就是 F11 那种整页模式，在任何子页都能按。
     与模块自己的全屏是两码事——番茄钟 / 闪卡练习各自的「⛶ 全屏」只让那一个模块
     铺满屏幕（见 POMO_JS 与闪卡全屏段），这里铺的是整页。 -->
<button class="shell-fs" id="shell-fs" title="整页全屏（Esc 退出）" aria-label="整页全屏">⛶ 全屏</button>
<div class="dashboard">
    <!-- 左侧导航：点一项切一页，不再是一条道滚到底 -->
    <aside class="sidenav" id="sidenav">
        <div class="sidenav-brand">
            <span class="sidenav-brand-text">改造我们的学习</span>
            <button class="sidenav-toggle" id="sidenav-toggle"
                    title="收起侧边栏" aria-label="收起侧边栏" aria-expanded="true">◀</button>
        </div>
        <!-- data-short：收起后只显示首字，title 补回完整名称 -->
        <button class="sidenav-item" data-page="overview" data-short="总" title="总览">总览</button>
        <button class="sidenav-item" data-page="notes" data-short="笔" title="笔记">笔记</button>
        <button class="sidenav-item" data-page="flash" data-short="闪" title="闪卡">闪卡</button>
        <button class="sidenav-item" data-page="activity" data-short="计" title="计划页">计划页</button>
        <button class="sidenav-item" data-page="mistakes" data-short="复" title="错题复盘">错题复盘</button>
        <button class="sidenav-item" data-page="study" data-short="学" title="薄弱点学习">薄弱点学习</button>
        <button class="sidenav-item" data-page="review" data-short="早" title="早间回顾">早间回顾</button>
        <button class="sidenav-item" data-page="settings" data-short="设" title="设置">设置</button>
    </aside>

    <main class="dash-main">
    <header>
        <h1>改造我们的学习</h1>
        <div class="subtitle" id="header-subtitle"></div>
    </header>

    <!-- ============ 总览 ============ -->
    <div class="page" data-page="overview">
        <!-- Section 1: Top Metrics -->
        <div class="metrics" id="metrics-row"></div>

        <!-- Section 1b: 番茄钟（结构与逻辑见 POMO_JS）。放在大盘首页最上方而不是
             单独一页：开大盘的第一件事是「这轮学多久」，而不是去看昨天的数据。
             这个容器只是占位——卡片实体由 JS 建出来，全屏时会被整体搬进
             body 层的 #pm-overlay，离开总览页也照样能继续看表。 -->
        <div id="pm-slot"></div>

        <!-- Section 1c: 专注与打卡数据条（番茄钟成绩 + 早间回顾打卡，渲染见 SHELL_JS）。
             与番茄钟卡片分开：那张卡是「现在这一轮」，这条是「这些天到底练了多少」。 -->
        <div class="section" id="focus-strip">
            <div class="strip-loading">正在读取专注与打卡数据…</div>
        </div>

        <!-- Section 3: 薄弱提醒（闪卡正确率驱动，已验证掌握的不再提醒） -->
        <div class="row">
            <div class="section">
                <h2>薄弱知识点（按闪卡正确率）</h2>
                <div id="weak-list"></div>
            </div>
            <div class="section">
                <h2>真缺口（无笔记且未验证掌握）</h2>
                <ul class="gap-list" id="gap-list"></ul>
            </div>
        </div>

        <!-- Section 3b: 错因画像（来自错题复盘；渲染逻辑在 RV_JS，注册到 overview） -->
        <div class="section">
            <h2>🔁 常犯错误画像 · 重蹈覆辙提醒</h2>
            <div id="pattern-box"><div class="rv-loading">正在汇总错因…</div></div>
        </div>

        <!-- Coverage Heatmap -->
        <div class="section">
            <h2>科目掌握度热力图（笔记 + 闪卡正确率）</h2>
            <div id="heatmap-container"></div>
        </div>

        <!-- Timeline + Level Distribution -->
        <div class="row">
            <div class="section">
                <div class="chart-head">
                    <h2>笔记增长趋势</h2>
                    <div class="range-toggle" id="timeline-range">
                        <button class="range-btn is-active" data-range="day">日</button>
                        <button class="range-btn" data-range="week">周</button>
                        <button class="range-btn" data-range="month">月</button>
                    </div>
                </div>
                <div class="chart-container" id="timeline-chart"></div>
            </div>
            <div class="section">
                <h2>级别分布</h2>
                <div class="chart-container" id="level-chart"></div>
            </div>
        </div>
    </div>

    <!-- ============ 笔记盘活 ============ -->
    <div class="page" data-page="notes" hidden>
        <!-- Section 0: 搜索 + 筛选（2026-09-21）。放在盘活面板之上：
             找笔记是打开这一页的第一个动作，盘活是「没目标时扫一遍」。
             UI 与逻辑都在 NOTEQ_JS（含读笔记时的「就问这段」面板）。 -->
        <div class="section" id="note-search">
            <div class="ns-top">
                <div class="ns-inputwrap">
                    <span class="ns-icon">🔍</span>
                    <input id="ns-input" class="ns-input" type="search" autocomplete="off" spellcheck="false"
                           placeholder="搜笔记：标题、正文都行 · 多个词用空格分隔（全都要命中）">
                    <button class="ns-clear" id="ns-clear" title="清空" hidden>✕</button>
                </div>
                <button class="ns-browse" id="ns-browse" title="不搜关键词，只看筛选出来的笔记">浏览</button>
            </div>
            <div class="ns-facets" id="ns-facets"></div>
            <div class="ns-meta" id="ns-meta"></div>
            <div class="ns-results" id="ns-results"><div class="ns-hint">正在读取笔记索引…</div></div>
        </div>
        <!-- Section 2: 笔记盘活面板（强化阶段核心：把沉睡笔记重新练起来） -->
        <div class="section">
            <h2>📖 笔记盘活面板 · 别让笔记睡过去</h2>
            <div class="rev-head">
                <div>
                    <div class="rev-score" id="rev-score">--</div>
                    <div class="rev-score-label">笔记活跃分（热100% · 温60% · 冷20% · 冰冻0%）</div>
                </div>
                <div class="rev-chips" id="rev-chips"></div>
            </div>
            <div id="rev-by-prefix"></div>
            <h2 style="margin-top:18px;">🧊 盘活目标清单（冷/冰冻笔记，按紧迫度排序）</h2>
            <div id="rev-targets"></div>
            <div class="rev-tip" id="rev-tip"></div>
        </div>
    </div>

    <!-- ============ 闪卡 ============ -->
    <div class="page" data-page="flash" hidden>
        <!-- Section 4b: 闪卡练习区（看大盘时顺便刷题） -->
        <div class="section" id="flash-practice">
            <div class="chart-head">
                <h2>闪卡练习区 · 优先薄弱与冷笔记盘活</h2>
                <div class="fs-tools">
                    <!-- 学习计时：点「开始学习」才走表，窗口失焦/切后台自动暂停 -->
                    <span class="fs-timer off" id="fs-timer"
                          title="学习计时：点「开始学习」后才计时，切到别的窗口或标签页会自动暂停">⏱ 未开始</span>
                    <button class="fs-full-toggle" id="fs-stop" hidden
                            title="结束本次学习并停止计时">⏹ 结束</button>
                    <button class="fs-full-toggle" id="fs-sfx-toggle" title="答题音效">🔊 音效</button>
                    <button class="fs-full-toggle" id="fs-full-toggle"
                            title="全屏练习（F，Esc 退出）">⛶ 全屏</button>
                </div>
            </div>
            <div class="fs-box" id="flash-studio"></div>
        </div>

        <!-- Section 4c: 闪卡范围筛选（顺位放在练习区下面；平时收起，点按钮浮窗打开） -->
        <div class="section" id="flash-filter"></div>

        <!-- Flashcard Stats -->
        <div class="section">
            <h2>闪卡记忆状态</h2>
            <div class="chart-container" id="flashcard-chart"></div>
        </div>
    </div>

    <!-- ============ 计划页（今日任务，agent 定时生成 + 手动增删） ============ -->
    <div class="page" data-page="activity" hidden>
        <!-- Section 5a: 今日任务（agent 定时生成 + 手动增删） -->
        <div class="section">
            <div class="chart-head">
                <h2>✅ 今日任务 <span class="task-progress" id="task-progress"></span></h2>
                <span class="task-day" id="task-day"></span>
            </div>
            <ul class="task-list" id="task-list"></ul>
            <div class="task-add">
                <input class="task-input" id="task-input" maxlength="200"
                       placeholder="加一条今天的任务，回车即可添加…">
                <select class="task-subject" id="task-subject">
                    <option value="">综合</option>
                    <option value="408">408</option>
                    <option value="政治">政治</option>
                    <option value="数学一">数学一</option>
                    <option value="英语一">英语一</option>
                </select>
                <button class="fs-btn" id="task-add-btn">添加</button>
            </div>
            <div class="task-hint" id="task-hint"></div>
        </div>
    </div>

    <!-- ============ 错题复盘（上传卷子/照片 → AI 对话 → 沉淀错因 → 专项练习） ============ -->
    <div class="page" data-page="mistakes" hidden>
        <div class="section">
            <h2>📕 错题复盘</h2>
            <div id="rv-mistakes"><div class="rv-loading">正在载入复盘会话…</div></div>
        </div>
    </div>

    <!-- ============ 薄弱点学习（说知识点 → 检索题库/笔记/错因 → 讲 + 练） ============ -->
    <div class="page" data-page="study" hidden>
        <div class="section">
            <h2>🧪 薄弱点学习</h2>
            <div id="rv-study"><div class="rv-loading">准备中…</div></div>
        </div>
    </div>

    <!-- ============ 早间回顾（并入大盘；内容与进度都在本机服务端，多设备同步） ============ -->
    <div class="page" data-page="review" hidden>
        <div class="section">
            <h2>🌅 早间回顾</h2>
            <div id="mr-root"><div class="mr-loading">正在读取早间回顾…</div></div>
        </div>
    </div>

    <!-- ============ 设置 ============ -->
    <div class="page" data-page="settings" hidden>
        <div class="section">
            <h2>⚙ 设置</h2>
            <div id="settings-root"></div>
        </div>
    </div>

    <footer>
        生成于 <span id="gen-time"></span> &middot; 数据来源: 笔记索引 / 知识图谱 / 闪卡数据库 &middot; 已验证掌握 <span id="verified-count"></span> 个无笔记考点
    </footer>
    </main>
</div>

<!-- 番茄钟全屏层：#pm-slot 只是占位，卡片实体在按「全屏」时被搬进这里（同一个
     DOM 节点搬家，状态与计时都不重置）。它必须在 .dashboard 之外、body 之下，
     否则会被「总览」页的 hidden 一起藏掉——切到别的子页番茄钟就凭空消失了。 -->
<div class="pm-overlay" id="pm-overlay" hidden></div>

<!-- 闪卡浮窗（2026-09-22）：让「薄弱点学习」「错题复盘」这些页不必跳走就能刷闪卡。
     #fs-float-body 里**没有**练习区的内容——它是空的，练习区实体由 FLASH_JS 在
     打开浮窗时整块搬进来（节点搬家，同番茄钟全屏的做法），关掉再搬回闪卡页。
     放在 .dashboard 之外、body 之下：和番茄钟同理，切子页才不会被一起藏掉。 -->
<div class="fs-float" id="fs-float" hidden>
    <div class="fs-float-bar" id="fs-float-bar">
        <span class="fs-float-title" id="fs-float-title">🧠 闪卡浮窗</span>
        <span class="fs-float-tag" id="fs-float-tag">拖动标题栏移动 · 右下角拉伸 · 练习进度与闪卡页共用</span>
        <button class="fs-float-btn" id="fs-float-home" title="收起浮窗并回到「闪卡」页">↩ 回闪卡页</button>
        <button class="fs-float-btn" id="fs-float-close" title="收起浮窗（进度不丢，回闪卡页可继续）">✕ 收起</button>
    </div>
    <div class="fs-float-body" id="fs-float-body"></div>
</div>

<script>
// __LOG_JS__
// ============================================================
// Data (injected by Python)
// ============================================================
const D = {data_json};

// ============================================================
// Helpers
// ============================================================
// ============================================================
// 新中式调色板（JS 侧）—— 与 CSS 的 :root 同源，换色时两处一起改
// 为什么 JS 里还要一份：D3 的 .attr("fill", ...) 设的是 SVG 属性，
// 不解析 CSS 的 var()；配色映射表也必须拿到具体色值。
// ============================================================
const PALETTE = {{
    zhusha:   "#B84A42",  // 朱砂
    zhuqing:  "#6F9A8D",  // 竹青
    dianqing: "#5B7C99",  // 靛青
    xiang:    "#C89B4A",  // 缃
    zi:       "#8A6FA8",  // 紫
    zhushaLt:   "#E08A80",
    zhuqingLt:  "#9CC4B6",
    dianqingLt: "#92B4D0",
    xiangLt:    "#E0C07E",
    xuan: "#E5E9E7",
    tao:  "#97A5A3",
    hui:  "#66726F",
    bian: "#2C3636",
    mo:   "#151A1A",
    moLight: "#1F2626"
}};

const SUBJECT_COLORS = {{
    "408":  PALETTE.zhuqing,
    "数学": PALETTE.dianqing,
    "政治": PALETTE.zhusha,
    "英语": PALETTE.xiang
}};

function el(tag, attrs, parent) {{
    const e = document.createElement(tag);
    if (attrs) Object.assign(e, attrs);
    if (parent) parent.appendChild(e);
    return e;
}}

// ============================================================
// 图表入场动效的**全局**开关（2026-09-21）
//
// ⚠️ 必须声明在顶层共享：d3 补间不认 CSS 的 prefers-reduced-motion，得在 JS 里判一次。
// 一开始只把它写在柱状图那个渲染器里，折线图跟着引用 → ReferenceError → 折线在画完
// 第一条线之后整段中断（点、悬停准线、其余三个科目全没了），而页面上只表现为「图少
// 了一半」。同一段脚本里的各图共用这两个常量，别再各写一份。
// ============================================================
const CHART_MOTION = !(window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
const CHART_DUR = CHART_MOTION ? 620 : 0;
// 兜底把几何写死：d3 的补间靠 requestAnimationFrame 驱动，极端情况下（渲染时页面
// 正好被切到后台、浏览器节流）首帧迟迟不来，柱子会一直贴在 0 高度、线会一直是
// 「全偏移的虚线」——那等于图是空的。这些 setTimeout 到了就以终值为准：
// **最差只是「没有动画」，绝不会出现「图没画出来」**。
// （d3 补间下次 tick 写的是同一个终值，两者不打架。）
function chartSettle(fn, delay) {{
    try {{ setTimeout(fn, Math.max(0, delay) + CHART_DUR + 900); }} catch (e) {{}}
}}

// ============================================================
// Header
// ============================================================
document.getElementById("header-subtitle").textContent =
    "考试日期: " + D.countdown.exam_date + " | 上次同步: " + (D.sync.last_sync || "N/A");
document.getElementById("gen-time").textContent = new Date().toLocaleString("zh-CN");
document.getElementById("verified-count").textContent = D.verified_count || 0;

// ============================================================
// Section 1: Top Metrics
// ============================================================
(function() {{
    const row = document.getElementById("metrics-row");

    const cards = [
        {{
            label: "考试倒计时",
            value: D.countdown.days + " 天",
            detail: D.countdown.phase,
            cls: D.countdown.days <= 30 ? "red" : D.countdown.days <= 90 ? "orange" : "green"
        }},
        {{
            label: "笔记总数",
            value: D.notes.total + " 条",
            detail: D.notes.today_new > 0 ? "+" + D.notes.today_new + " 今日新增" : "今日无新增",
            cls: ""
        }},
        {{
            label: "笔记活跃分",
            value: ((D.revival && D.revival.freshness.summary.alive_score) != null
                ? D.revival.freshness.summary.alive_score : "--") + "%",
            detail: "冷/冰冻笔记 " + ((D.revival && (D.revival.freshness.summary.cold + D.revival.freshness.summary.frozen)) || 0) + " 篇待盘活",
            cls: (D.revival && D.revival.freshness.summary.alive_score >= 70) ? "green"
                : (D.revival && D.revival.freshness.summary.alive_score >= 40) ? "orange" : "red"
        }},
        {{
            label: "覆盖率",
            value: D.coverage.overall + "%",
            detail: "笔记覆盖 + 闪卡验证掌握",
            cls: D.coverage.overall >= 50 ? "green" : "orange"
        }},
        {{
            label: "闪卡待复习",
            value: D.flashcard.due + " 张",
            detail: "题库共 " + D.flashcard.total + " 张",
            cls: D.flashcard.due > 0 ? "orange" : "green"
        }}
    ];

    cards.forEach((c, ci) => {{
        const card = el("div", {{className: "metric-card"}}, row);
        // 交错入场：0 / 45 / 90 … 毫秒，卡片依次浮起来而不是整排一起弹
        card.style.animationDelay = (ci * 45) + "ms";
        el("div", {{className: "label", textContent: c.label}}, card);
        const v = el("div", {{className: "value " + c.cls, textContent: c.value}}, card);
        el("div", {{className: "detail", textContent: c.detail}}, card);
    }});
}})();

// ============================================================
// Section 2: Coverage Heatmap
// ============================================================
(function() {{
    const container = document.getElementById("heatmap-container");
    const subjects = ["408", "数学", "政治", "英语"];
    const data = D.heatmap;

    subjects.forEach((subj, rowIdx) => {{
        const row = el("div", {{className: "heatmap-row"}}, container);
        const label = el("div", {{className: "heatmap-row-label", textContent: subj}}, row);
        label.style.animationDelay = (rowIdx * 70) + "ms";
        const grid = el("div", {{className: "heatmap-grid"}}, row);

        const items = data.filter(d => d.subject === subj);
        items.forEach((item, ci) => {{
            const intensity = item.coverage / 100;
            const practiced = item.practiced || 0;
            const noteOnly = practiced === 0 && (item.note_only || 0) > 0;

            // 靛青 91,124,153 —— 与 :root 的 --dianqing-rgb 同源。
            const cell = el("div", {{className: "heatmap-cell"}}, grid);
            // 逐格延迟：一行行铺开，像格子被一格一格点上去
            // （CSS 里只放统一的 cellIn 关键帧，错开的时间写在内联样式上）
            cell.style.animationDelay = (rowIdx * 70 + ci * 7) + "ms";
            if (item.coverage === 0) {{
                cell.style.background = "rgba(91, 124, 153, .06)";
            }} else if (noteOnly) {{
                // 有笔记但一次没练：低饱和 + 虚线框，和「练过的实心格」区分开
                cell.classList.add("is-note-only");
                cell.style.background = "rgba(91, 124, 153, .16)";
            }} else {{
                cell.style.background = `rgba(91, 124, 153, ${{0.18 + intensity * 0.72}})`;
            }}
            cell.textContent = item.coverage + "%";

            const tooltip = el("div", {{className: "heatmap-tooltip"}}, cell);
            const src = practiced > 0
                ? `闪卡已练 ${{practiced}} 个考点`
                : (noteOnly ? "仅整理笔记，尚无答题记录" : "无笔记，也未练过");
            tooltip.textContent =
                `${{item.sub}} 第${{item.chapter}}章: ${{item.covered}}/${{item.total}} (${{item.coverage}}%) · ${{src}}`;
        }});
    }});

    // Legend
    const legend = el("div", {{className: "heatmap-legend"}}, container);
    el("span", {{textContent: "0%"}}, legend);
    const bar = el("div", {{className: "heatmap-legend-bar"}}, legend);
    el("span", {{textContent: "100%"}}, legend);
    el("span", {{className: "heatmap-legend-note",
        textContent: "虚线格 = 已整理笔记但没刷过闪卡"}}, legend);
}})();

// ============================================================
// Section 3: Note Growth Timeline (D3 line chart)
// ============================================================
// 懒渲染：这个图读 container.clientWidth 定宽度，而容器隐藏时它恒为 0，
// 画出来就是空白。注册到 __pageRenderers，等「总览」页真可见了再跑第一次。
window.__pageRenderers = window.__pageRenderers || {{}};
window.__pageRenderers.overview = [function () {{
    const container = document.getElementById("timeline-chart");
    const raw = D.timeline;
    if (!raw || raw.length === 0) {{
        container.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:40px 0;">暂无时间线数据</p>';
        return;
    }}

    const SUBJECTS = ["408", "数学", "政治", "英语"];
    const parseDate = d3.timeParse("%Y-%m-%d");
    const all = raw
        .map(d => Object.assign({{}}, d, {{dateObj: parseDate(d.date)}}))
        .filter(d => d.dateObj);

    // 视图配置：保留几个桶 + 分桶对齐方式 + 步进 + 标签格式
    // keep=null 表示「从有记录的第一个月一直铺到最近一个月」
    const RANGES = {{
        day:   {{keep: 14,   floor: d => d3.timeDay.floor(d),
                 offset: (d, n) => d3.timeDay.offset(d, n),
                 label: d => d3.timeFormat("%m/%d")(d)}},
        week:  {{keep: 12,   floor: d => d3.timeMonday.floor(d),
                 offset: (d, n) => d3.timeMonday.offset(d, n),
                 label: d => d3.timeFormat("%m/%d")(d) + " 周"}},
        // 月视图只看 2026-03 起：更早的记录是零散的旧材料，按需求丢弃
        month: {{keep: null, start: new Date(2026, 2, 1),
                 floor: d => d3.timeMonth.floor(d),
                 offset: (d, n) => d3.timeMonth.offset(d, n),
                 label: d => d3.timeFormat("%y/%m")(d)}}
    }};

    const zeroBucket = () => {{
        const b = {{total: 0}};
        SUBJECTS.forEach(s => {{ b[s] = 0; }});
        return b;
    }};

    function bucketize(rangeKey) {{
        const cfg = RANGES[rangeKey];

        // 先按桶累加有数据的那些周期
        const sums = new Map();
        all.forEach(d => {{
            const key = +cfg.floor(d.dateObj);
            if (!sums.has(key)) sums.set(key, zeroBucket());
            const b = sums.get(key);
            SUBJECTS.forEach(s => {{ b[s] += (d[s] || 0); }});
            b.total += (d.total || 0);
        }});

        // 再铺一条**连续**的桶序列，没数据的周期补 0。
        // 只留有数据的桶会让时间轴说谎：目前记录只落在 6 个月份上
        // （2025-06 之后直接跳到 2026-04），不补零的话 2025-06 会紧挨着
        // 2026-04，中间 9 个月的空档被挤没，看起来像一直在连续记笔记。
        const sorted = all.slice().sort((a, b) => a.dateObj - b.dateObj);
        const lastKey = +cfg.floor(sorted[sorted.length - 1].dateObj);
        const seq = [];
        if (cfg.keep) {{
            for (let i = cfg.keep - 1; i >= 0; i--) {{
                seq.push(cfg.floor(cfg.offset(new Date(lastKey), -i)));
            }}
        }} else {{
            // 起点取 max(有记录的首月, cfg.start)——cfg.start 用于砍掉更早的旧数据
            let cur = cfg.floor(sorted[0].dateObj);
            if (cfg.start && cur < cfg.start) cur = cfg.floor(cfg.start);
            const end = new Date(lastKey);
            let guard = 0;                      // 防 offset 异常时死循环
            while (cur <= end && guard++ < 600) {{
                seq.push(new Date(cur));
                cur = cfg.offset(cur, 1);
            }}
        }}

        return seq.map(dateObj =>
            Object.assign(zeroBucket(), sums.get(+dateObj) || {{}}, {{dateObj}}));
    }}

    let range = "day";

    function render() {{
        container.innerHTML = "";
        const cfg = RANGES[range];
        const buckets = bucketize(range);
        if (buckets.length === 0) {{
            container.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:40px 0;">该视图暂无数据</p>';
            return;
        }}

        const margin = {{top: 20, right: 20, bottom: 34, left: 40}};
        const width = Math.max(240, container.clientWidth - margin.left - margin.right);
        const height = 200 - margin.top - margin.bottom;

        const svg = d3.select(container).append("svg")
            .attr("width", width + margin.left + margin.right)
            .attr("height", height + margin.top + margin.bottom)
            .append("g")
            .attr("transform", `translate(${{margin.left}},${{margin.top}})`);

        // 离散刻度：每个桶一个确定位置，刻度和数据点必然对齐。
        // 旧实现用 scaleTime + axisBottom().ticks()，由 D3 自选「漂亮」刻度，
        // 位置和真实数据点错开，窄容器下标签还会互相压住。
        const x = d3.scalePoint()
            .domain(buckets.map((b, i) => i))
            .range([0, width])
            .padding(0.5);

        const yMax = d3.max(buckets, b => d3.max(SUBJECTS, s => b[s] || 0)) || 1;
        const y = d3.scaleLinear().domain([0, yMax]).nice().range([height, 0]);

        // 标签抽稀：按可用宽度算每隔几个桶标一个。
        // 从**最后一个桶往前**等间隔取，保证「最近」这个刻度一定在，
        // 且相邻刻度恒定相隔 step 个桶。早先写成 i % step === 0 再补一个
        // 末位，补出来的末位会和前一个刻度贴在一起（只差 1 个桶）。
        const perLabel = range === "month" ? 46 : 40;
        const maxLabels = Math.max(2, Math.floor(width / perLabel));
        const step = Math.max(1, Math.ceil(buckets.length / maxLabels));
        const tickIdx = [];
        for (let i = buckets.length - 1; i >= 0; i -= step) tickIdx.unshift(i);

        svg.append("g")
            .attr("class", "axis")
            .attr("transform", `translate(0,${{height}})`)
            .call(d3.axisBottom(x)
                .tickValues(tickIdx)
                .tickFormat(i => cfg.label(buckets[i].dateObj)));

        svg.append("g")
            .attr("class", "axis")
            .call(d3.axisLeft(y).ticks(5).tickFormat(d3.format("d")));

        SUBJECTS.forEach(subj => {{
            if (!buckets.some(b => (b[subj] || 0) > 0)) return;

            const line = d3.line()
                .x((b, i) => x(i))
                .y(b => y(b[subj] || 0))
                .curve(d3.curveMonotoneX);

            const path = svg.append("path")
                .datum(buckets)
                .attr("fill", "none")
                .attr("stroke", SUBJECT_COLORS[subj])
                .attr("stroke-width", 2)
                .attr("d", line);

            // 入场：把这条线「画出来」。用 getTotalLength 拉一根等长的 dasharray，
            // 再把 dashoffset 从全长补到 0；跑完**必须清掉 dasharray**，
            // 否则线一直是虚线状态（之后 hover 重画会露馅）。
            if (CHART_MOTION && path.node && path.node().getTotalLength) {{
                try {{
                    const L = path.node().getTotalLength();
                    if (L > 0) {{
                        const clearDash = function () {{
                            path.attr("stroke-dasharray", null).attr("stroke-dashoffset", null);
                        }};
                        path.attr("stroke-dasharray", L + " " + L)
                            .attr("stroke-dashoffset", L)
                            .transition().duration(820).ease(d3.easeCubicOut)
                            .attr("stroke-dashoffset", 0)
                            .on("end", clearDash);
                        // 补间没跑到就兜底清掉：留着 dashoffset=全长 的话这条线是**看不见的**
                        chartSettle(clearDash, 820);
                    }}
                }} catch (e) {{ /* SVG 量不到长度就退化成「直接出现」 */ }}
            }}

            const dots = svg.selectAll(`.dot-${{subj}}`)
                .data(buckets.map((b, i) => ({{b, i}})).filter(o => (o.b[subj] || 0) > 0))
                .enter().append("circle")
                .attr("cx", o => x(o.i))
                .attr("cy", o => y(o.b[subj]))
                .attr("fill", SUBJECT_COLORS[subj])
                .attr("r", CHART_MOTION ? 0 : 3);
            if (CHART_MOTION) {{
                dots.transition().duration(300).ease(d3.easeBackOut.overshoot(1.4))
                    .delay(o => 260 + o.i * 14)
                    .attr("r", 3);
            }}
        }});

        // ---- 悬停读数：十字准线 + 整列各科篇数 ----
        // 原来信息只藏在原生 <title> 里——要悬停半天才弹，而且一次只看得见一个点。
        // 改成对准哪个周期就同时给出四科读数，并高亮该周期上所有有值的点。
        const tip = el("div", {{className: "tl-tip"}}, container);
        tip.style.display = "none";

        const focus = svg.append("g").attr("pointer-events", "none").style("display", "none");
        focus.append("line").attr("class", "tl-guide").attr("y1", 0).attr("y2", height);
        const halos = focus.selectAll("circle")
            .data(SUBJECTS).enter().append("circle")
            .attr("class", "tl-halo").attr("cx", 0).attr("r", 4.5);

        svg.append("rect")
            .attr("width", width).attr("height", height)
            .attr("fill", "none").attr("pointer-events", "all")
            .on("mousemove", function (ev) {{
                const mx = d3.pointer(ev)[0];
                let best = 0, bestD = Infinity;
                buckets.forEach((b, i) => {{
                    const dist = Math.abs(x(i) - mx);
                    if (dist < bestD) {{ bestD = dist; best = i; }}
                }});
                const b = buckets[best];
                focus.style("display", null).attr("transform", `translate(${{x(best)}},0)`);
                halos.attr("cy", s => y(b[s] || 0))
                     .attr("fill", s => SUBJECT_COLORS[s])
                     .style("display", s => (b[s] || 0) > 0 ? null : "none");

                let rows = "";
                SUBJECTS.forEach(s => {{
                    rows += '<div class="tl-tip-row"><i style="background:' + SUBJECT_COLORS[s]
                          + '"></i>' + s + '<b>' + (b[s] || 0) + '</b></div>';
                }});
                tip.innerHTML = '<div class="tl-tip-head">' + cfg.label(b.dateObj)
                              + ' · 共 ' + b.total + ' 篇</div>' + rows;
                tip.style.display = "block";
                // 贴左右边时把浮层推回容器内，免得溢出被裁
                const px = x(best) + margin.left;
                const tw = tip.offsetWidth;
                tip.style.left = Math.max(2, Math.min(width + margin.left - tw - 2, px - tw / 2)) + "px";
                tip.style.top = (margin.top - 4) + "px";
            }})
            .on("mouseleave", function () {{
                focus.style("display", "none");
                tip.style.display = "none";
            }});
    }}

    // 图例挂在 .section 上而不是容器里 —— render() 会清空容器，挂里面会被抹掉
    const legendDiv = el("div", {{className: "chart-legend"}}, container.parentElement);
    SUBJECTS.forEach(subj => {{
        const item = el("div", {{className: "chart-legend-item"}}, legendDiv);
        const dot = el("div", {{className: "chart-legend-dot"}}, item);
        dot.style.background = SUBJECT_COLORS[subj];
        el("span", {{textContent: subj}}, item);
    }});

    const toggle = document.getElementById("timeline-range");
    if (toggle) {{
        toggle.addEventListener("click", ev => {{
            const btn = ev.target.closest(".range-btn");
            if (!btn || btn.dataset.range === range) return;
            range = btn.dataset.range;
            toggle.querySelectorAll(".range-btn").forEach(b =>
                b.classList.toggle("is-active", b === btn));
            render();
        }});
    }}

    render();
}}];

// ============================================================
// Section 4: Level Distribution (D3 stacked bar)
// ============================================================
(window.__pageRenderers.overview = window.__pageRenderers.overview || []).push(function () {{
    const container = document.getElementById("level-chart");
    const data = D.level_dist;
    if (!data || data.length === 0) {{
        container.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:40px 0;">暂无级别数据</p>';
        return;
    }}

    const margin = {{top: 20, right: 20, bottom: 30, left: 40}};
    const width = container.clientWidth - margin.left - margin.right;
    const height = 200 - margin.top - margin.bottom;

    const svg = d3.select(container).append("svg")
        .attr("width", width + margin.left + margin.right)
        .attr("height", height + margin.top + margin.bottom)
        .append("g")
        .attr("transform", `translate(${{margin.left}},${{margin.top}})`);

    const levels = ["L1", "L2", "L3"];
    const colors = {{L1: PALETTE.dianqing, L2: PALETTE.xiang, L3: PALETTE.zhusha}};

    const x = d3.scaleBand()
        .domain(data.map(d => d.subject))
        .range([0, width])
        .padding(0.3);

    const yMax = d3.max(data, d => d.L1 + d.L2 + d.L3);
    const y = d3.scaleLinear()
        .domain([0, yMax])
        .range([height, 0]);

    svg.append("g")
        .attr("class", "axis")
        .attr("transform", `translate(0,${{height}})`)
        .call(d3.axisBottom(x));

    svg.append("g")
        .attr("class", "axis")
        .call(d3.axisLeft(y).ticks(5));

    // 坐标轴先淡入，柱子再长——顺序反了会像「柱子撞在还没画好的轴上」
    svg.selectAll("g.axis")
        .attr("opacity", 0)
        .transition().duration(CHART_MOTION ? 380 : 0)
        .attr("opacity", 1);

    // Stacked bars：每段都从**基线**长出来（先贴底、高度 0，再补间到目标位置）。
    // 同一科目里的 L1→L2→L3 依次起步、科目之间再错开，看着是「一层层堆上去」。
    data.forEach((d, di) => {{
        let cumY = 0;
        levels.forEach((lv, li) => {{
            const val = d[lv] || 0;
            const yTop = y(cumY + val), yBot = y(cumY);
            const rect = svg.append("rect")
                .datum({{ subject: d.subject, level: lv, val: val }})
                .attr("class", "lv-bar")
                .attr("x", x(d.subject))
                .attr("width", x.bandwidth())
                .attr("fill", colors[lv])
                .attr("rx", 2)
                .attr("y", yBot)
                .attr("height", 0);
            if (val > 0) {{
                const y2 = yTop, h2 = Math.max(0, yBot - yTop);
                rect.transition()
                    .duration(CHART_DUR)
                    .delay(di * 85 + li * 35)
                    .ease(d3.easeCubicOut)
                    .attr("y", y2)
                    .attr("height", h2);
                chartSettle(function () {{ rect.attr("y", y2).attr("height", h2); }}, di * 85 + li * 35);
            }}
            cumY += val;
        }});
    }});

    // ---- 悬停：整列（同一科目的三层）一起抬起来、其余列压暗，并弹出这一列的读数 ----
    // 动效走 CSS 过渡（.lv-bar），比 d3 补间轻：mouseenter 一秒能来几十次，
    // 补间会排队堆积，而 CSS 过渡只保留最后一帧。
    const bars = svg.selectAll("rect.lv-bar");
    const tip = el("div", {{ className: "tl-tip" }}, container);
    tip.style.display = "none";
    function showLvTip(subj) {{
        const row = data.filter(r => r.subject === subj)[0] || {{}};
        const sum = (row.L1 || 0) + (row.L2 || 0) + (row.L3 || 0);
        tip.innerHTML = '<div class="tl-tip-head">' + subj + ' · 共 ' + sum + ' 条</div>'
            + levels.map(lv => '<div class="tl-tip-row"><i style="background:' + colors[lv]
                + '"></i>' + lv + '<b>' + (row[lv] || 0) + '</b></div>').join("");
        tip.style.display = "block";
        // 贴着那一列居中，贴边时把浮层推回容器内（和趋势图的做法一致）
        const tw = tip.offsetWidth;
        const cx = x(subj) + x.bandwidth() / 2 + margin.left;
        tip.style.left = Math.max(2, Math.min(width + margin.left - tw - 2, cx - tw / 2)) + "px";
        tip.style.top = (margin.top - 4) + "px";
    }}
    function hlBars(fn) {{
        const any = bars.filter(fn);
        container.classList.toggle("lv-dim", any.size() > 0);
        bars.classed("hl", fn);
    }}
    bars.on("mouseenter", function (ev, b) {{ hlBars(x2 => x2.subject === b.subject); showLvTip(b.subject); }})
        .on("mouseleave", function () {{ hlBars(() => false); tip.style.display = "none"; }});

    // Legend：鼠标移到某一级上，就把四科里这一级都点亮（其余压暗）——一眼看出
    // 「L3 主要集中在 408 和政治」这种结构。
    const legendDiv = el("div", {{className: "chart-legend"}}, container);
    levels.forEach(lv => {{
        const item = el("div", {{className: "chart-legend-item"}}, legendDiv);
        const dot = el("div", {{className: "chart-legend-dot"}}, item);
        dot.style.background = colors[lv];
        el("span", {{textContent: lv}}, item);
        item.onmouseenter = function () {{ hlBars(b => b.level === lv); }};
        item.onmouseleave = function () {{ hlBars(() => false); }};
    }});
}});

// ============================================================
// Section 5: 闪卡记忆状态 —— 左环形图 + 右正确率趋势
// ============================================================
// 同在「闪卡」页，同样读 clientWidth，同样要等页面可见才画。
// 2026-09-17 改版：原来环形图占满整行（圆心钉在 x=130，右边一大片空白），
// 趋势图横在下面 80px——没有坐标轴、没有悬停读数，正确率挤在 67~88 之间
// 被 0~100 的刻度压成一条直线。现在左右分栏，两边都带悬停浮层。
window.__pageRenderers.flash = [function () {{
    const container = document.getElementById("flashcard-chart");
    const states = D.card_states;
    const total = Object.values(states).reduce((a, b) => a + b, 0);

    if (total === 0) {{
        container.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:40px 0;">暂无闪卡数据</p>';
        return;
    }}

    const pieData = Object.entries(states)
        .filter(([, v]) => v > 0)
        .map(([k, v]) => ({{label: k, value: v}}));

    // render() 有被再次调用的可能，先清空，免得图例叠成两份
    container.innerHTML = "";
    const layout = el("div", {{className: "fc-layout"}}, container);
    const left = el("div", {{className: "fc-donut"}}, layout);
    const right = el("div", {{className: "fc-trend"}}, layout);

    const color = d3.scaleOrdinal()
        .domain(["New", "Learning", "Review", "Relearning"])
        .range([PALETTE.hui, PALETTE.xiang, PALETTE.zhuqing, PALETTE.zhusha]);

    // ===================== 左：环形图 =====================
    const w = 220;
    const radius = w / 2 - 8;

    const svg = d3.select(left).append("svg")
        .attr("class", "fc-donut-svg")
        .attr("width", w)
        .attr("height", w)
        .append("g")
        .attr("transform", `translate(${{w/2}},${{w/2}})`);

    const pie = d3.pie().value(d => d.value).sort(null);
    const arc = d3.arc().innerRadius(radius * 0.56).outerRadius(radius);

    let sweeping = CHART_MOTION;
    const arcBig = d3.arc().innerRadius(radius * 0.56).outerRadius(radius + 5);
    const arcs = svg.selectAll("path")
        .data(pie(pieData))
        .enter().append("path")
        .attr("class", "pm-arc")
        .attr("fill", d => color(d.data.label))
        .attr("stroke", PALETTE.mo)
        .attr("stroke-width", 2);
    // 先写终值再补间：万一补间一次都没 tick，饼图至少是**完整可见**的
    // （attrTween 自己从 startAngle 插值，不依赖当前属性值，所以照样有扫开效果）
    arcs.attr("d", arc);
    if (CHART_MOTION) {{
        arcs.transition().duration(720).ease(d3.easeCubicOut)
            .attrTween("d", function (d) {{
                const from = {{ startAngle: d.startAngle, endAngle: d.startAngle }};
                const i = d3.interpolate(from, d);
                return function (t) {{ return arc(i(t)); }};
            }})
            // 扫开过程中别接悬停：那会把补间打断，扇形停在半个位置
            .on("end", function () {{ sweeping = false; }});
    }}

    // Center text（悬停某块时会临时换成那一块的读数，走开再还原）
    svg.append("text")
        .attr("class", "pie-center-num")
        .attr("text-anchor", "middle")
        .attr("dy", "-0.2em")
        .attr("fill", PALETTE.xuan)
        .attr("font-size", "1.4rem")
        .attr("font-weight", "700")
        .text(total);

    svg.append("text")
        .attr("class", "pie-center-label")
        .attr("text-anchor", "middle")
        .attr("dy", "1.2em")
        .attr("fill", PALETTE.tao)
        .attr("font-size", "0.7rem")
        .text("张闪卡");

    // ---- 悬停：这一块向外弹 5px、其余压暗，中心数字换成这一块的值，并弹浮层 ----
    // ⚠️ d3 v7 的 on() 回调是 (event, d)，**事件在前**。写成 function (d) 的话
    // 拿到的是 MouseEvent，d.data 是 undefined——一悬停就 TypeError，扇形不动、
    // 中心数字也不换，看起来就是「悬停没反应」。
    const numEl = svg.select(".pie-center-num"), labEl = svg.select(".pie-center-label");
    const pieTip = el("div", {{className: "tl-tip"}}, left);
    pieTip.style.display = "none";
    const pct = v => (v / total * 100).toFixed(1) + "%";

    function pieHot(ev, d) {{
        if (sweeping || !d) return;              // 扫开途中不接，免得把补间打断
        arcs.classed("dimmed", x => x !== d);
        d3.select(this).attr("d", arcBig);
        numEl.text(d.data.value);
        labEl.text(d.data.label);

        const c = color(d.data.label);
        pieTip.innerHTML = '<div class="tl-tip-head">' + d.data.label + '</div>'
            + '<div class="tl-tip-row"><i style="background:' + c + '"></i>张数<b>'
            + d.data.value + '</b></div>'
            + '<div class="tl-tip-row"><i style="background:' + c + '"></i>占比<b>'
            + pct(d.data.value) + '</b></div>';
        pieTip.style.display = "block";
        const p = d3.pointer(ev, left);
        const tw = pieTip.offsetWidth || 0;
        pieTip.style.left = Math.max(0, Math.min(left.clientWidth - tw, p[0] - tw / 2)) + "px";
        pieTip.style.top = Math.max(0, p[1] - (pieTip.offsetHeight || 0) - 10) + "px";
    }}
    function pieCalm() {{
        if (sweeping) return;
        arcs.classed("dimmed", false);
        arcs.attr("d", arc);
        numEl.text(total);
        labEl.text("张闪卡");
        pieTip.style.display = "none";
    }}
    arcs.on("mouseenter", pieHot).on("mouseleave", pieCalm);

    // Legend：悬停某一项 = 把对应的那块扇形弹出并高亮（和直接悬停扇形一个效果）
    const legendDiv = el("div", {{className: "chart-legend"}}, left);
    pieData.forEach(d => {{
        const item = el("div", {{className: "chart-legend-item"}}, legendDiv);
        const dot = el("div", {{className: "chart-legend-dot"}}, item);
        dot.style.background = color(d.label);
        el("span", {{textContent: `${{d.label}} (${{d.value}})`}}, item);
        const pick = () => arcs.filter(x => x.data.label === d.label);
        item.onmouseenter = function () {{
            if (sweeping) return;
            item.classList.add("legend-item-hot");
            arcs.classed("dimmed", x => x.data.label !== d.label);
            pick().attr("d", arcBig);
            numEl.text(d.value); labEl.text(d.label);
        }};
        item.onmouseleave = function () {{
            item.classList.remove("legend-item-hot");
            pieCalm();
        }};
    }});

    // ===================== 右：正确率趋势 =====================
    const trend = (D.accuracy_trend || []).slice();
    const head = el("div", {{className: "fc-trend-head"}}, right);
    el("div", {{className: "fc-trend-title", textContent: "正确率趋势（近 14 天）"}}, head);
    if (trend.length > 0) {{
        const last = trend[trend.length - 1];
        const now = el("div", {{className: "fc-trend-now"}}, head);
        el("b", {{textContent: last.accuracy + "%"}}, now);
        el("span", {{textContent: " " + last.date.slice(5) + " · " + last.total + " 题"}}, now);
    }}

    if (trend.length === 0) {{
        el("div", {{className: "fc-trend-empty", textContent: "近 14 天还没有闪卡记录"}}, right);
        return;
    }}

    const W = Math.max(260, right.clientWidth || (container.clientWidth - 260) || 360);
    const H = 190;
    const M = {{top: 12, right: 14, bottom: 26, left: 40}};
    const pT = M.top, pB = H - M.bottom, pL = M.left, pR = W - M.right;

    const tsvg = d3.select(right).append("svg")
        .attr("class", "fc-trend-svg")
        .attr("width", W)
        .attr("height", H);

    const xs = d3.scalePoint().domain(trend.map(d => d.date)).range([pL, pR]).padding(0.4);

    // 正确率常年挤在 60~90 之间，硬用 0~100 会把线压成一条直线；下界退到最近的
    // 十位（**不低于 0**），但刻度一定要画出来——截断坐标轴而不标注就是视觉夸大。
    const lo = Math.min.apply(null, trend.map(d => d.accuracy));
    const yMin = lo >= 50 ? Math.floor((lo - 10) / 10) * 10 : 0;
    const ys = d3.scaleLinear().domain([yMin, 100]).range([pB, pT]);
    const yTicks = ys.ticks(4);

    // 横向网格线 + 纵轴刻度（"%"就写在刻度上，省掉一条旋转的轴标题）
    tsvg.append("g").selectAll("line")
        .data(yTicks).enter().append("line")
        .attr("x1", pL).attr("x2", pR)
        .attr("y1", d => ys(d)).attr("y2", d => ys(d))
        .attr("stroke", PALETTE.bian).attr("stroke-width", 1)
        .attr("stroke-dasharray", d => (d === 100 ? null : "2 4"));

    tsvg.append("g").selectAll("text")
        .data(yTicks).enter().append("text")
        .attr("x", pL - 6).attr("y", d => ys(d))
        .attr("text-anchor", "end").attr("dominant-baseline", "middle")
        .attr("fill", PALETTE.hui).attr("font-size", "0.62rem")
        .text(d => d + "%");

    // 横轴日期
    tsvg.append("g").selectAll("text")
        .data(trend).enter().append("text")
        .attr("x", d => xs(d.date)).attr("y", pB + 16)
        .attr("text-anchor", "middle")
        .attr("fill", PALETTE.hui).attr("font-size", "0.62rem")
        .text(d => d.date.slice(5));

    // 面积 + 折线（面积只是给线一个落脚的底，压得很淡）
    tsvg.append("path")
        .datum(trend)
        .attr("fill", PALETTE.zhuqing).attr("opacity", .12)
        .attr("d", d3.area().x(d => xs(d.date)).y0(pB).y1(d => ys(d.accuracy))
            .curve(d3.curveMonotoneX));

    tsvg.append("path")
        .datum(trend)
        .attr("fill", "none")
        .attr("stroke", PALETTE.zhuqing)
        .attr("stroke-width", 2)
        .attr("d", d3.line().x(d => xs(d.date)).y(d => ys(d.accuracy))
            .curve(d3.curveMonotoneX));

    tsvg.append("g").selectAll("circle")
        .data(trend).enter().append("circle")
        .attr("cx", d => xs(d.date)).attr("cy", d => ys(d.accuracy))
        .attr("r", 3.2)
        .attr("fill", PALETTE.zhuqing)
        .attr("stroke", PALETTE.mo).attr("stroke-width", 1.5);

    // ---- 悬停：竖向准线 + 光环 + 当天读数（日期 / 正确率 / 练习题数）----
    const tip = el("div", {{className: "tl-tip"}}, right);
    tip.style.display = "none";
    // 浮层是列内绝对定位，而准线坐标是 svg 内的 —— 差一个表头高度
    const svgTop = head.offsetHeight || 0;

    const guide = tsvg.append("line")
        .attr("class", "tl-guide").attr("y1", pT).attr("y2", pB).attr("x1", 0).attr("x2", 0)
        .style("display", "none");
    const halo = tsvg.append("circle")
        .attr("r", 6.5).attr("fill", PALETTE.zhuqing).attr("opacity", .3)
        .style("display", "none");

    tsvg.append("rect")
        .attr("x", pL - 12).attr("y", pT)
        .attr("width", Math.max(1, pR - pL + 24)).attr("height", Math.max(1, pB - pT))
        .attr("fill", "none").attr("pointer-events", "all")
        .on("mousemove", function (ev) {{
            const mx = d3.pointer(ev)[0];
            let best = 0, bestD = Infinity;
            trend.forEach((d, i) => {{
                const dist = Math.abs(xs(d.date) - mx);
                if (dist < bestD) {{ bestD = dist; best = i; }}
            }});
            const d = trend[best], cx = xs(d.date), cy = ys(d.accuracy);
            guide.style("display", null).attr("x1", cx).attr("x2", cx);
            halo.style("display", null).attr("cx", cx).attr("cy", cy);

            tip.innerHTML = '<div class="tl-tip-head">' + d.date + '</div>'
                + '<div class="tl-tip-row"><i style="background:' + PALETTE.zhuqing + '"></i>正确率<b>'
                + d.accuracy + '%</b></div>'
                + '<div class="tl-tip-row"><i style="background:' + PALETTE.hui + '"></i>练习题数<b>'
                + d.total + '</b></div>';
            tip.style.display = "block";
            const tw = tip.offsetWidth || 0;
            tip.style.left = Math.max(2, Math.min(W - tw - 2, cx - tw / 2)) + "px";
            tip.style.top = Math.max(0, svgTop + cy - (tip.offsetHeight || 0) - 12) + "px";
        }})
        .on("mouseleave", function () {{
            guide.style("display", "none");
            halo.style("display", "none");
            tip.style.display = "none";
        }});
}}];

// ============================================================
// Section 2a: 薄弱知识点（来自闪卡错误，非任务完成率）
// ============================================================
(function() {{
    const box = document.getElementById("weak-list");
    const weak = (D.weak_topics && D.weak_topics.weak) || [];
    const uncovered = (D.weak_topics && D.weak_topics.uncovered_weighty) || [];

    if (weak.length === 0 && uncovered.length === 0) {{
        box.innerHTML = '<div class="weak-empty">暂无薄弱信号 —— 去下方练习区刷几组闪卡，错在哪里就提醒哪里</div>';
        return;
    }}

    // 正确率分档上色：<60% 朱砂、60-79% 缃、>=80% 青灰（越红越该复习）
    const accBand = a => a < 60 ? "bad" : a < 80 ? "warn" : "ok";
    const accColor = a => a < 60 ? "var(--zhusha)" : a < 80 ? "var(--xiang)" : "var(--zhuqing)";

    weak.forEach(w => {{
        const item = el("div", {{className: "weak-item"}}, box);
        const left = el("div", {{className: "weak-main"}}, item);

        const head = el("div", {{className: "weak-head"}}, left);
        const name = el("div", {{className: "weak-name", textContent: w.name}}, head);
        if (w.low_sample) {{
            const tag = el("span", {{className: "weak-tag", textContent: "样本少"}}, name);
            tag.title = "练习次数不足 5 次，正确率仅供参考";
        }}
        el("div", {{className: "weak-acc " + accBand(w.accuracy),
                    textContent: w.accuracy + "%"}}, head);

        const bar = el("div", {{className: "weak-bar"}}, left);
        const fill = el("div", {{className: "weak-bar-fill"}}, bar);
        fill.style.width = Math.max(2, w.accuracy) + "%";
        fill.style.background = accColor(w.accuracy);

        const meta = w.subject + " · 正确率 " + w.correct + "/" + w.total + " 题";
        el("div", {{className: "weak-meta",
                    textContent: w.recent_total
                        ? meta + " · 近30天 " + w.recent_accuracy + "%（"
                          + (w.recent_total - w.recent_wrong) + "/" + w.recent_total + "）"
                        : meta}}, left);

        el("div", {{className: "weak-count", textContent: "重点复习"}}, item);
    }});

    if (uncovered.length > 0) {{
        const tip = el("div", {{className: "weak-note"}}, box);
        tip.textContent = "高分考点尚未出卡（可针对性生成验证卡）："
            + uncovered.map(u => u.name + "(" + u.weight + "分)").join("、");
    }}

    const note = el("div", {{className: "weak-note"}}, box);
    note.textContent = "规则：按正确率（答对题数/练习题数）从低到高排，不是按错误次数——"
        + "练得多的考点自然错得多，比错误次数等于比谁刷得多。"
        + "从没练过的考点不在这里（去下面练习区开卡）；答对过的考点不会因为「没整理笔记」被点名。";

    capList(box, ".weak-item", 3, "薄弱知识点（按闪卡正确率） · 全部");
}})();

// ============================================================
// Section 2b: 真缺口 Top 5（无笔记且未通过闪卡验证）
// ============================================================
(function() {{
    const list = document.getElementById("gap-list");
    const gaps = D.top_gaps;

    if (!gaps || gaps.length === 0) {{
        list.innerHTML = '<li style="color:var(--text-muted);text-align:center;padding:40px 0;">所有考点已有笔记或已验证掌握</li>';
        return;
    }}

    gaps.forEach(g => {{
        const li = el("li", {{className: "gap-item"}}, list);
        const rank = el("div", {{className: "gap-rank", textContent: g.rank}}, li);
        rank.style.background = g.rank <= 2 ? PALETTE.zhusha : g.rank <= 4 ? PALETTE.xiang : PALETTE.dianqing;

        const info = el("div", {{className: "gap-info"}}, li);
        el("div", {{className: "gap-topic", textContent: g.topic}}, info);
        el("div", {{className: "gap-meta", textContent: `${{g.subject}} · ${{g.sub}}`}}, info);

        el("div", {{
            className: "gap-weight",
            textContent: g.weight + "分"
        }}, li);
    }});

    capList(list, ".gap-item", 3, "真缺口（无笔记且未验证掌握） · 全部");
}})();

// ============================================================
// 总览页薄弱提醒两卡的折叠（2026-09-18）：平时只展示前 N 项，
// 「展开全部 / 收起」就地展开，「浮窗查看」弹 sk-overlay 模态看全量。
// ============================================================
function capList(box, itemSel, n, title) {{
    const items = Array.from(box.querySelectorAll(itemSel));
    if (items.length <= n) return;                       // 不足 N 项就不折腾
    const hidden = items.slice(n);
    hidden.forEach(function (el) {{ el.classList.add("over-cap"); }});

    const foot = document.createElement("div");
    foot.className = "cap-foot";
    foot.innerHTML = '<span class="cap-info">共 ' + items.length + ' 项 · 默认展示前 ' + n + ' 项</span>'
        + '<button class="cap-btn" data-act="expand">展开全部 ▾</button>'
        + '<button class="cap-btn cap-float" data-act="float">🔍 浮窗查看</button>';
    box.after(foot);

    const expand = foot.querySelector('[data-act="expand"]');
    expand.onclick = function () {{
        const collapsed = hidden[0].classList.contains("over-cap");
        hidden.forEach(function (el) {{ el.classList.toggle("over-cap", !collapsed); }});
        expand.textContent = collapsed ? "收起 ▴" : "展开全部 ▾";
    }};
    const fl = foot.querySelector('[data-act="float"]');
    fl.onclick = function () {{ openCapFloat(items, title); }};
}}
function openCapFloat(items, title) {{
    const ov = document.createElement("div");
    ov.className = "sk-overlay";
    ov.innerHTML = '<div class="sk-box">'
        + '<div class="sk-head"><span class="sk-title"></span>'
        + '<button class="rev-modal-close" type="button" title="关闭（Esc）">✕</button></div>'
        + '<div class="sk-body cap-body"></div></div>';
    document.body.appendChild(ov);
    ov.querySelector(".sk-title").textContent = title;
    const body = ov.querySelector(".sk-body");
    items.forEach(function (el) {{ body.appendChild(el.cloneNode(true)); }});
    ov.querySelector(".rev-modal-close").onclick = function () {{ ov.remove(); }};
    ov.addEventListener("click", function (ev) {{ if (ev.target === ov) ov.remove(); }});
    document.addEventListener("keydown", function esc(e) {{
        if (e.key === "Escape" && ov.isConnected) {{ ov.remove(); document.removeEventListener("keydown", esc, true); }}
    }}, true);
}}

// __DAY_START__
// __KEYS_JS__
// __FLASH_JS__
// __REVIVE_JS__
// __NOTEQ_JS__
// __TASK_JS__
// __SETTINGS_JS__
// __POMO_JS__
// __SHELL_JS__
// __MR_JS__
// __RV_JS__
// __NAV_JS__
// __BOOT_JS__
</script>
</body>
</html>'''


# ---------------------------------------------------------------------------
# 笔记盘活区（样式与交互脚本为普通字符串，避免 f-string 大括号转义）
# ---------------------------------------------------------------------------
REVIVE_CSS = '''
        .rev-head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
        .rev-score { font-size: 2rem; font-weight: 700; }
        .rev-score-label { font-size: 0.8rem; color: var(--text-secondary); }
        .rev-chips { display: flex; gap: 8px; flex-wrap: wrap; margin-left: auto; }
        .rev-chip { font-size: 0.75rem; padding: 3px 10px; border-radius: 12px; }
        .chip-hot { background: rgba(var(--zhuqing-rgb),.15); color: var(--zhuqing-lt); }
        .chip-warm { background: rgba(var(--xiang-rgb),.15); color: var(--xiang-lt); }
        .chip-cold { background: rgba(var(--dianqing-rgb),.15); color: var(--dianqing-lt); }
        .chip-frozen { background: rgba(var(--zhusha-rgb),.15); color: var(--zhusha-lt); }

        .rev-prefix-row { display: flex; align-items: center; gap: 10px; margin: 7px 0; font-size: 0.8rem; }
        .rev-prefix-name { width: 120px; flex-shrink: 0; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .rev-bar { flex: 1; height: 10px; border-radius: 2px; background: var(--bg-secondary); overflow: hidden; display: flex; }
        .rev-bar-seg { height: 100%; }
        .rev-prefix-meta { width: 190px; flex-shrink: 0; text-align: right; color: var(--text-muted); font-size: 0.72rem; }

        .rev-target { display: flex; align-items: center; gap: 10px; padding: 9px 0; border-bottom: 1px solid var(--border-color); font-size: 0.86rem; }
        .rev-target:last-child { border-bottom: none; }
        .rev-badge { font-size: 0.7rem; padding: 2px 8px; border-radius: 10px; white-space: nowrap; }
        .rev-target-info { flex: 1; min-width: 0; }
        .rev-target-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .rev-target-meta { font-size: 0.72rem; color: var(--text-muted); margin-top: 2px; }
        .rev-warn { color: var(--zhusha-lt); }
        .rev-urgency { font-size: 0.78rem; color: var(--accent-red); background: rgba(239,68,68,.12); border-radius: 10px; padding: 2px 9px; white-space: nowrap; }
        .rev-actions { display: flex; gap: 6px; flex-shrink: 0; }
        .rev-btn { background: var(--bg-secondary); color: var(--text-primary); border: 1px solid var(--border-color); border-radius: var(--border-radius); padding: 5px 11px; font-size: 0.78rem; cursor: pointer; white-space: nowrap; }
        .rev-btn:hover:not(:disabled) { border-color: var(--accent-blue); }
        .rev-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .rev-btn.primary { background: rgba(var(--dianqing-rgb),.18); border-color: rgba(var(--dianqing-rgb),.4); color: var(--dianqing-lt); }
        .rev-btn.ok { background: rgba(var(--zhuqing-rgb),.18); border-color: rgba(var(--zhuqing-rgb),.4); color: var(--zhuqing-lt); }
        .rev-empty { color: var(--text-muted); text-align: center; padding: 30px 0; font-size: 0.85rem; }
        .rev-error { color: var(--zhusha-lt); font-size: 0.75rem; margin-top: 8px; }
        .rev-tip { margin-top: 10px; font-size: 0.72rem; color: var(--text-muted); line-height: 1.6; }

        /* 读笔记弹窗 */
        /* ⚠️ 2026-09-13 修复：原为 var(--card-bg)，但主题里定义的变量叫 --bg-card，
           该变量从未存在 → 弹窗背景解析失败变成全透明，底层清单直接透上来压住正文。
           这不是"需要加模糊"的问题，是变量名写错了；两者都已修正。 */
        .rev-modal { position: fixed; inset: 0; background: rgba(0,0,0,.55); -webkit-backdrop-filter: blur(14px) saturate(0.85); backdrop-filter: blur(14px) saturate(0.85); display: flex; align-items: center; justify-content: center; z-index: 100; }
        .rev-modal-box { width: min(860px, 92vw); max-height: 86vh; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 18px 48px rgba(0,0,0,.5); }
        .rev-modal-head { padding: 14px 18px; border-bottom: var(--rule); font-weight: 600; display: flex; align-items: center; gap: 10px; }
        .rev-modal-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--font-serif); letter-spacing: .03em; }
        .rev-modal-tool { cursor: pointer; color: var(--text-secondary); font-size: 0.76rem; background: var(--bg-secondary); border: var(--rule); border-radius: var(--border-radius); padding: 3px 10px; white-space: nowrap; }
        .rev-modal-tool:hover { border-color: var(--dianqing); color: var(--text-primary); }
        .rev-modal-close { cursor: pointer; color: var(--text-muted); font-size: 1.1rem; background: none; border: none; padding: 0 4px; }
        .rev-modal-close:hover { color: var(--zhusha-lt); }
        .rev-modal-main { display: flex; flex: 1; min-height: 0; }
        .rev-modal-body { flex: 1; min-width: 0; padding: 18px; overflow-y: auto; font-size: 0.88rem; line-height: 1.75; scroll-behavior: smooth; }

        /* ---- 全屏阅读：铺满视口 + 左侧大纲 ---- */
        .rev-modal-box.full { width: 100vw; max-width: 100vw; height: 100vh; max-height: 100vh; border-radius: 0; border: none; }
        .rev-modal-box.full .rev-modal-body { padding: 24px max(28px, calc((100vw - 250px - 900px) / 2)); }
        .rev-outline { display: none; }
        .rev-modal-box.full .rev-outline { display: block; width: 250px; flex: none; overflow-y: auto; padding: 18px 6px 18px 16px; border-right: var(--rule); background: var(--bg-secondary); }
        .rev-outline-item { font-size: 0.78rem; line-height: 1.5; color: var(--text-secondary); padding: 4px 8px; border-left: 2px solid transparent; cursor: pointer; border-radius: 0 var(--border-radius) var(--border-radius) 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .rev-outline-item:hover { color: var(--text-primary); background: var(--bg-card-hover); }
        .rev-outline-item.lv1 { font-weight: 600; color: var(--text-primary); }
        .rev-outline-item.lv2 { padding-left: 18px; }
        .rev-outline-item.lv3 { padding-left: 30px; font-size: 0.74rem; color: var(--text-muted); }
        .rev-outline-item.active { border-left-color: var(--zhusha); color: var(--text-primary); background: rgba(var(--zhusha-rgb),.1); }
        .rev-outline-empty { font-size: 0.75rem; color: var(--text-muted); padding: 8px; }
        .rev-modal-body h1, .rev-modal-body h2, .rev-modal-body h3 { font-family: var(--font-serif); letter-spacing: .03em; margin: 18px 0 8px; color: var(--text-primary); }
        .rev-modal-body h1 { font-size: 1.25rem; } .rev-modal-body h2 { font-size: 1.1rem; } .rev-modal-body h3 { font-size: 0.98rem; }
        .rev-modal-body code { background: var(--bg-secondary); padding: 1px 5px; border-radius: 3px; font-size: 0.85em; font-family: "Cascadia Code", Consolas, monospace; }
        .rev-modal-body pre { background: var(--bg-primary); border: var(--rule); padding: 10px 12px; border-radius: var(--border-radius); overflow-x: auto; }
        /* 公式：行内不挤压行高，独立成块的上下留白；KaTeX 缺字体时不至于糊成一团 */
        .rev-modal-body .katex { font-size: 1.02em; }
        .rev-modal-body .katex-display { margin: 10px 0; overflow-x: auto; overflow-y: hidden; padding: 2px 0; }
        .rev-modal-body .tex-fallback { background: rgba(var(--xiang-rgb),.18); color: var(--xiang-lt); padding: 1px 5px; border-radius: 3px; font-size: 0.86em; }
        /* 笔记插图：笔记里用 <img style="width:70%"> 控制大小，这里补上圆角与上限定宽 */
        .rev-modal-body .rev-img { display: block; max-width: 100%; height: auto; margin: 14px auto; border: var(--rule); border-radius: var(--border-radius); background: var(--bg-primary); }
        .rev-modal-body .rev-img-bad { display: inline-block; color: var(--xiang-lt); background: rgba(var(--xiang-rgb),.15); border: 1px solid rgba(var(--xiang-rgb),.4); border-radius: 3px; padding: 1px 6px; font-size: 0.82em; }
        /* 引用块与提示块（> [!TIP] 等） */
        .rev-modal-body .rev-quote { margin: 12px 0; padding: 9px 14px; background: var(--bg-primary); border-left: 3px solid var(--hui); border-radius: var(--border-radius); color: var(--text-secondary); font-size: 0.88rem; }
        .rev-modal-body .rev-alert { margin: 12px 0; padding: 10px 14px; border-left: 3px solid var(--hui); border-radius: var(--border-radius); font-size: 0.88rem; color: var(--text-secondary); }
        .rev-modal-body .rev-alert-head { font-size: 0.74rem; font-weight: 600; letter-spacing: .12em; margin-bottom: 5px; }
        .rev-modal-body .rev-quote-table { border-collapse: collapse; margin: 6px 0; }
        .rev-modal-body .rev-alert.note { border-left-color: var(--dianqing); background: rgba(var(--dianqing-rgb),.10); }
        .rev-modal-body .rev-alert.note .rev-alert-head { color: var(--dianqing-lt); }
        .rev-modal-body .rev-alert.tip { border-left-color: var(--zhuqing); background: rgba(var(--zhuqing-rgb),.10); }
        .rev-modal-body .rev-alert.tip .rev-alert-head { color: var(--zhuqing-lt); }
        .rev-modal-body .rev-alert.important { border-left-color: var(--zi); background: rgba(var(--zi-rgb),.12); }
        .rev-modal-body .rev-alert.important .rev-alert-head { color: var(--zi-lt); }
        .rev-modal-body .rev-alert.warning { border-left-color: var(--xiang); background: rgba(var(--xiang-rgb),.10); }
        .rev-modal-body .rev-alert.warning .rev-alert-head { color: var(--xiang-lt); }
        .rev-modal-body .rev-alert.caution { border-left-color: var(--zhusha); background: rgba(var(--zhusha-rgb),.10); }
        .rev-modal-body .rev-alert.caution .rev-alert-head { color: var(--zhusha-lt); }
        /* mermaid：先显示源码，绘制成功后整块换成 SVG */
        .rev-modal-body .mermaid-box { margin: 14px 0; padding: 12px; background: var(--bg-primary); border: var(--rule); border-radius: var(--border-radius); overflow-x: auto; }
        .rev-modal-body .mermaid-box.done { text-align: center; }
        .rev-modal-body .mermaid-box svg { max-width: 100%; height: auto; }
        .rev-modal-body table { border-collapse: collapse; margin: 8px 0; }
        .rev-modal-body td, .rev-modal-body th { border: 1px solid var(--border-color); padding: 4px 9px; font-size: 0.82rem; }
        .rev-modal-foot { padding: 12px 18px; border-top: var(--rule); display: flex; justify-content: flex-end; gap: 8px; }

        '''

REVIVE_JS = '''
// ============================================================
// 笔记盘活区：冷笔记清单 + 阅读弹窗 + 一键盘活出题
// ============================================================
(function() {
    // API 基址：用当前页面 origin，平板/手机经局域网访问时才能正常调接口；
    // 本地以 file:// 直开时回落到 localhost:8080
    const API = location.protocol.startsWith('http') ? location.origin : "http://localhost:8080";
    const REV = (D.revival || {});
    const FRESH = REV.freshness || { summary: {}, by_prefix: {}, targets: [] };
    const PREFIX_NAME = {
        "408-DS": "408/数据结构", "408-CO": "408/计组", "408-OS": "408/操作系统", "408-CN": "408/计网",
        "MATH-GS": "数学/高数", "MATH-XD": "数学/线代", "MATH-GL": "数学/概率论",
        "POL-MY": "政治/马原", "POL-SG": "政治/史纲", "POL-MZ": "政治/毛中特",
        "POL-SX": "政治/思修", "POL-XX": "政治/习思想",
        "ENG-VOC": "英语/词汇", "ENG-GRAM": "英语/语法",
        "ENG-READ": "英语/阅读", "ENG-WRITE": "英语/写作",
        "ENG-CLOZE": "英语/完形", "ENG-TRAN": "英语/翻译"
    };
    const CAT_META = {
        hot: { label: "热", color: PALETTE.zhuqing }, warm: { label: "温", color: PALETTE.xiang },
        cold: { label: "冷", color: PALETTE.dianqing }, frozen: { label: "冰冻", color: PALETTE.zhusha }
    };
    function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
    function scoreColor(v) { return v >= 70 ? PALETTE.zhuqingLt : v >= 40 ? PALETTE.xiangLt : PALETTE.zhushaLt; }

    // ---- 总览：活跃分 + 四档分布 ----
    const s = FRESH.summary || {};
    const scoreEl = document.getElementById("rev-score");
    scoreEl.textContent = (s.alive_score != null ? s.alive_score : "--") + "%";
    scoreEl.style.color = scoreColor(s.alive_score || 0);
    const chips = [
        ["hot", s.hot || 0], ["warm", s.warm || 0], ["cold", s.cold || 0], ["frozen", s.frozen || 0]
    ];
    const chipBox = document.getElementById("rev-chips");
    chips.forEach(([cat, n]) => {
        const c = el("span", { className: "rev-chip chip-" + cat,
            textContent: CAT_META[cat].label + " " + n + " 篇" }, chipBox);
    });

    // ---- 各科目堆叠条 ----
    const byPrefixBox = document.getElementById("rev-by-prefix");
    Object.keys(FRESH.by_prefix || {}).sort().forEach(pfx => {
        const info = FRESH.by_prefix[pfx];
        const row = el("div", { className: "rev-prefix-row" }, byPrefixBox);
        el("div", { className: "rev-prefix-name", textContent: PREFIX_NAME[pfx] || pfx,
            title: pfx }, row);
        const bar = el("div", { className: "rev-bar" }, row);
        ["hot", "warm", "cold", "frozen"].forEach(cat => {
            const n = info[cat] || 0;
            if (n > 0) {
                const seg = el("div", { className: "rev-bar-seg" }, bar);
                seg.style.width = (n / info.total * 100) + "%";
                seg.style.background = CAT_META[cat].color;
                seg.title = CAT_META[cat].label + " " + n + " 篇";
            }
        });
        const linked = info.cards > 0 ? ("卡片" + info.cards + " · 近30天练" + (info.reviews_30d || 0)) : "尚无闪卡覆盖";
        el("div", { className: "rev-prefix-meta", textContent: info.total + "篇 · 活跃" + info.alive_score + "% · " + linked }, row);
    });

    // ---- 盘活目标清单 ----
    const listBox = document.getElementById("rev-targets");
    const targets = FRESH.targets || [];
    if (targets.length === 0) {
        listBox.innerHTML = '<div class="rev-empty">所有笔记都在活跃状态，暂无需要盘活的冷笔记</div>';
    }
    targets.forEach(t => {
        const item = el("div", { className: "rev-target" }, listBox);
        const badge = el("span", { className: "rev-badge chip-" + t.cat,
            textContent: CAT_META[t.cat].label + " · 闲置" + t.days_idle + "天" }, item);
        const info = el("div", { className: "rev-target-info" }, item);
        el("div", { className: "rev-target-name", textContent: t.name, title: t.file }, info);
        const cardInfo = t.cards > 0
            ? (t.practiced_30d ? "闪卡近30天练过" : '<span class="rev-warn">有闪卡但近30天未练</span>')
            : '<span class="rev-warn">尚无闪卡覆盖</span>';
        el("div", { className: "rev-target-meta", innerHTML: (PREFIX_NAME[t.prefix] || t.prefix) + " · " + cardInfo }, info);
        el("span", { className: "rev-urgency", textContent: "紧迫" + t.urgency,
            title: "章节考点权重×闲置程度" }, item);
        const actions = el("div", { className: "rev-actions" }, item);
        const readBtn = el("button", { className: "rev-btn", textContent: "读" }, actions);
        readBtn.addEventListener("click", () => openNote(t, readBtn));
        const genBtn = el("button", { className: "rev-btn primary", textContent: "盘活出题" }, actions);
        genBtn.addEventListener("click", () => genCards(t.prefix, genBtn));
    });

    const tip = document.getElementById("rev-tip");
    if (tip) tip.textContent = "规则：闲置天数 = 今天 − 最近一次（修改或在大盘读完标记）。点「读」并读完后标记，即可把它重新盘活；点「盘活出题」会基于这篇冷笔记即时生成针对性闪卡。";

    // ---- KaTeX 懒加载 ----
    // 实现已提到全局（见本文件前面「全局按需 KaTeX」块），这里直接用 window 上的那份，
    // 避免两个 loader 各注入一次 <script>。笔记阅读本来就必须连本地服务，KaTeX 走
    // 相对路径按需加载即可，不必内联那 3MB（含 60 个字体文件）。
    // 带兜底：本块要能脱离 FLASH_JS 单独跑（tools/test_note_render.js 就是单独抽它），
    // 拿不到全局实现时退回"不加载 + 源码展示"，而不是直接 ReferenceError。
    const ensureKatex = window.ensureKatex || (() => Promise.resolve(false));

    // ---- mermaid 懒加载 ----
    // 3.5MB，只在笔记里真出现 ```mermaid 时才加载。和 KaTeX 一样走本地 vendored
    // 文件（tools/mermaid/），断网也能画图。
    let mermaidPromise = null;
    let mmdSeq = 0;
    function ensureMermaid() {
        if (mermaidPromise) return mermaidPromise;
        mermaidPromise = new Promise(resolve => {
            if (window.mermaid) { resolve(true); return; }
            const s = document.createElement("script");
            s.src = "tools/mermaid/mermaid.min.js";
            s.onload = () => {
                try {
                    window.mermaid.initialize({
                        startOnLoad: false,
                        securityLevel: "strict",
                        suppressErrorRendering: true,   // 语法错时别往页面里塞红色报错图
                        theme: "base",
                        fontFamily: '"Songti SC", "Microsoft YaHei", sans-serif',
                        themeVariables: {
                            darkMode: true,
                            background: "#151A1A",
                            primaryColor: "#1F2626",
                            primaryTextColor: "#E5E9E7",
                            primaryBorderColor: "#5B7C99",
                            secondaryColor: "#273030",
                            tertiaryColor: "#1A2020",
                            lineColor: "#97A5A3",
                            textColor: "#E5E9E7",
                            fontSize: "14px",
                        },
                    });
                } catch (e) { /* 配置失败也让下面的 render 去试 */ }
                resolve(true);
            };
            s.onerror = () => resolve(false);
            document.head.appendChild(s);
        });
        return mermaidPromise;
    }

    // blocks 要由调用方传快照进来：mermaid 是异步画的，若中途又开了另一篇笔记，
    // mdRender 会把 mermaidBlocks 清空，那时候再按索引去取就串篇了。
    async function renderMermaidIn(root, blocks) {
        const boxes = root.querySelectorAll(".mermaid-box");
        if (!boxes.length) return;
        if (!(await ensureMermaid())) {
            boxes.forEach(b => b.insertAdjacentHTML("afterbegin",
                '<div class="rev-error">图表组件（mermaid）未加载，暂以源码显示。'
                + '请确认 src/tools/mermaid/ 存在，且是通过本地服务访问本页。</div>'));
            return;
        }
        for (const b of Array.from(boxes)) {
            const src = blocks[+b.dataset.mid];
            if (!src) continue;
            try {
                const { svg } = await window.mermaid.render("mmd-" + (++mmdSeq), src);
                b.innerHTML = svg;
                b.classList.add("done");
            } catch (e) {
                b.insertAdjacentHTML("afterbegin",
                    '<div class="rev-error">图表语法有误，已按源码显示：' + esc(e && e.message || e) + '</div>');
            }
        }
    }

    // 与全局同一实现（全局版用 escHtml 兜底，行为一致）
    const katexHtml = window.katexHtml
        || ((tex) => '<code class="tex-fallback">' + esc(tex) + '</code>');
    // ASCII 上下标（O(n^2)、∬_D、a_i → <sub>/<sup>）：与闪卡、AI 回复共用同一份实现，
    // 免得同一段公式在笔记和闪卡里呈现两种结果。桥没搭上就原样返回——
    // 笔记照常能看，只是少个上下标效果（2026-09-17）。
    const asciiMath = window.asciiMath || ((s) => s);

    // ---- 简易 Markdown 渲染（只覆盖笔记常用语法）----
    // 数学公式：先把 $$...$$ 与 $...$ 摘成占位符。**必须在 HTML 转义之前摘**，
    // 否则 \frac 的反斜杠、a<b 的尖括号会先被转义，KaTeX 收到的是坏源码。
    // 围栏代码块也要先摘，免得代码里的 $ 被误当公式。
    // 笔记插图：<img src="./assets/x.png" style="width:70%"> 与 markdown 的 ![alt](src)。
    // ⚠️ 必须在 HTML 转义之前摘出来，否则整个标签会被转成 &lt;img …&gt; 当成字面文本显示。
    // 摘出来后只按白名单重建标签（src/alt/title/尺寸），笔记里写没写 onerror 都不会带进来。
    const SAFE_LEN = /^(?:\\d+(?:\\.\\d+)?(?:%|px|em|rem|vw|vh|pt))$/;
    let noteDir = "";   // 当前笔记所在目录（知识库相对），相对插图路径按它解析
    const imgSpecs = [];
    const mermaidBlocks = [];   // 本次渲染收集到的 mermaid 源码，插入 DOM 后统一绘制

    // GitHub 风格的提示块：> [!TIP] / [!NOTE] / [!IMPORTANT] / [!WARNING] / [!CAUTION]。
    // 配色沿用墨色系，底色都是低透明度——整块高饱和的红在暗底上很刺眼。
    const ALERT_META = {
        NOTE:      { cls: "note",      label: "注意" },
        TIP:       { cls: "tip",       label: "提示" },
        IMPORTANT: { cls: "important", label: "重要" },
        WARNING:   { cls: "warning",   label: "警告" },
        CAUTION:   { cls: "caution",   label: "危险" },
    };

    function normalizeRel(p) {
        const out = [];
        for (const seg of String(p).split("/")) {
            if (!seg || seg === ".") continue;
            if (seg === "..") { if (!out.length) return null; out.pop(); continue; }
            out.push(seg);
        }
        return out.join("/");
    }

    // 把笔记里的 src 换算成能取到图的 URL
    function resolveImgSrc(src) {
        const s = String(src || "").trim();
        if (!s) return "";
        if (/^(?:https?:|data:|blob:)/i.test(s)) return s;   // 外链/内联，原样
        if (s.startsWith("//")) return s;
        const base = s.startsWith("/") ? s.slice(1) : (noteDir ? noteDir + "/" + s : s);
        const rel = normalizeRel(base);
        if (!rel) return "";
        return API + "/api/notes/asset?path=" + encodeURIComponent(rel);
    }

    // 只认这几个属性；其余（尤其是 on*）一律丢弃
    function buildImg(attrs) {
        const src = resolveImgSrc(attrs.src);
        if (!src) {
            return '<span class="rev-img-bad">[图片路径无法解析' +
                (attrs.src ? "：" + esc(attrs.src) : "") + ']</span>';
        }
        let style = "";
        const w = (attrs.style || "").match(/(?:^|;)\\s*width\\s*:\\s*([^;]+)/i);
        const wv = ((w && w[1]) || attrs.width || "").trim();
        if (SAFE_LEN.test(wv)) style = ' style="width:' + wv + '"';
        return '<img class="rev-img" src="' + esc(src) + '"' + style
            + ' alt="' + esc(attrs.alt || "") + '"'
            + (attrs.title ? ' title="' + esc(attrs.title) + '"' : "")
            + ' loading="lazy" decoding="async">';
    }

    function parseAttrs(raw) {
        const o = {};
        const re = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)\\s*=\\s*("[^"]*"|'[^']*'|[^\\s"'>]+)/g;
        let m;
        while ((m = re.exec(raw))) {
            let v = m[2];
            if (v.length > 1 && (v[0] === '"' || v[0] === "'") && v.slice(-1) === v[0]) v = v.slice(1, -1);
            o[m[1].toLowerCase()] = v;
        }
        return o;
    }

    function mdRender(src) {
        const maths = [], codes = [];
        let headingSeq = 0;
        imgSpecs.length = 0;
        mermaidBlocks.length = 0;
        let t = String(src);
        t = t.replace(/```[\\s\\S]*?```/g, m => {
            codes.push(m); return "@@CODE" + (codes.length - 1) + "@@";
        });
        t = t.replace(/!\\[([^\\]]*)\\]\\(\\s*([^)\\s]+)(?:\\s+"[^"]*")?\\s*\\)/g, (m, alt, s) => {
            imgSpecs.push({ src: s, alt: alt });
            return "@@IMG" + (imgSpecs.length - 1) + "@@";
        });
        t = t.replace(/<img\\b[^>]*>/gi, m => {
            imgSpecs.push(parseAttrs(m));
            return "@@IMG" + (imgSpecs.length - 1) + "@@";
        });
        // 公式抽取统一走 maskMath（= splitMath，与闪卡卡片、AI 回复共用同一套）：
        // 既认 $..$ / $$..$$，也认 \\[..\\] / \\(..\\)，还认**没有定界的裸 LaTeX**。
        // ️ 它是前一个 IIFE 挂在 window 上的；万一桥没搭上（脚本被裁/顺序变了），
        //    退回这里原来的两条正则 —— 笔记照常能看，只是少了裸 LaTeX 那点能力。
        if (typeof maskMath === "function") {
            t = maskMath(t, maths);
        } else {
            t = t.replace(/\\$\\$([\\s\\S]+?)\\$\\$/g, (m, tex) => {
                maths.push({ tex: tex.trim(), display: true });
                return "@@MATH" + (maths.length - 1) + "@@";
            });
            // 行内公式用 Pandoc 惯例：开 $ 之后、闭 $ 之前都不许是空白。
            // 否则「花了 $100 … 又花了 $200」这种两个美元符号的句子会被误配对成公式。
            t = t.replace(/(^|[^\\\\$])\\$(\\S(?:[^\\n$]*\\S)?)\\$/g, (m, pre, tex) => {
                maths.push({ tex: tex.trim(), display: false });
                return pre + "@@MATH" + (maths.length - 1) + "@@";
            });
        }
        t = t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        const out = [];
        let inCode = false, inTable = false;
        let inQuote = false, quoteAlert = "", quoteLines = [];
        t.split(/\\r?\\n/).forEach(line => {
            const cm = line.match(/^@@CODE(\\d+)@@$/);
            if (cm) {
                const whole = codes[+cm[1]];
                // 信息串只取围栏后面紧挨着的那串词，不碰换行符。这个文件是 Python
                // 普通字符串，正则里的换行转义极易踩坑（写少了会变成真换行把 JS 拆断），
                // 能绕开就绕开。
                const im = whole.match(/^`{3}([a-zA-Z0-9_-]*)/);
                const info = im ? im[1].toLowerCase() : "";
                const raw = whole.replace(/^```[^\\n]*\\n?/, "").replace(/```$/, "");
                if (info === "mermaid") {
                    // 先把源码摆出来，等 mermaid 就绪再换成 SVG。加载失败或语法有误
                    // 就保持原样，至少不会变成一片空白。
                    mermaidBlocks.push(raw);
                    out.push('<div class="mermaid-box" data-mid="' + (mermaidBlocks.length - 1) + '">'
                        + '<pre><code>' + esc(raw) + '</code></pre></div>');
                } else {
                    out.push("<pre><code>" + esc(raw) + "</code></pre>");
                }
                return;
            }
            if (/^```/.test(line)) {
                if (inCode) { out.push("</code></pre>"); inCode = false; }
                else { out.push('<pre><code>'); inCode = true; }
                return;
            }
            if (inCode) { out.push(line); return; }
            // ⚠️ 行首的 > 在这之前已经被转义成 &gt; 了（转义在行循环之前统一做），
            // 所以这里必须匹配转义后的形式，否则引用块永远进不来。
            const qm = line.match(/^&gt;\s?(.*)$/);
            if (qm) {
                if (!inQuote) { inQuote = true; quoteAlert = ""; quoteLines = []; }
                const c = qm[1];
                // 提示块的首行是 > [!TIP]（GitHub 还允许在后面补一句自定义标题）
                const am = c.match(/^\[!([A-Za-z]+)\]\s*(.*)$/);
                if (am && !quoteLines.length) {
                    // 全库 56 个标记里有 22 个是连着写两遍的（> [!NOTE] 下一行又是
                    // > [!NOTE]）。第二行当重复丢掉，否则会在提示框正文里显示成
                    // 一行字面量 [!NOTE]。
                    if (!quoteAlert) {
                        quoteAlert = am[1].toUpperCase();
                        if (am[2].trim()) quoteLines.push("**" + am[2].trim() + "**");
                    }
                } else {
                    quoteLines.push(c);
                }
                return;
            }
            flushQuote();
            if (/^\|.*\|\s*$/.test(line)) {
                if (/^\|[-:\s|]+\|\s*$/.test(line)) return;
                if (!inTable) { out.push("<table>"); inTable = true; }
                const cells = line.trim().replace(/^\||\|$/g, "").split("|");
                out.push("<tr>" + cells.map(c => "<td>" + inline(c.trim()) + "</td>").join("") + "</tr>");
                return;
            }
            if (inTable) { out.push("</table>"); inTable = false; }
            let m;
            if ((m = line.match(/^(#{1,6})\s+(.*)/))) {
                // 加 id 作为大纲锚点（全屏阅读的侧边 outline 要跳转到这里）。
                // 注意是 {1,6}：笔记里用 ###### 标「图 7.1」这类图注，只认到 #### 的话
                // 六级标题会掉进普通段落分支，整行带着井号原样显示出来。
                const lv = Math.min(3, m[1].length);
                out.push('<h' + lv + ' id="md-h' + (headingSeq++) + '">' + inline(m[2]) + '</h' + lv + '>');
            } else if (/^---+\s*$/.test(line)) {
                out.push("<hr>");
            } else if (/^\s*[-*]\s+/.test(line)) {
                out.push("<div>• " + inline(line.replace(/^\s*[-*]\s+/, "")) + "</div>");
            } else if (line.trim() === "") {
                out.push("<br>");
            } else {
                out.push("<div>" + inline(line) + "</div>");
            }
        });
        if (inTable) out.push("</table>");
        if (inCode) out.push("</code></pre>");
        flushQuote();
        return out.join("\\n");
        // 引用块收尾：> [!TIP] 变成带标题的提示框，其余普通引用变成左侧竖线的引文块。
        // 引用里的表格（笔记里 29 行）也一并还原，否则会显示成一堆竖线。
        function flushQuote() {
            if (!inQuote) return;
            // 嵌套引用（> > 内容）再剥一层，否则内层的 > 会当成正文显示出来
            const rows = quoteLines.map(x => x.replace(/^(?:&gt;\s?)+/, "").trim()).filter(x => x !== "");
            const alert = quoteAlert;
            inQuote = false; quoteAlert = ""; quoteLines = [];
            if (!rows.length && !alert) return;
            const parts = [];
            let tbl = false;
            rows.forEach(x => {
                if (/^\|.*\|$/.test(x)) {
                    if (/^\|[-:\s|]+$/.test(x)) return;          // 表头下那条分隔线
                    if (!tbl) { parts.push('<table class="rev-quote-table">'); tbl = true; }
                    const cells = x.replace(/^\||\|$/g, "").split("|");
                    parts.push("<tr>" + cells.map(c => "<td>" + inline(c.trim()) + "</td>").join("") + "</tr>");
                    return;
                }
                if (tbl) { parts.push("</table>"); tbl = false; }
                parts.push("<div>" + inline(x) + "</div>");
            });
            if (tbl) parts.push("</table>");
            const html = parts.join("");
            const meta = ALERT_META[alert];
            out.push(meta
                ? '<div class="rev-alert ' + meta.cls + '"><div class="rev-alert-head">'
                  + meta.label + "</div>" + html + "</div>"
                : '<div class="rev-quote">' + html + "</div>");
        }

        function inline(x) {
            // ⚠️ ASCII 上下标（O(n^2)、∬_D、a_i）必须**排在最前面**处理：
            //    此时 @@MATH / @@IMG 还是占位符，不会被误伤；等 @@MATH 换成 KaTeX 之后再跑，
            //    未就绪时的 tex-fallback 里就是公式源码，会被二次加工。
            //    规则与闪卡、AI 回复共用同一份（前一个 IIFE 挂在 window 上）；
            //    桥没搭上（脚本被裁/顺序变了）就跳过，笔记照常能看。
            const codes = [];
            const SOH = String.fromCharCode(1);
            // 行内代码先摘出来：代码里的 _ ^ 是程序文本，不该变成上下标
            let y = x.replace(/`([^`]+)`/g, (m, c) => {
                codes.push(c);
                return SOH + (codes.length - 1) + SOH;
            });
            y = asciiMath(y);
            return y
                .replace(/@@IMG(\\d+)@@/g, (m, i) => buildImg(imgSpecs[+i] || {}))
                .replace(/\\*\\*([^*]+)\\*\\*/g, "<b>$1</b>")
                .replace(/!\[([^\]]*)\]\([^)]+\)/g, "[图:$1]")
                .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
                .replace(/@@MATH(\d+)@@/g, (m, i) => katexHtml(maths[+i].tex, maths[+i].display))
                .replace(new RegExp(SOH + "([0-9]+)" + SOH, "g"),
                         (m, i) => "<code>" + codes[+i] + "</code>");
        }
    }

    // ---- 阅读弹窗 ----
    let modal = null;
    function openNote(t, btn) {
        if (modal) modal.remove();
        modal = el("div", { className: "rev-modal" });
        const box = el("div", { className: "rev-modal-box" }, modal);
        const head = el("div", { className: "rev-modal-head" }, box);
        el("span", { className: "rev-modal-title", textContent: t.name }, head);
        // 「就问这段」：把右侧提问面板展开（面板本身由 NOTEQ_JS 挂上来）
        const qaBtn = el("button", { className: "rev-modal-tool", textContent: "💬 就问这段",
                                     title: "就我正在读的这一段问 AI" }, head);
        const fsBtn = el("button", { className: "rev-modal-tool", textContent: "⛶ 全屏",
            title: "全屏阅读（" + __keys.pretty(__keys.specOf("read.full")) + "）" }, head);
        const closeBtn = el("button", { className: "rev-modal-close", textContent: "✕" }, head);

        // 主区：左侧 outline（仅全屏时显示）+ 正文 + 右侧提问面板（可折叠）
        const main = el("div", { className: "rev-modal-main" }, box);
        const outline = el("aside", { className: "rev-outline" }, main);
        const body = el("div", { className: "rev-modal-body", innerHTML: "加载中…" }, main);
        const qaPane = el("aside", { className: "rev-qa", hidden: true }, main);

        // ---- 阅读位置 → 提问时的上下文 ----
        // 「我正在读的这一段」＝当前视口里那些块的文字（往上多留一点、往下只取到
        // 视口内），外加最后一个已经滚过顶部的标题当小节名。这样 AI 拿到的是
        // 「你此刻在看什么」，而不是整篇笔记（整篇太大，也会把重点冲淡）。
        function currentContext() {
            const top = body.getBoundingClientRect().top;
            const vh = body.clientHeight || 420;
            const picked = [];
            const kids = body.children;
            for (let i = 0; i < kids.length; i++) {
                const n = kids[i];
                if (!n.getBoundingClientRect) continue;
                const r = n.getBoundingClientRect();
                if (r.height === 0) continue;
                const rel = r.top - top;
                if (rel + r.height < -100) continue;      // 早滚过去了
                if (rel > vh * 0.92) break;               // 还没读到
                const s = (n.textContent || "").trim();
                if (s) picked.push(s);
            }
            let section = "";
            const hs = body.querySelectorAll("h1, h2, h3, h4");
            for (let i = 0; i < hs.length; i++) {
                if (hs[i].getBoundingClientRect().top - top <= 26) section = hs[i].textContent || "";
            }
            return { section: section, text: picked.join("\\n").slice(0, 2400) };
        }
        if (typeof globalThis.__noteQaMount === "function") {
            try {
                globalThis.__noteQaMount(qaPane, {
                    path: t.file, title: t.name, scroller: body, getContext: currentContext,
                });
            } catch (e) { console.error("[笔记提问] 面板挂载失败:", e); }
        }
        let qaOpen = false;
        qaBtn.addEventListener("click", () => {
            qaOpen = !qaOpen;
            qaPane.hidden = !qaOpen;
            box.classList.toggle("qa-open", qaOpen);
            qaBtn.textContent = qaOpen ? " 收起提问" : "💬 就问这段";
        });

        const foot = el("div", { className: "rev-modal-foot" }, box);
        const doneBtn = el("button", { className: "rev-btn primary", textContent: "读完 · 标记已盘活" }, foot);
        // 从搜索/提问入口打开时没有「盘活」按钮可标记，就不用显示这一行
        if (!btn) foot.hidden = true;
        document.body.appendChild(modal);

        // ---- 全屏阅读 ----
        // 用「铺满视口的沉浸式布局」而不是 Fullscreen API：后者会让 Esc 的语义
        // 变复杂（浏览器先退全屏、我们的 Esc 又要关弹窗，两级状态容易打架）。
        let isFull = false;
        function setFull(on) {
            isFull = on;
            box.classList.toggle("full", on);
            fsBtn.textContent = on ? "⤢ 退出全屏" : "⛶ 全屏";
            if (on) buildOutline();
        }
        fsBtn.addEventListener("click", () => setFull(!isFull));

        // ---- 侧边目录 ----
        function outlineItems() {
            return Array.from(outline.querySelectorAll(".rev-outline-item"));
        }
        function buildOutline() {
            const hs = body.querySelectorAll("h1, h2, h3");
            if (!hs.length) {
                outline.innerHTML = '<div class="rev-outline-empty">本篇没有小标题</div>';
                return;
            }
            outline.innerHTML = "";
            hs.forEach(h => {
                const a = document.createElement("div");
                a.className = "rev-outline-item lv" + h.tagName[1];
                a.textContent = h.textContent;
                a.title = h.textContent;
                a.dataset.hid = h.id;
                a.addEventListener("click", () => {
                    h.scrollIntoView({ behavior: "smooth", block: "start" });
                });
                outline.appendChild(a);
            });
            syncOutline();
        }
        // 滚动跟随：找出最后一个已经滚过正文顶部的标题
        function syncOutline() {
            if (!isFull) return;
            const items = outlineItems();
            if (!items.length) return;
            const bodyTop = body.getBoundingClientRect().top;
            const hs = body.querySelectorAll("h1, h2, h3");
            let cur = 0;
            hs.forEach((h, i) => {
                if (h.getBoundingClientRect().top - bodyTop <= 16) cur = i;
            });
            items.forEach((it, i) => it.classList.toggle("active", i === cur));
            const act = items[cur];
            if (act && act.scrollIntoView) {
                // 目录自身过长时，把当前项滚进可视区（block:nearest 不会乱跳）
                act.scrollIntoView({ block: "nearest" });
            }
        }
        body.addEventListener("scroll", () => { if (isFull) syncOutline(); });

        // ---- 键盘：Esc 先退全屏，再关弹窗；F 切换全屏 ----
        function onKey(e) {
            if (e.key === "Escape") {
                e.stopPropagation();
                if (isFull) setFull(false); else close();
            } else if (__keys.matches("read.full", e) && !/INPUT|TEXTAREA/.test(e.target.tagName)) {
                setFull(!isFull);
            }
        }
        document.addEventListener("keydown", onKey, true);

        const close = () => {
            document.removeEventListener("keydown", onKey, true);
            modal.remove();
            modal = null;
        };
        closeBtn.addEventListener("click", close);
        modal.addEventListener("click", e => { if (e.target === modal) close(); });

        // 先确保 KaTeX 就绪再渲染，公式才不会先以源码闪一下
        Promise.all([
            ensureKatex(),
            fetch(API + "/api/notes/preview?path=" + encodeURIComponent(t.file)).then(r => r.json()),
        ]).then(([katexOk, d]) => {
            if (!d.ok) throw new Error(d.error || "读取失败");
            // 笔记里的插图是相对笔记自己写的（./assets/x.png），渲染前先把它所在目录
            // 告诉 mdRender，否则浏览器会按页面位置去 src/ 底下找图。
            const cut = String(t.file || "").lastIndexOf("/");
            noteDir = cut >= 0 ? String(t.file).slice(0, cut) : "";
            body.innerHTML = mdRender(d.content);
            renderMermaidIn(body, mermaidBlocks.slice());
            // 从搜索进来（带了关键词/小标题）就定位到命中处——件、公式都渲染完再做，
            // 否则文本节点还会被 KaTeX 替换掉，定位就到不了。
            if ((t.query || t.anchor || t.keywords) && typeof globalThis.__noteHitLocate === "function") {
                try { globalThis.__noteHitLocate(body, { query: t.query || "", anchor: t.anchor || "", keywords: t.keywords || "" }); }
                catch (e) { console.error("[笔记] 定位命中处失败:", e); }
            }
            if (!katexOk) body.insertAdjacentHTML("afterbegin",
                '<div class="rev-error">公式渲染组件（KaTeX）未加载，公式暂以源码显示。'
                + '请确认 src/tools/katex/dist/ 存在（它由本地服务提供，见 serve.js 的 /tools/katex/ 路由），'
                + '且是通过本地服务（启动考研大盘.bat）访问本页。</div>');
            if (isFull) buildOutline();
        }).catch(e => {
            body.innerHTML = '<div class="rev-error">读取失败：' + esc(e.message) +
                '<br>请确认已通过「启动考研大盘.bat」启动本地服务。</div>';
            doneBtn.disabled = true;
        });

        doneBtn.addEventListener("click", () => {
            if (!btn) { close(); return; }
            doneBtn.disabled = true;
            fetch(API + "/api/notes/touch", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: t.file })
            }).then(r => r.json()).then(d => {
                if (!d.ok) throw new Error(d.error || "标记失败");
                btn.textContent = "已盘活"; btn.disabled = true; btn.classList.add("ok");
                close();
            }).catch(e => { doneBtn.disabled = false; alert("标记失败：" + e.message); });
        });
    }

    // ---- 一键盘活出题 ----
    const jobTimers = {};
    function genCards(prefix, btn) {
        btn.disabled = true; btn.textContent = "提交中…";
        fetch(API + "/api/targeted-cards/generate", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prefixes: [prefix], count: 8 })
        }).then(r => r.json()).then(d => {
            if (!d.ok) throw new Error(d.error || "提交失败");
            btn.textContent = "生成中…";
            pollJob(d.job_id, btn);
        }).catch(e => {
            btn.disabled = false; btn.textContent = "盘活出题";
            showRevError(e.message + "（请确认本地服务已启动）");
        });
    }
    function pollJob(jobId, btn) {
        if (jobTimers[jobId]) return;
        jobTimers[jobId] = setInterval(() => {
            fetch(API + "/api/targeted-cards/status?id=" + encodeURIComponent(jobId)).then(r => r.json()).then(d => {
                if (!d.ok) return;
                if (d.status === "running") { btn.textContent = "生成中…"; return; }
                clearInterval(jobTimers[jobId]); delete jobTimers[jobId];
                if (d.status === "done") {
                    btn.textContent = "+" + d.inserted + "张卡"; btn.classList.add("ok");
                } else {
                    btn.disabled = false; btn.textContent = "盘活出题";
                    showRevError("生成失败：" + (d.error || "未知错误").slice(0, 200));
                }
            }).catch(() => {});
        }, 4000);
    }
    function showRevError(msg) {
        const tipEl = document.getElementById("rev-tip");
        let errEl = document.getElementById("rev-error");
        if (!errEl) { errEl = el("div", { className: "rev-error", id: "rev-error" }, tipEl ? tipEl.parentNode : document.body); }
        errEl.textContent = "⚠ " + msg;
    }

    // 搜索页要「点一下就打开这篇笔记」——把阅读器开出去（第二个参数是盘活按钮，
    // 从搜索进来时没有，阅读器内部会自己处理）。
    globalThis.__revOpenNote = function (opts) {
        const file = typeof opts === "string" ? opts : (opts && opts.file);
        const name = (typeof opts === "object" && opts && opts.name) || String(file || "").split("/").pop();
        const anchor = (typeof opts === "object" && opts && opts.anchor) || "";
        const query = (typeof opts === "object" && opts && opts.query) || "";
        const keywords = (typeof opts === "object" && opts && opts.keywords) || "";
        if (!file) return false;
        openNote({ file: file, name: name, anchor: anchor, query: query, keywords: keywords }, null);
        return true;
    };
})();

// ============================================================
'''


NAV_CSS = '''
        /* ============================================================
           分栏导航（2026-09-13）：左侧固定导航 + 右侧内容区，点一项切一页。
           侧栏沿用笔记弹窗全屏 outline 那套（定宽 + sticky + 左描边高亮）。
           ============================================================ */
        .dashboard { max-width: none; display: flex; gap: 24px; align-items: flex-start; }
        .sidenav { width: 190px; flex: none; position: sticky; top: 20px; display: flex; flex-direction: column;
                   gap: 3px; background: var(--bg-card); border: 1px solid var(--border-color);
                   border-radius: var(--border-radius); padding: 12px;
                   transition: width .2s ease, padding .2s ease; }
        .sidenav-brand { font-family: var(--font-serif); font-size: 0.92rem; letter-spacing: .16em; color: var(--xuan);
                         padding: 4px 12px 12px; border-bottom: var(--rule); margin-bottom: 8px;
                         display: flex; align-items: center; justify-content: space-between; gap: 6px; }
        .sidenav-toggle { font: inherit; font-size: 0.82rem; line-height: 1; flex: none; cursor: pointer;
                          background: none; border: 1px solid transparent; border-radius: 4px;
                          color: var(--text-muted); padding: 3px 6px; transition: all .15s; }
        .sidenav-toggle:hover { color: var(--text-primary); border-color: var(--border-color);
                                background: var(--bg-card-hover); }
        .sidenav-item { font: inherit; font-size: 0.86rem; text-align: left; cursor: pointer; padding: 9px 12px;
                        background: none; border: none; border-left: 3px solid transparent;
                        border-radius: var(--border-radius); color: var(--text-secondary); transition: all .14s; }
        .sidenav-item:hover { background: var(--bg-card-hover); color: var(--text-primary); }
        .sidenav-item.active { color: var(--xuan); border-left-color: var(--zhusha); background: rgba(var(--zhusha-rgb),.12); }
        /* 收起态：整条变窄、文字换首字。宽度过渡挂在 .sidenav 上，不是这里的 font-size */
        .sidenav.collapsed { width: 58px; padding: 12px 7px; }
        .sidenav.collapsed .sidenav-brand { justify-content: center; padding: 4px 0 12px; }
        .sidenav.collapsed .sidenav-brand-text { display: none; }
        .sidenav.collapsed .sidenav-item { font-size: 0; text-align: center; padding: 9px 0; }
        .sidenav.collapsed .sidenav-item::before { content: attr(data-short); font-size: 0.86rem; }
        .dash-main { flex: 1; min-width: 0; max-width: 1200px; }
        .page[hidden] { display: none; }
        /* 窄屏：侧栏塌成顶部横向 tab，避免占掉半屏宽 */
        @media (max-width: 768px) {
            .dashboard { display: block; }
            .sidenav { width: auto; position: sticky; top: 0; z-index: 20; flex-direction: row; gap: 6px;
                       overflow-x: auto; padding: 8px; margin-bottom: 16px; }
            .sidenav-brand { display: none; }
            .sidenav-item { white-space: nowrap; border-left: none; border-bottom: 3px solid transparent; padding: 6px 12px; }
            .sidenav-item.active { border-left: none; border-bottom-color: var(--zhusha); }
            /* 横向 tab 下没有「收起」的概念，按钮隐藏，
               同时忽略 collapsed——否则会留下 font-size:0 的空白 tab */
            .sidenav-toggle { display: none; }
            .sidenav.collapsed { width: auto; padding: 8px; }
            .sidenav.collapsed .sidenav-item { font-size: 0.86rem; padding: 6px 12px; }
            .sidenav.collapsed .sidenav-item::before { content: none; }
            .dash-main { max-width: none; }
        }
'''

FX_CSS = '''
        /* ============================================================
           统一微交互（2026-09-14）：可点/可悬停的表面共用同一套节奏。
           此前反馈是散的——有的元素有过渡、有的一按下去毫无动静，
           这里集中补齐，省得以后每个模块再各写各的。
           ============================================================ */
        /* --- 子页入场（2026-09-22）---
           之前切子页是「点一下立刻硬切」。NAV_JS 靠 toggling .page[hidden] 换页，
           display 一重建（none→block）挂在上面的 animation 就会重头跑，所以只要给
           可见页挂一条淡入即可，所有子页统一有进场动效，不再只有总览那几张图在动。
           只动画 opacity、不碰 transform：transform 会给页面建新的包含块，把设置页里
           position:fixed 的浮窗（快捷键 / 闪卡筛选）锚到页面而非视口，浮窗会铺不满。 */
        @keyframes pageIn { from { opacity: 0; } to { opacity: 1; } }
        .page:not([hidden]) { animation: pageIn .26s ease-out; }

        .metric-card, .weak-item, .gap-list li, .deck-card,
        .fs-opt, .fs-btn, .heatmap-cell, .range-btn, .sidenav-item {
            transition: transform .16s ease, border-color .16s ease,
                        background .16s ease, box-shadow .16s ease, color .16s ease;
        }

        .metric-card:hover { transform: translateY(-2px); border-color: var(--dianqing); }
        .metric-card:active { transform: translateY(0) scale(.995); }

        .weak-item:hover, .gap-list li:hover { transform: translateX(3px); }

        /* 热力图格子放大时要盖住相邻格，所以必须带 z-index。
           .heatmap-cell 本身已是 position:relative，不用重复声明。 */
        .heatmap-cell:hover { transform: scale(1.1); z-index: 3; box-shadow: 0 3px 10px rgba(0,0,0,.35); }

        .fs-opt:hover:not(:disabled) { transform: translateX(3px); }
        .fs-btn:active, .range-btn:active { transform: scale(.95); }
        .deck-card:active { transform: translateY(0) scale(.995); }

        /* --- 时间线悬停：十字准线 + 整列读数浮层 --- */
        .chart-container { position: relative; }
        .tl-guide { stroke: var(--text-muted); stroke-width: 1; stroke-dasharray: 3 3; opacity: .75; }
        .tl-halo { stroke: var(--bg-card); stroke-width: 2; }
        .tl-tip {
            position: absolute; pointer-events: none; z-index: 5;
            background: rgba(var(--mo-rgb), .97); border: 1px solid var(--border-color);
            border-radius: 6px; padding: 7px 10px; font-size: 0.72rem;
            color: var(--text-primary); white-space: nowrap;
            box-shadow: 0 4px 14px rgba(0,0,0,.4);
        }
        .tl-tip-head { color: var(--text-secondary); font-size: 0.7rem; margin-bottom: 4px; }
        .tl-tip-row { display: flex; align-items: center; gap: 6px; line-height: 1.7; }
        .tl-tip-row i { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
        .tl-tip-row b { margin-left: auto; padding-left: 12px; }

        /* 对动效敏感的人：位移/缩放全关，只保留颜色变化 */
        @media (prefers-reduced-motion: reduce) {
            .page:not([hidden]) { animation: none !important; }
            .metric-card, .weak-item, .gap-list li, .deck-card, .fs-opt,
            .fs-btn, .heatmap-cell, .range-btn, .sidenav-item, .sidenav,
            .section.is-full, .fs-full-toggle {
                transition: none !important;
            }
            .metric-card:hover, .weak-item:hover, .gap-list li:hover, .heatmap-cell:hover,
            .fs-opt:hover:not(:disabled), .fs-btn:active,
            .range-btn:active, .metric-card:active, .deck-card:active {
                transform: none !important;
            }
        }
'''

NAV_JS = '''
// ============================================================
// 分栏导航：hash 路由 + 首次进入某页才渲染该页图表（懒渲染）。
//
// 为什么必须懒渲染：D3 那几个图都读 container.clientWidth 定宽度，而
// display:none 的容器 clientWidth 恒为 0，画出来就是一片空白。所以凡是碰
// clientWidth 的渲染器都注册到 window.__pageRenderers，等那页真可见了再跑。
// ============================================================
(function () {
    const PAGES = ["overview", "notes", "flash", "activity", "mistakes", "study", "review", "settings"];
    const DEFAULT_PAGE = "overview";
    let current = null;

    function pageFromHash() {
        const h = (location.hash || "").replace(/^#\\/?/, "");
        return PAGES.indexOf(h) >= 0 ? h : DEFAULT_PAGE;
    }

    function runRenderers(name) {
        const list = (window.__pageRenderers && window.__pageRenderers[name]) || [];
        list.forEach(fn => {
            if (fn.__done) return;
            try { fn(); fn.__done = true; }
            catch (e) { console.error("[nav] 渲染 " + name + " 失败:", e); }
        });
    }

    function activate(name) {
        if (current === name) return;
        current = name;
        document.querySelectorAll(".page").forEach(p => { p.hidden = (p.dataset.page !== name); });
        document.querySelectorAll(".sidenav-item").forEach(b => {
            b.classList.toggle("active", b.dataset.page === name);
        });
        runRenderers(name);
    }

    // —— 点侧栏切换 ——
    // ⚠️ 2026-09-13 修复：按钮是 <button class="sidenav-item" data-page="...">，
    //    既没有 href 也没有 onclick，而这里原来**只**监听 hashchange，
    //    所以点「总览 / 笔记盘活 / 闪卡 / 练习活动」完全没反应。
    //    现在显式绑定点按；走法仍是「改 hash → hashchange → activate」，
    //    保留浏览器前进/后退；hash 没变时（重复点当前项）hashchange 不触发，直接 activate。
    function go(name) {
        if (PAGES.indexOf(name) < 0) return;
        if (name === pageFromHash()) activate(name);
        else location.hash = "#/" + name;
    }
    document.querySelectorAll(".sidenav-item").forEach(btn => {
        btn.addEventListener("click", () => go(btn.dataset.page));
    });

    // —— 侧栏收展 ——
    // 状态存 localStorage，刷新后保持。收起后文字换成首字（由 CSS 的
    // data-short 生成），窄屏下按钮被 CSS 隐藏、collapsed 也不再生效。
    const SIDENAV_KEY = "kaoyan.sidenav.collapsed";
    const sidenav = document.getElementById("sidenav");
    const navToggle = document.getElementById("sidenav-toggle");

    function applyNavCollapsed(collapsed) {
        if (!sidenav) return;
        sidenav.classList.toggle("collapsed", collapsed);
        if (navToggle) {
            const label = collapsed ? "展开侧边栏" : "收起侧边栏";
            navToggle.textContent = collapsed ? "▶" : "◀";
            navToggle.title = label;
            navToggle.setAttribute("aria-label", label);
            navToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
        }
    }

    let navCollapsed = false;
    // 无痕/禁用存储时 localStorage 会直接抛异常，不能让它带崩整个导航
    try { navCollapsed = localStorage.getItem(SIDENAV_KEY) === "1"; } catch (e) {}
    applyNavCollapsed(navCollapsed);

    if (navToggle) {
        navToggle.addEventListener("click", () => {
            navCollapsed = !navCollapsed;
            applyNavCollapsed(navCollapsed);
            try { localStorage.setItem(SIDENAV_KEY, navCollapsed ? "1" : "0"); } catch (e) {}
        });
    }

    // 早间回顾（review 页）的渲染器由 MR_JS 自行注册到 window.__pageRenderers.review，
    // 且 MR_JS 必须注入在本文件之前——本文件末尾会立刻 activate() 一次。

    window.addEventListener("hashchange", () => activate(pageFromHash()));
    activate(pageFromHash());
})();
'''

TASK_CSS = '''
        /* --- 今日任务（2026-09-14）--- */
        .task-progress { font-size: 0.8rem; color: var(--text-muted); font-weight: 400; margin-left: 6px; }
        .task-day { font-size: 0.75rem; color: var(--text-muted); flex: none; }
        .task-list { list-style: none; margin: 0 0 12px; padding: 0; }
        .task-item { display: flex; align-items: flex-start; gap: 9px; padding: 8px 10px;
            border-radius: 6px; transition: background .14s, transform .14s; }
        .task-item:hover { background: var(--bg-card-hover); transform: translateX(2px); }
        .task-item.done .task-text { color: var(--text-muted); text-decoration: line-through; }
        .task-check { flex: none; width: 16px; height: 16px; margin-top: 2px; cursor: pointer;
            accent-color: var(--dianqing); }
        .task-text { flex: 1; min-width: 0; font-size: 0.86rem; line-height: 1.6;
            color: var(--text-primary); word-break: break-word; }
        .task-tag { flex: none; font-size: 0.68rem; padding: 1px 7px; border-radius: 9px;
            border: 1px solid var(--border-color); color: var(--text-muted); }
        .task-tag.ai { color: var(--zhuqing-lt); border-color: rgba(var(--zhuqing-rgb),.45);
            background: rgba(var(--zhuqing-rgb),.12); }
        .task-tag.me { color: var(--xiang-lt); border-color: rgba(var(--xiang-rgb),.45);
            background: rgba(var(--xiang-rgb),.12); }
        .task-del { flex: none; background: none; border: 0; cursor: pointer; font-size: 0.92rem;
            color: var(--text-muted); padding: 0 4px; line-height: 1.4; opacity: .4;
            transition: opacity .14s, color .14s; }
        .task-item:hover .task-del { opacity: 1; }
        .task-del:hover { color: var(--zhusha-lt); }
        .task-add { display: flex; gap: 8px; flex-wrap: wrap; }
        .task-input { flex: 1; min-width: 180px; box-sizing: border-box; font-family: inherit;
            font-size: 0.85rem; padding: 8px 11px; background: var(--bg-primary);
            color: var(--text-primary); border: 1px solid var(--border-color); border-radius: 6px; }
        .task-input:focus { outline: none; border-color: var(--dianqing); }
        .task-subject { font-family: inherit; font-size: 0.82rem; padding: 8px; flex: none;
            background: var(--bg-primary); color: var(--text-secondary);
            border: 1px solid var(--border-color); border-radius: 6px; }
        .task-hint { margin-top: 10px; font-size: 0.72rem; color: var(--text-muted); line-height: 1.6; }
        .task-empty { color: var(--text-muted); font-size: 0.84rem; padding: 16px 0; text-align: center; }
'''

TASK_JS = '''
// ============================================================
// 今日任务（2026-09-14）
//
// 数据来自 serve.js 的 /api/tasks*，表 daily_tasks 由 migrate.js 幂等建出。
// agent 每天 00:00 用 daily_tasks.py 直写库；这里的增删改打 HTTP 接口。
// 用户自加与被删的任务都会留痕（软删除），agent 据此调整后续布置——
// 所以「删掉」是软删除，不是真删。
// ============================================================
(function () {
    // API 基址：用当前页面 origin，平板/手机经局域网访问时才能正常调接口；
    // 本地以 file:// 直开时回落到 localhost:8080
    const API = location.protocol.startsWith('http') ? location.origin : "http://localhost:8080";
    const box = document.getElementById("task-list");
    if (!box) return;                     // 容器缺失（页面结构变了）就别往下走
    const progressEl = document.getElementById("task-progress");
    const hintEl = document.getElementById("task-hint");
    const inputEl = document.getElementById("task-input");
    const selEl = document.getElementById("task-subject");
    const addBtn = document.getElementById("task-add-btn");
    const dayEl = document.getElementById("task-day");
    let busy = false;

    // 本地日期。禁止 toISOString()——那是 UTC，UTC+8 凌晨会差一天。
    // 走 studyDay()：凌晨 4 点前算前一天，跟服务端 localToday() 同一套规则。
    function todayStr() { return studyDay(); }

    function toast(msg) {
        const t = document.createElement("div");
        t.className = "fs-toast";
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 1800);
    }

    async function api(path, method, payload) {
        const opt = { method: method || "GET" };
        if (payload) {
            opt.headers = { "Content-Type": "application/json" };
            opt.body = JSON.stringify(payload);
        }
        const r = await fetch(API + path, opt);
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || ("HTTP " + r.status));
        return d;
    }

    function renderTask(t) {
        const li = el("li", { className: "task-item" + (t.done ? " done" : "") }, box);
        const cb = el("input", { className: "task-check", type: "checkbox" }, li);
        cb.checked = !!t.done;
        cb.onchange = async () => {
            cb.disabled = true;
            try {
                await api("/api/tasks/done", "POST", { id: t.id, done: cb.checked });
                t.done = cb.checked;
                li.classList.toggle("done", !!t.done);
                updateProgress();
            } catch (e) {
                cb.checked = !cb.checked;      // 回滚，别让界面撒谎
                toast("保存失败：" + e.message);
            } finally { cb.disabled = false; }
        };
        // 任务文本一律 textContent（agent/用户都可能写入任意字符）
        el("div", { className: "task-text", textContent: t.text }, li);
        if (t.subject) el("span", { className: "task-tag", textContent: t.subject }, li);
        el("span", {
            className: "task-tag " + (t.source === "user" ? "me" : "ai"),
            textContent: t.source === "user" ? "我加的" : "AI 布置",
        }, li);
        const del = el("button", { className: "task-del", textContent: "×", title: "删除" }, li);
        del.onclick = async () => {
            if (!confirm("删除这条任务？\\n\\n" + t.text)) return;
            try {
                await api("/api/tasks/delete", "POST", { id: t.id });
                li.remove();
                updateProgress();
            } catch (e) { toast("删除失败：" + e.message); }
        };
        return li;
    }

    let tasks = [];
    function updateProgress() {
        const live = tasks.filter(t => !t.deleted);
        const done = live.filter(t => t.done).length;
        progressEl.textContent = live.length ? (done + " / " + live.length) : "";
    }

    function render(d) {
        tasks = d.tasks || [];
        box.innerHTML = "";
        const live = tasks.filter(t => !t.deleted);
        if (!live.length) {
            el("li", {
                className: "task-empty",
                textContent: "今天还没有任务。等一下 AI 布置，或在下面自己加一条。",
            }, box);
            updateProgress();
            return;
        }
        // 未完成的排前面：打开页面先看到该做的事
        live.sort((a, b) => (a.done - b.done) || (a.id - b.id));
        live.forEach(renderTask);
        updateProgress();
    }

    async function load() {
        dayEl.textContent = todayStr();
        try {
            render(await api("/api/tasks?date=" + todayStr()));
            hintEl.textContent = "";
        } catch (e) {
            box.innerHTML = "";
            el("li", { className: "task-empty", textContent: "读取失败：" + e.message }, box);
            hintEl.textContent = "任务接口需要本地服务在运行——用桌面「考研大盘」快捷方式启动即可。";
        }
    }

    async function addTask() {
        const text = inputEl.value.trim();
        if (!text || busy) return;
        busy = true;
        addBtn.disabled = true;
        try {
            await api("/api/tasks/add", "POST", {
                date: todayStr(), text: text, subject: selEl.value || null,
            });
            inputEl.value = "";
            await load();
        } catch (e) {
            toast("添加失败：" + e.message);
        } finally {
            busy = false;
            addBtn.disabled = false;
            inputEl.focus();
        }
    }

    addBtn.onclick = addTask;
    inputEl.onkeydown = (e) => { if (e.key === "Enter" && !(e.isComposing || e.keyCode === 229)) { e.preventDefault(); addTask(); } };

    // 懒加载：切到「活动」页才拉取（runRenderers 的 __done 守卫保证只跑一次）。
    // 用 push 而不是赋值，避免覆盖别人给 activity 注册的渲染器。
    (window.__pageRenderers = window.__pageRenderers || {});
    (window.__pageRenderers.activity = window.__pageRenderers.activity || []).push(load);
})();
'''

THEME_CSS = '''
        /* ============================================================
           浅色主题 + 自定义背景（2026-09-14）

           浅色只覆盖**基础色组**，语义别名（--bg-card / --text-primary / …）
           自动跟着变，不必逐条重写。设计语汇保持一致：深色是「青墨底·月白字」，
           浅色就是「纸白底·青墨字」——同一套新中式，不是另一套配色。
           ============================================================ */
        [data-theme="light"] {
            --mo:        #F4F7F5;   /* 纸白    — 页面底 */
            --mo-2:      #E9EFEC;   /* 次纸白  — 次级底 */
            --mo-light:  #FFFFFF;   /* 卡片 */
            --mo-hover:  #E3EAE6;
            --xuan:      #1B2422;   /* 主文字：青墨 */
            --tao:       #4B5754;
            --hui:       #7C8885;
            --zhusha:    #A83A32;
            --zhuqing:   #3F7261;
            --dianqing:  #35597D;
            --xiang:     #96702A;
            --zi:        #664D82;
            --bian:      #D6DEDA;

            /* 深色下 -lt 是「在暗底上提亮」，浅底上要反过来压暗才有对比度 */
            --zhusha-lt:   #8E2F28;
            --zhuqing-lt:  #2F5A4C;
            --dianqing-lt: #274868;
            --xiang-lt:    #7A5A1F;
            --zi-lt:       #523D69;

            --zhusha-rgb:   168,58,50;
            --zhuqing-rgb:  63,114,97;
            --dianqing-rgb: 53,89,125;
            --xiang-rgb:    150,112,42;
            --zi-rgb:       102,77,130;
            --xuan-rgb:     27,36,34;
            --mo-rgb:       244,247,245;
        }
        /* 几处硬编码的亮色在浅底上对比不足，单独压一下 */
        [data-theme="light"] .fs-verdict.ok { color: #2E7D5B; }
        [data-theme="light"] .fs-verdict.bad { color: #B3261E; }
        [data-theme="light"] .heatmap-cell:hover { box-shadow: 0 3px 10px rgba(0,0,0,.18); }
        [data-theme="light"] .tl-tip { box-shadow: 0 4px 14px rgba(0,0,0,.16); }
        [data-theme="light"] .fs-toast { box-shadow: 0 4px 14px rgba(0,0,0,.16); }
        [data-theme="light"] .heatmap-cell.is-note-only { box-shadow: inset 0 0 0 1px rgba(27,36,34,.35); }

        /* ---- 自定义背景图 ----
           独立一个固定层放图：z-index:-1 让它落在内容之下、body 背景之上。
           上面再叠一层与主题同色的遮罩保证文字可读，透明度由设置页调节。 */
        #bg-layer {
            position: fixed; inset: 0; z-index: -1; display: none;
            background-size: cover; background-position: center; background-attachment: fixed;
        }
        /* 遮罩透明度 = 1 - 用户设的「背景可见度」，由设置页写 --bg-opacity */
        #bg-layer::after {
            content: ""; position: absolute; inset: 0;
            background: var(--mo);
            opacity: calc(1 - var(--bg-opacity, 0.35));
        }
        body.has-bg #bg-layer { display: block; }
        /* 有背景图时卡片走毛玻璃，否则一大块不透明会把图完全盖住 */
        body.has-bg .section,
        body.has-bg .sidenav,
        body.has-bg .metric-card {
            backdrop-filter: blur(14px) saturate(1.15);
            -webkit-backdrop-filter: blur(14px) saturate(1.15);
            background: rgba(var(--mo-rgb), .72);
        }
'''

SETTINGS_CSS = '''
        /* --- 设置页（2026-09-14）--- */
        .set-group { margin-bottom: 22px; }
        .set-group:last-child { margin-bottom: 0; }
        .set-title { font-size: 0.86rem; color: var(--text-primary); margin-bottom: 4px;
            display: flex; align-items: center; gap: 8px; }
        /* --- 设置分组手风琴（2026-09-22）---
           设置项太多、全平铺会拖成一屏长卷。把每组收成一张卡片：平时全折叠只留
           标题行，一眼扫完有哪些分组，点到哪组再展开。foldGroups() 在渲染后统一包
           .set-body 并把标题变成可点表头，模板那一大段不用跟着改。 */
        .set-group.acc { border: 1px solid var(--border-color); border-radius: 10px;
            background: var(--bg-card); margin-bottom: 10px; overflow: hidden; }
        .set-group.acc:last-child { margin-bottom: 0; }
        .set-group.acc > .set-title { cursor: pointer; padding: 11px 14px; margin-bottom: 0;
            user-select: none; display: flex; align-items: center; gap: 8px;
            transition: background .14s ease; }
        .set-group.acc > .set-title:hover { background: var(--bg-card-hover); }
        .set-group.acc > .set-title::after { content: "▾"; margin-left: auto; flex: none;
            font-size: 0.8em; color: var(--text-muted); transition: transform .2s ease; }
        .set-group.acc.collapsed > .set-title::after { transform: rotate(-90deg); }
        .set-body { overflow: hidden; max-height: 2600px; opacity: 1; padding: 0 14px 14px;
            transition: max-height .3s ease, opacity .22s ease, padding .3s ease; }
        .set-group.acc.collapsed .set-body { max-height: 0; opacity: 0;
            padding-top: 0; padding-bottom: 0; }
        @media (prefers-reduced-motion: reduce) { .set-body { transition: none !important; } }
        .set-label { font-size: 0.76rem; color: var(--text-muted); display: block; margin: 10px 0 5px; }
        .set-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
        .set-input { flex: 1; min-width: 190px; box-sizing: border-box; font-family: inherit;
            font-size: 0.85rem; padding: 8px 11px; background: var(--bg-primary);
            color: var(--text-primary); border: 1px solid var(--border-color); border-radius: 6px; }
        .set-input:focus { outline: none; border-color: var(--dianqing); }
        .set-input.mono { font-family: Consolas, "Courier New", monospace; }
        /* 自己写提示词用的多行框（2026-09-21）：占满整行、可纵向拉伸 */
        .set-ta { flex: none; width: 100%; min-height: 96px; margin: 2px 0 8px; resize: vertical;
            line-height: 1.6; font-size: 0.84rem; }
        .set-num { width: 92px; flex: none; text-align: center; }
        .set-hint { margin-top: 9px; font-size: 0.72rem; color: var(--text-muted); line-height: 1.75; }
        .set-hint code { background: var(--bg-secondary); padding: 1px 5px; border-radius: 3px;
            font-family: Consolas, "Courier New", monospace; }
        /* 平板/手机连接卡片（2026-09-20）：局域网二维码 + 地址 */
        .set-lan { display: flex; gap: 18px; align-items: flex-start; flex-wrap: wrap; margin-top: 6px; }
        .set-qr { width: 150px; height: 150px; padding: 8px; background: #fff; border-radius: 8px; flex-shrink: 0; }
        .set-qr img { width: 100%; height: 100%; display: block; image-rendering: pixelated; }
        .set-lan-info { flex: 1; min-width: 200px; }
        .set-lan-url { font-family: Consolas, "Courier New", monospace; font-size: 0.82rem;
            background: var(--bg-secondary); padding: 6px 10px; border-radius: 4px; margin-bottom: 6px;
            word-break: break-all; color: var(--dianqing); cursor: pointer; user-select: all; }
        .set-lan-url:hover { background: var(--border-color); }
        .set-lan-tip { font-size: 0.72rem; color: var(--text-muted); line-height: 1.7; }
        .set-status { font-size: 0.74rem; }
        .set-status.ok { color: var(--zhuqing-lt); }
        .set-status.warn { color: var(--xiang-lt); }
        .set-seg { display: inline-flex; border: 1px solid var(--border-color); border-radius: 6px; overflow: hidden; }
        .set-seg button { font: inherit; font-size: 0.8rem; cursor: pointer; padding: 7px 20px;
            background: transparent; border: 0; color: var(--text-secondary); transition: all .15s; }
        .set-seg button:hover { color: var(--text-primary); background: var(--bg-secondary); }
        .set-seg button.on { background: var(--dianqing); color: #fff; }
        .set-range { flex: 1; min-width: 150px; accent-color: var(--dianqing); }
        .set-bg-preview { margin-top: 10px; width: 240px; height: 135px; border-radius: 8px;
            border: 1px solid var(--border-color); background-size: cover; background-position: center;
            display: none; }
        .set-bg-preview.on { display: block; }
        /* --- 番茄钟轮播背景（2026-09-21）--- */
        /* 缩略图用 grid 而不是 flex+wrap：换行时 flex 会按内容撑出不齐的行高，
           一排里混着横图竖图就参差了。固定列宽 + aspect-ratio 才是整齐的九宫格。 */
        .set-thumbs { display: grid; grid-template-columns: repeat(auto-fill, minmax(108px, 1fr));
            gap: 8px; margin-top: 10px; }
        .set-thumbs:empty { display: none; }
        .set-thumb { position: relative; aspect-ratio: 4 / 3; border-radius: 6px; overflow: hidden;
            border: 1px solid var(--border-color); background-size: cover; background-position: center;
            background-color: var(--bg-secondary); }
        .set-thumb-n { position: absolute; left: 4px; bottom: 2px; font-size: 0.62rem;
            color: var(--text-muted); text-shadow: 0 1px 3px rgba(var(--mo-rgb), .9); }
        .set-thumb-x { position: absolute; top: 2px; right: 2px; width: 20px; height: 20px;
            display: grid; place-items: center; cursor: pointer; border: 0; border-radius: 50%;
            background: rgba(var(--mo-rgb), .72); color: var(--xuan); font: inherit; font-size: 0.72rem;
            line-height: 1; padding: 0; opacity: 0; transition: opacity .12s; }
        .set-thumb:hover .set-thumb-x { opacity: 1; }
        .set-thumb-x:hover { background: var(--zhusha); }
        .set-thumb-empty { grid-column: 1 / -1; font-size: 0.72rem; color: var(--text-muted);
            border: 1px dashed var(--border-color); border-radius: 6px; padding: 14px 10px; text-align: center; }
        /* --- 快捷键表（2026-09-17）---
           一张按组排的表：左边动作名 + 说明，右边按键芯片。芯片就是「按钮」，
           点一下进录制态 —— 所以它得看着像能按（有边框、hover 变色），
           不然没人会想到去点它。 */
        .key-group { margin-top: 14px; }
        .key-group-name { font-size: 0.74rem; color: var(--text-secondary); margin-bottom: 2px;
            padding-bottom: 6px; border-bottom: 1px solid var(--border-color); }
        .key-row { display: flex; align-items: center; gap: 10px; padding: 8px 0;
            border-bottom: 1px dashed var(--border-color); }
        .key-row:last-child { border-bottom: 0; }
        .key-info { flex: 1; min-width: 0; }
        .key-name { font-size: 0.8rem; color: var(--text-primary); display: flex;
            align-items: center; gap: 8px; flex-wrap: wrap; }
        .key-alias { font-size: 0.66rem; color: var(--text-muted); border: 1px solid var(--border-color);
            border-radius: 3px; padding: 1px 5px; }
        .key-desc { font-size: 0.7rem; color: var(--text-muted); margin-top: 3px; line-height: 1.6; }
        .key-chip { font: inherit; font-family: Consolas, "Courier New", monospace; font-size: 0.76rem;
            flex-shrink: 0; min-width: 86px; text-align: center; padding: 5px 10px; cursor: pointer;
            background: var(--bg-secondary); color: var(--text-primary);
            border: 1px solid var(--border-color); border-radius: 5px; transition: all .15s; }
        .key-chip:hover { border-color: var(--dianqing); color: var(--dianqing); }
        /* 录制态：红框 + 呼吸，让人知道「现在正在等按键」 */
        .key-chip.rec { border-color: var(--zhusha); color: var(--zhusha-lt); background: transparent;
            animation: key-rec 1.1s ease-in-out infinite; }
        @keyframes key-rec { 0%, 100% { opacity: 1; } 50% { opacity: .45; } }
        /* 改过的键位标成缃色：扫一眼就知道哪些不是默认了 */
        .key-chip.custom { border-color: var(--xiang); color: var(--xiang-lt); }
        .key-chip.fixed { cursor: default; color: var(--text-muted); background: transparent;
            border-style: dashed; }
        .key-chip.fixed:hover { border-color: var(--border-color); color: var(--text-muted); }
        .key-mini { font: inherit; font-size: 0.76rem; flex-shrink: 0; width: 26px; height: 26px;
            line-height: 1; padding: 0; cursor: pointer; color: var(--text-secondary);
            background: transparent; border: 1px solid var(--border-color); border-radius: 5px;
            transition: all .15s; }
        .key-mini:hover:not(:disabled) { border-color: var(--dianqing); color: var(--dianqing); }
        .key-mini:disabled { opacity: .25; cursor: default; }
        /* 固定行没有「恢复默认」按钮，用等宽占位让两边的芯片对齐 */
        .key-mini-pad { flex-shrink: 0; width: 26px; }
        .key-foot { margin-top: 14px; }
        /* --- 快捷键设置浮窗（2026-09-18）---
           设置页平时收成一个「打开」按钮，整张键位表放进这个浮窗。
           结构与 rev-modal 同源，但故意用独立的 sk- 前缀——因为它的锚点
           是设置页（.page[data-page="settings"]）而不是 body，一离开该页
           整块一起藏掉，不会被其它 fixed 层互相挤压。 */
        .sk-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.55);
            -webkit-backdrop-filter: blur(14px) saturate(0.85); backdrop-filter: blur(14px) saturate(0.85);
            display: flex; align-items: center; justify-content: center; z-index: 100; }
        /* 用 hidden 属性隐藏浮窗时，必须显式盖回 display:none——.sk-overlay 上面的
           display:flex 会覆盖浏览器对 [hidden] 的默认 display:none，于是 ✕/Esc 点了
           关不掉（2026-09-18 用户反馈）。这条选择器权重更高，能压住它。 */
        .sk-overlay[hidden] { display: none; }
        .sk-box { width: min(760px, 92vw); max-height: 86vh; background: var(--bg-card);
            border: 1px solid var(--border-color); border-radius: 12px; display: flex;
            flex-direction: column; overflow: hidden; box-shadow: 0 18px 48px rgba(0,0,0,.5); }
        .sk-head { padding: 14px 18px; border-bottom: var(--rule); font-weight: 600;
            display: flex; align-items: center; gap: 10px; flex: none; }
        .sk-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
            white-space: nowrap; font-family: var(--font-serif); letter-spacing: .03em; }
        .sk-body { flex: 1; min-height: 0; padding: 14px 18px 18px; overflow-y: auto;
            font-size: 0.88rem; line-height: 1.75; scroll-behavior: smooth; }
        /* 设置页收起态那个「已自定义 N 项 / 未修改」小尾注 */
        .sk-count { margin-left: 8px; font-weight: 400; font-size: 0.72rem;
            color: var(--text-muted); }
        .sk-count.has { color: var(--xiang-lt); }
        /* --- 📐 口径注册表（2026-09-20）--- */
        .mtr-wrap { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
        .mtr-item { border: 1px solid var(--border-color); border-radius: 8px;
            padding: 9px 12px; background: var(--bg-card); }
        .mtr-item.editable { border-left: 3px solid var(--xiang-lt); }
        .mtr-item.convention { border-left: 3px solid var(--text-muted); opacity: .92; }
        .mtr-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
        .mtr-badge { flex: none; font-size: 0.66rem; padding: 1px 6px; border-radius: 4px;
            border: 1px solid var(--border-color); color: var(--text-secondary); }
        .mtr-badge.runtime { color: var(--xiang-lt); border-color: var(--xiang-lt); }
        .mtr-badge.convention { opacity: .75; }
        .mtr-title { font-weight: 600; font-size: 0.86rem; }
        .mtr-id { font-size: 0.68rem; color: var(--text-muted); font-family: Consolas, monospace; }
        .mtr-cur { margin-left: auto; font-family: Consolas, monospace; font-size: 0.82rem;
            color: var(--xiang-lt); }
        .mtr-unit { font-style: normal; font-size: 0.68rem; color: var(--text-muted); }
        .mtr-ov { font-style: normal; font-size: 0.62rem; padding: 0 4px; border-radius: 3px;
            background: var(--xiang-lt); color: #1a1a1a; }
        .mtr-meta { margin-top: 4px; font-size: 0.68rem; color: var(--text-muted);
            word-break: break-all; }
        .mtr-desc { margin-top: 5px; font-size: 0.76rem; line-height: 1.7;
            color: var(--text-secondary); }
        .mtr-ctrl { margin-top: 7px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .mtr-ctrl .set-num { width: 92px; }
        .mtr-range { font-size: 0.68rem; color: var(--text-muted); }
        .mtr-reset { opacity: .8; }
        .mtr-msg { font-size: 0.72rem; }
        .mtr-msg.ok { color: var(--xiang-lt); }
        .mtr-msg.err { color: var(--zhuqing); }
'''

# ---------------------------------------------------------------------------
# 建库引导（BOOT_CSS / BOOT_JS，2026-09-19）
#
# 触发：首次无 subjects.json（/api/bootstrap/status → need_bootstrap）时全屏引导，
#       把「任意学科」的建库流程走完：欢迎 → 选模板/自定义 → 配参考书+子科体系+
#       笔记布局 → 确认建库（POST /api/bootstrap/create）。设置页「学科管理」浮窗
#       复用同一套学科表单组件（见 BOOT_JS 的 subjectForm），可随时新增/编辑/删除
#       学科、扩充某科的知识体系。
#
# 前缀：boot-（引导页）/ bf-（学科表单字段）。z-index 9999 全屏盖过一切。
# ---------------------------------------------------------------------------
BOOT_CSS = '''
        /* ---- 建库引导全屏层 ---- */
        .boot-overlay { position: fixed; inset: 0; z-index: 9999;
            background: rgba(var(--mo-rgb), .92);
            -webkit-backdrop-filter: blur(18px) saturate(.8); backdrop-filter: blur(18px) saturate(.8);
            display: flex; align-items: center; justify-content: center;
            padding: 24px; overflow-y: auto; }
        .boot-overlay[hidden] { display: none; }
        .boot-card { width: min(920px, 96vw); max-height: 94vh; display: flex;
            flex-direction: column; background: var(--bg-card);
            border: 1px solid var(--border-color); border-radius: 16px; overflow: hidden;
            box-shadow: 0 24px 64px rgba(0,0,0,.55); }
        .boot-head { padding: 18px 22px 14px; border-bottom: var(--rule);
            display: flex; align-items: center; gap: 12px; flex: none; }
        .boot-title { flex: 1; font-family: var(--font-serif); font-size: 1.25rem;
            font-weight: 700; letter-spacing: .04em; }
        .boot-steps { display: flex; gap: 6px; align-items: center; }
        .boot-step-dot { width: 8px; height: 8px; border-radius: 50%;
            background: var(--border-color); transition: all .25s ease; }
        .boot-step-dot.on { background: var(--zhuqing); transform: scale(1.25); }
        .boot-step-dot.done { background: var(--zhuqing-lt); }
        .boot-body { flex: 1; min-height: 0; overflow-y: auto; padding: 20px 22px 6px;
            scroll-behavior: smooth; }
        .boot-foot { padding: 12px 22px 18px; border-top: var(--rule); flex: none;
            display: flex; align-items: center; gap: 10px; }
        .boot-foot .spacer { flex: 1; }
        .boot-hint { color: var(--text-muted); font-size: .82rem; line-height: 1.7; }
        .boot-hint code { background: rgba(var(--zhuqing-rgb), .12); color: var(--zhuqing-lt);
            padding: 1px 6px; border-radius: 5px; font-size: .78rem; }
        .boot-welcome { text-align: center; padding: 28px 12px 18px; }
        .boot-welcome .boot-logo { font-size: 3.2rem; line-height: 1; margin-bottom: 14px; }
        .boot-welcome h3 { font-family: var(--font-serif); font-size: 1.5rem;
            margin: 0 0 10px; letter-spacing: .05em; }
        .boot-welcome p { color: var(--text-secondary); max-width: 620px;
            margin: 0 auto 20px; line-height: 1.85; }
        .boot-welcome .boot-tag { display: inline-block; margin: 4px 6px;
            padding: 4px 14px; border-radius: 20px; font-size: .82rem;
            background: rgba(var(--zhuqing-rgb), .14); color: var(--zhuqing-lt);
            border: 1px solid rgba(var(--zhuqing-rgb), .35); }

        /* ---- 方式选择：模板卡片 / 自定义 ---- */
        .boot-modes { display: grid; grid-template-columns: 1fr 1fr; gap: 14px;
            margin-bottom: 16px; }
        .boot-mode { padding: 18px; border-radius: 12px; cursor: pointer;
            border: 1px solid var(--border-color); background: var(--bg-secondary);
            transition: border-color .2s, transform .15s; }
        .boot-mode:hover { transform: translateY(-2px); }
        .boot-mode.on { border-color: var(--zhuqing); background: rgba(var(--zhuqing-rgb), .08); }
        .boot-mode .boot-mode-ico { font-size: 1.6rem; }
        .boot-mode h4 { margin: 8px 0 4px; font-family: var(--font-serif); }
        .boot-mode p { margin: 0; color: var(--text-muted); font-size: .8rem; line-height: 1.6; }
        .boot-tpl-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
            gap: 12px; margin-top: 4px; }
        .boot-tpl { position: relative; padding: 14px 14px 12px; border-radius: 12px;
            border: 1px solid var(--border-color); background: var(--bg-secondary);
            cursor: pointer; transition: border-color .2s; }
        .boot-tpl:hover { border-color: var(--xiang-lt); }
        .boot-tpl.on { border-color: var(--zhuqing); background: rgba(var(--zhuqing-rgb), .08); }
        .boot-tpl .boot-tpl-name { font-weight: 600; font-size: .95rem;
            display: flex; align-items: center; gap: 8px; }
        .boot-tpl .boot-tpl-color { width: 10px; height: 10px; border-radius: 50%;
            flex: none; }
        .boot-tpl .boot-tpl-meta { margin-top: 6px; font-size: .74rem;
            color: var(--text-muted); line-height: 1.55; }

        /* ---- 学科表单（引导配置 + 学科管理共用） ---- */
        .bf-card { border: 1px solid var(--border-color); border-radius: 12px;
            padding: 14px 16px; margin-bottom: 14px; background: var(--bg-secondary); }
        .bf-card-head { display: flex; align-items: center; gap: 10px;
            font-weight: 600; font-family: var(--font-serif); font-size: .95rem;
            margin-bottom: 12px; }
        .bf-card-head .bf-tpl-tag { font-size: .68rem; font-weight: 400;
            color: var(--text-muted); border: 1px solid var(--border-color);
            border-radius: 10px; padding: 1px 8px; }
        .bf-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 10px 12px; }
        .bf-field { display: flex; flex-direction: column; gap: 4px; }
        .bf-field label { font-size: .74rem; color: var(--text-muted); }
        .bf-field input, .bf-field textarea, .bf-field select {
            background: var(--bg-primary); border: 1px solid var(--border-color);
            border-radius: 8px; color: var(--text-primary); padding: 7px 10px;
            font-size: .85rem; font-family: inherit; width: 100%; box-sizing: border-box; }
        .bf-field textarea { resize: vertical; min-height: 64px; line-height: 1.65; }
        .bf-field.wide { grid-column: 1 / -1; }
        .bf-swatches { display: flex; gap: 8px; flex-wrap: wrap; padding-top: 4px; }
        .bf-swatch { width: 26px; height: 26px; border-radius: 50%; cursor: pointer;
            border: 2px solid transparent; transition: transform .12s; }
        .bf-swatch:hover { transform: scale(1.15); }
        .bf-swatch.on { border-color: var(--xuan); box-shadow: 0 0 0 2px var(--mo); }

        /* 参考书行 */
        .bf-books { margin-top: 4px; }
        .bf-book { display: grid; grid-template-columns: 2.2fr 1fr 0.9fr 2fr auto;
            gap: 8px; margin-bottom: 8px; align-items: center; }
        .bf-book input { background: var(--bg-primary); border: 1px solid var(--border-color);
            border-radius: 8px; color: var(--text-primary); padding: 6px 9px; font-size: .82rem; }
        .bf-del { background: none; border: none; color: var(--zhusha-lt); cursor: pointer;
            font-size: 1rem; padding: 4px 6px; border-radius: 6px; }
        .bf-del:hover { background: rgba(var(--zhusha-rgb), .14); }
        .bf-add { background: rgba(var(--zhuqing-rgb), .12); color: var(--zhuqing-lt);
            border: 1px dashed rgba(var(--zhuqing-rgb), .5); border-radius: 8px;
            padding: 5px 12px; font-size: .8rem; cursor: pointer; }
        .bf-add:hover { background: rgba(var(--zhuqing-rgb), .2); }

        /* 子科卡 */
        .bf-subs { margin-top: 6px; display: flex; flex-direction: column; gap: 10px; }
        .bf-sub { border: 1px solid var(--border-color); border-radius: 10px;
            padding: 12px 14px; background: var(--bg-card); }
        .bf-sub-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
        .bf-sub-head input { background: var(--bg-primary); border: 1px solid var(--border-color);
            border-radius: 8px; color: var(--text-primary); padding: 5px 9px; font-size: .8rem; }
        .bf-sub-head .bf-sub-key { width: 70px; }
        .bf-sub-head .bf-sub-dir { width: 110px; }
        .bf-sub-head .bf-sub-prefix { width: 110px; }
        .bf-sub-head .bf-del { margin-left: auto; }
        .bf-chapters { width: 100%; background: var(--bg-primary);
            border: 1px solid var(--border-color); border-radius: 8px; color: var(--text-primary);
            padding: 7px 10px; font-size: .8rem; resize: vertical; min-height: 56px;
            line-height: 1.6; box-sizing: border-box; }
        .bf-sub-hint { font-size: .7rem; color: var(--text-muted); margin-top: 3px; }

        /* 确认页摘要 */
        .boot-summary { display: flex; flex-direction: column; gap: 10px; }
        .boot-sum-item { display: flex; align-items: center; gap: 12px;
            border: 1px solid var(--border-color); border-radius: 10px;
            padding: 12px 16px; background: var(--bg-secondary); }
        .boot-sum-item .boot-tpl-color { width: 12px; height: 12px; border-radius: 50%; flex: none; }
        .boot-sum-item .boot-sum-name { font-weight: 600; }
        .boot-sum-item .boot-sum-meta { font-size: .78rem; color: var(--text-muted); }
        .boot-sum-extra { margin-top: 8px; }
        .boot-progress { position: absolute; inset: 0; background: rgba(var(--mo-rgb), .7);
            display: flex; align-items: center; justify-content: center; z-index: 5;
            border-radius: 16px; font-family: var(--font-serif); }
        .boot-progress[hidden] { display: none; }
        .boot-card { position: relative; }
'''

SETTINGS_JS = '''
// ============================================================
// 设置页（2026-09-14）
//
// 三类配置各归其位：
//   · 模型 / API Key → src/.secrets.json（密钥**只写不读**，界面只显示尾 4 位）
//   · 每日闪卡额度    → config 表
//   · 主题 / 背景图    → config 表 + src/assets/
//
// 外观（主题 + 背景）在页面加载时就要生效，不能等切到设置页——所以这段
// 在脚本加载时立刻跑一次「先应用本地缓存、再与服务端核对」。
// ============================================================
(function () {
    // API 基址：用当前页面 origin，平板/手机经局域网访问时才能正常调接口；
    // 本地以 file:// 直开时回落到 localhost:8080
    const API = location.protocol.startsWith('http') ? location.origin : "http://localhost:8080";
    const LS_THEME = "kaoyan.ui.theme";
    const LS_BG = "kaoyan.ui.bg";
    const LS_BG_OP = "kaoyan.ui.bgOpacity";

    // ---- 外观：立即应用（先用 localStorage 抢首屏，再和服务端核对）----
    function applyTheme(t) {
        document.documentElement.dataset.theme = (t === "light" ? "light" : "dark");
    }
    function applyBg(path) {
        const layer = document.getElementById("bg-layer");
        if (!layer) return;
        if (path) {
            layer.style.backgroundImage = 'url("' + API + "/api/notes/asset?path="
                + encodeURIComponent(path) + '")';
            document.body.classList.add("has-bg");
        } else {
            layer.style.backgroundImage = "";
            document.body.classList.remove("has-bg");
        }
    }
    // 遮罩是 ::after 伪元素，改不了它的内联样式，所以走 CSS 变量传值
    function applyBgOpacity(opacity) {
        const op = Number(opacity);
        document.documentElement.style.setProperty("--bg-opacity",
            String(Number.isFinite(op) ? op : 0.35));
    }

    let cachedTheme = "dark";
    try {
        cachedTheme = localStorage.getItem(LS_THEME) || "dark";
        applyTheme(cachedTheme);
        const cbg = localStorage.getItem(LS_BG) || "";
        const cop = localStorage.getItem(LS_BG_OP) || "0.35";
        applyBgOpacity(cop);
        if (cbg) applyBg(cbg);
    } catch (e) { /* 隐私模式下 localStorage 会抛错，不能带崩整页 */ }

    // ---- 与服务端核对（server 是唯一真相源）----
    function syncAppearance(s) {
        const ui = (s && s.ui) || {};
        const theme = ui.theme || "dark";
        applyTheme(theme);
        applyBgOpacity(ui.bg_opacity);
        applyBg(ui.background || "");
        try {
            localStorage.setItem(LS_THEME, theme);
            localStorage.setItem(LS_BG, ui.background || "");
            localStorage.setItem(LS_BG_OP, ui.bg_opacity || "0.35");
        } catch (e) {}
    }
    fetch(API + "/api/settings").then(r => r.json())
        .then(d => { if (d && d.ok) syncAppearance(d); })
        .catch(() => {});

    // ---- 设置分组手风琴（2026-09-22）----
    // 把每组「标题以外的内容」统一包进一个 .set-body，标题变成可点表头：
    // 平时全折叠只留标题行，点标题展开该组。放在渲染后统一处理，
    // 模板那一大段 innerHTML 字符串不用跟着逐个改。
    function foldGroups(container) {
        container.querySelectorAll(".set-group").forEach(g => {
            const title = g.querySelector(":scope > .set-title");
            if (!title) return;
            const body = document.createElement("div");
            body.className = "set-body";
            Array.from(g.childNodes)
                .filter(n => n.nodeType === Node.ELEMENT_NODE && n !== title)
                .forEach(k => body.appendChild(k));
            g.appendChild(body);
            g.classList.add("acc", "collapsed");
            title.setAttribute("role", "button");
            title.setAttribute("tabindex", "0");
            const toggleGroup = function (ev) {
                ev.preventDefault();
                g.classList.toggle("collapsed");
            };
            title.addEventListener("click", toggleGroup);
            title.addEventListener("keydown", function (ev) {
                if (ev.key === "Enter" || ev.key === " ") toggleGroup(ev);
            });
        });
    }

    // ---- 设置页 UI（懒加载：切到该页才渲染）----
    function render(container) {
        container.innerHTML =
            '<div class="sec-body">'
          + '  <div class="set-group"><div class="set-title">🤖 模型与 API Key</div>'
          + '    <span class="set-label">API Key（只写不读，保存后只显示尾 4 位）</span>'
          + '    <div class="set-row">'
          + '      <input class="set-input mono" id="set-key" type="password" autocomplete="off"'
          + '             placeholder="留空 = 不修改">'
          + '      <button class="fs-btn" id="set-key-save">保存</button>'
          + '      <button class="fs-btn" id="set-key-clear">清除</button>'
          + '    </div>'
          + '    <span class="set-label">模型</span>'
          + '    <div class="set-row">'
          + '      <input class="set-input mono" id="set-model" list="set-models" placeholder="deepseek-flash">'
          + '      <datalist id="set-models">'
          + '        <option value="deepseek-flash"></option>'
          + '        <option value="deepseek-v4-pro"></option>'
          + '      </datalist>'
          + '      <button class="fs-btn" id="set-model-save">保存模型</button>'
          + '    </div>'
          + '    <div class="set-hint" id="set-llm-status"></div>'
          + '    <div class="set-hint">批改简答题、生成错题解析都走这个模型。'
          + '关键密钥只存在服务端 <code>src/.secrets.json</code>，不会下发到浏览器。</div>'
          + '    <span class="set-label">答错解析 / 追问的思考强度（默认）</span>'
          + '    <div class="set-seg" id="set-think">'
          + '      <button data-think="quick">关掉思考 · 跟手</button>'
          + '      <button data-think="deep">深度思考 · 更细</button>'
          + '    </div>'
          + '    <div class="set-hint" id="set-think-status"></div>'
          + '    <div class="set-hint">关掉思考能省掉一段白等（实测 3.1s → 1.1s），正文质量基本不变；'
          + '想每次都多想一会儿就选「深度思考」。<br>'
          + '面板上还有一颗 <b>深想一遍</b> 按钮，可以单次要求深度思考，'
          + '不受这里影响。读笔记时的提问<b>始终深度思考</b>——那儿问的通常正是不会的点。</div>'
          + '  </div>'
          + '  <div class="set-group"><div class="set-title">✍️ AI 提示词（你自己写）</div>'
          + '    <span class="set-label">错题解析 / 追问用的提示词</span>'
          + '    <textarea class="set-input set-ta" id="set-prompt-exp" rows="4" spellcheck="false"'
          + '      placeholder="留空 = 用内置的极简默认（只有一句角色 + 公式格式）"></textarea>'
          + '    <div class="set-row">'
          + '      <button class="fs-btn" id="set-prompt-exp-save">保存</button>'
          + '      <button class="fs-btn" id="set-prompt-exp-reset">恢复内置默认</button>'
          + '      <span class="set-hint" id="set-prompt-exp-status" style="margin:0;"></span>'
          + '    </div>'
          + '    <div class="set-hint">「答错讲解」和「答对后的追问」共用这一份；'
          + '两者的区别写在请求里（选的是哪一项 / 你自己提的问题），所以一份就够。'
          + '内置默认<b>只保留角色与公式格式</b>——怎么讲完全由你定。</div>'
          + '    <span class="set-label">读笔记提问用的提示词</span>'
          + '    <textarea class="set-input set-ta" id="set-prompt-qa" rows="4" spellcheck="false"'
          + '      placeholder="留空 = 用内置的极简默认"></textarea>'
          + '    <div class="set-row">'
          + '      <button class="fs-btn" id="set-prompt-qa-save">保存</button>'
          + '      <button class="fs-btn" id="set-prompt-qa-reset">恢复内置默认</button>'
          + '      <span class="set-hint" id="set-prompt-qa-status" style="margin:0;"></span>'
          + '    </div>'
          + '    <div class="set-hint">读笔记时问的多半是你不会的点，这里写你希望它怎么讲。'
          + '读笔记提问<b>始终深度思考</b>（不受上面思考强度默认值影响）。</div>'
          + '  </div>'
          + '  <div class="set-group"><div class="set-title">🃏 每日闪卡额度</div>'
          + '    <div class="set-row">'
          + '      <span class="set-label" style="margin:0;">新卡</span>'
          + '      <input class="set-input set-num" id="set-new" type="number" min="0" max="500">'
          + '      <span class="set-label" style="margin:0;">复习卡</span>'
          + '      <input class="set-input set-num" id="set-review" type="number" min="0" max="2000">'
          + '      <button class="fs-btn" id="set-quota-save">保存</button>'
          + '    </div>'
          + '    <div class="set-hint" id="set-quota-status"></div>'
          + '    <div class="set-hint">新卡上限直接影响四科能不能都开张——'
          + '额度是先到先得的，太小的话卡多的科目会把额度吃光。改完下一轮组题即生效。</div>'
          + '    <div class="set-row">'
          + '      <span class="set-label" style="margin:0;">今日刷完后再来</span>'
          + '      <input class="set-input set-num" id="set-extra" type="number" min="1" max="100">'
          + '      <span class="set-label" style="margin:0;">张</span>'
          + '      <button class="fs-btn" id="set-extra-save">保存</button>'
          + '      <span class="set-hint" id="set-extra-status" style="margin:0;"></span>'
          + '    </div>'
          + '    <div class="set-hint">练习区卡片头部的「🔁 再来 N 张」就是这里设的张数'
          + '（今日额度用完时的空状态也会给一个按钮）。它<b>不受每日额度限制</b>，'
          + '而且优先级排序不变——<b>有到期/学习中的卡就会先复习它们</b>，不会只塞新卡。'
          + '「重开一组」还是走每日额度；额度用完了它会自动接上这一组。</div>'
          + '  </div>'
          + '  <div class="set-group"><div class="set-title">📱 平板 / 手机连接</div>'
          + '    <div class="set-lan">'
          + '      <div class="set-qr" id="set-qr"><div style="color:#888;font-size:0.7rem;text-align:center;padding-top:60px;">加载中…</div></div>'
          + '      <div class="set-lan-info">'
          + '        <div class="set-lan-url" id="set-lan-url">—</div>'
          + '        <div class="set-lan-tip">确保平板/手机和这台电脑连在同一个 WiFi，用系统相机扫二维码即可打开大盘。'
          + '在平板上答简答题时会出现手写区，可以直接用笔写答案；早间回顾已并入大盘的「早」页。</div>'
          + '        <div class="set-hint" id="set-lan-status"></div>'
          + '      </div>'
          + '    </div>'
          + '  </div>'
          + '  <div class="set-group"><div class="set-title">🎨 外观</div>'
          + '    <span class="set-label">主题</span>'
          + '    <div class="set-seg" id="set-theme">'
          + '      <button data-theme="dark">深色</button><button data-theme="light">浅色</button>'
          + '    </div>'
          + '    <span class="set-label">背景图</span>'
          + '    <div class="set-row">'
          + '      <input type="file" id="set-bg-file" accept="image/*" style="display:none">'
          + '      <button class="fs-btn" id="set-bg-pick">选择图片</button>'
          + '      <button class="fs-btn" id="set-bg-clear">移除背景</button>'
          + '    </div>'
          + '    <div class="set-bg-preview" id="set-bg-preview"></div>'
          + '    <span class="set-label">背景可见度</span>'
          + '    <div class="set-row">'
          + '      <input class="set-range" id="set-bg-op" type="range" min="0" max="0.9" step="0.05">'
          + '      <span class="set-status" id="set-bg-op-val"></span>'
          + '    </div>'
          + '    <div class="set-hint" id="set-bg-status"></div>'
          + '    <div class="set-hint">图片存在服务端 <code>src/assets/</code>，'
          + '上传前会先在浏览器里压缩，手机原图也不会撑爆。</div>'
          + '    <span class="set-label">鼠标粒子拖尾（全页面）</span>'
          + '    <div class="set-seg" id="set-fx">'
          + '      <button data-fx="on">开</button><button data-fx="off">关</button>'
          + '    </div>'
          + '    <div class="set-hint" id="set-fx-status"></div>'
          + '    <div class="set-hint">鼠标走过后留下一串细腻的细尘粒子，轻轻飘一小段就化没，'
          + '每个子页都生效（不只首页）。只在鼠标/触控笔上出；系统开了「减弱动效」时会自动不出现。</div>'
          + '  </div>'
          + '  <div class="set-group"><div class="set-title">🍅 番茄钟</div>'
          + '    <span class="set-label">轮播背景图（最多 12 张，按添加顺序轮换）</span>'
          + '    <div class="set-row">'
          + '      <input type="file" id="set-pomo-file" accept="image/*" multiple style="display:none">'
          + '      <button class="fs-btn" id="set-pomo-pick">添加图片</button>'
          + '      <button class="fs-btn" id="set-pomo-clear">全部清空</button>'
          + '    </div>'
          + '    <div class="set-thumbs" id="set-pomo-thumbs"></div>'
          + '    <span class="set-label">每张停留</span>'
          + '    <div class="set-row">'
          + '      <input class="set-range" id="set-pomo-int" type="range" min="5" max="120" step="5">'
          + '      <span class="set-status" id="set-pomo-int-val"></span>'
          + '    </div>'
          + '    <span class="set-label">遮罩浓度（压暗背景，保证倒计时看得清）</span>'
          + '    <div class="set-row">'
          + '      <input class="set-range" id="set-pomo-dim" type="range" min="0" max="0.9" step="0.05">'
          + '      <span class="set-status" id="set-pomo-dim-val"></span>'
          + '    </div>'
          + '    <div class="set-hint">卡片上的字会跟着当前这张图自动配色：图偏亮就换成深字，'
          + '偏暗就用白字，不用手调。要是某张图一半亮一半暗（亮天空压着暗地面那种），'
          + '两种字色都有半边糊，这时会在这档浓度之上再自动补一点遮罩——所以偶尔'
          + '看着比滑杆标的更暗，是正常的。</div>'
          + '    <span class="set-label">轮播显示在</span>'
          + '    <div class="set-seg" id="set-pomo-show">'
          + '      <button data-show="both">卡片 + 全屏</button>'
          + '      <button data-show="full">仅全屏</button>'
          + '      <button data-show="off">不使用</button>'
          + '    </div>'
          + '    <div class="set-hint" id="set-pomo-status"></div>'
          + '    <div class="set-hint">图存在服务端 <code>src/assets/pomo/</code>，和整页背景是两码事：'
          + '整页背景是一张铺底，番茄钟要的是一叠轮换。番茄钟本体在大盘「总览」页顶部，'
          + '预设 45+10×3 / 60+15×2 / 90 / 120 / 180，也支持全屏。</div>'
          + '  </div>'
          + '  <div class="set-group" id="set-keys-group"><div class="set-title">⌨ 快捷键'
          + '      <span class="sk-count" id="sk-count"></span></div>'
          + '    <div class="set-hint">平时收起，点下面按钮用浮窗打开全部快捷键；'
          + '在浮窗里点按键就能改：点一下 → 按下新键，Esc 取消。</div>'
          + '    <button class="fs-btn" id="sk-open" type="button">⌨ 打开快捷键设置</button>'
          + '  </div>'
          + '  <div class="set-group" id="set-subj-group"><div class="set-title">📚 学科管理'
          + '      <span class="sk-count" id="set-subj-count"></span></div>'
          + '    <div class="set-hint" id="set-subj-summary">正在读取学科…</div>'
          + '    <div class="set-row">'
          + '      <button class="fs-btn" id="set-subj-open" type="button">📚 打开学科管理</button>'
          + '      <button class="fs-btn" id="set-subj-add" type="button">＋ 新增学科</button>'
          + '    </div>'
          + '    <div class="set-hint">学科 = 一套独立的「笔记目录 + 参考书 + 子科体系 + 闪卡前缀」。'
          + '这里可以随时新增、编辑、扩充（比如给数学加一本参考书、给 408 加一个子科），'
          + '也可以删除不学的学科（只删配置，不碰笔记与数据）。</div>'
          + '  </div>'
          + '  <div class="set-group" id="set-metrics-group"><div class="set-title">📐 口径'
          + '      <span class="sk-count" id="set-metrics-count"></span></div>'
          + '    <div class="set-hint" id="set-metrics-summary">正在读取口径注册表…</div>'
          + '    <div class="set-hint">这是全系统「什么算一条笔记 / 什么算覆盖 / 什么算掌握 / '
          + '各类时间窗与阈值」的唯一事实源（<code>src/metrics_spec.json</code>）。'
          + '标了「可改」的项改完<b>立刻生效</b>（写进 config 表，服务端每次请求都读）；'
          + '标「固定」的要改代码；标「约定」的是语义，永远不要改（改了历史数据就没意义了）。</div>'
          + '    <div class="mtr-wrap" id="set-metrics-body"></div>'
          + '  </div>'
          + '</div>';
        foldGroups(container);   // 先把手风琴结构包好，再 bind/load（by id 查找不受打包影响）
        bind(container);
        bindPomo(container);
        initKeysModal(container);      // 先建好浮窗（含 #set-keys），再让 KEYS_JS 往里填表
        renderKeys(container);
        renderMetrics(container);      // 📐 口径：从 /api/metrics 拉注册表快照渲染
        load(container);
    }

    // 快捷键设置：设置页平时只留一个「打开」按钮，整张键位表放在浮窗里。
    // 浮窗挂在该设置页下（.page[data-page="settings"]）而不是 body——这样
    // 一离开设置页它就被一起藏掉，不会被 backdrop-filter 弄得 fixed 错位。
    function initKeysModal(container) {
        const open = container.querySelector("#sk-open");
        const countEl = container.querySelector("#sk-count");
        if (!open) return;
        const pageEl = container.closest('.page[data-page="settings"]') || document.body;

        function updateKeyCount() {
            if (!countEl) return;
            let n = 0;
            if (globalThis.__keys && globalThis.__keys.DEFS) {
                n = globalThis.__keys.DEFS.filter(function (d) {
                    return globalThis.__keys.isCustom(d.id);
                }).length;
            }
            countEl.textContent = n > 0 ? ("已自定义 " + n + " 项 · ") : "未修改 · ";
            countEl.classList.toggle("has", n > 0);
        }

        let ov = document.getElementById("sk-overlay");
        if (ov) {
            ov._anchor = pageEl;         // 页面重建过也可能换容器，记录当前归属
            if (ov.parentNode !== pageEl) pageEl.appendChild(ov);
            open.onclick = function () { ov.hidden = false; updateKeyCount(); };
            updateKeyCount();
            return;
        }

        ov = document.createElement("div");
        ov.id = "sk-overlay";
        ov.className = "sk-overlay";
        ov.hidden = true;
        ov.innerHTML =
            '<div class="sk-box">'
            + '<div class="sk-head"><span class="sk-title">⌨ 快捷键设置</span>'
            + '<button class="rev-modal-close" id="sk-close" type="button" title="关闭（Esc）">✕</button></div>'
            + '<div class="sk-body">'
            + '<div class="set-hint" style="margin-top:0;">点按键就能改：点一下 → 按下新键，Esc 取消。'
            + '改过的键标成金色，右边 ↺ 恢复默认。只认单键（可带 Ctrl / Alt），'
            + 'Shift 不区分——按 F 和按 Shift+F 是同一个键。</div>'
            + '<div id="set-keys"></div>'
            + '<div class="set-hint">虚线框的是固定键位，不开放修改：'
            + '「选项 A–D / 自评 1–4」是一整组键，还要靠「有没有显示答案」区分含义；'
            + '「Esc」被全屏与各层弹窗共用，改了容易把人锁在里面出不来。</div>'
            + '</div></div>';
        pageEl.appendChild(ov);

        const close = function () {
            // 正在录键时按 Esc 是「取消这次改键」，不该顺手把浮窗关了
            if (globalThis.__keys && globalThis.__keys.recording && globalThis.__keys.recording()) return;
            ov.hidden = true;
        };
        ov.querySelector("#sk-close").onclick = close;
        ov.addEventListener("click", function (ev) { if (ev.target === ov) close(); });

        open.onclick = function () {
            ov.hidden = false;
            // 打开时重刷一遍键位表 + 计数，确保跟最新的自定义状态一致
            if (globalThis.__keys && typeof globalThis.__keys.renderUI === "function") {
                try { globalThis.__keys.renderUI(); } catch (e) {}
            }
            updateKeyCount();
        };

        // 录键一定要用 capture、且比 KEYS_JS 的录制监听**先注册**：非录制态 Esc 关浮窗，
        // 录制态则不拦（留给 KEYS_JS 去「取消改键」，否则会连浮窗一起关掉）。
        document.addEventListener("keydown", function (ev) {
            if (ov.hidden || ev.key !== "Escape") return;
            if (globalThis.__keys && globalThis.__keys.recording && globalThis.__keys.recording()) return;
            close();
        }, true);

        // 改完键/恢复默认都会 emit 这个事件，收起态那枚计数跟着变
        document.addEventListener("kaoyan:keys-changed", updateKeyCount);
        updateKeyCount();
    }

    // 快捷键表整块交给 KEYS_JS 自己渲染 —— 表在它手里，设置页不该知道条目长什么样，
    // 否则加一个动作要改两个文件。它内部是按 id 找 #set-keys 的。
    function renderKeys(container) {
        try { globalThis.__keys.renderUI(); }
        catch (e) { console.error("[settings] 快捷键分组渲染失败:", e); }
    }

    // ---- 📐 口径（2026-09-20）------------------------------------------------
    // 数据来自 GET /api/metrics（服务端读 src/metrics_spec.json）。
    // 页面**不抄任何口径文案**：标题、说明、来源、可改范围全由注册表下发，
    // 所以以后改口径只需要动 metrics_spec.json，这一页跟着变。
    // 三类 scope 的展示区别：
    //   runtime + editable → 可直接改（写 config 表，服务端每次请求读 → 立刻生效）
    //   fixed              → 只读，但要告诉用户「改哪里」
    //   convention         → 只读语义，标「不要改」
    const MTR_SCOPE_LABEL = { runtime: "可改", fixed: "固定", convention: "约定" };

    function mtrValText(v) {
        if (v == null || v === "") return "—";
        if (typeof v === "object") return JSON.stringify(v, null, 0);
        return String(v);
    }

    function renderMetrics(container) {
        const host = container.querySelector("#set-metrics-body");
        const sumEl = container.querySelector("#set-metrics-summary");
        const cntEl = container.querySelector("#set-metrics-count");
        if (!host) return;

        async function paint() {
            let d;
            try { d = await api("/api/metrics"); }
            catch (e) {
                if (sumEl) sumEl.textContent = "⚠ 读取口径注册表失败：" + e.message
                    + "（若本地服务是旧版，重启「启动考研大盘」后生效）";
                return;
            }
            const items = d.items || [];
            const c = d.counts || {};
            if (cntEl) {
                cntEl.textContent = "共 " + items.length + " 项 · 可改 " + (c.runtime || 0)
                    + " · 固定 " + (c.fixed || 0) + " · 约定 " + (c.convention || 0) + " · ";
                cntEl.classList.toggle("has", (c.runtime || 0) > 0);
            }
            if (sumEl) {
                sumEl.textContent = "注册表 v" + (d.version || 0)
                    + "  ·  " + (d.spec_path || "src/metrics_spec.json")
                    + (d.spec_mtime ? "（" + d.spec_mtime.slice(0, 16).replace("T", " ") + "）" : "");
            }

            host.innerHTML = items.map(function (it) {
                const editable = it.editable && it.key;
                const rid = "mtr-" + it.id.replace(/[^\w]/g, "-");
                let head = '<div class="mtr-head">'
                    + '<span class="mtr-badge ' + it.scope + '">' + (MTR_SCOPE_LABEL[it.scope] || it.scope) + "</span>"
                    + '<span class="mtr-title">' + esc(it.title) + "</span>"
                    + '<span class="mtr-id">' + esc(it.id) + "</span>"
                    + (editable
                        ? '<span class="mtr-cur" id="' + rid + '-cur">' + esc(mtrValText(it.value))
                          + (it.overridden ? ' <em class="mtr-ov">已改</em>' : "") + "</span>"
                        : '<span class="mtr-cur">' + esc(mtrValText(it.value))
                          + (it.unit ? ' <em class="mtr-unit">' + esc(it.unit) + "</em>" : "") + "</span>")
                    + "</div>";

                let ctrl = "";
                if (editable) {
                    const r = it.range || [];
                    ctrl = '<div class="mtr-ctrl">'
                        + '<input class="set-input set-num" id="' + rid + '" type="number"'
                        + ' value="' + esc(it.value) + '"'
                        + (r.length === 2 ? ' min="' + r[0] + '" max="' + r[1] + '"' : "")
                        + (it.unit ? ' title="' + esc(it.unit) + '"' : "") + ">"
                        + (r.length === 2 ? '<span class="mtr-range">' + r[0] + " ~ " + r[1] + "</span>" : "")
                        + '<button class="fs-btn" data-mtr-save="' + esc(it.key) + '" data-mtr-el="' + rid + '">保存</button>'
                        + (it.overridden
                            ? '<button class="fs-btn mtr-reset" data-mtr-reset="' + esc(it.key)
                              + '" data-mtr-el="' + rid + '" data-mtr-def="' + esc(it.default) + '">恢复默认(' + esc(it.default) + ")</button>"
                            : '<span class="mtr-range">默认 ' + esc(it.default) + "</span>")
                        + '<span class="mtr-msg" id="' + rid + '-msg"></span>'
                        + "</div>";
                }

                const meta = []
                    .concat(it.unit && !editable ? [] : [])
                    .concat(it.source ? ["来源：" + it.source] : [])
                    .concat((it.used_by || []).length ? ["被谁用：" + it.used_by.join("、")] : [])
                    .join("　·　");

                return '<div class="mtr-item ' + it.scope + (editable ? " editable" : "") + '">'
                    + head + ctrl
                    + (meta ? '<div class="mtr-meta">' + esc(meta) + "</div>" : "")
                    + (it.desc ? '<div class="mtr-desc">' + esc(it.desc) + "</div>" : "")
                    + "</div>";
            }).join("");

            host.onclick = async function (ev) {
                const sv = ev.target.closest("[data-mtr-save]");
                const rs = ev.target.closest("[data-mtr-reset]");
                try {
                    if (sv) {
                        const key = sv.dataset.mtrSave;
                        const el = document.getElementById(sv.dataset.mtrEl);
                        const msg = document.getElementById(sv.dataset.mtrEl + "-msg");
                        const body = {};
                        body[key] = el.value;
                        sv.disabled = true;
                        try {
                            await api("/api/settings", "POST", body);
                            if (msg) { msg.textContent = "已保存 ✓ 立刻生效"; msg.className = "mtr-msg ok"; }
                        } catch (e2) {
                            if (msg) { msg.textContent = "✗ " + e2.message; msg.className = "mtr-msg err"; }
                        }
                        sv.disabled = false;
                    } else if (rs) {
                        const key = rs.dataset.mtrReset;
                        const el = document.getElementById(rs.dataset.mtrEl);
                        const msg = document.getElementById(rs.dataset.mtrEl + "-msg");
                        el.value = rs.dataset.mtrDef;
                        await api("/api/settings", "POST", (function () { const b = {}; b[key] = el.value; return b; })());
                        paint();          // 改了「是否覆盖」的状态，整体重画一次
                    }
                } catch (e3) {
                    console.error("[metrics] 保存失败:", e3);
                }
            };
        }
        paint();
    }

    let cur = null;

    async function api(path, method, payload) {
        const opt = { method: method || "GET" };
        if (payload) {
            opt.headers = { "Content-Type": "application/json" };
            opt.body = JSON.stringify(payload);
        }
        const r = await fetch(API + path, opt);
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || ("HTTP " + r.status));
        return d;
    }

    function status(el, msg, cls) {
        el.className = "set-hint set-status " + (cls || "");
        el.textContent = msg;
    }
    // 把任意文本变成能塞进 innerHTML **和属性值**的安全串。
    // 多加一步 &quot; 是因为 textContent→innerHTML 只转义 & < >，不转义引号；
    // 图片路径要写进 data-del="…"，漏了引号就能越出属性。
    function esc(s) {
        const d = document.createElement("div");
        d.textContent = s == null ? "" : String(s);
        return d.innerHTML.replace(/"/g, "&quot;");
    }

    async function load(container) {
        try {
            const d = await api("/api/settings");
            cur = d;
            container.querySelector("#set-new").value = d.review.new_per_day;
            container.querySelector("#set-review").value = d.review.reviews_per_day;
            // 「再来一组」的张数（默认 10）：服务端没给就落 10
            const ex = container.querySelector("#set-extra");
            if (ex) ex.value = (d.review && d.review.flash_extra_count) || 10;
            container.querySelector("#set-model").value = d.llm.model || "";
            const kEl = container.querySelector("#set-key");
            kEl.placeholder = d.llm.has_key ? ("已设置 " + d.llm.key_hint + "，留空 = 不修改") : "尚未设置";
            status(container.querySelector("#set-llm-status"),
                d.llm.has_key ? ("✅ 当前模型 " + d.llm.model + "，密钥 " + d.llm.key_hint)
                              : "⚠ 尚未配置密钥，AI 批改与错题解析不可用",
                d.llm.has_key ? "ok" : "warn");
            syncAppearance(d);
            markTheme(container, d.ui.theme);
            // 鼠标光效开关（服务端存 ui_mouse_fx，多设备一致）
            const fxOn = (d.ui.mouse_fx || "on") !== "off";
            container.querySelectorAll("#set-fx button").forEach(x =>
                x.classList.toggle("on", (x.dataset.fx === "on") === fxOn));
            status(container.querySelector("#set-fx-status"), "");
            // 解析/追问的思考强度默认值（on = 关掉思考）
            const quickOn = (d.ui.explain_quick || "on") !== "off";
            container.querySelectorAll("#set-think button").forEach(x =>
                x.classList.toggle("on", (x.dataset.think === "quick") === quickOn));
            status(container.querySelector("#set-think-status"), "");
            // AI 提示词：回填用户自己写的那份（空 = 用内置默认，占位符会说明）
            const pe = container.querySelector("#set-prompt-exp");
            if (pe) pe.value = d.ui.prompt_explain || "";
            const pq = container.querySelector("#set-prompt-qa");
            if (pq) pq.value = d.ui.prompt_note_qa || "";
            status(container.querySelector("#set-prompt-exp-status"), "");
            status(container.querySelector("#set-prompt-qa-status"), "");
            const op = Number(d.ui.bg_opacity);
            container.querySelector("#set-bg-op").value = Number.isFinite(op) ? op : 0.35;
            container.querySelector("#set-bg-op-val").textContent =
                Math.round((Number.isFinite(op) ? op : 0.35) * 100) + "%";
            const pv = container.querySelector("#set-bg-preview");
            if (d.ui.background) {
                pv.classList.add("on");
                pv.style.backgroundImage = 'url("' + API + "/api/notes/asset?path="
                    + encodeURIComponent(d.ui.background) + '")';
            } else { pv.classList.remove("on"); }

            // 番茄钟一组（轮播图 + 偏好）。服务端是旧版没有 pomo 时整段跳过，
            // 不能让一个 undefined 把上面已经填好的表单也带崩。
            try { renderPomo(container, d.pomo); }
            catch (e) { console.error("[settings] 番茄钟分组渲染失败:", e); }

            // 局域网访问信息（二维码 + 地址）
            loadLanInfo(container);
        } catch (e) {
            status(container.querySelector("#set-llm-status"),
                "读取设置失败：" + e.message + "（本地服务未运行？）", "warn");
        }
    }

    // 平板/手机连接：取服务端算好的局域网地址与二维码
    async function loadLanInfo(container) {
        const qrEl = container.querySelector("#set-qr");
        const urlEl = container.querySelector("#set-lan-url");
        const statusEl = container.querySelector("#set-lan-status");
        if (!qrEl || !urlEl) return;
        try {
            const d = await api("/api/lan-info");
            if (!d.ok || !d.primary) {
                qrEl.innerHTML = '<div style="color:#888;font-size:0.7rem;text-align:center;padding-top:60px;">不可用</div>';
                return;
            }
            urlEl.textContent = d.primary;
            urlEl.title = "点击复制";
            urlEl.onclick = () => {
                try {
                    navigator.clipboard.writeText(d.primary);
                    if (statusEl) statusEl.textContent = "✅ 已复制链接";
                } catch (e) { /* 部分浏览器不支持 clipboard API */ }
            };
            if (d.qr) {
                qrEl.innerHTML = '<img src="' + d.qr + '" alt="扫码连接" title="用手机/平板相机扫描">';
            } else {
                qrEl.innerHTML = '<div style="color:#888;font-size:0.7rem;text-align:center;padding-top:55px;">二维码不可用<br>请直接输入网址</div>';
            }
            // 多个网卡时把其余地址列出来，主地址扫不通可手动换
            if (d.urls && d.urls.length > 1) {
                const extra = document.createElement("div");
                extra.style.fontSize = "0.7rem";
                extra.style.color = "var(--text-muted)";
                extra.style.marginTop = "4px";
                extra.innerHTML = "其他地址：" + d.urls.slice(1).map(u =>
                    '<span style="margin-right:10px;color:var(--text-secondary);">' + u + '</span>').join("");
                urlEl.parentNode.insertBefore(extra, urlEl.nextSibling);
            }
        } catch (e) {
            qrEl.innerHTML = '<div style="color:#c88;font-size:0.7rem;text-align:center;padding-top:55px;">加载失败</div>';
            if (statusEl) statusEl.textContent = "⚠ " + e.message;
        }
    }

    function markTheme(container, t) {
        container.querySelectorAll("#set-theme button").forEach(b =>
            b.classList.toggle("on", b.dataset.theme === (t === "light" ? "light" : "dark")));
    }

    // ============================================================
    // 番茄钟分组（2026-09-21）：轮播背景图 + 三个偏好
    //
    // 只负责「配」，不负责「播」——轮播逻辑在 POMO_JS 里，这边每改一项就叫一次
    // globalThis.__pomoReload()，让总览页那张卡立刻按新配置换图，不用刷新整页。
    // ============================================================
    function pomoImgUrl(p) {
        return API + "/api/notes/asset?path=" + encodeURIComponent(p);
    }
    function renderPomo(container, p) {
        const box = container.querySelector("#set-pomo-thumbs");
        if (!box) return;
        const cfg = p || {};
        const imgs = Array.isArray(cfg.images) ? cfg.images : [];
        box.innerHTML = imgs.length
            ? imgs.map((x, i) => '<div class="set-thumb" style="background-image:url(&quot;'
                + pomoImgUrl(x) + '&quot;)"><span class="set-thumb-n">' + (i + 1) + "</span>"
                + '<button class="set-thumb-x" data-del="' + esc(x) + '" title="移除这张">✕</button></div>').join("")
            : '<div class="set-thumb-empty">还没有轮播背景。添加几张风景 / 书桌照，'
              + '全屏跑番茄钟时就会慢慢轮换。</div>';
        box.querySelectorAll("[data-del]").forEach(b => {
            b.onclick = async () => {
                try {
                    const d = await api("/api/settings/pomo/background/remove", "POST", { path: b.dataset.del });
                    renderPomo(container, Object.assign({}, cur && cur.pomo, { images: d.backgrounds }));
                    reloadPomo();
                    status(container.querySelector("#set-pomo-status"), "已移除一张", "ok");
                } catch (e) {
                    status(container.querySelector("#set-pomo-status"), "移除失败：" + e.message, "warn");
                }
            };
        });
        const iv = Number(cfg.interval) || 20, dm = Number(cfg.dim);
        container.querySelector("#set-pomo-int").value = iv;
        container.querySelector("#set-pomo-int-val").textContent = iv + " 秒";
        container.querySelector("#set-pomo-dim").value = Number.isFinite(dm) ? dm : 0.35;
        container.querySelector("#set-pomo-dim-val").textContent =
            Math.round((Number.isFinite(dm) ? dm : 0.35) * 100) + "%";
        container.querySelectorAll("#set-pomo-show button").forEach(b =>
            b.classList.toggle("on", b.dataset.show === (cfg.show || "both")));
    }
    // 改完让总览页的番茄钟自己重新拉一次配置；它不在场（POMO_JS 没跑起来）就算了
    function reloadPomo() {
        try { if (globalThis.__pomoReload) globalThis.__pomoReload(); } catch (e) {}
    }

    function bindPomo(container) {
        const $ = (s) => container.querySelector(s);
        const say = (m, cls) => status($("#set-pomo-status"), m, cls);

        $("#set-pomo-pick").onclick = () => $("#set-pomo-file").click();
        $("#set-pomo-file").onchange = async (ev) => {
            const files = Array.prototype.slice.call(ev.target.files || []);
            ev.target.value = "";
            if (!files.length) return;
            say("正在压缩并上传（" + files.length + " 张）…");
            let okCount = 0, last = null;
            // 串行而不是 Promise.all：一次塞多张时并发上传会把 8MB 的上限按总体积算，
            // 而且服务端写清单是读-改-写，并发会互相覆盖掉对方的条目。
            for (const f of files) {
                try {
                    const d = await api("/api/settings/pomo/background", "POST", { image: await compress(f) });
                    last = d.backgrounds; okCount++;
                } catch (e) { say("上传失败：" + e.message, "warn"); }
            }
            if (last) {
                cur = cur || {};
                cur.pomo = Object.assign({}, cur.pomo, { images: last });
                renderPomo(container, cur.pomo);
                reloadPomo();
                say(okCount ? ("✅ 已添加 " + okCount + " 张（共 " + last.length + " 张在轮播）") : "未添加任何图片",
                    okCount ? "ok" : "warn");
            }
        };
        $("#set-pomo-clear").onclick = async () => {
            if (!confirm("清空番茄钟的全部轮播背景？")) return;
            try {
                const d = await api("/api/settings/pomo/background/clear", "POST", {});
                cur = cur || {}; cur.pomo = Object.assign({}, cur.pomo, { images: [] });
                renderPomo(container, cur.pomo);
                reloadPomo();
                say("已清空（删掉 " + d.backgrounds.length + " 张）", "ok");
            } catch (e) { say("清空失败：" + e.message, "warn"); }
        };
        $("#set-pomo-int").oninput = (ev) => {
            $("#set-pomo-int-val").textContent = ev.target.value + " 秒";
        };
        $("#set-pomo-int").onchange = async (ev) => {
            try {
                await api("/api/settings", "POST", { pomo_interval: ev.target.value });
                reloadPomo(); say("✅ 每张停留 " + ev.target.value + " 秒", "ok");
            } catch (e) { say("保存失败：" + e.message, "warn"); }
        };
        $("#set-pomo-dim").oninput = (ev) => {
            $("#set-pomo-dim-val").textContent = Math.round(ev.target.value * 100) + "%";
        };
        $("#set-pomo-dim").onchange = async (ev) => {
            try {
                await api("/api/settings", "POST", { pomo_dim: ev.target.value });
                reloadPomo(); say("✅ 遮罩 " + Math.round(ev.target.value * 100) + "%", "ok");
            } catch (e) { say("保存失败：" + e.message, "warn"); }
        };
        $("#set-pomo-show").onclick = async (ev) => {
            const b = ev.target.closest("button[data-show]");
            if (!b) return;
            container.querySelectorAll("#set-pomo-show button").forEach(x =>
                x.classList.toggle("on", x === b));
            try {
                await api("/api/settings", "POST", { pomo_show: b.dataset.show });
                reloadPomo();
                say("✅ 轮播" + (b.dataset.show === "off" ? "已关闭"
                    : (b.dataset.show === "full" ? "只在全屏显示" : "卡片与全屏都显示")), "ok");
            } catch (e) { say("保存失败：" + e.message, "warn"); }
        };
    }

    function toast(msg) {
        const t = document.createElement("div");
        t.className = "fs-toast";
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 1800);
    }

    // 浏览器端压缩：手机原图 3-5MB，直接传既慢又可能撞上限。
    // 压到最长边 1920 / JPEG q0.85 后通常 < 500KB，做背景足够清晰。
    function compress(file) {
        return new Promise((resolve, reject) => {
            const fr = new FileReader();
            fr.onerror = () => reject(new Error("读取文件失败"));
            fr.onload = () => {
                const img = new Image();
                img.onerror = () => reject(new Error("这不是有效的图片"));
                img.onload = () => {
                    const MAX = 1920;
                    let w = img.width, h = img.height;
                    const scale = Math.min(1, MAX / Math.max(w, h));
                    w = Math.round(w * scale); h = Math.round(h * scale);
                    const cv = document.createElement("canvas");
                    cv.width = w; cv.height = h;
                    cv.getContext("2d").drawImage(img, 0, 0, w, h);
                    // PNG 截图带透明通道，转 JPEG 会变黑底，所以 PNG 保留原格式
                    const isPng = /image\\/png/.test(file.type);
                    resolve(cv.toDataURL(isPng ? "image/png" : "image/jpeg", isPng ? undefined : 0.85));
                };
                img.src = fr.result;
            };
            fr.readAsDataURL(file);
        });
    }

    function bind(container) {
        const $ = (s) => container.querySelector(s);

        $("#set-key-save").onclick = async () => {
            const v = $("#set-key").value.trim();
            if (!v) { toast("请先填入 API Key"); return; }
            try {
                const d = await api("/api/settings/apikey", "POST", { api_key: v });
                $("#set-key").value = "";
                $("#set-key").placeholder = "已设置 " + d.llm.key_hint + "，留空 = 不修改";
                status($("#set-llm-status"), "✅ 密钥已保存（" + d.llm.key_hint + "）", "ok");
                toast("API Key 已保存");
            } catch (e) { status($("#set-llm-status"), "保存失败：" + e.message, "warn"); }
        };
        $("#set-key-clear").onclick = async () => {
            if (!confirm("清除已保存的 API Key？清除后 AI 批改与错题解析会不可用。")) return;
            try {
                const d = await api("/api/settings/apikey", "POST", { clear_key: true });
                $("#set-key").placeholder = "尚未设置";
                status($("#set-llm-status"), "⚠ 密钥已清除", "warn");
            } catch (e) { status($("#set-llm-status"), "清除失败：" + e.message, "warn"); }
        };
        $("#set-model-save").onclick = async () => {
            const m = $("#set-model").value.trim();
            if (!m) { toast("请填入模型名"); return; }
            try {
                const d = await api("/api/settings/apikey", "POST", { model: m });
                status($("#set-llm-status"), "✅ 已切换到模型 " + d.llm.model, "ok");
                toast("模型已切换");
            } catch (e) { status($("#set-llm-status"), "保存失败：" + e.message, "warn"); }
        };
        $("#set-quota-save").onclick = async () => {
            try {
                await api("/api/settings", "POST", {
                    new_per_day: $("#set-new").value, reviews_per_day: $("#set-review").value,
                });
                status($("#set-quota-status"), "✅ 已保存，下一轮组题生效", "ok");
                toast("每日数量已保存");
            } catch (e) { status($("#set-quota-status"), "保存失败：" + e.message, "warn"); }
        };
        $("#set-extra-save").onclick = async () => {
            const v = parseInt($("#set-extra").value, 10);
            if (!Number.isFinite(v) || v < 1 || v > 100) {
                status($("#set-extra-status"), "请填 1~100 之间的整数", "warn");
                return;
            }
            try {
                await api("/api/settings", "POST", { flash_extra_count: v });
                status($("#set-extra-status"), "✅ 已保存", "ok");
                // 闪卡页头部那个「🔁 再来一组（N 张）」立刻跟着改口径（跨 IIFE 走事件，
                // 与 keys-changed 一个套路）——他刚改的数，回去就该看见
                try { document.dispatchEvent(new CustomEvent("kaoyan:extra-count", { detail: v })); } catch (e) {}
            } catch (e) { status($("#set-extra-status"), "保存失败：" + e.message, "warn"); }
        };
        $("#set-theme").onclick = async (ev) => {
            const b = ev.target.closest("button[data-theme]");
            if (!b) return;
            const t = b.dataset.theme;
            applyTheme(t);
            markTheme(container, t);
            try {
                localStorage.setItem(LS_THEME, t);
                await api("/api/settings", "POST", { theme: t });
            } catch (e) { toast("主题已切换（未能保存到服务端）"); }
        };
        $("#set-think").onclick = async (ev) => {
            const b = ev.target.closest("button[data-think]");
            if (!b) return;
            const quick = b.dataset.think === "quick";
            container.querySelectorAll("#set-think button").forEach(x =>
                x.classList.toggle("on", x === b));
            try {
                await api("/api/settings", "POST", { explain_quick: quick ? "on" : "off" });
                status($("#set-think-status"), quick
                    ? "✅ 以后默认关掉思考（更快）" : "✅ 以后默认深度思考（更细，稍慢）", "ok");
            } catch (e) { status($("#set-think-status"), "保存失败：" + e.message, "warn"); }
        };
        // ---- AI 提示词：由你自己写（2026-09-21 用户要求「别替我写提示词」）----
        // 留空 = 服务端用内置的极简默认（只有一句角色 + 公式格式），我不再往你的
        // 提示词后面追加任何要求；「恢复内置默认」就是把这一项清空。
        function wirePrompt(prefix, key) {
            const ta = $("#" + prefix);
            const save = $("#" + prefix + "-save");
            const reset = $("#" + prefix + "-reset");
            const st = $("#" + prefix + "-status");
            if (!ta) return;
            if (save) save.onclick = async () => {
                const body = {}; body[key] = ta.value;
                try {
                    await api("/api/settings", "POST", body);
                    status(st, ta.value.trim() ? "✅ 已保存（完全按你写的来）" : "已清空 → 用内置极简默认", "ok");
                } catch (e) { status(st, "保存失败：" + e.message, "warn"); }
            };
            if (reset) reset.onclick = async () => {
                const body = {}; body[key] = "";
                try {
                    await api("/api/settings", "POST", body);
                    ta.value = "";
                    status(st, "已恢复内置默认（只保留角色与公式格式）", "ok");
                } catch (e) { status(st, "恢复失败：" + e.message, "warn"); }
            };
        }
        wirePrompt("set-prompt-exp", "prompt_explain");
        wirePrompt("set-prompt-qa", "prompt_note_qa");
        $("#set-fx").onclick = async (ev) => {
            const b = ev.target.closest("button[data-fx]");
            if (!b) return;
            const v = b.dataset.fx;
            container.querySelectorAll("#set-fx button").forEach(x =>
                x.classList.toggle("on", x === b));
            try {
                await api("/api/settings", "POST", { mouse_fx: v });
                // 立刻生效，不用刷新整页
                if (globalThis.__mouseFxReload) globalThis.__mouseFxReload();
                status($("#set-fx-status"), v === "on" ? "✅ 已开启" : "已关闭（随时可以再开）", "ok");
            } catch (e) { status($("#set-fx-status"), "保存失败：" + e.message, "warn"); }
        };
        $("#set-bg-pick").onclick = () => $("#set-bg-file").click();
        $("#set-bg-file").onchange = async (ev) => {
            const f = ev.target.files && ev.target.files[0];
            if (!f) return;
            status($("#set-bg-status"), "正在压缩并上传…");
            try {
                const dataUrl = await compress(f);
                const kb = Math.round(dataUrl.length * 0.75 / 1024);
                const d = await api("/api/settings/background", "POST", { image: dataUrl });
                applyBg(d.background);
                const pv = $("#set-bg-preview");
                pv.classList.add("on");
                pv.style.backgroundImage = 'url("' + API + "/api/notes/asset?path="
                    + encodeURIComponent(d.background) + '&t=' + Date.now() + '")';
                try { localStorage.setItem(LS_BG, d.background); } catch (e) {}
                status($("#set-bg-status"), "✅ 已应用（压缩后约 " + kb + "KB）", "ok");
            } catch (e) {
                status($("#set-bg-status"), "上传失败：" + e.message, "warn");
            } finally { ev.target.value = ""; }
        };
        $("#set-bg-clear").onclick = async () => {
            try {
                await api("/api/settings/background/clear", "POST", {});
                applyBg("");
                $("#set-bg-preview").classList.remove("on");
                try { localStorage.setItem(LS_BG, ""); } catch (e) {}
                status($("#set-bg-status"), "已移除背景图", "ok");
            } catch (e) { status($("#set-bg-status"), "移除失败：" + e.message, "warn"); }
        };
        $("#set-bg-op").oninput = (ev) => {
            const v = ev.target.value;
            $("#set-bg-op-val").textContent = Math.round(v * 100) + "%";
            applyBgOpacity(v);
            try { localStorage.setItem(LS_BG_OP, v); } catch (e) {}
        };
        $("#set-bg-op").onchange = async (ev) => {
            try { await api("/api/settings", "POST", { bg_opacity: ev.target.value }); } catch (e) {}
        };
        // 📚 学科管理：入口按钮 + 摘要。实际列表/编辑/新增/删除全在 BOOT_JS 的
        // globalThis.__bootManager（注入顺序在 NAV_JS 之后，点击时必然已就绪）。
        const refreshSubjSummary = () => {
            const box = $("#set-subj-summary");
            const cnt = $("#set-subj-count");
            if (!box) return;
            fetch(API + "/api/subjects").then(r => r.json()).then((d) => {
                const list = (d && d.payload && d.payload.subjects) || [];
                box.textContent = list.length
                    ? list.map(s => (s.name || s.id) + "（" + ((s.subs || []).length) + " 子科）").join(" · ")
                    : "尚未建库，点「＋ 新增学科」开始第一门。";
                if (cnt) { cnt.textContent = list.length ? (list.length + " 门") : ""; }
            }).catch(() => {
                box.textContent = "读取学科失败";
            });
        };
        const subjOpen = $("#set-subj-open");
        if (subjOpen) subjOpen.onclick = () => {
            if (globalThis.__bootManager) globalThis.__bootManager.open(container);
            else toast("学科管理组件尚未就绪，稍后再试");
        };
        const subjAdd = $("#set-subj-add");
        if (subjAdd) subjAdd.onclick = () => {
            if (globalThis.__bootManager) globalThis.__bootManager.edit(null, container);
            else toast("学科管理组件尚未就绪，稍后再试");
        };
        refreshSubjSummary();
    }

    (window.__pageRenderers = window.__pageRenderers || {});
    (window.__pageRenderers.settings = window.__pageRenderers.settings || []).push(function () {
        const box = document.getElementById("settings-root");
        if (box) render(box);
    });
})();
'''


# ---------------------------------------------------------------------------
# 建库引导 + 学科管理（BOOT_JS）
#
# 启动时查 /api/bootstrap/status：首次无 subjects.json（need_bootstrap=true）
# 就全屏引导建库。引导分四步：欢迎 → 选方式（模板多选 / 自定义学科）→
# 逐科配置（参考书、子科体系、笔记布局）→ 确认建库。确认后 POST
# /api/bootstrap/create，成功即刷新页面进入大盘。
#
# 学科管理浮窗（globalThis.__bootManager.open()）供设置页「📚 学科管理」
# 分组调用：列出现有学科，可新增（走 /api/subjects/add）、编辑（update，
# 也用于扩充某科的知识体系）、删除（delete，只删配置不动笔记/数据）。
# 两者共用 subjectForm() 渲染同一套学科编辑表单，保证口径一致。
# ============================================================
BOOT_JS = '''
// ============================================================
// 建库引导 + 学科管理（2026-09-19 泛化「任意学科」）
//
// 启动时查 /api/bootstrap/status：首次无 subjects.json 就全屏引导建库。
// 设置页的「📚 学科管理」分组通过 globalThis.__bootManager.open() 打开浮窗。
// ============================================================
(function () {
    const API = location.protocol.startsWith('http') ? location.origin : "http://localhost:8080";

    // ---- 基础工具 ----
    function esc(s) {
        const d = document.createElement("div");
        d.textContent = s == null ? "" : String(s);
        return d.innerHTML;
    }
    function num(v, def) {
        const n = Number(v);
        return Number.isFinite(n) && n > 0 ? n : (def || 100);
    }
    function parts(s) {
        return String(s || "").split(/[\\r\\n]+/).map(x => x.trim()).filter(Boolean);
    }
    const SWATCHES = ["#5B7C99", "#7FA8D9", "#C89B4A", "#B84A42", "#6F9A8D", "#8A6FA8", "#9A8FB8", "#66726F"];

    // ---- 学科表单组件 ----
    // subjectForm(host, subj, opts) → { get(), setEnabled(e) }
    // subj: {id?, name, short, color, total_score, notes_dir, method, note_layout, books[], subs[]}
    // opts: { tplName?: 模板角标文案, removable?: 可删除（引导/管理列表用） }
    // 返回 get() 读取当前表单内容；表单数据在 DOM 里（data-f / data-b / data-s）。
    function subjectForm(host, subj, opts) {
        opts = opts || {};
        const s = subj || {};
        const books = Array.isArray(s.books) ? s.books : [];
        const subs = Array.isArray(s.subs) ? s.subs : [];

        host.innerHTML = ''
            + '<div class="bf-card" data-role="form">'
            + '  <div class="bf-card-head">'
            + '    <span>📚 ' + (s.name || '新学科') + '</span>'
            + (opts.tplName ? '<span class="bf-tpl-tag">' + esc(opts.tplName) + '</span>' : '')
            + (opts.removable ? '<button class="bf-del" data-act="remove" title="移除" style="margin-left:auto;">✕</button>' : '')
            + '  </div>'
            + '  <div class="bf-grid">'
            + '    <div class="bf-field"><label>学科名称</label><input data-f="name" value="' + esc(s.name || '') + '" placeholder="如：数据结构"></div>'
            + '    <div class="bf-field"><label>简称（图表/侧边栏用）</label><input data-f="short" value="' + esc(s.short || '') + '" placeholder="如：408"></div>'
            + '    <div class="bf-field"><label>总分（规划每日占比用）</label><input data-f="total_score" type="number" min="1" max="500" value="' + (s.total_score || 100) + '"></div>'
            + '    <div class="bf-field"><label>笔记根目录（知识库下的文件夹）</label><input data-f="notes_dir" value="' + esc(s.notes_dir || '') + '" placeholder="如：Math"></div>'
            + '    <div class="bf-field wide"><label>配色</label><div class="bf-swatches" data-role="swatches"></div></div>'
            + '    <div class="bf-field wide"><label>学习方法 / 当前进度（给自己看的一句话）</label>'
            + '      <input data-f="method" value="' + esc(s.method || '') + '" placeholder="如：一轮复习 → 强化"></div>'
            + '    <div class="bf-field wide"><label>笔记布局说明（每科笔记怎么组织）</label>'
            + '      <textarea data-f="note_layout" placeholder="如：按子科目录分章建 md，文件名「第N章_标题.md」">' + esc(s.note_layout || '') + '</textarea></div>'
            + '  </div>'
            + '  <div class="bf-books">'
            + '    <div class="bf-card-head" style="font-size:.85rem;margin-top:4px;">📖 参考书'
            + '      <button class="bf-add" data-act="add-book" type="button">＋ 添加参考书</button></div>'
            + '    <div data-role="books"></div>'
            + '  </div>'
            + '  <div class="bf-subs">'
            + '    <div class="bf-card-head" style="font-size:.85rem;">🧩 子科 / 知识体系'
            + '      <button class="bf-add" data-act="add-sub" type="button">＋ 添加子科</button></div>'
            + '    <div class="boot-hint" style="margin:0 0 8px;">子科是学科内部的划分（如数学下的高数/线代）；'
            + '每个子科一个笔记目录、一个闪卡前缀，章节填它下面的知识点章节。建完随时可以在设置页扩充。</div>'
            + '    <div data-role="subs"></div>'
            + '  </div>'
            + '</div>';

        // 配色 swatch
        const swatchBox = host.querySelector('[data-role="swatches"]');
        let curColor = s.color || "#5B7C99";
        SWATCHES.forEach(function (c) {
            const b = document.createElement("button");
            b.type = "button";
            b.className = "bf-swatch" + (c === curColor ? " on" : "");
            b.style.background = c;
            b.title = c;
            b.dataset.color = c;
            b.addEventListener("click", function () {
                curColor = c;
                swatchBox.querySelectorAll(".bf-swatch").forEach(x => x.classList.remove("on"));
                b.classList.add("on");
            });
            swatchBox.appendChild(b);
        });

        // 参考书行
        const bookBox = host.querySelector('[data-role="books"]');
        function addBook(b) {
            b = b || {};
            const row = document.createElement("div");
            row.className = "bf-book";
            row.innerHTML = ''
                + '<input placeholder="书名" data-b="title" value="' + esc(b.title || '') + '">'
                + '<input placeholder="作者" data-b="author" value="' + esc(b.author || '') + '">'
                + '<input placeholder="阶段" data-b="phase" value="' + esc(b.phase || '') + '" title="基础 / 强化 / 真题…">'
                + '<input placeholder="备注" data-b="note" value="' + esc(b.note || '') + '">'
                + '<button class="bf-del" data-act="del-book" type="button" title="删除此行">✕</button>';
            bookBox.appendChild(row);
        }
        books.forEach(addBook);

        // 子科卡
        const subBox = host.querySelector('[data-role="subs"]');
        function addSub(su) {
            su = su || {};
            const card = document.createElement("div");
            card.className = "bf-sub";
            card.innerHTML = ''
                + '<div class="bf-sub-head">'
                + '  <input class="bf-sub-key" placeholder="key" data-s="key" value="' + esc(su.key || '') + '" title="子科标识（如 DS）">'
                + '  <input placeholder="名称" data-s="name" value="' + esc(su.name || '') + '" style="flex:1;">'
                + '  <input class="bf-sub-dir" placeholder="笔记目录" data-s="dir" value="' + esc(su.dir || '') + '">'
                + '  <input class="bf-sub-prefix" placeholder="闪卡前缀" data-s="prefix" value="' + esc(su.prefix || '') + '">'
                + '  <button class="bf-del" data-act="del-sub" type="button" title="删除子科">✕</button>'
                + '</div>'
                + '<textarea class="bf-chapters" data-s="chapters" placeholder="每行一个章节，如：&#10;绪论&#10;线性表">' + esc((su.chapters || []).join("\\n")) + '</textarea>'
                + '<div class="bf-sub-hint">章节每行一个；闪卡前缀格式建议「科号-子科」，如 408-DS</div>';
            subBox.appendChild(card);
        }
        subs.forEach(addSub);

        host.addEventListener("click", function (ev) {
            const t = ev.target.closest("button[data-act]");
            if (!t) return;
            if (t.dataset.act === "add-book") { addBook({}); }
            else if (t.dataset.act === "add-sub") { addSub({}); }
            else if (t.dataset.act === "del-book") { t.closest(".bf-book").remove(); }
            else if (t.dataset.act === "del-sub") { t.closest(".bf-sub").remove(); }
            else if (t.dataset.act === "remove" && opts.onRemove) { opts.onRemove(); }
        });

        // 读取表单
        function get() {
            const data = {
                name: (host.querySelector('[data-f="name"]') || {}).value || "",
                short: (host.querySelector('[data-f="short"]') || {}).value || "",
                total_score: num((host.querySelector('[data-f="total_score"]') || {}).value),
                notes_dir: (host.querySelector('[data-f="notes_dir"]') || {}).value || "",
                color: curColor,
                method: (host.querySelector('[data-f="method"]') || {}).value || "",
                note_layout: (host.querySelector('[data-f="note_layout"]') || {}).value || "",
                books: [],
                subs: [],
            };
            host.querySelectorAll(".bf-book").forEach(function (r) {
                const g = (k) => (r.querySelector('[data-b="' + k + '"]') || {}).value || "";
                data.books.push({ title: g("title"), author: g("author"), phase: g("phase"), note: g("note") });
            });
            host.querySelectorAll(".bf-sub").forEach(function (c) {
                const g = (k) => (c.querySelector('[data-s="' + k + '"]') || {}).value || "";
                data.subs.push({
                    key: g("key"), name: g("name"), dir: g("dir"), prefix: g("prefix"),
                    chapters: parts(g("chapters")),
                });
            });
            return data;
        }
        return { get: get };
    }

    // ---- 建库引导 ----
    // 状态：step 0 欢迎 / 1 选方式 / 2 逐科配置 / 3 确认建库
    let bootOv = null;
    let bootState = null;

    function bootStepDots(total, cur) {
        let h = "";
        for (let i = 0; i < total; i++) {
            const cls = i === cur ? "on" : (i < cur ? "done" : "");
            h += '<span class="boot-step-dot ' + cls + '"></span>';
        }
        return h;
    }

    function showBootstrap(status) {
        if (bootOv) return;
        const ov = document.createElement("div");
        ov.className = "boot-overlay";
        ov.innerHTML = '<div class="boot-card">'
            + '<div class="boot-head"><span class="boot-title">🚀 建库引导</span>'
            + '<div class="boot-steps" data-role="steps"></div></div>'
            + '<div class="boot-body" data-role="body"></div>'
            + '<div class="boot-foot"><button class="fs-btn" data-act="back" type="button">← 上一步</button>'
            + '<span class="boot-hint" data-role="tip"></span>'
            + '<span class="spacer"></span>'
            + '<button class="fs-btn" data-act="next" type="button">下一步 →</button></div>'
            + '<div class="boot-progress" data-role="progress" hidden>正在建库…</div></div>';
        document.body.appendChild(ov);

        bootState = {
            step: 0,
            mode: null,          // 'tpl' | 'custom'
            selectedTpl: [],     // 选中的模板
            subjects: [],        // [{tplId?, data, form?}]
            status: status || { templates: [], subjects: [] },
        };
        bootOv = ov;

        ov.addEventListener("click", function (ev) {
            const t = ev.target.closest("button[data-act]");
            if (!t) return;
            if (t.dataset.act === "next") bootNext();
            else if (t.dataset.act === "back") bootBack();
        });

        renderStep();
    }

    function bootTips(step) {
        return [
            "这个大盘不只属于考研——任何学科都能建库：参考书、子科体系、笔记、闪卡、每日计划全都能配。",
            "模板带好了参考书与章节体系，选完还能改；也可以从零自定义一门自己的学科。",
            "参考书、子科、章节都能增减；模板只是起点，改成你自己要的形态。",
            "建库会生成学科配置（subjects.json）、笔记目录与进度文件；完成后刷新进入大盘。",
        ][step] || "";
    }

    function renderStep() {
        const st = bootState;
        const steps = bootOv.querySelector('[data-role="steps"]');
        const body = bootOv.querySelector('[data-role="body"]');
        const tip = bootOv.querySelector('[data-role="tip"]');
        const backBtn = bootOv.querySelector('[data-act="back"]');
        const nextBtn = bootOv.querySelector('[data-act="next"]');
        steps.innerHTML = bootStepDots(4, st.step);
        tip.textContent = bootTips(st.step);
        backBtn.style.visibility = st.step === 0 ? "hidden" : "visible";
        nextBtn.style.visibility = st.step === 3 ? "hidden" : "visible";
        nextBtn.textContent = "下一步 →";

        if (st.step === 0) {
            body.innerHTML = ''
                + '<div class="boot-welcome">'
                + '  <div class="boot-logo">🏛️</div>'
                + '  <h3>欢迎使用「学习大盘」</h3>'
                + '  <p>一个把 <b>笔记 · 闪卡 · 知识图谱 · 每日计划 · 错题复盘</b> 串在一起的本地学习系统。'
                + '不只服务考研：任何学科都能在这里建库、记笔记、刷题、复盘。<br>'
                + '第一次使用需要先建库——告诉我们你学什么，剩下的体系我们来搭。</p>'
                + '  <div>'
                + '    <span class="boot-tag">📝 双链笔记</span>'
                + '    <span class="boot-tag">🧠 间隔重复闪卡</span>'
                + '    <span class="boot-tag">🗺️ 知识图谱</span>'
                + '    <span class="boot-tag">📅 每日计划</span>'
                + '    <span class="boot-tag">🔁 错题复盘</span>'
                + '  </div>'
                + '</div>';
            return;
        }

        if (st.step === 1) {
            const tpls = (st.status && st.status.templates) || [];
            let tplHtml = '';
            if (tpls.length) {
                tplHtml = '<div class="boot-modes" style="margin-bottom:10px;">'
                    + '<div class="boot-mode' + (st.mode === "tpl" ? " on" : "") + '" data-mode="tpl">'
                    + '  <div class="boot-mode-ico">📐</div><h4>从模板创建</h4>'
                    + '  <p>内置完整参考书与章节体系，选完即用，可再微调</p></div>'
                    + '<div class="boot-mode' + (st.mode === "custom" ? " on" : "") + '" data-mode="custom">'
                    + '  <div class="boot-mode-ico">🛠️</div><h4>从零自定义</h4>'
                    + '  <p>不依赖模板，完全按自己的学科形态搭建</p></div></div>'
                    + (st.mode === "tpl" ? '<div class="boot-hint" style="margin-bottom:8px;">选中的模板（可多选）：</div>'
                        + '<div class="boot-tpl-grid">'
                        + tpls.map(function (t, i) {
                            const on = st.selectedTpl.indexOf(t.id) >= 0;
                            const nSub = (t.subs || []).length;
                            const nBook = (t.books || []).length;
                            return '<div class="boot-tpl' + (on ? " on" : "") + '" data-tpl="' + esc(t.id) + '">'
                                + '<div class="boot-tpl-name"><span class="boot-tpl-color" style="background:' + esc(t.color || "#888") + ';"></span>'
                                + esc(t.name || t.id) + '</div>'
                                + '<div class="boot-tpl-meta">' + nSub + ' 个子科 · ' + nBook + ' 本参考书'
                                + (t.method ? '<br>' + esc(t.method) : '') + '</div></div>';
                        }).join('')
                        + '</div>' : '')
                    + (st.mode === "custom"
                        ? '<div class="boot-hint">下一步直接填写你的学科信息：名称、参考书、子科体系、笔记布局。<br>'
                        + '如果模板里有接近的，推荐选模板再改，比自己从零搭快很多。</div>' : '');
            } else {
                tplHtml = '<div class="boot-modes">'
                    + '<div class="boot-mode on" data-mode="custom">'
                    + '  <div class="boot-mode-ico">🛠️</div><h4>从零自定义</h4>'
                    + '  <p>服务端暂未提供模板，直接按自己的学科形态搭建</p></div></div>';
            }
            body.innerHTML = tplHtml;
            body.querySelectorAll(".boot-mode").forEach(function (m) {
                m.addEventListener("click", function () {
                    st.mode = m.dataset.mode;
                    if (st.mode === "tpl" && !st.selectedTpl.length && (st.status.templates || []).length) {
                        st.selectedTpl = [st.status.templates[0].id];
                    }
                    renderStep();
                });
            });
            body.querySelectorAll(".boot-tpl").forEach(function (c) {
                c.addEventListener("click", function () {
                    const id = c.dataset.tpl;
                    const i = st.selectedTpl.indexOf(id);
                    if (i >= 0) st.selectedTpl.splice(i, 1);
                    else st.selectedTpl.push(id);
                    c.classList.toggle("on", st.selectedTpl.indexOf(id) >= 0);
                });
            });
            return;
        }

        if (st.step === 2) {
            // 组装 subjects 列表（模板选中 → 展开；custom 模式 → 空表单）
            const rebuild = st.subjects.length === 0;
            if (rebuild) {
                st.subjects = [];
                if (st.mode === "tpl") {
                    const tpls = (st.status.templates) || [];
                    st.selectedTpl.forEach(function (id) {
                        const t = tpls.find(x => x.id === id || x.template === id);
                        if (t) {
                            st.subjects.push({
                                tplId: id,
                                data: JSON.parse(JSON.stringify(t)),
                            });
                        }
                    });
                }
                if (st.mode === "custom" || !st.subjects.length) {
                    st.subjects.push({ tplId: null, data: {} });
                }
            }
            body.innerHTML = '';
            st.subjects.forEach(function (item, idx) {
                const card = document.createElement("div");
                card.dataset.idx = idx;
                body.appendChild(card);
                const tpl = item.tplId
                    ? ((st.status.templates || []).find(x => x.id === item.tplId || x.template === item.tplId) || {})
                    : null;
                const form = subjectForm(card, item.data, {
                    tplName: tpl ? ("模板 · " + (tpl.name || item.tplId)) : "自定义学科",
                    removable: true,
                    onRemove: function () {
                        st.subjects.splice(idx, 1);
                        renderStep();
                    },
                });
                item.form = form;
            });
            return;
        }

        if (st.step === 3) {
            // 读取表单 → 摘要
            const items = st.subjects.map(function (item) {
                const d = item.form.get();
                return { tplId: item.tplId, d: d };
            });
            const ok = items.every(function (it) {
                return (it.d.name || it.d.short) && (it.d.notes_dir || it.tplId);
            });
            body.innerHTML = ''
                + '<div class="boot-hint" style="margin-bottom:10px;">确认建库内容。将创建以下学科的配置、笔记目录与进度文件：</div>'
                + '<div class="boot-summary">'
                + items.map(function (it, i) {
                    return '<div class="boot-sum-item">'
                        + '<span class="boot-tpl-color" style="background:' + esc(it.d.color || "#888") + ';"></span>'
                        + '<div style="flex:1;">'
                        + '  <div class="boot-sum-name">' + esc(it.d.name || it.d.short || ("学科" + (i + 1))) + '</div>'
                        + '  <div class="boot-sum-meta">目录 <code>' + esc(it.d.notes_dir || "（未填）") + '</code>'
                        + ' · ' + it.d.subs.length + ' 个子科 · ' + it.d.books.length + ' 本参考书 · 总分 ' + it.d.total_score + '</div>'
                        + '</div></div>';
                }).join('')
                + '</div>'
                + (ok ? '' : '<div class="boot-hint" style="margin-top:10px;color:var(--zhusha-lt);">⚠ 有学科缺名称或笔记目录，'
                    + '请返回上一步补全（模板学科可留空目录，会自动使用模板目录）。</div>');
            if (ok) {
                nextBtn.style.visibility = "visible";
                nextBtn.textContent = "🚀 完成建库";
                nextBtn.onclick = function () { doBootstrap(items); };
            } else {
                nextBtn.style.visibility = "visible";
                nextBtn.textContent = "返回修改";
                nextBtn.onclick = function () { bootState.step = 2; renderStep(); };
            }
            return;
        }
    }

    function bootNext() {
        const st = bootState;
        if (st.step === 1) {
            if (st.mode === "tpl" && !st.selectedTpl.length) { toast("至少选一个模板，或改选「从零自定义」"); return; }
        }
        st.step += 1;
        renderStep();
    }

    function bootBack() {
        if (bootState.step === 0) return;
        bootState.step -= 1;
        renderStep();
    }

    // 建库动作
    function doBootstrap(items) {
        const ov = bootOv;
        const prog = ov.querySelector('[data-role="progress"]');
        prog.hidden = false;
        const payload = {
            subjects: items.map(function (it) {
                if (it.tplId) return { template: it.tplId, custom: it.d };
                return { custom: it.d };
            }),
        };
        fetch(API + "/api/bootstrap/create", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        }).then(function (r) { return r.json(); }).then(function (d) {
            if (!d.ok) { prog.hidden = true; toast("建库失败：" + (d.error || "")); return; }
            toast("✅ 建库成功：" + d.added.join("、") + (d.skipped.length ? "；已存在跳过：" + d.skipped.join("、") : ""));
            setTimeout(function () { location.reload(); }, 1200);
        }).catch(function (e) {
            prog.hidden = true;
            toast("建库失败：" + e.message);
        });
    }

    // ---- 学科管理浮窗（设置页「📚 学科管理」分组调用）----
    const manager = {
        open: function (anchorContainer) {
            const pageEl = anchorContainer && anchorContainer.closest
                ? (anchorContainer.closest('.page[data-page="settings"]') || document.body)
                : document.body;
            let ov = document.getElementById("subj-overlay");
            if (!ov) {
                ov = document.createElement("div");
                ov.id = "subj-overlay";
                ov.className = "sk-overlay";
                ov.innerHTML = '<div class="sk-box">'
                    + '<div class="sk-head"><span class="sk-title">📚 学科管理</span>'
                    + '<button class="rev-modal-close" data-act="close" type="button" title="关闭（Esc）">✕</button></div>'
                    + '<div class="sk-body subj-body"></div></div>';
                ov.addEventListener("click", function (ev) {
                    const t = ev.target.closest("[data-act]");
                    if (t && t.dataset.act === "close") ov.hidden = true;
                    else if (ev.target === ov) ov.hidden = true;
                    else if (t && t.dataset.act === "add") manager.edit(null, pageEl);
                    else if (t && t.dataset.act === "edit") manager.edit(t.dataset.id, pageEl);
                    else if (t && t.dataset.act === "del") manager.del(t.dataset.id);
                });
                document.addEventListener("keydown", function esc(e) {
                    if (e.key === "Escape" && ov.isConnected && !ov.hidden) { ov.hidden = true; }
                }, true);
            }
            if (ov.parentNode !== pageEl) pageEl.appendChild(ov);
            ov.hidden = false;
            manager.reload();
        },

        reload: function () {
            const ov = document.getElementById("subj-overlay");
            if (!ov || ov.hidden) return;
            const body = ov.querySelector(".subj-body");
            body.innerHTML = '<div class="boot-hint">正在读取学科…</div>';
            fetch(API + "/api/subjects").then(function (r) { return r.json(); }).then(function (d) {
                if (!d.ok || !d.payload) { body.innerHTML = '<div class="boot-hint">读取失败</div>'; return; }
                const list = d.payload.subjects || [];
                body.innerHTML = ''
                    + '<div class="boot-hint" style="margin-bottom:10px;">共 ' + list.length + ' 门学科。'
                    + '新增学科会在知识库下建笔记目录；删除只删配置，不碰笔记与数据。</div>'
                    + '<div class="boot-summary">'
                    + list.map(function (s) {
                        return '<div class="boot-sum-item">'
                            + '<span class="boot-tpl-color" style="background:' + esc(s.color || "#888") + ';"></span>'
                            + '<div style="flex:1;">'
                            + '  <div class="boot-sum-name">' + esc(s.name) + ' <span class="boot-hint">(' + esc(s.short) + ')</span></div>'
                            + '  <div class="boot-sum-meta">目录 <code>' + esc(s.notes_dir || "—") + '</code>'
                            + ' · ' + (s.subs || []).length + ' 个子科 · ' + (s.books || []).length + ' 本参考书</div>'
                            + '</div>'
                            + '<button class="fs-btn" data-act="edit" data-id="' + esc(s.id) + '" type="button">编辑 / 扩充</button>'
                            + '<button class="fs-btn" data-act="del" data-id="' + esc(s.id) + '" type="button" style="color:var(--zhusha-lt);">删除</button>'
                            + '</div>';
                    }).join('')
                    + '</div>'
                    + '<div style="margin-top:14px;"><button class="fs-btn" data-act="add" type="button">＋ 新增学科</button></div>';
            }).catch(function (e) {
                body.innerHTML = '<div class="boot-hint">读取失败：' + esc(e.message) + '</div>';
            });
        },

        // 编辑（id 为空 = 新增）。表单浮层挂同一 overlay 内。
        edit: function (id, pageEl) {
            const ov = document.getElementById("subj-overlay");
            const body = ov.querySelector(".subj-body");
            body.innerHTML = '<div class="boot-hint">正在加载…</div>';
            const done = function (subject) {
                body.innerHTML = '';
                const card = document.createElement("div");
                body.appendChild(card);
                const form = subjectForm(card, subject || {}, {
                    tplName: id ? "编辑学科 · 可扩充知识体系" : "新增学科",
                });
                body.insertAdjacentHTML("beforeend",
                    '<div style="margin-top:14px;display:flex;gap:10px;">'
                    + '<button class="fs-btn" data-act="save" type="button">' + (id ? "保存修改" : "创建学科") + '</button>'
                    + '<button class="fs-btn" data-act="cancel" type="button">取消</button></div>');
                body.querySelector('[data-act="save"]').onclick = function () {
                    const d = form.get();
                    if (!(d.name || d.short)) { toast("请填写学科名称"); return; }
                    const p = id ? { id: id, patch: d } : { subject: d };
                    fetch(API + (id ? "/api/subjects/update" : "/api/subjects/add"), {
                        method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(p),
                    }).then(function (r) { return r.json(); }).then(function (r2) {
                        if (!r2.ok) { toast("保存失败：" + (r2.error || "")); return; }
                        toast(id ? "✅ 已保存修改" : "✅ 学科已创建");
                        manager.reload();
                    }).catch(function (e) { toast("保存失败：" + e.message); });
                };
                body.querySelector('[data-act="cancel"]').onclick = function () { manager.reload(); };
            };
            if (!id) { done(null); return; }
            fetch(API + "/api/subjects").then(function (r) { return r.json(); }).then(function (d) {
                const s = (d.payload.subjects || []).find(function (x) { return x.id === id; });
                done(s || null);
            }).catch(function () { done(null); });
        },

        del: function (id) {
            if (!confirm("删除学科「" + id + "」的配置？笔记与数据不会删除。")) return;
            fetch(API + "/api/subjects/delete", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ id: id }),
            }).then(function (r) { return r.json(); }).then(function (d) {
                if (!d.ok) { toast("删除失败：" + (d.error || "")); return; }
                toast("已删除（笔记与数据保留）");
                manager.reload();
            }).catch(function (e) { toast("删除失败：" + e.message); });
        },
    };
    globalThis.__bootManager = manager;

    // ---- 启动检查：无库则全屏引导 ----
    fetch(API + "/api/bootstrap/status").then(function (r) { return r.json(); }).then(function (d) {
        if (d && d.ok && d.need_bootstrap) showBootstrap(d);
    }).catch(function () { /* 服务端未就绪时不打扰 */ });
})();
'''



# ---------------------------------------------------------------------------
# 番茄钟（大盘首页，2026-09-21）
#
# 与闪卡的学习计时是两套东西，故意不合并：
#   · 闪卡计时答的是「这段时间我有没有在学」→ 失焦必须停，否则数字造假
#   · 番茄钟答的是「离休息还有几分钟」    → 墙钟，切走了也得继续走、到点响铃
# 合并成一个开关，迟早会把其中一边做错。
# ---------------------------------------------------------------------------

POMO_CSS = '''
        /* ============================================================
           番茄钟（2026-09-21）。所有规则都挂在 .pm-card 上而不是
           「在总览页里」这个条件上——按全屏时同一个节点会被搬进 body 层的
           #pm-overlay，选择器要是依赖页面归属，搬过去就全裸了。
           ============================================================ */
        .pm-card { position: relative; overflow: hidden; margin-bottom: 20px;
            background: var(--bg-card); border: 1px solid var(--border-color);
            border-radius: var(--border-radius); padding: 18px 20px 20px;
            /* 压在照片上的那几行字走这一套变量，不直接用 --text-*：那三级灰是按
               纯色卡片调的（--hui 在卡片上够用），铺到照片上就等于隐形。
               取哪一套由 POMO_JS 按当前这张图的实测亮度挑，见「文字配色」段。 */
            --pm-fg:   var(--text-primary);
            --pm-fg2:  var(--text-secondary);
            --pm-fg3:  var(--text-muted);
            --pm-acc:  var(--xiang-lt);
            --pm-line: var(--border-color);
            --pm-halo: rgba(var(--mo-rgb), .6); }
        /* 轮播背景：两层叠着交叉淡入淡出，避免出现「换图时先黑一下」 */
        .pm-bg { position: absolute; inset: 0; z-index: 0; background-size: cover;
            background-position: center; background-repeat: no-repeat;
            opacity: 0; transition: opacity 1.6s ease; }
        .pm-bg.on { opacity: 1; }
        .pm-card:not(.pm-hasbg) .pm-bg { display: none; }
        /* 遮罩层：图再好看，也不能让 45:00 变成看不清的字。浓度由设置里的 --pm-dim 控；
           --pm-scrim 是「这张图一半亮一半暗、换哪种字色都有半边糊」时 POMO_JS 自己
           补的那一档（见 applyMood），平时是 0。 */
        .pm-dim { position: absolute; inset: 0; z-index: 1;
            background: linear-gradient(rgba(var(--mo-rgb), calc(var(--pm-dim, .35) + var(--pm-scrim, 0) + .1)),
                                        rgba(var(--mo-rgb), calc(var(--pm-dim, .35) + var(--pm-scrim, 0))));
            pointer-events: none; }
        .pm-card:not(.pm-hasbg) .pm-dim { display: none; }
        .pm-inner { position: relative; z-index: 2; }
        /* 有图时弱文字整体提一档：照片的细节会把灰字吃掉，--tao / --hui 那两级
           是按纯色底定的，压在图上就是看不清（2026-09-21 用户反馈）。
           这一档写死色值、不跟主题走——判断依据是「这张照片暗不暗」，跟大盘
           当前是深色还是浅色主题没关系：暗照片就得配亮字。 */
        .pm-hasbg { --pm-fg: #EDF1EF; --pm-fg2: #C7D0CD; --pm-fg3: #A7B3B0;
            --pm-acc: #E0C07E; --pm-line: rgba(229, 233, 231, .32);
            --pm-halo: rgba(8, 12, 12, .8); }
        /* 实测这张图亮部够亮 → 整套换成深字。同样写死：亮照片就得配深字。 */
        .pm-hasbg.pm-on-lit { --pm-fg: #121817; --pm-fg2: rgba(18, 24, 23, .87);
            --pm-fg3: rgba(18, 24, 23, .72); --pm-acc: #8A6414;
            --pm-line: rgba(18, 24, 23, .34); --pm-halo: rgba(255, 255, 255, .78); }
        /* 光晕只发给「直接压在图上」的那几个文本节点。挂在 .pm-inner 上一路继承，
           会连按钮和输入框一起描边——那些自己有实底，再套一圈白光反而脏。
           单靠一层 12px 柔光在照片上不够（字缘和底糊在一起），加一道 2px 硬影
           把字勾出来，缩到小窗大小也还立得住。 */
        .pm-hasbg .pm-h, .pm-hasbg .pm-day, .pm-hasbg .pm-remain,
        .pm-hasbg .pm-phase, .pm-hasbg .pm-plan, .pm-hasbg .pm-next,
        .pm-hasbg .pm-custom, .pm-hasbg .pm-esc, .pm-hasbg .pm-hint,
        .pm-hasbg .fs-full-toggle {
            text-shadow: 0 1px 2px var(--pm-halo), 0 0 16px var(--pm-halo);
            transition: color 1.2s ease; }
        /* 工具按钮本来有自己的 hover 过渡，别被上面那条 1.2s 拖慢 */
        .pm-hasbg .fs-full-toggle { transition: all .15s; }
        .pm-hasbg .pm-cnum { text-shadow: none; }   /* 输入框自己有实底 */

        .pm-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
        .pm-h { font-size: 1.1rem; font-weight: 600; font-family: var(--font-serif);
            letter-spacing: .04em; border-left: 3px solid var(--zhusha); padding-left: 10px;
            color: var(--pm-fg); margin: 0; }
        .pm-day { font-size: 0.76rem; color: var(--pm-fg2); }
        .pm-day b { color: var(--pm-acc); }
        .pm-tools { margin-left: auto; display: flex; gap: 8px; flex: none; }
        /* 工具按钮压在图上：底色透明、边框又只有 --bian 那么暗，照片一亮就整排
           消失。hover 也换成半透明底——原来那块 --bg-secondary 是实心深色，
           深色主题下会在浅照片上砸出一块黑砖。 */
        .pm-card .fs-full-toggle { color: var(--pm-fg2); border-color: var(--pm-line); }
        .pm-hasbg .fs-full-toggle:hover { color: var(--pm-fg); border-color: var(--pm-fg2);
            background: rgba(var(--mo-rgb), .45); }

        .pm-presets { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
        .pm-chip { font: inherit; font-size: 0.8rem; cursor: pointer; padding: 6px 14px;
            border-radius: 16px; border: 1px solid var(--border-color); background: var(--bg-primary);
            color: var(--text-secondary); transition: all .15s; }
        .pm-chip:hover { color: var(--text-primary); border-color: var(--zhusha); }
        .pm-chip.on { border-color: var(--zhusha); color: var(--xuan);
            background: rgba(var(--zhusha-rgb), .18); }
        .pm-chip .pm-chip-n { font-size: 0.68rem; color: var(--text-muted); margin-left: 6px; }
        .pm-chip.on .pm-chip-n { color: var(--tao); }
        /* 选中那颗是半透明朱砂底，照片会透上来——浅照片上白字就浮掉了。
           没选中的 chip 有实底（--bg-primary），不用管。 */
        .pm-hasbg.pm-on-lit .pm-chip.on { color: #121817; background: rgba(var(--zhusha-rgb), .34); }
        .pm-hasbg.pm-on-lit .pm-chip.on .pm-chip-n { color: rgba(18, 24, 23, .72); }
        /* 自己存的预设：chip 右边接一小截 ✕ 用来删。内置那五个不给这个口子
           ——删了没法恢复，不如不给。 */
        .pm-chipw { display: inline-flex; }
        .pm-chipw .pm-chip { border-top-right-radius: 0; border-bottom-right-radius: 0; }
        .pm-chip-x { font: inherit; font-size: 0.7rem; line-height: 1; cursor: pointer;
            padding: 0 10px; border: 1px solid var(--border-color); border-left: 0;
            background: var(--bg-primary); color: var(--text-muted);
            border-radius: 0 16px 16px 0; transition: all .15s; }
        .pm-chip-x:hover { color: var(--zhusha-lt); border-color: var(--zhusha);
            background: rgba(var(--zhusha-rgb), .12); }
        /* 触屏没有 hover，那截就常驻；鼠标设备上平时藏着，指过去才浮出来 */
        @media (hover: hover) {
            .pm-chip-x { opacity: 0; }
            .pm-chipw:hover .pm-chip-x { opacity: 1; }
        }

        .pm-body { display: flex; gap: 26px; align-items: center; flex-wrap: wrap; }
        .pm-dial { position: relative; width: 216px; height: 216px; flex: none; }
        .pm-svg { width: 100%; height: 100%; transform: rotate(-90deg); display: block; }
        .pm-ring-bg { fill: none; stroke: var(--border-color); stroke-width: 9; }
        .pm-hasbg .pm-ring-bg { stroke: var(--pm-line); }   /* 没跑完的那半圈也得看得见 */
        .pm-ring-fg { fill: none; stroke: var(--zhusha); stroke-width: 9; stroke-linecap: round;
            transition: stroke-dashoffset .35s linear, stroke .3s; }
        .pm-card.pm-brk .pm-ring-fg { stroke: var(--zhuqing); }
        .pm-card.pm-done .pm-ring-fg { stroke: var(--xiang); }
        .pm-dial-mid { position: absolute; inset: 0; display: flex; flex-direction: column;
            align-items: center; justify-content: center; gap: 4px; text-align: center; }
        .pm-remain { font-size: 2.5rem; font-weight: 700; line-height: 1.1;
            font-variant-numeric: tabular-nums; letter-spacing: .01em; color: var(--pm-fg); }
        .pm-phase { font-size: 0.76rem; color: var(--pm-fg2); }
        .pm-dots { display: flex; gap: 5px; margin-top: 4px; }
        .pm-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--border-color); }
        .pm-hasbg .pm-dot:not(.ok):not(.now) { background: var(--pm-fg3); }
        .pm-dot.ok { background: var(--zhuqing); }
        .pm-dot.now { background: var(--zhusha); box-shadow: 0 0 0 3px rgba(var(--zhusha-rgb), .18); }
        .pm-card.pm-pause .pm-dot.now { opacity: .5; }

        .pm-side { flex: 1; min-width: 232px; }
        .pm-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
        .pm-main-btn { border-color: var(--zhusha); color: var(--zhusha-lt);
            font-size: 0.95rem; padding: 9px 26px; }
        .pm-main-btn:hover { border-color: var(--zhusha-lt); background: rgba(var(--zhusha-rgb), .12); }
        .pm-card.pm-brk .pm-main-btn { border-color: var(--zhuqing); color: var(--zhuqing-lt); }
        .pm-plan { font-size: 0.82rem; color: var(--pm-fg); margin-bottom: 4px; }
        .pm-next { font-size: 0.74rem; color: var(--pm-fg3); min-height: 1.5em; margin-bottom: 12px; }
        .pm-custom { display: flex; gap: 6px; align-items: center; flex-wrap: wrap;
            font-size: 0.74rem; color: var(--pm-fg3); margin-bottom: 10px; }
        .pm-clabel { font-size: 0.72rem; color: var(--pm-fg3); margin-right: 2px; }
        .pm-cu { font-size: 0.7rem; color: var(--pm-fg3); }
        .pm-cnum { width: 54px; font: inherit; font-size: 0.78rem; padding: 4px 6px; text-align: center;
            background: var(--bg-primary); color: var(--text-primary);
            border: 1px solid var(--border-color); border-radius: 4px; }
        .pm-cnum:focus { outline: none; border-color: var(--dianqing); }
        /* 自定义那一行里两颗按钮（用这套 / ＋存为预设）收小一号，
           跟「自定义 45 分 专注 10 分 歇 2 轮」那排输入框一样高 */
        .pm-capply, .pm-csave { font-size: 0.74rem; padding: 4px 12px; }
        .pm-hint { font-size: 0.7rem; color: var(--pm-fg3); line-height: 1.75; }

        /* ---- 全屏层 ---- */
        body.pm-lock { overflow: hidden; }
        .pm-overlay { position: static; }
        .pm-overlay[hidden] { display: none; }
        .pm-card.pm-fs { position: fixed; inset: 0; z-index: 1200; margin: 0;
            border: none; border-radius: 0; background: var(--mo);
            display: flex; align-items: center; justify-content: center;
            padding: clamp(18px, 4vh, 56px); animation: pmIn .2s ease; }
        @keyframes pmIn { from { opacity: 0; } to { opacity: 1; } }
        .pm-fs .pm-inner { width: 100%; max-width: 900px; }
        .pm-fs .pm-body { flex-direction: column; gap: 26px; }
        .pm-fs .pm-dial { width: min(52vh, 520px); height: min(52vh, 520px); }
        .pm-fs .pm-remain { font-size: min(11vh, 108px); }
        .pm-fs .pm-phase { font-size: 1rem; }
        .pm-fs .pm-side { text-align: center; min-width: 0; }
        .pm-fs .pm-actions, .pm-fs .pm-presets { justify-content: center; }
        .pm-fs .pm-hint { display: none; }
        .pm-fs .pm-h { border-left: 0; padding-left: 0; font-size: 1.3rem; }
        .pm-fs .pm-esc { display: inline; }
        .pm-esc { display: none; font-size: 0.72rem; color: var(--pm-fg3); }
        /* 浏览器真全屏时（拿到 fullscreenElement）：让节点自己铺满，
           :fullscreen 的默认底色是黑，会盖掉我们的背景图，所以要显式 transparent */
        /* ---- 番茄钟浮动小窗（2026-09-21 晚）----
           全平台可用的那种小窗：拖动 + 调透明度，任意子页都在。
           系统级置顶窗口（Document PiP）与锁屏显示（Media Session）见 POMO_JS。 */
        .pm-mini { position: fixed; z-index: 1100; width: 152px; box-sizing: border-box;
            padding: 8px 10px 10px; border-radius: 12px; color: var(--xuan);
            background: rgba(var(--mo-rgb), .84); border: 1px solid var(--border-color);
            -webkit-backdrop-filter: blur(8px); backdrop-filter: blur(8px);
            box-shadow: 0 6px 22px rgba(0, 0, 0, .38);
            /* 透明度走变量：交互时临时提到 1，松手回到用户设定的值 */
            opacity: var(--pm-mini-op, .85); transition: opacity .18s, border-color .2s;
            -webkit-user-select: none; user-select: none; }
        .pm-mini[hidden] { display: none; }
        .pm-mini:hover, .pm-mini:focus-within { opacity: 1; }
        .pm-mini.pm-mini-brk { border-color: rgba(var(--zhuqing-rgb), .55); }
        .pm-mini.pm-pause { border-color: rgba(var(--xiang-rgb), .5); }
        /* touch-action 只关在拖拽把手上：整卡都关掉的话，里面的透明度滑杆
           在平板上就拨不动了（手指一动被当成拖窗）。 */
        .pm-mini-head { display: flex; align-items: center; gap: 5px; cursor: grab;
            touch-action: none; }
        .pm-mini-head:active { cursor: grabbing; }
        .pm-mini-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--zhusha); flex: none; }
        .pm-mini-brk .pm-mini-dot { background: var(--zhuqing); }
        .pm-mini.pm-pause .pm-mini-dot { background: var(--xiang); }
        .pm-mini-ph { flex: 1; font-size: 0.66rem; color: var(--tao); white-space: nowrap;
            overflow: hidden; text-overflow: ellipsis; }
        .pm-mini-x { font: inherit; font-size: 0.72rem; line-height: 1; cursor: pointer;
            padding: 1px 5px; background: none; border: 0; border-radius: 4px; color: var(--hui); }
        .pm-mini-x:hover { color: var(--xuan); background: rgba(var(--xuan-rgb), .12); }
        .pm-mini-time { font-size: 1.62rem; font-weight: 700; line-height: 1.15; margin: 3px 0 5px;
            font-variant-numeric: tabular-nums; letter-spacing: .02em; }
        .pm-mini-bar { height: 3px; border-radius: 2px; overflow: hidden;
            background: rgba(var(--xuan-rgb), .13); }
        .pm-mini-bar i { display: block; height: 100%; width: 0; background: var(--zhusha); }
        .pm-mini-brk .pm-mini-bar i { background: var(--zhuqing); }
        .pm-mini-row { display: flex; gap: 4px; margin-top: 8px; }
        .pm-mini-btn { flex: 1; font: inherit; font-size: 0.8rem; line-height: 1.5; cursor: pointer;
            padding: 5px 0; background: rgba(var(--xuan-rgb), .07); color: var(--tao);
            border: 1px solid var(--border-color); border-radius: 6px; }
        .pm-mini-btn:hover { color: var(--xuan); border-color: var(--dianqing); }
        .pm-mini-btn.go { flex: 1.5; color: var(--zhusha-lt); border-color: var(--zhusha); }
        .pm-mini-op { display: flex; align-items: center; gap: 6px; margin-top: 8px; }
        .pm-mini-op input { flex: 1; min-width: 0; height: 14px; accent-color: var(--dianqing); }
        .pm-mini-op span { min-width: 28px; text-align: right; font-size: 0.6rem; color: var(--hui); }
        /* PiP 窗口里那份：窗口本身自带背景，卡片不用再描边加阴影 */
        .pm-mini-pip { position: static; width: auto; border: 0; border-radius: 0;
            box-shadow: none; opacity: 1; background: var(--mo); }
        .pm-mini-pip, .pm-mini-pip * { touch-action: auto; }
        @media (max-width: 720px) {
            .pm-mini { width: 158px; padding: 9px 11px 11px; }
            .pm-mini-time { font-size: 1.7rem; }
            .pm-mini-btn { padding: 7px 0; }   /* 手指点得着 */
        }
        .pm-card:fullscreen { background: var(--mo); }
        .pm-card:-webkit-full-screen { background: var(--mo); }
        /* 原生全屏时浏览器会把容器涂成黑底，背景图得跟着铺满才不露黑边 */
        .pm-overlay:fullscreen { background: var(--mo); }
        .pm-overlay:-webkit-full-screen { background: var(--mo); }
'''


POMO_JS = '''
// ============================================================
// 番茄钟（2026-09-21，大盘首页）
//
// 四条设计约束，改之前先读：
//  1. **墙钟语义**：所有状态都换算成「这一段的结束时刻 endAt（epoch）」来存，
//     暂停时才存剩余量。这样刷新 / 断网 / 电脑睡眠醒来，表都对得上真实时间，
//     不会像「每 tick 减一秒」那样越走越慢。
//  2. **和闪卡计时相反**：闪卡的计时是「我有没有在学」，失焦就该停；
//     番茄钟答的是「离休息还有几分钟」，切走了也必须继续走、到点响铃。
//     所以这里**不监听** blur，别照着闪卡那边抄。
//  3. **全屏是搬家不是复制**：卡片节点在 #pm-slot 与 body 层的 #pm-overlay
//     之间移动（同一个 DOM 节点），计时器与背景轮播都不因搬家重置；
//     而 overlay 在 .page 之外，所以切到别的子页，番茄钟也还在跑、还看得见。
//  4. **状态在服务端，不在本机**（2026-09-21 晚）：电脑上开一轮、人走到平板
//     前接着看，两边必须是同一只表。写走 POST /api/pomodoro/state，每 12 秒
//     拉一次对齐；endAt 进出都按**服务端时钟**换算（skew），否则两台机器时间
//     差几分钟，另一台算出来的剩余时间就是错的。localStorage 只当首屏缓存。
// ============================================================
(function () {
    const API = location.protocol.startsWith('http') ? location.origin : "http://localhost:8080";
    const slot = document.getElementById("pm-slot");
    const overlay = document.getElementById("pm-overlay");
    if (!slot || !overlay) return;   // 容器没挂上来（比如产物被裁过）就整段静默退出

    const LS_PLAN = "kaoyan.pomo.plan.v1";    // 选中的方案 id / 自定义参数
    const LS_RUN  = "kaoyan.pomo.run.v1";     // 运行快照（pos / endAt / running…）
    const LS_DAY  = "kaoyan.pomo.day.v1";     // 今日完成数（按本地日期归零）
    const RING = 2 * Math.PI * 88;            // 与 CSS 里 r=88 的圆环一致
    const BASE_TITLE = document.title;

    // 预设。45+10×3 / 60+15×2 是「几轮」，90/120/180 是单段一次到底（brk=0
    // 就不生成休息段），别给 90 分钟硬塞一个收尾休息——那是在骗人多一段计划。
    const PRESETS = [
        { id: "45x3", label: "45 + 10 × 3", work: 45, brk: 10, rounds: 3, note: "三节 45 分钟，每节之间歇 10 分钟" },
        { id: "60x2", label: "60 + 15 × 2", work: 60, brk: 15, rounds: 2, note: "两节一小时，适合数学 / 408 整块刷题" },
        { id: "90",   label: "90 分钟",     work: 90, brk: 0,  rounds: 1, note: "一场模拟试卷的时长，中途不停" },
        { id: "120",  label: "120 分钟",    work: 120, brk: 0, rounds: 1, note: "半日计划的一个整块" },
        { id: "180",  label: "180 分钟",    work: 180, brk: 0, rounds: 1, note: "三小时连做：中途别指望表会停" }
    ];

    // ---- 小工具 ----------------------------------------------------------
    function esc(s) {
        const d = document.createElement("div");
        d.textContent = s == null ? "" : String(s);
        return d.innerHTML;
    }
    // 本模块自己的小提示：.fs-toast 的样式在 FLASH_CSS 里（那份一直都在），
    // 但 toast 函数不能跨模块借——POMO_JS 是独立 IIFE，写裸名会当场 ReferenceError
    // （2026-09-21 就是这里的 5 处调用被 test_pomodoro 抓出来的）。
    function toast(msg) {
        const t = document.createElement("div");
        t.className = "fs-toast";
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(function () { if (t.remove) t.remove(); }, 2600);
    }
    function pad(n) { return (n < 10 ? "0" : "") + n; }
    function fmtMs(ms) {
        const t = Math.max(0, Math.floor(ms / 1000));
        const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
        return h > 0 ? (h + ":" + pad(m) + ":" + pad(s)) : (pad(m) + ":" + pad(s));
    }
    function fmtMin(min) {
        const h = Math.floor(min / 60), m = min % 60;
        return (h > 0 ? h + " 小时" : "") + (h > 0 && m > 0 ? " " : "") + (m > 0 || h === 0 ? m + " 分" : "");
    }
    // ⚠️ 必须和服务端 localToday() 一致（都是「凌晨 4 点前算前一天」）。
    // 差一天就会真的坏事：下面 mergeToday 拿服务端那份 today 跟本地这份比，
    // 判定「服务端那份属于新的一天」时会把刚记上的成绩覆盖成 0。
    function todayKey() { return studyDay(); }
    function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
    function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
    function lsDel(k) { try { localStorage.removeItem(k); } catch (e) {} }
    function readJson(k) { try { return JSON.parse(lsGet(k) || "null"); } catch (e) { return null; } }

    // ---- 提示音（自带一套，不蹭闪卡的答题音效）--------------------------
    // 不合并的理由：答题音效被静音很常见（怕吵），但番茄钟到点不响就等于没有
    // 番茄钟。两套各留一个开关，互不牵连。
    // 开关状态存服务端 config（pomo_sound），而不是 localStorage：平板上关掉提示音，
    // 电脑这边也该知道——和「早间回顾」把打卡搬进 SQLite 是同一个理由。
    let actx = null, muted = false;
    function ac() {
        if (!actx) {
            const AC = window.AudioContext || window.webkitAudioContext;
            if (!AC) return null;
            actx = new AC();
        }
        if (actx.state === "suspended") { try { actx.resume(); } catch (e) {} }
        return actx;
    }
    function tone(freq, delay, dur, gain, type) {
        const c = ac(); if (!c) return;
        try {
            const t0 = c.currentTime + delay;
            const osc = c.createOscillator(), g = c.createGain();
            osc.type = type || "triangle"; osc.frequency.value = freq;
            g.gain.setValueAtTime(0.0001, t0);
            g.gain.exponentialRampToValueAtTime(gain || 0.18, t0 + 0.02);
            g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
            osc.connect(g); g.connect(c.destination);
            osc.start(t0); osc.stop(t0 + dur + 0.05);
        } catch (e) { /* 音频不可用不该影响计时 */ }
    }
    // 专注段结束：三声渐强，人在隔壁房也该听见；休息段结束：两声轻促，不催命。
    function chime(kind) {
        if (muted) return;
        if (kind === "work") {
            [880.00, 1108.73, 1318.51].forEach(function (f, i) { tone(f, i * 0.17, 0.45, 0.2); });
        } else if (kind === "brk") {
            tone(659.25, 0, 0.18, 0.16, "sine"); tone(987.77, 0.18, 0.3, 0.18);
        } else {
            [523.25, 659.25, 783.99, 1046.5].forEach(function (f, i) {
                tone(f, i * 0.09, 0.32, 0.16, i < 2 ? "sine" : "triangle");
            });
        }
    }

    // ---- 计划与状态 ------------------------------------------------------
    let plan = null;            // {id, label, work, brk, rounds, note, segs:[{kind,min,round}], totalMin}
    let pos = 0;                // 当前段下标
    let running = false;
    let startedOnce = false;    // 这一轮有没有按过开始：决定按钮写「开始」还是「继续」
    let endAt = 0;              // running 时：本段结束的 epoch 毫秒
    let remain = 0;             // 暂停时：本段剩余毫秒
    let full = false;           // 全屏（沉浸）中
    let expiredNote = "";       // 页面关闭期间走完了整轮的提示
    const CUSTOM = { id: "custom", label: "自定义", note: "自己定的节奏" };

    function buildPlan(base) {
        const work = Math.max(1, Math.round(Number(base.work) || 25));
        const brk = Math.max(0, Math.round(Number(base.brk) || 0));
        const rounds = Math.max(1, Math.min(24, Math.round(Number(base.rounds) || 1)));
        const segs = [];
        for (let r = 1; r <= rounds; r++) {
            segs.push({ kind: "work", min: work, round: r });
            if (brk > 0) segs.push({ kind: "brk", min: brk, round: r });
        }
        return {
            id: base.id, label: base.label || "自定义", work: work, brk: brk, rounds: rounds,
            note: base.note || "", segs: segs,
            totalMin: segs.reduce(function (a, s) { return a + s.min; }, 0),
            workMin: work * rounds
        };
    }
    // ---- 自己存的预设（2026-09-17）----
    // 内置那五个写死在 PRESETS 里；这一份是用户拿「＋ 存为预设」攒的，只存本机。
    // 没往服务端放：番茄钟的偏好本来就走 localStorage（LS_PLAN / LS_RUN / MINI_KEY），
    // 预设属于「这台机器上我顺手的节奏」，跟跨设备的计时状态不是一回事。
    const MY_KEY = "kaoyan.pomo.presets.v1";
    const MY_MAX = 12;                       // 上限：别让一排 chip 长到换三行
    let myPresets = (function () {
        const a = readJson(MY_KEY);
        if (!Array.isArray(a)) return [];
        // 存进来的东西不可信（手改过 localStorage / 旧版本写的），逐条验一遍
        return a.filter(function (p) {
            return p && typeof p.id === "string" && p.id.indexOf("my:") === 0
                && Number(p.work) > 0;
        }).slice(0, MY_MAX);
    })();
    function saveMy() { lsSet(MY_KEY, JSON.stringify(myPresets)); }
    function allPresets() { return PRESETS.concat(myPresets); }
    // 标签跟内置那五个一个长法：90 分钟 / 45 + 10 × 3
    function myLabel(work, brk, rounds) {
        return (brk > 0 || rounds > 1)
            ? work + (brk > 0 ? " + " + brk : "") + (rounds > 1 ? " × " + rounds : "")
            : work + " 分钟";
    }
    // id 由参数算出来（不是随机数）：同样的节奏存两次会撞成同一个，
    // 于是「重复保存」天然变成「跳到已有那个」，不会攒出一排一模一样的 chip。
    function myId(work, brk, rounds) { return "my:" + work + "x" + brk + "x" + rounds; }

    function presetById(id) {
        for (const p of allPresets()) if (p.id === id) return p;
        return null;
    }
    function segMs(s) { return s.min * 60000; }
    function curSeg() { return pos < plan.segs.length ? plan.segs[pos] : null; }

    // ---- 今日成绩与近况 --------------------------------------------------
    // 服务端是唯一真相源（多设备同一份），localStorage 只当「首屏秒显」的缓存：
    // 打开页面时先用缓存把数字画出来，GET 回来再对齐，避免先看到 0 再跳一下。
    let day = { date: "", pomos: 0, min: 0 };
    let days = [];                 // 近 14 天 [{date, pomos, min}]，给首页数据条用
    function readDayCache() {
        const o = readJson(LS_DAY);
        if (o && o.date === todayKey()) return { date: o.date, pomos: o.pomos | 0, min: o.min | 0 };
        return { date: todayKey(), pomos: 0, min: 0 };
    }
    function applyDay(stat, list) {
        mergeToday(stat);
        if (Array.isArray(list)) days = list;
        lsSet(LS_DAY, JSON.stringify(day));
        if (typeof globalThis.__focusStripReload === "function") {
            try { globalThis.__focusStripReload(); } catch (e) {}
        }
    }
    // 今日成绩**只增不减**：刚跑完一段，本地先记上、POST 还在路上，这时若来一发
    // 读接口（每 12 秒一次的那个），服务端回的还是「没记上」的旧值——直接覆盖就
    // 会把那一笔抹掉，用户看到数字闪一下又掉回去。同一天取两边最大值即可，
    // 番茄数在一天里本来就不会减少。
    function mergeToday(stat) {
        if (!stat) return;
        const d = stat.date || todayKey();
        const p = stat.pomos | 0, m = stat.min | 0;
        if (d === todayKey() && day.date === todayKey()) {
            day = { date: todayKey(), pomos: Math.max(day.pomos, p), min: Math.max(day.min, m) };
        } else {
            day = { date: d, pomos: p, min: m };
        }
    }
    function credit(min) {
        // 本地先记上（表立刻对），服务端自增负责合并多设备——两端各记一次不会互相覆盖
        day = { date: todayKey(), pomos: day.pomos + 1, min: day.min + min };
        lsSet(LS_DAY, JSON.stringify(day));
        pushCredit(min);
    }
    function pushCredit(min) {
        fetch(API + "/api/pomodoro/credit", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ min: min, date: todayKey() })
        }).then(function (r) { return r.json(); }).then(function (d) {
            if (d && d.ok) { applyServerNow(d.server_now); applyDay(d.today_stat, d.days); paint(); }
        }).catch(function () { /* 离线也不影响本地这一轮，下次 GET 会对齐 */ });
    }

    // ---- 落盘 / 恢复 / 跨设备同步 ----------------------------------------
    // 四件事分开：
    //   snapshot()    —— 当前状态快照（不含时钟换算）
    //   saveRun()     —— 本地快照 + 写服务端（刷新秒恢复、多设备共用一只表）
    //   adoptRemote() —— 接受服务端版本（只在它比本地已知版本新时）
    //
    // ⚠️ 时间基准只有一条：**本机时钟**。内部所有比较（endAt / remain / catchUp）
    //    都走 Date.now()，只有「进出服务端」那两处做 ±skew 换算（写进去用服务端
    //    时钟，读出来换回本机时钟）。两边混用是踩过的坑：会差出一个 skew 的量，
    //    表现成「平板上的表比电脑慢 7 秒」。
    let skew = 0;                 // 服务端时钟 - 本机时钟
    let appliedUpdated = 0;       // 已知的服务端版本号（server_updated）
    let pushing = 0;              // 正在写服务端：期间不采纳远端，免得被自己的回声打回去
    // ⚠️ 只有拿到**合法**的 server_now 才动 skew。老服务端 / 测试桩里没有这个字段时，
    //    若直接算 `undefined - Date.now()`，skew 会变成一个巨大的负数，全盘时间就废了。
    function applyServerNow(t) {
        const v = Number(t);
        if (Number.isFinite(v) && v > 0) skew = v - Date.now();
    }
    function snapshot() {
        const seg = curSeg();
        return {
            plan: { id: plan.id, work: plan.work, brk: plan.brk, rounds: plan.rounds, label: plan.label, note: plan.note },
            pos: pos, running: running, startedOnce: startedOnce,
            endAt: running ? endAt : 0,
            remain: running ? 0 : (seg ? Math.max(0, remain) : 0)
        };
    }
    function saveRun() {
        const s = snapshot();
        lsSet(LS_RUN, JSON.stringify(s));
        pushRun(s);
    }
    function pushRun(s) {
        pushing += 1;
        fetch(API + "/api/pomodoro/state", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ run: {
                plan: s.plan, pos: s.pos, running: s.running, started_once: s.startedOnce,
                end_at: s.running ? Math.round(s.endAt + skew) : 0,
                remain_ms: s.running ? 0 : Math.round(s.remain),
            } })
        }).then(function (r) { return r.json(); }).then(function (d) {
            pushing -= 1;
            if (d && d.ok) {
                applyServerNow(d.server_now);
                if (d.run && d.run.server_updated) appliedUpdated = d.run.server_updated;
            }
        }).catch(function () { pushing -= 1; });
    }
    function restoreLocal(s) {
        plan = buildPlan(s.plan);
        pos = Math.max(0, Math.min(plan.segs.length, s.pos | 0));
        running = !!s.running;
        const seg = curSeg();
        endAt = running ? (Number(s.endAt) || 0) : 0;
        remain = seg ? (running ? Math.max(0, endAt - Date.now()) : (Number(s.remain) || segMs(seg))) : 0;
        // 「开始过没有」要跟着恢复，否则刷新一次，暂停中的按钮就从「继续」退回「开始」
        startedOnce = !!s.startedOnce || running || pos > 0;
    }
    // 服务端版本 → 本地。返回是否真的采纳了。
    function adoptRemote(run, serverNow) {
        if (!run) return false;
        const up = Number(run.server_updated) || 0;
        if (up && up <= appliedUpdated) return false;      // 手里这份已经是新的
        appliedUpdated = up || appliedUpdated;
        plan = buildPlan(run.plan);
        pos = Math.max(0, Math.min(plan.segs.length, run.pos | 0));
        running = !!run.running;
        startedOnce = !!run.started_once || running || pos > 0;
        const seg = curSeg();
        if (running && seg) {
            endAt = (Number(run.end_at) || 0) - skew;      // 服务端时钟 → 本机时钟
            remain = Math.max(0, endAt - Date.now());
        } else {
            endAt = 0;
            remain = seg ? Math.max(0, Number(run.remain_ms) || segMs(seg)) : 0;
        }
        // 采纳的是「别处正在跑」的表：把该走完的段就地结算（本机不响铃，避免和
        // 那台设备一起叫两遍）
        if (running) catchUp(false);
        expiredNote = "";
        return true;
    }
    function syncState() {
        return fetch(API + "/api/pomodoro/state").then(function (r) { return r.json(); }).then(function (d) {
            if (!d || !d.ok) return false;
            applyServerNow(d.server_now);
            if (d.today_stat) mergeToday(Object.assign({}, d.today_stat, { date: d.today || todayKey() }));
            if (Array.isArray(d.days)) days = d.days;
            lsSet(LS_DAY, JSON.stringify(day));
            const took = (pushing === 0) ? adoptRemote(d.run, d.server_now) : false;
            if (took) { renderPresets(); paint(); }
            // 服务端还没有这只表（典型场景：本机刚升级，本地 localStorage 里那轮还在跑），
            // 而本机手上有一轮在跑/暂停 → 交上去，别的设备才看得见。
            // 只在服务端为空时做，所以不会覆盖另一台设备正在跑的表。
            if (!d.run && !took && pushing === 0 && startedOnce) saveRun();
            if (typeof globalThis.__focusStripReload === "function") {
                try { globalThis.__focusStripReload(); } catch (e) {}
            }
            return took;
        }).catch(function () { return false; });
    }
    globalThis.__pomoSync = syncState;     // 首页数据条 / 别的模块想立刻对齐时用

    function loadRun() {
        // 1) 本地缓存先顶上（刷新后立刻就是对的，不用等服务端）
        const o = readJson(LS_RUN);
        if (o && o.plan && o.plan.work > 0) {
            restoreLocal(o);
            if (running) { catchUp(true); }
        } else {
            const p = readJson(LS_PLAN);
            const hit = p && presetById(p.id) ? presetById(p.id) : (p && p.work ? p : PRESETS[0]);
            plan = buildPlan(hit);
            pos = 0; running = false; remain = segMs(plan.segs[0]); endAt = 0;
        }
        day = readDayCache();
        // 2) 再和服务端对齐（另一台设备开着的表就是靠这一步同步过来的）
        syncState();
    }

    // 走完当前段并推进。byClock=按表走完（要记账、要响）；skip=手动跳过（不记）。
    function finishSeg(byClock) {
        const s = curSeg();
        if (!s) return;
        if (byClock && s.kind === "work") credit(s.min);
        pos += 1;
        const nx = curSeg();
        if (!nx) { running = false; endAt = 0; remain = 0; if (byClock) chime("done"); return; }
        remain = segMs(nx);
        if (running) endAt = Date.now() + remain;
        if (byClock) chime(s.kind === "work" ? "work" : "brk");
    }
    // 页面被关掉 / 电脑睡眠期间也可能整轮走完。刷新回来要一次补齐：
    // 走完的专注段照常记成绩（人确实把那 45 分钟过完了），最后一声铃不追放。
    function catchUp(fromLoad) {
        let n = 0, ended = 0;
        while (running && curSeg() && Date.now() >= endAt && n++ < 200) {
            const s = curSeg();
            if (s.kind === "work") ended += 1;
            finishSeg(true);
        }
        if (fromLoad && ended > 0) expiredNote = "上次关掉页面期间走完了 " + ended + " 个专注段，已照记。";
        if (!curSeg()) { running = false; }
        saveRun();
    }

    // ---- 背景轮播 --------------------------------------------------------
    let bg = { images: [], interval: 20, dim: 0.35, show: "both", sound: "on" };
    let bgIdx = -1, bgTimer = null, bgLayer = 0;
    let bgShot = null;          // 最近一张解好的图：改窗口尺寸时要按新尺寸重量一次
    function assetUrl(p) { return API + "/api/notes/asset?path=" + encodeURIComponent(p); }
    function bgWanted() {
        if (bg.show === "off" || !bg.images.length) return false;
        return bg.show === "both" || full;       // full = 只在全屏时铺
    }

    // ---- 文字配色：每换一张图，先量一下它有多亮 --------------------------
    // 轮播图一换，压在它上面的字就可能糊掉：白字撞上亮天空、黑字撞上夜景，
    // 毛病是同一个——字色一直按「纯色卡片」定的，照片根本不在考虑范围内
    // （2026-09-21 用户反馈：亮底上的灰字基本看不见）。所以每换一张就量一次，
    // 再决定这一轮用哪套字。
    //
    // 为什么不是整张图求平均：background-size:cover 会裁掉一大半（4:3 的图铺进
    // 长条卡里只剩中间一条），被裁掉的部分不该参与判断——先按 cover 的算法反推
    // 出可见矩形，只采样那一块。量到的亮度还要叠上当前遮罩浓度，因为用户看到的
    // 是叠完遮罩之后的底。
    const PM_P_HI = 0.46;      // 亮部超过它，白字就开始糊了
    const PM_M_LIT = 0.42;     // 整张平均超过它 = 没有暗处可躲，索性整张换深字
    // 补遮罩的目标：把亮部压回 PM_P_HI 底下一点点。贴着阈值取是有意的——
    // 取太低的话，p85=.46 不补、.47 就要补一大口，换图时遮罩会一跳一跳。
    const PM_P_WANT = 0.44;
    const PM_SCRIM_MAX = 0.45; // 兜底上限：真遇上大片死白的图，再深就成灰板了

    function inkRgb() {
        // 遮罩颜色跟着主题走（深色主题是青墨、浅色主题是纸白），量的时候得用同一个。
        // 取不到（DOM 桩 / 旧浏览器）就按深色主题的默认值算。
        let s = "";
        try { s = getComputedStyle(document.documentElement).getPropertyValue("--mo-rgb"); } catch (e) {}
        const m = String(s || "").split(",").map(function (x) { return parseFloat(x); });
        return m.length === 3 && m.every(function (x) { return isFinite(x); }) ? m : [21, 26, 26];
    }
    function lum709(r, g, b) { return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255; }

    // → { mean, p85 }，都是「叠完遮罩之后」的亮度（0~1）；量不了就返回 null
    function measureBg(im) {
        const cw = host.clientWidth, ch = host.clientHeight;
        const iw = im.naturalWidth, ih = im.naturalHeight;
        if (!cw || !ch || !iw || !ih) return null;   // 卡片正被藏着（切在别的子页），先不量
        const k = Math.max(cw / iw, ch / ih);        // cover：短边先铺满
        const sw = Math.min(iw, cw / k), sh = Math.min(ih, ch / k);
        let d;
        try {
            const c = document.createElement("canvas");
            c.width = c.height = 32;                 // 32×32 够用了：要的是明暗，不是细节
            const g = c.getContext("2d");
            g.drawImage(im, (iw - sw) / 2, (ih - sh) / 2, sw, sh, 0, 0, 32, 32);
            d = g.getImageData(0, 0, 32, 32).data;
        } catch (e) { return null; }                 // 跨域图读不到像素，就当没量过
        // ⚠️ 这里只用用户设的 --pm-dim，故意不把自动补的 --pm-scrim 算进去：
        //    算进去就成了闭环（补完更暗 → 下次量出来不用补 → 遮罩归零 → 又变亮），
        //    换图时遮罩会来回抽。基准始终是「用户设的那一档」。
        const ink = inkRgb(), dim = Math.max(0, Math.min(1, (Number(bg.dim) || 0) + 0.05));
        const v = [];                                // 渐变上下差 .1，取中间那一档
        for (let i = 0; i < d.length; i += 4) {
            v.push(lum709(d[i] * (1 - dim) + ink[0] * dim,
                          d[i + 1] * (1 - dim) + ink[1] * dim,
                          d[i + 2] * (1 - dim) + ink[2] * dim));
        }
        v.sort(function (a, b) { return a - b; });
        let sum = 0;
        for (let i = 0; i < v.length; i++) sum += v[i];
        // 判「白字会不会糊」看的是亮部（p85）而不是平均：平均会被大片暗地面稀释，
        // 而用户指的恰恰是左上角那块亮天空。
        return { mean: sum / v.length, p85: v[Math.floor(v.length * 0.85)] };
    }

    function applyMood(m) {
        // 量不到就维持上一次的判断，别乱清——切一趟子页回来字色不该闪一下
        if (!m) return;
        host.classList.remove("pm-on-lit");
        host.style.setProperty("--pm-scrim", "0");
        if (m.p85 <= PM_P_HI) return;                // 亮部本来就够暗，白字看得清，不用动
        if (m.mean >= PM_M_LIT) { host.classList.add("pm-on-lit"); return; }   // 整张都亮 → 换深字
        // 剩下的是一半亮一半暗（亮天空压着暗地面）：换深字则暗处糊，留白字则亮处糊，
        // 光换颜色解决不了，只能把遮罩再压深一点。压到亮部落进 PM_P_WANT 为止，
        // 上限 PM_SCRIM_MAX——再深照片就成灰板了，宁可字难认也别把图毁了。
        const ink = lum709.apply(null, inkRgb());
        const need = (m.p85 - PM_P_WANT) / Math.max(0.05, m.p85 - ink);
        host.style.setProperty("--pm-scrim", Math.min(PM_SCRIM_MAX, need).toFixed(3));
    }

    function stepBg() {
        const list = bg.images;
        if (!list.length) return;
        bgIdx = (bgIdx + 1) % list.length;
        // 先把图解码好再切层：否则切过去的瞬间是空白，看上去像闪了一下
        const im = new Image();
        im.onload = function () {
            const layers = host.querySelectorAll(".pm-bg");
            const show = layers[bgLayer % layers.length];
            const hide = layers[(bgLayer + 1) % layers.length];
            if (!show) return;
            show.style.backgroundImage = 'url("' + assetUrl(list[bgIdx]) + '")';
            show.classList.add("on");
            if (hide) hide.classList.remove("on");
            bgLayer += 1;
            bgShot = im;
            applyMood(measureBg(im));        // 换了底就重挑一次字色
        };
        im.onerror = function () { /* 这张坏了就跳过，下一张轮到时再说 */ };
        im.src = assetUrl(list[bgIdx]);
    }
    function syncBg() {
        const want = bgWanted();
        host.classList.toggle("pm-hasbg", want);
        host.style.setProperty("--pm-dim", String(bg.dim));
        if (!want) {
            if (bgTimer) { clearInterval(bgTimer); bgTimer = null; }
            // 图撤了，字色回到纯色卡那一套（不这么做的话，上一次判定留下的
            // .pm-on-lit 会挂在没图的卡上，把深字配给深底）
            host.classList.remove("pm-on-lit");
            host.style.setProperty("--pm-scrim", "0");
            return;
        }
        if (bgIdx < 0 || bgIdx >= bg.images.length) bgIdx = -1;
        stepBg();
        if (bgTimer) clearInterval(bgTimer);
        bgTimer = setInterval(stepBg, Math.max(5, bg.interval | 0) * 1000);
    }
    // 窗口一改大小，cover 露出来的那块就跟着变，得按新尺寸重量一次。
    // 防抖 300ms：拖窗口时每帧重画一次 canvas 是白费。全屏进出走的是 setFull →
    // syncBg → stepBg，本来就会重量，不用在这儿再管。
    let bgResizeT = null;
    function remeasure() {
        if (bgResizeT) clearTimeout(bgResizeT);
        bgResizeT = setTimeout(function () { if (bgShot) applyMood(measureBg(bgShot)); }, 300);
    }
    if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
        window.addEventListener("resize", remeasure);
    }
    // 切回这个标签页时补量一次：在别的标签页里轮播悄悄换过图，那张的亮度
    // 是没量过的（卡片当时被藏着），不等下一次轮播就把字色跟上来。
    if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
        document.addEventListener("visibilitychange", function () { if (!document.hidden) remeasure(); });
    }
    function loadCfg(after) {
        fetch(API + "/api/settings").then(function (r) { return r.json(); }).then(function (d) {
            if (d && d.ok && d.pomo) {
                bg = d.pomo;
                muted = bg.sound === "off";     // 服务端是唯一真相源，本地不另存一份
            }
            syncBg();
            if (after) after(); else paint();
        }).catch(function () { if (after) after(); });
    }
    // 设置页改完背景/间隔后叫一声，不用刷新整页
    globalThis.__pomoReload = function () { loadCfg(null); };

    // ---- 结构 ------------------------------------------------------------
    const host = document.createElement("div");
    host.className = "pm-card";
    host.innerHTML =
          '<div class="pm-bg pm-bg-a"></div><div class="pm-bg pm-bg-b"></div>'
        + '<div class="pm-dim"></div>'
        + '<div class="pm-inner">'
        + '  <div class="pm-head">'
        + '    <span class="pm-h">🍅 番茄钟</span>'
        + '    <span class="pm-day" id="pm-day"></span>'
        + '    <span class="pm-tools">'
        + '      <button class="fs-full-toggle" id="pm-sound" title="阶段结束提示音"></button>'
        + '      <button class="fs-full-toggle" id="pm-mini-btn" title="页内小窗：在本页浮动，可拖动、可调透明度（切子页也看得见）">小窗</button>'
        + '      <button class="fs-full-toggle" id="pm-pip" title="独立小窗：跳出浏览器、始终置顶，最小化浏览器也不受影响（桌面版 Chrome / Edge）" hidden>独立小窗</button>'
        + '      <button class="fs-full-toggle" id="pm-media" title="锁屏 / 通知栏显示倒计时（手机浏览器）" hidden>锁屏</button>'
        + '      <button class="fs-full-toggle" id="pm-full" title="全屏沉浸（Esc 退出）">⛶ 全屏</button>'
        + '    </span>'
        + '  </div>'
        + '  <div class="pm-presets" id="pm-presets"></div>'
        + '  <div class="pm-body">'
        + '    <div class="pm-dial">'
        + '      <svg viewBox="0 0 200 200" class="pm-svg" aria-hidden="true">'
        + '        <circle class="pm-ring-bg" cx="100" cy="100" r="88"></circle>'
        + '        <circle class="pm-ring-fg" cx="100" cy="100" r="88" id="pm-ring"></circle>'
        + '      </svg>'
        + '      <div class="pm-dial-mid">'
        + '        <div class="pm-remain" id="pm-time">--:--</div>'
        + '        <div class="pm-phase" id="pm-phase">准备开始</div>'
        + '        <div class="pm-dots" id="pm-dots"></div>'
        + '      </div>'
        + '    </div>'
        + '    <div class="pm-side">'
        + '      <div class="pm-actions">'
        + '        <button class="fs-btn pm-main-btn" id="pm-toggle">▶ 开始</button>'
        + '        <button class="fs-btn" id="pm-reset" title="回到第 1 段">↺ 重置</button>'
        + '        <button class="fs-btn" id="pm-skip" title="跳过这一段（不计入今日成绩）">⏭ 跳过</button>'
        + '        <button class="fs-btn" id="pm-stop" title="结束整个番茄钟">⏹ 结束</button>'
        + '      </div>'
        + '      <div class="pm-plan" id="pm-plan"></div>'
        + '      <div class="pm-next" id="pm-next"></div>'
        + '      <div class="pm-custom">'
        + '        <span class="pm-clabel">自定义</span>'
        + '        <input class="pm-cnum" id="pm-cwork" type="number" min="1" max="600" step="5" value="45" title="专注分钟数">'
        + '        <span class="pm-cu">分 专注</span>'
        + '        <input class="pm-cnum" id="pm-cbrk" type="number" min="0" max="60" step="5" value="10" title="休息分钟数，0 = 不歇">'
        + '        <span class="pm-cu">分 歇</span>'
        + '        <input class="pm-cnum" id="pm-crounds" type="number" min="1" max="24" value="2" title="轮数">'
        + '        <span class="pm-cu">轮</span>'
        + '        <button class="fs-btn pm-capply" id="pm-capply">用这套</button>'
        + '        <button class="fs-btn pm-csave" id="pm-csave" title="把上面这组数字存成一个预设，以后一键切（存出来的 chip 右边带 ✕，可以删）">＋ 存为预设</button>'
        + '      </div>'
        + '      <div class="pm-hint">番茄钟是墙钟：切到别的窗口也照走（和闪卡那个「失焦即停」的学习计时是两回事）。'
        + '轮播背景在「设置 → 🍅 番茄钟」里配。<span class="pm-esc">按 Esc 退出全屏。</span></div>'
        + '    </div>'
        + '  </div>'
        + '</div>';
    slot.appendChild(host);

    const $ = function (id) { return host.querySelector("#" + id); };
    const els = {
        time: $("pm-time"), phase: $("pm-phase"), ring: $("pm-ring"), dots: $("pm-dots"),
        presets: $("pm-presets"), plan: $("pm-plan"), next: $("pm-next"), day: $("pm-day"),
        toggle: $("pm-toggle"), reset: $("pm-reset"), skip: $("pm-skip"), stop: $("pm-stop"),
        full: $("pm-full"), sound: $("pm-sound"),
        miniBtn: $("pm-mini-btn"), pip: $("pm-pip"), media: $("pm-media"),
        cwork: $("pm-cwork"), cbrk: $("pm-cbrk"), crounds: $("pm-crounds"),
        capply: $("pm-capply"), csave: $("pm-csave")
    };

    function renderPresets() {
        els.presets.innerHTML = allPresets().map(function (p) {
            const chip = '<button class="pm-chip' + (plan.id === p.id ? " on" : "") + '" data-preset="'
                + esc(p.id) + '" title="' + esc(p.note) + '">' + esc(p.label)
                + '<span class="pm-chip-n">' + p.rounds + " 段</span></button>";
            if (p.id.indexOf("my:") !== 0) return chip;      // 内置的不带删除尾巴
            return '<span class="pm-chipw">' + chip + '<button class="pm-chip-x" data-del="'
                + esc(p.id) + '" title="删除这个预设">✕</button></span>';
        }).join("");
        els.presets.querySelectorAll("[data-preset]").forEach(function (b) {
            b.onclick = function () { pick(presetById(b.dataset.preset)); };
        });
        els.presets.querySelectorAll("[data-del]").forEach(function (b) {
            b.onclick = function () { dropMy(b.getAttribute("data-del")); };
        });
    }
    function dropMy(id) {
        const hit = presetById(id);
        myPresets = myPresets.filter(function (p) { return p.id !== id; });
        saveMy();
        // 删掉的正是在跑/暂停中那个：表不能停（人正在专心），但它从此不再对应任何
        // 预设，于是就地改成「自定义」。这里**不能**写 pick(PRESETS[0])——那会把
        // 用户当前这一轮的进度清掉，删个预设不该有这个代价。
        if (plan.id === id) {
            plan = Object.assign({}, plan, { id: CUSTOM.id, label: CUSTOM.label, note: CUSTOM.note });
            lsSet(LS_PLAN, JSON.stringify({ id: plan.id, work: plan.work, brk: plan.brk,
                                            rounds: plan.rounds, label: plan.label }));
            saveRun();
        }
        renderPresets(); paint();
        if (hit) toast("已删除预设：" + hit.label);
    }

    // ---- 绘制 ------------------------------------------------------------
    function phaseName(s) { return s ? (s.kind === "work" ? "专注" : "休息") : "完成"; }
    function paint() {
        const seg = curSeg();
        const left = seg ? (running ? Math.max(0, endAt - Date.now()) : Math.max(0, remain)) : 0;
        els.time.textContent = seg ? fmtMs(left) : "00:00";
        host.classList.toggle("pm-brk", !!seg && seg.kind === "brk");
        host.classList.toggle("pm-done", !seg);
        host.classList.toggle("pm-pause", !running);

        let ph;
        if (!seg) ph = "全部完成 🎉";
        else if (running) ph = phaseName(seg) + " · 第 " + seg.round + "/" + plan.rounds + " 轮";
        else ph = (startedOnce ? "已暂停 · " : "准备开始 · ") + phaseName(seg) + " 第 " + seg.round + "/" + plan.rounds + " 轮";
        els.phase.textContent = expiredNote && !seg ? expiredNote : ph;

        const p = seg ? (1 - left / segMs(seg)) : 1;
        els.ring.style.strokeDasharray = String(RING);
        els.ring.style.strokeDashoffset = String(RING * (1 - Math.max(0, Math.min(1, p))));

        els.dots.innerHTML = plan.segs.filter(function (s) { return s.kind === "work"; }).map(function (s) {
            const done = s.round < (curSeg() ? curSeg().round : plan.rounds + 1);
            const now = !!curSeg() && curSeg().kind === "work" && curSeg().round === s.round;
            return '<span class="pm-dot' + (done ? " ok" : "") + (now ? " now" : "") + '"></span>';
        }).join("");

        els.toggle.textContent = running ? "⏸ 暂停" : (seg ? (startedOnce ? "▶ 继续" : "▶ 开始") : "▶ 再来一轮");
        // 「结束」只在真有一轮在跑/暂停中时出现：待开始和已完成都没有可结束的东西
        els.stop.hidden = !(startedOnce && seg);
        els.plan.innerHTML = esc(plan.label) + ' <span class="pm-cu">· 共 '
            + fmtMin(plan.totalMin) + '（专注 ' + fmtMin(plan.workMin) + '）</span>';
        const nx = pos + 1 < plan.segs.length ? plan.segs[pos + 1] : null;
        els.next.textContent = !seg
            ? (expiredNote || "这一轮已经跑完，换个预设或再来一次。")
            : (running || pos > 0
                ? "这一段：" + phaseName(seg) + " " + seg.min + " 分钟"
                  + (nx ? " · 接下来：" + phaseName(nx) + " " + nx.min + " 分钟"
                        : " · 之后就收工了")
                : "共 " + plan.segs.length + " 段 · 第一段：" + phaseName(seg) + " " + seg.min + " 分钟");
        const d = day;
        els.day.innerHTML = "今日 <b>" + d.pomos + "</b> 个番茄 · 专注 <b>" + fmtMin(d.min) + "</b>" + (d.min ? "" : "（还没记上）");
        els.sound.textContent = muted ? "🔕 提示音" : "🔔 提示音";
        els.sound.classList.toggle("is-off", muted);
        // 小窗 / 系统小窗 / 锁屏 三颗按钮：不支持的直接藏起来（别给个按了没用的键）
        if (els.miniBtn) {
            els.miniBtn.classList.toggle("is-off", !(mini.on && !miniEl.hidden));
            els.miniBtn.title = (mini.on && !miniEl.hidden)
                ? "页内小窗：开着（点一下收起）"
                : "页内小窗：点一下打开（在本页浮动，可拖动、可调透明度）";
        }
        if (els.pip) {
            const open = !!(pipWin && !pipWin.closed);
            els.pip.hidden = !pipSupported();
            // is-off = 「没开」（和音效/小窗/锁屏三颗按钮同一套读法：压暗＝关着）
            els.pip.classList.toggle("is-off", !open);
            els.pip.title = open ? "独立小窗：已打开（点一下关掉）"
                : "独立小窗：跳出浏览器、始终置顶，最小化浏览器也不受影响（桌面版 Chrome / Edge）";
        }
        if (els.media) {
            els.media.hidden = !mediaSupported();
            els.media.classList.toggle("is-off", !mediaOn);
            els.media.title = mediaOn ? "锁屏 / 通知栏显示：开（点一下关闭）"
                                      : "锁屏 / 通知栏显示倒计时（点一下开启）";
        }
        els.full.textContent = full ? " 退出全屏" : "⛶ 全屏";
        els.full.title = full ? "退出全屏（Esc）" : "全屏沉浸（Esc 退出）";

        // 小窗 / 系统小窗 / 锁屏 三处同步（都由这一处 paint 驱动，别各自算时间）
        paintMini();
        paintPip();
        syncMedia();

        // 标题栏挂倒计时只在「有一轮在进行中」时才抢：刚打开大盘、一轮都没开始，
        // 就把别人的标题改掉是越权。
        document.title = seg && (running || startedOnce)
            ? (running ? "▶ " : "⏸ ") + fmtMs(left) + " " + phaseName(seg) + " · " + BASE_TITLE
            : (!seg && startedOnce ? "🍅 番茄钟完成 · " + BASE_TITLE : BASE_TITLE);
    }

    // ---- 控制 ------------------------------------------------------------
    function pick(p) {
        if (!p) return;
        if (curSeg() && (running || startedOnce) && !confirm("番茄钟还在跑，切到「" + p.label + "」会放弃当前进度。继续？")) return;
        plan = buildPlan(p);
        pos = 0; running = false; endAt = 0; remain = segMs(plan.segs[0]);
        startedOnce = false;
        expiredNote = "";
        lsSet(LS_PLAN, JSON.stringify({ id: p.id, work: plan.work, brk: plan.brk, rounds: plan.rounds, label: p.label }));
        saveRun(); renderPresets(); paint();
    }
    function start() {
        if (!curSeg()) { pos = 0; }                 // 跑完了再点 = 从头再来一轮
        expiredNote = "";
        const seg = curSeg();
        if (remain <= 0 || remain > segMs(seg)) remain = segMs(seg);
        endAt = Date.now() + remain;
        running = true;
        startedOnce = true;
        ac();                                        // 借这次用户手势解锁音频，稍后才能响
        saveRun(); paint();
    }
    function pause() {
        if (!running) return;
        remain = Math.max(0, endAt - Date.now());
        running = false; endAt = 0;
        saveRun(); paint();
    }
    function reset() {
        pos = 0; running = false; endAt = 0; expiredNote = ""; startedOnce = false;
        remain = plan.segs.length ? segMs(plan.segs[0]) : 0;
        saveRun(); paint();
    }
    function stop() {
        if (!confirm("结束这个番茄钟？当前这一段不计入今日成绩。")) return;
        pos = plan.segs.length; running = false; endAt = 0; remain = 0; startedOnce = false;
        lsDel(LS_RUN);
        paint();
    }
    function skip() {
        if (!curSeg()) return;
        finishSeg(false);                            // 手动跳过不记成绩
        saveRun(); paint();
    }

    // ---- 全屏 ------------------------------------------------------------
    // 优先用浏览器真全屏（F11 那种整屏），拿不到就退回 CSS 铺满 —— 两条路都
    // 走同一个 .pm-fs 类，所以下面不用关心到底哪条生效了。
    function setFull(on) {
        if (on === full) return;
        full = on;
        if (on) {
            overlay.hidden = false;
            overlay.appendChild(host);
            host.classList.add("pm-fs");
            document.body.classList.add("pm-lock");
            try {
                const pr = overlay.requestFullscreen
                    ? overlay.requestFullscreen() : Promise.reject(new Error("no fs api"));
                if (pr && pr.catch) pr.catch(function () { /* 被拦就用 CSS 铺满，够用 */ });
            } catch (e) {}
        } else {
            host.classList.remove("pm-fs");
            document.body.classList.remove("pm-lock");
            slot.appendChild(host);
            overlay.hidden = true;
            if (document.fullscreenElement && document.exitFullscreen) {
                try { const pr = document.exitFullscreen(); if (pr && pr.catch) pr.catch(function () {}); } catch (e) {}
            }
        }
        syncBg(); paint();
    }
    document.addEventListener("fullscreenchange", function () {
        if (!document.fullscreenElement && full) setFull(false);
    });
    document.addEventListener("keydown", function (e) {
        if (e.key !== "Escape" || !full) return;
        if (document.fullscreenElement) return;   // 原生那层会自己退，跟着 fullscreenchange 走
        // 只退最上面这一层：闪卡区可能也处在全屏，Esc 一次退两层很吓人
        if (e.stopPropagation) e.stopPropagation();
        setFull(false);
    }, true);
    // 闪卡区要靠这个判断「现在是不是被番茄钟盖着」——全屏番茄钟下面还接数字键
    // 评分是最典型的「手比眼快」事故。
    globalThis.__pomoFullscreen = function () { return full; };

    // ---- 事件 ------------------------------------------------------------
    els.toggle.onclick = function () { if (running) pause(); else start(); };
    els.reset.onclick = reset;
    els.skip.onclick = skip;
    els.stop.onclick = stop;
    els.full.onclick = function () { setFull(!full); };
    if (els.miniBtn) els.miniBtn.onclick = function () { setMini(!mini.on); };
    if (els.pip) els.pip.onclick = function () {
        if (pipWin && !pipWin.closed) { try { pipWin.close(); } catch (e) {} pipWin = null; paint(); return; }
        openPip();
    };
    if (els.media) els.media.onclick = function () { setMedia(!mediaOn); };
    els.sound.onclick = function () {
        muted = !muted;
        paint();
        if (!muted) { ac(); chime("brk"); }      // 现挂现响一声，让人知道开关是真的
        fetch(API + "/api/settings", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ pomo_sound: muted ? "off" : "on" })
        }).catch(function () { /* 存不上也不影响这一轮，下次核对会补 */ });
    };
    els.capply.onclick = function () {
        const p = {
            id: "custom", label: "自定义",
            work: Number(els.cwork.value) || 45,
            brk: Math.max(0, Number(els.cbrk.value) || 0),
            rounds: Number(els.crounds.value) || 1,
            note: "自定义节奏"
        };
        pick(p);
    };
    els.csave.onclick = function () {
        // 和输入框上的 min/max 对齐：手打进来的数也要夹一次
        const work = Math.max(1, Math.min(600, Math.round(Number(els.cwork.value) || 45)));
        const brk = Math.max(0, Math.min(60, Math.round(Number(els.cbrk.value) || 0)));
        const rounds = Math.max(1, Math.min(24, Math.round(Number(els.crounds.value) || 1)));
        const id = myId(work, brk, rounds);
        const dup = presetById(id);
        if (dup) { toast("这组数字已经存过了：" + dup.label); pick(dup); return; }
        if (myPresets.length >= MY_MAX) {
            toast("自定义预设最多 " + MY_MAX + " 个，先删掉几个再存");
            return;
        }
        const p = { id: id, label: myLabel(work, brk, rounds), work: work, brk: brk, rounds: rounds,
                    note: "我存的预设 · 专注 " + work + " 分钟" + (rounds > 1 ? " × " + rounds + " 轮" : "") };
        myPresets.push(p);
        saveMy();
        // 存归存，不顺手把正在跑的那一轮换掉——「记下来」和「切过去」是两件事，
        // 用户想用自己去点那颗新 chip。
        renderPresets();
        toast("已存为预设：" + p.label + "（点它就能用，右边的 ✕ 删掉）");
    };

    // ============================================================
    // 浮动小窗 / 系统小窗 / 锁屏显示（2026-09-21 晚）
    //
    // 三种「把番茄钟挪出页面」的能力，边界完全不同，先记清楚：
    //   · **页面内小窗**（.pm-mini）：任何设备都能用——可拖动、可调透明度，
    //     切到别的子页也浮在那儿。但它活在**这个页面**里，切到别的 App 就没了。
    //   · **系统小窗**（Document Picture-in-Picture）：真·独立置顶窗口，能浮在
    //     别的应用上面做别的事。⚠️ 只有桌面版 Chromium（Chrome / Edge）支持，
    //     手机和平板上的浏览器一律没有——所以按钮默认藏起来，支持才显示。
    //   · **锁屏 / 通知栏**（Media Session）：手机上唯一能摸到「状态栏」的路子。
    //     靠一条静音音轨把媒体会话挂住，倒计时就出现在锁屏卡片 / 通知栏里，
    //     还能用锁屏的播放/暂停键控制。浏览器不给就退化成只能用小窗。
    //     它必须由一次用户点击启动（浏览器不给自动播音频）。
    // ============================================================
    const MINI_KEY = "kaoyan.pomo.mini.v1";
    // 透明度下限：原来是 25%，淡到那个程度页内小窗基本只剩个影子，
    // 想要「几乎看不见、只剩个数字」也随你——反正鼠标一碰就回到不透明。
    const MINI_OP_MIN = 0.12;
    const mini = { on: true, x: null, y: null, op: 0.85 };
    (function loadMini() {
        const o = readJson(MINI_KEY);
        if (!o || typeof o !== "object") return;
        if (typeof o.on === "boolean") mini.on = o.on;
        if (typeof o.x === "number") mini.x = o.x;
        if (typeof o.y === "number") mini.y = o.y;
        const op = Number(o.op);
        if (op >= MINI_OP_MIN && op <= 1) mini.op = op;
    })();
    function saveMini() { lsSet(MINI_KEY, JSON.stringify(mini)); }

    // 小窗内容（页面内小窗与 PiP 窗口共用同一份结构）
    // 图标一律写成 \\uXXXX 转义：这串会被同时塞进两个文档，写成字面量
    // 在编辑/传输链路上容易被吃掉（踩过）。
    function miniInner() {
        return '<div class="pm-mini-head" data-drag="1">'
            + '<span class="pm-mini-dot"></span>'
            + '<span class="pm-mini-ph" data-f="phase">准备开始</span>'
            + '<button class="pm-mini-x" data-act="close" title="收起小窗">\\u2715</button>'
            + '</div>'
            + '<div class="pm-mini-time" data-f="time">--:--</div>'
            + '<div class="pm-mini-bar"><i data-f="bar"></i></div>'
            + '<div class="pm-mini-row">'
            + '<button class="pm-mini-btn go" data-act="toggle" title="开始 / 暂停">\\u25B6</button>'
            + '<button class="pm-mini-btn" data-act="skip" title="跳过这一段（不计成绩）">\\u23ED</button>'
            + '<button class="pm-mini-btn" data-act="full" title="回到全屏沉浸">\\u26F6</button>'
            + '<button class="pm-mini-btn" data-act="pip" title="跳出浏览器：开一个独立置顶小窗（桌面版 Chrome / Edge）" hidden>\\u2197</button>'
            + '</div>'
            + '<div class="pm-mini-op" title="调整小窗透明度">'
            + '<input type="range" data-act="op" min="12" max="100" step="2" aria-label="小窗透明度">'
            + '<span data-f="opv">85%</span>'
            + '</div>';
    }
    function setField(root, f, text) {
        const el = root.querySelector('[data-f="' + f + '"]');
        if (el) el.textContent = text;
    }
    // 把按钮接上同一套状态机（小窗与 PiP 共用；onClose 各自不同）
    function bindMiniActions(root, onClose) {
        root.querySelectorAll("[data-act]").forEach(function (b) {
            const act = b.getAttribute("data-act");
            if (act === "close") b.onclick = onClose;
            else if (act === "toggle") b.onclick = function () { if (running) pause(); else start(); };
            else if (act === "skip") b.onclick = function () { skip(); };
            else if (act === "full") b.onclick = function () { setFull(true); };
            else if (act === "pip") b.onclick = function () { openPip(); };
            else if (act === "op") b.oninput = function () {
                mini.op = Math.max(MINI_OP_MIN, Math.min(1, Number(b.value) / 100));
                applyMiniOpacity(); saveMini();
                // 页内小窗和 PiP 各有一份自己的百分比文字，写在各自的 root 上
                root.querySelectorAll('[data-f="opv"]').forEach(function (s) {
                    s.textContent = Math.round(mini.op * 100) + "%";
                });
            };
        });
    }
    const miniEl = document.createElement("div");
    miniEl.className = "pm-mini";
    miniEl.hidden = true;
    miniEl.innerHTML = miniInner();
    if (document.body && document.body.appendChild) document.body.appendChild(miniEl);
    bindMiniActions(miniEl, function () { setMini(false); });

    // 页内小窗和 PiP 卡片是两棵 DOM 树（PiP 那份在另一个 document 里），
    // 透明度得两边都写一遍，不能只写 miniEl。
    function miniRoots() {
        const out = [miniEl];
        try {
            if (pipWin && !pipWin.closed && pipWin.document) {
                const c = pipWin.document.querySelector(".pm-mini");
                if (c) out.push(c);
            }
        } catch (e) { /* PiP 文档已经没了就算了 */ }
        return out;
    }
    function applyMiniOpacity() {
        miniRoots().forEach(function (el) {
            if (el && el.style && el.style.setProperty) {
                el.style.setProperty("--pm-mini-op", String(mini.op));
            }
        });
    }
    function placeMini() {
        if (!miniEl.offsetWidth) return;      // 还没量到尺寸（隐藏中）就先不摆
        const w = miniEl.offsetWidth, h = miniEl.offsetHeight;
        const vw = window.innerWidth || 360, vh = window.innerHeight || 640;
        let x = mini.x, y = mini.y;
        if (x == null || y == null) { x = vw - w - 14; y = vh - h - 16; }   // 默认右下角
        x = Math.max(6, Math.min(Math.max(6, vw - w - 6), x));
        y = Math.max(6, Math.min(Math.max(6, vh - h - 6), y));
        mini.x = Math.round(x); mini.y = Math.round(y);
        miniEl.style.left = mini.x + "px";
        miniEl.style.top = mini.y + "px";
    }
    function setMini(on) {
        mini.on = !!on;
        saveMini();
        paint();
    }
    function paintMini() {
        // 番茄钟本体全屏时小窗是多余的（同一个表看两遍），先收起来；
        // 已经开了独立小窗（PiP）时也收起来——两份同样的表只会互相打架。
        const pipOpen = !!(pipWin && !pipWin.closed);
        const want = mini.on && (startedOnce || running) && !full && !pipOpen;
        miniEl.hidden = !want;
        // 页内小窗上那个「跳出浏览器」按钮：只有真支持 PiP 才给（手机/平板不给假希望）
        const pop = miniEl.querySelector('[data-act="pip"]');
        if (pop) pop.hidden = !pipSupported();
        if (!want) return;
        const seg = curSeg();
        const left = seg ? (running ? Math.max(0, endAt - Date.now()) : Math.max(0, remain)) : 0;
        const pct = seg ? Math.round(100 * (1 - left / segMs(seg))) : 100;
        setField(miniEl, "time", seg ? fmtMs(left) : "00:00");
        setField(miniEl, "phase", !seg ? "已完成"
            : (running ? "" : "暂停 · ") + phaseName(seg) + " 第 " + seg.round + "/" + plan.rounds + " 轮");
        miniEl.classList.toggle("pm-pause", !running);
        miniEl.classList.toggle("pm-mini-brk", !!seg && seg.kind === "brk");
        const bar = miniEl.querySelector('[data-f="bar"]');
        if (bar && bar.style) bar.style.width = Math.max(0, Math.min(100, pct)) + "%";
        const tg = miniEl.querySelector('[data-act="toggle"]');
        if (tg) tg.textContent = running ? "\\u23F8" : "\\u25B6";
        const opv = miniEl.querySelector('[data-act="op"]');
        if (opv && document.activeElement !== opv) opv.value = String(Math.round(mini.op * 100));
        setField(miniEl, "opv", Math.round(mini.op * 100) + "%");
        applyMiniOpacity();
        placeMini();
    }
    // 拖动：pointer 事件同时覆盖鼠标 / 触屏 / 触控笔
    (function bindMiniDrag() {
        const head = miniEl.querySelector("[data-drag]");
        if (!head || !head.addEventListener) return;
        let dragging = false, armed = false, dx = 0, dy = 0, sx = 0, sy = 0;
        head.addEventListener("pointerdown", function (e) {
            if (e.button != null && e.button !== 0) return;
            // ⚠️ 按在按钮/滑杆上时**绝不能**进入拖拽：一旦 setPointerCapture，
            //    pointerup 与随之而来的 click 会被改派到把手本身，里面的 ✕ 就永远
            //    收不到点击（用户实测「小窗上的叉不起作用」就是这个）。
            //    slide/close 这些都是把手的子元素，必须原样放行。
            if (e.target && e.target.closest && e.target.closest("button, input, a, select")) return;
            const r = miniEl.getBoundingClientRect ? miniEl.getBoundingClientRect()
                                                   : { left: mini.x || 0, top: mini.y || 0 };
            dx = e.clientX - r.left; dy = e.clientY - r.top;
            sx = e.clientX; sy = e.clientY;
            armed = true;            // 先只记起点：真的动了才算拖拽
        });
        head.addEventListener("pointermove", function (e) {
            if (!armed) return;
            if (!dragging) {
                // 5px 阈值：手抖一下不算拖，语义上也就不需要抢指针
                if (Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) < 5) return;
                dragging = true;
                if (head.setPointerCapture) { try { head.setPointerCapture(e.pointerId); } catch (err) {} }
            }
            mini.x = e.clientX - dx; mini.y = e.clientY - dy;
            placeMini();
        });
        const end = function (e) {
            armed = false;
            if (!dragging) return;
            dragging = false;
            // 主动交还指针：不还的话，下一次按下又会被当成还在拖
            if (e && e.pointerId != null && head.releasePointerCapture) {
                try { head.releasePointerCapture(e.pointerId); } catch (err) {}
            }
            saveMini();
        };
        head.addEventListener("pointerup", end);
        head.addEventListener("pointercancel", end);
        // 双击回到默认位置：拖到屏幕角落/刘海后面也能找回来
        head.addEventListener("dblclick", function (e) {
            if (e && e.target && e.target.closest && e.target.closest("button, input")) return;
            mini.x = null; mini.y = null; placeMini(); saveMini();
        });
    })();
    if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
        window.addEventListener("resize", placeMini);
    }

    // ---- 系统小窗（Document PiP，桌面版 Chromium 独有）----
    let pipWin = null;
    // ⚠️ PiP 窗口是系统级窗口，浏览器不给做真透明（窗口本身永远是一块实心底），
    //    所以这里的「透明度」只能是**内容变淡**：字、进度条、按钮往底色里退。
    //    想要真透明请用页内小窗——那个才真的透出后面的东西。
    const PIP_CSS = 'html,body{margin:0;height:100%;background:#151A1A;color:#E5E9E7;'
        + 'font-family:system-ui,"Microsoft YaHei",sans-serif}'
        + '.pm-mini{position:static;width:auto;height:100%;box-sizing:border-box;'
        + 'display:flex;flex-direction:column;justify-content:center;'
        + 'padding:10px 12px;border:0;border-radius:0;box-shadow:none;background:#151A1A;'
        + 'opacity:var(--pm-mini-op,1);transition:opacity .18s}'
        + '.pm-mini-head{display:flex;align-items:center;gap:6px}'
        + '.pm-mini-dot{width:7px;height:7px;border-radius:50%;background:#B84A42}'
        + '.pm-mini-ph{flex:1;font-size:11px;color:#97A5A3;overflow:hidden;white-space:nowrap}'
        + '.pm-mini-x{background:none;border:0;color:#66726F;font-size:11px;cursor:pointer}'
        + '.pm-mini-time{font-size:30px;font-weight:700;margin:2px 0 6px;font-variant-numeric:tabular-nums}'
        + '.pm-mini-bar{height:3px;border-radius:2px;background:#2C3636;overflow:hidden}'
        + '.pm-mini-bar i{display:block;height:100%;width:0;background:#B84A42}'
        + '.pm-mini-row{display:flex;gap:5px;margin-top:8px}'
        + '.pm-mini-btn{flex:1;padding:4px 0;font:inherit;font-size:12px;cursor:pointer;'
        + 'background:#1F2626;color:#97A5A3;border:1px solid #2C3636;border-radius:5px}'
        + '.pm-mini-btn.go{flex:1.5;color:#E08A80;border-color:#B84A42}'
        + '.pm-mini-op{display:flex;align-items:center;gap:6px;margin-top:7px}'
        + '.pm-mini-op input{flex:1;min-width:0;height:12px;accent-color:#5B7C99;cursor:pointer}'
        + '.pm-mini-op span{min-width:30px;text-align:right;font-size:10px;color:#66726F}';
    function pipSupported() {
        return typeof window !== "undefined" && !!window.documentPictureInPicture
            && typeof window.documentPictureInPicture.requestWindow === "function";
    }
    function paintPip() {
        if (!pipWin || pipWin.closed) return;
        const doc = pipWin.document;
        const seg = curSeg();
        const left = seg ? (running ? Math.max(0, endAt - Date.now()) : Math.max(0, remain)) : 0;
        if (!doc || !doc.querySelector) return;
        setField(doc, "time", seg ? fmtMs(left) : "00:00");
        setField(doc, "phase", !seg ? "已完成"
            : (running ? "" : "暂停 · ") + phaseName(seg) + " 第 " + seg.round + "/" + plan.rounds + " 轮");
        const bar = doc.querySelector('[data-f="bar"]');
        if (bar && bar.style) bar.style.width = (seg ? Math.round(100 * (1 - left / segMs(seg))) : 100) + "%";
        const tg = doc.querySelector('[data-act="toggle"]');
        if (tg) tg.textContent = running ? "\\u23F8" : "\\u25B6";
        // 透明度滑杆：PiP 里也放出来了，得跟着 mini.op 走
        const op = doc.querySelector('[data-act="op"]');
        if (op && doc.activeElement !== op) op.value = String(Math.round(mini.op * 100));
        setField(doc, "opv", Math.round(mini.op * 100) + "%");
        applyMiniOpacity();
    }
    function openPip() {
        if (!pipSupported()) {
            // 这里必须说清楚「为什么不行」，不然用户只会觉得按钮坏了：
            // 手机/平板的浏览器根本没有「独立窗口」这个能力。
            toast("这个浏览器开不了独立小窗（手机 / 平板浏览器没有这个能力）——"
                + "先用页内小窗，锁屏显示可以让你在锁屏上看到倒计时");
            return;
        }
        if (pipWin && !pipWin.closed) return;      // 已经开着一个了
        try {
            // 高度 196：比原来多留一条，给透明度滑杆（PiP 里也放出来了）
            const pr = window.documentPictureInPicture.requestWindow({ width: 208, height: 196 });
            if (!pr || !pr.then) return;
            pr.then(function (w) {
                pipWin = w;
                try {
                    const doc = w.document;
                    doc.body.innerHTML = '<style>' + PIP_CSS + '</style>'
                        + '<div class="pm-mini pm-mini-pip">' + miniInner() + '</div>';
                    bindMiniActions(doc, function () { try { w.close(); } catch (e) {} });
                    if (w.addEventListener) {
                        w.addEventListener("pagehide", function () {
                            pipWin = null;
                            try { paint(); } catch (e) {}
                        });
                    }
                } catch (e) {
                    // 窗口已经开出来了，注入/绑定失败也得让主表自己接着画，
                    // 不然页内小窗会一直以为自己「被 PiP 顶掉了」而收着（踩过）。
                    toast("独立小窗内容注入失败：" + (e && e.message ? e.message : e));
                }
                paint();
            }).catch(function (e) { toast("独立小窗打开失败：" + (e && e.message ? e.message : e)); });
        } catch (e) { toast("系统小窗打开失败：" + e.message); }
    }

    // ---- 锁屏 / 通知栏（Media Session）----
    // 静音音轨是这里的关键：浏览器只为「正在播放的媒体」显示锁屏卡片，
    // 所以挂一条听不见的 WAV 循环，把会话一直撑着。运行时现场生成，不塞大段 base64。
    let mediaOn = false, mediaAudio = null, mediaAt = 0, mediaSig = "";
    if (lsGet("kaoyan.pomo.media") === "1") mediaOn = true;
    function mediaSupported() {
        return typeof navigator !== "undefined" && !!navigator.mediaSession;
    }
    function silentWavUrl() {
        const len = 4000;                              // 0.5 秒 @ 8kHz 8bit
        const buf = new ArrayBuffer(44 + len);
        const v = new DataView(buf);
        const ws = function (o, s) { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
        ws(0, "RIFF"); v.setUint32(4, 36 + len, true); ws(8, "WAVEfmt ");
        v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
        v.setUint32(24, 8000, true); v.setUint32(28, 8000, true);
        v.setUint16(32, 1, true); v.setUint16(34, 8, true);
        ws(36, "data"); v.setUint32(40, len, true);
        for (let i = 0; i < len; i++) v.setUint8(44 + i, 128);   // 8bit 静音＝中点
        try { return URL.createObjectURL(new Blob([buf], { type: "audio/wav" })); } catch (e) { return ""; }
    }
    function bindMediaActions() {
        if (!mediaSupported()) return;
        const MS = navigator.mediaSession;
        const set = function (a, f) { try { MS.setActionHandler(a, f); } catch (e) {} };
        set("play", function () { if (!running) start(); });
        set("pause", function () { if (running) pause(); });
        set("stop", function () { setMedia(false); });
        set("nexttrack", function () { skip(); });
    }
    function setMedia(on) {
        if (on && !mediaSupported()) { toast("这个浏览器不支持锁屏显示"); return; }
        if (on && !mediaAudio) {
            mediaAudio = document.createElement("audio");
            mediaAudio.loop = true;
            mediaAudio.setAttribute("playsinline", "");
            if (mediaAudio.setAttribute) mediaAudio.setAttribute("aria-hidden", "true");
            mediaAudio.src = silentWavUrl();
            if (document.body && document.body.appendChild) document.body.appendChild(mediaAudio);
        }
        mediaOn = !!on;
        lsSet("kaoyan.pomo.media", mediaOn ? "1" : "0");
        try {
            if (mediaOn) {
                const pr = mediaAudio.play();
                if (pr && pr.catch) pr.catch(function () { toast("锁屏显示已开，但这个浏览器没让音频起播——锁屏可能看不到"); });
            } else {
                mediaAudio.pause();
                if (mediaSupported()) navigator.mediaSession.metadata = null;
            }
        } catch (e) {}
        if (mediaOn) bindMediaActions();
        mediaAt = 0;
        paint();
    }
    function syncMedia(force) {
        if (!mediaOn || !mediaSupported()) return;
        const seg = curSeg();
        // 除了「每 900ms 刷一次倒计时」，running / 段 一变也要立刻上报——
        // 否则在锁屏上按了暂停，卡片还挂着「计时中」，用户以为没生效。
        const sig = (running ? "1" : "0") + (seg ? seg.kind + seg.round : "x") + pos;
        const now = Date.now();
        if (!force && sig === mediaSig && now - mediaAt < 900) return;
        mediaAt = now; mediaSig = sig;
        const left = seg ? (running ? Math.max(0, endAt - Date.now()) : Math.max(0, remain)) : 0;
        const dur = seg ? segMs(seg) / 1000 : 0;
        try {
            const MS = navigator.mediaSession;
            MS.playbackState = running ? "playing" : (startedOnce && seg ? "paused" : "none");
            if (typeof window.MediaMetadata === "function") {
                MS.metadata = new window.MediaMetadata({
                    title: seg ? (phaseName(seg) + " " + fmtMs(left)) : "番茄钟已完成",
                    artist: "第 " + (seg ? seg.round : plan.rounds) + "/" + plan.rounds + " 轮 · "
                        + (running ? "计时中" : (seg ? "已暂停" : "结束")),
                    album: "改造我们的学习 · 番茄钟",
                });
            }
            if (seg && MS.setPositionState && dur > 0) {
                MS.setPositionState({ duration: dur, position: Math.max(0, Math.min(dur, dur - left / 1000)), playbackRate: 1 });
            }
        } catch (e) { /* 浏览器不支持某个字段就跳过，别影响计时 */ }
    }

    // ---- 起表 ------------------------------------------------------------
    // 250ms 一次：显示秒级刷新够了，而段的推进靠的是和 endAt 比大小，
    // 就算这一拍被浏览器节流延后，也不会把计时本身带偏。
    loadRun();
    renderPresets();
    syncBg();
    paint();
    setInterval(function () {
        if (running) {
            if (Date.now() >= endAt) catchUp(false);
            paint();
        }
    }, 250);
    loadCfg(paint);   // 拉一次番茄钟设置（轮播图 / 间隔 / 遮罩），失败也要正常跑

    // 跨设备对齐：每 12 秒问一次服务端（只读，很轻）。在电脑上开了一轮，走到
    // 平板前打开页面，最迟 12 秒内表就同步过去；从后台切回前台也立刻对一次。
    setInterval(function () {
        if (document.hidden) return;
        syncState();
    }, 12000);
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) syncState();
    });
    // window 也要守卫：DOM 桩里未必有 addEventListener（闪卡那边同理）
    if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
        window.addEventListener("focus", function () { syncState(); });
        // 上次开着「锁屏显示」的话，这次打开页面浏览器不肯自动播音频（要用户手势）。
        // 所以等第一次点按/按键时把它重新挂上，锁屏卡片就回来了。
        const rearm = function () {
            if (mediaOn && mediaAudio && mediaAudio.paused) {
                try { const pr = mediaAudio.play(); if (pr && pr.catch) pr.catch(function () {}); } catch (e) {}
                bindMediaActions();
            }
            window.removeEventListener("pointerdown", rearm);
            window.removeEventListener("keydown", rearm);
        };
        window.addEventListener("pointerdown", rearm);
        window.addEventListener("keydown", rearm);
    }

    // 首页那条「专注」数据直接读这里，省得两边各拉一次接口
    globalThis.__pomoStats = function () { return { day: day, days: days }; };
})();
'''


# ---------------------------------------------------------------------------
# 页面外壳（2026-09-21）：右上角「整页全屏」+ 首页「专注与打卡」数据条
#
# 和 POMO_JS 分开是刻意的：
#   · 番茄钟 / 闪卡的全屏 = 那**一个模块**铺满屏幕（模块自己的按钮）
#   · 这里的全屏        = **整页**铺满（F11 那种），常驻右上角，任何子页都能按
# 两件事共用一个「全屏」单词，但意图完全不同，混在一起写迟早会互相打架。
# ---------------------------------------------------------------------------

SHELL_CSS = '''
        /* --- 鼠标粒子光效（2026-09-21）---
           画布整页 fixed、pointer-events:none（绝不挡点击）。
           压在内容之上但用混合模式当「光」使：深色主题 screen（发光），
           浅色主题 multiply（不然 screen 到白底上等于看不见）。 */
        #mouse-fx { position: fixed; inset: 0; width: 100vw; height: 100vh;
            pointer-events: none; z-index: 40; opacity: .85;
            mix-blend-mode: screen; }
        [data-theme="light"] #mouse-fx { mix-blend-mode: multiply; opacity: .5; }
        #mouse-fx[hidden] { display: none; }

        /* --- 图表悬停（2026-09-21）---
           柱子/扇形的悬停都用 CSS transition 做，而不是 d3 补间：
           mouseenter 一秒钟能来几十次，补间会排队堆积；CSS 过渡天生只保留最后一帧。 */
        .lv-bar { transition: transform .16s ease, fill-opacity .16s ease; }
        .chart-container.lv-dim .lv-bar { fill-opacity: .34; }
        .chart-container.lv-dim .lv-bar.hl { fill-opacity: 1; transform: translateY(-2px); }
        .pm-arc { transition: fill-opacity .16s ease; cursor: pointer; }
        .pm-arc.dimmed { fill-opacity: .35; }
        .legend-item-hot { color: var(--text-primary); }
        .legend-item-hot .chart-legend-dot { box-shadow: 0 0 0 2px rgba(var(--xuan-rgb), .25); }
        @media (prefers-reduced-motion: reduce) {
            .lv-bar, .pm-arc { transition: none !important; }
        }

        /* --- 右上角整页全屏（2026-09-21）--- */
        /* z-index 880：高于正文，低于闪卡全屏(900)与番茄钟全屏(1200)——那两个
           模块铺满时，这颗按钮就该被盖住，免得点出「页中页」。 */
        .shell-fs { position: fixed; top: 14px; right: 16px; z-index: 880;
            font: inherit; font-size: 0.76rem; line-height: 1; cursor: pointer;
            padding: 7px 12px; border-radius: 14px; color: var(--text-secondary);
            background: rgba(var(--mo-rgb), .55); border: 1px solid var(--border-color);
            -webkit-backdrop-filter: blur(4px); backdrop-filter: blur(4px);
            transition: color .15s, border-color .15s, background .15s; }
        .shell-fs:hover { color: var(--text-primary); border-color: var(--dianqing);
            background: rgba(var(--mo-rgb), .82); }
        .shell-fs.on { color: var(--dianqing-lt); border-color: var(--dianqing); }
        /* 整页全屏时浏览器会把根元素刷成黑底，浅色主题下就是一圈黑边 */
        html:fullscreen { background: var(--bg-primary); }
        html:-webkit-full-screen { background: var(--bg-primary); }
        @media (max-width: 720px) {
            /* 手机竖屏：页头是居中的长标题，右上角留给按钮会压到副标题上，
               所以窄屏改成右下角悬浮——手掌自然落点，也避开页头。 */
            .shell-fs { top: auto; bottom: 14px; right: 12px; padding: 8px 12px; font-size: 0.74rem; }
        }

        /* --- 首页「专注与打卡」数据条（2026-09-21 双视角改版）---
           番茄钟成绩和早间回顾打卡合成一张卡，两个视角切换：
           周曲线看形状（这几天是不是在掉），月热力图看密度（这个月有没有整段空掉）。 */
        .strip-loading { font-size: 0.8rem; color: var(--text-muted); padding: 6px 0; }
        .strip-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
        .strip-title { font-size: 0.92rem; font-weight: 600; font-family: var(--font-serif);
            color: var(--text-primary); }
        .strip-grow { flex: 1 1 auto; }
        .strip-kpis { display: flex; align-items: flex-start; gap: 22px; flex-wrap: wrap; margin-bottom: 14px; }
        .strip-kpi { font-size: 0.72rem; color: var(--text-secondary); }
        .strip-kpi b { display: block; font-size: 1.18rem; font-variant-numeric: tabular-nums;
            color: var(--text-primary); line-height: 1.35; }
        .strip-kpi.zhu b { color: var(--zhusha-lt); }
        .strip-kpi.qing b { color: var(--zhuqing-lt); }
        .strip-kpi.xiang b { color: var(--xiang-lt); }
        .strip-sep { width: 1px; height: 34px; background: var(--border-color); flex: 0 0 auto; }
        .strip-view { min-height: 188px; }
        .strip-hint { font-size: 0.7rem; color: var(--text-muted); margin-top: 10px; line-height: 1.75; }
        .strip-empty { font-size: 0.76rem; color: var(--text-muted); padding: 4px 0 2px; }
        .strip-link { font: inherit; font-size: 0.72rem; cursor: pointer; padding: 3px 11px;
            border-radius: 10px; background: none; border: 1px solid var(--border-color);
            color: var(--text-secondary); }
        .strip-link:hover { color: var(--text-primary); border-color: var(--dianqing); }

        /* --- 周曲线 ---
           SVG 走 preserveAspectRatio="none" 横向铺满，横竖拉伸比不一样，
           所以圆点和数字不用 <circle>，改成绝对定位的 HTML 叠在上面——
           否则圆点会被压成椭圆。线条用 non-scaling-stroke 保住 2px 粗细。 */
        /* 高度和月热力图（188px）对齐，切视角时下面的内容不会跳一下 */
        .curve-wrap { position: relative; height: 188px; }
        /* 顶部 16px 留给峰值那天的数字，底部 32px 给星期和状态点——
           基线离轴太近的话，零值那几天的平线会从「五六日」脸上压过去 */
        .curve-plot { position: absolute; left: 0; right: 0; top: 16px; bottom: 32px; }
        .curve-svg { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
        .curve-area { stroke: none; }
        .curve-line { fill: none; stroke: var(--zhusha); stroke-width: 2;
            stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }
        .curve-base { stroke: var(--border-color); stroke-width: 1; vector-effect: non-scaling-stroke; }
        .curve-layer { position: absolute; inset: 0; }
        .curve-pt { position: absolute; width: 0; height: 0; }
        .curve-pt i { position: absolute; left: -4px; top: -4px; width: 8px; height: 8px;
            border-radius: 50%; background: var(--zhusha); box-shadow: 0 0 0 2.5px var(--bg-card); }
        .curve-pt.zero i { background: var(--mo-hover); }
        .curve-pt.today i { box-shadow: 0 0 0 2.5px var(--bg-card),
            0 0 0 4.5px rgba(var(--xiang-rgb), .45); }
        .curve-pt b { position: absolute; left: 0; bottom: 11px; transform: translateX(-50%);
            font-size: 0.62rem; font-weight: 500; font-variant-numeric: tabular-nums;
            color: var(--text-muted); white-space: nowrap; }
        .curve-axis { position: absolute; left: 0; right: 0; bottom: 0; height: 32px; }
        .curve-axis-x { position: absolute; top: 0; transform: translateX(-50%); text-align: center; }
        .curve-axis-x span { display: block; font-size: 0.62rem; color: var(--text-muted); }
        .curve-axis-x em { display: block; width: 6px; height: 6px; margin: 4px auto 0;
            border-radius: 50%; background: transparent; border: 1px solid var(--border-color); }
        .curve-axis-x em.studied { background: rgba(var(--dianqing-rgb), .55);
            border-color: var(--dianqing); }
        .curve-axis-x em.checked { background: var(--zhuqing); border-color: var(--zhuqing); }

        /* --- 月热力图 ---
           行=周，列=周一到周日，铺最近 5 周。两个维度挤在一格里：
           底色写专注时长，描边写打卡——填充管「练了多久」，描边管「打没打卡」。 */
        .hm-wrap { display: flex; align-items: flex-start; gap: 34px; flex-wrap: wrap; }
        /* 左标签 32 + 7 列 28 + 6 道 4px 缝 = 256；行高 28 × 6 + 缝 = 188，
           正好和曲线的 .curve-wrap 一样高，切视角时下面不跳。 */
        .hm-grid { display: grid; grid-template-columns: 32px repeat(7, 28px);
            grid-auto-rows: 28px; gap: 4px; flex: 0 0 auto; }
        .hm-wd { display: flex; align-items: center; justify-content: center;
            font-size: 0.62rem; color: var(--text-muted); }
        .hm-wk { display: flex; align-items: center; font-size: 0.62rem; color: var(--text-muted);
            font-variant-numeric: tabular-nums; }
        .hm-cell { border-radius: 5px; background: var(--bg-secondary);
            border: 1px solid var(--border-color); }
        .hm-cell.l1 { background: rgba(var(--zhusha-rgb), .18); border-color: rgba(var(--zhusha-rgb), .24); }
        .hm-cell.l2 { background: rgba(var(--zhusha-rgb), .34); border-color: rgba(var(--zhusha-rgb), .40); }
        .hm-cell.l3 { background: rgba(var(--zhusha-rgb), .52); border-color: rgba(var(--zhusha-rgb), .58); }
        .hm-cell.l4 { background: rgba(var(--zhusha-rgb), .72); border-color: rgba(var(--zhusha-rgb), .78); }
        .hm-cell.l5 { background: rgba(var(--zhusha-rgb), .95); border-color: var(--zhusha-lt); }
        .hm-cell.future { background: transparent; border-style: dashed; opacity: .45; }
        /* 圈要压得住底色（最深那档是实心朱砂），所以给到 2px 并提亮一档 */
        .hm-cell.checked { box-shadow: inset 0 0 0 2px var(--zhuqing-lt); }
        .hm-cell.studied { box-shadow: inset 0 0 0 2px var(--dianqing-lt); }
        .hm-cell.today { outline: 2px solid var(--xiang-lt); outline-offset: 1px; }
        /* 侧栏只占自己那份宽（别 flex:1 把空档吃光，否则右边那组汇总数就没法靠边了），
           说明用 flex-wrap 横向铺开。窄屏放不下时整块换行。 */
        .hm-side { flex: 0 1 auto; min-width: 0; }
        .hm-legend { display: flex; align-items: center; gap: 4px; margin-bottom: 14px; flex-wrap: wrap; }
        .hm-legend .hm-cell, .hm-note .hm-cell { width: 14px; height: 14px; border-radius: 3px; }
        .hm-legend-t { font-size: 0.66rem; color: var(--text-muted); margin: 0 5px; }
        .hm-notes { display: flex; flex-wrap: wrap; gap: 6px 26px; margin-bottom: 12px; }
        .hm-note { display: flex; align-items: center; gap: 8px; font-size: 0.66rem;
            color: var(--text-muted); }
        .hm-note .hm-cell { background: rgba(var(--zhusha-rgb), .34); }
        /* 三个汇总数靠右顶住卡片边：不这么摆的话，宽屏上热力图右边会空一大片 */
        .hm-stats { display: flex; gap: 30px; flex: 0 0 auto; margin-left: auto; }
        .hm-kpi { font-size: 0.7rem; color: var(--text-secondary); }
        .hm-kpi b { display: block; font-size: 1.1rem; font-variant-numeric: tabular-nums;
            color: var(--text-primary); line-height: 1.35; margin-top: 2px; }
        .hm-kpi.zhu b { color: var(--zhusha-lt); }
        .hm-kpi.qing b { color: var(--zhuqing-lt); }
        .hm-kpi.xiang b { color: var(--xiang-lt); }
'''

SHELL_JS = '''
// ============================================================
// 页面外壳（2026-09-21）
//  ① 右上角「整页全屏」：F11 那种，任何子页都能按。
//     与番茄钟 / 闪卡各自的「模块全屏」是两件事，别混。
//  ② 首页「专注与打卡」数据条：番茄钟成绩（读 POMO_JS 的 __pomoStats）
//     + 早间回顾打卡（/api/morning-review/overview）。
// ============================================================
(function () {
    const API = location.protocol.startsWith('http') ? location.origin : "http://localhost:8080";

    function esc(s) {
        const d = document.createElement("div");
        d.textContent = s == null ? "" : String(s);
        return d.innerHTML;
    }
    function toast(msg) {
        const t = document.createElement("div");
        t.className = "fs-toast";
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(function () { if (t.remove) t.remove(); }, 2200);
    }

    // ---------- ① 整页全屏 ----------
    const fsBtn = document.getElementById("shell-fs");
    if (fsBtn) {
        const pageIsFull = () => !!document.fullscreenElement
            && document.fullscreenElement === document.documentElement;
        function syncFs() {
            const on = pageIsFull();
            fsBtn.className = "shell-fs" + (on ? " on" : "");
            fsBtn.textContent = on ? "✕ 退出全屏" : "\\u26F6 全屏";
            fsBtn.title = on ? "退出整页全屏（Esc）" : "整页全屏（Esc 退出）";
            fsBtn.setAttribute("aria-pressed", on ? "true" : "false");
        }
        fsBtn.onclick = function () {
            // 已经全屏（不管是整页还是某个模块）就先退出来，否则用户按「全屏」
            // 反而会卡在模块全屏里出不去
            try {
                if (document.fullscreenElement) {
                    const pr = document.exitFullscreen ? document.exitFullscreen() : null;
                    if (pr && pr.catch) pr.catch(function () {});
                } else {
                    // 用 <html> 而不是 <body>：body 全屏后，fixed 定位的元素在部分
                    // 浏览器里会失去视口参照，右上角按钮和自定义背景层会一起跑偏
                    const el = document.documentElement;
                    if (!el.requestFullscreen) throw new Error("no api");
                    const pr = el.requestFullscreen();
                    if (pr && pr.catch) pr.catch(function () { toast("这个浏览器拒绝了全屏请求，可以按 F11"); });
                }
            } catch (e) { toast("这个浏览器不支持整页全屏，可以按 F11"); }
        };
        document.addEventListener("fullscreenchange", syncFs);
        syncFs();
    }

    // ---------- ③ 鼠标粒子拖尾（2026-09-22，改为「细尘」）----------
    // 鼠标走过后留一串细腻的小点：没有大亮点、不闪、不炸——像扫过一层薄灰，
    // 细细地飘一小段就化没了。此前那版「离子星火」太亮太闪（布灵布灵的），
    // 用户明确要改成低调的粒子。几条自我约束：
    //   · **每个子页都生效**，切页不中断
    //   · 只有鼠标 / 触控笔触发（手指拖动不出，免得平板上满屏光点还费电）
    //   · 粒子放完就**停掉 rAF**（省电；下一次 pointermove 再启）
    //   · 设置里关掉、或系统开了「减弱动效」→ 整段不跑
    (function mouseFx() {
        const cv = document.getElementById("mouse-fx");
        if (!cv || !cv.getContext) return;
        const REDUCED = !!(window.matchMedia
            && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
        const ctx = cv.getContext("2d");
        const MAXP = 260;                 // 粒子上限：再密也只是白烧 CPU，260 够走完整条拖尾
        let W = 0, H = 0, dpr = 1, on = true, raf = 0, parts = [];
        let lastX = null, lastY = null;

        function resize() {
            dpr = Math.min(2, window.devicePixelRatio || 1);
            W = window.innerWidth || 1024; H = window.innerHeight || 768;
            cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        }
        // 颜色跟主题走：深色主题用提亮版，浅色主题用原色。
        // 统一降到中低亮度——细尘要的是「看得见但压得住」，不是发光。
        function palette() {
            const cs = getComputedStyle(document.documentElement);
            const g = (k, dflt) => (cs.getPropertyValue(k) || "").trim() || dflt;
            return (document.documentElement.dataset.theme === "light")
                ? [g("--zhuqing", "#6F9A8D"), g("--dianqing", "#5B7C99"), g("--xiang", "#C89B4A")]
                : [g("--zhuqing-lt", "#9CC4B6"), g("--dianqing-lt", "#92B4D0"), g("--xiang-lt", "#E0C07E")];
        }
        // 回退安全的角度换算（atan2 对 0/0 会返回 NaN，得兜一手）
        function angleOf(dx, dy) {
            const t = Math.atan2(dy, dx);
            return Number.isFinite(t) ? t : 0;
        }
        // 撒一粒细尘。没有「头/尾」之分：所有粒子同一种脾性，大小随机但都偏小，
        // 沿轨迹撒下、带一点点朝鼠标行进方向的漂移，缓缓消散。
        // 上一版压到 0.42 透明度、速度又轻，快看不见了（用户反馈「直接没有了」），
        // 这里提到约 0.8 却仍是最小的小圆点、普通混合、不发光不闪烁——看得见但很干净。
        function push(x, y, angle, speed) {
            const pal = palette();
            parts.push({
                x: x + (Math.random() - 0.5) * 2.5,
                y: y + (Math.random() - 0.5) * 2.5,
                vx: Math.cos(angle) * speed,
                vy: Math.sin(angle) * speed - 0.05,
                r: 0.9 + Math.random() * 1.8,         // 0.9 ~ 2.7px，细但清晰
                life: 1,
                decay: 0.012 + Math.random() * 0.02,
                c: pal[(Math.random() * pal.length) | 0]
            });
            if (parts.length > MAXP) parts.splice(0, parts.length - MAXP);
        }
        function frame() {
            raf = 0;
            ctx.clearRect(0, 0, W, H);
            // 普通混合：粒子只是「干净的点」，不发光、不闪烁、不叠出光晕。
            // 每帧单层小圆 + 透明度随寿命线性收掉。
            for (let i = parts.length - 1; i >= 0; i--) {
                const p = parts[i];
                p.x += p.vx; p.y += p.vy;
                p.vx *= 0.96; p.vy *= 0.96;           // 轻阻尼：尘埃滑一小段就停住化掉
                p.life -= p.decay;
                if (p.life <= 0) { parts.splice(i, 1); continue; }
                ctx.globalAlpha = p.life * 0.8;
                ctx.fillStyle = p.c;
                ctx.beginPath();
                ctx.arc(p.x, p.y, Math.max(0.2, p.r * p.life), 0, Math.PI * 2);
                ctx.fill();
            }
            ctx.globalAlpha = 1;
            if (parts.length) raf = requestAnimationFrame(frame);
        }
        function tick() { if (!raf && parts.length) raf = requestAnimationFrame(frame); }
        function clearAll() {
            parts.length = 0; lastX = lastY = null;
            if (raf) { cancelAnimationFrame(raf); raf = 0; }
            ctx.clearRect(0, 0, W, H);
        }
        function apply() {
            const show = on && !REDUCED;
            if (!show) { clearAll(); cv.hidden = true; return; }
            cv.hidden = false;
        }
        function move(e) {
            if (!on || REDUCED) return;
            if (e.pointerType && e.pointerType !== "mouse" && e.pointerType !== "pen") return;
            const x = e.clientX, y = e.clientY;
            if (lastX == null) { lastX = x; lastY = y; }
            const dx = x - lastX, dy = y - lastY;
            const dist = Math.sqrt(dx * dx + dy * dy);
            // 沿轨迹均匀撒点：快移时两点间距大，用插值补点保证拖尾连续。
            // 每点朝移动的反方向轻轻带一点速度，粒子自然落在轨迹上而不是糊在光标上。
            // dist/6 比上一版密一些，不然快速一扫就稀疏得看不见。
            const back = angleOf(-dx, -dy);
            const steps = Math.min(8, Math.round(dist / 6));
            for (let i = 1; i <= steps; i++) {
                push(lastX + dx * (i / steps), lastY + dy * (i / steps),
                     back + (Math.random() - 0.5) * 1.2, 0.5 + Math.random() * 1.0);
                if (!(i % 3)) {   // 每隔两个补点多撒一粒，轨迹更绵密而不糊成一条线
                    push(lastX + dx * (i / steps), lastY + dy * (i / steps),
                         back + Math.PI + (Math.random() - 0.5) * 1.6, 0.25 + Math.random() * 0.5);
                }
            }
            if (!steps && dist > 1.0) {
                push(x, y, back + (Math.random() - 0.5) * 1.2, 0.5 + Math.random() * 0.8);
            }
            lastX = x; lastY = y;
            tick();
        }
        function loadPref() {
            // 与服务端核对（和主题一样：服务端是唯一真相源，多设备一致）
            fetch(API + "/api/settings").then(r => r.json()).then(d => {
                if (d && d.ok && d.ui) { on = (d.ui.mouse_fx || "on") !== "off"; apply(); }
            }).catch(function () {});
        }
        globalThis.__mouseFxReload = loadPref;
        resize();
        apply();
        if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
            window.addEventListener("resize", function () { resize(); apply(); });
            window.addEventListener("pointermove", move, { passive: true });
            // 切子页时清掉上一页残留的粒子，每页重新起笔
            window.addEventListener("hashchange", function () { lastX = lastY = null; });
        }
        document.addEventListener("visibilitychange", function () {
            if (document.hidden) clearAll(); else apply();
        });
        if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
            window.addEventListener("blur", function () { lastX = lastY = null; clearAll(); });
        }
        loadPref();
        setTimeout(apply, 0);
    })();

    // ---------- ④ 首页「专注与打卡」数据条 ----------
    //  番茄钟成绩和早间回顾打卡合成一张卡：这两个数回答的是同一个问题
    //  「这些天到底练了多少」，并排摆反而要眼睛来回找。
    //  两个视角各管一头——周曲线看形状（这几天是不是在掉），
    //  月热力图看密度（这个月有没有整段空掉）。选哪边记在 localStorage。
    const strip = document.getElementById("focus-strip");
    if (!strip) return;

    const VIEW_KEY = "strip.view";
    let view = "week";
    // 无痕模式下 localStorage 会直接抛异常，不能让它带崩整条数据条
    try { if (localStorage.getItem(VIEW_KEY) === "month") view = "month"; } catch (e) {}

    function pad(n) { return (n < 10 ? "0" : "") + n; }
    function dstr(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
    // 以「学习日」为最后一天往前数 n 天。用日历日的话，0:00~4:00 之间曲线尾巴会
    // 多出一格空的、而服务端刚记的那笔还挂在前一天上，看着像今天白干了。
    function lastDays(n) {
        const out = [];
        const p = studyDay().split("-");
        const t = new Date(+p[0], +p[1] - 1, +p[2]);
        for (let i = n - 1; i >= 0; i--) {
            const d = new Date(t.getFullYear(), t.getMonth(), t.getDate() - i);
            out.push(dstr(d));
        }
        return out;
    }
    function fmtMin(min) {
        const h = Math.floor(min / 60), m = min % 60;
        return (h > 0 ? h + " 小时" : "") + (h > 0 && m > 0 ? " " : "") + (m > 0 || h === 0 ? m + " 分" : "");
    }
    function wd(dateStr) {
        const p = String(dateStr).split("-");
        const d = new Date(+p[0], +p[1] - 1, +p[2]);
        return "日一二三四五六".charAt(d.getDay());
    }
    // 折线平滑：Catmull-Rom 转三次贝塞尔。x 是均匀的，控制点取 dx/6 就够顺；
    // 端点各缺一个邻居，直接用自己顶上（切线减半，不会甩出去）。
    function smooth(pts) {
        if (pts.length < 2) return "";
        let d = "M" + pts[0][0].toFixed(2) + "," + pts[0][1].toFixed(2);
        for (let i = 0; i < pts.length - 1; i++) {
            const p0 = pts[i - 1] || pts[i], p1 = pts[i];
            const p2 = pts[i + 1], p3 = pts[i + 2] || pts[i + 1];
            d += " C" + (p1[0] + (p2[0] - p0[0]) / 6).toFixed(2) + "," + (p1[1] + (p2[1] - p0[1]) / 6).toFixed(2)
               + " " + (p2[0] - (p3[0] - p1[0]) / 6).toFixed(2) + "," + (p2[1] - (p3[1] - p1[1]) / 6).toFixed(2)
               + " " + p2[0].toFixed(2) + "," + p2[1].toFixed(2);
        }
        return d;
    }
    // 点上的数字用紧凑写法：90 分钟 → 1.5h，450 → 7.5h。四位数的「450」会挤到邻居。
    function fmtShort(min) {
        if (min <= 0) return "";
        return min >= 60 ? (Math.round(min / 6) / 10) + "h" : min + "m";
    }
    // 专注时长分档（月热力图底色）。按本段最长的一天取相对值，不写死绝对值——
    // 状态好的月份和状态差的月份都能看出层次。
    function lvl(min, max) {
        if (min <= 0) return 0;
        return Math.max(1, Math.min(5, Math.ceil(min / max * 5)));
    }

    // --- 视角①：周曲线（近 7 天专注时长，格子底下挂打卡状态）---
    function weekHtml(byDay, mk) {
        const win7 = lastDays(7);
        const today = win7[win7.length - 1];
        const mins = win7.map(x => (byDay[x] || {}).min || 0);
        if (!mins.some(v => v > 0)) {
            return '<div class="strip-empty">近 7 天还没有专注记录。在总览页顶部的番茄钟里点「▶ 开始」，'
                 + "每跑完一段专注就会自动记上一笔（多设备共用同一份）。</div>";
        }
        // viewBox 是 0..100 的百分比空间（preserveAspectRatio="none"）。
        // BOT 是零值的基线，压在 86 而不是 94：留给下面那排星期和状态点。
        const max = Math.max(1, ...mins);
        const X0 = 4, X1 = 96, TOP = 26, BOT = 86;
        const xs = i => X0 + (X1 - X0) * i / (win7.length - 1);
        const ys = v => BOT - (BOT - TOP) * v / max;
        const pts = win7.map((x, i) => [xs(i), ys(mins[i])]);
        const line = smooth(pts);
        const area = line + " L" + X1.toFixed(2) + "," + BOT + " L" + X0.toFixed(2) + "," + BOT + " Z";

        const dots = win7.map((x, i) => {
            const rec = byDay[x] || { pomos: 0, min: 0 };
            const tip = x + "：" + (rec.pomos || 0) + " 个番茄 · " + fmtMin(rec.min || 0)
                + (mk.checked.has(x) ? " · 已打卡" : (mk.studied.has(x) ? " · 有复习" : ""));
            return '<div class="curve-pt' + (mins[i] ? "" : " zero") + (x === today ? " today" : "")
                + '" style="left:' + xs(i).toFixed(2) + "%;bottom:" + (100 - ys(mins[i])).toFixed(2) + '%">'
                + '<b title="' + esc(tip) + '">' + fmtShort(mins[i]) + "</b>"
                + '<i title="' + esc(tip) + '"></i></div>';
        }).join("");
        const axis = win7.map((x, i) => '<div class="curve-axis-x" style="left:'
            + xs(i).toFixed(2) + '%" title="' + esc(x) + '">'
            + "<span>" + wd(x) + "</span>"
            + '<em class="' + (mk.checked.has(x) ? "checked" : (mk.studied.has(x) ? "studied" : ""))
            + '"></em></div>').join("");

        return '<div class="curve-wrap">'
            + '<div class="curve-plot">'
            +   '<svg class="curve-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">'
            +     '<defs><linearGradient id="curveFill" x1="0" y1="0" x2="0" y2="1">'
            +       '<stop offset="0%" stop-color="rgba(184,74,66,.34)"/>'
            +       '<stop offset="100%" stop-color="rgba(184,74,66,0)"/>'
            +     "</linearGradient></defs>"
            +     '<path class="curve-area" d="' + area + '" fill="url(#curveFill)"/>'
            +     '<line class="curve-base" x1="0" y1="' + BOT + '" x2="100" y2="' + BOT + '"/>'
            +     '<path class="curve-line" d="' + line + '"/>'
            +   "</svg>"
            +   '<div class="curve-layer">' + dots + "</div>"
            + "</div>"
            + '<div class="curve-axis">' + axis + "</div>"
            + "</div>";
    }

    // --- 视角②：月热力图（最近 5 周，行=周，列=周一到周日）---
    function monthHtml(byDay, mk) {
        // 网格也锚在学习日上，跟前两者一套（见 lastDays 的注释）
        const tp = studyDay().split("-");
        const t = new Date(+tp[0], +tp[1] - 1, +tp[2]);
        const today = dstr(t);
        const dow = (t.getDay() + 6) % 7;                       // 周一 = 0
        const monday = new Date(t.getFullYear(), t.getMonth(), t.getDate() - dow);
        const start = new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() - 28);

        const WEEKS = 5;
        let max = 0, sum = 0, active = 0;
        const grid = [];
        for (let i = 0; i < WEEKS * 7; i++) {
            const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
            const ds = dstr(d);
            const future = ds > today;
            const rec = byDay[ds] || { pomos: 0, min: 0 };
            if (!future) {
                max = Math.max(max, rec.min || 0);
                sum += rec.min || 0;
                if (rec.min > 0) active++;
            }
            grid.push({ date: ds, min: rec.min || 0, pomos: rec.pomos || 0, future: future });
        }

        let cells = '<div class="hm-wd"></div>'
            + "一二三四五六日".split("").map(c => '<div class="hm-wd">' + c + "</div>").join("");
        for (let w = 0; w < WEEKS; w++) {
            const first = grid[w * 7];
            const p = first.date.split("-");
            cells += '<div class="hm-wk">' + (+p[1]) + "/" + (+p[2]) + "</div>";
            for (let i = 0; i < 7; i++) {
                const c = grid[w * 7 + i];
                const n = c.future ? "future" : "l" + lvl(c.min, Math.max(1, max));
                const ring = (!c.future && mk.checked.has(c.date)) ? " checked"
                    : ((!c.future && mk.studied.has(c.date)) ? " studied" : "");
                const tip = c.future ? c.date + "（还没到）"
                    : c.date + "：" + (c.min > 0 ? c.pomos + " 个番茄 · " + fmtMin(c.min) : "没有专注记录")
                      + (mk.checked.has(c.date) ? " · 已打卡" : (mk.studied.has(c.date) ? " · 有复习" : ""));
                // hm-d = 日子格（图例里那几个色块也挂 .hm-cell，靠这个类分开）
                cells += '<div class="hm-cell hm-d ' + n + ring + (c.date === today ? " today" : "")
                    + '" title="' + esc(tip) + '"></div>';
            }
        }

        const side =
              '<div class="hm-side">'
            +   '<div class="hm-legend"><span class="hm-legend-t">专注时长</span>'
            +     '<i class="hm-cell"></i><i class="hm-cell l1"></i><i class="hm-cell l2"></i>'
            +     '<i class="hm-cell l3"></i><i class="hm-cell l4"></i><i class="hm-cell l5"></i>'
            +     '<span class="hm-legend-t">少 → 多</span></div>'
            +   '<div class="hm-notes">'
            +     '<div class="hm-note"><i class="hm-cell l3 checked"></i>已打卡</div>'
            +     '<div class="hm-note"><i class="hm-cell l3 studied"></i>有复习内容但没打卡</div>'
            +     '<div class="hm-note"><i class="hm-cell today"></i>今天</div>'
            +   "</div>"
            + "</div>"                                  // ← 先收 .hm-side
            + '<div class="hm-stats">'                  //   汇总数得是 .hm-wrap 的直接子元素，
            +   '<div class="hm-kpi zhu">近 5 周专注<b>' + fmtMin(sum) + "</b></div>"
            +   '<div class="hm-kpi qing">有专注的天数<b>' + active + " 天</b></div>"
            +   '<div class="hm-kpi xiang">最久的一天<b>' + fmtMin(max) + "</b></div>"
            + "</div>";                                 //   不然 margin-left:auto 靠不了右

        // 那 5 周一次没练过就别摆一排空格子了，直接说明白
        const body = active === 0
            ? '<div class="strip-empty">近 5 周还没有专注记录。跑完一段番茄钟就会在这里点亮一格。</div>'
            : '<div class="hm-grid">' + cells + "</div>" + side;
        return '<div class="hm-wrap">' + body + "</div>";
    }

    let POMO = null, MR = null, mrLoaded = false;
    function render() {
        const d = (POMO && POMO.day) || { pomos: 0, min: 0 };
        const byDay = {};
        ((POMO && POMO.days) || []).forEach(x => { byDay[x.date] = x; });
        const win7 = lastDays(7);
        const today = win7[win7.length - 1];
        const sum7 = win7.reduce((a, x) => a + ((byDay[x] || {}).pomos || 0), 0);
        const min7 = win7.reduce((a, x) => a + ((byDay[x] || {}).min || 0), 0);

        // 打卡集合：接口没回来（或本地服务没起）时留空集合，
        // 曲线底下的点会全部显示成空心——比整条数据条消失好。
        const mk = {
            checked: new Set((MR && MR.checkins) || []),
            studied: new Set(((MR && MR.days) || []).filter(x => x.studied).map(x => x.date)),
        };
        const checkedToday = mk.checked.has(today);
        // MR 读不到时打卡那几个数写成 "--"：显示 0 天会让人以为断签了
        const mrNum = v => mrLoaded ? (v || 0) : "--";

        const head = '<div class="strip-head">'
            + '<span class="strip-title">🍅 专注与打卡</span>'
            + '<div class="range-toggle" id="strip-view">'
            +   '<button class="range-btn' + (view === "week" ? " is-active" : "")
            +     '" data-view="week">周曲线</button>'
            +   '<button class="range-btn' + (view === "month" ? " is-active" : "")
            +     '" data-view="month">月热力图</button>'
            + "</div>"
            + '<span class="strip-grow"></span>'
            + '<button class="strip-link" id="strip-go-pomo">去跑一轮</button>'
            + '<button class="strip-link" id="strip-go-mr">'
            +   (checkedToday ? "今日已打卡 ✓" : "去打卡") + "</button>"
            + "</div>";

        const kpis = '<div class="strip-kpis">'
            +   '<div class="strip-kpi zhu">今日番茄<b>' + d.pomos + " 个</b></div>"
            +   '<div class="strip-kpi zhu">今日专注<b>' + fmtMin(d.min) + "</b></div>"
            +   '<div class="strip-kpi">近 7 天<b>' + sum7 + " 个 · " + fmtMin(min7) + "</b></div>"
            +   '<div class="strip-sep"></div>'
            +   '<div class="strip-kpi qing">连续打卡<b>' + mrNum(MR && MR.streak) + " 天</b></div>"
            +   '<div class="strip-kpi qing">累计打卡<b>' + mrNum(MR && MR.total_days) + " 天</b></div>"
            +   '<div class="strip-kpi xiang">今天<b>' + (checkedToday ? "已打卡" : "未打卡") + "</b></div>"
            + "</div>";

        const hint = view === "month"
            ? "底色深浅＝那天专注了多久（相对近 5 周最久的一天）· 绿圈＝已打卡 · "
              + "蓝圈＝当天有复习内容但没打卡 · 今天那格带橙框。"
              + (MR && (MR.checkins || []).length
                  ? "最近一次打卡：" + esc((MR.checkins || []).slice(-1)[0]) + "。" : "")
              + (pomoShort
                  ? " ※ 番茄记录只读到近 14 天：本地服务没起，或是还在跑不认识 ?days=42 的旧版——"
                    + "重启「考研复习服务」后就能铺满 5 周。" : "")
            : "曲线是每天的专注时长；圆点下面那格是当天的打卡状态（绿＝已打卡 · 蓝＝有复习没打卡）。"
              + "跑完一段专注自动计一个，跳过或中途结束不计。";

        strip.innerHTML = head + kpis + '<div class="strip-view">'
            + (view === "month" ? monthHtml(byDay, mk) : weekHtml(byDay, mk)) + "</div>"
            + '<div class="strip-hint">' + hint + "</div>";

        strip.querySelectorAll("#strip-view .range-btn").forEach(b => {
            b.onclick = () => {
                if (b.dataset.view === view) return;
                view = b.dataset.view;
                try { localStorage.setItem(VIEW_KEY, view); } catch (e) {}
                render();
            };
        });
        const b1 = document.getElementById("strip-go-pomo");
        if (b1) b1.onclick = () => { const t = document.getElementById("pm-slot");
            if (t && t.scrollIntoView) t.scrollIntoView({ behavior: "smooth", block: "start" }); };
        const b2 = document.getElementById("strip-go-mr");
        if (b2) b2.onclick = () => { location.hash = "#/review"; };
    }

    let mrCache = null, mrAt = 0;
    function loadMr(force) {
        const now = Date.now();
        if (!force && mrCache && now - mrAt < 120000) return Promise.resolve(mrCache);
        return fetch(API + "/api/morning-review/overview").then(r => r.json()).then(d => {
            mrCache = (d && d.ok) ? d : null;
            mrAt = now;
            return mrCache;
        }).catch(() => null);
    }
    // 退回 14 天时置位：月视角只铺得起 2 周，得说一声，
    // 不然 3 周前的空格子会被当成「那天没学」——这是会骗人的。
    let pomoShort = false;
    function pomoStats() {
        // 要 42 天：月热力图铺 5 周，POMO_JS 手里那份只有 14 天。
        // 今日那一格优先用 POMO_JS 的：刚记完一笔时它比接口回包还新。
        return fetch(API + "/api/pomodoro/state?days=42").then(r => {
            if (!r.ok) throw new Error("http " + r.status);
            return r.json();
        }).then(d => {
            if (!d || !d.ok) throw new Error("bad payload");
            let day = d.today_stat;
            if (typeof globalThis.__pomoStats === "function") {
                try { const p = globalThis.__pomoStats(); if (p && p.day) day = p.day; } catch (e) {}
            }
            pomoShort = false;
            return { day: day, days: d.days || [] };
        }).catch(() => {
            // 接口够不着（直接开 file:// 没起服务），或服务端还是不认识 ?days= 的旧版
            pomoShort = true;
            if (typeof globalThis.__pomoStats === "function") {
                try { const p = globalThis.__pomoStats(); if (p) return p; } catch (e) {}
            }
            return { day: null, days: [] };
        });
    }
    function reload(force) {
        // 番茄钟那边刚记完一笔会立刻叫一声，那次不能被 2 分钟的缓存挡住
        const p = pomoStats();
        const m = loadMr(!!force);
        Promise.all([p, m]).then(([po, mr]) => {
            POMO = po;
            MR = mr;
            mrLoaded = !!mr;
            render();
        });
    }
    // 番茄钟那边记完一笔会叫一声（见 POMO_JS 的 __focusStripReload）
    globalThis.__focusStripReload = function () { reload(true); };
    reload(false);
    // 每 60 秒顺手对一次打卡状态（早间回顾那页打卡后，这条不用刷新整页也会变）
    setInterval(function () { if (!document.hidden) reload(true); }, 60000);
})();
'''


# ---------------------------------------------------------------------------
# 笔记搜索 + 读笔记时的提问（2026-09-21）
#
# ① 搜索：标题与正文一起搜（空格分词 = 全部要命中；整句搜不到会自动拆词再搜一轮）
# ② 筛选：科目 / 子科 / 章节 / 级别 / 标签，筛完直接点开笔记
# ③ 「就问这段」：读笔记时把**当前正在读的那一段**当上下文问 AI，多轮追问，
#    问答落 SQLite（note_qa 表），下次读同一篇还能翻出上次问过什么
#
# 后端在 serve.js（/api/notes/search · /api/notes/ask · /api/notes/qa），
# 阅读器弹层归 REVIVE_JS——它通过 globalThis.__noteQaMount 把面板挂上来，
# 并传一个 getContext() 告诉我们「现在读到哪一段」。两边都不硬依赖对方。
# ---------------------------------------------------------------------------

NOTEQ_CSS = '''
        /* --- 笔记搜索（2026-09-21）--- */
        .ns-top { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; }
        .ns-inputwrap { position: relative; flex: 1; min-width: 0; display: flex; align-items: center; }
        .ns-icon { position: absolute; left: 12px; font-size: 0.9rem; opacity: .6; pointer-events: none; }
        .ns-input { width: 100%; box-sizing: border-box; font: inherit; font-size: 0.92rem;
            padding: 11px 38px 11px 36px; border-radius: 10px; color: var(--text-primary);
            background: var(--bg-primary); border: 1px solid var(--border-color); }
        .ns-input:focus { outline: none; border-color: var(--dianqing);
            box-shadow: 0 0 0 3px rgba(var(--dianqing-rgb), .16); }
        .ns-input::placeholder { color: var(--text-muted); }
        .ns-input::-webkit-search-cancel-button { display: none; }
        .ns-clear { position: absolute; right: 8px; font: inherit; font-size: 0.8rem; line-height: 1;
            cursor: pointer; padding: 5px 8px; background: none; border: 0; border-radius: 6px;
            color: var(--text-muted); }
        .ns-clear:hover { color: var(--text-primary); background: var(--bg-secondary); }
        .ns-browse { font: inherit; font-size: 0.8rem; cursor: pointer; padding: 10px 16px;
            border-radius: 10px; background: var(--bg-primary); color: var(--text-secondary);
            border: 1px solid var(--border-color); white-space: nowrap; }
        .ns-browse:hover { color: var(--text-primary); border-color: var(--dianqing); }
        .ns-facets { display: flex; flex-direction: column; gap: 7px; margin-bottom: 12px; }
        .ns-frow { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
        .ns-flabel { font-size: 0.7rem; color: var(--text-muted); flex: none; width: 34px; text-align: right; }
        .ns-chips { display: flex; gap: 6px; flex-wrap: wrap; }
        .ns-chip { font: inherit; font-size: 0.74rem; cursor: pointer; padding: 3px 10px;
            border-radius: 11px; background: var(--bg-primary); color: var(--text-secondary);
            border: 1px solid var(--border-color); transition: all .15s; }
        .ns-chip:hover { color: var(--text-primary); border-color: var(--dianqing); }
        .ns-chip.on { background: rgba(var(--dianqing-rgb), .18); border-color: var(--dianqing);
            color: var(--dianqing-lt); }
        .ns-chip i { font-style: normal; opacity: .55; margin-left: 4px; font-size: 0.68rem; }
        .ns-meta { font-size: 0.74rem; color: var(--text-muted); margin-bottom: 10px; line-height: 1.8; }
        .ns-meta b { color: var(--text-secondary); }
        .ns-results { display: flex; flex-direction: column; gap: 8px; max-height: 46vh; overflow-y: auto; }
        .ns-hint { font-size: 0.78rem; color: var(--text-muted); padding: 6px 0; line-height: 1.9; }
        .ns-item { border: 1px solid var(--border-color); border-radius: 8px; padding: 10px 12px;
            background: var(--bg-primary); cursor: pointer; transition: border-color .15s, background .15s; }
        .ns-item:hover { border-color: var(--dianqing); background: var(--bg-secondary); }
        .ns-item-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
        .ns-item-title { font-size: 0.88rem; font-weight: 600; color: var(--text-primary); }
        .ns-item-title mark, .ns-item-snip mark { background: rgba(var(--xiang-rgb), .3);
            color: var(--xiang-lt); border-radius: 2px; padding: 0 2px; }
        .ns-badge { font-size: 0.66rem; padding: 1px 7px; border-radius: 9px;
            background: var(--bg-secondary); color: var(--text-muted); border: 1px solid var(--border-color); }
        .ns-badge.hot { background: rgba(var(--zhuqing-rgb), .16); color: var(--zhuqing-lt);
            border-color: rgba(var(--zhuqing-rgb), .4); }
        .ns-badge.lv { background: rgba(var(--dianqing-rgb), .14); color: var(--dianqing-lt);
            border-color: rgba(var(--dianqing-rgb), .35); }
        .ns-item-open { margin-left: auto; font-size: 0.72rem; color: var(--text-muted); flex: none; }
        .ns-item:hover .ns-item-open { color: var(--dianqing-lt); }
        .ns-item-path { font-size: 0.68rem; color: var(--text-muted); margin-top: 3px;
            font-family: Consolas, "Courier New", monospace; }
        .ns-item-snip { font-size: 0.76rem; color: var(--text-secondary); margin-top: 6px; line-height: 1.75;
            display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
        .ns-more { font-size: 0.74rem; color: var(--text-muted); padding: 2px 0 0; }

        /* --- 从搜索进来时的「定位到命中处」（2026-09-21）--- */
        .rev-hit { background: rgba(var(--xiang-rgb), .28); color: var(--xiang-lt);
            border-radius: 3px; padding: 0 1px; transition: background .2s; }
        .rev-hit.cur { background: rgba(var(--xiang-rgb), .55); color: var(--xuan);
            box-shadow: 0 0 0 2px rgba(var(--xiang-rgb), .28); }
        .rev-hit-head { animation: revHitFlash 2.4s ease-out 1; }
        @keyframes revHitFlash {
            0% { background: rgba(var(--xiang-rgb), .35); }
            100% { background: transparent; }
        }
        /* 关键词重叠定位的整块闪烁：滚到最像的那段，整段泛一下光再褪掉 */
        .rev-hit-node { animation: revHitFlash 2.4s ease-out 1; }
        /* 命中导航条：粘在正文顶部，滚多远都够得着 */
        .rev-find { position: sticky; top: 0; z-index: 6; display: flex; align-items: center;
            gap: 8px; margin: -6px 0 14px; padding: 7px 12px; border-radius: 6px;
            background: rgba(var(--mo-rgb), .94); border: 1px solid var(--border-color);
            font-size: 0.74rem; color: var(--text-secondary);
            -webkit-backdrop-filter: blur(6px); backdrop-filter: blur(6px); }
        .rev-find-t { flex: 1; min-width: 0; }
        .rev-find b { color: var(--xiang-lt); }
        .rev-find button { font: inherit; font-size: 0.76rem; line-height: 1; cursor: pointer;
            padding: 4px 9px; border-radius: 5px; background: var(--bg-primary);
            color: var(--text-secondary); border: 1px solid var(--border-color); }
        .rev-find button:hover { color: var(--text-primary); border-color: var(--dianqing); }
        @media (prefers-reduced-motion: reduce) {
            .rev-hit-head { animation: none; }
            .rev-hit { transition: none; }
        }

        /* --- 读笔记时的「就问这段」面板 --- */
        .rev-modal-box.qa-open { width: min(1180px, 96vw); }
        .rev-qa { display: none; width: 340px; flex: none; border-left: var(--rule);
            background: var(--bg-secondary); flex-direction: column; min-height: 0; }
        .rev-qa:not([hidden]) { display: flex; }
        .rev-modal-box.full .rev-qa { width: 380px; }
        .qa-head { display: flex; align-items: center; gap: 8px; padding: 12px 14px 10px;
            border-bottom: var(--rule); }
        .qa-head b { font-size: 0.82rem; color: var(--text-primary); }
        .qa-sec { flex: 1; min-width: 0; font-size: 0.68rem; color: var(--text-muted);
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .qa-new { font: inherit; font-size: 0.7rem; cursor: pointer; padding: 3px 9px; border-radius: 8px;
            background: var(--bg-primary); color: var(--text-secondary); border: 1px solid var(--border-color); }
        .qa-new:hover { color: var(--text-primary); border-color: var(--dianqing); }
        .qa-ctx { margin: 10px 14px 0; padding: 8px 10px; border-radius: 6px; font-size: 0.7rem;
            color: var(--text-muted); background: var(--bg-primary); border: 1px dashed var(--border-color);
            max-height: 74px; overflow: hidden; cursor: pointer; line-height: 1.7; }
        .qa-ctx.open { max-height: 220px; overflow-y: auto; }
        .qa-log { flex: 1; min-height: 0; overflow-y: auto; padding: 10px 14px; }
        .qa-turn { margin-bottom: 12px; }
        .qa-q { font-size: 0.78rem; color: var(--text-primary); margin-bottom: 6px; }
        .qa-q::before { content: "我："; color: var(--text-muted); }
        .qa-a { font-size: 0.8rem; line-height: 1.75; color: var(--text-secondary); }
        .qa-a .md-p { margin: 0 0 7px; }
        .qa-a .md-h { margin: 9px 0 5px; font-size: 0.85rem; color: var(--text-primary); }
        .qa-a .md-ul { margin: 4px 0 8px 16px; }
        .qa-a .md-pre { margin: 7px 0; padding: 8px 10px; background: var(--bg-primary);
            border-radius: 4px; overflow-x: auto; font-size: 0.74rem; }
        .qa-a .md-code { padding: 1px 4px; background: var(--bg-primary); border-radius: 3px;
            font-family: Consolas, "Courier New", monospace; font-size: 0.9em; }
        .qa-a .md-table { width: 100%; border-collapse: collapse; margin: 6px 0; font-size: 0.74rem;
            display: block; overflow-x: auto; }
        .qa-a .md-table th, .qa-a .md-table td { border: 1px solid var(--border-color); padding: 4px 8px; }
        .qa-loading { font-size: 0.75rem; color: var(--text-muted); }
        .qa-err { font-size: 0.75rem; color: var(--xiang-lt); line-height: 1.7; }
        .qa-hist { border-top: var(--rule); padding: 9px 14px; max-height: 150px; overflow-y: auto; }
        .qa-hist-t { font-size: 0.7rem; color: var(--text-muted); margin-bottom: 6px; }
        .qa-hist-i { font-size: 0.72rem; color: var(--text-secondary); cursor: pointer;
            padding: 3px 0; border-bottom: 1px dashed var(--border-color); }
        .qa-hist-i:hover { color: var(--dianqing-lt); }
        .qa-ask { border-top: var(--rule); padding: 10px 14px 12px; display: flex; gap: 8px; }
        .qa-in { flex: 1; min-width: 0; box-sizing: border-box; font: inherit; font-size: 0.8rem;
            padding: 8px 10px; border-radius: 8px; resize: vertical; min-height: 40px; max-height: 120px;
            background: var(--bg-primary); color: var(--text-primary); border: 1px solid var(--border-color); }
        .qa-in:focus { outline: none; border-color: var(--dianqing); }
        .qa-send { flex: none; font: inherit; font-size: 0.78rem; cursor: pointer; padding: 8px 16px;
            border-radius: 8px; background: var(--bg-primary); color: var(--zhuqing-lt);
            border: 1px solid var(--zhuqing); }
        .qa-send:hover { background: rgba(var(--zhuqing-rgb), .14); }
        .qa-send:disabled { opacity: .5; cursor: default; }
        /* 窄屏：提问面板改成底部抽屉，别跟正文抢宽度 */
        @media (max-width: 900px) {
            .rev-modal-main { flex-direction: column; }
            .rev-qa { width: auto; border-left: 0; border-top: var(--rule); max-height: 52vh; }
            .rev-modal-box.qa-open { width: min(900px, 96vw); }
        }
        /* --- 触屏设备（平板/手机）上的提问与追问框（2026-09-21）---
           ⚠️ iOS Safari 聚焦 font-size < 16px 的输入框会**自动放大整页**，
              页面一缩放，发送键就跳到别处去了——看着就像「点了没反应」。
              触屏一律给 16px，同时把点按目标做大一点。 */
        @media (pointer: coarse) {
            .fs-exp-input, .qa-in, .ns-input { font-size: 16px; }
            .fs-exp-send, .qa-send, .ns-browse { min-height: 42px; padding: 10px 18px; font-size: 0.86rem; }
            .fs-exp-ask, .qa-ask { padding: 12px 14px; }
            .qa-hist-i { padding: 7px 0; }
            .fs-exp-retry { padding: 5px 12px; }
        }
'''


NOTEQ_JS = '''
// ============================================================
// 笔记搜索 + 读笔记时的提问（2026-09-21）
//   ① 搜索框：标题与正文一起搜（空格分词 = 全部命中；整句搜不到自动拆词再搜）
//   ② 筛选：科目 / 子科 / 章节 / 级别 / 标签 → 筛出来直接点开读
//   ③ 「就问这段」面板：由阅读器（REVIVE_JS）通过 globalThis.__noteQaMount 挂载，
//      上下文＝当前视口里那几段文字（getContext 由阅读器提供）＋小节名；
//      多轮追问，问答落服务端 note_qa 表，下次读同一篇能看到历史。
// ============================================================
(function () {
    const API = location.protocol.startsWith('http') ? location.origin : "http://localhost:8080";

    function esc(s) {
        const d = document.createElement("div");
        d.textContent = s == null ? "" : String(s);
        return d.innerHTML;
    }
    function toast(msg) {
        const t = document.createElement("div");
        t.className = "fs-toast";
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(function () { if (t.remove) t.remove(); }, 2200);
    }
    function md(raw) {
        // 复用闪卡那边调好的 Markdown+LaTeX 渲染器（表格/公式都认）
        if (typeof globalThis.__mdTex === "function") {
            try { return globalThis.__mdTex(raw); } catch (e) { /* 退回纯文本 */ }
        }
        return "<p>" + esc(raw).replace(/\\n/g, "<br>") + "</p>";
    }
    // 把命中的关键词标出来（先转义再包 mark，顺序不能反）
    function highlight(text, tokens) {
        let html = esc(text);
        (tokens || []).forEach(function (t) {
            if (!t || t.length < 1) return;
            const re = new RegExp("(" + t.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&") + ")", "gi");
            html = html.replace(re, "<mark>$1</mark>");
        });
        return html;
    }

    // ---------------- ① 搜索 + ② 筛选 ----------------
    // ⚠️ 搜索这一段单独包一层：容器不在（产物被裁过 / 别的页面引用了这段脚本）
    //    只跳过搜索，**不能 return 掉整个模块**——下面的「定位命中处」和
    //    「就问这段」跟搜索容器没关系，被一起带走就白丢了功能（探针页里炸过）。
    (function initSearch() {
    const input = document.getElementById("ns-input");
    const clearBtn = document.getElementById("ns-clear");
    const browseBtn = document.getElementById("ns-browse");
    const facetBox = document.getElementById("ns-facets");
    const metaBox = document.getElementById("ns-meta");
    const listBox = document.getElementById("ns-results");
    if (!input || !listBox) return;      // 不在「笔」页：只跳过搜索

    const F = { subject: "", sub: "", level: "", chapter: "", tag: "" };
    let facets = null, timer = 0, lastTook = 0, results = [], how = "", soft = [], tokens = [];
    // 高亮用「整句关键词 + 拆出来的词」的并集：拆词模式下命中词在 soft 里，
    // 整句模式下在 tokens 里，只取其中一个都会漏标。
    function hlWords() {
        const seen = {};
        return tokens.concat(soft).filter(function (x) {
            const k = String(x || "").toLowerCase();
            if (!k || seen[k]) return false;
            seen[k] = 1; return true;
        });
    }

    function hasFilter() { return !!(F.subject || F.sub || F.level || F.chapter || F.tag); }
    function qs() {
        const p = [];
        if (input.value.trim()) p.push("q=" + encodeURIComponent(input.value.trim()));
        ["subject", "sub", "level", "chapter", "tag"].forEach(function (k) {
            if (F[k]) p.push(k + "=" + encodeURIComponent(F[k]));
        });
        p.push("limit=60");
        return p.join("&");
    }

    function renderFacets() {
        if (!facetBox) return;
        if (!facets) { facetBox.innerHTML = ""; return; }
        const row = function (label, list, key, fmt) {
            if (!list || !list.length) return "";
            const chip = function (item) {
                const val = item.value;
                const on = F[key] === val;
                return '<button class="ns-chip' + (on ? " on" : "") + '" data-f="' + key
                    + '" data-v="' + esc(val) + '">' + esc(fmt ? fmt(item) : val)
                    + '<i>' + item.n + "</i></button>";
            };
            return '<div class="ns-frow"><span class="ns-flabel">' + label + "</span>"
                + '<div class="ns-chips">' + list.map(chip).join("") + "</div></div>";
        };
        facetBox.innerHTML =
              row("科目", facets.subjects, "subject", function (x) { return x.label || x.value; })
            + row("子科", facets.subs, "sub", function (x) { return x.label || x.value; })
            + row("章节", facets.chapters, "chapter", function (x) { return x.value; })
            + row("级别", facets.levels, "level", function (x) { return x.value; })
            + row("标签", facets.tags, "tag", function (x) { return x.value; });
        facetBox.querySelectorAll("[data-f]").forEach(function (b) {
            b.onclick = function () {
                const k = b.dataset.f, v = b.dataset.v;
                F[k] = (F[k] === v) ? "" : v;
                // 选了子科就顺带把科目也定上（两个筛选是「与」的关系，不定上会互相打脸）
                if (k === "sub" && F.sub) {
                    const hit = (facets.subs || []).filter(x => x.value === v)[0];
                    if (hit && hit.subject) F.subject = hit.subject;
                }
                if (k === "subject" && F.subject && F.sub) {
                    const ok = (facets.subs || []).some(x => x.value === F.sub && x.subject === F.subject);
                    if (!ok) F.sub = "";
                }
                renderFacets();
                run(true);
            };
        });
    }

    function renderResults() {
        if (!results.length) {
            listBox.innerHTML = input.value.trim()
                ? '<div class="ns-hint">没找到包含「' + esc(input.value.trim()) + '」的笔记。'
                  + '<br>试试少写几个字、换同义词，或点上面的科目/子科直接浏览。</div>'
                : '<div class="ns-hint">输入关键词搜标题与正文；或点上方的科目 / 子科 / 章节 / 级别 / 标签筛选，'
                  + '筛出来的笔记点一下就能打开读。</div>';
            return;
        }
        listBox.innerHTML = results.map(function (r, i) {
            const chips = [];
            chips.push('<span class="ns-badge">' + esc(r.subjectLabel || r.subject) + "</span>");
            if (r.subName) chips.push('<span class="ns-badge">' + esc(r.subName) + "</span>");
            if (r.chapter) chips.push('<span class="ns-badge">' + esc(r.chapter) + "</span>");
            if (r.level) chips.push('<span class="ns-badge lv">' + esc(r.level) + "</span>");
            if (r.hits > 0) chips.push('<span class="ns-badge hot">命中 ' + r.hits + "</span>");
            if (r.orphan) chips.push('<span class="ns-badge">仅索引条目</span>');
            return '<div class="ns-item" data-i="' + i + '">'
                + '<div class="ns-item-head">'
                +   '<span class="ns-item-title">' + highlight(r.title || r.name, hlWords()) + "</span>"
                +   chips.join("")
                +   '<span class="ns-item-open">' + (r.path ? (r.hits > 0 ? "打开并定位 →" : "打开 →") : "无对应文件") + "</span>"
                + "</div>"
                + '<div class="ns-item-path">' + esc(r.path || "（这条只在笔记索引里，没有链接到 .md 文件）") + "</div>"
                + (r.snippet ? '<div class="ns-item-snip">' + highlight(r.snippet, hlWords()) + "</div>" : "")
                + "</div>";
        }).join("");
        listBox.querySelectorAll(".ns-item").forEach(function (el) {
            el.onclick = function () {
                const r = results[+el.dataset.i];
                if (!r) return;
                if (!r.path) { toast("这条只在索引里，没有对应的笔记文件"); return; }
                if (typeof globalThis.__revOpenNote === "function") {
                    // ⚠️ 定位用的关键词要用**服务端实际匹配到的词**：整句没命中时它会自动拆词
                    //    （「旋转和平衡」→ 旋转 + 平衡）。拿原句去正文里找必然找不到，于是
                    //    只剩「滚到小标题那一节」这个兜底 —— 用户反馈「定位不太成功」就是这个。
                    const words = hlWords();
                    globalThis.__revOpenNote({
                        file: r.path, name: r.title || r.name,
                        // 只按标题命中时不必定位（正文里没有它）
                        query: r.hits > 0 ? (words.join(" ") || input.value.trim()) : "",
                        anchor: r.anchor || "",
                    });
                } else { toast("阅读器还没加载好，稍后再点一次"); }
            };
        });
    }

    function renderMeta() {
        if (!metaBox) return;
        const bits = [];
        if (input.value.trim()) bits.push("关键词 <b>" + esc(input.value.trim()) + "</b>");
        const fs = [];
        ["subject", "sub", "level", "chapter", "tag"].forEach(function (k) { if (F[k]) fs.push(F[k]); });
        if (fs.length) bits.push("筛选 <b>" + esc(fs.join(" · ")) + "</b>");
        if (how === "split" && soft.length) {
            bits.push('整句没搜到，已按拆开的关键词 <b>' + esc(soft.join(" + ")) + "</b> 匹配");
        } else if (how === "any" && soft.length) {
            bits.push('按 <b>' + esc(soft.join(" / ")) + "</b> 中任一词匹配（可能不全）");
        }
        bits.push("共 <b>" + results.length + "</b> 篇" + (lastTook ? "（" + lastTook + "ms）" : ""));
        metaBox.innerHTML = bits.join(" · ");
    }

    async function run(force) {
        if (!facets || force === "facets") {
            // 首次（或显式刷新）顺带把筛选选项拉回来
        }
        try {
            const url = API + "/api/notes/search?" + qs() + (facets ? "&facets=0" : "&facets=1");
            const d = await (await fetch(url)).json();
            if (!d.ok) throw new Error(d.error || "搜索失败");
            if (d.facets) facets = d.facets;
            results = d.results || [];
            how = d.how || ""; soft = d.soft || []; tokens = d.tokens || [];
            lastTook = d.took_ms || 0;
            renderFacets(); renderMeta(); renderResults();
        } catch (e) {
            listBox.innerHTML = '<div class="ns-hint">搜索失败：' + esc(e.message)
                + "<br>（本地服务没起来？用桌面「启动考研大盘.bat」打开本页）</div>";
        }
    }
    function debounced() { clearTimeout(timer); timer = setTimeout(run, 260); }

    input.addEventListener("input", function () {
        if (clearBtn) clearBtn.hidden = !input.value;
        debounced();
    });
    input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !(e.isComposing || e.keyCode === 229)) { e.preventDefault(); clearTimeout(timer); run(true); }
        else if (e.key === "Escape") { input.value = ""; if (clearBtn) clearBtn.hidden = true; run(true); }
    });
    if (clearBtn) clearBtn.onclick = function () {
        input.value = ""; clearBtn.hidden = true; input.focus(); run(true);
    };
    if (browseBtn) browseBtn.onclick = function () {
        input.value = ""; if (clearBtn) clearBtn.hidden = true;
        F.subject = ""; F.sub = ""; F.level = ""; F.chapter = ""; F.tag = "";
        renderFacets(); run(true);
        listBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
    };
    // 懒渲染：真进到「笔」页才拉索引（首屏不必读 700 个文件）
    (window.__pageRenderers = window.__pageRenderers || {});
    (window.__pageRenderers.notes = window.__pageRenderers.notes || []).push(function () { run(true); });
    })();   // initSearch 结束

    // ---------------- ④ 「打开并定位到命中处」 ----------------
    // 搜索命中的是**正文**时，打开笔记要直接跳到那一处，而不是让人自己翻。
    // 做法：遍历渲染后正文的文本节点找关键词 → 包成 <mark class="rev-hit"> →
    // 滚到第一处 + 顶部给一条「命中 N 处 / 上一处 / 下一处」的小条。
    // 兜底：markdown 可能把词拆进不同标签（`进**程**`），这时按服务端算出的
    // 最近小标题文本滚到那一节（mode=anchor）。
    function textNodesOf(root) {
        const out = [];
        if (!root || typeof document.createTreeWalker !== "function") return out;
        const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
            acceptNode: function (n) {
                if (!n.nodeValue || !n.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
                const p = n.parentNode;
                if (!p) return NodeFilter.FILTER_REJECT;
                const tag = String(p.nodeName || "").toUpperCase();
                if (tag === "SCRIPT" || tag === "STYLE" || tag === "MARK" || tag === "TEXTAREA") {
                    return NodeFilter.FILTER_REJECT;
                }
                return NodeFilter.FILTER_ACCEPT;
            },
        });
        let n;
        while ((n = w.nextNode())) out.push(n);
        return out;
    }
    function clearMarks(root) {
        if (!root || !root.querySelectorAll) return;
        Array.prototype.slice.call(root.querySelectorAll("mark.rev-hit")).forEach(function (m) {
            const p = m.parentNode;
            if (!p) return;
            p.replaceChild(document.createTextNode(m.textContent || ""), m);
            p.normalize && p.normalize();
        });
        const bar = root.querySelector(".rev-find");
        if (bar && bar.remove) bar.remove();
    }
    function markText(root, tokens) {
        const marks = [];
        const lows = tokens.map(function (t) { return String(t).toLowerCase(); }).filter(Boolean);
        textNodesOf(root).forEach(function (node) {
            const text = node.nodeValue || "";
            const low = text.toLowerCase();
            const spans = [];
            lows.forEach(function (t) {
                let from = 0, guard = 0;
                while (guard++ < 200) {
                    const at = low.indexOf(t, from);
                    if (at < 0) break;
                    spans.push([at, at + t.length]);
                    from = at + t.length;
                }
            });
            if (!spans.length) return;
            spans.sort(function (a, b) { return a[0] - b[0]; });
            const merged = [];
            spans.forEach(function (s) {
                const last = merged[merged.length - 1];
                if (last && s[0] <= last[1]) { last[1] = Math.max(last[1], s[1]); }
                else merged.push([s[0], s[1]]);
            });
            // 每个文本节点内部从后往前切、把 mark 记进 local（unshift 保证节点内顺序），
// 最后按**节点顺序** push 进总表 —— 直接在总表上 unshift 会让后一个段落的
// 命中排到前面去，于是「下一处」反而往回跳（探针页实测踩过）。
            const local = [];
            for (let i = merged.length - 1; i >= 0; i--) {
                const a = merged[i][0], b = merged[i][1];
                const after = node.splitText(b);
                const mid = node.splitText(a);
                const m = document.createElement("mark");
                m.className = "rev-hit";
                mid.parentNode.insertBefore(m, mid);
                m.appendChild(mid);
                local.unshift(m);
                void after;              // 右半边保持原样，不再参与切割
            }
            for (let k = 0; k < local.length; k++) marks.push(local[k]);
        });
        return marks;
    }
    function goToMark(marks, i) {
        marks.forEach(function (m, k) { m.classList.toggle("cur", k === i); });
        const m = marks[i];
        if (m && m.scrollIntoView) {
            try { m.scrollIntoView({ behavior: "smooth", block: "center" }); }
            catch (e) { try { m.scrollIntoView(); } catch (e2) {} }
        }
    }
    function findHeading(root, anchor) {
        if (!root || !root.querySelectorAll || !anchor) return null;
        const want = String(anchor).replace(/\s+/g, "");
        const hs = root.querySelectorAll("h1, h2, h3, h4");
        // 第一遍：整体匹配（子串，双向都算）——命中说明标题就是小标题原文。
        for (let i = 0; i < hs.length; i++) {
            const t = String(hs[i].textContent || "").replace(/\s+/g, "");
            if (t && (t.indexOf(want) >= 0 || want.indexOf(t) >= 0)) return hs[i];
        }
        // 第二遍：标题带括号/破折号时，整体往往撞不上笔记小标题（如
        // 「扩展操作码（变长操作码）」→ 笔记小标题只有「…扩展操作码技术」）。
        // 那就按分隔符拆成关键词，挑「笔记里真实出现的那段」，取最长的那截去定位，
        // 保证能滚到对应那一节而不是打开在笔记开头。
        const segs = want.split(/[（）()\[\]{}、，,。.；;：:/\\s—–-]+/)
            .map(function (s) { return s.trim(); })
            .filter(function (s) { return s.length >= 2; });
        let best = null, bestLen = 0;
        for (let i = 0; i < hs.length; i++) {
            const t = String(hs[i].textContent || "").replace(/\s+/g, "");
            for (let k = 0; k < segs.length; k++) {
                if (segs[k].length > bestLen && t.indexOf(segs[k]) >= 0) {
                    best = hs[i]; bestLen = segs[k].length;
                }
            }
        }
        return best;
    }
    // 标题≠笔记小标题、正文又没原句时（早间回顾的条目基本都这样——title 是「话题」，
    // body 是改写句），按关键词重叠挑「命中词累计长度最大」的那一格滚过去整块闪一下。
    function keywordFlash(root, text) {
        if (!root || !text) return 0;
        const kws = [], seen = {};
        String(text).split(/[^\u4e00-\u9fa5A-Za-z0-9]+/g)
            .forEach(function (s) {
                if (!s || s.length < 2) return;
                const k = s.toLowerCase();
                if (!seen[k]) { seen[k] = 1; kws.push(k); }
            });
        if (!kws.length) return 0;
        let best = null, bestScore = 0;
        textNodesOf(root).forEach(function (n) {
            const low = (n.nodeValue || "").toLowerCase();
            let sc = 0;
            for (let i = 0; i < kws.length; i++) if (low.indexOf(kws[i]) >= 0) sc += kws[i].length;
            if (sc > bestScore) { bestScore = sc; best = n; }
        });
        if (!best || bestScore <= 0) return 0;
        let el = best;
        while (el && el !== root && !/^(P|LI|H[1-6]|TD|DIV|BLOCKQUOTE|PRE|UL|OL)$/i.test(el.nodeName)) {
            el = el.parentNode;
        }
        if (!el || el === root) el = best.parentNode || best;
        try { el.scrollIntoView({ behavior: "smooth", block: "center" }); }
        catch (e) { try { el.scrollIntoView(); } catch (e2) {} }
        if (el.classList) {
            el.classList.add("rev-hit-node");
            setTimeout(function () { if (el.classList) el.classList.remove("rev-hit-node"); }, 2600);
        }
        return 1;
    }
    globalThis.__noteHitLocate = function (root, opts) {
        if (!root || !opts) return { count: 0, mode: "none" };
        const query = String(opts.query || "").trim();
        const tokens = query.split(/\s+/).map(function (s) { return s.trim(); }).filter(Boolean);
        clearMarks(root);
        const marks = tokens.length ? markText(root, tokens) : [];
        if (marks.length) {
            // 顶部小条：命中几处 + 上一处/下一处 + 清除
            const bar = document.createElement("div");
            bar.className = "rev-find";
            bar.innerHTML = '<span class="rev-find-t">正文里命中 <b>' + marks.length + '</b> 处「'
                + esc(query) + '」</span>'
                + '<button data-a="prev" title="上一处">↑</button>'
                + '<button data-a="next" title="下一处">↓</button>'
                + '<button data-a="close" title="清除高亮"></button>';
            root.insertBefore(bar, root.firstChild);
            let cur = 0;
            const jump = function (i) { cur = (i + marks.length) % marks.length; goToMark(marks, cur); };
            Array.prototype.slice.call(bar.querySelectorAll("button")).forEach(function (b) {
                b.onclick = function () {
                    const a = b.getAttribute("data-a");
                    if (a === "prev") jump(cur - 1);
                    else if (a === "next") jump(cur + 1);
                    else { clearMarks(root); }
                };
            });
            goToMark(marks, 0);
            return { count: marks.length, mode: "text" };
        }
        const head = findHeading(root, opts.anchor);
        if (head) {
            try { head.scrollIntoView({ behavior: "smooth", block: "start" }); }
            catch (e) { try { head.scrollIntoView(); } catch (e2) {} }
            if (head.classList) head.classList.add("rev-hit-head");
            setTimeout(function () { if (head.classList) head.classList.remove("rev-hit-head"); }, 2600);
            return { count: 0, mode: "anchor" };
        }
        const kwText = String(opts.keywords || opts.anchor || opts.query || "").trim();
        if (kwText && keywordFlash(root, kwText)) return { count: 0, mode: "keyword" };
        return { count: 0, mode: "none" };
    };

    // ---------------- ③ 「就问这段」面板 ----------------
    // 阅读器把面板容器与「当前读到哪一段」的取法一起交过来（see REVIVE_JS.openNote）
    globalThis.__noteQaMount = function (pane, opts) {
        if (!pane || !opts || !opts.path) return;
        let threadId = "";
        const state = { busy: false };
        pane.innerHTML =
              '<div class="qa-head"><b>💬 就问这段</b><span class="qa-sec" data-f="sec">正在读：—</span>'
            + '  <button class="qa-new" data-act="new">新对话</button></div>'
            + '<div class="qa-ctx" data-f="ctx" title="点一下展开/收起：这就是要发给 AI 的上下文">（正在读取上下文…）</div>'
            + '<div class="qa-log" data-f="log"><div class="qa-hint" style="font-size:0.74rem;color:var(--text-muted);line-height:1.8;">'
            + '读到不懂的地方，直接问。发出去的是<b>你此刻在看的这一段</b>＋你的问题，不是整篇笔记。</div></div>'
            + '<div class="qa-hist" data-f="hist" hidden></div>'
            + '<div class="qa-ask">'
            + '  <textarea class="qa-in" data-f="in" rows="2" enterkeyhint="send" autocapitalize="off"'
            + '    placeholder="就这段问点什么…（Enter 发送，Shift+Enter 换行）"></textarea>'
            + '  <button class="qa-send" data-act="send">发送</button>'
            + '</div>';
        const $ = function (n) { return pane.querySelector('[data-f="' + n + '"]'); };
        const logBox = $("log"), ctxBox = $("ctx"), secBox = $("sec"), inBox = $("in"), histBox = $("hist");

        function refreshCtx() {
            let c = { section: "", text: "" };
            try { c = opts.getContext() || c; } catch (e) {}
            const head = (c.section ? c.section + " · " : "") + (c.text ? c.text.length + " 字" : "没读到正文");
            ctxBox.textContent = head + (c.text ? "　—　" + c.text.slice(0, 90).replace(/\\s+/g, " ") + "…" : "");
            secBox.textContent = "正在读：" + (c.section || "（还没到小标题）");
            return c;
        }
        refreshCtx();
        if (opts.scroller && opts.scroller.addEventListener) {
            let last = 0;
            opts.scroller.addEventListener("scroll", function () {
                const now = Date.now();
                if (now - last < 400) return;      // 滚动里别疯狂重算
                last = now; refreshCtx();
            });
        }
        ctxBox.onclick = function () { ctxBox.classList.toggle("open"); };

        function addTurn(q, a) {
            const t = document.createElement("div");
            t.className = "qa-turn";
            t.innerHTML = '<div class="qa-q">' + esc(q) + "</div>"
                + '<div class="qa-a">' + (a == null ? '<span class="qa-loading">思考中…</span>' : md(a)) + "</div>";
            logBox.appendChild(t);
            logBox.scrollTop = logBox.scrollHeight;
            return t;
        }
        function showErr(msg) {
            const d = document.createElement("div");
            d.className = "qa-err";
            d.textContent = "⚠ " + msg;
            logBox.appendChild(d);
            logBox.scrollTop = logBox.scrollHeight;
        }
        function renderThread(messages, withHist) {
            logBox.innerHTML = "";
            for (let i = 0; i < messages.length; i += 2) {
                addTurn(messages[i] ? messages[i].content : "", messages[i + 1] ? messages[i + 1].content : "");
            }
            if (withHist) {
                const d = document.createElement("div");
                d.className = "qa-hint";
                d.style.cssText = "font-size:0.72rem;color:var(--text-muted);margin-top:6px;";
                d.textContent = "（这是以前问过的记录）";
                logBox.appendChild(d);
            }
        }
        async function send() {
            const q = (inBox.value || "").trim();
            if (!q) return;
            if (state.busy) { toast("还在回答上一个问题…"); return; }
            const c = refreshCtx();
            state.busy = true;
            const sendBtn = pane.querySelector('[data-act="send"]');
            if (sendBtn) sendBtn.disabled = true;
            inBox.value = "";
            const turn = addTurn(q, null);
            try {
                const r = await fetch(API + "/api/notes/ask", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        path: opts.path, section: c.section, context: c.text,
                        question: q, thread_id: threadId || undefined,
                    }),
                });
                const d = await r.json();
                if (!d.ok) throw new Error(d.error || "回答失败");
                threadId = d.thread_id || threadId;
                turn.querySelector(".qa-a").innerHTML = md(d.text);
                logBox.scrollTop = logBox.scrollHeight;
                loadHistory(true);
            } catch (e) {
                turn.querySelector(".qa-a").innerHTML = '<span class="qa-err">⚠ ' + esc(e.message) + "</span>";
            } finally {
                state.busy = false;
                if (sendBtn) sendBtn.disabled = false;
            }
        }
        async function loadHistory(quiet) {
            try {
                const d = await (await fetch(API + "/api/notes/qa?path=" + encodeURIComponent(opts.path)
                    + "&limit=8")).json();
                if (!d.ok) return;
                const threads = d.threads || [];
                if (!threads.length) { histBox.hidden = true; return; }
                histBox.hidden = false;
                histBox.innerHTML = '<div class="qa-hist-t">这篇笔记以前问过 ' + threads.length + ' 次'
                    + '（点一条回看）</div>'
                    + threads.map(function (t, i) {
                        return '<div class="qa-hist-i" data-i="' + i + '">'
                            + esc((t.first_question || t.messages[0] && t.messages[0].content || "").slice(0, 46))
                            + '<span style="color:var(--text-muted);"> · ' + esc((t.at || "").replace("T", " ").slice(5, 16))
                            + "</span></div>";
                    }).join("");
                histBox.querySelectorAll(".qa-hist-i").forEach(function (el) {
                    el.onclick = function () {
                        const t = threads[+el.dataset.i];
                        if (!t) return;
                        renderThread(t.messages, true);
                        threadId = t.thread_id;      // 接着这条继续问
                    };
                });
                if (!quiet && threads[0] && !logBox.querySelector(".qa-turn")) {
                    renderThread(threads[0].messages, true);
                    threadId = threads[0].thread_id;
                }
            } catch (e) { /* 读不到历史不影响提问 */ }
        }
        pane.querySelector('[data-act="new"]').onclick = function () {
            threadId = "";
            logBox.innerHTML = '<div class="qa-hint" style="font-size:0.74rem;color:var(--text-muted);">'
                + "新对话。发出去的是你此刻在看的这一段＋你的问题。</div>";
            inBox.focus();
        };
        pane.querySelector('[data-act="send"]').onclick = send;
        inBox.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && !e.shiftKey && !(e.isComposing || e.keyCode === 229)) { e.preventDefault(); send(); }
        });
        // 软键盘会盖住底部：聚焦时把输入框滚进可视区
        inBox.addEventListener("focus", function () {
            setTimeout(function () {
                try { inBox.scrollIntoView({ block: "center", behavior: "smooth" }); } catch (e2) {}
            }, 320);
        });
        loadHistory(false);
    };
})();
'''


# ---------------------------------------------------------------------------
# 早间回顾（并入大盘，2026-09-20）
# ---------------------------------------------------------------------------

MR_CSS = '''
        /* ============================================================
           早间回顾 —— 并入大盘后的样式。
           原来它是独立浅色页（#fafafa + 蓝色），和大盘并排非常割裂，
           所以这里一律改用大盘的色板变量：深浅主题都能自动跟随。
           ============================================================ */
        .mr-loading, .mr-empty { padding: 26px 4px; font-size: .84rem; color: var(--text-muted); line-height: 1.8; }
        .mr-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
        .mr-today { font-size: .74rem; color: var(--text-muted); white-space: nowrap; }
        .mr-stat { font-size: .74rem; color: var(--text-muted); white-space: nowrap; }
        .mr-stat b { color: var(--text-primary); }
        .mr-btn { font: inherit; font-size: .78rem; padding: 5px 13px; cursor: pointer;
            border: 1px solid var(--border-color); border-radius: var(--border-radius);
            background: var(--bg-secondary); color: var(--text-secondary); }
        .mr-btn:hover { color: var(--text-primary); border-color: var(--zhuqing); }
        .mr-btn.checked { color: var(--zhuqing-lt); border-color: var(--zhuqing);
            background: rgba(var(--zhuqing-rgb), .14); }
        .mr-btn[disabled] { opacity: .5; cursor: default; }

        .mr-card { background: var(--bg-card); border: 1px solid var(--border-color);
            border-radius: var(--border-radius); padding: 14px 16px; margin-bottom: 13px; }
        .mr-card-h { display: flex; align-items: center; gap: 8px; font-size: .92rem; font-weight: 600;
            color: var(--text-primary); padding-bottom: 9px; margin-bottom: 4px;
            border-bottom: 1px solid var(--border-color); }
        .mr-tag { margin-left: auto; font-size: .7rem; font-weight: 400; color: var(--text-muted); }
        .mr-sub { font-size: .74rem; color: var(--text-muted); margin: -2px 0 8px; }

        .mr-item { padding: 11px 0; border-bottom: 1px dashed var(--border-color); }
        .mr-item:last-child { border-bottom: none; padding-bottom: 2px; }
        .mr-badges { display: flex; gap: 5px; flex-wrap: wrap; margin-bottom: 5px; }
        .mr-badge { font-size: .67rem; padding: 1px 7px; border-radius: 9px;
            color: var(--dianqing-lt); background: rgba(var(--dianqing-rgb), .13);
            border: 1px solid rgba(var(--dianqing-rgb), .28); }
        .mr-item h4 { font-size: .89rem; color: var(--text-primary); margin-bottom: 5px; }
        .mr-item p { font-size: .84rem; line-height: 1.8; color: var(--text-secondary); }
        .mr-list { margin: 6px 0 0 18px; padding: 0; font-size: .83rem; line-height: 1.85;
            color: var(--text-secondary); }
        .mr-concl { margin-top: 7px; padding: 7px 11px; border-left: 2px solid var(--zhuqing);
            background: rgba(var(--zhuqing-rgb), .09); font-size: .82rem; line-height: 1.75;
            color: var(--text-secondary); }
        .mr-link { margin-top: 6px; font-size: .73rem; color: var(--text-muted); }
        .mr-link a { color: var(--dianqing-lt); text-decoration: none; }
        .mr-link a:hover { text-decoration: underline; }

        /* 闪卡练习 */
        .mr-tabs { display: flex; gap: 7px; flex-wrap: wrap; margin-bottom: 11px; }
        .mr-tab { font: inherit; font-size: .77rem; padding: 4px 11px; cursor: pointer;
            border: 1px solid var(--border-color); border-radius: var(--border-radius);
            background: var(--bg-secondary); color: var(--text-secondary); }
        .mr-tab.on { color: var(--zhuqing-lt); border-color: var(--zhuqing);
            background: rgba(var(--zhuqing-rgb), .13); }
        .mr-cnt { opacity: .75; font-size: .7rem; margin-left: 3px; }
        .mr-fc { padding: 13px 15px; border: 1px solid var(--border-color);
            border-radius: var(--border-radius); background: var(--bg-secondary); cursor: pointer; }
        .mr-fc:hover { border-color: var(--dianqing); }
        .mr-fc-meta { display: flex; gap: 8px; align-items: center; font-size: .7rem;
            color: var(--text-muted); margin-bottom: 7px; }
        .mr-kind { padding: 0 6px; border-radius: 8px; font-size: .67rem; }
        .mr-kind.new { color: var(--xiang-lt); background: rgba(var(--xiang-rgb), .16); }
        .mr-kind.rev { color: var(--zhuqing-lt); background: rgba(var(--zhuqing-rgb), .16); }
        .mr-q { font-size: .9rem; line-height: 1.8; color: var(--text-primary); }
        .mr-a { margin-top: 11px; padding-top: 11px; border-top: 1px dashed var(--border-color);
            font-size: .84rem; line-height: 1.85; color: var(--text-secondary); }
        .mr-acts { display: flex; gap: 8px; align-items: center; margin-top: 11px; flex-wrap: wrap; }
        .mr-hint { font-size: .72rem; color: var(--text-muted); margin-left: auto; }
        .mr-ok { border-color: var(--zhuqing); color: var(--zhuqing-lt); }
        .mr-ok:hover { background: rgba(var(--zhuqing-rgb), .13); }
        .mr-no { border-color: var(--xiang); color: var(--xiang-lt); }
        .mr-no:hover { background: rgba(var(--xiang-rgb), .13); }
        .mr-next { border-color: var(--dianqing); color: var(--dianqing-lt); }
        .mr-next:hover { background: rgba(var(--dianqing-rgb), .13); }
        .mr-btn.primary { color: var(--zhuqing-lt); border-color: var(--zhuqing);
            background: rgba(var(--zhuqing-rgb), .14); }
        .mr-btn.primary:hover { background: rgba(var(--zhuqing-rgb), .22); }
        .mr-note { margin-top: 7px; color: var(--dianqing-lt); border-color: rgba(var(--dianqing-rgb), .35); }
        .mr-note:hover { background: rgba(var(--dianqing-rgb), .13); }
        .mr-fc-start { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
            padding: 13px 15px; border: 1px solid var(--border-color); border-radius: var(--border-radius);
            background: var(--bg-secondary); }
        .mr-fc-infos { display: flex; align-items: center; gap: 7px; font-size: .8rem;
            color: var(--text-secondary); }
        .mr-fc-start .mr-hint { margin-left: auto; }
        /* 早间回顾闪卡：这一组卡是「当天回顾内容」翻出来的（2026-09-20） */
        .mr-fc-note { margin-top: 10px; padding: 10px 13px; font-size: .75rem; line-height: 1.85;
            color: var(--text-secondary); background: rgba(var(--dianqing-rgb), .07);
            border-left: 3px solid var(--dianqing); border-radius: var(--border-radius); }
        .mr-fc-note .mr-dim { color: var(--text-muted); font-size: .72rem; }

        /* 英语长难句 */
        .mr-sent { padding: 12px 14px; background: var(--bg-secondary);
            border-left: 3px solid var(--dianqing); border-radius: var(--border-radius);
            font-size: .93rem; line-height: 2.05; color: var(--text-primary); }
        .hl-blue { color: var(--dianqing-lt); }
        .hl-red { color: var(--zhusha-lt); }
        .hl-amber { color: var(--xiang-lt); }
        .mr-trans { display: none; margin-top: 9px; padding: 10px 13px; font-size: .84rem;
            line-height: 1.85; color: var(--text-secondary);
            background: rgba(var(--zhuqing-rgb), .07); border-radius: var(--border-radius); }
        .mr-trans.on { display: block; }
        .mr-struct { margin-top: 9px; padding: 11px 13px; background: var(--bg-secondary);
            border-radius: var(--border-radius); font-family: Consolas, "Courier New", monospace;
            font-size: .76rem; line-height: 1.95; color: var(--text-secondary);
            white-space: pre-wrap; overflow-x: auto; }
        .mr-h4 { font-size: .84rem; color: var(--text-primary); margin: 13px 0 6px; }
        .mr-table { width: 100%; border-collapse: collapse; font-size: .81rem; }
        .mr-table th, .mr-table td { border: 1px solid var(--border-color); padding: 5px 9px;
            text-align: left; vertical-align: top; line-height: 1.7; }
        .mr-table th { background: var(--bg-secondary); color: var(--text-primary); font-weight: 600; }
        .mr-table td { color: var(--text-secondary); }
        .mr-gram { padding: 9px 0; border-bottom: 1px dashed var(--border-color); }
        .mr-gram:last-child { border-bottom: none; }
        .mr-gram b { font-size: .84rem; color: var(--text-primary); }
        .mr-gram p { margin-top: 4px; font-size: .82rem; line-height: 1.8; color: var(--text-secondary); }
        .mr-num { display: inline-flex; align-items: center; justify-content: center;
            width: 17px; height: 17px; margin-right: 6px; border-radius: 3px; font-size: .68rem;
            color: var(--zhuqing-lt); background: rgba(var(--zhuqing-rgb), .16); }
        .mr-code, .mr-item code, .mr-a code, .mr-gram code { padding: 1px 5px;
            background: var(--bg-secondary); border-radius: 3px;
            font-family: Consolas, "Courier New", monospace; font-size: .95em; }
        /* ---- 布局优化（融入大盘，兼顾美学+效率）---- */
        .mr-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; align-items: start; }
        .mr-span2 { grid-column: 1 / -1; }
        @media (max-width: 880px) { .mr-grid { grid-template-columns: 1fr; } }
        .mr-agenda { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; padding: 11px 14px;
            margin-bottom: 14px; background: linear-gradient(90deg, rgba(var(--dianqing-rgb), .10), rgba(var(--zhusha-rgb), .06));
            border: 1px solid var(--border-color); border-radius: 10px; }
        .mr-agenda-l { display: flex; align-items: baseline; gap: 9px; white-space: nowrap; }
        .mr-agenda-t { font-size: .95rem; font-weight: 600; color: var(--text-primary); }
        .mr-agenda-amt { font-size: .74rem; color: var(--text-muted); }
        .mr-agenda-amt b { color: var(--zhusha); font-size: 1rem; }
        .mr-agenda-m { display: flex; gap: 6px; flex-wrap: wrap; }
        .mr-meter { display: inline-flex; align-items: center; gap: 5px; font-size: .72rem;
            padding: 3px 8px; border-radius: 10px; background: var(--bg-card);
            border: 1px solid var(--border-color); color: var(--text-secondary); }
        .mr-meter b { color: var(--dianqing-lt); }
        .mr-agenda-r { margin-left: auto; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
        .mr-focus-h { font-size: .72rem; color: var(--text-muted); }
        .mr-focus { font-size: .7rem; padding: 2px 9px; border-radius: 9px; color: var(--xiang-lt);
            background: rgba(var(--xiang-rgb), .12); }
        .mr-time { font-size: .68rem; font-weight: 400; color: var(--text-muted); margin-left: 6px; }
        .mr-item .mr-kind { display: inline-flex; margin-left: auto; }
'''

MR_JS = '''
// ============================================================
// 早间回顾（并入大盘，2026-09-20）
//
// 内容读服务端 morning_review.json，打卡与间隔重复状态存 SQLite。
// 平板/手机只是发请求并渲染，所有进度都留在这台电脑上——原先它们存在
// 各设备自己的 localStorage 里，换设备就各算各的，现在两端看到同一份。
//
// 正文（title/body/conclusion/句子/语法点）来自本地工作流生成的可信数据，
// 内含 <strong>/<code>/<span class="hl-*"> 等标记，因此按原样插入；
// 而 subject、日期、错误信息等运行期文本一律 esc。
// ============================================================
(function () {
    const API = location.protocol.startsWith('http') ? location.origin : "http://localhost:8080";
    const root = document.getElementById("mr-root");
    if (!root) return;

    const esc = (s) => String(s == null ? "" : s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

    // 闪卡面板当前选中的「段」（'' = 全部）。切换只影响这一组卡怎么拼。
    const S = { ov: null, date: null, day: null, fcSec: '', busy: false };

    function toast(msg) {
        const t = document.createElement("div");
        t.className = "fs-toast";
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 1900);
    }
    async function get(path) {
        const r = await fetch(API + path);
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || ("HTTP " + r.status));
        return d;
    }
    function post(path, payload) {
        return fetch(API + path, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        }).then(r => r.json());
    }

    // ---- 顶部工具条：今天的打卡 / 连续统计 ----
    // 过去每天的记录只保留在数据里（morning_review.json + SQLite），前端不展示历史天。
    function barHtml() {
        const ov = S.ov;
        const cur = ov.days.find(d => d.date === S.date) || {};
        return '<div class="mr-bar">'
            + '<button class="mr-btn' + (cur.checked ? ' checked' : '') + '" id="mr-check">'
            + (cur.checked ? '✅ 今日已打卡' : '🔖 打卡今天') + '</button>'
            + '<span class="mr-stat">连续 <b>' + (ov.streak || 0) + '</b> 天 · 累计 <b>'
            + (ov.total_days || 0) + '</b> 天</span>'
            + '<span class="mr-stat mr-today" style="margin-left:auto;">' + esc(S.date || '') + '</span></div>';
    }

    // 条目正文是**原样插入**的 HTML 片段（数据里自带 <strong>/<code>/<span>），所以这里
    // 不能转义、也不能走 richText。但字段里确实会出现 `2^n`、`(−1)^{n−1}`、`(1+x)^α`
    // 这类 ASCII 上下标写法 —— 交给共用的 asciiMathHtml：按标签切开，只转标签外的文本。
    // （2026-09-22 用户截图：数学要点的标题与正文里 `^α`、`^{n−1}` 原样漏出。）
    const rich = (s) => {
        const f = (typeof globalThis !== "undefined" && globalThis.asciiMathHtml)
            || ((x) => x);                       // 桥没搭上就原样输出，本模块要能单独跑
        return f(s == null ? "" : s);
    };

    function linkHtml(l, loc) {
        if (!l || !l.name) return '';
        // 本地笔记不能用 file:// 直接跳（打不开），改成调大盘的阅读器浮窗渲染。
        // 定位：anchor 是「滚到某小标题」、query 是「高亮正文里那几处」；早间回顾的
        // 条目标题是「话题」、正文是改写句，两个都撞不上笔记小标题，所以额外带一份
        // keywords（条目 body 的纯文本），靠关键词重叠滚到最像的那段。都放按钮
        // data-* 上，点开阅读器时再传给 __revOpenNote。
        loc = loc || {};
        return '<button type="button" class="mr-btn mr-note"'
            + ' data-note="' + esc(l.path || '') + '"'
            + ' data-name="' + esc(l.name || '') + '"'
            + ' data-anchor="' + esc(loc.anchor || '') + '"'
            + ' data-query="' + esc(loc.query || '') + '"'
            + ' data-words="' + esc(loc.keywords || '') + '"'
            + ' title="打开笔记：' + esc(l.name || '') + '">📖 打开笔记</button>';
    }

    // ---- 知识点回顾 ----
    function reviewCard(day) {
        const items = day.review || [];
        if (!items.length) return '';
        const html = items.map(it => {
            const badges = (it.badges || []).map(b => '<span class="mr-badge">' + esc(b) + '</span>').join('');
            const list = (it.list && it.list.length)
                ? '<ul class="mr-list">' + it.list.map(x => '<li>' + rich(x) + '</li>').join('') + '</ul>' : '';
            const concl = it.conclusion ? '<div class="mr-concl">' + rich(it.conclusion) + '</div>' : '';
            return '<div class="mr-item">'
                + (badges ? '<div class="mr-badges">' + badges + '</div>' : '')
                + '<h4>' + rich(it.title) + '</h4>'
                + '<p>' + rich(it.body) + '</p>' + list + concl
                + linkHtml(it.noteLink, {
                    anchor: it.title,
                    keywords: String(it.body || '').replace(/<[^>]*>/g, ' ')
                }) + '</div>';
        }).join('');
        return '<div class="mr-card"><div class="mr-card-h">📚 知识点回顾'
            + '<span class="mr-tag">' + items.length + ' 条</span></div>'
            + '<div class="mr-sub">' + esc(day.subject || '') + '</div>' + html + '</div>';
    }

    // ============================================================
    // 早间回顾闪卡（2026-09-20 按用户澄清重做）
    //
    // 用户原话：「早间的这个闪卡的作用，不是再去学一学闪卡库里的闪卡，而就是用来学
    // 早间回顾的。就是把早间回顾的内容变成翻转的闪卡。这样正好我把这个闪卡读完之后，
    // 就自动打卡，早间回顾就可以了。」
    //
    // 所以这一组卡**完全由本页的当天数据拼出来**：知识点回顾 / 今日小测 / 数学要点 / 英语，
    // 正面是「提示」（标题、问题、单词、原句），反面是那一天原文里对应的内容。
    //
    // 走闪卡练习区的**本地卡组**通道（__flashFloat.local）：
    //   · 不归卡进主闪卡库、不占每日额度、不写 review_log、不动 FSRS
    //     （旧做法把这一页的题 POST /api/study/cards 归进主库再按卡号组题，等于把早间回顾
    //      混进了闪卡库：既污染调度，又会把「点名卡号」写进筛选条件）
    //   · 进度只存本机，不影响主库「当日这一组」
    // 整组（「全部」那一段，即这一天的所有回顾内容）刷完 → **自动打卡**。
    // ============================================================
    const SEC_ICON = { rv: '📚', qz: '❓', mt: '🧮', en: '📝' };

    /** 把某一天的回顾内容拆成几段可翻的卡。front/back 都是**已拼好的 HTML**。 */
    function daySections(day) {
        const secs = [];
        const badgesHtml = (arr) => (arr && arr.length
            ? '<div class="mr-badges">' + arr.map(b => '<span class="mr-badge">' + esc(b) + '</span>').join('') + '</div>'
            : '');

        const rv = (day.review || []).map((it, i) => ({
            id: 'rv' + i,
            front: rich(it.title),
            back: badgesHtml(it.badges)
                + '<p>' + rich(it.body) + '</p>'
                + (it.list && it.list.length
                    ? '<ul class="mr-list">' + it.list.map(x => '<li>' + rich(x) + '</li>').join('') + '</ul>' : '')
                + (it.conclusion ? '<div class="mr-concl">' + rich(it.conclusion) + '</div>' : ''),
        }));
        if (rv.length) secs.push({ key: 'rv', label: '📚 知识点回顾', cards: rv });

        const qz = (day.quiz || []).map((it, i) => ({
            id: 'qz' + i,
            front: rich(it.question),
            back: rich(it.answer),
        }));
        if (qz.length) secs.push({ key: 'qz', label: '❓ 今日小测', cards: qz });

        const mt = ((day.math || {}).points || []).map((p, i) => ({
            id: 'mt' + i,
            front: rich(p.title),
            back: badgesHtml(p.badges)
                + '<p>' + rich(p.body) + '</p>'
                + (p.conclusion ? '<div class="mr-concl">' + rich(p.conclusion) + '</div>' : ''),
        }));
        if (mt.length) secs.push({ key: 'mt', label: '🧮 数学要点', cards: mt });

        const e = day.english || {};
        const en = [];
        if (e.sentence) {
            en.push({
                id: 'en0',
                front: '先自己翻译、再拆结构：<br>' + rich(e.sentence),
                back: (e.translation ? '<p><b>参考译文：</b>' + rich(e.translation) + '</p>' : '')
                    + (e.structure ? '<div class="mr-struct">' + esc(e.structure) + '</div>' : ''),
            });
        }
        (e.words || []).forEach((w, i) => en.push({
            id: 'wd' + i,
            front: '「' + esc(w.word) + '」在这句里是什么意思？',
            back: '<p><b>句中含义：</b>' + esc(w.meaning) + '</p>'
                + (w.familiar ? '<p><b>常见熟义：</b>' + esc(w.familiar) + '</p>' : ''),
        }));
        (e.grammar || []).forEach((g, i) => en.push({
            id: 'gr' + i,
            front: rich(g.title),
            back: '<p>' + rich(g.body) + '</p>',
        }));
        (e.decompose || []).forEach((d, i) => en.push({
            id: 'dc' + i,
            front: '这句里的成分「' + esc(d.badge || ('第 ' + (i + 1) + ' 处')) + '」是什么？',
            back: '<p>' + esc(d.content) + '</p>' + (d.note ? '<p>' + esc(d.note) + '</p>' : ''),
        }));
        if (en.length) secs.push({ key: 'en', label: '📝 英语一', cards: en });
        return secs;
    }

    function dayAllCards(day) {
        return daySections(day).reduce((a, s) => a.concat(s.cards), []);
    }

    // ---- 闪卡练习面板：主角是「这一天的回顾内容」，不是闪卡库 ----
    function fcCard() {
        const day = S.day;
        if (!day) return '';
        const secs = daySections(day);
        const all = dayAllCards(day);
        const cur = S.fcSec ? secs.filter(s => s.key === S.fcSec)[0] : null;
        const group = cur ? cur.cards : all;
        const st = (typeof globalThis.__mrFlashState === 'function')
            ? globalThis.__mrFlashState(S.date) : { left: 0, done: false };
        const checked = !!((S.ov.days.filter(d => d.date === S.date)[0] || {}).checked);

        const chips = ['<button class="mr-tab' + (!S.fcSec ? ' on' : '') + '" data-fc="">全部'
                + '<span class="mr-cnt">' + all.length + '</span></button>']
            .concat(secs.map(s => '<button class="mr-tab' + (S.fcSec === s.key ? ' on' : '') + '" data-fc="'
                + s.key + '">' + s.label + '<span class="mr-cnt">' + s.cards.length + '</span></button>'))
            .join('');

        let inner;
        if (!group.length) {
            inner = '<div class="mr-empty">这一天还没有可翻的回顾内容。'
                + '「morning-review 工作流」生成知识点 / 小测 / 数学要点 / 英语之后，这里就有卡了。</div>';
        } else {
            inner = '<div class="mr-fc-start">'
                + '<div class="mr-fc-infos">本组 <b>' + group.length + '</b> 张</div>'
                + '<button class="mr-btn primary" id="mr-fc-start">'
                + (st.left && !cur ? '▶ 继续本组（还剩 ' + st.left + ' 张）' : '🎯 开始闪卡练习') + '</button>'
                + (st.left && !cur ? '<button class="mr-btn" id="mr-fc-restart">↺ 从头来</button>' : '')
                + '<span class="mr-hint">翻卡看原文 → 自评</span>'
                + '</div>'
                + '<div class="mr-fc-note">'
                + (cur
                    ? '只练这一段（<b>' + cur.label + '</b>），不会自动打卡——打卡要『全部』那段刷完。'
                    : '这一天的 <b>' + all.length + ' 张</b>全在这一组：'
                      + secs.map(s => s.label + ' ' + s.cards.length).join(' · ')
                      + '。<b>整组刷完会自动打卡</b>，早间回顾就算做完了。')
                + (st.done ? '<br>✅ 这一天的卡今天已经刷完过一遍了，想再练点「↺ 从头来」。' : '')
                + (checked ? '<br>🔖 这一天已打卡。' : '')
                + '<br><span class="mr-dim">这些卡不占闪卡库的每日额度，也不进 FSRS 调度。</span>'
                + '</div>';
        }
        return '<div class="mr-card"><div class="mr-card-h">🎯 早间回顾闪卡'
            + '<span class="mr-tag">' + esc(S.date || '') + '</span></div>'
            + '<div class="mr-tabs">' + chips + '</div>' + inner + '</div>';
    }

    /** 开练：把当前这一段（或全部）翻成卡，交给闪卡练习区的本地通道。 */
    function startDayPractice(fresh) {
        const day = S.day;
        if (!day) return;
        const secs = daySections(day);
        const cur = S.fcSec ? secs.filter(s => s.key === S.fcSec)[0] : null;
        const picked = cur ? cur.cards : dayAllCards(day);
        if (!picked.length) { toast('这一天没有可翻的回顾内容'); return; }
        const api = globalThis.__flashFloat;
        if (!api || typeof api.local !== 'function') { toast('闪卡模块未就绪，稍后再点一次'); return; }
        const cards = picked.map(c => ({
            id: 'mrd-' + S.date + '-' + c.id,
            sec: cur ? cur.key : '',
            secLabel: cur ? cur.label : (SEC_ICON.rv + ' 今日回顾'),
            front: c.front, back: c.back,
        }));
        const st = (typeof globalThis.__mrFlashState === 'function')
            ? globalThis.__mrFlashState(S.date) : { left: 0, done: false };
        const resume = !cur && !fresh && st.left > 0;
        api.local(cards, '早间回顾 · ' + S.date, {
            kind: 'mr-day',
            date: S.date,
            resume: resume,
            // 只有「全部」那一组刷完才算今天回顾做完了 → 自动打卡
            onFinish: cur ? null : autoCheckin,
        });
        if (typeof globalThis.__webLog === 'function') {
            globalThis.__webLog({ kind: 'mr-flash', message: '早间回顾闪卡开练 ' + S.date
                + ' 段=' + (cur ? cur.key : '全部') + ' 张数=' + cards.length + (resume ? '（续刷）' : '') });
        }
    }

    /** 整组刷完 → 自动打卡（早间回顾的完成信号）。返回一句给总结屏显示的话。 */
    async function autoCheckin() {
        try {
            const r = await post('/api/morning-review/checkin', { date: S.date, on: true });
            if (!r.ok) return '⚠ 打卡失败：' + (r.error || '');
            if (typeof globalThis.__webLog === 'function') {
                globalThis.__webLog({ kind: 'checkin', message: '早间回顾闪卡刷完 → 自动打卡 ' + S.date });
            }
            await refreshOverview();
            renderAll();
            return '✅ 早间回顾已打卡（' + S.date + '）—— 这一天的回顾就算做完了。';
        } catch (e) {
            return '⚠ 打卡失败：无法连接本地服务。闪卡练完了，回到「早」页点「🔖 打卡今天」补一下。';
        }
    }


    // ---- 英语长难句 ----
    function engCard(day) {
        const e = day.english;
        if (!e) return '';
        const words = (e.words || []).map(w =>
            '<tr><td><strong>' + (w.word || '') + '</strong></td><td>' + (w.meaning || '')
            + '</td><td>' + (w.familiar || '') + '</td></tr>').join('');
        const gram = (e.grammar || []).map((g, i) =>
            '<div class="mr-gram"><b><span class="mr-num">' + (i + 1) + '</span>'
            + (g.title || '') + '</b><p>' + (g.body || '') + '</p></div>').join('');
        const dec = (e.decompose || []).length
            ? '<div class="mr-h4">成分分析</div><table class="mr-table"><thead><tr><th style="width:90px;">成分</th>'
              + '<th style="width:45%;">内容</th><th>说明</th></tr></thead><tbody>'
              + e.decompose.map(d => '<tr><td><span class="mr-badge">' + esc(d.badge || '')
                + '</span></td><td>' + (d.content || '') + '</td><td>' + (d.note || '') + '</td></tr>').join('')
              + '</tbody></table>' : '';
        return '<div class="mr-card"><div class="mr-card-h">📝 英语一长难句精析'
            + (e.source ? '<span class="mr-tag">' + esc(e.source) + '</span>' : '') + '</div>'
            + '<div class="mr-sent">' + rich(e.sentence) + '</div>'
            + '<div class="mr-acts"><button class="mr-btn" id="mr-trans-btn">🔍 查看参考译文</button></div>'
            + '<div class="mr-trans" id="mr-trans"><strong>参考译文：</strong>' + rich(e.translation) + '</div>'
            + (e.structure ? '<div class="mr-h4">🔍 结构拆解</div><div class="mr-struct">'
                + esc(e.structure) + '</div>' : '')
            + dec
            + (words ? '<div class="mr-h4">📖 熟词生义</div><table class="mr-table"><thead><tr>'
                + '<th>词汇</th><th>句中含义</th><th>常见熟义</th></tr></thead><tbody>'
                + words + '</tbody></table>' : '')
            + (gram ? '<div class="mr-h4">📐 核心语法点</div>' + gram : '')
            + linkHtml(e.noteLink, { query: e.sentence || "" }) + '</div>';
    }

    // ---- 数学要点（计算陷阱 / 错题 / 泰勒专题）----
    function mathCard(day) {
        const m = day.math;
        const pts = (m && m.points) ? m.points : [];
        if (!pts.length) return '';
        const html = pts.map(p => {
            const badges = (p.badges || []).map(b => '<span class="mr-badge">' + esc(b) + '</span>').join('');
            const concl = p.conclusion ? '<div class="mr-concl">' + rich(p.conclusion) + '</div>' : '';
            const tag = p.type === 'taylor' ? '<span class="mr-kind rev">泰勒·记忆</span>'
                       : p.type === 'trap' ? '<span class="mr-kind new">陷阱</span>'
                       : '<span class="mr-kind">' + esc(p.type || '要点') + '</span>';
            return '<div class="mr-item">'
                + '<div class="mr-badges">' + badges + tag + '</div>'
                + '<h4>' + rich(p.title) + '</h4>'
                + '<p>' + rich(p.body) + '</p>' + concl + linkHtml(p.noteLink, { anchor: p.title }) + '</div>';
        }).join('');
        return '<div class="mr-card"><div class="mr-card-h">🧮 数学要点'
            + '<span class="mr-time">约 ' + Math.max(pts.length * 3, 4) + ' 分钟</span>'
            + '<span class="mr-tag">陷阱 · 错题 · 泰勒</span></div>' + html + '</div>';
    }

    // ---- 今日议程：15 分钟时间预算 + 今日焦点（记忆优先，兼顾大盘观感）----
    function agendaHtml() {
        const d = S.day || {};
        const units = [];
        if ((d.review || []).length) units.push(['📚 知识点', Math.min(d.review.length * 2, 8)]);
        const mp = (d.math && d.math.points) || [];
        if (mp.length) units.push(['🧮 数学要点', Math.max(mp.length * 3, 4)]);
        // 闪卡那 3 分钟只在「这一天真的有可翻的回顾内容」时才算进预算
        const hasFc = dayAllCards(d).length;
        if (hasFc) units.push(['🎯 闪卡', 3]);
        if (d.english) units.push(['📝 长难句', 2]);
        const total = Math.min(units.reduce((a, u) => a + u[1], 0), 15);
        const seen = {}, chips = [];
        (d.review || []).concat(mp).forEach(it => (it.badges || []).forEach(b => {
            if (!seen[b] && chips.length < 5) { seen[b] = 1; chips.push(b); }
        }));
        const chip = chips.length
            ? '<span class="mr-focus-h">今日焦点</span>'
              + chips.map(c => '<span class="mr-focus">' + esc(c) + '</span>').join('') : '';
        const meter = units.map(u =>
            '<span class="mr-meter">' + u[0] + ' <b>' + u[1] + '′</b></span>').join('');
        return '<div class="mr-agenda"><div class="mr-agenda-l"><span class="mr-agenda-t">🌅 今日早间回顾</span>'
            + '<span class="mr-agenda-amt">约 <b>' + total + '</b> 分钟</span></div>'
            + '<div class="mr-agenda-m">' + meter + '</div>'
            + (chip ? '<div class="mr-agenda-r">' + chip + '</div>' : '') + '</div>';
    }

    function renderAll() {
        if (!S.ov) return;
        if (!S.day) {
            root.innerHTML = barHtml() + '<div class="mr-empty">该日期暂无复习内容。</div>';
            return;
        }
        const c1 = reviewCard(S.day);
        const c2 = mathCard(S.day);
        const c3 = fcCard();
        const c4 = engCard(S.day);
        let inner = '';
        if (c1) inner += '<div class="mr-span2">' + c1 + '</div>';
        const halves = [c2, c3].filter(Boolean);
        if (halves.length) {
            const cls = halves.length === 1 ? 'mr-span2' : 'mr-span1';
            inner += '<div class="' + cls + '">' + halves[0] + '</div>'
                + (halves[1] ? '<div class="mr-span1">' + halves[1] + '</div>' : '');
        }
        if (c4) inner += '<div class="mr-span2">' + c4 + '</div>';
        root.innerHTML = barHtml() + agendaHtml() + '<div class="mr-grid">' + inner + '</div>';
    }

    async function refreshOverview() {
        const ov = await get('/api/morning-review/overview');
        S.ov = ov;
        const cur = ov.days.find(d => d.date === S.date);
        if (S.day) S.day.checked = !!(cur && cur.checked);
    }

    async function selectDate(d) {
        if (S.busy || !d) return;
        S.busy = true; S.date = d;
        renderAll();
        try {
            const enc = encodeURIComponent(d);
            const dd = await get('/api/morning-review/day?date=' + enc);
            S.day = dd.day; S.day.checked = !!dd.checked;
            renderAll();
        } catch (e) {
            S.day = null;
            root.innerHTML = barHtml()
                + '<div class="mr-empty">⚠ 读取失败：' + esc(e.message) + '</div>';
        } finally {
            S.busy = false;
        }
    }

    async function toggleCheckin() {
        if (!S.date || S.busy) return;
        const cur = S.ov.days.find(d => d.date === S.date);
        const on = !(cur && cur.checked);
        try {
            const r = await post('/api/morning-review/checkin', { date: S.date, on });
            if (!r.ok) {
                if (typeof globalThis.__webLog === 'function') globalThis.__webLog({ kind: 'checkin-fail', message: '打卡失败: ' + S.date + ' on=' + on + ' ' + (r.error || '') });
                toast('打卡失败：' + (r.error || '')); return;
            }
            if (typeof globalThis.__webLog === 'function') globalThis.__webLog({ kind: 'checkin', message: '打卡 ' + S.date + ' on=' + on });
            await refreshOverview();
            renderAll();
            toast(on ? '✅ 已打卡（同步到电脑）' : '已取消打卡');
        } catch (e) {
            if (typeof globalThis.__webLog === 'function') globalThis.__webLog({ kind: 'checkin-err', message: '打卡异常: ' + String(e && e.message || e) });
            toast('打卡失败：无法连接本地服务');
        }
    }

    // 统一事件委托：渲染只换 innerHTML，不重复绑监听
    root.addEventListener('click', (ev) => {
        const chip = ev.target.closest('[data-date]');
        if (chip) { selectDate(chip.dataset.date); return; }
        // 闪卡面板的「段」切换（'' = 全部）——只改这一组怎么拼，不动别的
        const fcTab = ev.target.closest('[data-fc]');
        if (fcTab) { S.fcSec = fcTab.dataset.fc || ''; renderAll(); return; }
        if (ev.target.closest('#mr-check')) { toggleCheckin(); return; }
        if (ev.target.closest('#mr-fc-start')) { startDayPractice(false); return; }
        if (ev.target.closest('#mr-fc-restart')) { startDayPractice(true); return; }
        const note = ev.target.closest('[data-note]');
        if (note) {
            const file = note.dataset.note;
            if (!file) return;
            if (typeof globalThis.__webLog === 'function') globalThis.__webLog({ kind: 'open-note', message: '打开笔记 ' + file + (note.dataset.anchor ? ' #' + note.dataset.anchor : '') });
            if (typeof globalThis.__revOpenNote === 'function') {
                globalThis.__revOpenNote({
                    file: file, name: note.dataset.name || '',
                    anchor: note.dataset.anchor || '',
                    query: note.dataset.query || '',
                    keywords: note.dataset.words || '',
                });
            } else { toast('阅读器未就绪，稍后再点一次'); }
            return;
        }
        if (ev.target.closest('#mr-trans-btn')) {
            const box = document.getElementById('mr-trans');
            const btn = document.getElementById('mr-trans-btn');
            if (!box) return;
            const on = box.classList.toggle('on');
            if (btn) btn.textContent = on ? '🙈 收起参考译文' : '🔍 查看参考译文';
        }
    });

    async function init() {
        try {
            S.ov = await get('/api/morning-review/overview');
            const dates = S.ov.days.map(d => d.date);
            if (!dates.length) {
                root.innerHTML = '<div class="mr-empty">还没有早间回顾内容。'
                    + '让 agent 跑一次 morning-review 工作流即可生成。</div>';
                return;
            }
            S.date = dates.indexOf(S.ov.today) >= 0 ? S.ov.today : dates[dates.length - 1];
            await selectDate(S.date);
        } catch (e) {
            root.innerHTML = '<div class="mr-empty">⚠ 无法读取早间回顾：' + esc(e.message)
                + '<br>请确认本地服务已启动（桌面「考研大盘」快捷方式会自动启动）。</div>';
        }
    }

    (window.__pageRenderers = window.__pageRenderers || {});
    (window.__pageRenderers.review = window.__pageRenderers.review || []).push(init);
})();
'''


# ---------------------------------------------------------------------------
# 错题复盘 + 薄弱点学习（2026-09-20）
# ---------------------------------------------------------------------------

RV_CSS = '''
        /* ============================================================
           错题复盘 / 薄弱点学习：同样只用大盘色板，保证三页风格一致。
           ============================================================ */
        .rv-wrap { display: grid; grid-template-columns: 232px minmax(0, 1fr); gap: 14px; align-items: start; }
        @media (max-width: 860px) { .rv-wrap { grid-template-columns: 1fr; } }
        .rv-side { display: flex; flex-direction: column; gap: 8px; }
        .rv-hint { font-size: .72rem; color: var(--text-muted); line-height: 1.75; }
        .rv-sessions { max-height: 62vh; overflow-y: auto; display: flex; flex-direction: column; gap: 5px; }
        .rv-sess { font: inherit; font-size: .77rem; text-align: left; cursor: pointer;
            padding: 7px 9px; border: 1px solid var(--border-color); border-radius: var(--border-radius);
            background: var(--bg-secondary); color: var(--text-secondary); }
        .rv-sess:hover { color: var(--text-primary); border-color: var(--dianqing); }
        .rv-sess.on { color: var(--dianqing-lt); border-color: var(--dianqing);
            background: rgba(var(--dianqing-rgb), .13); }
        .rv-sess .rv-d { display: block; font-size: .68rem; color: var(--text-muted); margin-top: 2px; }
        .rv-sess .rv-e { display: inline-block; margin-top: 3px; font-size: .66rem;
            color: var(--zhusha-lt); background: rgba(var(--zhusha-rgb), .14);
            padding: 0 6px; border-radius: 8px; }

        .rv-card { background: var(--bg-card); border: 1px solid var(--border-color);
            border-radius: var(--border-radius); padding: 13px 15px; margin-bottom: 12px; }
        .rv-card-h { display: flex; align-items: center; gap: 8px; font-size: .9rem; font-weight: 600;
            color: var(--text-primary); padding-bottom: 8px; margin-bottom: 8px;
            border-bottom: 1px solid var(--border-color); flex-wrap: wrap; }
        .rv-tag { margin-left: auto; font-size: .69rem; font-weight: 400; color: var(--text-muted); }
        .rv-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
        .rv-input, .rv-select { font: inherit; font-size: .82rem; padding: 6px 10px;
            background: var(--bg-secondary); color: var(--text-primary);
            border: 1px solid var(--border-color); border-radius: var(--border-radius); }
        .rv-input:focus, .rv-select:focus { outline: none; border-color: var(--dianqing); }
        .rv-text { flex: 1; min-width: 180px; }
        .rv-btn { font: inherit; font-size: .78rem; padding: 5px 13px; cursor: pointer;
            border: 1px solid var(--border-color); border-radius: var(--border-radius);
            background: var(--bg-secondary); color: var(--text-secondary); }
        .rv-btn:hover { color: var(--text-primary); border-color: var(--zhuqing); }
        .rv-btn.primary { color: var(--zhuqing-lt); border-color: var(--zhuqing);
            background: rgba(var(--zhuqing-rgb), .14); }
        .rv-btn.warn { color: var(--xiang-lt); border-color: var(--xiang); }
        .rv-btn[disabled] { opacity: .45; cursor: default; }
        .rv-btn.on { color: var(--dianqing-lt); border-color: var(--dianqing);
            background: rgba(var(--dianqing-rgb), .14); }

        /* 对话流 */
        .rv-chat { max-height: 56vh; overflow-y: auto; display: flex; flex-direction: column; gap: 10px;
            padding-right: 4px; }
        .rv-msg { font-size: .84rem; line-height: 1.85; color: var(--text-secondary);
            padding: 9px 12px; border-radius: var(--border-radius); border: 1px solid var(--border-color); }
        .rv-msg.me { align-self: flex-end; max-width: 82%;
            background: rgba(var(--dianqing-rgb), .1); border-color: rgba(var(--dianqing-rgb), .3); }
        .rv-msg.ai { align-self: flex-start; max-width: 96%; background: var(--bg-secondary); }
        .rv-msg .rv-who { display: block; font-size: .67rem; color: var(--text-muted); margin-bottom: 4px; }
        .rv-msg strong { color: var(--text-primary); }
        .rv-msg code { padding: 1px 5px; background: var(--bg-primary); border-radius: 3px;
            font-family: Consolas, "Courier New", monospace; font-size: .93em; }
        .rv-msg ul, .rv-msg ol { margin: 4px 0 4px 18px; }
        .rv-msg pre { margin: 6px 0; padding: 8px 10px; background: var(--bg-primary);
            border-radius: 4px; overflow-x: auto; font-size: .78rem; }
        /* AI 手写的练习题：正文照常看，想练就一键进闪卡浮窗（2026-09-22） */
        .rv-flashbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
            margin-top: 10px; padding-top: 9px; border-top: 1px dashed var(--border-color); }
        .rv-flashbar .rv-hint { flex: 1; min-width: 0; }
        /* AI 自己调接口的留痕（2026-09-22）：一行小芯片，写清它刚才干了什么 */
        .rv-tools { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
        .rv-tools:empty { display: none; }
        .rv-tool { font-size: .68rem; color: var(--text-muted); padding: 1px 8px;
            border: 1px dashed var(--border-color); border-radius: 10px;
            background: var(--bg-primary); }
        /* 正在跑的那一步：呼吸的圆点 + 亮一点的字，一眼能看出「它还在动」 */
        .rv-tool.pending { color: var(--zhuqing-lt); border-style: solid;
            border-color: var(--zhuqing); }
        .rv-tool.pending::before { content: "◍ "; }
        /* 流式期间的状态行（正在思考 / 正在搜题库 / 已 7s） */
        .rv-live-state { display: flex; align-items: center; gap: 6px;
            font-size: .72rem; color: var(--zhuqing-lt); }
        .rv-live-state::before { content: ""; width: 6px; height: 6px; border-radius: 50%;
            background: currentColor; animation: rvLivePulse 1s ease-in-out infinite; }
        @keyframes rvLivePulse { 0%,100% { opacity: .25 } 50% { opacity: 1 } }
        .rv-live-text:empty { display: none; }
        .rv-live-text { margin-top: 6px; }
        .rv-thumbs { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
        .rv-thumb { position: relative; }
        .rv-thumb img { width: 74px; height: 74px; object-fit: cover; display: block;
            border: 1px solid var(--border-color); border-radius: 4px; cursor: zoom-in; }
        .rv-drop { margin-top: 9px; padding: 14px; text-align: center; font-size: .78rem;
            color: var(--text-muted); border: 1px dashed var(--border-color); border-radius: 6px; }
        .rv-drop.on { border-color: var(--dianqing); color: var(--dianqing-lt); }

        /* 错题清单 */
        .rv-err { display: flex; gap: 9px; align-items: flex-start; padding: 9px 0;
            border-bottom: 1px dashed var(--border-color); font-size: .82rem; }
        .rv-err:last-child { border-bottom: none; }
        .rv-err.done { opacity: .55; }
        .rv-err .rv-body { flex: 1; min-width: 0; }
        .rv-err .rv-t { color: var(--text-primary); font-weight: 600; }
        .rv-err .rv-c { font-size: .76rem; color: var(--text-secondary); margin-top: 3px; line-height: 1.7; }
        .rv-pill { display: inline-block; font-size: .66rem; padding: 0 7px; border-radius: 8px;
            margin-left: 5px; color: var(--xiang-lt); background: rgba(var(--xiang-rgb), .16); }
        .rv-pill.ok { color: var(--zhuqing-lt); background: rgba(var(--zhuqing-rgb), .16); }
        .rv-check { cursor: pointer; margin-top: 3px; accent-color: var(--zhuqing); }

        /* 检索命中（学习区） */
        .rv-hits { display: flex; flex-direction: column; gap: 6px; }
        .rv-hit { display: flex; gap: 8px; align-items: center; padding: 7px 10px; font-size: .79rem;
            border: 1px solid var(--border-color); border-radius: var(--border-radius);
            background: var(--bg-secondary); color: var(--text-secondary); }
        .rv-hit .rv-name { flex: 1; min-width: 0; color: var(--text-primary); }
        .rv-hit .rv-m { font-size: .69rem; color: var(--text-muted); white-space: nowrap; }
        .rv-empty { padding: 18px 4px; text-align: center; font-size: .8rem; color: var(--text-muted); }
        .rv-loading { padding: 18px 4px; text-align: center; font-size: .8rem; color: var(--text-muted); }

        /* 首页错因可视化 */
        .rv-pat-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: .76rem; }
        .rv-pat-name { width: 132px; flex: none; color: var(--text-secondary);
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .rv-pat-bar { flex: 1; height: 9px; background: var(--bg-secondary); border-radius: 5px; overflow: hidden; }
        .rv-pat-fill { height: 100%; background: linear-gradient(90deg, var(--xiang), var(--zhusha)); }
        .rv-pat-n { width: 30px; flex: none; text-align: right; color: var(--text-muted); font-size: .7rem; }
'''

RV_JS = '''
// ============================================================
// 错题复盘 + 薄弱点学习（2026-09-20）
//
// 两页共用一套底层：文件存储的复盘会话 + 错因画像 + 闪卡系统。
// 差别只在入口——复盘从「上传的卷子」进，学习区从「口头说的薄弱点」进。
//
// 专项练习刻意不重造：把考点前缀交给闪卡页的筛选（mode=browse + topic=），
// 于是手写板、简答题 AI 批改、FSRS 评分回写全部自动继承。
// ============================================================
(function () {
    const API = location.protocol.startsWith('http') ? location.origin : "http://localhost:8080";
    const SUBJECTS = ["408", "数学一", "政治", "英语一"];

    const esc = (s) => String(s == null ? "" : s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    // AI 回复是 Markdown+LaTeX，复用闪卡区已调好的 mdTex（含表格与 KaTeX）
    const md = (s) => (window.__mdTex ? window.__mdTex(s) : esc(s));

    function toast(msg) {
        const t = document.createElement("div");
        t.className = "fs-toast"; t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 2200);
    }
    async function get(path) {
        const r = await fetch(API + path);
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || ("HTTP " + r.status));
        return d;
    }
    async function post(path, payload) {
        const r = await fetch(API + path, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || ("HTTP " + r.status));
        return d;
    }

    // 上传前压一遍：手机拍的一整页原图好几 MB，模型不需要那么高像素，
    // 而且 dataURL 要进 JSON body，太大 serve.js 会直接拒。
    const MAX_SIDE = 1400, JPEG_Q = 0.82;
    function compressImage(file) {
        return new Promise((resolve, reject) => {
            if (!file || !/^image\\//.test(file.type)) return reject(new Error("只支持图片"));
            const fr = new FileReader();
            fr.onerror = () => reject(new Error("读取图片失败"));
            fr.onload = () => {
                const img = new Image();
                img.onerror = () => reject(new Error("图片解码失败"));
                img.onload = () => {
                    const scale = Math.min(1, MAX_SIDE / Math.max(img.width, img.height));
                    const cv = document.createElement("canvas");
                    cv.width = Math.max(1, Math.round(img.width * scale));
                    cv.height = Math.max(1, Math.round(img.height * scale));
                    const cx = cv.getContext("2d");
                    cx.fillStyle = "#fff"; cx.fillRect(0, 0, cv.width, cv.height);  // JPEG 无透明
                    cx.drawImage(img, 0, 0, cv.width, cv.height);
                    resolve({ dataUrl: cv.toDataURL("image/jpeg", JPEG_Q), w: cv.width, h: cv.height });
                };
                img.src = fr.result;
            };
            fr.readAsDataURL(file);
        });
    }

    // 把考点 ID 收成前缀（408-OS-03-01 → 408-OS），与闪卡选题口径一致
    const prefixOf = (tid) => String(tid || "").split("-").slice(0, 2).join("-");
    // 专项练习 = 切到闪卡页并**直接开练**。这里必须走 __flashStart 而不是
    // __flashApplyFilter：2026-09-21 起闪卡区停在「开始」闸门上等点击，
    // 只 applyFilter 的话用户从复盘页点「去专项练习」会被扔在闸门前，
    // 等于跳了个寂寞。startWithFilter 顺带起表，符合「开练」二字的预期。
    function goPractice(subject, topicPrefix) {
        const filter = { subject: subject || "", bucket: "", topic: topicPrefix || "" };
        const entry = window.__flashStart || window.__flashApplyFilter;
        if (entry) {
            try { entry(filter); } catch (e) { /* 闪卡区未就绪就只跳转 */ }
        }
        location.hash = "#/flash";
        toast(window.__flashStart ? "已按该考点组题开练，计时开始" : "已按该考点筛题，去闪卡页开练");
    }

    // ========================= 错题复盘 =========================
    const M = { subject: SUBJECTS[0], list: [], sid: null, detail: null,
                pending: [], pendingPdf: [], busy: false, draft: null };

    function mRoot() { return document.getElementById("rv-mistakes"); }

    async function mLoadList() {
        const d = await get("/api/review/overview");
        M.list = (d.sessions && d.sessions[M.subject]) || [];
        if (!M.sid && M.list.length) await mOpen(M.list[0].id);
        else mRender();
    }

    async function mOpen(sid) {
        M.sid = sid; M.draft = null;
        M.pending = []; M.pendingPdf = [];   // 切会话别把上一次的待发图带过去
        try {
            M.detail = await get("/api/review/session?subject=" + encodeURIComponent(M.subject) + "&sid=" + encodeURIComponent(sid));
        } catch (e) { toast("打开会话失败：" + e.message); M.detail = null; }
        mRender();
    }

    function mChatHtml() {
        const turns = (M.detail && M.detail.turns) || [];
        if (!turns.length) {
            return '<div class="rv-empty">还没有对话。上传一张错题照片（或整页卷子），'
                + '跟我说说你当时怎么想的，我们一题一题过。</div>';
        }
        return turns.map(t => '<div class="rv-msg ' + (t.role === "user" ? "me" : "ai") + '">'
            + '<span class="rv-who">' + (t.role === "user" ? "我" : "AI 教练") + '</span>'
            + md(t.content) + '</div>').join("");
    }

    function mErrorsHtml() {
        const errs = (M.detail && M.detail.errors) || [];
        if (M.draft) {
            return '<div class="rv-hint">下面是 AI 从对话里提炼的错题，勾掉不对的、改完再保存：</div>'
                + M.draft.map((e, i) => '<div class="rv-err">'
                    + '<input type="checkbox" class="rv-check" data-dk="' + i + '" checked>'
                    + '<div class="rv-body"><div class="rv-t">' + esc(e.title || e.topic_hint || "未命名") + '</div>'
                    + '<div class="rv-c">错因：<input class="rv-input" style="width:150px;font-size:.75rem;padding:2px 6px" '
                    + 'data-df="cause" data-dk="' + i + '" value="' + esc(e.cause || "") + '">'
                    + '｜严重度 <input class="rv-input" style="width:42px;font-size:.75rem;padding:2px 6px" type="number" min="1" max="5" '
                    + 'data-df="severity" data-dk="' + i + '" value="' + (Number(e.severity) || 3) + '">'
                    + '<div>' + esc(e.what_wrong || "") + '</div></div></div>').join("")
                + '<div class="rv-row" style="margin-top:10px">'
                + '<button class="rv-btn primary" id="rv-err-save">保存到错题本</button>'
                + '<button class="rv-btn" id="rv-err-cancel">取消</button></div>';
        }
        if (!errs.length) return '<div class="rv-empty">还没有登记错题。复盘到一段落后点「提炼错题」。</div>';
        return errs.map((e, i) => '<div class="rv-err' + (e.resolved ? " done" : "") + '">'
            + '<input type="checkbox" class="rv-check" data-rk="' + i + '"' + (e.resolved ? " checked" : "") + ' title="勾上=已闭环">'
            + '<div class="rv-body"><div class="rv-t">' + esc(e.title || e.topic_hint || "未命名")
            + '<span class="rv-pill' + (e.resolved ? " ok" : "") + '">'
            + (e.resolved ? "已闭环" : esc(e.cause || "未归类")) + '</span></div>'
            + '<div class="rv-c">' + esc(e.what_wrong || "")
            + (e.topic_hint ? '<br>考点：' + esc(e.topic_hint) : '') + '</div></div>'
            + (e.topic_id ? '<button class="rv-btn" data-drill="' + esc(prefixOf(e.topic_id) || "") + '">刷这个</button>' : '')
            + '</div>').join("");
    }

    function mRender() {
        const box = mRoot();
        if (!box) return;
        const list = M.list.map(s => '<button class="rv-sess' + (s.id === M.sid ? " on" : "") + '" data-sid="' + s.id + '">'
            + esc(s.title || s.id)
            + '<span class="rv-d">' + esc((s.date || "").slice(5)) + '</span>'
            + (s.error_count ? '<span class="rv-e">' + s.error_count + ' 错</span>' : '')
            + '</button>').join("") || '<div class="rv-hint">还没有会话</div>';
        const det = M.detail;
        const files = (det && det.meta && det.meta.files) || [];
        box.innerHTML =
            '<div class="rv-row" style="margin-bottom:12px">'
            + '<select class="rv-select" id="rv-subj">' + SUBJECTS.map(s =>
                '<option' + (s === M.subject ? ' selected' : '') + '>' + s + '</option>').join('') + '</select>'
            + '<button class="rv-btn primary" id="rv-new">＋ 新建复盘</button>'
            + '<span class="rv-hint" style="margin-left:auto">数据存在电脑 Review/ 下，平板只是发请求</span>'
            + '</div>'
            + '<div class="rv-wrap"><div class="rv-side"><div class="rv-sessions">' + list + '</div></div>'
            + '<div>'
            + (det
                ? '<div class="rv-card"><div class="rv-card-h">' + esc(det.meta.title)
                    + '<span class="rv-tag">' + esc(det.meta.date) + ' · ' + esc(det.meta.subject) + '</span></div>'
                    + (files.length ? '<div class="rv-thumbs">' + files.map(f =>
                        '<span class="rv-thumb"><img src="' + API + '/api/notes/asset?path='
                        + encodeURIComponent(f.path) + '" alt="上传图" data-zoom="' + esc(f.path)
                        + '" title="' + (f.kind === 'pdf-page' ? 'PDF 页（点击放大）' : '上传图（点击放大）') + '"></span>').join('')
                        + '</div>' : '')
                    // 已转换的 PDF 页可以反复引用：只传路径，服务端读盘，不必重传几 MB
                    + (files.filter(f => f.kind === 'pdf-page').length
                        ? '<div class="rv-row" style="margin-top:7px">'
                          + '<button class="rv-btn" id="rv-usepdf">📄 引用已转换的 '
                          + files.filter(f => f.kind === 'pdf-page').length + ' 页</button>'
                          + '<span class="rv-hint">再次提问时可复用，无需重传 PDF</span></div>' : '')
                    + '<div class="rv-chat" id="rv-chat">' + mChatHtml() + '</div>'
                    + '<div class="rv-drop" id="rv-drop">把错题图片/扫描的 PDF 卷子拖进来，或点「传图 / PDF」；也可直接 Ctrl+V 粘贴截图'
                    + '<br><span style="font-size:.7rem">PDF 会在本机自动转成逐页图片（PyMuPDF），原 PDF 一并留存</span>'
                    + '<input type="file" id="rv-file" accept="image/*,.pdf,application/pdf" multiple style="display:none">'
                    + '<div class="rv-row" style="margin-top:8px"><button class="rv-btn" id="rv-pick">📷 传图 / PDF</button>'
                    + '<span class="rv-hint" id="rv-pend"></span></div></div>'
                    + '<div class="rv-row" style="margin-top:9px">'
                    + '<input class="rv-input rv-text" id="rv-msg" placeholder="说说你当时怎么想的、卡在哪一步…">'
                    + '<button class="rv-btn primary" id="rv-send">发送</button></div>'
                    + '<div class="rv-row" style="margin-top:8px">'
                    + '<button class="rv-btn" id="rv-extract">🔍 提炼错题</button>'
                    + '<button class="rv-btn" id="rv-drill">🎯 按错因去专项练习</button></div>'
                    + '</div>'
                    + '<div class="rv-card"><div class="rv-card-h">📋 错题本'
                    + '<span class="rv-tag">勾选=已闭环（会停止提醒并降权）</span></div>'
                    + mErrorsHtml() + '</div>'
                : '<div class="rv-card"><div class="rv-empty">选一个会话，或点「＋ 新建复盘」开始。</div></div>')
            + '</div></div>';
        mBind();
        const c = document.getElementById("rv-chat");
        if (c) c.scrollTop = c.scrollHeight;
    }

    function mBind() {
        const subj = document.getElementById("rv-subj");
        if (subj) subj.onchange = () => { M.subject = subj.value; M.sid = null; M.detail = null; mLoadList(); };
        const nb = document.getElementById("rv-new");
        if (nb) nb.onclick = async () => {
            const title = (prompt("这次复盘的名称（如：9月模拟卷3 数学）") || "").trim();
            try {
                const d = await post("/api/review/session", { subject: M.subject, title });
                await mLoadList(); await mOpen(d.meta.id);
            } catch (e) { toast("新建失败：" + e.message); }
        };
        document.querySelectorAll("#rv-mistakes [data-sid]").forEach(b =>
            b.onclick = () => mOpen(b.dataset.sid));
        const pick = document.getElementById("rv-pick"), file = document.getElementById("rv-file");
        if (pick && file) { pick.onclick = () => file.click(); file.onchange = async (ev) => { await addFiles(ev.target.files); ev.target.value = ""; }; }
        const drop = document.getElementById("rv-drop");
        if (drop) {
            drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("on"); });
            drop.addEventListener("dragleave", () => drop.classList.remove("on"));
            drop.addEventListener("drop", async (e) => {
                e.preventDefault(); drop.classList.remove("on");
                await addFiles(e.dataTransfer && e.dataTransfer.files);
            });
        }
        const msg = document.getElementById("rv-msg");
        if (msg) msg.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !(e.isComposing || e.keyCode === 229)) { e.preventDefault(); mSend(); } });
        const sb = document.getElementById("rv-send"); if (sb) sb.onclick = mSend;
        const eb = document.getElementById("rv-extract");
        if (eb) eb.onclick = async () => {
            if (M.busy) return;
            eb.disabled = true; eb.textContent = "提炼中…";
            try {
                const d = await post("/api/review/extract", { subject: M.subject, sid: M.sid });
                M.draft = d.errors || [];
                if (!M.draft.length) toast("这轮对话里没提炼出错题");
                mRender();
            } catch (e) { toast("提炼失败：" + e.message); eb.disabled = false; eb.textContent = "🔍 提炼错题"; }
        };
        const db = document.getElementById("rv-drill");
        if (db) db.onclick = () => {
            const errs = (M.detail && M.detail.errors) || [];
            const t = errs.find(x => x.topic_id);
            const open = errs.filter(x => !x.resolved);
            if (!open.length) { toast("错题本还是空的，先提炼错题"); return; }
            goPractice(M.subject, t ? prefixOf(t.topic_id) : "");
        };
        const cancel = document.getElementById("rv-err-cancel");
        if (cancel) cancel.onclick = () => { M.draft = null; mRender(); };
        const save = document.getElementById("rv-err-save");
        if (save) save.onclick = async () => {
            const kept = [];
            document.querySelectorAll("#rv-mistakes [data-dk]").forEach(cb => {
                if (!cb.checked) return;
                const i = Number(cb.dataset.dk);
                const base = M.draft[i] || {};
                const causeEl = document.querySelector('#rv-mistakes [data-df="cause"][data-dk="' + i + '"]');
                const sevEl = document.querySelector('#rv-mistakes [data-df="severity"][data-dk="' + i + '"]');
                kept.push({
                    topic_hint: base.topic_hint || "", title: base.title || "",
                    what_wrong: base.what_wrong || "", evidence: base.evidence || "",
                    cause: causeEl ? causeEl.value : (base.cause || ""),
                    cause_kind: base.cause_kind || "concept",
                    severity: sevEl ? sevEl.value : (base.severity || 3),
                    resolved: false,
                });
            });
            try {
                const merged = ((M.detail && M.detail.errors) || []).concat(kept);
                await post("/api/review/errors", { subject: M.subject, sid: M.sid, errors: merged });
                M.draft = null;
                await mRefresh();
                toast("已存进错题本");
            } catch (e) { toast("保存失败：" + e.message); }
        };
        document.querySelectorAll("#rv-mistakes [data-rk]").forEach(cb => cb.onchange = async () => {
            const i = Number(cb.dataset.rk);
            const errs = ((M.detail && M.detail.errors) || []).map((e, j) =>
                j === i ? Object.assign({}, e, { resolved: cb.checked }) : e);
            try {
                await post("/api/review/errors", { subject: M.subject, sid: M.sid, errors: errs });
                await mRefresh();
            } catch (e) { toast("更新失败：" + e.message); }
        });
        document.querySelectorAll("#rv-mistakes [data-drill]").forEach(b =>
            b.onclick = () => goPractice(M.subject, b.dataset.drill));
        const usePdf = document.getElementById("rv-usepdf");
        if (usePdf) usePdf.onclick = () => {
            const pages = (((M.detail || {}).meta || {}).files || [])
                .filter(f => f.kind === "pdf-page").map(f => f.path);
            M.pendingPdf = pages;
            pendSummary();
            toast("已引用 " + pages.length + " 页，发送时一并交给 AI");
        };
        document.querySelectorAll("#rv-mistakes [data-zoom]").forEach(img =>
            img.onclick = () => window.open(API + "/api/notes/asset?path=" + encodeURIComponent(img.dataset.zoom), "_blank"));
    }

    function readAsDataUrl(file) {
        return new Promise((resolve, reject) => {
            const fr = new FileReader();
            fr.onerror = () => reject(new Error("读取文件失败"));
            fr.onload = () => resolve(fr.result);
            fr.readAsDataURL(file);
        });
    }

    function pendSummary() {
        const p = document.getElementById("rv-pend");
        if (!p) return;
        const bits = [];
        if (M.pending.length) bits.push(M.pending.length + " 张图");
        if (M.pendingPdf.length) bits.push(M.pendingPdf.length + " 页 PDF");
        p.textContent = bits.length ? "待发：" + bits.join(" + ") : "";
    }

    async function addFiles(list) {
        const arr = Array.from(list || []);
        const imgs = arr.filter(f => /^image\\//.test(f.type));
        const pdfs = arr.filter(f => f.type === "application/pdf" || /\\.pdf$/i.test(f.name || ""));
        for (const f of imgs) {
            try {
                const { dataUrl } = await compressImage(f);
                M.pending.push(dataUrl);
            } catch (e) { toast("图片处理失败：" + e.message); }
        }
        for (const f of pdfs) await addPdf(f);
        pendSummary();
    }

    // PDF 交给服务端转页图（本机 PyMuPDF）：浏览器端做这件事要么装大依赖、
    // 要么渲染质量不可控，而模型最终吃的是图，转换放服务端还方便复用与排错。
    async function addPdf(file) {
        const btn = document.getElementById("rv-pick");
        if (btn) { btn.disabled = true; btn.textContent = "PDF 转换中…"; }
        try {
            const dataUrl = await readAsDataUrl(file);
            const d = await post("/api/review/upload-pdf", {
                subject: M.subject, sid: M.sid, pdf: dataUrl, filename: file.name || "scan.pdf",
            });
            M.pendingPdf = M.pendingPdf.concat((d.pages || []).map(x => x.path));
            toast(d.converted + " 页已转换"
                + (d.skipped ? "（PDF 共 " + d.total_pages + " 页，本次取前 " + d.converted + " 页）" : ""));
            await mRefresh();   // 页图已登记进会话，刷新即可看到缩略图
        } catch (e) {
            toast("PDF 失败：" + e.message);
        } finally {
            const b2 = document.getElementById("rv-pick");
            if (b2) { b2.disabled = false; b2.textContent = "📷 传图 / PDF"; }
        }
    }

    async function mRefresh() {
        try {
            M.detail = await get("/api/review/session?subject=" + encodeURIComponent(M.subject) + "&sid=" + encodeURIComponent(M.sid));
        } catch (e) { /* 保留旧内容 */ }
        const d = await get("/api/review/overview");
        M.list = (d.sessions && d.sessions[M.subject]) || [];
        mRender();
    }

    async function mSend() {
        const box = document.getElementById("rv-msg");
        if (!box || M.busy) return;
        const text = box.value.trim();
        if (!text && !M.pending.length && !M.pendingPdf.length) return;
        M.busy = true;
        const btn = document.getElementById("rv-send");
        if (btn) { btn.disabled = true; btn.textContent = "思考中…"; }
        const imgs = M.pending.slice();
        const pdfPages = M.pendingPdf.slice();
        box.value = "";
        // 先把图落到会话目录：这样复盘记录里留着原件，之后回看或重跑提炼都还能取到
        for (const u of imgs) {
            try { await post("/api/review/upload", { subject: M.subject, sid: M.sid, image: u }); }
            catch (e) { toast("上传失败：" + e.message); }
        }
        M.pending = [];
        M.pendingPdf = [];
        pendSummary();
        try {
            // PDF 页图已在服务端落盘，这里只传路径引用，避免重复上传几 MB
            await post("/api/review/chat", {
                subject: M.subject, sid: M.sid,
                message: (text || "（看图）") + (pdfPages.length ? "（引用 " + pdfPages.length + " 页）" : ""),
                images: [], page_paths: pdfPages,
            });
            await mRefresh();
        } catch (e) {
            toast("发送失败：" + e.message);
            mRender();
        } finally {
            M.busy = false;
            const b2 = document.getElementById("rv-send");
            if (b2) { b2.disabled = false; b2.textContent = "发送"; }
        }
    }

    // 粘贴截图直接进待发区（只在复盘页可见时接管，避免抢走闪卡页的粘贴）
    document.addEventListener("paste", (ev) => {
        const page = document.querySelector('.page[data-page="mistakes"]');
        if (!page || page.hidden || !M.sid) return;
        const items = (ev.clipboardData && ev.clipboardData.items) || [];
        const imgs = items.filter(it => it.kind === "file" && /^image\\//.test(it.type)).map(it => it.getAsFile());
        if (imgs.length) { ev.preventDefault(); addFiles(imgs); }
    });

    // ========================= 薄弱点学习 =========================
    const ST = { subject: "all", hits: null, history: [], busy: false };

    function sRoot() { return document.getElementById("rv-study"); }

    function sRender() {
        const box = sRoot();
        if (!box) return;
        const h = ST.hits;
        box.innerHTML =
            '<div class="rv-card"><div class="rv-card-h">🎯 说出你觉得薄弱的知识点'
            + '<span class="rv-tag">我会翻题库、笔记和你的历史错因</span></div>'
            + '<div class="rv-row">'
            + '<select class="rv-select" id="st-subj">'
            + '<option value="all">全部科目</option>'
            + SUBJECTS.map(s => '<option' + (s === ST.subject ? ' selected' : '') + '>' + s + '</option>').join('')
            + '</select>'
            + '<input class="rv-input rv-text" id="st-q" placeholder="例如：Cache 写回与写allocate、Karnaugh 卡圈组、中值定理…" value="' + esc(ST.q || '') + '">'
            + '<button class="rv-btn primary" id="st-go">开始</button></div>'
            + '<div class="rv-hint" style="margin-top:8px">不传卷子也能练：说个知识点，我结合现有数据给你讲清 + 当场出小题。</div>'
            + '</div>'
            + (h ? '<div class="rv-card"><div class="rv-card-h">🔎 检索命中'
                + '<span class="rv-tag">只给索引，正文点开才读，不塞满上下文</span></div>'
                + '<div class="rv-hits">'
                + ((h.topics || []).length ? (h.topics || []).map(t =>
                    '<div class="rv-hit"><span class="rv-name">' + esc(t.name) + '</span>'
                    + '<span class="rv-m">' + esc(t.id) + '｜权重' + (t.exam_weight == null ? '?' : t.exam_weight)
                    + '｜卡' + t.cards + (t.acc != null ? '｜正确率' + t.acc + '%(' + t.answered + '次)' : '｜没练过') + '</span>'
                    + '<button class="rv-btn" data-st-drill="' + esc(t.prefix || '') + '">刷这考点</button></div>').join('')
                    : '<div class="rv-hint">题库里没有直接命中的考点——可以先按下面笔记的标题说细一点。</div>')
                + ((h.causes || []).length ? '<div class="rv-hint" style="margin-top:8px">你的历史错因命中：'
                    + esc((h.causes || []).join('；')) + '</div>' : '')
                + '</div></div>' : '')
            // 「闪卡浮窗」入口：不跳页就能刷，练习区实体是从闪卡页搬过来的那一块
            // （全屏、音效、键盘、评分、进度同步都在，见 FLASH_JS 的浮窗段）。
            + '<div class="rv-card"><div class="rv-card-h">💬 对话'
            + '<button class="rv-btn" id="st-float" style="margin-left:auto" '
            + 'title="把闪卡练习区以浮窗打开：全屏 / 音效 / 快捷键 / 评分都一样">🧠 闪卡浮窗</button></div>'
            + '<div class="rv-chat" id="st-chat">'
            + (ST.history.length ? ST.history.map((t, ti) =>
                '<div class="rv-msg ' + (t.role === "user" ? "me" : "ai") + (t.streaming ? " streaming" : "") + '">'
                + '<span class="rv-who">' + (t.role === "user" ? "我" : "AI") + '</span>'
                // 流式中：正文还没定稿，先给一块「活区」，由 sSend 往里写状态/工具/正文
                // （见 sSend 的 SSE 段）。结束后照旧走 md(t.content) 那条静态路。
                + (t.streaming
                    ? '<div class="rv-live">'
                      + '<div class="rv-live-state" data-live-state>正在思考…</div>'
                      + '<div class="rv-tools" data-live-tools></div>'
                      + '<div class="rv-live-text" data-live-text></div>'
                      + '</div>'
                    : md(t.content)
                      // AI 这一轮自己调了什么接口（搜题库/搜笔记/归卡/开练），一行芯片写清楚，
                      // 免得它做了事人却不知道。见 sSend 的 viaAgent 与 sRunActions。
                      + ((Array.isArray(t.trace) && t.trace.length)
                          ? '<div class="rv-tools">' + t.trace.map(x =>
                              '<span class="rv-tool">🔧 ' + esc(x.summary || x.tool) + '</span>').join('') + '</div>'
                          : '')
                      // AI 手写的题：正文照常读，想练就一键归卡 + 进浮窗（见 sPractice）
                      + ((Array.isArray(t.cards) && t.cards.length)
                          ? '<div class="rv-flashbar">'
                            + '<button class="rv-btn primary" data-st-flash="' + ti + '">🧠 用闪卡练这 '
                            + t.cards.length + ' 张</button>'
                            + '<span class="rv-hint">先归到题库，再按卡号精确组题——同题干复用已有卡，'
                            + '评分/进度与闪卡页同一套</span></div>'
                          : ''))
                + '</div>').join("")
                : '<div class="rv-empty">说个知识点开始。</div>')
            + '</div>'
            + ((ST.hits && (ST.hits.notes || []).length)
                ? '<div class="rv-hint" style="margin:8px 0 0">相关笔记：'
                  + ST.hits.notes.map(n => n.path
                      ? '<a href="' + API + '/api/notes/preview?path=' + encodeURIComponent(n.path)
                        + '" target="_blank" style="color:var(--dianqing-lt);margin-right:10px;">'
                        + esc((n.subject ? n.subject + '·' : '') + n.title) + '</a>'
                      // 不少条目是「已整理到第X章」的问答，没存直链：只给标题，别渲染成点不开的空链接
                      : '<span style="color:var(--text-muted);margin-right:10px;">'
                        + esc((n.subject ? n.subject + '·' : '') + n.title) + '（见对应章节）</span>'
                    ).join('') + '</div>' : '')
            + '<div class="rv-row" style="margin-top:10px">'
            + '<input class="rv-input rv-text" id="st-msg" placeholder="追问、要小题、让它先讲概念…（Enter 发送）">'
            + '<button class="rv-btn primary" id="st-send">发送</button></div>'
            + '</div>';
        sBind();
        const c = document.getElementById("st-chat");
        if (c) c.scrollTop = c.scrollHeight;
    }

    function sBind() {
        const sj = document.getElementById("st-subj");
        if (sj) sj.onchange = () => { ST.subject = sj.value; sSearch(); };
        const q = document.getElementById("st-q");
        const go = document.getElementById("st-go");
        if (go) go.onclick = () => { ST.q = (q && q.value || "").trim(); sSearch(); };
        if (q) q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !(e.isComposing || e.keyCode === 229)) { ST.q = q.value.trim(); sSearch(); } });
        const m = document.getElementById("st-msg");
        if (m) m.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !(e.isComposing || e.keyCode === 229)) { e.preventDefault(); sSend(); } });
        const sb = document.getElementById("st-send"); if (sb) sb.onclick = sSend;
        document.querySelectorAll("#rv-study [data-st-drill]").forEach(b =>
            b.onclick = () => goPractice(ST.subject === "all" ? "" : ST.subject, b.dataset.stDrill));
        // 闪卡浮窗：只碰 FLASH_JS 暴露的那一个接口，不自己造一套练习 UI
        const fw = document.getElementById("st-float");
        if (fw) fw.onclick = () => {
            const api = window.__flashFloat;
            if (!api) { toast("闪卡模块还没就绪，刷新一下再试"); return; }
            api.open("🧠 闪卡浮窗");
            toast("闪卡浮窗已打开：全屏 / 音效 / 快捷键都在");
        };
        document.querySelectorAll("#rv-study [data-st-flash]").forEach(b =>
            b.onclick = () => sPractice(ST.history[Number(b.dataset.stFlash)]));
    }

    // ============================================================
    // 「练这几张」：把 AI 手写的题真正练起来（2026-09-22）
    // 两步——① 归卡：POST /api/study/cards 把题写进 questions+cards，拿回 card_id；
    //          ② 开练：把 card_id 交给闪卡模块，在浮窗里按卡号精确组题。
    // 为什么非归卡不可：闪卡的评分、撤销、进度存档、多端续刷全靠 card_id，
    // 临时卡练完就散、还写不进 FSRS，等于练了个寂寞。
    // 归卡失败就如实说（选项不全、模型没给 cards 块），别装作练起来了。
    // ============================================================
    async function sPractice(turn) {
        const cards = (turn && Array.isArray(turn.cards)) ? turn.cards : [];
        if (!cards.length) { toast("这一轮没有可练的题"); return; }
        const api = window.__flashFloat;
        if (!api) { toast("闪卡模块还没就绪，刷新一下再试"); return; }
        try {
            const r = await post("/api/study/cards", {
                cards: cards,
                subject: ST.subject === "all" ? "" : ST.subject,
            });
            const ids = r.card_ids || [];
            if (!ids.length) throw new Error("这几张都没能入库（题干或选项不完整）");
            api.practice(ids, "🧠 刚出的 " + ids.length + " 张");
            const bits = [];
            if (r.inserted) bits.push("新增 " + r.inserted + " 张");
            if (r.reused) bits.push("复用已有 " + r.reused + " 张");
            toast("已归卡（" + (bits.join(" · ") || ids.length + " 张") + "），浮窗开练");
        } catch (e) {
            toast("开练失败：" + e.message);
        }
    }

    // AI 自己调接口时留下的「动作」。目前只有一种：让它把闪卡浮窗弹出来开练。
    // ⚠️ 服务端不在浏览器里，弹窗这一下只能前端做 —— 所以服务端只登记意图
    // （actions 里一条 practice），真正调 __flashFloat 的是这里。
    function sRunActions(actions) {
        const list = Array.isArray(actions) ? actions : [];
        const api = window.__flashFloat;
        list.forEach(function (a) {
            // AI 复核掉一条「这题有问题」的标记：留个痕，说明它真的动了库
            // （改题发生在服务端，界面上那张卡不会自己变，所以必须说出来）。
            if (a && a.type === "card_fixed") {
                const what = a.status === "fixed"
                    ? ("已改好" + ((a.changes || []).length ? "（" + a.changes.join("/") + "）" : ""))
                    : (a.status === "dismissed" ? "已驳回（不是问题）" : "已删卡");
                toast("AI 复核了标记 #" + a.report_id + "：" + what);
                return;
            }
            if (!a || a.type !== "practice") return;   // card_filed 只是留痕，不用动界面
            const ids = Array.isArray(a.card_ids) ? a.card_ids : [];
            if (!ids.length) return;
            if (!api) { toast("闪卡模块还没就绪，刷新一下再试"); return; }
            api.practice(ids, a.title || ("🧠 刚出的 " + ids.length + " 张"));
            toast("AI 自己调了闪卡：已开练 " + ids.length + " 张");
        });
    }

    async function sSearch() {
        if (!ST.q) { ST.hits = null; sRender(); return; }
        try {
            ST.hits = await get("/api/study/context?q=" + encodeURIComponent(ST.q) + "&subject=" + encodeURIComponent(ST.subject));
            ST.history = [];
            sRender();
            const box = document.getElementById("st-q");
            if (box) box.value = ST.q;
        } catch (e) { toast("检索失败：" + e.message); }
    }

    // ============================================================
    // 流式：让「AI 在干什么」看得见（2026-09-22 用户反馈「无法感知工作状态」）
    //
    // 工具回路一轮十几秒，非流式只给一个「思考中…」，人没法判断它是在查题库、在写卡
    // 还是卡住了。这里走 SSE：服务端把「正在推演（已写多少字）→ 正在搜什么 → 搜到了
    // 什么 → 正文逐字」实时推过来，画在气泡里，旁边还有「已 Ns」在走。
    //
    // ⚠️ 流式期间**不整页重绘**（sRender）：每个字重绘整段对话会闪，还会把滚动条和
    //    输入焦点一起弄丢。只更新那一条气泡里的活区，结束后再整页重绘收尾。
    // ⚠️ 三级退路：SSE → 非流式 agent → 老 chat。任一环挂了下一级顶上，不留白屏。
    // ============================================================
    function stLive() {
        const b = document.querySelector("#st-chat .rv-msg.ai:last-child");
        if (!b) return null;
        return { box: b,
                 state: b.querySelector("[data-live-state]"),
                 tools: b.querySelector("[data-live-tools]"),
                 text: b.querySelector("[data-live-text]") };
    }
    function stScroll() {
        const c = document.getElementById("st-chat");
        if (!c) return;
        // 只有本来就贴底才跟着走：人往上翻在看历史时别把他拽回来
        if (c.scrollHeight - c.scrollTop - c.clientHeight < 90) c.scrollTop = c.scrollHeight;
    }

    async function sSend() {
        const box = document.getElementById("st-msg");
        if (!box || ST.busy) return;
        const text = box.value.trim();
        if (!text) return;
        ST.busy = true;
        const btn = document.getElementById("st-send");
        if (btn) { btn.disabled = true; btn.textContent = "思考中…"; }
        ST.history.push({ role: "user", content: text });
        const payload = { message: text, subject: ST.subject, history: ST.history.slice(0, -1) };
        // 先挂一条「正在干活」的空回复：流式内容会往它的活区里长
        const turn = { role: "assistant", content: "", cards: [], trace: [], streaming: true };
        ST.history.push(turn);
        box.value = "";
        sRender();

        const live = stLive();
        const t0 = Date.now();
        let lastState = "正在思考", acc = "", paintTimer = null, started = false;
        let doneEv = null, errMsg = "", actions = [];
        const setState = (s) => {
            lastState = s;
            if (live && live.state) {
                live.state.textContent = s + "（已 " + Math.round((Date.now() - t0) / 1000) + "s）";
            }
        };
        // 秒表：证明它还活着（模型推演时可能好几秒一个字都不吐）
        const tick = setInterval(() => {
            if (!live || !live.state) return;
            live.state.textContent = lastState + "（已 " + Math.round((Date.now() - t0) / 1000) + "s）";
        }, 1000);
        const paintText = () => {
            if (paintTimer) return;      // 合并到 60ms 一次：逐字重排会很卡
            paintTimer = setTimeout(() => {
                paintTimer = null;
                if (!live || !live.text) return;
                try { live.text.innerHTML = md(acc); }
                catch (e) { live.text.textContent = acc; }   // 半截公式可能渲染不出来，退回纯文本
                stScroll();
            }, 60);
        };
        const paintTools = () => {
            if (!live || !live.tools) return;
            live.tools.innerHTML = turn.trace.map(x => '<span class="rv-tool' + (x.pending ? " pending" : "") + '">🔧 '
                + esc(x.pending ? x.summary : (x.summary || x.tool)) + '</span>').join("");
            stScroll();
        };
        const handle = (ev) => {
            if (!ev || !ev.type) return;
            if (ev.type === "start") { setState("正在思考"); return; }
            if (ev.type === "round") { setState(ev.n > 1 ? ("第 " + ev.n + " 轮") : "正在思考"); return; }
            if (ev.type === "thinking") { setState("正在推演（已写 " + ev.chars + " 字）"); return; }
            if (ev.type === "delta") {
                acc += ev.text || "";
                if (!started) { started = true; setState("正文"); }
                paintText();
                return;
            }
            if (ev.type === "tool_start") {
                turn.trace.push({ tool: ev.tool, summary: ev.label || "正在调用工具…", pending: true });
                setState(ev.label || "正在调用工具");
                paintTools();
                return;
            }
            if (ev.type === "tool") {
                for (let i = turn.trace.length - 1; i >= 0; i--) {
                    if (turn.trace[i].pending && turn.trace[i].tool === ev.tool) {
                        turn.trace[i].summary = ev.summary || ev.tool;
                        turn.trace[i].pending = false;
                        break;
                    }
                }
                paintTools();
                setState(ev.summary || "继续");
                return;
            }
            if (ev.type === "done") { doneEv = ev; return; }
            if (ev.type === "error") { errMsg = ev.error || "出错了"; return; }
        };

        try {
            const resp = await fetch(API + "/api/study/agent/stream", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            if (!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);
            const reader = resp.body.getReader();
            const dec = new TextDecoder();
            let buf = "";
            for (;;) {
                const step = await reader.read();
                if (step.done) break;
                buf += dec.decode(step.value, { stream: true });
                let i;
                // ⚠️ 下面那几个转义必须**双写反斜杠**：RV_JS 是 Python 普通字符串，
                //    写单个转义会被 Python 先解释成真换行/真回车，JS 字符串当场断开
                //    （整段 RV_JS 报 SyntaxError，页面上那一块什么都点不动）。
                //    连注释里也不能出现单反斜杠 —— 这个坑前后踩了三次，
                //    所以立了 tools/test_js_syntax.js 当闸门。
                while ((i = buf.indexOf("\\n\\n")) >= 0) {
                    const chunk = buf.slice(0, i);
                    buf = buf.slice(i + 2);
                    chunk.split("\\n").forEach(line => {
                        line = line.replace(/\\r$/, "");
                        if (line.indexOf("data:") !== 0) return;
                        let ev = null;
                        try { ev = JSON.parse(line.slice(5).trim()); } catch (e) { return; }
                        if (ev && ev.type) { started = true; handle(ev); }
                    });
                }
            }
        } catch (e) {
            if (!errMsg) errMsg = e.message;
        } finally {
            clearInterval(tick);
            if (paintTimer) { clearTimeout(paintTimer); paintTimer = null; }
        }

        const wasStreaming = started;   // 连上过没有（决定要不要走退路）
        const hadText = acc.length > 0;
        if (doneEv) {
            turn.content = String(doneEv.text || acc || "");
            turn.cards = Array.isArray(doneEv.cards) ? doneEv.cards : [];
            if (Array.isArray(doneEv.trace) && doneEv.trace.length) turn.trace = doneEv.trace;
            actions = doneEv.actions || [];
            if (doneEv.topics && doneEv.topics.length) {
                ST.hits = Object.assign({}, ST.hits || {}, { topics: doneEv.topics,
                    notes: doneEv.notes || (ST.hits && ST.hits.notes) || [] });
            }
        } else if (!wasStreaming || !hadText) {
            // 一个字都没出来就断了 → 这条路走不通（老服务端没重启 / 模型不支持流式），
            // 老实退回非流式：agent → 老 chat，两级都试，功能降级但不报错。
            try {
                const d = await post("/api/study/agent", payload);
                turn.content = d.text;
                turn.cards = Array.isArray(d.cards) ? d.cards : [];
                turn.trace = Array.isArray(d.trace) ? d.trace : [];
                actions = d.actions || [];
                if (d.topics && d.topics.length) {
                    ST.hits = Object.assign({}, ST.hits || {}, { topics: d.topics,
                        notes: d.notes || (ST.hits && ST.hits.notes) || [] });
                }
            } catch (e1) {
                try {
                    const d2 = await post("/api/study/chat", payload);
                    turn.content = d2.text;
                    turn.cards = Array.isArray(d2.cards) ? d2.cards : [];
                } catch (e2) {
                    turn.content = "⚠ " + e2.message;
                }
            }
        } else {
            // 吐了一半才断：把已有的留下，说清是断的，不要重跑一遍（免得答案来两份）
            turn.content = acc + "\\n\\n⚠ 回答中断：" + (errMsg || "连接被断开");
        }
        turn.streaming = false;
        ST.busy = false;
        if (btn) { btn.disabled = false; btn.textContent = "发送"; }
        sRender();
        sRunActions(actions);
    }

    // ========================= 首页：错因可视化 =========================
    // 只读 _patterns（L1），不碰单会话文件；容器在总览页，图靠这里画。
    async function drawPatterns() {
        const box = document.getElementById("pattern-box");
        if (!box) return;
        try {
            const d = await get("/api/review/overview");
            const p = d.patterns;
            if (!p || !p.subjects) {
                box.innerHTML = '<div class="rv-empty">还没有错因数据。去「复盘」页传一张错题照片开始。</div>';
                return;
            }
            const rows = [];
            for (const s of SUBJECTS) {
                const blk = p.subjects[s] || {};
                for (const c of (blk.top_causes || []).slice(0, 3)) rows.push({ subject: s, ...c });
            }
            rows.sort((a, b) => b.score - a.score);
            const top = rows.slice(0, 8);
            if (!top.length) {
                box.innerHTML = '<div class="rv-empty">错题本还是空的——复盘过的题会在这里变成提醒。</div>';
                return;
            }
            const max = Math.max.apply(null, top.map(r => r.score || 0)) || 1;
            box.innerHTML = top.map(r =>
                '<div class="rv-pat-row" title="' + esc(r.cause) + '｜' + r.count + ' 次'
                + (r.has_cards ? '｜已出卡' : '｜尚无卡') + '">'
                + '<span class="rv-pat-name">' + esc(r.subject) + '·' + esc(r.cause) + '</span>'
                + '<span class="rv-pat-bar"><span class="rv-pat-fill" style="width:'
                + Math.max(6, Math.round(r.score / max * 100)) + '%"></span></span>'
                + '<span class="rv-pat-n">' + r.count + '</span></div>').join("")
                + '<div class="rv-hint" style="margin-top:6px">条长=加权分（次数×时间衰减×是否闭环），'
                + '已闭环的会自己降权沉底。'
                + ((d.patterns && d.patterns.card_suggestions || []).length
                    ? ' 另有 ' + d.patterns.card_suggestions.length + ' 条待出卡。' : '')
                + '</div>';
        } catch (e) {
            box.innerHTML = '<div class="rv-empty">读不到错因画像：' + esc(e.message) + '</div>';
        }
    }

    // ========================= 注册懒渲染 =========================
    (window.__pageRenderers = window.__pageRenderers || {});
    function reg(name, fn) {
        (window.__pageRenderers[name] = window.__pageRenderers[name] || []).push(fn);
    }
    reg("mistakes", function () { mRender(); mLoadList(); });
    reg("study", function () { sRender(); });
    reg("overview", function () { drawPatterns(); });
})();
'''



# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Loading data sources...")
    # 1. Load all data
    index = load_index()
    print(f"  Notes index: {len(index.get('entries', []))} entries")

    graphs = load_graphs()
    print(f"  Knowledge graphs: {len(graphs)} subjects")

    db_stats = load_db_stats()
    print(f"  Flashcard DB: {db_stats['total_cards']} cards")

    # 2. Compute statistics
    print("\nComputing statistics...")
    data = compute_stats(index, graphs, db_stats)

    print(f"  Countdown: {data['countdown']['days']} days ({data['countdown']['phase']})")
    print(f"  Total notes: {data['notes']['total']}")
    print(f"  Coverage: {data['coverage']['overall']}%")
    print(f"  Heatmap cells: {len(data['heatmap'])}")
    print(f"  Timeline points: {len(data['timeline'])}")
    print(f"  Top gaps: {len(data['top_gaps'])}")
    rs = data['revival']['freshness']['summary']
    print(f"  Note revival: alive={rs['alive_score']}% "
          f"(hot {rs['hot']} / warm {rs['warm']} / cold {rs['cold']} / frozen {rs['frozen']})")

    # 3. Generate HTML
    print("\nGenerating dashboard HTML...")
    html = generate_html(data)

    # 3.1 注入闪卡练习区与盘活区（普通字符串，避免 f-string 大括号转义）
    html = html.replace("/* __FLASH_CSS__ */", FLASH_CSS)
    html = html.replace("// __FLASH_JS__", FLASH_JS)
    html = html.replace("/* __DECK_CSS__ */", DECK_CSS)
    # DECK_JS 不再注入：闪卡画廊已并入主库（题目走 import_deck_cards.py 进了
    # question_bank.db），#deck-library 容器也一并摘掉了。⚠️ 三个 JS 占位符同处
    # 一个 <script> 块，DECK_JS 对容器没有空值守卫，留着会抛错并**连带阻断
    # REVIVE_JS**（笔记弹窗、活动日历全废），所以是"必须不注入"而不是"可以留着"。
    html = html.replace("/* __REVIVE_CSS__ */", REVIVE_CSS)
    # NOTEQ 紧跟 REVIVE：它给阅读器提供「就问这段」面板（靠 __noteQaMount 挂钩），
    # 同时管「笔」页顶部的搜索/筛选。两者都在 NAV_JS 之前，赶得上末尾那次 activate()。
    html = html.replace("/* __NOTEQ_CSS__ */", NOTEQ_CSS)
    html = html.replace("// __NOTEQ_JS__", NOTEQ_JS)
    html = html.replace("// __REVIVE_JS__", REVIVE_JS)
    html = html.replace("/* __NAV_CSS__ */", NAV_CSS)
    html = html.replace("// __NAV_JS__", NAV_JS)
    html = html.replace("/* __FX_CSS__ */", FX_CSS)
    html = html.replace("/* __TASK_CSS__ */", TASK_CSS)
    html = html.replace("/* __THEME_CSS__ */", THEME_CSS)
    html = html.replace("/* __SETTINGS_CSS__ */", SETTINGS_CSS)
    # POMO_CSS 排在 SETTINGS_CSS 之后：番茄钟卡片要借用 .fs-full-toggle / .fs-btn
    # 这些闪卡区定义的控件皮肤，同优先级下后写的规则才有机会微调它们。
    html = html.replace("/* __POMO_CSS__ */", POMO_CSS)
    # SHELL_CSS 紧跟 POMO_CSS：它要借用闪卡区的 .fs-toast、番茄钟的配色变量
    html = html.replace("/* __SHELL_CSS__ */", SHELL_CSS)
    html = html.replace("/* __MR_CSS__ */", MR_CSS)
    # MR_JS 必须在 NAV_JS 之前：它要先把 review 页渲染器注册好，
    # 才赶得上 NAV_JS 末尾那次 activate()（hash 停在 #/review 时首屏才不空白）。
    html = html.replace("// __MR_JS__", MR_JS)
    html = html.replace("/* __RV_CSS__ */", RV_CSS)
    # RV_JS 同样必须在 NAV_JS 之前：它要先把 mistakes/study/overview 的渲染器注册好，
    # 才赶得上 NAV_JS 末尾那次 activate()。
    html = html.replace("// __RV_JS__", RV_JS)
    # BOOT_CSS 排在 RV_CSS 之后：建库引导全屏层要覆盖所有模块（z-index 9999），
    # 同时复用 .fs-btn / .sk-overlay 等既有控件皮肤，后写才盖得住。
    html = html.replace("/* __BOOT_CSS__ */", BOOT_CSS)
    # BOOT_JS 注入在 NAV_JS 之后（脚本最末尾）：引导层在页面渲染完成后才弹，
    # 此时所有渲染器已注册、DOM 已就绪，遮罩盖在最上面不会被任何子页重绘顶掉。
    html = html.replace("// __BOOT_JS__", BOOT_JS)
    # SETTINGS_JS 也要在 NAV_JS 之前：它在加载时立刻应用主题/背景（不能等切到设置页），
    # 同时把设置页渲染器注册好，赶得上 NAV_JS 末尾那次 activate()。
    html = html.replace("// __SETTINGS_JS__", SETTINGS_JS)
    # TASK_JS 必须注入在 NAV_JS **之前**：NAV_JS 末尾会立刻 activate() 一次并调用
    # 该页的渲染器，晚注册就赶不上首屏（hash 直接停在 #/activity 时会空白）。
    html = html.replace("// __TASK_JS__", TASK_JS)
    # POMO_JS 建的是总览页顶部的番茄钟，不注册 __pageRenderers（它不读 clientWidth，
    # 圆环是固定 viewBox 的 SVG），但同样要排在 NAV_JS 之前——它靠末尾的
    # activate() 之前的这段时间把节点搬进 #pm-overlay 的钩子准备好。
    html = html.replace("// __POMO_JS__", POMO_JS)
    # SHELL_JS 在 POMO_JS 之后：首页数据条直接读 POMO_JS 挂在 globalThis 上的
    # __pomoStats()（近 14 天成绩），省一次接口来回。也要在 NAV_JS 之前。
    html = html.replace("// __SHELL_JS__", SHELL_JS)

    # 运行时错误日志必须**最先**注入：要在任何可能抛错的脚本执行前就注册好
    # window 'error'/'unhandledrejection' 监听，才能抓到第一帧错误；也早于 const D。
    html = html.replace("// __LOG_JS__", LOG_JS)

    # 学习日起点必须**最先**注入：FLASH_JS / POMO_JS 一加载就要拿它算「今天」，
    # 晚一行它们就只能退回本地日历日，跟服务端差一天。
    html = html.replace("// __DAY_START__", DAY_START_JS)

    # 快捷键总表要排在所有用到它的模块前面。其实匹配是事件触发时才走的（加载顺序
    # 理论上无所谓），但设置页的提示文案与 FLASH_JS 的 updateHint 都会读它，
    # 放前面省得以后有人把初始化挪到渲染之后，又踩一次「未定义」。
    html = html.replace("// __KEYS_JS__", KEYS_JS)

    # 3.2 输出服务端选题辅助数据（serve.js 的 /api/flashcards/session 读取）
    fresh = data["revival"]["freshness"]
    # 冷笔记前缀：存在冷/冰冻笔记的前缀，练习区选题向其新卡倾斜
    cold_prefixes = sorted({t["prefix"] for t in fresh["targets"]})
    dash_data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "recent_prefixes": data["recent_prefixes"],
        "cold_prefixes": cold_prefixes,
        "weak_topic_ids": [w["id"] for w in data["weak_topics"]["weak"]],
    }
    with open(DASH_DATA_OUT, "w", encoding="utf-8") as f:
        json.dump(dash_data, f, ensure_ascii=False, indent=2)
    print(f"  Dashboard data: {DASH_DATA_OUT.name} "
          f"(recent_prefixes={len(dash_data['recent_prefixes'])}, weak_topics={len(dash_data['weak_topic_ids'])})")

    # 3.5 D3 本地内联：本地大盘要求完全离线可用，
    # 优先内联 src/tools/d3.min.js，缺失时才回退 CDN 引用
    d3_cdn_tag = '<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>'
    d3_local = BASE_DIR / "src" / "tools" / "d3.min.js"
    if d3_cdn_tag in html:
        if d3_local.exists():
            d3_code = d3_local.read_text(encoding="utf-8")
            html = html.replace(d3_cdn_tag, f"<script>{d3_code}</script>", 1)
            print("  D3 inlined from local file (offline-ready)")
        else:
            print("  [WARN] src/tools/d3.min.js 缺失，图表依赖 CDN（离线时不可用）")

    # 4. Write output
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"\nDashboard written to: {OUTPUT_PATH}")
    print(f"File size: {size_kb:.1f} KB")
    print(f"HTML lines: {html.count(chr(10)) + 1}")
    print("\nDone!")


if __name__ == "__main__":
    main()

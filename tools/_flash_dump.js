
// ============================================================
// 全局按需 KaTeX：内容里的 $...$（行内）与 $$...$$（独立行）渲染成公式，其余原样输出
// 供闪卡练习区、闪卡库、笔记预览等所有展示题面/解析的地方共用。
//   1. 先按公式切段再转义——文本段走 escHtml()，公式段走 KaTeX。绝不能先整串转义，
//      否则 \frac 的反斜杠、a<b 的尖括号会先变成实体，KaTeX 收到的是坏源码。
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
    function richText(s) {
        const raw = s == null ? "" : String(s);
        if (raw.indexOf("$") < 0) return escHtml(raw);
        if (!window.katex) ensureKatex();   // 按需加载；就绪后 flushMath 会原地重渲染
        const re = /(\$\$[\s\S]+?\$\$|\$[^$
]+?\$)/g;
        let out = "", last = 0, m;
        while ((m = re.exec(raw)) !== null) {
            if (m.index > last) out += escHtml(raw.slice(last, m.index));
            const seg = m[0];
            // 行内公式的字符集排除了 $，所以以 $$ 开头的一定是独立行公式
            if (seg.slice(0, 2) === "$$") out += katexHtml(seg.slice(2, -2), true);
            else out += katexHtml(seg.slice(1, -1), false);
            last = m.index + seg.length;
        }
        return out + escHtml(raw.slice(last));
    }

    // 登记待渲染元素：texWrap 返回带 data-texid 的容器，KaTeX 就绪后原地重渲染
    let texSeq = 0;
    const texStore = new Map();
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
            if (raw != null) el.innerHTML = richText(raw);
        });
    }

    window.ensureKatex = ensureKatex;
    window.katexHtml = katexHtml;
    window.richText = richText;
    window.texWrap = texWrap;
    window.resetTexStore = resetTexStore;
    window.flushMath = flushMath;
})();

// ============================================================
// 闪卡练习区：看大盘时顺便刷题。选题由服务端完成
// （薄弱卡 > 到期卡 > 近日笔记相关新卡），评分即时 FSRS 回写。
// ============================================================
(function() {
    const API = "http://localhost:8080";
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
                    filter: null };
    // 自动判错的提交句柄：翻页前要等它落地，否则本地推进了而服务端没记账
    let pendingSubmit = null;
    const LS_KEY = "kaoyan_flash_session_v1";
    const LS_FILTER = "kaoyan_flash_filter_v1";   // 筛选条件单独存，跨「重开一组」保留
    // 筛选页上**待确认**的选择。与 state.filter（正在生效的）分开，否则点一下 chip
    // 就会让筛选条件和场上正在刷的卡对不上。
    let pending = { subject: "", bucket: "" };

    function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
    // ⚠️ 本地日期，不要用 toISOString().slice(0,10)（那是 UTC，UTC+8 早 8 点前会差一天）
    function todayStr() {
        const d = new Date();
        return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
    }

    // ---- 进度保持：本组会话存 localStorage，刷新后从断点继续 ----
    function saveSession() {
        try {
            localStorage.setItem(LS_KEY, JSON.stringify({
                date: todayStr(), cards: state.cards, idx: state.idx, stats: state.stats,
                lastRatedIdx: state.lastRatedIdx, filter: state.filter
            }));
        } catch (e) {}
    }
    function clearSession() { try { localStorage.removeItem(LS_KEY); } catch (e) {} }
    function tryResume() {
        try {
            const raw = localStorage.getItem(LS_KEY);
            if (!raw) return false;
            const s = JSON.parse(raw);
            if (s.date !== todayStr() || !Array.isArray(s.cards) || s.idx >= s.cards.length) return false;
            state.cards = s.cards; state.idx = s.idx;
            state.stats = s.stats || {1: 0, 2: 0, 3: 0, 4: 0};
            state.lastRatedIdx = typeof s.lastRatedIdx === "number" ? s.lastRatedIdx : null;
            state.filter = (s.filter && typeof s.filter === "object") ? s.filter : readFilter();
            return true;
        } catch (e) { return false; }
    }
    function fetchToday() {
        fetch(API + "/api/flashcards/today").then(r => r.json()).then(d => {
            if (d.ok) state.reviewedToday = d.reviewed_today;
        }).catch(() => {});
    }

    // ---- 筛选条件 ----
    const SUBJECTS = ["政治", "408", "数学一", "英语一"];
    const BUCKETS = ["", "new", "learning", "review", "mature", "leech", "suspended"];
    function readFilter() {
        try {
            const f = JSON.parse(localStorage.getItem(LS_FILTER) || "null");
            if (!f || typeof f !== "object") return null;
            return normalizeFilter(f);
        } catch (e) { return null; }
    }
    function normalizeFilter(f) {
        const subject = SUBJECTS.includes(f.subject) ? f.subject : "";
        const bucket = BUCKETS.includes(f.bucket) ? f.bucket : "";
        return (subject || bucket) ? { subject: subject, bucket: bucket } : null;
    }
    function applyFilter(f) {
        state.filter = f ? normalizeFilter(f) : null;
        try {
            if (state.filter) localStorage.setItem(LS_FILTER, JSON.stringify(state.filter));
            else localStorage.removeItem(LS_FILTER);
        } catch (e) {}
        renderFilterBar();
    }
    // 筛选页点「开始」走这里；也供卡片头部的科目快捷入口复用
    function startWithFilter(f) {
        applyFilter(f);
        clearSession();
        loadSession(true);
    }
    // 暴露到 globalThis 而不是 window：test_flash_keyboard.js 用
    // new Function("document","localStorage","fetch", js) 注入执行，
    // 那个作用域里没有 window，写 window.xxx 会让 47 项测试全炸。
    globalThis.__flashApplyFilter = applyFilter;
    globalThis.__flashStart = startWithFilter;

    async function loadSession(fresh) {
        if (fresh) clearSession();
        box.innerHTML = '<div class="fs-loading">正在为你挑选针对性闪卡…</div>';
        // 无筛选时 URL 与改造前逐字一致（?limit=30），保证既有一致性与测试稳定
        let url = API + "/api/flashcards/session?limit=" + (state.filter ? 50 : 30);
        if (state.filter) {
            if (state.filter.subject) url += "&subject=" + encodeURIComponent(state.filter.subject);
            if (state.filter.bucket) url += "&bucket=" + encodeURIComponent(state.filter.bucket);
            url += "&mode=browse";
        }
        try {
            const resp = await fetch(url);
            const data = await resp.json();
            if (!data.ok || !data.cards || data.cards.length === 0) {
                box.innerHTML = '<div class="fs-empty">题库暂无卡片</div>';
                return;
            }
            state.cards = data.cards; state.idx = 0;
            state.stats = {1: 0, 2: 0, 3: 0, 4: 0};
            state.limits = data.limits || null;
            state.counts = data.counts || null;
            state.lastRating = null;
            state.lastRatedIdx = null;
            state.autoWrong = false; state.saveFailed = false; pendingSubmit = null;
            saveSession();
            renderCard();
        } catch (e) {
            box.innerHTML = '<div class="fs-empty">⚠️ 无法连接本地复习服务。<br>请关闭本页，改用桌面上的「考研大盘」快捷方式打开（它会自动启动服务）。</div>';
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
        if (!state.revealed) {
            if (!opts.length) el.textContent = "快捷键：空格 显示答案 · U 撤销上一张";
            else if (c.type === "judge") el.textContent = "快捷键：1 正确 · 2 错误 · 空格 显示答案";
            else el.textContent = "快捷键：A–D 或 1–4 选选项 · 空格 显示答案";
        } else if (state.autoWrong) {
            el.textContent = "已记「忘记」· 空格 / 回车 下一张 · U 撤销重答";
        } else {
            el.textContent = "自评：2 模糊 · 3 记得 · 4 简单（空格 = 记得）· U 撤销";
        }
    }

    function renderCard() {
        if (state.idx >= state.cards.length) { renderSummary(); return; }
        const c = state.cards[state.idx];
        const ct = c.content || {};
        state.revealed = false; state.answered = false;
        state.autoWrong = false; state.saveFailed = false; pendingSubmit = null;
        resetTexStore();   // 上一张的 tex 登记随 DOM 一起作废，避免无限累积

        let html = '<div class="fs-head">'
            + '<span class="fs-progress">第 ' + (state.idx + 1) + ' / ' + state.cards.length + ' 张</span>'
            + '<span class="fs-badge due">今日已复习 ' + (state.reviewedToday || 0) + '</span>'
            + badge(c)
            + limitsHtml()
            + '<span class="fs-topic">' + esc(c.subject) + ' · ' + esc(c.topic_name) + '</span>'
            + '<button class="fs-btn" id="fs-stats-btn" style="margin-left:auto;padding:2px 10px;font-size:0.7rem;">📊 统计</button>'
            + '<button class="fs-btn" id="fs-restart" style="padding:2px 10px;font-size:0.7rem;">重开一组</button>'
            + '</div>'
            + '<div id="fs-stats-panel" class="fs-stats-panel" data-open="0"></div>';
        html += '<div class="fs-stem">' + texWrap(ct.stem || ct.question || "") + '</div>';
        html += '<div id="fs-body"></div><div id="fs-feedback"></div>';
        box.innerHTML = html;
        const rs = document.getElementById("fs-restart");
        if (rs) rs.onclick = () => loadSession(true);
        const sb = document.getElementById("fs-stats-btn");
        if (sb) sb.onclick = () => showStats();

        const body = document.getElementById("fs-body");
        const isJudge = c.type === "judge";
        const opts = optionsOf(c);

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
            btn.textContent = "显示答案（空格）";
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

        if (ci >= 0 && i === ci) { showFeedback(ct); return; }

        // 选错：立即按 Again(1) 回写，但卡片停在本页让用户看清正确答案与解析，
        // 空格/回车才翻页。回写失败则退回手动评分（见 showFeedback 的 saveFailed 分支）。
        state.autoWrong = true;
        showFeedback(ct);
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
        showFeedback(ct);
    }

    // 把答案规范成可读文本（布尔 → 正确/错误）
    function answerText(ct) {
        if (typeof ct.answer === "boolean") return ct.answer ? "正确" : "错误";
        const a = ct.answer != null ? String(ct.answer).trim() : "";
        if (/^true$/i.test(a)) return "正确";
        if (/^false$/i.test(a)) return "错误";
        return a;
    }

    function showFeedback(ct) {
        const fb = document.getElementById("fs-feedback");
        const card = state.cards[state.idx] || {};
        let html = "";

        // 答案行：文本型答案（判断/填空/简答）直接显示；选择题库里 answer 是序号，
        // 单靠 answerText 会得到空串，这里补成「正确答案：A. 选项原文」。
        const ans = answerText(ct);
        const isTextAnswer = typeof ct.answer === "boolean"
            || (typeof ct.answer === "string" && !/^[A-Da-d]$/.test(String(ct.answer).trim()));
        if (isTextAnswer && ans) {
            html += '<div class="fs-explain"><b>答案：</b>' + texWrap(ans) + '</div>';
        } else {
            const opts = optionsOf(card);
            const ci = correctIndex(ct, opts);
            if (ci >= 0 && opts[ci] != null) {
                html += '<div class="fs-explain"><b>正确答案：</b>'
                    + texWrap(String.fromCharCode(65 + ci) + ". " + opts[ci]) + '</div>';
            }
        }
        if (ct.explanation) html += '<div class="fs-explain">' + texWrap(ct.explanation) + '</div>';
        if (Array.isArray(ct.traps) && ct.traps.filter(Boolean).length)
            html += '<div class="fs-traps">⚠ 易错点：' + ct.traps.filter(Boolean).map(texWrap).join("；") + '</div>';

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
                + [1, 2, 3, 4].map(r => '<button class="fs-btn fs-rate' + r + '" data-rate="' + r + '">'
                    + '<span class="fs-rate-label">' + r + ' ' + LABELS[r] + '</span>'
                    + (pv[r] ? '<span class="fs-rate-pv">' + esc(pv[r]) + '</span>' : '')
                    + '</button>').join("")
                + '</div>';
        }
        html += '<div class="fs-undo-row"><button class="fs-btn fs-undo" id="fs-undo">'
            + (state.autoWrong ? '↶ 撤销，重新作答（U）' : '↶ 撤销上一次评分（U）')
            + '</button></div>';
        fb.innerHTML = html;

        fb.querySelectorAll("[data-rate]").forEach(b => {
            b.onclick = () => rate(parseInt(b.dataset.rate, 10));
        });
        const nb = document.getElementById("fs-next");
        if (nb) nb.onclick = () => goNext();
        const ub = document.getElementById("fs-undo");
        if (ub) ub.onclick = () => undo();
        updateHint();
    }

    // 撤销上一次评分：服务端按 review_log 快照还原卡片，本地回到那张卡重新作答。
    // 目标卡由 lastRatedIdx 决定（不是 idx-1）：「选错即判」时 idx 还没翻页。
    async function undo() {
        if (state.sending) return;
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
        state.sending = true;
        let ok = false, resp = null;
        try {
            const r0 = await fetch(API + "/api/flashcards/review", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({ card_id: c.card_id, rating: r })
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
        if (pendingSubmit) { pendingSubmit.then(() => { if (!state.saveFailed) go(); }); return; }
        if (state.saveFailed) return;
        go();
    }

    async function rate(r) {
        if (!state.revealed || state.sending) return;
        if (await submitRating(r)) goNext();
    }

    function renderSummary() {
        clearSession();
        const s = state.stats;
        const total = s[1] + s[2] + s[3] + s[4];
        box.innerHTML = '<div class="fs-summary"><h3>本组完成 🎉</h3>'
            + '<p>共 ' + total + ' 张 · 忘记 ' + s[1] + ' · 模糊 ' + s[2] + ' · 记得 ' + s[3] + ' · 简单 ' + s[4] + '</p>'
            + '<p style="font-size:0.75rem;color:var(--text-muted);">今日累计已复习 ' + (state.reviewedToday || 0) + ' 张</p>'
            + '<button class="fs-btn" id="fs-again">再来一组</button></div>';
        document.getElementById("fs-again").onclick = () => loadSession(true);
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
        { key: "suspended", label: "已暂停" },
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

    function renderFilterBar() {
        if (!fbox) return;
        const buckets = BUCKET_META.map(b =>
            chip(b.label, b.key, bucketCount(b.key), pending.bucket === b.key, "data-bucket")).join("");
        const subs = [""].concat(SUBJECTS).map(s =>
            chip(s || "全部", s, subjectCount(s), pending.subject === s, "data-subject")).join("");
        const n = subjectCount(pending.subject || "");
        const parts = [];
        if (pending.subject) parts.push(pending.subject);
        const bm = BUCKET_META.filter(b => b.key === pending.bucket)[0];
        if (bm && bm.key) parts.push(bm.label);
        const scope = parts.length ? parts.join(" · ") : "全部闪卡";
        fbox.innerHTML =
            '<h2>闪卡筛选</h2>'
            + '<div class="ff-group"><div class="ff-label">状态</div><div class="ff-chips">' + buckets + '</div></div>'
            + '<div class="ff-group"><div class="ff-label">科目</div><div class="ff-chips">' + subs + '</div></div>'
            + '<div class="ff-foot">'
            +   '<span class="ff-summary">当前范围：' + esc(scope)
            +     (n == null ? "" : ' · <b>' + n + '</b> 张') + '</span>'
            +   '<button class="fs-btn fs-next" id="ff-start">开始刷题</button>'
            +   '<button class="fs-btn" id="ff-all">全部闪卡</button>'
            + '</div>';
        fbox.querySelectorAll("[data-bucket]").forEach(b => {
            b.onclick = () => { pending.bucket = b.dataset.bucket; renderFilterBar(); };
        });
        fbox.querySelectorAll("[data-subject]").forEach(b => {
            b.onclick = () => { pending.subject = b.dataset.subject; renderFilterBar(); };
        });
        const sb = fbox.querySelector("#ff-start");
        if (sb) sb.onclick = () => startWithFilter(pending);
        const ab = fbox.querySelector("#ff-all");
        if (ab) ab.onclick = () => { pending = { subject: "", bucket: "" }; startWithFilter(null); };
    }

    // 键盘分四个阶段，互不重叠（数字键在不同阶段含义不同，靠状态消歧）：
    //   ① 未作答的选择/判断 → A–D / 1–4 选选项，空格看答案
    //   ② 已自动判错        → 空格/回车 下一张，U 撤销重答
    //   ③ 已显示答案        → 1–4 自评（空格 = 记得，Anki 惯例）
    //   ④ 未显示答案的填空/简答 → 空格显示答案
    document.addEventListener("keydown", (e) => {
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        if (!state.cards.length || state.idx >= state.cards.length) return;
        if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
        // 读笔记弹层 / 卡组覆盖层打开时不要评分——那时数字键是在翻笔记，不是在答题
        if (document.querySelector(".rev-modal, .deck-overlay")) return;
        const k = e.key;
        const isEnter = e.code === "Space" || k === "Enter";

        // U 在哪个阶段都是撤销，先拦下来。选项键只占 A–I / 1–9，不会和它撞。
        if (k === "u" || k === "U") { e.preventDefault(); undo(); return; }

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
            if (isEnter) { e.preventDefault(); reveal(); }
            return;
        }

        // ② 已自动判错：只等翻页
        if (state.autoWrong) {
            if (isEnter) { e.preventDefault(); goNext(); }
            return;
        }

        // ③ 自评
        if (state.revealed) {
            if (["1", "2", "3", "4"].includes(k)) { e.preventDefault(); rate(parseInt(k, 10)); }
            else if (isEnter) { e.preventDefault(); rate(3); }
            return;
        }

        // ④ 填空/简答：先看答案
        if (isEnter) {
            e.preventDefault();
            const bodyBtn = document.querySelector("#fs-body .fs-btn");
            if (bodyBtn) bodyBtn.click();
        }
    });

    // 启动时把上次的筛选选择也恢复出来，让筛选页显示的选择和场上正在刷的卡一致
    const savedFilter = readFilter();
    if (savedFilter) {
        pending = { subject: savedFilter.subject, bucket: savedFilter.bucket };
        state.filter = savedFilter;
    }
    renderFilterBar();
    fetchToday();
    loadFacets();
    if (tryResume()) { renderCard(); } else { loadSession(); }
})();

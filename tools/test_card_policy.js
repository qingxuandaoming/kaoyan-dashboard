/*
 * 组题策略的用例（2026-09-19）
 *
 * 起因是用户的一句话：
 *   「这些闪卡有些我连对两次连对三次，其实就没必要再排了…优先还是排我没有练过的卡，
 *     然后优先排新卡，新出的关于我最近的知识上的那些卡。就算我对的那些题，如果我对 AI
 *     有过追问，那可以给我一个 pin 的键，我可以把它钉在那个卡的位置，下次我看到它的时候，
 *     我可以再看看它。像这种有过追问的可以出现 3 次 4 次，但没有追问又连续对的，就可以降低优先级。」
 *
 * 这条链上最容易出事的三处，用例就盯这三处：
 *   ① **连对的判定**：得按 id 倒序取最近几次（同一天可能答多次），
 *      而且是「连续」对——中间错一次就该断掉；
 *   ② **追问的判定**：explain_log 的 card_id 列是空的（只能靠 question_id 关联），
 *      而且「他打的字」不等于全部 user 行（服务端会自己写一条「我的选择：X」）——
 *      这两条任何一条漏了，就会出现「明明问过却被当成连对退役」；
 *   ③ **钉住的语义**：钉住的卡绝不能被退役（他钉住的意思就是「下次还要看见它」），
 *      取消钉住要真的删掉那一行。
 *
 * 跑在**真题库的副本**上：不碰 src/question_bank.db（那是他正在用的活数据）。
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { DatabaseSync } = require("node:sqlite");

const SRC = path.join(__dirname, "..");
const policy = require(path.join(SRC, "card_policy.js"));
const REAL_DB = path.join(SRC, "question_bank.db");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

const tmp = path.join(os.tmpdir(), "kaoyan_card_policy_" + Date.now() + ".db");
fs.copyFileSync(REAL_DB, tmp);
const realSizeBefore = fs.statSync(REAL_DB).size;

// 造一个干净的小世界，不受真库历史影响（真库现在已经有几百条答题记录）
function freshWorld() {
  const db = new DatabaseSync(tmp);
  db.exec(`
    CREATE TABLE IF NOT EXISTS _t (x INTEGER);
    DELETE FROM review_log;
    DELETE FROM explain_log;
    CREATE TABLE IF NOT EXISTS card_pins (
      card_id TEXT PRIMARY KEY, note TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')), updated_at TEXT);
    DELETE FROM card_pins;
  `);
  return db;
}

// 挑两张真实存在的卡来当样本（题目/考点都在，追问与钉住才有意义）
{
  const db = new DatabaseSync(tmp, { readOnly: true });
  var SAMPLE = db.prepare("SELECT id, question_id FROM cards ORDER BY id LIMIT 4").all();
  db.close();
}
const C1 = SAMPLE[0], C2 = SAMPLE[1], C3 = SAMPLE[2], C4 = SAMPLE[3];

function addReview(db, card, qid, rating, dayOffset) {
  db.prepare(`INSERT INTO review_log (card_id, question_id, rating, review_date)
              VALUES (?, ?, ?, datetime('now','localtime', ?))`)
    .run(card, qid, rating, `-${dayOffset} days`);
}
function addAsk(db, qid, text, dayOffset) {
  db.prepare(`INSERT INTO explain_log (thread_id, question_id, card_id, role, content, created_at)
              VALUES (?, ?, NULL, 'user', ?, datetime('now','localtime', ?))`)
    .run("t-" + qid, qid, text, `-${dayOffset} days`);
}

// ---------------------------------------------------------------------------
console.log("\n[1] 连对的判定：按 id 倒序取最近几次，且必须是**连续**的");
{
  const db = freshWorld();
  // C1：最近三次都是 3/4 → 连对 3
  addReview(db, C1.id, C1.question_id, 4, 5);
  addReview(db, C1.id, C1.question_id, 3, 3);
  addReview(db, C1.id, C1.question_id, 3, 1);
  // C2：最近对、再往前错 → 连对只有 1（中间的错必须断掉）
  addReview(db, C2.id, C2.question_id, 4, 7);
  addReview(db, C2.id, C2.question_id, 1, 4);
  addReview(db, C2.id, C2.question_id, 3, 1);
  // C3：最近错 → 连对 0
  addReview(db, C3.id, C3.question_id, 3, 6);
  addReview(db, C3.id, C3.question_id, 2, 1);

  const rev = policy.loadReviewSignals(db);
  check("三次连对 → streak=3", rev.get(C1.id).streak === 3, JSON.stringify(rev.get(C1.id)));
  check("中间错过一次 → streak 只有 1", rev.get(C2.id).streak === 1, JSON.stringify(rev.get(C2.id)));
  check("最近一次是错 → streak=0", rev.get(C3.id).streak === 0, JSON.stringify(rev.get(C3.id)));
  check("练习次数照实统计", rev.get(C2.id).reviews === 3);

  // ⚠️ 同一天答多次：先后只能靠 id，不能靠 review_date 字符串
  const db2 = freshWorld();
  const sameDay = "datetime('now','localtime')";
  db2.prepare(`INSERT INTO review_log (card_id, question_id, rating, review_date)
               VALUES (?, ?, 2, ${sameDay})`).run(C1.id, C1.question_id);
  db2.prepare(`INSERT INTO review_log (card_id, question_id, rating, review_date)
               VALUES (?, ?, 4, ${sameDay})`).run(C1.id, C1.question_id);
  const rev2 = policy.loadReviewSignals(db2);
  check("★ 同一天先错后对 → 算连对 1（按 id 而不是日期字符串）",
    rev2.get(C1.id).streak === 1, JSON.stringify(rev2.get(C1.id)));
  db2.close();
  db.close();
}

// ---------------------------------------------------------------------------
console.log("\n[2] 追问的判定：按 question_id 找，且要认「他打的字」而不是自动写的那条");
{
  const db = freshWorld();
  addAsk(db, C1.question_id, "【我问】为什么这里要乘 x^k？", 2);
  addAsk(db, C2.question_id, "关中断之后不会阻塞吗？", 1);          // 同一线索里的裸追问
  addAsk(db, C3.question_id, "我的选择：A. 直接寻址", 1);            // 自动写的，不算他提问
  const signals = policy.loadSignals(db);
  const s1 = policy.signalFor(signals, C1.id, C1.question_id);
  const s2 = policy.signalFor(signals, C2.id, C2.question_id);
  const s3 = policy.signalFor(signals, C3.id, C3.question_id);
  check("★ 带【我问】前缀的算追问", s1.asks === 1, JSON.stringify(s1));
  check("★ 裸追问（同一线索里接着问的）也算", s2.asks === 1, JSON.stringify(s2));
  check("★ 自动写的「我的选择：…」不算他提问", s3.asks === 0, JSON.stringify(s3));
  check("没问过的卡 asks=0", policy.signalFor(signals, C4.id, C4.question_id).asks === 0);
  db.close();
}

// ---------------------------------------------------------------------------
console.log("\n[2b] 聚合路径不能丢字段（loadSignals → signalFor 是服务端走的那条）");
{
  // 这条是 E2E 抓到的真 bug：loadSignals 组装 byCard 时漏了 `recent`，
  // 于是服务端拿到的信号里没有作答时间 → afterAskCorrect 恒为 0 → 「问过」又变回永久特权。
  // 单测手搓 sig 带着 recent，所以只测规则是抓不到的，必须测**聚合函数**。
  const db = freshWorld();
  addReview(db, C1.id, C1.question_id, 4, 3);
  addReview(db, C1.id, C1.question_id, 4, 1);
  addAsk(db, C1.question_id, "【我问】为什么？", 5);
  const sig = policy.signalFor(policy.loadSignals(db), C1.id, C1.question_id);
  check("★ 聚合后仍带着 recent（作答时间）", Array.isArray(sig.recent) && sig.recent.length === 2,
    JSON.stringify(sig.recent));
  check("★ 聚合后 afterAskCorrect 算得出 2（不是 0）", policy.afterAskCorrect(sig) === 2,
    String(policy.afterAskCorrect(sig)));
  check("★ 聚合后的 streak 也对", sig.streak === 2, String(sig.streak));
  db.close();
}

// ---------------------------------------------------------------------------
console.log("\n[3] 退役与降权：连对够多且没问过才收起来，钉住的一律不收");
{
  const sig = (o) => Object.assign({ streak: 0, reviews: 0, asks: 0, pinned: false }, o);
  check("连对 3 次、没问过、没钉住 → 退役", policy.isRetired(sig({ streak: 3 })) === true);
  check("连对 2 次 → 还不够退役", policy.isRetired(sig({ streak: 2 })) === false);
  check("★ 问过的卡不退役", policy.isRetired(sig({ streak: 5, asks: 2 })) === false);
  check("★ 钉住的卡不退役", policy.isRetired(sig({ streak: 5, pinned: true })) === false);
  check("连对 2 次 → 降权档", policy.isDemoted(sig({ streak: 2 })) === true);
  check("问过的卡不降权", policy.isDemoted(sig({ streak: 4, asks: 1 })) === false);
  check("阈值可调：retire=2 时连对 2 次就退役",
    policy.isRetired(sig({ streak: 2 }), { retireStreak: 2 }) === true);
  check("阈值可关：retire=0 时永不退役",
    policy.isRetired(sig({ streak: 9 }), { retireStreak: 0 }) === false);
}

// ---------------------------------------------------------------------------
console.log("\n[3b] 「问过」的出口：提问之后又连对够多次，就该退出这个待遇（2026-09-19 用户要求）");
{
  // 用户原话：「你这样我问过的，那永远优先级都高了，但不应该是这样。如果后来我已经学会了、
  // 掌握了这个点，它应该有一定的退出机制。比如说我连续都对。」
  const now = Date.now();
  const at = (dayAgo) => new Date(now - dayAgo * 86400000).toISOString().slice(0, 19).replace("T", " ");
  // recent 是「近→远」。streak 要照真实 loader 那样算（从最新往回连续对的个数），
  // 否则手搓的 sig 与生产里的对象不是一个东西，断言会假红/假绿。
  const mkSig = (recent, asks = 1, lastAsk = at(5)) => {
    let st = 0;
    for (const r of recent) { if (r.rating >= 3) st += 1; else break; }
    return { asks, lastAsk, streak: st, reviews: recent.length, recent, pinned: false };
  };
  const sigAsk = mkSig;

  // ① 提问后又连对 2 次（不够 3）→ 豁免还在
  const two = sigAsk([{ rating: 4, date: at(1) }, { rating: 3, date: at(2) }, { rating: 1, date: at(6) }]);
  check("提问后连对 2 次 → 还算卡点（豁免仍在）", policy.afterAskCorrect(two) === 2);
  check("  └ 仍旧不退役", policy.isRetired(two) === false);
  check("  └ 仍旧置顶档 ASKED", policy.priorityOf(two, {}).prio === policy.PRIO.ASKED);

  // ② 提问后又连对 3 次 → 退出
  const three = sigAsk([{ rating: 4, date: at(1) }, { rating: 3, date: at(2) }, { rating: 3, date: at(3) }]);
  check("★ 提问后连对 3 次 → 豁免作废", policy.askExemptionHolds(three) === false);
  check("★ 于是按普通卡退役（移出智能组）", policy.isRetired(three) === true);
  check("★ 不再占 ASKED 档", policy.priorityOf(three, {}).prio !== policy.PRIO.ASKED,
    JSON.stringify(policy.priorityOf(three, {})));

  // ③ **提问之前**的连对不算数（那时他还没搞懂）
  const beforeOnly = sigAsk([{ rating: 4, date: at(9) }, { rating: 4, date: at(8) },
                             { rating: 4, date: at(7) }], 1, at(5));
  check("★ 提问之前的连对不算「学会了」", policy.afterAskCorrect(beforeOnly) === 0);
  check("  └ 豁免仍然成立", policy.askExemptionHolds(beforeOnly) === true);

  // ④ 提问后又错过一次 → 连续计数被打断（从最新往回数，遇到错就停）
  const wrongAfter = sigAsk([{ rating: 4, date: at(1) }, { rating: 2, date: at(2) },
                             { rating: 4, date: at(3) }, { rating: 4, date: at(4) }]);
  check("★ 提问后又错过一次 → 计数被截断，攒不到出口", policy.afterAskCorrect(wrongAfter) === 1,
    String(policy.afterAskCorrect(wrongAfter)));
  check("  └ 豁免仍在", policy.askExemptionHolds(wrongAfter) === true);

  // ⑤ 阈值可调 / 可关
  check("阈值可调：ask_exit=2 时连对 2 次就退出",
    policy.askExemptionHolds(two, { askExitStreak: 2 }) === false);
  check("阈值可关：ask_exit=0 → 退回旧行为（永远豁免）",
    policy.askExemptionHolds(three, { askExitStreak: 0 }) === true);

  // ⑥ 钉住仍然是唯一的永久例外
  const pinnedAsked = Object.assign(sigAsk([{ rating: 4, date: at(1) }, { rating: 4, date: at(2) },
                                            { rating: 4, date: at(3) }]), { pinned: true });
  check("★ 📌 钉住的是唯一永久例外（连对再多也不退役）", policy.isRetired(pinnedAsked) === false);
  check("  └ 而且它照旧置顶档 PINNED",
    policy.priorityOf(pinnedAsked, {}).prio === policy.PRIO.PINNED);
}

// ---------------------------------------------------------------------------
console.log("\n[4] 优先级阶梯：钉住 > 问过 > 反复忘 > 新出的卡 > 没练过 > 薄弱 > 到期 > 连对降权");
{
  const P = policy.PRIO;
  const pinned = policy.priorityOf({ streak: 9, pinned: true, asks: 0 }, { due: true });
  const asked = policy.priorityOf({ streak: 9, asks: 2 }, { due: true });
  const leech = policy.priorityOf({ streak: 0 }, { leech: true, due: true });
  const fresh = policy.priorityOf({ reviews: 0 }, { isNew: true, freshCard: true, recentTopic: true });
  const freshCold = policy.priorityOf({ reviews: 0 }, { isNew: true, coldTopic: true });
  const brandNew = policy.priorityOf({ reviews: 0 }, { isNew: true });
  const weak = policy.priorityOf({ reviews: 3, streak: 0 }, { weak: true, due: true });
  const due = policy.priorityOf({ reviews: 3 }, { due: true });
  const streak2 = policy.priorityOf({ reviews: 3, streak: 2 }, {});
  check("钉住置顶", pinned.prio === P.PINNED, JSON.stringify(pinned));
  check("★ 问过的排在反复忘之前（他当场没想通的地方优先再看）", asked.prio > leech.prio);
  check("★ 反复忘的排在没练过的之前", leech.prio > brandNew.prio);
  check("★ 新出的、挂在近日学的考点上的卡 → 比普通新卡还高",
    fresh.prio === P.FRESH_NEW && fresh.prio > brandNew.prio, JSON.stringify(fresh));
  check("冷笔记考点的新卡同样抬起来", freshCold.prio === P.FRESH_NEW);
  check("★ 没练过的卡排在到期复习之前", brandNew.prio > due.prio);
  check("薄弱卡高于普通到期", weak.prio > due.prio);
  check("连对 2 次降到最低档之一（但仍在组里）", streak2.prio === P.STREAK2, JSON.stringify(streak2));
  // ⚠️ 这条是 E2E 实测抓到的：连对 2 次的卡往往正好"到期"，如果先判 due，
  //    「连对两次就降」永远不会生效（当时拿到 prio=3 DUE）。
  check("★ 连对 2 次的卡即使已到期，也走降权档（不被 DUE 抢走）",
    policy.priorityOf({ reviews: 3, streak: 2 }, { due: true }).prio === P.STREAK2,
    JSON.stringify(policy.priorityOf({ reviews: 3, streak: 2 }, { due: true })));
  check("★ 但降权不是不许出现：到期卡照样是候选（档位只影响先后）",
    policy.isRetired({ streak: 2 }) === false);
  check("阶梯数字没有重复（排序才有确定含义）",
    new Set(Object.values(P)).size === Object.keys(P).length);
  check("每档都有中文标签", Object.values(P).every(v => policy.PRIO_LABEL[v]));
}

// ---------------------------------------------------------------------------
console.log("\n[5] 新出的卡：按入库时间判定（近 14 天）");
{
  const now = Date.now();
  const iso = (msAgo) => new Date(now - msAgo).toISOString().slice(0, 19).replace("T", " ");
  check("3 天前入库 → 新", policy.isFreshCard(iso(3 * 86400000), now) === true);
  check("30 天前入库 → 不算新", policy.isFreshCard(iso(30 * 86400000), now) === false);
  check("没有入库时间 → 不算（不猜）", policy.isFreshCard("", now) === false);
  check("脏数据不抛异常", policy.isFreshCard("不是日期", now) === false);
}

// ---------------------------------------------------------------------------
console.log("\n[6] 钉住：写入 / 取消 / 清单（跑在副本上）");
{
  const db = freshWorld();
  check("一开始没有钉住的", policy.countPins(db) === 0);
  const r1 = policy.setPin(db, C1.id, true, "这道题的解法我要再看");
  check("钉住成功", r1.ok === true && r1.pinned === true, JSON.stringify(r1));
  check("数量 +1", policy.countPins(db) === 1);
  const r1b = policy.setPin(db, C1.id, true, "改个备注");
  check("重复钉住不报错、也不多一行（幂等）", r1b.ok === true && policy.countPins(db) === 1);
  policy.setPin(db, C2.id, true);
  const pins = policy.listPins(db, {});
  check("清单带出题目与考点（筛选页要显示）", pins.length === 2 && !!pins[0].stem,
    JSON.stringify(pins[0] && pins[0].stem));
  check("清单带出备注", pins.some(p => p.note === "改个备注"));
  const r2 = policy.setPin(db, C1.id, false);
  check("取消钉住成功", r2.ok === true && r2.pinned === false);
  check("★ 取消后真的少了一张", policy.countPins(db) === 1);
  check("★ 不存在的卡号 → 拒绝（不许写进一张空气卡）",
    policy.setPin(db, "C-不存在-0001", true).ok === false);
  check("★ 非法卡号（中文/SQL 片段）→ 拒绝", policy.setPin(db, "'; DROP TABLE cards;--", true).ok === false);
  check("card_id 白名单：合法卡号原样通过", policy.normalizeCardId(C1.id) === C1.id);
  check("card_id 白名单：中文被拒", policy.normalizeCardId("卡-A") === "");

  // 信号里能读回钉住状态
  const signals = policy.loadSignals(db);
  check("★ 组题信号里能读到钉住（否则智能组没法置顶它）",
    policy.signalFor(signals, C2.id, C2.question_id).pinned === true);
  db.close();
}

// ---------------------------------------------------------------------------
console.log("\n[7] 别碰真库");
{
  check("★ 全程没动 src/question_bank.db（文件大小不变）",
    fs.statSync(REAL_DB).size === realSizeBefore);
}

try { fs.unlinkSync(tmp); } catch (e) { /* ignore */ }

console.log("\n" + (fail ? "有失败：" : "全部通过：") + "pass=" + pass + " fail=" + fail);
process.exit(fail ? 1 : 0);

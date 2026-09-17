/*
 * 「这道题有问题」标记与复核的用例（2026-09-17）
 *
 * 起因是用户在练习里撞到一张**题目本身错了**的卡（选择题 A、C 都对），他要的是
 * 「练习时能标出来 + 每日任务里 agent 能拿到并修好」。这条链上有三段最容易出事：
 *   ① 报卡去重 —— 同一张卡标两次不能变成两条待修（否则每日任务里同一题修两遍）；
 *   ② 改题的校验 —— 模型来改题，改坏了（选项少一条、答案越界、把答案指到别的项）
 *      比不改更糟，所以白名单 + 校验必须拦得住；
 *   ③ 复核后的状态回写 —— 修完徽标要消失、待修数要减，否则用户会以为标记丢了。
 *
 * 跑在**真题库的副本**上：不碰 src/question_bank.db（那是他正在用的活数据）。
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const { DatabaseSync } = require("node:sqlite");

const SRC = path.join(__dirname, "..");
const reports = require(path.join(SRC, "card_reports.js"));
const REAL_DB = path.join(SRC, "question_bank.db");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

// ---------------------------------------------------------------------------
console.log("\n[1] 原因 id：认得的→原样，认不得的→other（不能落进一个前端没有标签的值）");
{
  check("multi_correct 认得", reports.normalizeKind("multi_correct") === "multi_correct");
  check("大小写/空格容错", reports.normalizeKind("  Answer_Wrong ") === "answer_wrong");
  check("乱七八糟的值退回 other", reports.normalizeKind("题目有毒") === "other");
  check("空值退回 other", reports.normalizeKind(null) === "other");
  check("每个 id 都有中文标签", reports.KINDS.every(k => k.id && k.label));
  check("中文标签查得到", reports.kindLabel("multi_correct") === "多个选项都对");
  check("★ 标签表与库里的默认值一致（other 必须在表里）",
    reports.KIND_IDS.indexOf("other") >= 0);
}

// ---------------------------------------------------------------------------
console.log("\n[2] 改题的校验（模型改题的闸门）");
{
  const base = {
    stem: "已知 y₁* 是 f₁(x) 的特解", options: ["甲", "乙", "丙", "丁"],
    answer: 0, explanation: "因为甲", traps: ["别忘特解"],
  };
  const v1 = reports.validatePatch({ options: ["甲改", "乙", "丙", "丁"], answer: 0 }, base, "choice");
  check("正常改选项+答案：通过", v1.ok === true, JSON.stringify(v1));
  check("记下了改了哪些字段", v1.ok && v1.changes.join(",") === "options,answer", JSON.stringify(v1.changes));

  const p1 = reports.validatePatch({ options: ["A. 甲", "B、乙", "C）丙", "D：丁"], answer: 1 }, base, "choice");
  check("★ 选项自带的 A./B、 前缀被剥掉（渲染层已经画了字母徽章）",
    p1.ok && p1.content.options[0] === "甲" && p1.content.options[2] === "丙", JSON.stringify(p1.content));

  check("选项少一条 → 拒",
    reports.validatePatch({ options: ["甲", "乙", "丙"] }, base, "choice").ok === false);
  check("选项有空的 → 拒",
    reports.validatePatch({ options: ["甲", "乙", "  ", "丁"] }, base, "choice").ok === false);
  check("选项重复 → 拒（去装饰标点后判重）",
    reports.validatePatch({ options: ["甲", "甲。", "丙", "丁"] }, base, "choice").ok === false);
  check("选项里数学符号不同就不算重复（1 与 −1）",
    reports.validatePatch({ options: ["1", "-1", "2", "-2"], answer: 0 }, base, "choice").ok === true);

  check("answer 写字母 A → 转成下标 0",
    (() => { const r = reports.validatePatch({ answer: "A" }, base, "choice"); return r.ok && r.content.answer === 0; })());
  check("answer 写下标字符串 '2' → 2",
    (() => { const r = reports.validatePatch({ answer: "2" }, base, "choice"); return r.ok && r.content.answer === 2; })());
  check("answer 写选项原文 → 定位到下标",
    (() => { const r = reports.validatePatch({ answer: "丙" }, base, "choice"); return r.ok && r.content.answer === 2; })());
  check("★ answer 越界 → 拒（不能留一张永远判错的卡）",
    reports.validatePatch({ answer: 7 }, base, "choice").ok === false);
  check("answer 对不上任何选项 → 拒",
    reports.validatePatch({ answer: "戊" }, base, "choice").ok === false);

  check("★ 只改选项没给 answer → 通过但带提醒（位置可能已经对不上）",
    (() => { const r = reports.validatePatch({ options: ["甲", "乙", "丙", "丁"] }, base, "choice");
             return r.ok && r.warnings.length === 1; })());

  check("不认识的字段 → 拒（白名单）",
    reports.validatePatch({ difficulty: 0.9 }, base, "choice").ok === false);
  check("空 patch → 拒", reports.validatePatch({}, base, "choice").ok === false);
  check("stem 改成空 → 拒", reports.validatePatch({ stem: "   " }, base, "choice").ok === false);
  check("填空题不能改 options",
    reports.validatePatch({ options: ["甲", "乙", "丙", "丁"] }, { stem: "x=", answer: "1" }, "fill").ok === false);
  check("填空题 answer 走文本",
    (() => { const r = reports.validatePatch({ answer: "$C_1e^{3x}$" }, { stem: "x=", answer: "1" }, "fill");
             return r.ok && r.content.answer === "$C_1e^{3x}$"; })());
  check("判断题 answer 写 true → 正确",
    (() => { const r = reports.validatePatch({ answer: true }, { stem: "对么", answer: "错误" }, "judge");
             return r.ok && r.content.answer === "正确"; })());
  check("判断题 answer 写『也许』 → 拒",
    reports.validatePatch({ answer: "也许" }, { stem: "对么", answer: "错误" }, "judge").ok === false);
  check("只改解析也认", reports.validatePatch({ explanation: "补一句" }, base, "choice").ok === true);
}

// ---------------------------------------------------------------------------
console.log("\n[3] 报卡 / 列卡（跑在真题库副本上）");
{
  const before = fs.statSync(REAL_DB).size;
  if (!fs.existsSync(REAL_DB)) {
    check("题库存在（跳过这一段）", false, REAL_DB);
  } else {
    const tmp = path.join(os.tmpdir(), "kaoyan_card_reports_" + Date.now() + ".db");
    fs.copyFileSync(REAL_DB, tmp);
    try {
      const db = new DatabaseSync(tmp);
      // 建表这一步照抄 migrate.js 的 DDL（用例不依赖「用户已经跑过迁移」）
      db.exec(`CREATE TABLE IF NOT EXISTS card_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT NOT NULL, question_id TEXT,
        kind TEXT NOT NULL DEFAULT 'other', note TEXT, chosen TEXT, correct TEXT, snapshot TEXT,
        status TEXT NOT NULL DEFAULT 'open', source TEXT NOT NULL DEFAULT 'user', fix_note TEXT,
        fixed_at TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')), updated_at TEXT)`);
      db.exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_card_reports_open ON card_reports(card_id) WHERE status = 'open'");
      // 副本可能带着**真库的报卡历史**（用户标记过的、agent 复核过的都在里面），
      // 而这一段的断言是按「绝对条数」写的 → 先把副本上的历史清掉再开跑。
      // （也是为什么这一段必须跑副本：真库压根不该被用例碰。）
      db.exec("DELETE FROM card_reports");

      // 挑一张真实的选择题卡（有选项、有答案）
      const cards = db.prepare(
        "SELECT c.id, q.id AS qid, q.content FROM cards c JOIN questions q ON q.id = c.question_id "
        + "WHERE q.type = 'choice' ORDER BY c.id LIMIT 3"
      ).all();
      check("副本里有可用的选择题（前置）", cards.length >= 1, String(cards.length));
      const card = cards[0];
      const baseContent = JSON.parse(card.content);

      check("不存在的卡号 → 报卡失败", reports.addReport(db, { card_id: "C-不存在" }).ok === false);

      const r1 = reports.addReport(db, { card_id: card.id, kind: "multi_correct", note: "A、C 都对",
        chosen: "B. 乙", correct: "A. 甲" });
      check("报卡成功且是新的一条", r1.ok === true && r1.created === true, JSON.stringify(r1));
      check("待修数 = 1", reports.countOpen(db) === 1, String(r1.count));

      const r2 = reports.addReport(db, { card_id: card.id, kind: "answer_wrong", note: "改成答案有误" });
      check("★ 同一张卡再报一次：还是同一条（改内容，不插新行）",
        r2.ok && r2.created === false && r2.id === r1.id, JSON.stringify(r2));
      check("★ 库里仍然只有 1 条待修（部分唯一索引兜底）", reports.countOpen(db) === 1);
      check("原因被更新成后来的那个", (() => {
        const r = reports.getReport(db, r1.id); return r && r.kind === "answer_wrong" && r.note === "改成答案有误";
      })());
      check("★ 重复标记没把「他当时选的 / 当时的标答」抹掉（复核时要看的就是它）", (() => {
        const r = reports.getReport(db, r1.id);
        return r.chosen === "B. 乙" && r.correct === "A. 甲";
      })());
      const list = reports.listReports(db, { status: "open" });
      check("列卡带上题目内容（agent 不用再查一次库）",
        list.length === 1 && list[0].content && String(list[0].content.stem || "").length > 0);
      check("★ 带上中文原因标签（人看的清单要能读懂）", list[0].kind_label === "答案有误");
      check("带上考点/科目/到期", list[0].due_date !== undefined && list[0].topic_id !== undefined);
      check("报卡时存了快照（改题前后可比）",
        list[0].snapshot && list[0].snapshot.stem === baseContent.stem);
      check("题目没动过 → stale 为假", list[0].stale === false);

      // 有人在别处改了题（模拟：AI 解析/批改脚本）→ 标记应能看出「报卡后题目变了」
      const bumped = Object.assign({}, baseContent, { explanation: "（后来补的解析）" });
      db.prepare("UPDATE questions SET content = ? WHERE id = ?").run(JSON.stringify(bumped), card.qid);
      check("★ 报卡后题目被改过 → stale 为真（复核前先看它，别重复修）",
        reports.listReports(db, { status: "open" })[0].stale === true);
      reports.addReport(db, { card_id: card.id, kind: "unclear", note: "再标一次" });
      check("★ 再标一次不刷新快照（stale 仍是「相对第一次报卡」的意思）",
        reports.listReports(db, { status: "open" })[0].stale === true);

      check("按科目筛得动（科目名取错就筛不出来）",
        reports.listReports(db, { status: "open", subject: "不存在的科目" }).length === 0);
      check("status=all 也能列", reports.listReports(db, { status: "all" }).length === 1);
      check("brief 一行简报可读", reports.brief(list[0]).indexOf("答案有误") > 0);

      // ---- 复核 ----
      const bad = reports.resolveReport(db, r1.id, { action: "fixed", patch: { options: ["只有一条"] } });
      check("改坏了 → 拒（状态不变）", bad.ok === false && reports.countOpen(db) === 1, JSON.stringify(bad));

      const dry = reports.resolveReport(db, r1.id, {
        action: "fixed", note: "把 C 改成真干扰项", dry_run: true,
        patch: { options: ["甲", "乙", "丙", "丁"], answer: 0 },
      });
      check("dry_run 只算不写", dry.ok === true && dry.dry_run === true && reports.countOpen(db) === 1);
      check("dry_run 也回改动清单", Array.isArray(dry.changes) && dry.changes.indexOf("options") >= 0);

      const okFix = reports.resolveReport(db, r1.id, {
        action: "fixed", note: "把 C 换成真干扰项", patch: { options: ["甲", "乙", "丙", "丁"], answer: 0 },
      });
      check("复核 fixed 成功", okFix.ok === true && okFix.status === "fixed", JSON.stringify(okFix));
      check("★ 题目内容真的被改写了",
        JSON.parse(db.prepare("SELECT content FROM questions WHERE id = ?").get(card.qid).content).options[0] === "甲");
      check("★ 待修清零（前端徽标随之消失）", reports.countOpen(db) === 0);
      check("复核结论落库（以后能回看改了什么）", (() => {
        const r = db.prepare("SELECT fix_note, fixed_at, status FROM card_reports WHERE id = ?").get(r1.id);
        return r.status === "fixed" && r.fix_note === "把 C 换成真干扰项" && !!r.fixed_at;
      })());
      check("★ 同一张卡还能再被标记（历史行不挡新标记）", (() => {
        const r3 = reports.addReport(db, { card_id: card.id, kind: "unclear", note: "还是看不懂" });
        return r3.ok && r3.created === true && reports.countOpen(db) === 1;
      })());

      const again = reports.resolveReport(db, r1.id, { action: "fixed", note: "再修一次" });
      check("已处理的标记再复核 → 拒（不会改两遍）", again.ok === false);

      const open2 = reports.listReports(db, { status: "open" })[0];
      check("驳回不写理由 → 拒", reports.resolveReport(db, open2.id, { action: "dismissed" }).ok === false);
      check("fixed 既没 patch 也没 note → 拒",
        reports.resolveReport(db, open2.id, { action: "fixed" }).ok === false);
      check("驳回想写理由 → 过", reports.resolveReport(db, open2.id, { action: "dismissed", note: "我记错了，题没毛病" }).ok === true);
      check("驳回后不再出现在待修清单", reports.countOpen(db) === 0);

      // 删卡这条路：与🗑删卡同一个动作（软删）
      const c2 = cards[1] || cards[0];
      const r4 = reports.addReport(db, { card_id: c2.id, kind: "dup", note: "和另一张重复" });
      check("报了第二张卡", r4.ok === true && reports.countOpen(db) === 1);
      const del = reports.resolveReport(db, r4.id, { action: "deleted", note: "与 C-XXX 重复，保留那张" });
      check("复核 deleted 成功", del.ok === true && del.status === "deleted");
      check("★ 卡被软删（suspended=1，可恢复，不是物理删）",
        db.prepare("SELECT suspended FROM cards WHERE id = ?").get(c2.id).suspended === 1);
      check("删卡后待修清零", reports.countOpen(db) === 0);

      // 已删的卡又被标记 → 放回来（用户的意思是「这题要处理」，不是「要它消失」）
      const r5 = reports.addReport(db, { card_id: c2.id, kind: "stem_wrong", note: "题干错了" });
      check("★ 已暂停的卡被重新标记 → 自动放回队列",
        r5.ok === true && db.prepare("SELECT suspended FROM cards WHERE id = ?").get(c2.id).suspended === 0);

      // session 端点那条 SQL 的取法：LEFT JOIN 出待修标记（前端画徽标就靠它）
      const shown = db.prepare(
        "SELECT c.id AS card_id, r.id AS rid, r.kind FROM cards c "
        + "LEFT JOIN card_reports r ON r.card_id = c.id AND r.status = 'open' WHERE c.id = ?"
      ).get(c2.id);
      check("按 session 的取法能带出 open 标记（徽标数据）", !!shown && !!shown.rid && shown.kind === "stem_wrong");

      db.close();

      // ---- CLI（每日任务里 agent 走的就是这条路，不用开网页）----
      const cli = (args) => execFileSync(process.execPath, [path.join(SRC, "card_reports.js")].concat(args), {
        encoding: "utf-8", env: Object.assign({}, process.env, { DB_PATH: tmp }), timeout: 20000,
      });
      const out = cli(["list"]);
      check("★ CLI list 能列出待修（每日任务的入口）", out.indexOf("题干有误") >= 0, out.slice(0, 200));
      check("CLI count 给出条数", cli(["count"]).trim() === "1");
      const j = JSON.parse(cli(["list", "--json"]));
      const cid = j[0].id;
      const fixOut = JSON.parse(cli(["fix", String(cid), "--note", "题干里的下标改对了",
        "--patch", JSON.stringify({ stem: "（改过的题干）" })]));
      check("★ CLI fix 能改题", fixOut.ok === true && fixOut.changes.indexOf("stem") >= 0, JSON.stringify(fixOut));
      check("CLI fix 后待修清零", cli(["count"]).trim() === "0");
      const after = JSON.parse(cli(["list", "--status", "all", "--json"]));
      check("CLI 能给历史清单（含 fixed）",
        after.filter(r => r.status === "fixed" && r.fix_note === "题干里的下标改对了").length >= 1);
      let cliFail = "";
      try { cli(["fix", "999999", "--note", "x"]); } catch (e) { cliFail = String(e.status); }
      check("CLI 对不存在的 id 退出码非 0", cliFail !== "", cliFail);

      // CLI report：agent 自己发现的问题也走同一条链路（标记不只网页一个入口）
      const made = JSON.parse(cli(["report", c2.id, "--kind", "answer_wrong",
        "--note", "命令行报的", "--chosen", "C. 丙", "--correct", "A. 甲"]));
      check("★ CLI report 能报卡并回待修数", made.ok === true && made.created === true && made.count === 1,
        JSON.stringify(made));
      const shownCli = JSON.parse(cli(["show", String(made.id), "--json"]));
      check("CLI show 能摊开一条标记（含来源 source=agent）",
        shownCli.id === made.id && shownCli.note === "命令行报的" && shownCli.source === "agent",
        JSON.stringify({ id: shownCli.id, note: shownCli.note, source: shownCli.source }));
      check("CLI show 带上了他选的与当时的标答",
        shownCli.chosen === "C. 丙" && shownCli.correct === "A. 甲", JSON.stringify(shownCli.chosen));

      check("★ 全程没碰真实题库（文件大小不变）", fs.statSync(REAL_DB).size === before);
    } catch (e) {
      check("这一段不抛异常", false, e.message + "\n" + (e.stack || ""));
    } finally {
      try { fs.unlinkSync(tmp); } catch (e) {}
    }
  }
}

console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
process.exit(fail === 0 ? 0 : 1);

/*
 * 薄弱点学习「AI 手写题 → 题库卡片」的用例（2026-09-22）
 *
 * 覆盖两段最容易回归、又看不见失败的东西：
 *   ① 抠 ```cards 块 + 规范化 —— 模型给的格式千奇百怪，错了就是「选了正确答案却判错」；
 *   ② 入库与去重 —— 同一道题不该被插第二遍（用户报过「同一题一天问两遍」）。
 *
 * 入库那一段跑在**真题库的副本**上：不碰 src/question_bank.db，
 * 免得用例把几百张真实卡片搅乱。
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { DatabaseSync } = require("node:sqlite");

const SRC = path.join(__dirname, "..");
const studyCards = require(path.join(SRC, "study_cards.js"));
const REAL_DB = path.join(SRC, "question_bank.db");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

// ---------------------------------------------------------------------------
console.log("\n[1] 从回复里抠 JSON 数组（顶层是数组，不是对象）");
{
  const a = studyCards.parseCardsArray('[{"stem":"甲"}]');
  check("纯数组", Array.isArray(a) && a.length === 1);

  const b = studyCards.parseCardsArray('先讲两句。\n```cards\n[{"stem":"甲"},{"stem":"乙"}]\n```\n后面还有话。');
  check("裹在代码块里、前后都有正文", Array.isArray(b) && b.length === 2);

  // ⚠️ grade_llm.extractJson 找的是第一个 `{`，会只取回第一道题——这条就是守它别被换回去
  const c = studyCards.parseCardsArray('[{"stem":"甲"},{"stem":"乙"},{"stem":"丙"}]');
  check("★ 多道题一个都不少（3 道）", Array.isArray(c) && c.length === 3, JSON.stringify(c));

  const d = studyCards.parseCardsArray('[{"stem":"含 ] 和中括号 [ 的字符串"}]');
  check("字符串里的中括号不当成结束", Array.isArray(d) && d[0].stem.indexOf("]") >= 0, JSON.stringify(d));

  check("没有方括号 → null", studyCards.parseCardsArray("没有任何 JSON") === null);
  check("对象而不是数组 → null", studyCards.parseCardsArray('{"stem":"甲"}') === null);
  check("被截断的数组 → null", studyCards.parseCardsArray('[{"stem":"甲"},{"stem"') === null);
}

// ---------------------------------------------------------------------------
console.log("\n[2] 抠掉 cards 块，正文要留着");
{
  const raw = "题在下面：\n\n题 1 设 y1,y2 是解，则通解是\nA. …\n\n```cards\n"
    + '[{"type":"choice","stem":"设 y1,y2 是解","options":["甲","乙","丙","丁"],"answer":"C","explanation":"因为丙"}]\n'
    + "```\n";
  const r = studyCards.splitCards(raw);
  check("正文保留了题目（前面的字还在）", r.text.indexOf("题在下面") >= 0 && r.text.indexOf("A. …") >= 0, r.text);
  check("正文里不再有 ```cards 那段 JSON", r.text.indexOf("```cards") < 0 && r.text.indexOf('"stem"') < 0, r.text);
  check("卡片被解析出来", r.cards.length === 1, JSON.stringify(r.cards));
  check("answer 的字母 C 转成了下标 2", r.cards[0] && r.cards[0].answer === 2, JSON.stringify(r.cards[0]));

  const noBlock = studyCards.splitCards("只讲概念，没出题。");
  check("没有块时正文原样", noBlock.text === "只讲概念，没出题。" && noBlock.cards.length === 0);

  const broken = studyCards.splitCards("题目：\n```cards\n[{坏掉的 JSON\n```\n");
  check("★ 块里不是合法 JSON 时，正文一个字都不删（宁可难看也别丢内容）",
    broken.text.indexOf("坏掉的 JSON") >= 0 && broken.cards.length === 0, broken.text);

  const upper = studyCards.splitCards('正文\n```CARDS\n[{"stem":"甲","options":["a","b","c","d"],"answer":1}]\n```');
  check("块名大小写不敏感", upper.cards.length === 1 && upper.text === "正文", upper.text);
}

// ---------------------------------------------------------------------------
console.log("\n[3] 规范化：模型给的格式要收敛成题库认的形状");
{
  const choice = studyCards.normalizeCards([{
    type: "single", stem: "题干", options: ["A. 甲", "B、乙", "C）丙", "D．丁"], answer: "B",
  }]);
  check("选项的 A./B、前缀被剥掉（不再双份字母）",
    choice[0] && choice[0].options[0] === "甲" && choice[0].options[1] === "乙"
    && choice[0].options[2] === "丙" && choice[0].options[3] === "丁",
    JSON.stringify(choice[0] && choice[0].options));
  check("type=single 归成 choice", choice[0] && choice[0].type === "choice");
  check("answer 的字母 B 转成下标 1", choice[0] && choice[0].answer === 1);

  const byText = studyCards.normalizeCards([{
    stem: "题干", options: ["甲", "乙", "丙", "丁"], answer: "丙",
  }]);
  check("answer 写选项原文也认（丙 → 下标 2）", byText[0] && byText[0].answer === 2);

  const bad = studyCards.normalizeCards([
    { stem: "只有三个选项", options: ["甲", "乙", "丙"], answer: 0 },
    { stem: "答案越界", options: ["甲", "乙", "丙", "丁"], answer: 7 },
    { stem: "", options: ["甲", "乙", "丙", "丁"], answer: 0 },
    { stem: "没答案", options: ["甲", "乙", "丙", "丁"] },
  ]);
  check("★ 选项不足 4 条 / 答案越界 / 题干空 / 没答案 —— 一律丢掉（宁少不错）",
    bad.length === 0, JSON.stringify(bad));

  const judge = studyCards.normalizeCards([
    { type: "judge", stem: "对的那道", answer: true },
    { type: "truefalse", stem: "错的那道", answer: "错" },
    { type: "judge", stem: "认不出的", answer: "也许吧" },
  ]);
  check("判断题 true → 正确", judge[0] && judge[0].answer === "正确");
  check("判断题 错 → 错误（type=truefalse 也认）", judge[1] && judge[1].answer === "错误"
    && judge[1].type === "judge");
  check("判不出正误的判断题丢掉", judge.length === 2, JSON.stringify(judge));

  const fill = studyCards.normalizeCards([{ type: "fill", stem: "TCP 首部最小 ____ 字节", answer: "20" }]);
  check("填空题答案保留", fill[0] && fill[0].type === "fill" && fill[0].answer === "20");

  const many = studyCards.normalizeCards(Array.from({ length: 20 }, (_, i) => ({
    stem: "题" + i, options: ["甲", "乙", "丙", "丁"], answer: 0,
  })));
  check("一次最多 8 张（上限 " + studyCards.MAX_CARDS + "）", many.length === 8, String(many.length));

  const traps = studyCards.normalizeCards([{
    stem: "题干", options: ["甲", "乙", "丙", "丁"], answer: 0,
    traps: ["易错一", "", null, "易错二", "易错三", "易错四", "易错五"],
  }]);
  check("traps 去空值并限 4 条", traps[0] && traps[0].traps.length === 4, JSON.stringify(traps[0] && traps[0].traps));
}

// ---------------------------------------------------------------------------
console.log("\n[4] 入库与去重（跑在真题库的副本上，不动 src/question_bank.db）");
{
  if (!fs.existsSync(REAL_DB)) {
    check("题库存在（跳过入库用例）", false, REAL_DB);
  } else {
    const tmp = path.join(os.tmpdir(), "kaoyan_study_cards_test_" + Date.now() + ".db");
    fs.copyFileSync(REAL_DB, tmp);
    const before = fs.statSync(REAL_DB).size;
    try {
      const db = new DatabaseSync(tmp);
      const nCards = () => db.prepare("SELECT COUNT(*) AS n FROM cards").get().n;
      const nQ = () => db.prepare("SELECT COUNT(*) AS n FROM questions").get().n;

      const cards = studyCards.normalizeCards([{
        type: "choice",
        stem: "【用例】设 y1,y2 是 y''+p(x)y'+q(x)y=f(x) 的解，则通解是",
        options: ["甲", "乙", "丙", "丁"], answer: 2,
        explanation: "因为丙", traps: ["忘掉特解"], topic: "常微分方程",
      }]);
      check("用例卡通过规范化", cards.length === 1);

      const c0 = nCards(), q0 = nQ();
      const r1 = studyCards.insertCards(db, cards, {
        subject: "数学一", today: "2026-09-22",
        findTopic: () => "MATH-GS-01",   // 用例里不查真图谱，直接给一个存在的考点
      });
      check("首次入库：新增 1 张", r1.inserted === 1 && r1.reused === 0, JSON.stringify(r1));
      check("返回了卡号（前端靠它精确组题）",
        r1.card_ids.length === 1 && /^C-STUDY-[0-9A-F]{8}$/.test(r1.card_ids[0]), JSON.stringify(r1.card_ids));
      check("cards 表 +1", nCards() === c0 + 1, nCards() + " vs " + (c0 + 1));
      check("questions 表 +1", nQ() === q0 + 1);
      check("卡是新卡（state=0）",
        db.prepare("SELECT state, due_date, question_id FROM cards WHERE id = ?").get(r1.card_ids[0]).state === 0);
      check("题干与解析按题库的 JSON 形状落库", (() => {
        const row = db.prepare("SELECT q.type, q.content, q.source FROM questions q JOIN cards c ON c.question_id = q.id "
          + "WHERE c.id = ?").get(r1.card_ids[0]);
        if (!row) return false;
        const ct = JSON.parse(row.content);
        return row.type === "choice" && ct.answer === 2 && ct.options.length === 4
          && ct.explanation === "因为丙" && /^AI手写-/.test(row.source);
      })());
      check("卡片挂到了考点上（能进统计口径）",
        (db.prepare("SELECT q.topic_id AS t FROM questions q JOIN cards c ON c.question_id = q.id WHERE c.id = ?")
          .get(r1.card_ids[0]) || {}).t === "MATH-GS-01");

      // 再问一遍同一个知识点：必须复用，不能再插一张
      const r2 = studyCards.insertCards(db, cards, { subject: "数学一", today: "2026-09-22", findTopic: () => "" });
      check("★ 同题干第二次：复用已有卡（不再插一张，避免一天问两遍）",
        r2.inserted === 0 && r2.reused === 1, JSON.stringify(r2));
      check("复用给的是同一个卡号", r2.card_ids[0] === r1.card_ids[0]);
      check("cards 表没有被加第二张", nCards() === c0 + 1);

      // 展示层那套渲染依赖的字段：session 端点是 cards JOIN questions JOIN topics
      const shown = db.prepare(
        "SELECT c.id AS card_id, q.type, q.content, t.subject FROM cards c "
        + "JOIN questions q ON c.question_id = q.id LEFT JOIN topics t ON q.topic_id = t.id WHERE c.id = ?"
      ).get(r1.card_ids[0]);
      check("按闪卡 session 的取法能取回这张卡（含 content）",
        !!shown && JSON.parse(shown.content).stem.indexOf("通解") > 0, JSON.stringify(shown));

      db.close();
    } catch (e) {
      check("入库用例不抛异常", false, e.message);
    } finally {
      try { fs.unlinkSync(tmp); } catch (e) {}
    }
    check("★ 全程没碰真实题库（文件大小不变）", fs.statSync(REAL_DB).size === before);
  }
}

console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
process.exit(fail === 0 ? 0 : 1);

/*
 * 闪卡「继续本组」数字一致性测试（2026-09-17）。
 *
 * 用户报的现象：闸门按钮写「继续本组（还剩 20 张，从第 7 张起）」，点下去却从
 * 别的地方开始 —— 他明明按的是第 7 张。查下来是两件事叠在一起：
 *
 *  ① GET /api/flashcards/session?peek=1   —— 只报数字（闸门按钮上那句）
 *     GET /api/flashcards/session?resume=1 —— 报数字 + 把卡片还回来
 *     resume 一直会「剔掉今天已经答过的卡」（同一题一天问好几遍的堵口），peek 不会。
 *     于是 peek 报的是**没剔过**的原始列表，按钮上的位置和点下去拿到的对不上。
 *
 *  ② 就算两边算法一致，那个位置也仍然会虚高：idx 之前还压着「更早答过、不可能再
 *     展示」的卡（比如当天凌晨 4 点前答的，按学习日算昨天，不在今天要剔的名单里）。
 *     库里 18 张、idx=5、今天答过 3 张，剔完显示「从第 3 张起」—— 前两张就是这种
 *     残留，人读成「要跳过 2 张没答的」。
 *
 * 所以现在 remainingCards() 一次做两件事：剔今天已答的 + 去掉当前位置之前的，
 * 返回的 idx 恒为 0（剩下的就是「从头接着刷」，按钮不必再报位置）。
 *
 * 这个文件守两件事：
 *   ① 算法对（尤其是「挪完还停在原来那张卡上」——不然会跳过没答的题）
 *   ② **peek 与 resume 调的是同一个函数** —— 各写一份正是这次 bug 的来源，
 *      所以用源码级断言钉住，别让以后谁顺手在 peek 里再手搓一遍
 */
const fs = require("fs");
const path = require("path");

const SRC = path.join(__dirname, "..", "serve.js");
const src = fs.readFileSync(SRC, "utf-8");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra ? "  → " + extra : "")); }
}

// 按括号配平从源码里抠出「const NAME = …;」（serve.js 一 require 就起 HTTP 服务，
// 不能直接引它）。同 tools/test_net_guard.js 的做法。
function grab(name) {
  const idx = src.indexOf("const " + name + " = ");
  if (idx < 0) return null;
  let depth = 0, i = src.indexOf("=", idx);
  for (; i < src.length; i++) {
    const ch = src[i];
    if (ch === "(" || ch === "[" || ch === "{") depth++;
    else if (ch === ")" || ch === "]" || ch === "}") depth--;
    else if (ch === ";" && depth === 0) return src.slice(idx, i + 1);
  }
  return null;
}

const body = grab("remainingCards");
if (!body) { console.log("没能从 serve.js 抠出 remainingCards"); process.exit(1); }
const sandbox = {};
new Function("exports", body + "\nexports.f = remainingCards;")(sandbox);
const remaining = sandbox.f;

const card = id => ({ card_id: id });
const ids = r => r.cards.map(c => c.card_id);
const S = (...xs) => new Set(xs);

(async () => {

  // ---------- 1. 返回的就是「接着要刷的那些」 ----------
  console.log("\n[1] 剩下的卡：从头接着刷，第一张必须是你原来停的那张");
  {
    // 组里 5 张，停在 d（idx=3）；a、b 是今天答过的
    const r = remaining([card("a"), card("b"), card("c"), card("d"), card("e")], 3, S("a", "b"));
    check("★ 第一张就是原来停的那张（d）", ids(r)[0] === "d", ids(r).join(","));
    check("★ idx 恒为 0（剩下的就是从第 1 张起）", r.idx === 0, String(r.idx));
    check("★ 前面那些不会再展示的卡都去掉了", ids(r).join(",") === "d,e", ids(r).join(","));
  }
  {
    // idx=0：还没开始，什么都不该被去掉（除了今天已答的）
    const r = remaining([card("a"), card("b"), card("c")], 0, S());
    check("没开始过就原样返回", ids(r).join(",") === "a,b,c" && r.idx === 0);
  }
  {
    // 关键回归：c 在 idx 之前、但它是「更早答过」的残留（不在今天名单里）。
    // 以前这段会被留下，导致位置虚高；现在必须一起去掉。
    const r = remaining([card("a"), card("b"), card("c"), card("d")], 3, S("a"));
    check("★ idx 之前的残留（c）也去掉", ids(r).join(",") === "d", ids(r).join(","));
    check("第一张仍是原来停的那张", r.cards[0].card_id === "d");
  }
  {
    // 答过的卡在 idx 之后：也要剔，但不影响起点
    const r = remaining([card("a"), card("b"), card("c"), card("d")], 1, S("c", "d"));
    check("★ idx 之后今天答过的同样剔掉", ids(r).join(",") === "b", ids(r).join(","));
    check("起点不受影响（b 还是第一张）", r.cards[0].card_id === "b");
  }
  {
    const r = remaining([card("a"), card("b")], 1, S("a", "b"));
    check("★ 全被剔光：空数组 + idx 为 0（不能让下标为负）",
      r.cards.length === 0 && r.idx === 0, JSON.stringify(r));
  }
  {
    const r = remaining([card("a"), card("b")], 9, S());
    check("idx 超过总数（脏数据）夹回长度，结果为空", r.cards.length === 0 && r.idx === 0,
      JSON.stringify(r));
  }
  {
    // 没有 card_id 的条目（AI 手写题归卡失败之类）不能因为「查不到就当成答过」被误剔。
    // ⚠️ 这里 idx=0：位置之前的卡会被裁掉，用 idx=0 才能单独看出「剔卡」这一步的结果。
    const r = remaining([{ card_id: "" }, card("a"), { card_id: null }, card("b")], 0, S("b"));
    check("★ 没有 card_id 的条目保留（不能误剔）", r.cards.length === 3, String(r.cards.length));
    check("今天答过的那张照样剔掉", ids(r).indexOf("b") < 0, ids(r).join(","));
  }
  {
    // 幂等：拿结果再算一次不该再变（闸门会反复 peek）
    const once = remaining([card("a"), card("b"), card("c")], 2, S("a"));
    const twice = remaining(once.cards, once.idx, S("a"));
    check("★ 幂等：同样的输入再算一次结果不变",
      ids(twice).join(",") === ids(once).join(",") && twice.idx === 0, ids(twice).join(","));
  }

  // ---------- 2. peek 与 resume 必须调同一个函数（这次 bug 的真正来源）----------
  console.log("\n[2] peek 与 resume 必须走同一个函数");
  {
    const calls = (src.match(/remainingCards\(/g) || []).length;
    // 1 处在定义里，所以调用点应当只有 1 处
    check("★ remainingCards 只有一处调用（不是各写一份）", calls === 1, "出现 " + calls + " 次");
    check("★ 调用点在 peek/resume 分支里，且 peek 也走它",
      /remainingCards\(\s*sess\.cards,\s*sess\.idx,\s*answeredToday\(rdb\)\s*\)/.test(src));
    check("★ 没有第二份手搓的剔卡（done.has(cid) 只出现一次）",
      (src.match(/done\.has\(cid\)/g) || []).length === 1,
      String((src.match(/done\.has\(cid\)/g) || []).length));
    check("★ 那一份就在 remainingCards 里",
      src.indexOf("done.has(cid)") > src.indexOf("const remainingCards"));
    check("剔卡发生在 wantCards 分支之外（否则 peek 又漏掉了）",
      src.indexOf("const left = remainingCards(") <
      src.indexOf("if (wantCards) {", src.indexOf("const left = remainingCards(")));
    check("peek 是只读的：算完了才另开写连接",
      src.indexOf("const left = remainingCards(") < src.indexOf("const wdb = new DatabaseSync"));
  }

  console.log("\n" + (fail === 0 ? "全部通过" : "有失败") + "：pass=" + pass + " fail=" + fail);
  process.exit(fail === 0 ? 0 : 1);
})();

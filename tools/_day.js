/*
 * 用例里的「今天」必须跟页面同一套规则：凌晨 4 点前算前一天。
 *
 * 为什么不能让用例自己算日历日：2026-09-17 凌晨 3 点加学习日起点时踩到了——
 * 桩里回的是 09-17，而页面的 todayStr()/todayKey() 已经是 09-16，
 * 于是三件事一起坏：番茄钟的 mergeToday 判定「服务端那份属于新的一天」把成绩
 * 覆盖成 0、闪卡闸门认不出没刷完的本组（直接 TypeError）、会话数据条少一天。
 * 这类失败只在 0:00~4:00 之间出现，白天跑测试根本发现不了。
 *
 * 常量和规则都不在这里写第二遍：从抽出来的那段页面 JS 里读 DAY_START_HOUR。
 */
function dayStartHour(js) {
  const m = /const DAY_START_HOUR = (\d+);/.exec(js);
  if (!m) {
    throw new Error("抽出来的 JS 里没有 DAY_START_HOUR —— "
      + "tools/extract_js.py 的 PRELUDE 是不是漏掉了 DAY_START_JS？");
  }
  return Number(m[1]);
}

/** 页面版的 studyDay() —— 凌晨 DAY_START_HOUR 点前算前一天 */
function dayKey(js, d) {
  const t = d ? new Date(d.getTime()) : new Date();
  if (t.getHours() < dayStartHour(js)) t.setDate(t.getDate() - 1);
  return t.getFullYear() + "-" + String(t.getMonth() + 1).padStart(2, "0")
       + "-" + String(t.getDate()).padStart(2, "0");
}

/** 以学习日为最后一天往前数 n 天（跟 SHELL_JS 的 lastDays 同一套） */
function lastDays(js, n) {
  const p = dayKey(js).split("-");
  const t = new Date(+p[0], +p[1] - 1, +p[2]);
  const out = [];
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(t.getFullYear(), t.getMonth(), t.getDate() - i);
    out.push(d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0")
           + "-" + String(d.getDate()).padStart(2, "0"));
  }
  return out;
}

module.exports = { dayStartHour, dayKey, lastDays };

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
english_coverage.py — 按「英语一题型」判定覆盖，供 gap_analysis.py 与 generate_dashboard.py 共用

为什么单独成模块：这两处原本各算各的缺口。gap_analysis 会看 progress 与文件证据，
而 generate_dashboard 只认「笔记索引里有没有条目」——结果报告说「写作 3/3 已覆盖」，
大盘却仍把小作文/大作文/翻译列进真缺口。同一件事两套口径，必然长期打架。
现在两边都调本模块，判定只有一份。

为什么不再用「外刊篇数」判阅读：外刊精读练的是 Part A 的语感与话题熟悉度，
和 Part B（七选五/小标题/段落排序）几乎是两种能力。原先「外刊 91 篇 ≥ 80」判 Part B
已覆盖，是拿错证据下结论——Part B 实际一份专项材料都没有。

证据优先级（每个题型各找自己的证据，缺就说缺什么）：
  1. 该题型的闪卡答题记录（最客观：练过就是练过）
  2. 真题/批改/讲义文件（按题型关键词识别）
  3. progress.json 的进度数字（词汇、语法这类「贯穿全卷」的能力项）
"""

import os

# 与大盘/题库一致的英语考点前缀
TYPE_KEYS = [
    "ENG-VOC-01", "ENG-VOC-02", "ENG-VOC-03", "ENG-VOC-04",
    "ENG-GRAM-01", "ENG-GRAM-02",
    "ENG-READ-01", "ENG-READ-02", "ENG-READ-03", "ENG-READ-04",
    "ENG-WRITE-01", "ENG-WRITE-02", "ENG-WRITE-03",
    "ENG-TRN-01", "ENG-TRN-02",
]

# Part B 的专项材料长什么样：七选五 / 小标题匹配 / 段落排序 / 信息匹配
PARTB_KEYS = ["七选五", "小标题", "新题型", "part b", "partb", "信息匹配", "段落排序"]
# 「排序」单独用会误命中「单词排序.pdf」这类无关文件，所以只用上面这些完整词组

DOC_EXTS = (".md", ".pdf", ".docx", ".pptx")
# 刻意不含 .url：快捷方式不承载内容，曾经它让 Part B 看起来「有材料」


def _scan(root, keys, exts=DOC_EXTS):
    """递归找文件名含任一关键词的资料文件。keys 传 [""] 等于列全部。"""
    hits = []
    if not os.path.isdir(root):
        return hits
    for dirpath, _dn, fns in os.walk(root):
        for fn in fns:
            if not fn.lower().endswith(exts):
                continue
            low = fn.lower()
            if any(k.lower() in low for k in keys):
                hits.append(fn)
    return hits


def _first_dir(cands, label, warn):
    for p in cands:
        if os.path.isdir(p):
            return p
    warn.append(f"{label}未找到（试过 {[os.path.basename(c) for c in cands]}）——相关考点会被误判为缺口")
    return cands[0]


def english_type_coverage(kaoyan_root, progress_data, review_counts=None):
    """
    返回 (coverage, metrics, warnings)
      coverage  : {topic_id: (covered: bool, reason: str)}
      metrics   : {子科名: 一行证据摘要}，给报告与大盘展示用
      warnings  : 探测异常（目录缺失、少一路证据），宁可吵一句
    """
    warn = []
    cov = {}
    metrics = {}
    review_counts = review_counts or {}

    def rev(tid):
        """该题型的闪卡答题记录 (cards, reviews, ok)。"""
        return review_counts.get(tid, (0, 0, 0))

    def practised(tid, min_reviews=1):
        cards, n, ok = rev(tid)
        return n >= min_reviews

    eng = os.path.join(kaoyan_root, "English")
    read_dir = os.path.join(eng, "reading&magazines")
    papers_dir = os.path.join(eng, "历年真题及答案")
    write_dir = _first_dir([
        os.path.join(eng, "translation&writing"),
        os.path.join(eng, "translation&write"),   # 旧笔误名，兼容一版
    ], "写作/翻译目录", warn)

    e = (progress_data or {}).get("subjects", {}).get("英语", {}) or {}
    vocab_size = e.get("vocabulary_size", 0)
    vocab_target = e.get("vocab_target", 5500)
    phrases = e.get("phrases_learned", 0)

    # 词汇库：辨析组数
    distinctions = 0
    vdb = os.path.join(eng, r"word&phrase\vocab-graph\data\vocab_database.json")
    try:
        import json
        with open(vdb, "r", encoding="utf-8") as f:
            d = json.load(f)
        distinctions = len(d.get("distinctions", [])) if isinstance(d, dict) else 0
    except Exception as exc:
        warn.append(f"词汇库读不到（{exc}），辨析数按 0 计")

    grammar_md = os.path.join(eng, r"grammar\考研英语语法笔记.md")
    grammar_exists = os.path.exists(grammar_md)
    if not grammar_exists:
        warn.append(f"语法笔记不存在：{grammar_md}")

    long_cnt = 0
    long_md = os.path.join(eng, r"grammar\长难句笔记.md")
    try:
        import re
        with open(long_md, "r", encoding="utf-8") as f:
            txt = f.read()
        long_cnt = txt.count("<!-- note-meta:entry")
        if long_cnt == 0:
            long_cnt = len(re.findall(r"^## \d{4}-\d{2}-\d{2}", txt, re.MULTILINE))
    except Exception as exc:
        warn.append(f"长难句笔记读不到（{exc}）")

    # --- 阅读：Part A 与 Part B 分开找证据 ---
    read_topics = _scan(os.path.join(read_dir, "阅读专题"), ["专题_"], (".md",))
    synonym = os.path.exists(os.path.join(read_dir, "同义替换.md"))
    papers_all = _scan(papers_dir, ["阅读真题逐词翻译"])
    papers_y1 = [f for f in papers_all if ("英一" in f or "英语一" in f or "05~09" in f)]
    partb = _scan(eng, PARTB_KEYS)

    # --- 写作：批改记录按题型分开数 ---
    small_essay, big_essay = [], []
    if os.path.isdir(write_dir):
        try:
            for fn in os.listdir(write_dir):
                if "批改记录" not in fn or not fn.endswith(".md"):
                    continue
                if "应用文" in fn or "小作文" in fn:
                    small_essay.append(fn)
                elif any(k in fn for k in ("图画", "大作文", "图表")):
                    big_essay.append(fn)
                else:
                    big_essay.append(fn)   # 未标题型的按大作文计，宁可少判小作文
        except Exception as exc:
            warn.append(f"写作目录读取失败：{exc}")
    write_accum = os.path.exists(os.path.join(write_dir, "积累.md"))

    # --- 翻译 / 完形 ---
    translation_files = _scan(os.path.join(write_dir, "翻译"), [""], (".md",))
    translation_real = [f for f in translation_files if "真题" in f]
    cloze = _scan(eng, ["完形", "cloze"])

    # --- 逐题型判定，每个都带理由 ---
    def decide(tid, ok, reason):
        if not ok and practised(tid):
            ok, reason = True, reason + "+已刷卡"
        cov[tid] = (bool(ok), reason)
        return ok

    # 词汇（贯穿全卷，看进度量）
    decide("ENG-VOC-01", vocab_size >= 4400, f"核心词{vocab_size}/{vocab_target}")
    decide("ENG-VOC-02", distinctions >= 20, f"辨析{distinctions}组")
    decide("ENG-VOC-03", phrases >= 500, f"短语{phrases}")
    decide("ENG-VOC-04", vocab_size >= 4000, f"词汇量{vocab_size}")
    metrics["词汇"] = " ".join(
        f"{t[-2:]}:{cov[t][1]}{'✓' if cov[t][0] else '✗'}" for t in
        ["ENG-VOC-01", "ENG-VOC-02", "ENG-VOC-03", "ENG-VOC-04"])

    # 语法与长难句
    decide("ENG-GRAM-01", grammar_exists, "语法笔记" + ("✓" if grammar_exists else "✗"))
    decide("ENG-GRAM-02", long_cnt >= 10, f"长难句{long_cnt}条")
    metrics["语法与长难句"] = f"语法笔记{'✓' if grammar_exists else '✗'} 长难句{long_cnt}条"

    # 阅读
    decide("ENG-READ-01", len(read_topics) >= 3 or synonym,
           f"阅读专题{len(read_topics)}讲+同义替换{'✓' if synonym else '✗'}")
    decide("ENG-READ-02", len(partb) >= 1, f"PartB专项材料{len(partb)}份")
    decide("ENG-READ-03", len(papers_y1) >= 1, f"英一真题逐词翻译{len(papers_y1)}份")
    decide("ENG-READ-04", len(partb) >= 2, f"新题型材料{len(partb)}份")
    metrics["阅读理解"] = (f"阅读专题{len(read_topics)}讲 同义替换{'✓' if synonym else '✗'} "
                          f"英一真题{len(papers_y1)}份 PartB/新题型材料{len(partb)}份")
    if not cov["ENG-READ-02"][0]:
        warn.append("阅读Part B（七选五/小标题/段落排序）没有任何专项材料——这是真缺口，"
                    "外刊精读不能替代它")

    # 写作
    decide("ENG-WRITE-01", len(small_essay) >= 1, f"小作文批改{len(small_essay)}篇")
    decide("ENG-WRITE-02", len(big_essay) >= 1, f"大作文批改{len(big_essay)}篇")
    decide("ENG-WRITE-03", write_accum, "积累.md" + ("✓" if write_accum else "✗"))
    metrics["写作"] = (f"小作文批改{len(small_essay)}篇 大作文批改{len(big_essay)}篇 "
                      f"积累{'✓' if write_accum else '✗'}")

    # 完形与翻译
    decide("ENG-TRN-01", len(cloze) >= 1, f"完形材料{len(cloze)}份")
    decide("ENG-TRN-02", len(translation_files) >= 1,
           f"翻译练习{len(translation_files)}篇（真题精讲{len(translation_real)}）")
    metrics["翻译与完形"] = (f"完形材料{len(cloze)}份 翻译练习{len(translation_files)}篇"
                            f"（含真题精讲{len(translation_real)}）")

    return cov, metrics, warn


def english_review_counts(db_path):
    """
    各题型的闪卡数与答题记录：{topic_stem: (cards, reviews, ok)}。
    卡挂在 subtopic（ENG-READ-02-01）上，按 ID 前三段归并到题型级。
    读不到返回空字典——少一路证据，但不至于把已练过的判成没练。
    """
    out = {}
    try:
        import sqlite3
        uri = f"file:{str(db_path).replace(os.sep, '/')}?mode=ro"
        c = sqlite3.connect(uri, uri=True)
        c.row_factory = sqlite3.Row
        rows = c.execute("""
            SELECT t.id AS tid,
                   COUNT(DISTINCT ca.id) AS cards,
                   COUNT(rl.id) AS reviews,
                   SUM(CASE WHEN rl.rating >= 3 THEN 1 ELSE 0 END) AS ok
              FROM topics t
              LEFT JOIN questions q ON q.topic_id = t.id
              LEFT JOIN cards ca ON ca.question_id = q.id AND COALESCE(ca.suspended,0) = 0
              LEFT JOIN review_log rl ON rl.question_id = q.id
             WHERE t.subject = '英语一'
             GROUP BY t.id
        """)
        for r in rows:
            stem = "-".join(str(r["tid"]).split("-")[:3])
            agg = out.setdefault(stem, [0, 0, 0])
            agg[0] += r["cards"] or 0
            agg[1] += r["reviews"] or 0
            agg[2] += r["ok"] or 0
        c.close()
    except Exception:
        return {}
    return {k: tuple(v) for k, v in out.items()}


if __name__ == "__main__":
    # 自测：直接打印当前判定，方便核对
    import io
    import json
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ROOT = r"C:\Users\92534\Desktop\考研"
    with open(os.path.join(ROOT, "src", "progress.json"), encoding="utf-8") as f:
        prog = json.load(f)
    rc = english_review_counts(os.path.join(ROOT, "src", "question_bank.db"))
    cov, metrics, warns = english_type_coverage(ROOT, prog, rc)
    for tid in TYPE_KEYS:
        ok, why = cov[tid]
        print(f"  {'✓' if ok else '✗'} {tid:<14} {why}")
    print("\n子科摘要:")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    if warns:
        print("\n告警:")
        for w in warns:
            print(f"  · {w}")

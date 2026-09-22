# -*- coding: utf-8 -*-
"""学科配置层 —— subjects.json 的读写/模板/校验/迁移。

背景（2026-09-19）
================
系统原本写死考研四科：generate_dashboard.py 的 ALL_SUBJECTS/NOTE_PREFIX_MAP、
serve.js 的 NOTE_SUBJECTS/QUOTA_SUBJECTS/TASK_SUBJECTS、gap_analysis.py 与
daily_planner.py 各自的科目映射表。现在把它泛化成「任意学科」：

  * subjects.json 是**单一事实源**，所有脚本都从它派生科目清单与映射。
  * 每门学科带齐各消费方要的键（topics.subject / progress 键 / 图谱键 /
    笔记目录 / 前缀映射 / 参考书 / 章节体系 / 配色）。
  * 内置考研四科模板（参考书 + 章节体系来自现有 progress.json 与图谱），
    新建学科可以从模板起步，也可以空白自建。

文件位置：src/subjects.json（与 question_bank.db 同层）。
"""
import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CONF_PATH = os.path.join(HERE, "subjects.json")
TEMPLATES_PATH = os.path.join(HERE, "subjects_templates.json")
PROGRESS_PATH = os.path.join(HERE, "progress.json")
GRAPH_DIR = os.path.join(HERE, "knowledge_graph")
ROOT_DIR = os.path.dirname(HERE)          # 知识库根（笔记目录在这里面）

CONF_VERSION = 1


# ---------------------------------------------------------------------------
# 内置模板：考研四科。参考书/章节体系对齐 progress.json 与知识图谱的现状。
# ---------------------------------------------------------------------------
def _kaoyan_templates():
    """返回考研四科模板（深拷贝用：调用方不得改这里返回的常量）。"""
    return [
        {
            "id": "408",
            "name": "408 计算机学科专业基础",
            "short": "408",
            "aliases": ["408", "计算机", "408计算机"],
            "progress_key": "408",
            "graph_key": "408",
            "graph_file": "408_graph.json",
            "notes_dir": "408",
            "total_score": 150,
            "max_ratio": 0.35,
            "color": "#5B7C99",
            "enabled": True,
            "template": "kaoyan-408",
            "method": "王道四本书一轮复习 → 强化（DS强化中）",
            "subs": [
                {"key": "DS", "name": "数据结构", "dir": "DS", "prefix": "408-DS",
                 "chapters": ["绪论", "线性表", "栈、队列和数组", "串", "树与二叉树",
                              "图", "查找", "排序"]},
                {"key": "CO", "name": "计算机组成原理", "dir": "CO", "prefix": "408-CO",
                 "chapters": ["计算机系统概述", "数据的表示和运算", "存储系统",
                              "指令系统", "中央处理器", "总线与输入输出系统"]},
                {"key": "OS", "name": "操作系统", "dir": "OS", "prefix": "408-OS",
                 "chapters": ["操作系统概述", "进程与线程", "内存管理", "文件管理",
                              "输入输出管理"]},
                {"key": "CN", "name": "计算机网络", "dir": "CN", "prefix": "408-CN",
                 "chapters": ["计算机网络体系结构", "物理层", "数据链路层",
                              "网络层", "传输层", "应用层"]},
            ],
            "books": [
                {"title": "王道计算机考研复习指导（四本）", "author": "王道论坛",
                 "phase": "基础", "note": "数据结构/计组/操作系统/计网四册"},
                {"title": "王道强化讲义 + 单科书二刷", "author": "王道论坛",
                 "phase": "强化", "note": "当前：数据结构"},
                {"title": "408历年真题（2009-2026）", "author": "", "phase": "真题",
                 "note": ""},
                {"title": "王道模拟卷", "author": "王道论坛", "phase": "模拟", "note": ""},
            ],
            "note_layout": "按子科目录（DS/CO/OS/CN）分章建 md，文件名「第N章_标题.md」，"
                           "由 build_index.py 生成 笔记索引.yaml",
        },
        {
            "id": "数学一",
            "name": "数学一",
            "short": "数学",
            "aliases": ["数学", "数学一", "数一"],
            "progress_key": "数学",
            "graph_key": "数学一",
            "graph_file": "math_graph.json",
            "notes_dir": "Math",
            "total_score": 150,
            "max_ratio": 0.40,
            "color": "#7FA8D9",
            "enabled": True,
            "template": "kaoyan-math",
            "method": "张宇30讲一轮复习 → 强化（线代强化中）",
            "subs": [
                {"key": "高等数学", "name": "高等数学", "dir": "高数",
                 "prefix": "MATH-GS", "display": "高数",
                 # 高数按张宇《高数18讲》拆分（2026-09-22）：讲号 = 图谱 MATH-GS-NN。
                 # 线代/概率仍是教材章名——它们的笔记没有迁移，靠图谱 note_chapter 换算。
                 "chapters": ["函数极限与连续", "数列极限", "一元函数微分学的概念",
                              "一元函数微分学的计算",
                              "一元函数微分学的应用(一)——几何应用",
                              "一元函数微分学的应用(二)——中值定理、微分等式与微分不等式",
                              "一元函数微分学的应用(三)——物理应用",
                              "一元函数积分学的概念与性质", "一元函数积分学的计算",
                              "一元函数积分学的应用(一)——几何应用",
                              "一元函数积分学的应用(二)——积分等式与积分不等式",
                              "一元函数积分学的应用(三)——物理应用",
                              "多元函数微分学", "二重积分", "微分方程", "无穷级数",
                              "多元函数积分学的预备知识", "多元函数积分学"]},
                {"key": "线性代数", "name": "线性代数", "dir": "线代",
                 "prefix": "MATH-XD", "display": "线代",
                 "chapters": ["行列式", "矩阵", "向量", "线性方程组", "特征值与特征向量",
                              "二次型"]},
                {"key": "概率论与数理统计", "name": "概率论与数理统计", "dir": "概率论",
                 "prefix": "MATH-GL", "display": "概率",
                 "chapters": ["随机事件与概率", "一维随机变量", "多维随机变量",
                              "数字特征", "大数定律与中心极限定理", "数理统计"]},
            ],
            "books": [
                {"title": "张宇基础30讲", "author": "张宇", "phase": "基础",
                 "note": "高数18讲 + 线代6讲 + 概率6讲"},
                {"title": "武忠祥17堂课 / 张宇18讲 + 李永乐线代辅导讲义",
                 "author": "武忠祥/张宇/李永乐", "phase": "强化", "note": ""},
                {"title": "李林880题", "author": "李林", "phase": "强化习题", "note": ""},
                {"title": "近15年真题（2010-2024）", "author": "", "phase": "真题", "note": ""},
                {"title": "李林6+4套卷 + 张宇8+4套卷", "author": "李林/张宇",
                 "phase": "模拟", "note": ""},
            ],
            "note_layout": "按子科目录建 md。高数按张宇《高数18讲》拆分，文件名「第N讲_标题.md」"
                           "（讲号 = 图谱 MATH-GS-NN，2026-09-22 迁移）；线代与概率论仍按教材章，"
                           "文件名「第N章_标题.md」，靠图谱的 note_chapter 换算到讲",
        },
        {
            "id": "政治",
            "name": "政治",
            "short": "政治",
            "aliases": ["政治"],
            "progress_key": "政治",
            "graph_key": "政治",
            "graph_file": "politics_graph.json",
            "notes_dir": "Politics",
            "total_score": 100,
            "max_ratio": 0.30,
            "color": "#C89B4A",
            "enabled": True,
            "template": "kaoyan-politics",
            "method": "徐涛强化课 + 优题库基础篇 + 背诵手册",
            "subs": [
                {"key": "马原", "name": "马克思主义基本原理", "dir": "马原",
                 "prefix": "POL-MY", "display": "马原", "chapters": []},
                {"key": "毛中特", "name": "毛泽东思想和中国特色社会主义理论体系概论",
                 "dir": "毛中特", "prefix": "POL-MZ", "display": "毛中特", "chapters": []},
                {"key": "史纲", "name": "中国近现代史纲要", "dir": "史纲",
                 "prefix": "POL-SG", "display": "史纲", "chapters": []},
                {"key": "思修", "name": "思想道德与法治", "dir": "思修",
                 "prefix": "POL-SX", "display": "思修", "chapters": []},
                {"key": "习思想", "name": "习近平新时代中国特色社会主义思想概论",
                 "dir": "习思想", "prefix": "POL-XX", "display": "习思想", "chapters": []},
            ],
            "books": [
                {"title": "徐涛强化课 + 优题库基础篇", "author": "徐涛",
                 "phase": "基础", "note": ""},
                {"title": "背诵手册", "author": "", "phase": "背诵", "note": "已启动"},
                {"title": "肖1000题 / 肖八 / 肖四 + 腿姐技巧班", "author": "肖秀荣/腿姐",
                 "phase": "冲刺", "note": ""},
            ],
            "note_layout": "一科一个 md（马原.md / 史纲.md …），由笔记工作流维护",
        },
        {
            "id": "英语一",
            "name": "英语一",
            "short": "英语",
            "aliases": ["英语", "英语一"],
            "progress_key": "英语",
            "graph_key": "英语一",
            "graph_file": "english_graph.json",
            "notes_dir": "English",
            "total_score": 100,
            "max_ratio": 0.20,
            "color": "#9A8FB8",
            "enabled": True,
            "template": "kaoyan-english",
            "method": "不背单词APP + 红宝书正序版 + 真题阅读",
            "subs": [
                {"key": "词汇", "name": "词汇", "dir": "word&phrase",
                 "prefix": "ENG-VOC", "chapters": []},
                {"key": "语法", "name": "语法与长难句", "dir": "grammar",
                 "prefix": "ENG-GRAM", "chapters": []},
                {"key": "完形", "name": "完形填空", "dir": "past-papers",
                 "prefix": "ENG-CLOZE", "chapters": []},
                {"key": "翻译", "name": "翻译", "dir": "translation&writing",
                 "prefix": "ENG-TRAN", "chapters": []},
                {"key": "阅读", "name": "阅读理解", "dir": "reading&magazines",
                 "prefix": "ENG-READ", "chapters": []},
                {"key": "写作", "name": "写作", "dir": "translation&writing",
                 "prefix": "ENG-WRITE", "chapters": []},
            ],
            "books": [
                {"title": "不背单词APP + 红宝书正序版", "author": "",
                 "phase": "基础", "note": "目标 5500 词"},
                {"title": "真题阅读（近15年）", "author": "", "phase": "强化",
                 "note": "阅读一轮进行中"},
                {"title": "真题 + 作文范文", "author": "", "phase": "冲刺", "note": ""},
            ],
            "note_layout": "按能力模块建目录（word&phrase/grammar/reading&magazines/…），"
                           "写作与翻译共用 translation&writing 目录",
        },
    ]


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------
def _notes_extras():
    """额外笔记目录：不参与学习统计，只进笔记搜索/索引（如复试）。"""
    return [
        {"key": "复试", "label": "复试", "dir": "Re-examination"},
    ]


def load():
    """读 subjects.json；不存在返回 None（调用方据此判断是否需要建库引导）。"""
    try:
        with open(CONF_PATH, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d


def load_subjects():
    """返回启用中的学科列表（无配置时返回空列表）。"""
    d = load()
    if not d or not isinstance(d.get("subjects"), list):
        return []
    return [s for s in d["subjects"] if s.get("enabled", True)]


def save(data):
    """写 subjects.json（带版本与时间戳）。"""
    data.setdefault("version", CONF_VERSION)
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = CONF_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONF_PATH)


# ---------------------------------------------------------------------------
# 迁移：从现有 progress.json / 图谱 生成初始配置（无配置时的兜底）
# ---------------------------------------------------------------------------
def _load_progress():
    try:
        with open(PROGRESS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def migrate_from_existing():
    """用内置模板生成初始 subjects.json；已存在则不动。返回 (data, created)。"""
    if os.path.exists(CONF_PATH):
        return load(), False
    data = {"version": CONF_VERSION, "notes_extra": _notes_extras(),
            "subjects": _kaoyan_templates()}
    save(data)
    return data, True


def export_templates():
    """把内置模板导出为独立 JSON（serve.js / 引导 UI 用，避免跨语言重复维护）。"""
    with open(TEMPLATES_PATH, "w", encoding="utf-8") as f:
        json.dump(_kaoyan_templates(), f, ensure_ascii=False, indent=2)
    return TEMPLATES_PATH


def notes_extras():
    """额外笔记目录清单（serve.js NOTE_SUBJECTS 的 extras 部分）。"""
    return _notes_extras()


# ---------------------------------------------------------------------------
# 供各脚本使用的派生视图
# ---------------------------------------------------------------------------
def all_names():
    """显示名清单（顺序即配置顺序）。"""
    return [s.get("name", s["id"]) for s in load_subjects()]


def short_names():
    """图表/进度用的短名清单（408 / 数学 / 政治 / 英语）。"""
    return [s.get("short", s["id"]) for s in load_subjects()]


def display_map():
    """graph_key → 显示名（generate_dashboard 各科正确率/标题用）。"""
    return {s.get("graph_key", s["id"]): s.get("name", s["id"]) for s in load_subjects()}


def graph_files():
    """graph_key → 图谱文件名。"""
    return {s.get("graph_key", s["id"]): s["graph_file"] for s in load_subjects() if s.get("graph_file")}


def progress_map():
    """graph_key → progress.json 键。"""
    return {s.get("graph_key", s["id"]): s.get("progress_key", s["id"]) for s in load_subjects()}


def total_scores():
    """graph_key → 总分。"""
    return {s.get("graph_key", s["id"]): s.get("total_score", 100) for s in load_subjects()}


def note_subjects():
    """serve.js NOTE_SUBJECTS 用：{key,label,dir} 列表。"""
    return [{"key": s.get("graph_key", s["id"]),
             "label": s.get("short", s["id"]),
             "dir": s["notes_dir"]} for s in load_subjects() if s.get("notes_dir")]


def note_prefix_map():
    """笔记目录 → 闪卡前缀（NOTE_PREFIX_MAP 全量）。"""
    out = {}
    for s in load_subjects():
        for sub in s.get("subs", []):
            if sub.get("dir") and sub.get("prefix"):
                out[f"{s['notes_dir']}/{sub['dir']}"] = sub["prefix"]
    return out


def subject_prefixes():
    """短名 → 前缀列表（SUBJECT_ALL_PREFIXES）。"""
    out = {}
    for s in load_subjects():
        prefixes = [sub["prefix"] for sub in s.get("subs", []) if sub.get("prefix")]
        if prefixes:
            out[s.get("short", s["id"])] = prefixes
    return out


def quota_subjects():
    """topics.subject 原值清单（QUOTA_SUBJECTS）。"""
    return [s.get("graph_key", s["id"]) for s in load_subjects()]


def task_subjects():
    """每日任务科目清单（TASK_SUBJECTS）。"""
    return [s.get("graph_key", s["id"]) for s in load_subjects()]


def graph_headers():
    """graph_key → 标题头（gap_analysis SUBJECT_HEADERS）。"""
    return {s.get("graph_key", s["id"]): f"{s.get('name', s['id'])} (总分 {s.get('total_score', 100)})"
            for s in load_subjects()}


def sub_display():
    """子科 key → 显示名（SUB_DISPLAY 全量，跨科合并）。

    优先取 sub 的 display（缩略名，如「高数」「马原」），否则取 name。
    图谱里 sub 的 key 与 subs 配置的 key 必须一致（408 用 DS/CO/OS/CN，
    数学用中文全名，政治用短 key）。
    """
    out = {}
    for s in load_subjects():
        for sub in s.get("subs", []):
            out[sub["key"]] = sub.get("display") or sub.get("name", sub["key"])
    return out


def plan_display():
    """daily_planner SUBJECT_DISPLAY：graph_key → 短名。"""
    return {s.get("graph_key", s["id"]): s.get("short", s["id"]) for s in load_subjects()}


def notes_subject_map():
    """笔记索引 label → graph_key（NOTES_SUBJECT_MAP）。"""
    out = {}
    for s in load_subjects():
        short = s.get("short", s["id"])
        out[short] = s.get("graph_key", s["id"])
        for a in s.get("aliases", []):
            out.setdefault(a, s.get("graph_key", s["id"]))
    return out


def subject_max_ratio():
    """graph_key → 最大占比（SUBJECT_MAX_RATIO；缺省 0.35）。"""
    return {s.get("graph_key", s["id"]): s.get("max_ratio", 0.35) for s in load_subjects()}


def summary():
    """轻量列表（引导/管理 UI 用）。"""
    out = []
    for s in load_subjects():
        out.append({
            "id": s["id"],
            "name": s.get("name", s["id"]),
            "short": s.get("short", s["id"]),
            "color": s.get("color", "#888888"),
            "enabled": s.get("enabled", True),
            "notes_dir": s.get("notes_dir", ""),
            "books": s.get("books", []),
            "subs": s.get("subs", []),
            "note_layout": s.get("note_layout", ""),
            "method": s.get("method", ""),
            "template": s.get("template", "custom"),
        })
    return out


# ---------------------------------------------------------------------------
# CLI：快速生成/查看
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "gen":
        data, created = migrate_from_existing()
        export_templates()
        print("created" if created else "exists", CONF_PATH)
        print(json.dumps(summary(), ensure_ascii=False, indent=1)[:2000])
    elif len(sys.argv) > 1 and sys.argv[1] == "templates":
        export_templates()
        print("exported", TEMPLATES_PATH)
    else:
        print(json.dumps(summary(), ensure_ascii=False, indent=1))

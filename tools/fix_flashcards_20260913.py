#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
fix_flashcards_20260913.py — 408 闪卡修正 + 按笔记补卡

一、修正事实错误
  1. Q-408-OS-02-0005「信号量是一种整型变量」答案 true→false
     （记录型信号量是结构体，不是整型变量）
  2. Q-408-CN-02-0002「具有自同步能力的是」选项含 RZ，而 RZ 本身自同步 → 换干扰项
  3. Q-408-DS-02-0002 单链表 O(1) 删除，判断题两边都能说通 → 改为无歧义的选择题
  4. Q-408-OS-01-0003 中断/异常导致用户态→核心态，题干限定为"用户态下发生"
  5. Q-TGT-A31CA44C / Q-TGT-45234058 选项重复字母前缀 "A. A. 12"

二、修正 DS 章节归属（populate_questions.py 用王道章号，topics 来自知识图谱，
   后者把"串"并入查找，导致 DS-04 起整体错位一章；排序卡更挂在根本不存在的 408-DS-08）
   注意：daily_planner.py 会从 card_id 前缀反推 topic_id，所以 id 必须一起改，否则白改。

三、按笔记补 32 张卡（内存管理/CPU/存储器层次/树与二叉树 四个高权重低覆盖章节
   + 同步与互斥的信号量辨析）

不触碰任何 FSRS 调度状态（state/due_at/stability/difficulty/reps/lapses/...）。
"""

import io
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DB = Path(r"C:\Users\92534\Desktop\考研\src\question_bank.db")
TODAY = date.today().isoformat()

# ---------------------------------------------------------------------------
# 二、DS 章节归属重排：old_qid -> (new_qid, new_topic_id)
# ---------------------------------------------------------------------------
TOPIC_REMAP = {
    # 树和二叉树
    "Q-408-DS-05-0001": ("Q-408-DS-04-0001", "408-DS-04-01"),  # 完全二叉树叶子数
    "Q-408-DS-05-0003": ("Q-408-DS-04-0002", "408-DS-04-05"),  # 哈夫曼树
    "Q-408-DS-05-0004": ("Q-408-DS-04-0003", "408-DS-04-03"),  # 线索二叉树
    # 图
    "Q-408-DS-06-0001": ("Q-408-DS-05-0001", "408-DS-05-02"),  # BFS/DFS
    "Q-408-DS-06-0002": ("Q-408-DS-05-0002", "408-DS-05-05"),  # 拓扑排序
    "Q-408-DS-06-0003": ("Q-408-DS-05-0003", "408-DS-05-04"),  # Dijkstra/Floyd
    "Q-408-DS-06-0004": ("Q-408-DS-05-0004", "408-DS-05-03"),  # 最小生成树
    # 查找
    "Q-408-DS-07-0001": ("Q-408-DS-06-0001", "408-DS-06-04"),  # B+树
    "Q-408-DS-07-0002": ("Q-408-DS-06-0002", "408-DS-06-05"),  # 装填因子
    "Q-408-DS-07-0003": ("Q-408-DS-06-0003", "408-DS-06-05"),  # 散列冲突
    "Q-408-DS-04-0001": ("Q-408-DS-06-0004", "408-DS-06-06"),  # KMP
    "Q-408-DS-04-0002": ("Q-408-DS-06-0005", "408-DS-06-06"),  # KMP next
    "Q-408-DS-05-0002": ("Q-408-DS-06-0006", "408-DS-06"),     # BST 中序
    # 排序（原挂在不存在的 408-DS-08）
    "Q-408-DS-08-0001": ("Q-408-DS-07-0001", "408-DS-07-07"),  # 不稳定排序
    "Q-408-DS-08-0002": ("Q-408-DS-07-0002", "408-DS-07-07"),  # 最坏 O(nlogn)
    "Q-408-DS-08-0003": ("Q-408-DS-07-0003", "408-DS-07-07"),  # 小规模插入排序
    "Q-408-DS-08-0004": ("Q-408-DS-07-0004", "408-DS-07-02"),  # 快排最差
}

# ---------------------------------------------------------------------------
# 一、内容修正（整条 content 覆写，均为 JSON dict）
# ---------------------------------------------------------------------------
CONTENT_FIX = {
    # 1. 信号量：答案应为"错误"
    "Q-408-OS-02-0005": {
        "stem": "信号量是一种整型变量，除了初始化外，只能通过P操作和V操作来访问。",
        "answer": False,
        "explanation": (
            "错误。信号量分为两类，只有整型信号量才是整型变量。整型信号量定义为 int S，"
            "不满足让权等待；记录型信号量是结构体：typedef struct { int value; "
            "struct process *L; } semaphore; —— 除表示资源数目的 value 外，还有一个"
            "等待队列 L。题目把教材中「整型信号量是整型变量」的说法扩大成了「信号量是整型变量」，"
            "因此错误。（P、V 操作都是原子操作这一点本身是对的。）"
        ),
        "traps": [
            "把教材对「整型信号量」的限定描述误当成对「信号量」的一般描述",
            "忽略记录型信号量是结构体（value + 等待队列 L）",
        ],
        "tags": ["PV操作", "信号量", "记录型信号量", "整型信号量"],
    },
    # 2. 自同步编码：RZ 也是自同步信号，不能当干扰项
    "Q-408-CN-02-0002": {
        "stem": "以下编码方式中，具有自同步能力的是？",
        "options": [
            "不归零编码(NRZ)",
            "曼彻斯特编码",
            "反向不归零编码(NRZI)",
            "双极性AMI编码",
        ],
        "answer": 1,
        "explanation": (
            "曼彻斯特编码在每个码元中间都有一次电平跳变，接收方可以从跳变中提取时钟信号，"
            "实现自同步（不需要额外的时钟线）。NRZ 在连续相同比特时电平不变，接收方无法判断码元边界；"
            "NRZI 在连续多个 1 时同样没有跳变；AMI 在连续多个 0 时没有跳变，都不能自同步。"
            "注意：RZ（归零编码）在每个码元中间也归零，同样具有自同步能力，因此不能作为本题的干扰项。"
        ),
        "traps": ["误以为只有曼彻斯特编码自同步（RZ 也自同步，但浪费带宽）"],
        "tags": ["曼彻斯特编码", "自同步", "编码与调制"],
    },
    # 3. 单链表 O(1) 删除：原判断题两边都说得通，改为无歧义选择题
    "Q-408-DS-02-0002": {
        "stem": "在单链表中，已知指针 p 指向待删除结点（p 不是尾结点）。下列关于删除该结点的说法，正确的是？",
        "options": [
            "必须从头遍历找到 p 的前驱，时间复杂度 O(n)，别无他法",
            "可将 p 后继结点的值复制到 p，再删除 p 的后继结点，时间复杂度 O(1)",
            "单链表中删除任意结点都可以在 O(1) 时间内完成",
            "单链表中无法删除 p 所指结点，只能删除 p 的后继",
        ],
        "answer": 1,
        "explanation": (
            "单链表删除结点常规做法需要前驱，从头遍历为 O(n)；但若 p 不是尾结点，可用「偷梁换柱」法："
            "把 p->next 的值复制到 p，然后删除 p->next，效果等价于删除了 p，时间复杂度 O(1)。"
            "所以 A 说「别无他法」是错的；C 错在 p 为尾结点时此技巧失效，仍须 O(n) 找前驱；D 说法错误。"
        ),
        "traps": [
            "只记住「单链表删除要前驱 O(n)」，忽略偷梁换柱的 O(1) 技巧",
            "忽略该技巧的适用前提：p 不能是尾结点",
        ],
        "tags": ["单链表", "删除结点", "时间复杂度"],
    },
    # 4. 中断/异常：限定为"用户态下发生"才严格成立
    "Q-408-OS-01-0003": {
        "stem": "在用户态下发生的中断（外中断）和异常（内中断），都会导致CPU从用户态切换到核心态。",
        "answer": True,
        "explanation": (
            "正确。中断和异常是 CPU 由用户态进入核心态的两条途径。中断（外中断，如 I/O 完成、时钟中断）"
            "由外部事件触发；异常（内中断，如缺页、除零、系统调用）由当前指令执行引起。"
            "注意题干限定「在用户态下发生」：如果异常本身发生在核心态（如内核中发生缺页），"
            "则只涉及核心态内部的处理，不发生用户态→核心态的切换。"
        ),
        "traps": ["忽略「异常也可能发生在核心态」这一前提，把结论绝对化"],
        "tags": ["中断", "异常", "内核态与用户态"],
    },
}

# 选项重复字母前缀 "A. A. 12" → "12"
OPTION_PREFIX_FIX = ["Q-TGT-A31CA44C", "Q-TGT-45234058"]

# 判断题改选择题后，questions.type 列要一起改（否则前端按判断题渲染）
TYPE_FIX = {"Q-408-DS-02-0002": "choice"}

# ---------------------------------------------------------------------------
# 三、新增卡片：topic_id -> [card, ...]
#     card: {"type", "stem", "options"?, "answer", "explanation", "traps", "tags"}
# ---------------------------------------------------------------------------
NEW_CARDS = [
    # ---------- 408-OS-02-04 同步与互斥（配合被修正的信号量卡） ----------
    ("408-OS-02-04", {
        "type": "judge",
        "stem": "整型信号量是一个整型变量，除初始化外仅能通过P、V两个原子操作访问；但整型信号量不满足让权等待。",
        "answer": True,
        "explanation": (
            "正确。整型信号量定义为 int S，其 wait(S) 为 while(S<=0); S--; —— 当 S<=0 时进程"
            "一直在循环中测试，占着 CPU 空转，因此不满足让权等待。引入等待队列（记录型信号量）后，"
            "资源不足时用 block 原语主动放弃 CPU，才解决了忙等问题。"
        ),
        "traps": ["以为所有信号量都满足让权等待"],
        "tags": ["整型信号量", "让权等待", "PV操作"],
    }),
    ("408-OS-02-04", {
        "type": "choice",
        "stem": "关于整型信号量与记录型信号量，下列说法正确的是？",
        "options": [
            "两者都满足让权等待，都不存在忙等现象",
            "记录型信号量在资源计数值之外增加了等待队列，满足让权等待；整型信号量存在忙等",
            "整型信号量是结构体，记录型信号量是整型变量",
            "只有记录型信号量的值可以为负数，整型信号量的值不能为负",
        ],
        "answer": 1,
        "explanation": (
            "记录型信号量定义为 typedef struct { int value; struct process *L; } semaphore;，"
            "除资源数目 value 外还有等待队列 L；P 操作发现资源不足时调用 block 把自己挂到 L 上，"
            "主动放弃 CPU，满足让权等待。整型信号量只有一个整数，资源不足时忙等。"
            "A 错在整型信号量忙等；C 把两者说反；D 错在记录型信号量的值同样可以为负"
            "（|value| = 等待队列中的进程数），而且这不是区分两者的要点。"
        ),
        "traps": ["把两类信号量的数据结构记反", "误以为信号量的值不能为负数"],
        "tags": ["信号量", "记录型信号量", "整型信号量", "让权等待"],
    }),
    ("408-OS-02-04", {
        "type": "choice",
        "stem": "在实现进程互斥的四类方法（软件方法、硬件指令、关中断、信号量）中，满足「让权等待」准则的是？",
        "options": [
            "只有软件方法（Peterson 算法）",
            "只有硬件指令（TSW/Swap）",
            "只有信号量方法",
            "四类方法都满足",
        ],
        "answer": 2,
        "explanation": (
            "让权等待指进程暂时无法进入临界区时应主动放弃 CPU。Peterson 算法在 while 循环中忙等；"
            "TSW/Swap 是硬件自旋，同样忙等；关中断方法在等待时也是忙等。只有信号量方法在 P 操作"
            "失败时调用 block 原语把进程挂到该信号量的等待队列，主动让出 CPU，"
            "再由 V 操作的 wakeup 唤醒，真正满足让权等待。"
        ),
        "traps": ["把 TSW/Swap 当成信号量方法的一部分", "以为关中断能实现让权等待"],
        "tags": ["让权等待", "互斥", "信号量", "Peterson"],
    }),

    # ---------- 408-OS-03 内存管理 ----------
    ("408-OS-03-03", {
        "type": "choice",
        "stem": "不考虑TLB和Cache时，下列关于地址转换访存次数的说法，正确的是？",
        "options": [
            "页式和段式都只需1次访存，段页式需2次",
            "页式、段式各需2次访存，段页式需3次，n级页表需n+1次",
            "所有非连续分配方式都只需2次访存",
            "段页式需2次访存，因为它只需要查一张表",
        ],
        "answer": 1,
        "explanation": (
            "页表/段表本身存放在主存中，查表也要访存。页式 = 查页表1次 + 访数据1次 = 2次；"
            "段式 = 查段表1次 + 访数据1次 = 2次；段页式 = 查段表1次 + 查页表1次 + 访数据1次 = 3次。"
            "规律是每多一层表结构，访存次数 +1，n 级页表共 n+1 次。有 TLB 且命中时只需 1 次访存。"
        ),
        "traps": ["漏算查表本身的那一次访存", "把段页式当成只查一张表"],
        "tags": ["分页", "分段", "段页式", "访存次数"],
    }),
    ("408-OS-03-05", {
        "type": "judge",
        "stem": "TLB不命中会触发缺页中断。",
        "answer": False,
        "explanation": (
            "错误。三种「缺失」分属不同层次，不能叠加：TLB 不命中只是地址转换层的缓存未命中，"
            "硬件自动查页表（page table walk）取得页框号即可，不触发中断；Cache 不命中只多访存一次，"
            "同样不触发中断；只有页表项有效位 = 0（页不在物理内存）才触发缺页中断，由操作系统"
            "从磁盘调入页面。是否缺页与 TLB、Cache 的状态无关。"
        ),
        "traps": ["名字里都有「缺」，误以为 TLB 缺失就是缺页", "把 TLB/Cache/缺页三种缺失叠加计算"],
        "tags": ["TLB", "缺页中断", "Cache"],
    }),
    ("408-OS-03-05", {
        "type": "choice",
        "stem": "执行指令 copy A to B（A为源操作数，B为目标操作数），若指令本身、A、B各自跨越两个页面，则该指令执行过程中最多可能引发多少次缺页中断？",
        "options": ["3次", "4次", "6次", "8次"],
        "answer": 2,
        "explanation": (
            "取指令跨 2 页 → 2 次；读源操作数 A 跨 2 页 → 2 次；写目标操作数 B 跨 2 页 → 2 次，"
            "共 6 次。易错点：以为取指只算一次（指令跨页时取指也要访问两个页面），"
            "或漏掉指令本身跨页只算两个操作数（4 次）。"
        ),
        "traps": ["漏算取指阶段的缺页", "漏算操作数跨页"],
        "tags": ["缺页中断", "虚拟存储器", "跨页"],
    }),
    ("408-OS-03-06", {
        "type": "judge",
        "stem": "改进型CLOCK置换算法中，优先淘汰的是「访问位=1、修改位=1」的页面。",
        "answer": False,
        "explanation": (
            "错误。改进型 CLOCK 按 (访问位, 修改位) 把页面分四类，淘汰优先级为 "
            "(0,0) > (0,1) > (1,0) > (1,1)。优先淘汰「最近未被访问且未被修改」的页——"
            "既符合 LRU 思想，又不需要写回磁盘，代价最低。(1,1) 是最不该淘汰的，"
            "因为它刚被访问过且被修改过，淘汰它既要写回磁盘又违背局部性。"
        ),
        "traps": ["把优先级记反，以为(1,1)优先淘汰", "忽略修改位的意义是「淘汰后是否要写回磁盘」"],
        "tags": ["页面置换", "CLOCK", "改进型CLOCK"],
    }),
    ("408-OS-03-06", {
        "type": "judge",
        "stem": "在FIFO、LRU、OPT三种页面置换算法中，只有FIFO可能出现Belady异常。",
        "answer": True,
        "explanation": (
            "正确。Belady 异常指分配给进程的物理块数增加，缺页率反而上升。FIFO 只按进入内存的"
            "先后顺序淘汰，不反映页面的使用情况，因此会出现；LRU 和 OPT 都属于栈算法"
            "（物理块增加时，驻留集是原集合的超集，单调不减），不会出现 Belady 异常。"
        ),
        "traps": ["以为所有置换算法都可能有 Belady 异常"],
        "tags": ["页面置换", "Belady异常", "FIFO"],
    }),
    ("408-OS-03-06", {
        "type": "choice",
        "stem": "CLOCK（NRU）算法与LRU算法的本质关系是？",
        "options": [
            "CLOCK是LRU的精确实现",
            "CLOCK是LRU的近似实现，用「最近是否被访问过」近似「最近多久没被访问」",
            "两者完全相同，只是名字不同",
            "CLOCK精度比LRU更高，但开销更大",
        ],
        "answer": 1,
        "explanation": (
            "LRU 需要记录每个页面最近一次被访问的时间（每次访问都要更新时间戳，硬件开销大）；"
            "CLOCK 只给每页一个访问位，指针循环扫描：遇到访问位为 1 的清 0 并给第二次机会，"
            "遇到 0 的淘汰。本质是用 1 位信息近似「最近多久没用过」，精度低但开销小，"
            "实际操作系统常用。"
        ),
        "traps": ["以为 CLOCK 精度更高", "忽略 CLOCK 需要「给第二次机会」这一步"],
        "tags": ["页面置换", "CLOCK", "LRU"],
    }),
    ("408-OS-03-03", {
        "type": "judge",
        "stem": "采用多级页表后，各级页表都可以离散地存放在不连续的物理块中。",
        "answer": True,
        "explanation": (
            "正确。单级页表是一个一维数组，页号直接作为下标，因此整张页表必须连续存放，"
            "大地址空间下需要一大片连续内存。多级页表的上级表项中存放的是下级页表的物理块号"
            "（页框号），通过这个间接寻址就能定位到分散存放的下级页表，因此每级页表都可离散存放。"
        ),
        "traps": ["以为页表必须连续是普遍规律"],
        "tags": ["多级页表", "分页", "页表"],
    }),
    ("408-OS-03-08", {
        "type": "choice",
        "stem": "某请求分页系统测得：CPU利用率10%、磁盘交换区利用率99.7%、其他I/O设备利用率5%。以下措施中能显著提高CPU利用率的是？",
        "options": [
            "增大磁盘交换区容量、使用更快速的磁盘交换区",
            "增大内存容量、减少多道程序度数",
            "增加多道程序度数、使用更快的CPU",
            "增大磁盘交换区容量、使用更快的CPU",
        ],
        "answer": 1,
        "explanation": (
            "CPU 空闲而磁盘交换区几乎满载，是典型的系统颠簸（Thrashing）：内存不足导致缺页"
            "过于频繁，进程都在等页面换入。解决方向是降低缺页率——增大内存容量、减少多道程序度数。"
            "增大/加快交换区只是让颠簸更快发生，不减少换页频率；CPU 本来就空闲，提速没有意义。"
        ),
        "traps": ["看到交换区利用率高就去扩容交换区", "把颠簸误判为 CPU 性能不足"],
        "tags": ["颠簸", "Thrashing", "缺页", "多道程序度"],
    }),

    # ---------- 408-CO-05 中央处理器CPU ----------
    ("408-CO-05-06", {
        "type": "choice",
        "stem": "MIPS五段流水线中，下列关于load-use数据冒险的说法正确的是？",
        "options": [
            "load-use冒险可以用数据前推（转发）完全解决，不需要阻塞",
            "load在MEM段才产生数据，而下一条指令在EX段就需要，转发来不及，必须阻塞一个周期",
            "load-use冒险只发生在load与store之间",
            "只要两条指令之间隔了2条无关指令，就一定是load-use冒险",
        ],
        "answer": 1,
        "explanation": (
            "ALU 指令在 EX 段前半段产生结果，可以 EX→EX 转发；load 要到 MEM 段后半段才拿到数据，"
            "而下一条指令的 EX 段在同一周期的前半段就要用，时间上赶不上，因此必须阻塞（插入气泡）"
            "一个周期。判断 load-use 的三个条件：前一条是 load、后一条在 EX 段要用该 load 的目标寄存器、"
            "两条指令相邻（中间隔一条无关指令通常转发即可覆盖）。"
        ),
        "traps": ["以为所有数据冒险都能靠转发解决", "漏掉「两条指令相邻」这个条件"],
        "tags": ["流水线", "数据冒险", "load-use", "转发"],
    }),
    ("408-CO-05-06", {
        "type": "judge",
        "stem": "标准五段流水线中，分支指令在EX段确定是否跳转，会影响2个周期（I+1已在ID段、I+2已在IF段被错误取入）。",
        "answer": True,
        "explanation": (
            "正确。控制冒险发生在 IF 段：取指时还不知道下一条该取 PC+4 还是分支目标地址。"
            "标准五段流水线中 BEQ 用 ALU 在 EX 段做 rs−rt 比较后才确定跳转，此时 I+1 已进入 ID 段、"
            "I+2 已进入 IF 段，共 2 条指令被错误取入需要清空，即影响 2 个周期。"
            "若在 ID 段用专用比较器提前判断条件，则只影响 1 个周期。"
        ),
        "traps": ["把分支确定阶段记成 ID 段（那是早期 MIPS 的实现）"],
        "tags": ["流水线", "控制冒险", "分支"],
    }),
    ("408-CO-05-04", {
        "type": "choice",
        "stem": "关于微程序控制器中的控制存储器（CM），下列说法正确的是？",
        "options": [
            "CM是寄存器，用于暂存当前正在执行的微指令",
            "CM是Cache，用于缓存主存中的微程序",
            "CM是ROM，专门存放微指令，属于控制器的一部分",
            "CM就是主存中划出的一段区域",
        ],
        "answer": 2,
        "explanation": (
            "控制存储器 CM 是只读存储器（ROM），用于存放微程序（微指令序列）。"
            "它既不是寄存器（寄存器是 CPU 中的时序元件，数量少、按名字访问），"
            "也不是 Cache（Cache 是主存数据的高速副本，映射的是主存地址），更不属于主存。"
            "CM 属于控制器的一部分，与主存和数据通路隔离。"
        ),
        "traps": ["把 CM 当成寄存器或 Cache", "以为微指令存放在控制单元 CU 中（CU 是逻辑部件，CM 才是 ROM）"],
        "tags": ["微程序", "控制存储器", "控制器"],
    }),
    ("408-CO-05-04", {
        "type": "judge",
        "stem": "微程序控制器中，取指微程序是所有机器指令共享的公共微程序，执行微程序则因指令而异。",
        "answer": True,
        "explanation": (
            "正确。所有机器指令都要先取指，因此取指微程序是公共的，在控制存储器中只需存一份；"
            "取指完成后，由 IR 中的操作码字段决定转入哪一段执行微程序，每条机器指令对应自己"
            "专用的执行微程序。另外注意：取指微程序也要占用 CM 空间，所以机器指令条数与"
            "微指令条数并不成正比。"
        ),
        "traps": ["忽略取指微程序也占 CM 空间，导致容量计算漏算"],
        "tags": ["微程序", "取指微程序", "控制存储器"],
    }),
    ("408-CO-05-05", {
        "type": "choice",
        "stem": "关于中断隐指令，下列说法正确的是？",
        "options": [
            "中断隐指令是一条有操作码的ISA指令，程序员可以写在程序里",
            "中断隐指令是硬件自动执行的微操作序列，没有操作码，程序员不可控",
            "中断隐指令就是关中断指令CLI",
            "中断隐指令由操作系统在中断服务程序中显式调用",
        ],
        "answer": 1,
        "explanation": (
            "中断隐指令并不是真正的指令，而是 CPU 响应中断后由硬件自动完成的一系列微操作："
            "关中断、保存断点（PC）、引出中断服务程序。它没有操作码，程序员无法编程控制，"
            "因此不属于 ISA 指令。CLI/STI 是有操作码、可以写在代码里的 ISA 指令，"
            "与中断隐指令中的「关中断」微操作是两回事。"
        ),
        "traps": ["把中断隐指令当成一条真指令", "把它与 CLI 混为一谈"],
        "tags": ["中断", "中断隐指令", "异常"],
    }),
    ("408-CO-05-02", {
        "type": "choice",
        "stem": "关于程序计数器PC的更新，下列说法正确的是？",
        "options": [
            "PC的自增量为1，每条指令执行后固定加1",
            "PC不能被任何指令修改",
            "PC的自增量是当前指令的长度，且PC只能由转移/调用/返回/中断类指令隐式修改",
            "程序员可以用普通算术指令直接对PC赋值",
        ],
        "answer": 2,
        "explanation": (
            "顺序执行时 PC ← PC + 当前指令长度；变长指令集中不同指令长度不同，因此不是固定加 1。"
            "PC 不是通用寄存器，普通指令不能显式操作它，只有转移、调用、返回、中断等控制类指令"
            "才能隐式修改：调用后 PC = 被调用过程入口地址；无条件转移后 PC = 目标地址；"
            "条件转移后 PC 不一定为转移目标地址（要看条件是否成立）。"
        ),
        "traps": ["以为 PC 固定加 1", "以为 PC 可以由普通指令赋值"],
        "tags": ["PC", "程序计数器", "指令周期"],
    }),
    ("408-CO-05-01", {
        "type": "judge",
        "stem": "寄存器与Cache都由SRAM构成、都在CPU芯片内，但寄存器对程序员可见、可由指令显式读写，Cache对程序员透明、由硬件自动管理。",
        "answer": True,
        "explanation": (
            "正确。两者都是 CPU 内部的高速存储，都用 SRAM，但所属层次不同：寄存器属于 ISA 层面"
            "——有名字、出现在指令中、程序员可见可编程；Cache 属于微架构层面——没有独立地址空间、"
            "对程序员完全透明、由硬件自动完成装入与替换。这也是「寄存器不属于存储层次结构、"
            "Cache 属于」这一常见结论的来源。"
        ),
        "traps": ["因两者都用 SRAM、都在片内就认为没有区别"],
        "tags": ["寄存器", "Cache", "ISA"],
    }),
    ("408-CO-05-07", {
        "type": "choice",
        "stem": "关于超标量与超流水线，下列说法正确的是？",
        "options": [
            "超标量是纵向细分流水线，增加段数、缩短时钟周期",
            "超流水线是横向扩展功能部件，每周期同时发射多条指令",
            "超标量是每周期同时发射多条指令（空间并行），超流水线是进一步细分流水线段（时间并行）",
            "超标量必须依赖多核才能实现",
        ],
        "answer": 2,
        "explanation": (
            "超流水线 = 把流水线进一步细分，段数增加、时钟周期缩短，同一套硬件分更多时段工作，"
            "属于时间并行；超标量 = 重复设置多套功能部件，在一个周期内同时发射并执行多条指令，"
            "属于空间并行。两者可以叠加使用。超标量在单核内即可实现，不依赖多核。"
        ),
        "traps": ["把超标量和超流水线的方向记反", "以为超标量需要多核"],
        "tags": ["超标量", "超流水线", "并行"],
    }),

    # ---------- 408-CO-03 存储器层次结构 ----------
    ("408-CO-03-08", {
        "type": "judge",
        "stem": "Cache—主存层次结构的总容量等于主存容量与Cache容量之和。",
        "answer": False,
        "explanation": (
            "错误。Cache 中存放的是主存活跃块的副本，数据与主存大量重叠；且 Cache 对 CPU 完全透明、"
            "没有独立的地址编码，CPU 发出的始终是主存地址。因此该层次结构的逻辑存储容量按主存容量计算，"
            "Cache 不计入。一句话：Cache 扩展的是速度，不是容量；真正扩展容量的是虚拟存储器。"
        ),
        "traps": ["把两个「存储器」的容量直接相加", "以为 Cache 扩展了系统存储容量"],
        "tags": ["Cache", "存储容量", "虚拟存储器"],
    }),
    ("408-CO-03-03", {
        "type": "choice",
        "stem": "按408常规定义（唐朔飞/王道），采用组相联映射时，主存块号到Cache组号的映射关系是？",
        "options": [
            "组号 = 主存块号 mod 组数",
            "组号 = ⌊主存块号 / 路数⌋ mod 组数",
            "组号 = 主存块号 / 组数",
            "组号 = 主存块号 mod 路数",
        ],
        "answer": 0,
        "explanation": (
            "常规定义：Cache 组号 = 主存块号 mod 组数，相邻主存块交替映射到不同组。"
            "⌊主存块号 / 路数⌋ mod 组数 是蒋本珊《计算机组成原理》的定义，"
            "只有 2012 年 408 因当年指定该教材而使用（当年真题原题特别注明「本题映射方式与本书所讲不同」），"
            "2009、2013 至今均用常规定义。"
        ),
        "traps": ["按 2012 年特例的蒋本珊定义去算其余年份的题"],
        "tags": ["组相联", "Cache映射", "2012真题"],
    }),
    ("408-CO-03-03", {
        "type": "choice",
        "stem": "某计算机主存地址32位、按字节编址，块大小32B，Cache数据区32KB，采用4路组相联映射。则组索引位数和Tag位数分别是？",
        "options": [
            "组索引10位，Tag 17位",
            "组索引8位，Tag 19位",
            "组索引8位，Tag 17位",
            "组索引12位，Tag 15位",
        ],
        "answer": 1,
        "explanation": (
            "三步法：①块内偏移 = log₂(32B) = 5 位；②Cache 总行数 = 32KB / 32B = 1024 行，"
            "4 路组相联 → 组数 = 1024 / 4 = 256 = 2⁸ → 组索引 8 位；③Tag = 32 − 8 − 5 = 19 位。"
            "对照：直接映射（1024 行）索引 10 位、Tag 17 位；全相联索引 0 位、Tag 27 位。"
            "组内并行比较，比较器个数 = 路数 = 4。"
        ),
        "traps": ["把总行数当成组数（忘记除以路数）", "把索引位数当成行数位数"],
        "tags": ["组相联", "Tag位数", "Cache计算"],
    }),
    ("408-CO-03-04", {
        "type": "judge",
        "stem": "直接映射的Cache不需要替换算法。",
        "answer": True,
        "explanation": (
            "正确。直接映射中每个主存块只能映射到唯一的一行，位置固定，新块调入时直接覆盖原来那一行，"
            "没有选择的余地，因此不需要替换算法。组相联和全相联在组内/全局有多行可选，"
            "必须用 LRU、FIFO、随机等替换算法决定淘汰哪一行。"
        ),
        "traps": ["以为所有 Cache 都需要替换算法"],
        "tags": ["Cache", "替换算法", "直接映射"],
    }),
    ("408-CO-03-04", {
        "type": "choice",
        "stem": "Cache与主存之间进行替换时的基本单位是？",
        "options": ["字节", "字", "块（行）", "页"],
        "answer": 2,
        "explanation": (
            "Cache 与主存之间以「块（行）」为单位整体交换数据，这是空间局部性的要求："
            "访问某地址时把包含它的一整块调入，后续访问相邻数据即可命中。"
            "注意区分：CPU 访存的单位是字/字节，Cache 命中时只读写块中的某个字，"
            "只有未命中时才整块替换。「页」是虚拟存储器的交换单位。"
        ),
        "traps": ["把 CPU 访存单位（字）当成 Cache 替换单位", "与虚拟存储器的「页」混淆"],
        "tags": ["Cache", "块", "替换单位", "空间局部性"],
    }),
    ("408-CO-03-08", {
        "type": "judge",
        "stem": "指令Cache的空间局部性通常比数据Cache更好。",
        "answer": True,
        "explanation": (
            "正确。程序取指以顺序执行为主，PC 通常按 PC+4 递增，编译后的代码在内存中连续存放，"
            "取一条指令后同一块内的后续指令大概率马上被用到，因此指令 Cache 的空间局部性更好。"
            "数据访问模式复杂——链表中结点分散、哈希表随机访问、数组大跨距访问，"
            "都会破坏空间局部性，因此数据 Cache 的空间局部性相对较差。"
        ),
        "traps": ["以为取指有分支跳转所以局部性差（顺序执行仍占主导）"],
        "tags": ["Cache", "空间局部性", "指令Cache"],
    }),

    # ---------- 408-DS-04 树和二叉树 ----------
    ("408-DS-04-01", {
        "type": "judge",
        "stem": "任何一棵非空二叉树中，叶结点数等于度为2的结点数加1。",
        "answer": True,
        "explanation": (
            "正确。设结点总数 n = n₀+n₁+n₂。总分支数 = n−1（除根外每个结点有一条入边），"
            "而按出度计算总分支数 = 0×n₀ + 1×n₁ + 2×n₂ = n₁+2n₂。"
            "于是 n₀+n₁+n₂ = n₁+2n₂+1，化简得 n₀ = n₂+1。"
            "该式与 n₁ 无关，因此对任意非空二叉树都成立。"
        ),
        "traps": ["以为该性质只对完全二叉树成立"],
        "tags": ["二叉树性质", "叶结点", "度为2"],
    }),
    ("408-DS-04-01", {
        "type": "choice",
        "stem": "含n个结点的完全二叉树按层序从1开始编号，则结点i（满足2i ≤ n）的左孩子编号是？",
        "options": ["i+1", "2i", "2i+1", "⌊i/2⌋"],
        "answer": 1,
        "explanation": (
            "完全二叉树按层序编号时有：结点 i 的双亲为 ⌊i/2⌋；左孩子为 2i（若 2i ≤ n）；"
            "右孩子为 2i+1（若 2i+1 ≤ n）；若 i ≤ ⌊n/2⌋ 则结点 i 是分支结点，否则是叶结点。"
            "由最后一条还可推出：n 个结点的完全二叉树，叶结点编号从 ⌊n/2⌋+1 到 n。"
        ),
        "traps": ["把双亲编号 ⌊i/2⌋ 与孩子编号记混"],
        "tags": ["完全二叉树", "层序编号", "二叉树性质"],
    }),
    ("408-DS-04-02", {
        "type": "choice",
        "stem": "下列遍历序列组合中，不能唯一确定一棵二叉树的是？",
        "options": [
            "先序序列 + 中序序列",
            "后序序列 + 中序序列",
            "层次序列 + 中序序列",
            "先序序列 + 后序序列",
        ],
        "answer": 3,
        "explanation": (
            "先序+中序、后序+中序、层次+中序都能唯一确定一棵二叉树：中序序列中根结点左边是左子树、"
            "右边是右子树，而先序的第一个元素、后序的最后一个元素、层次序列的第一个元素都能给出根，"
            "于是可以递归划分左右子树。只有先序+后序不能唯一确定——当某个结点只有一个子树时，"
            "无法区分这棵子树是左子树还是右子树。"
        ),
        "traps": ["以为任意两种遍历组合都能唯一确定二叉树"],
        "tags": ["二叉树遍历", "构造二叉树", "先序后序"],
    }),
    ("408-DS-04-04", {
        "type": "judge",
        "stem": "将含m棵树的森林转换为二叉树后，二叉树根结点的右孩子链长度为m−1。",
        "answer": True,
        "explanation": (
            "正确。森林转二叉树用「左孩子右兄弟」：第一棵树的根作为二叉树的根，"
            "其余每棵树的根依次作为前一棵树根的右孩子。因此从根结点沿右孩子方向共有 m−1 个结点，"
            "右孩子链长度就是 m−1。反过来，若二叉树根结点有右孩子，把右孩子链断开即可还原成森林。"
        ),
        "traps": ["把右孩子链长度记成 m 或 m+1"],
        "tags": ["森林", "树转二叉树", "左孩子右兄弟"],
    }),
    ("408-DS-04-04", {
        "type": "choice",
        "stem": "树的后根遍历相当于其对应二叉树的哪种遍历？",
        "options": ["先序遍历", "中序遍历", "后序遍历", "层次遍历"],
        "answer": 1,
        "explanation": (
            "树转二叉树采用「左孩子右兄弟」后，遍历有固定对应关系：树的先根遍历 = 二叉树的先序遍历；"
            "树的后根遍历 = 二叉树的中序遍历。森林的遍历也有相同对应关系。"
            "这是树与二叉树转换中最常考的结论，注意不要按字面把「后根」对应成「后序」。"
        ),
        "traps": ["按字面把后根遍历对应成二叉树的后序遍历"],
        "tags": ["树转二叉树", "遍历对应", "后根遍历"],
    }),
    ("408-DS-04-05", {
        "type": "choice",
        "stem": "含n个叶结点的哈夫曼树，其结点总数和度为1的结点数分别是？",
        "options": ["2n−1，0", "2n，0", "2n−1，1", "2n+1，0"],
        "answer": 0,
        "explanation": (
            "哈夫曼树每次合并两棵根权值最小的树，合并 n−1 次，每次新增一个分支结点，"
            "故结点总数 = n + (n−1) = 2n−1；且不存在度为 1 的结点（只有度为 0 的叶结点和"
            "度为 2 的分支结点）。另外 WPL 有两种算法：① ∑(叶结点权值 × 路径长度)；"
            "② ∑(所有分支结点的权值)，两者结果相同。"
        ),
        "traps": ["以为哈夫曼树可能存在度为1的结点"],
        "tags": ["哈夫曼树", "WPL", "结点总数"],
    }),
    ("408-DS-04-06", {
        "type": "judge",
        "stem": "并查集采用树的双亲表示法存储，配合「小树并入大树」和「路径压缩」两种优化后，单次操作的平均时间复杂度近似为O(α(n))（α为阿克曼函数的反函数）。",
        "answer": True,
        "explanation": (
            "正确。并查集用数组实现双亲表示法：数组下标是元素编号，数组值存双亲的下标，"
            "根结点的双亲域存 −集合大小（或 −1）。「小树并入大树」控制树高不快速增长；"
            "「路径压缩」在 Find 时把查找路径上所有结点直接挂到根上。两者结合后单次操作"
            "近似 O(α(n))，实际应用中 α(n) < 5，可视为常数。"
        ),
        "traps": ["把并查集的存储结构误记为邻接表或孩子表示法"],
        "tags": ["并查集", "路径压缩", "双亲表示法"],
    }),
]


def main():
    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA foreign_keys=OFF")
    c = conn.cursor()
    stats = {"remap": 0, "content": 0, "opts": 0, "new": 0}

    # ---- 1. 内容修正 ----
    for qid, new_content in CONTENT_FIX.items():
        row = c.execute("SELECT content FROM questions WHERE id=?", (qid,)).fetchone()
        if not row:
            print(f"  [跳过·未找到] {qid}")
            continue
        old = json.loads(row[0])
        merged = dict(old)
        merged.update(new_content)
        c.execute("UPDATE questions SET content=? WHERE id=?",
                  (json.dumps(merged, ensure_ascii=False), qid))
        if "answer" in new_content:
            old_ans = old.get("answer")
            new_ans = new_content["answer"]
            flag = f" 答案 {old_ans} → {new_ans}" if old_ans != new_ans else ""
            print(f"  [修正内容] {qid}{flag}")
        else:
            print(f"  [修正内容] {qid}")
        stats["content"] += 1

    # ---- 2. 选项重复字母前缀 ----
    for qid in OPTION_PREFIX_FIX:
        row = c.execute("SELECT content FROM questions WHERE id=?", (qid,)).fetchone()
        if not row:
            print(f"  [跳过·未找到] {qid}")
            continue
        ct = json.loads(row[0])
        opts = ct.get("options") or []
        cleaned = []
        for o in opts:
            s = str(o).strip()
            if len(s) > 3 and s[1:3] in (". ", "、"):
                s = s[3:].strip()
            elif len(s) > 2 and s[0] in "ABCD" and s[1] in ".、":
                s = s[2:].strip()
            cleaned.append(s)
        ct["options"] = cleaned
        c.execute("UPDATE questions SET content=? WHERE id=?",
                  (json.dumps(ct, ensure_ascii=False), qid))
        print(f"  [修正选项] {qid} → {cleaned[:2]}...")
        stats["opts"] += 1

    # ---- 2b. 题型列同步 ----
    for qid, new_type in TYPE_FIX.items():
        cur = c.execute("SELECT type FROM questions WHERE id=?", (qid,)).fetchone()
        if cur and cur[0] != new_type:
            c.execute("UPDATE questions SET type=? WHERE id=?", (new_type, qid))
            print(f"  [修正题型] {qid} {cur[0]} → {new_type}")

    # ---- 3. DS 章节归属重排（questions.id / topic_id + cards + review_log 同步）----
    # 先整段腾挪，避免中途撞主键：先改成临时 id，再改成目标 id
    for old_qid, (new_qid, new_topic) in TOPIC_REMAP.items():
        row = c.execute("SELECT topic_id FROM questions WHERE id=?", (old_qid,)).fetchone()
        if not row:
            print(f"  [跳过·未找到] {old_qid}")
            continue
        tmp_qid = "TMP-" + old_qid
        c.execute("UPDATE questions SET id=? WHERE id=?", (tmp_qid, old_qid))
        c.execute("UPDATE cards SET question_id=? WHERE question_id=?", (tmp_qid, old_qid))
        c.execute("UPDATE review_log SET question_id=? WHERE question_id=?", (tmp_qid, old_qid))
        old_cid = "C-" + old_qid
        c.execute("UPDATE cards SET id=? WHERE id=?", ("TMP-" + old_cid, old_cid))
        c.execute("UPDATE review_log SET card_id=? WHERE card_id=?", ("TMP-" + old_cid, old_cid))
        print(f"  [归属] {old_qid} ({row[0]}) → {new_qid} ({new_topic})")
        stats["remap"] += 1

    # 第二遍：临时 id → 最终 id
    for old_qid, (new_qid, new_topic) in TOPIC_REMAP.items():
        tmp_qid = "TMP-" + old_qid
        if not c.execute("SELECT 1 FROM questions WHERE id=?", (tmp_qid,)).fetchone():
            continue
        c.execute("UPDATE questions SET id=?, topic_id=? WHERE id=?",
                  (new_qid, new_topic, tmp_qid))
        c.execute("UPDATE cards SET question_id=? WHERE question_id=?", (new_qid, tmp_qid))
        c.execute("UPDATE review_log SET question_id=? WHERE question_id=?", (new_qid, tmp_qid))
        c.execute("UPDATE cards SET id=? WHERE id=?", ("C-" + new_qid, "TMP-C-" + old_qid))
        c.execute("UPDATE review_log SET card_id=? WHERE card_id=?",
                  ("C-" + new_qid, "TMP-C-" + old_qid))

    # 兜底：卡片 id 格式若不统一（历史上少数卡 id == question_id），按 question_id 归一
    c.execute("UPDATE cards SET id = 'C-' || question_id "
              "WHERE id LIKE 'TMP-%' AND question_id NOT LIKE 'TMP-%'")
    c.execute("UPDATE review_log SET card_id = 'C-' || question_id "
              "WHERE card_id LIKE 'TMP-%' AND question_id NOT LIKE 'TMP-%'")

    # ---- 4. 新增卡片 ----
    # 计算每个章节的现有最大编号，续号
    seq = {}
    for (topic, _) in NEW_CARDS:
        if topic in seq:
            continue
        ch = "-".join(topic.split("-")[:3])          # 408-OS-02 / 408-CO-05 ...
        rows = c.execute(
            "SELECT id FROM questions WHERE id LIKE ?", (f"Q-{ch}-%",)).fetchall()
        mx = 0
        for (qid,) in rows:
            tail = qid.rsplit("-", 1)[-1]
            if tail.isdigit():
                mx = max(mx, int(tail))
        seq[topic] = mx
        seq[ch] = mx

    for topic, card in NEW_CARDS:
        # 防重复执行：同题干已入库则跳过
        if c.execute("SELECT 1 FROM questions WHERE content LIKE ?",
                     (f'%{card["stem"][:40]}%',)).fetchone():
            print(f"  [跳过·已存在] {card['stem'][:34]}...")
            continue
        ch = "-".join(topic.split("-")[:3])
        seq[ch] += 1
        n = seq[ch]
        qid = f"Q-{ch}-{n:04d}"
        cid = f"C-{qid}"
        content = {
            "stem": card["stem"],
            "explanation": card["explanation"],
            "tags": card["tags"],
            "traps": card.get("traps", []),
        }
        if card["type"] == "choice":
            content["options"] = card["options"]
            content["answer"] = card["answer"]
        else:
            content["answer"] = bool(card["answer"])
        c.execute(
            "INSERT INTO questions (id, topic_id, type, difficulty, source, content) "
            "VALUES (?,?,?,0.5,?,?)",
            (qid, topic, card["type"], f"笔记补卡-{TODAY}",
             json.dumps(content, ensure_ascii=False)))
        c.execute(
            "INSERT INTO cards (id, question_id, state, due_date) VALUES (?,?,0,?)",
            (cid, qid, TODAY))
        print(f"  [新增] {qid} {topic} ({card['type']}) {card['stem'][:34]}...")
        stats["new"] += 1

    conn.commit()

    # ---- 校验 ----
    print("\n=== 校验 ===")
    orphan = c.execute(
        "SELECT q.id, q.topic_id FROM questions q "
        "WHERE q.topic_id IS NOT NULL AND q.topic_id NOT IN (SELECT id FROM topics)"
    ).fetchall()
    print(f"孤儿知识点题：{len(orphan)}", orphan if orphan else "（无）")
    noattr = c.execute(
        "SELECT q.id FROM questions q LEFT JOIN topics t ON q.topic_id=t.id "
        "WHERE t.subject LIKE '408%' AND t.id IS NULL"
    ).fetchall()
    print(f"无 topic 的 408 题：{len(noattr)}")
    bad = c.execute(
        "SELECT count(*) FROM cards c LEFT JOIN questions q ON c.question_id=q.id "
        "WHERE q.id IS NULL"
    ).fetchone()[0]
    print(f"悬空 card：{bad}")
    n408 = c.execute(
        "SELECT count(*) FROM questions q JOIN topics t ON q.topic_id=t.id "
        "WHERE t.subject LIKE '408%'"
    ).fetchone()[0]
    print(f"408 题总数：{n408}")

    print(f"\n完成：内容修正 {stats['content']} · 选项修正 {stats['opts']} · "
          f"归属重排 {stats['remap']} · 新增 {stats['new']}")
    conn.close()


if __name__ == "__main__":
    main()

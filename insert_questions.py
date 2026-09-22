#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Insert new questions into the flashcard question bank."""

import sqlite3
import json
import random
from datetime import date

DB_PATH = r"E:\NPEE\src\question_bank.db"
today = date.today().isoformat()

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# ========== Verify/Create Topics ==========
topics_to_ensure = [
    ('408-OS-02-04', '408', '同步与互斥(临界区/信号量/PV操作)', 2, 1.0, 0.7),
    ('408-OS-02-05', '408', '经典同步问题(生产者消费者/读者写者/哲学家)', 2, 1.0, 0.7),
    ('ENG-VOC-01-03', '英语一', '词根词缀与词法', 1, 0.8, 0.4),
    ('408-CROSS-01', '408', '跨科综合题', 0, 1.5, 0.8),
]

for tid, subj, name, ch, weight, diff in topics_to_ensure:
    c.execute("SELECT id FROM topics WHERE id=?", (tid,))
    if not c.fetchone():
        c.execute("INSERT INTO topics VALUES(?,?,?,?,?,?)", (tid, subj, name, ch, weight, diff))
        print(f"  Created topic: {tid}")

conn.commit()

# ========== Helper ==========
def insert_q(qid, topic_id, qtype, difficulty, source, content_dict):
    content_json = json.dumps(content_dict, ensure_ascii=False)
    try:
        c.execute(
            "INSERT INTO questions (id, topic_id, type, difficulty, source, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
            (qid, topic_id, qtype, difficulty, source, content_json)
        )
        c.execute(
            "INSERT INTO cards (id, question_id, state, difficulty, stability, due_date, reps, lapses) "
            "VALUES (?, ?, 0, 0.0, 0.0, ?, 0, 0)",
            (qid, qid, today)
        )
        return True
    except sqlite3.IntegrityError:
        print(f"  Skipped existing: {qid}")
        return False


# ===========================
# TASK 1.1: 20 PV Operation Questions
# ===========================
pv_count = 0

# Q5: Judge - semaphore basics
pv_count += insert_q('Q-408-OS-02-0005', '408-OS-02-04', 'judge', 0.5, 'AI生成', {
    'stem': '信号量是一种整型变量，除了初始化外，只能通过P操作和V操作来访问，P操作和V操作都是原子操作。',
    'answer': True,
    'explanation': '信号量(Semaphore)是Dijkstra提出的一种进程同步机制，它本质上是一个整型变量加上一对原子操作P(wait)和V(signal)。P操作和V操作在执行过程中不可被中断，保证了原子性。',
    'tags': ['PV操作', '进程同步', '信号量']
})

# Q6: Choice - P operation
pv_count += insert_q('Q-408-OS-02-0006', '408-OS-02-04', 'choice', 0.5, 'AI生成', {
    'stem': 'P操作(wait操作)的执行逻辑是：先将信号量S减1，若S<0则进程阻塞。以下关于P操作的说法正确的是？',
    'options': ['P操作可能改变进程的状态为阻塞态', 'P操作一定不会改变信号量的值', 'P操作执行后S的值一定大于等于0', 'P操作是V操作的逆操作，两者完全等价'],
    'answer': 0,
    'explanation': 'P操作先将S减1，如果S<0，则将进程加入阻塞队列，因此P操作可能使进程从就绪/运行态变为阻塞态。P操作一定会改变S的值（减1），P操作后S可能为负数。',
    'tags': ['PV操作', '信号量', '进程同步']
})

# Q7: Judge - V operation
pv_count += insert_q('Q-408-OS-02-0007', '408-OS-02-04', 'judge', 0.4, 'AI生成', {
    'stem': 'V操作(signal操作)的执行逻辑是：先将信号量S加1，若S<=0则唤醒阻塞队列中的一个进程。',
    'answer': True,
    'explanation': 'V操作先将S加1。如果加1后S<=0，说明阻塞队列中还有进程在等待，于是唤醒一个阻塞进程。注意判断条件是S<=0而非S<0，因为加1前S可能为-1或更小。',
    'tags': ['PV操作', '信号量']
})

# Q8: Choice - mutex init value
pv_count += insert_q('Q-408-OS-02-0008', '408-OS-02-04', 'choice', 0.6, 'AI生成', {
    'stem': '互斥信号量(mutex)的初值通常设置为？',
    'options': ['0', '1', '-1', '进程数量'],
    'answer': 1,
    'explanation': '互斥信号量用于实现互斥访问临界资源，初值设为1，表示临界资源可用。第一个进程执行P(mutex)后S变为0，进入临界区；其他进程执行P(mutex)时S变为负数被阻塞。',
    'tags': ['PV操作', '互斥', '信号量']
})

# Q9: Judge - P/V pairing
pv_count += insert_q('Q-408-OS-02-0009', '408-OS-02-04', 'judge', 0.5, 'AI生成', {
    'stem': '用信号量实现互斥时，P(mutex)和V(mutex)必须成对出现在同一进程的临界区前后，缺少任何一个都会导致错误。',
    'answer': True,
    'explanation': 'P(mutex)在进入临界区前执行（申请资源），V(mutex)在离开临界区后执行（释放资源）。两者必须成对出现，否则会导致死锁（缺少V）或互斥失效（缺少P）。',
    'tags': ['PV操作', '互斥']
})

# Q10: Choice - producer-consumer order
pv_count += insert_q('Q-408-OS-02-0010', '408-OS-02-05', 'choice', 0.6, 'AI生成', {
    'stem': '在经典生产者-消费者问题中，使用3个信号量：mutex(初值1)、empty(初值n)、full(初值0)。生产者进程的正确操作顺序是？',
    'options': ['P(mutex) -> P(empty) -> 生产 -> V(mutex) -> V(full)', 'P(empty) -> P(mutex) -> 生产 -> V(mutex) -> V(full)', 'P(empty) -> P(mutex) -> 生产 -> V(full) -> V(mutex)', 'P(mutex) -> P(full) -> 生产 -> V(empty) -> V(mutex)'],
    'answer': 1,
    'explanation': '必须先P(empty)再P(mutex)。如果先P(mutex)再P(empty)，当缓冲区满时，生产者占有了mutex却被empty阻塞，而消费者需要mutex才能取走产品，导致死锁。',
    'tags': ['PV操作', '生产者-消费者', '进程同步']
})

# Q11: Judge - empty/full meaning
pv_count += insert_q('Q-408-OS-02-0011', '408-OS-02-05', 'judge', 0.5, 'AI生成', {
    'stem': '生产者-消费者问题中，信号量empty表示缓冲区中空位置的数量，信号量full表示缓冲区中已填充产品的数量。',
    'answer': True,
    'explanation': 'empty初值为n（缓冲区大小），表示可用空位数量；full初值为0，表示已有产品数量。两者是资源信号量，用于同步生产者和消费者。',
    'tags': ['PV操作', '生产者-消费者']
})

# Q12: Choice - P order swap consequence
pv_count += insert_q('Q-408-OS-02-0012', '408-OS-02-05', 'choice', 0.7, 'AI生成', {
    'stem': '生产者-消费者问题中，如果将生产者的P(empty)和P(mutex)顺序对调（先P(mutex)再P(empty)），在什么情况下会出现问题？',
    'options': ['缓冲区为空时', '缓冲区为满时', '任何时候都不会有问题', '只有多个生产者时才有问题'],
    'answer': 1,
    'explanation': '当缓冲区满时(empty=0)，如果生产者先执行P(mutex)成功（mutex变为0），然后执行P(empty)被阻塞。此时消费者需要P(mutex)进入临界区取产品，但mutex已被生产者占用，形成死锁。',
    'tags': ['PV操作', '生产者-消费者', '死锁']
})

# Q13: Judge - mutex necessity
pv_count += insert_q('Q-408-OS-02-0013', '408-OS-02-05', 'judge', 0.6, 'AI生成', {
    'stem': '在生产者-消费者问题中，如果去掉互斥信号量mutex，仅保留empty和full，程序仍然能正确运行，因为empty和full已经保证了互斥。',
    'answer': False,
    'explanation': 'empty和full是资源信号量，用于同步，不能保证对缓冲区的互斥访问。当缓冲区既不满也不空时，生产者和消费者可能同时访问缓冲区，导致数据竞争。mutex是必须的互斥信号量。',
    'tags': ['PV操作', '生产者-消费者', '互斥']
})

# Q14: Choice - reader-writer readcount
pv_count += insert_q('Q-408-OS-02-0014', '408-OS-02-05', 'choice', 0.6, 'AI生成', {
    'stem': '读者优先的读者-写者问题中，readcount(读者计数)的作用是？',
    'options': ['控制写者互斥访问', '记录正在读的读者数量，第一个读者与写者互斥，最后一个读者释放写锁', '限制读者的最大数量', '保证读者和写者同时访问'],
    'answer': 1,
    'explanation': 'readcount记录正在读的读者数。第一个读者执行P(wmutex)与写者互斥，后续读者直接读。最后一个读者执行V(wmutex)释放。这样多个读者可以同时读，但读写互斥。',
    'tags': ['PV操作', '读者-写者', '进程同步']
})

# Q15: Judge - reader priority starvation
pv_count += insert_q('Q-408-OS-02-0015', '408-OS-02-05', 'judge', 0.6, 'AI生成', {
    'stem': '读者优先的读者-写者问题中，只要有读者在读，后来的写者就必须等待，可能导致写者饥饿。',
    'answer': True,
    'explanation': '读者优先策略下，不断有新的读者到来时，写者将一直无法获得访问权（因为readcount始终不为0，wmutex不会被释放），导致写者饥饿。',
    'tags': ['PV操作', '读者-写者', '饥饿']
})

# Q16: Choice - writer priority
pv_count += insert_q('Q-408-OS-02-0016', '408-OS-02-05', 'choice', 0.7, 'AI生成', {
    'stem': '写者优先的读者-写者问题相比读者优先版本，额外增加的信号量w(初值1)的作用是？',
    'options': ['实现写者之间的互斥', '在有写者等待时阻止新的读者进入，防止写者饥饿', '记录等待的写者数量', '保护读操作的互斥'],
    'answer': 1,
    'explanation': '写者优先方案中，信号量w(初值1)用于在有写者等待时阻塞新到来的读者。第一个等待的写者执行P(w)锁住，后续读者在P(w)处被阻塞，直到所有等待的写者完成。',
    'tags': ['PV操作', '读者-写者', '写者优先']
})

# Q17: Choice - dining philosophers deadlock
pv_count += insert_q('Q-408-OS-02-0017', '408-OS-02-05', 'choice', 0.7, 'AI生成', {
    'stem': '5个哲学家围坐圆桌，每人左右各有一根筷子(共5根)。以下哪种方案不能解决哲学家进餐问题中的死锁？',
    'options': ['限制最多4个哲学家同时拿起筷子', '奇数号哲学家先拿左筷子再拿右筷子，偶数号先拿右筷子再拿左筷子', '每个哲学家都先拿左筷子再拿右筷子', '仅当左右两根筷子都可用时才同时拿起'],
    'answer': 2,
    'explanation': '如果每个哲学家都先拿左筷子再拿右筷子，当5个哲学家同时拿起左筷子时，每人右手边都没有筷子可用，形成循环等待——死锁。其他三个方案都能破坏死锁的必要条件。',
    'tags': ['PV操作', '哲学家进餐', '死锁']
})

# Q18: Judge - chopstick semaphore
pv_count += insert_q('Q-408-OS-02-0018', '408-OS-02-05', 'judge', 0.5, 'AI生成', {
    'stem': '哲学家进餐问题中，使用信号量数组chopstick[5](每个初值为1)来模拟5根筷子，哲学家进餐前需要分别P(chopstick[i])和P(chopstick[(i+1)%5])获取左右筷子。',
    'answer': True,
    'explanation': '每根筷子用一个信号量表示，初值为1。哲学家需要同时获得左右两根筷子（两个P操作）才能进餐，进餐后释放（两个V操作）。但简单实现可能导致死锁。',
    'tags': ['PV操作', '哲学家进餐']
})

# Q19: Choice - smokers problem
pv_count += insert_q('Q-408-OS-02-0019', '408-OS-02-05', 'choice', 0.7, 'AI生成', {
    'stem': '吸烟者问题中，代理商随机在桌上放两种物品（烟草/纸/火柴），3个吸烟者各缺一种。代理商放物品后应执行V操作唤醒对应的吸烟者，使用的信号量个数至少为？',
    'options': ['1个', '2个', '3个', '4个'],
    'answer': 3,
    'explanation': '需要至少4个信号量：1个agent信号量(代理商放物品后等待)，3个smoker信号量(分别对应缺烟草/缺纸/缺火柴的吸烟者)。代理商放物品后V对应的smoker信号量，然后P(agent)等待。',
    'tags': ['PV操作', '吸烟者问题']
})

# Q20: Judge - smokers vs producer-consumer
pv_count += insert_q('Q-408-OS-02-0020', '408-OS-02-05', 'judge', 0.6, 'AI生成', {
    'stem': '吸烟者问题与生产者-消费者问题的关键区别在于：吸烟者问题中代理商生产的物品组合是随机的，需要条件同步。',
    'answer': True,
    'explanation': '代理商随机放置两种物品的组合，不同的组合唤醒不同的吸烟者。这需要根据条件来选择唤醒哪个进程，是典型的条件同步问题。',
    'tags': ['PV操作', '吸烟者问题', '进程同步']
})

# Q21: Choice - sleeping barber
pv_count += insert_q('Q-408-OS-02-0021', '408-OS-02-05', 'choice', 0.7, 'AI生成', {
    'stem': '睡眠理发师问题中，理发店有1把理发椅和n把等候椅。customers信号量的作用是？',
    'options': ['记录正在理发的顾客数量', '记录等待理发的顾客数量，唤醒睡眠中的理发师', '控制等候椅的互斥访问', '记录理发师的忙闲状态'],
    'answer': 1,
    'explanation': 'customers信号量初值为0，每来一个顾客执行V(customers)唤醒理发师。理发师循环执行P(customers)，没有顾客时理发师睡眠。customers记录等待的顾客数量。',
    'tags': ['PV操作', '睡眠理发师', '进程同步']
})

# Q22: Choice - PV and deadlock
pv_count += insert_q('Q-408-OS-02-0022', '408-OS-02-04', 'choice', 0.6, 'AI生成', {
    'stem': '以下关于PV操作与死锁关系的说法，正确的是？',
    'options': ['使用PV操作一定不会发生死锁', 'PV操作使用不当可能导致死锁，如P操作顺序不对', 'PV操作本身就是死锁的一种形式', '只要使用互斥信号量就不会发生死锁'],
    'answer': 1,
    'explanation': 'PV操作本身是同步机制，但使用不当可能导致死锁。例如生产者-消费者问题中P操作顺序不对(先P(mutex)再P(empty))会导致死锁，哲学家进餐问题中同时拿左右筷子也会死锁。',
    'tags': ['PV操作', '死锁', '进程同步']
})

# Q23: Judge - semaphore value range
pv_count += insert_q('Q-408-OS-02-0023', '408-OS-02-04', 'judge', 0.5, 'AI生成', {
    'stem': '当信号量S的值为负数时，其绝对值表示阻塞队列中等待该信号量的进程个数。',
    'answer': True,
    'explanation': '信号量S>0表示可用资源数，S=0表示资源刚好用完但无等待进程，S<0表示有|S|个进程在阻塞队列中等待。这是信号量机制的基本性质。',
    'tags': ['PV操作', '信号量', '进程同步']
})

# Q24: Choice - synchronization concept
pv_count += insert_q('Q-408-OS-02-0024', '408-OS-02-04', 'choice', 0.5, 'AI生成', {
    'stem': '进程同步和进程互斥的区别是？',
    'options': ['同步和互斥完全相同，只是不同叫法', '互斥是指进程间不能同时访问临界资源，同步是指进程间的执行顺序有约束关系', '同步只存在于多处理器系统，互斥只存在于单处理器系统', '互斥用硬件实现，同步只能用软件实现'],
    'answer': 1,
    'explanation': '互斥强调对共享资源的排他访问（不能同时使用），是制约关系；同步强调进程间执行的先后顺序（必须先A后B），是协调关系。互斥可看作同步的特殊情况。',
    'tags': ['PV操作', '进程同步', '进程互斥']
})

print(f"Task 1.1: Inserted {pv_count} PV operation questions")


# ===========================
# TASK 1.2: 15 English Root/Affix Questions
# ===========================
eng_count = 0

# "字母+连字符"紧接括号 = 词缀标签（re-(再/重新)、-ful(充满...的)）
AFFIX_LABEL_RE = re.compile(r'^\s*(-[A-Za-z]{1,12}|-?[A-Za-z]{1,12}-)\s*[（(]')

eng_data = [
    # ⚠️ 选项必须同形态：meaning 与 distractors 都得是"裸中文释义"。
    #    绝不可写成 "re-(再/重新)" 这种带词缀标签的形式——那样正确答案
    #    就成了唯一没有标签的选项，学生只看格式就能选对（2026-09-13 用户反馈）。
    #    干扰项取自同语义场（前缀↔方向前缀，后缀↔词性后缀），必须真辨析才能排除。
    {
        'prefix': 'pre-',
        'meaning': '在前/预先',
        'distractors': ['在后/之后', '共同/一起', '向外/离开'],
        'examples': 'predict(预测)、preview(预览)、prepare(准备)',
        'expl': 'pre-表示"前"或"预先"，如predict=pre(前)+dict(说)=预言'
    },
    {
        'prefix': 'anti-',
        'meaning': '反对/对抗',
        'distractors': ['支持/赞成', '在...之前', '共同/一起'],
        'examples': 'antibiotic(抗生素)、antidote(解毒剂)、antisocial(反社会的)',
        'expl': 'anti-表示"反对"，如antibiotic=anti(反)+bio(生命)=抗生素。辨析：anti-(反对) ↔ pro-(支持)'
    },
    {
        'prefix': 'sub-',
        'meaning': '在...下面/次级',
        'distractors': ['在...上面/超越', '在...之间/相互', '跨越/转变'],
        'examples': 'submarine(潜水艇)、subordinate(下属)、subway(地铁)',
        'expl': 'sub-表示"在下方"或"次级"，如submarine=sub(下)+marine(海洋的)=潜水艇。辨析：sub-(下) ↔ super-(上)'
    },
    {
        'prefix': '-able/-ible',
        'meaning': '能被...的（含被动义）',
        'distractors': ['充满...的', '没有/缺少...的', '使...化'],
        'examples': 'readable(可读的)、flexible(灵活的)、visible(可见的)',
        'expl': '-able/-ible是形容词后缀，表示"能被...的"（被动），如unmistakable=不可能被弄错的=明确无误的'
    },
    {
        'prefix': 're-',
        'meaning': '回/再/重新',
        'distractors': ['向前/支持', '向下/去除', '向外/离开'],
        'examples': 'review(复习)、return(返回)、reject(拒绝)',
        'expl': 're-表示"回、再、重新"，如review=re(再)+view(看)=复习。注意reject是"扔回去"→拒绝，不是"再扔"'
    },
    {
        'prefix': 'inter-',
        'meaning': '在...之间/相互',
        'distractors': ['在...内部/在内', '在...外部/额外', '在...下面/次级'],
        'examples': 'international(国际的)、interact(互动)、internet(互联网)',
        'expl': 'inter-表示"在...之间"或"相互"，如international=inter(之间)+national(国家的)。辨析：inter-(之间) ↔ intra-(内部)'
    },
    {
        'prefix': 'trans-',
        'meaning': '跨越/穿过/转变',
        'distractors': ['环绕/周围', '反对/相反', '共同/一起'],
        'examples': 'transport(运输)、translate(翻译)、transform(转变)',
        'expl': 'trans-表示"跨越"或"转变"，如transport=trans(跨越)+port(携带)=运输'
    },
    {
        'prefix': '-tion/-sion',
        'meaning': '名词后缀（行为/过程/结果）',
        'distractors': ['形容词后缀（...的）', '动词后缀（使...）', '副词后缀（...地）'],
        'examples': 'education(教育)、decision(决定)、expansion(扩展)',
        'expl': '-tion/-sion将动词变为名词，表示行为、过程或结果，如decide->decision。心法：后缀定词性'
    },
    {
        'prefix': 'dis-',
        'meaning': '否定/相反/分离',
        'distractors': ['共同/一起', '向前/预先', '回/再/重新'],
        'examples': 'disagree(不同意)、discover(发现)、disconnect(断开)',
        'expl': 'dis-表示"否定"或"分离"，如disagree=dis(不)+agree(同意)。辨析：dis-(否定/分离) ↔ de-(向下/去除)'
    },
    {
        'prefix': 'con-/com-',
        'meaning': '共同/一起',
        'distractors': ['分开/离开', '向下/去除', '否定/分离'],
        'examples': 'connect(连接)、combine(结合)、confer(商议)',
        'expl': 'con-/com-表示"共同、一起"（com-用于b/m/p前），如connect=con(共同)+nect(绑定)。辨析：con-(一起) ↔ se-(分开)'
    },
    {
        'prefix': '-ment',
        'meaning': '名词后缀（行为/结果）',
        'distractors': ['形容词后缀（具有...性质的）', '动词后缀（使...化）', '副词后缀（...地）'],
        'examples': 'development(发展)、management(管理)、argument(论点)',
        'expl': '-ment将动词变为名词，表示行为或结果，如develop->development。argument是"论点"，表"人"的是-er/-or/-ist'
    },
    {
        'prefix': 'un-/in-/im-',
        'meaning': '否定前缀（表示"不"或"非"）',
        'distractors': ['方向前缀（表示"向前"）', '空间前缀（表示"在...之间"）', '程度前缀（表示"过度"）'],
        'examples': 'unhappy(不快乐的)、invisible(不可见的)、impossible(不可能的)',
        'expl': 'un-/in-/im-是否定前缀。in-会随词根首字母同化：接b/m/p→im-，接l→il-，接r→ir-，如irreversible(不可逆的)'
    },
    {
        'prefix': 'over-',
        'meaning': '过度/超过',
        'distractors': ['不足/低于', '在...之间/相互', '反对/对抗'],
        'examples': 'overcome(克服)、overload(超载)、overlook(忽视)',
        'expl': 'over-表示"过度"或"超过"，如overload=over(过度)+load(负载)=超载。辨析：over-(过度) ↔ under-(不足)'
    },
    {
        'prefix': 'mis-',
        'meaning': '错误/误',
        'distractors': ['否定/相反', '回/再/重新', '共同/一起'],
        'examples': 'mistake(错误)、misunderstand(误解)、mislead(误导)',
        'expl': 'mis-表示"错误"，如misunderstand=mis(误)+understand(理解)=误解。辨析：mis-(错误) ≠ dis-(否定)'
    },
    {
        'prefix': '-ful/-less',
        'meaning': '-ful表"充满...的"，-less表"缺少...的"，二者互为反义',
        'distractors': [
            '二者含义相同，都表"充满...的"',
            '-ful是名词后缀，-less是动词后缀',
            '二者都是副词后缀，表"...地"',
        ],
        'examples': 'careful(小心的)/careless(粗心的)、hopeful(有希望的)/hopeless(绝望的)',
        'expl': '-ful和-less是一对反义后缀：-ful=充满...的，-less=没有...的，如groundless=毫无根据的'
    },
]

for i, d in enumerate(eng_data):
    qid = f'Q-ENG-VOC-01-03-{i+1:04d}'

    options = [d['meaning']] + d['distractors'][:3]

    # 质量闸门：选项形态必须一致，否则正确答案会被格式暴露
    # （标签形如 re-(再/重新) 或 -ful(充满...的)，是"字母+连字符"紧接括号；
    #   中文释义里的全角括号如 "形容词后缀（...的）" 不算标签，应放行）
    assert len(options) == 4, f'{qid} 选项数不为 4'
    assert len(set(options)) == 4, f'{qid} 存在重复选项'
    assert not any(AFFIX_LABEL_RE.match(o) for o in options), \
        f'{qid} 选项里混入了"词缀(释义)"标签，格式会泄露答案'

    stem = f'词缀 "{d["prefix"]}" 的核心含义是？'

    random.seed(42 + i)
    indices = list(range(len(options)))
    random.shuffle(indices)
    shuffled = [options[j] for j in indices]
    correct_pos = indices.index(0)

    eng_count += insert_q(qid, 'ENG-VOC-01-03', 'choice', 0.4, 'AI生成', {
        'stem': stem,
        'options': shuffled,
        'answer': correct_pos,
        'explanation': d['expl'] + f' 例词：{d["examples"]}',
        'tags': ['词根词缀', '英语词汇']
    })

print(f"Task 1.2: Inserted {eng_count} English affix questions")


# ===========================
# TASK 1.3: 10 Cross-discipline Questions
# ===========================
cross_count = 0

cross_data = [
    {
        'id': 'Q-408-CROSS-0001',
        'topic_id': '408-CO-03',
        'content': {
            'stem': '在Cache与虚拟内存联动机制中，当CPU访问的虚拟地址对应的页不在物理内存中时(缺页)，以下描述正确的是？',
            'options': ['Cache直接返回缺页数据', '先处理缺页中断将页调入内存，然后再查Cache', 'Cache和缺页处理完全独立，互不影响', 'TLB命中则不会发生缺页'],
            'answer': 1,
            'explanation': '缺页时需要先将页面从磁盘调入物理内存（缺页中断处理），更新页表和TLB后，CPU才能正确访问该数据。如果Cache使用虚拟地址标记(VI)，可能在页面换入后Cache中无该数据。',
            'tags': ['跨科', '综合', 'Cache', '虚拟内存', '计组', '操作系统']
        }
    },
    {
        'id': 'Q-408-CROSS-0002',
        'topic_id': '408-OS-02',
        'content': {
            'stem': '中断机制与进程管理的关联中，以下说法正确的是？',
            'options': ['中断发生时CPU切换到用户态处理', '外部中断(如I/O完成)可能唤醒阻塞态进程', '中断向量表由用户进程维护', '中断处理不需要保存进程上下文'],
            'answer': 1,
            'explanation': '外部中断(如I/O完成中断)表示某个I/O操作完成，此时操作系统会检查是否有进程正在等待该I/O，若有则将进程从阻塞态唤醒。中断发生时CPU进入核心态，中断向量表由系统维护，且必须保存被中断进程的上下文。',
            'tags': ['跨科', '综合', '中断', '进程管理', '计组', '操作系统']
        }
    },
    {
        'id': 'Q-408-CROSS-0003',
        # 王道 8 章体系：B树/B+树属第7章「查找」（原 408-DS-06 是旧的 7 章编号）
        'topic_id': '408-DS-07',
        'content': {
            'stem': '文件系统中使用的B+树索引与数据结构中的B树相比，以下说法正确的是？',
            'options': ['B+树的所有节点都存储数据记录', 'B+树只有叶子节点存储数据记录，内部节点仅做索引', 'B+树的查找效率不如B树', 'B+树不支持顺序访问'],
            'answer': 1,
            'explanation': 'B+树的特点是只有叶子节点存储实际数据(或数据指针)，内部节点仅存储索引键值。叶子节点之间用链表连接，支持高效的顺序扫描。文件系统的目录索引常用B+树结构。',
            'tags': ['跨科', '综合', 'B+树', '文件系统', '数据结构', '操作系统']
        }
    },
    {
        'id': 'Q-408-CROSS-0004',
        'topic_id': '408-OS-01',
        'content': {
            'stem': 'TCP/IP协议栈中，OS的网络层(如Linux的IP层)与计网的网络层对应。以下关于网络层+OS的描述正确的是？',
            'options': ['IP分组转发不需要OS参与', 'OS的网络协议栈在内核态运行，网络中断由网卡硬件触发', 'TCP连接建立完全由应用层完成', 'OS不为网络数据分配缓冲区'],
            'answer': 1,
            'explanation': 'OS的网络协议栈运行在内核态，网卡接收到数据后触发硬件中断，OS中断处理程序将数据送入协议栈处理。IP分组转发需要OS路由表支持，TCP连接建立由传输层（内核）完成，OS需要为网络数据分配SKB(socket buffer)。',
            'tags': ['跨科', '综合', '网络协议栈', '操作系统', '计算机网络']
        }
    },
    {
        'id': 'Q-408-CROSS-0005',
        'topic_id': '408-DS-03',
        'content': {
            'stem': 'OS中的缓冲区管理(如I/O缓冲区)与数据结构中的队列密切相关。以下关于缓冲队列的说法正确的是？',
            'options': ['单缓冲区方案中，生产者和消费者可以完全并行', '双缓冲区方案使用两个缓冲区交替使用，减少了等待时间', '循环缓冲区不使用队列的概念', '缓冲区大小对系统性能没有影响'],
            'answer': 1,
            'explanation': '双缓冲(Double Buffering)方案使用两个缓冲区，当进程向一个缓冲区写数据时，另一个缓冲区的数据可以被读取，两者交替使用。这比单缓冲减少了等待时间，体现了队列/缓冲区在I/O管理中的重要性。',
            'tags': ['跨科', '综合', '缓冲区', '队列', '数据结构', '操作系统']
        }
    },
    {
        'id': 'Q-408-CROSS-0006',
        # 王道 7 章体系：DMA 属第7章「输入/输出系统」
        'topic_id': '408-CO-07',
        'content': {
            'stem': 'DMA(Direct Memory Access)与OS的I/O管理配合工作时，以下描述正确的是？',
            'options': ['DMA传输不需要CPU参与任何环节', 'DMA在数据传输阶段直接访问内存，传输完成后通过中断通知CPU', 'DMA传输过程中CPU必须停止所有操作', 'DMA只能用于磁盘I/O，不能用于网络'],
            'answer': 1,
            'explanation': 'DMA在数据传输阶段直接与内存交换数据，不经过CPU。传输完成后DMA控制器发出中断信号，CPU的中断处理程序通知OS进行后续操作(如唤醒等待I/O的进程)。DMA初始化时仍需CPU设置参数。',
            'tags': ['跨科', '综合', 'DMA', 'I/O管理', '计组', '操作系统']
        }
    },
    {
        'id': 'Q-408-CROSS-0007',
        'topic_id': '408-CO-03',
        'content': {
            'stem': 'TLB(快表)、页表和Cache的三级查找中，以下关于地址转换过程的描述正确的是？',
            'options': ['先查Cache，再查TLB，最后查页表', '先查TLB，命中则直接获得物理地址；未命中查页表获得物理页框号，再用物理地址查Cache', 'TLB和页表都在Cache中存储', 'TLB未命中就意味着Cache一定未命中'],
            'answer': 1,
            'explanation': '地址转换顺序：1)用VPN查TLB，命中->得到PFN->组成物理地址->查Cache；2)TLB未命中->查页表得到PFN->更新TLB->用物理地址查Cache。TLB使用虚拟页号索引，Cache可以使用物理或虚拟地址标记。',
            'tags': ['跨科', '综合', 'TLB', '页表', 'Cache', '计组', '操作系统']
        }
    },
    {
        'id': 'Q-408-CROSS-0008',
        'topic_id': '408-CO-05',
        'content': {
            'stem': '指令流水线与指令系统的关联中，RISC相比CISC更有利于流水线执行的原因是？',
            'options': ['RISC指令数量更多', 'RISC指令长度固定、执行时间相近，便于流水线各级均匀划分', 'RISC不需要寄存器', 'RISC只有一条指令'],
            'answer': 1,
            'explanation': 'RISC指令集的特点是：指令长度固定、寻址方式简单、大部分指令在一个时钟周期完成。这使得流水线的取指、译码、执行等各阶段时间均衡，减少流水线气泡，提高吞吐率。',
            'tags': ['跨科', '综合', '流水线', '指令系统', 'RISC', '计组']
        }
    },
    {
        'id': 'Q-408-CROSS-0009',
        'topic_id': '408-OS-01',
        'content': {
            'stem': 'Socket编程中，TCP三次握手与OS进程状态的关系，以下说法正确的是？',
            'options': ['TCP握手期间进程始终处于运行态', '调用connect()后进程可能进入阻塞态等待握手完成', 'TCP握手由应用层直接完成，不经过OS', '三次握手失败时进程会自动转为僵尸进程'],
            'answer': 1,
            'explanation': '在TCP客户端调用connect()后，OS内核发起三次握手，此时进程通常进入阻塞态(等待连接建立)。握手完成后进程被唤醒回到就绪态。TCP握手由OS的传输层内核代码完成，应用层只是触发。',
            'tags': ['跨科', '综合', 'Socket', 'TCP', '进程状态', '计算机网络', '操作系统']
        }
    },
    {
        'id': 'Q-408-CROSS-0010',
        'topic_id': '408-OS-04',
        'content': {
            'stem': '磁盘调度算法(如SCAN、C-SCAN)与文件系统的连续分配方式配合时，以下说法正确的是？',
            'options': ['连续分配方式不需要磁盘调度', '连续分配使文件的物理块相邻，SCAN算法能最大化顺序读的性能', 'C-SCAN比SCAN更适合连续分配', '磁盘调度只与磁头移动有关，与文件分配方式完全无关'],
            'answer': 1,
            'explanation': '连续分配的文件在磁盘上物理相邻，使用SCAN/C-SCAN等调度算法按磁道顺序访问时，磁头移动距离最小化，顺序读性能最优。文件分配方式影响磁盘请求的分布，与调度算法协同优化I/O性能。',
            'tags': ['跨科', '综合', '磁盘调度', '文件系统', '操作系统']
        }
    },
]

for q in cross_data:
    cross_count += insert_q(q['id'], q['topic_id'], 'choice', 0.7, 'AI生成', q['content'])

print(f"Task 1.3: Inserted {cross_count} cross-discipline questions")

conn.commit()

# Verify
c.execute('SELECT COUNT(*) FROM questions')
total_q = c.fetchone()[0]
c.execute('SELECT COUNT(*) FROM cards')
total_c = c.fetchone()[0]
print(f"\nFinal database counts: {total_q} questions, {total_c} cards")

conn.close()
print("All done!")

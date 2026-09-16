#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一次性补丁：把 insert_questions.py 的 eng_data 换成"选项同形态"版本。

原数据里 wrong 字段存的是 "re-(再/重新)" 这种带词缀标签的字符串，
options = [meaning] + wrong[:3] 会让正确答案成为唯一没有标签的裸释义
→ 格式本身泄露答案（学生不看词义就能 100% 选对）。

新数据里 distractors 全是裸中文释义，与 meaning 同形态，且取自同语义场的
易混词缀；建选项时用 assert 卡住形态，防止以后改数据时再次退化。
"""
import io
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SRC = Path(r"C:\Users\92534\Desktop\考研\src\insert_questions.py")

NEW_BLOCK = '''eng_data = [
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
    assert len(options) == 4, f'{qid} 选项数不为 4'
    assert len(set(options)) == 4, f'{qid} 存在重复选项'
    assert not any('(' in o or '（' in o and ('-' in o) for o in options), \\
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

print(f"Task 1.2: Inserted {eng_count} English affix questions")'''


def main():
    text = SRC.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines) if l.startswith("eng_data = ["))
    end = next(i for i, l in enumerate(lines) if "Task 1.2: Inserted" in l)
    out = "".join(lines[:start]) + NEW_BLOCK + "\n" + "".join(lines[end + 1:])
    SRC.write_text(out, encoding="utf-8")
    print(f"已替换 insert_questions.py 第 {start + 1}–{end + 1} 行（eng_data → 同形态选项版）")


if __name__ == "__main__":
    main()

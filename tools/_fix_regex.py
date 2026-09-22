# -*- coding: utf-8 -*-
"""一次性修复：FLASH_JS 里 richText 的正则被 Python 把 \\n 吃成了真换行。

源码是普通三引号字符串（不是 raw），所以文件里写 `\n` 会被 Python 解析成换行，
正则字面量被劈成两行 → 整个 <script> 语法错误 → 大盘全废。
要的是运行时的 `\n`，文件里必须写成 `\\n`。
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BS = chr(92)          # 一个反斜杠。用 chr() 构造，免得在多层转义里迷路

P = r"E:\NPEE\src\generate_dashboard.py"
s = io.open(P, encoding="utf-8").read()

old = "[^$" + BS + "n]+?"
new = "[^$" + BS + BS + "n]+?"

n = s.count(old)
print("找到 %d 处 [^$<BS>n] 形式" % n)
if n != 1:
    print("期望恰好 1 处，实际 %d，放弃自动修复" % n)
    sys.exit(1)

io.open(P, "w", encoding="utf-8").write(s.replace(old, new))
print("已写入。现在文件里是 [^$" + BS + BS + "n]+?")

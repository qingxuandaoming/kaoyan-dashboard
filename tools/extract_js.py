"""从 generate_dashboard.py 里抠一段 JS 字面量，写进指定文件。

用法：python tools/extract_js.py <变量名> <输出文件>

为什么要抽成公共脚本：页面里所有 JS 都挤在**同一个 <script>** 里，而且有先后
依赖——DAY_START_JS 定义的 studyDay() / DAY_START_HOUR 是 POMO_JS、FLASH_JS、
SHELL_JS 都要用的。测试如果只单独抠出一个变量去跑，拿到的就是 ReferenceError
（2026-09-17 加学习日起点时，三个测试文件同时挂在这上面）。

所以这里把「排在它前面的基础段」一并带上，顺序跟 generate_dashboard.py 里
replace 的顺序一致。以后再加新的公共前置段，只要往 PRELUDE 里加名字。
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "generate_dashboard.py")

# 注入在脚本块最前面的公共段，取值时一并带上（顺序即注入顺序）
#
# KEYS_JS（快捷键总表，2026-09-17）也在这里：FLASH_JS 的键盘段现在问的是
# __keys.matches("flash.undo", e)，不再自己比 ev.key。少带它，抽 FLASH_JS
# 单独跑就会在按第一个键时炸 undefined。
PRELUDE = ["DAY_START_JS", "KEYS_JS"]
# PRELUDE 里的段引用到的常量，也要一并求值
NEEDED_CONSTS = ["DAY_START_HOUR"]


def load_consts(src, names):
    """把 names 里的模块级常量求出来。

    只对白名单里的名字求值，别的一律跳过：文件里还有 Path(...) / date(...)
    这类调用，既没法 literal_eval、也不该在这里跑。eval 时清空 builtins，
    保证求值过程只能用已经拿到的常量（就是 DAY_START_HOUR 这种）。
    """
    ns = {}
    tree = ast.parse(src)
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in names:
            continue
        try:
            ns[target.id] = eval(  # noqa: S307 —— 自己的源码，且 builtins 已清空
                compile(ast.Expression(node.value), "<const>", "eval"),
                {"__builtins__": {}}, ns)
        except Exception:
            pass
    return ns


def main():
    if len(sys.argv) != 3:
        sys.stderr.write(__doc__)
        return 2
    want, out = sys.argv[1], sys.argv[2]
    ns = load_consts(io.open(SRC, encoding="utf-8").read(),
                     set(PRELUDE) | set(NEEDED_CONSTS) | {want})
    if want not in ns:
        sys.stderr.write("generate_dashboard.py 里找不到 %s\n" % want)
        return 1
    parts = [ns[k] for k in PRELUDE if k in ns] + [ns[want]]
    io.open(out, "w", encoding="utf-8").write("\n".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())

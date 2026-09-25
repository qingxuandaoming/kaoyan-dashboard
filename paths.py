# -*- coding: utf-8 -*-
r"""paths.py —— 路径的单一事实源（2026-09-25 代码/笔记分离后建立）。

背景：迁移前代码在 E:\NPEE\src、笔记在 E:\NPEE，两者用「src 的上级目录」隐式绑定，
导致 ~30 个文件各自硬编码 E:\NPEE 或 E:\NPEE\src。分离后代码根 = E:\Project\kaoyan-dashboard、
笔记根 = E:\NPEE，本模块把两个根收敛到一处：

- ``SRC_DIR``   代码根下的 src 目录（本文件所在目录，按 __file__ 推导，永远正确）
- ``CODE_ROOT`` 项目根（SRC_DIR 的上级；app/ 与 assets/ 在这层）
- ``NOTES_ROOT``笔记库根，解析顺序：环境变量 NOTES_ROOT > src/paths.json 的 notes_root > 内置默认

用法（src/ 顶层模块）::

    import paths
    ROOT = paths.NOTES_ROOT          # 笔记库根（数学/408/… 与 Review/Schedule 在这下面）
    DB   = os.path.join(paths.SRC_DIR, "question_bank.db")   # 代码自身产物

用法（src/tools/ 下的脚本，需先把 src 加进 sys.path）::

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import paths

⚠️ 别再在新代码里写 r"E:\NPEE" 或 __file__ 上溯两级当笔记根——统一从这里拿。
   JS 侧同源实现见 paths.js；两处默认值必须一致（tools/check_metrics_drift.py 会对拍）。
"""
import json
import os

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
CODE_ROOT = os.path.dirname(SRC_DIR)

#: 内置默认笔记根（paths.json 缺失或不可读时的兜底；与 paths.js 保持一致）
DEFAULT_NOTES_ROOT = r"E:\NPEE"

_CONFIG_PATH = os.path.join(SRC_DIR, "paths.json")


def _read_config_notes_root():
    """从 src/paths.json 读 notes_root；读不到/格式错都回退 None（交给内置默认）。"""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            val = json.load(fh).get("notes_root")
    except (OSError, ValueError):
        return None
    val = str(val).strip() if val else ""
    return os.path.abspath(val) if val else None


def resolve_notes_root():
    """env NOTES_ROOT > src/paths.json > 内置默认。"""
    env = os.environ.get("NOTES_ROOT")
    if env and env.strip():
        return os.path.abspath(env.strip())
    return _read_config_notes_root() or DEFAULT_NOTES_ROOT


#: 笔记库根（Math/408/English/Politics/Review/Schedule/… 都在这下面）
NOTES_ROOT = resolve_notes_root()


def note_path(*parts):
    """笔记根下的路径拼接：note_path("Math", "高数") -> <NOTES_ROOT>\Math\高数"""
    return os.path.join(NOTES_ROOT, *parts)


def src_path(*parts):
    """代码根（src/）下的路径拼接：src_path("question_bank.db")"""
    return os.path.join(SRC_DIR, *parts)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("SRC_DIR    =", SRC_DIR)
    print("CODE_ROOT  =", CODE_ROOT)
    print("NOTES_ROOT =", NOTES_ROOT)
    print("来源       =", "env NOTES_ROOT" if os.environ.get("NOTES_ROOT")
          else ("paths.json" if _read_config_notes_root() else "内置默认"))

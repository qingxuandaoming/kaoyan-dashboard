#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_shortcut.py — 给桌面上的「启动考研大盘.bat」加一个带图标的快捷方式

为什么要绕这一道：Windows 的 .bat 本身**不能**带图标，双击时永远是那个默认的
齿轮/文档图标。唯一办法是做一个指向它的 .lnk，把 IconLocation 指到我们的 .ico。
原 .bat 保持不动——快捷方式不依赖它的位置，你可以随时把它挪走或删掉。

图标由 tools/make_icon.py 生成；图形定义在 tools/icon_design.py。

用法：
    python src/tools/make_shortcut.py                 # 建/更新桌面快捷方式
    python src/tools/make_shortcut.py --hide-bat      # 顺便把原 .bat 设为隐藏
    python src/tools/make_shortcut.py --dry-run       # 只说要做什么
"""

import argparse
import io
import os
import subprocess
import sys

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(SRC_DIR))          # 考研/
ICON_PATH = os.path.join(ROOT, "assets", "kaoyan.ico")

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
BAT_PATH = os.path.join(DESKTOP, "启动考研大盘.bat")
LNK_PATH = os.path.join(DESKTOP, "启动考研大盘.lnk")
LNK_DESC = "改造我们的学习 · 一键启动"


def build_ps1():
    """生成建快捷方式的 PowerShell。

    ⚠️ 中文路径必须靠 UTF-8 BOM 才能让 PowerShell 5.1 正确读进来——它不带 BOM
    时会按系统 ANSI（这里是 GBK）解析脚本文件，中文直接变乱码，快捷方式就会
    指向一个不存在的路径。
    """
    q = lambda p: '"' + p.replace('"', '""') + '"'      # noqa: E731
    return "\n".join([
        '$ErrorActionPreference = "Stop"',
        '$ws = New-Object -ComObject WScript.Shell',
        '$sc = $ws.CreateShortcut(%s)' % q(LNK_PATH),
        '$sc.TargetPath = %s' % q(BAT_PATH),
        '$sc.WorkingDirectory = %s' % q(DESKTOP),
        '$sc.IconLocation = %s' % q(ICON_PATH + ",0"),
        '$sc.Description = %s' % q(LNK_DESC),
        '$sc.Save()',
        'if (-not (Test-Path -LiteralPath %s)) { throw "快捷方式没建成" }' % q(LNK_PATH),
        'Write-Output "OK"',
    ])


def run_ps1(script):
    tmp = os.path.join(SRC_DIR, "_mkshortcut.ps1")
    with io.open(tmp, "w", encoding="utf-8-sig") as f:      # BOM 见 build_ps1 注释
        f.write(script)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tmp],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return r


def set_hidden():
    r = subprocess.run(
        ["attrib", "+H", BAT_PATH], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hide-bat", action="store_true",
                    help="把原 .bat 设为隐藏，桌面上只留快捷方式")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    problems = []
    if not os.path.exists(BAT_PATH):
        problems.append("找不到启动脚本：%s" % BAT_PATH)
    if not os.path.exists(ICON_PATH):
        problems.append("找不到图标：%s（先跑 python src/tools/make_icon.py）" % ICON_PATH)
    if problems:
        for p in problems:
            print("[FAIL] " + p)
        return 1

    print("目标脚本 : %s" % BAT_PATH)
    print("图标     : %s" % ICON_PATH)
    print("快捷方式 : %s" % LNK_PATH)

    if args.dry_run:
        print("\n--dry-run，未做任何改动。将执行的 PowerShell：\n")
        print(build_ps1())
        return 0

    r = run_ps1(build_ps1())
    if r.returncode != 0 or "OK" not in (r.stdout or ""):
        print("[FAIL] PowerShell 报错：")
        print((r.stderr or r.stdout or "").strip())
        return 1
    print("\n[OK] 快捷方式已创建/更新")

    if args.hide_bat:
        if set_hidden():
            print("[OK] 原 .bat 已设为隐藏（资源管理器开「显示隐藏文件」时仍可见）")
        else:
            print("[WARN] 隐藏 .bat 失败，可手动右键 → 属性 → 隐藏")
    else:
        print("\n注意：桌面现在有两个入口（.bat 和 .lnk）。快捷方式不依赖 .bat 的位置，")
        print("      想清爽的话可以 `--hide-bat`，或直接把 .bat 拖进 考研 文件夹。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""launch_dashboard.py —— 考研大盘一键启动（全部逻辑在本脚本，bat 只做 ASCII 桩）。

为什么不让 bat 干活：cmd 对 **UTF-8 编码 / LF 行尾** 的批处理会解析错位——
中文行会被拦腰截断、碎片被当命令执行（现象：终端里 `'◆◆件（最多约' is not
recognized as an internal or external command`）。Python 的中文输出走控制台
Unicode API，任何代码页下都干净，所以启动逻辑整体搬到这里。

流程（比旧 bat 快：浏览器先开，数据后台刷新）：
  [1/3] 本地复习服务：
        · 已在运行（端口文件 + HTTP 探测双确认）→ **直接复用**，不再起第二个实例
          （旧 bat 不检测，重复双击会再起一个、端口一路往后跳：8888→9090→18080）；
        · restart 参数 → 按 .serve_pid 里记录的 PID 结束旧服务再起（用于吃新 serve.js）；
        · 否则起一个最小化窗口，轮询 .serve_port 拿实际端口（不再固定睡 6 秒）。
  [2/3] 打开浏览器：用已存在的 dashboard.html，秒开；
  [3/3] 流水线刷新：默认 run_pipeline.py --no-plan --if-stale（不陈旧就跳过），
        rebuild 参数强制全量。刷新完浏览器按 F5 即是最新。

用法：
    python tools\\launch_dashboard.py              正常启动
    python tools\\launch_dashboard.py rebuild      强制重建大盘数据
    python tools\\launch_dashboard.py restart      结束旧服务并重启
    python tools\\launch_dashboard.py --no-open    不打开浏览器（探针/测试用）
"""
import io
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paths  # 路径单一事实源（本脚本不写任何绝对根路径）

SRC = paths.SRC_DIR
SERVE_JS = os.path.join(SRC, "serve.js")
RUN_PIPELINE = os.path.join(SRC, "run_pipeline.py")
DASHBOARD = os.path.join(SRC, "dashboard.html")
PORT_FILE = os.path.join(SRC, ".serve_port")
PID_FILE = os.path.join(SRC, ".serve_pid")

NODE_CANDIDATE = r"C:\Program Files\nodejs\node.exe"
TASKKILL = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                        "System32", "taskkill.exe")

WAIT_PORT_SECONDS = 20.0
PROBE_TIMEOUT = 1.5


def log(msg=""):
    print(msg, flush=True)


def set_console_title(title):
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass


def read_port():
    try:
        txt = Path(PORT_FILE).read_text(encoding="utf-8").strip()
        port = int(txt)
        return port if 0 < port < 65536 else None
    except Exception:
        return None


def read_pid():
    try:
        txt = Path(PID_FILE).read_text(encoding="utf-8").strip()
        pid = int(txt)
        return pid if pid > 0 else None
    except Exception:
        return None


def probe_alive(port):
    """HTTP 探测：端口文件在不代表服务活着（进程可能已崩）。"""
    if not port:
        return False
    url = f"http://127.0.0.1:{port}/"
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        # 能回 HTTP 就说明服务在（哪怕某个路径 404/403）
        return e.code < 500
    except Exception:
        return False


def pid_running(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pid_by_port(port):
    """.serve_pid 缺失时的兜底（旧版服务没写过它）：从 netstat 找监听该端口的 PID。"""
    if not port:
        return None
    netstat = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                           "System32", "netstat.exe")
    if not os.path.exists(netstat):
        return None
    try:
        out = subprocess.run([netstat, "-ano"], capture_output=True,
                             timeout=10, text=True, errors="replace").stdout
    except Exception:
        return None
    needle = f":{port}"
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        # 形如: TCP  0.0.0.0:18080  0.0.0.0:0  LISTENING  40264
        if len(parts) >= 5 and parts[1].endswith(needle):
            try:
                return int(parts[-1])
            except ValueError:
                return None
    return None


def kill_service(pid):
    tk = TASKKILL if os.path.exists(TASKKILL) else "taskkill"
    try:
        subprocess.run([tk, "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=15)
        return True
    except Exception as e:
        log(f"[!] 结束旧服务失败（PID {pid}）：{e}")
        return False


def node_exe():
    return NODE_CANDIDATE if os.path.exists(NODE_CANDIDATE) else "node"


def start_service():
    """起一个最小化的独立控制台窗口跑 serve.js（与旧 bat 同形态）。"""
    node = node_exe()
    cmd = ["cmd", "/c", "start", "\"考研复习服务\"", "/MIN",
           f'"{node}"', f'"{SERVE_JS}"']
    subprocess.Popen(cmd, cwd=SRC)


def wait_port(deadline_s=WAIT_PORT_SECONDS):
    t0 = time.time()
    while time.time() - t0 < deadline_s:
        port = read_port()
        if port:
            return port
        time.sleep(0.5)
    return None


def ensure_service(want_restart):
    """返回 (port, reused:bool)。失败返回 (None, False)。"""
    port = read_port()
    alive = probe_alive(port)
    pid = read_pid()
    if alive and (not pid or not pid_running(pid)):
        pid = pid_by_port(port)  # 旧版服务没写 .serve_pid 时的兜底

    if alive and not want_restart:
        log(f"  服务已在运行（端口 {port}，PID {pid or '?'}）→ 直接复用，")
        log("  不再起第二个实例。要重启吃新代码请用：启动考研大盘.bat restart")
        return port, True

    if alive and want_restart:
        if not pid or not pid_running(pid):
            log("  [!] 服务在跑但找不到它的 PID（.serve_pid 与 netstat 都没有）。")
            log("      请手动关掉标题为「考研复习服务」的窗口后再试。")
            return None, False
        log(f"  restart：结束旧服务（PID {pid}）…")
        if not kill_service(pid):
            return None, False
        for _ in range(20):
            if not probe_alive(port):
                break
            time.sleep(0.25)
        port = None
        alive = False

    if alive:
        return port, True

    # 起新服务：先清掉旧端口文件，避免轮询读到上一次的端口
    try:
        os.remove(PORT_FILE)
    except OSError:
        pass
    log("  启动本地复习服务（最小化窗口「考研复习服务」）…")
    start_service()
    port = wait_port()
    if not port:
        log("  [!] 服务没能写出端口文件。请看「考研复习服务」窗口里的提示")
        log("      （常见：端口全被占/保留段；窗口 30 秒后自关，方便复制信息）。")
        return None, False
    # 端口文件写出了但 HTTP 还没就绪的话，再等一小会儿
    for _ in range(10):
        if probe_alive(port):
            break
        time.sleep(0.3)
    return port, False


def open_browser(port):
    url = f"http://localhost:{port}/dashboard.html"
    log(f"  打开浏览器：{url}")
    try:
        os.startfile(url)  # noqa: SLF001 - Windows only
        return True
    except Exception as e:
        log(f"  [!] 打开浏览器失败：{e}（手动访问上面的地址即可）")
        return False


def run_pipeline(force):
    args = [sys.executable, RUN_PIPELINE, "--no-plan"]
    if not force:
        args.append("--if-stale")
    log("")
    proc = subprocess.run(args, cwd=SRC)
    return proc.returncode


def main(argv):
    force = any(a.lower() == "rebuild" for a in argv)
    want_restart = any(a.lower() == "restart" for a in argv)
    no_open = "--no-open" in argv

    set_console_title("考研大盘一键启动")
    log("================================================")
    log("  考研学习仪表盘 · 一键启动")
    log("================================================")
    log("")

    log("[1/3] 本地复习服务（闪卡练习需要）…")
    port, reused = ensure_service(want_restart)
    if not port:
        return 1
    log(f"  服务端口：{port}")
    log("")

    have_dashboard = os.path.exists(DASHBOARD)
    if have_dashboard:
        log("[2/3] 打开大盘（先开页面，数据刷新在后面）…")
        if not no_open:
            open_browser(port)
        else:
            log("  --no-open：跳过打开浏览器")
        log("")
        log("[3/3] 刷新笔记数据与大盘…")
        rc = run_pipeline(force)
        if rc != 0:
            log("")
            log("[提示] 流水线有报错；浏览器里仍是上一次构建好的大盘，不影响使用。")
        else:
            log("")
            log("完成！浏览器里按 F5 即可看到最新数据。")
    else:
        log("[2/3] 还没有 dashboard.html，先跑流水线生成…")
        rc = run_pipeline(True)
        log("")
        log("[3/3] 打开大盘…")
        if rc == 0 and not no_open:
            open_browser(port)
        elif rc != 0:
            log("[!] 流水线失败，大盘没生成；见上面的报错。")
            return 1
        log("")
        log("完成！")

    if not no_open:
        log("（本窗口 3 秒后自动关闭）")
        time.sleep(3)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)

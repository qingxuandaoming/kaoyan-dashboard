@echo off
chcp 65001 >nul
title 改造我们的学习 · 一键启动

echo ================================================
echo   改造我们的学习 · 一键启动
echo ================================================
echo.

rem 注意：这个脚本里不要写带中文的括号块，行尾必须是 CRLF。
rem cmd 在 65001 代码页下对多字节字符很敏感，括号块 + LF 行尾都会让整段错位。

rem node / python 优先用绝对路径，PATH 里没有也能跑
set "NODE_EXE=node"
if exist "C:\Program Files\nodejs\node.exe" set "NODE_EXE=C:\Program Files\nodejs\node.exe"
set "PY_EXE=python"
if exist "C:\Users\92534\AppData\Local\Programs\Python\Python311\python.exe" set "PY_EXE=C:\Users\92534\AppData\Local\Programs\Python\Python311\python.exe"

echo [1/3] 刷新笔记数据与大盘...
"%PY_EXE%" "E:\NPEE\src\run_pipeline.py" --no-plan
if errorlevel 1 echo [提示] 流水线有报错，仍尝试打开大盘...

echo.
echo [2/3] 启动本地复习服务（闪卡练习需要）...
set "PORTFILE=E:\NPEE\src\.serve_port"
del "%PORTFILE%" >nul 2>&1
start "考研复习服务" /MIN cmd /c ""%NODE_EXE%" "E:\NPEE\src\serve.js""
rem 8080 可能落在 Windows 保留端口段里，serve.js 会自动换端口，
rem 所以等它把实际端口写进文件（最多约 10 秒）
ping 127.0.0.1 -n 7 >nul
set "PORT="
if exist "%PORTFILE%" set /p PORT=<"%PORTFILE%"
if defined PORT goto open
echo.
echo [!] 服务没能写出端口文件，请看标题为「考研复习服务」的那个窗口里的提示。
echo     按任意键后，本窗口会用 8080 试着打开大盘。
pause >nul
set "PORT=8080"

:open
echo 服务端口：%PORT%
echo [3/3] 打开大盘...
start "" "http://localhost:%PORT%/dashboard.html"

echo.
echo 完成！如需停止服务，关闭「考研复习服务」窗口即可。
echo （本窗口 3 秒后自动关闭）
ping 127.0.0.1 -n 4 >nul
exit /b 0

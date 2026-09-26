@echo off
rem ============================================================
rem  ASCII-only stub ON PURPOSE. cmd corrupts UTF-8 or LF batch
rem  files: Chinese lines get split mid-character and the
rem  fragments then run as commands, printing errors like
rem  "'xxx' is not recognized as an internal or external command".
rem  Keep this file ASCII + CRLF forever. All logic and all
rem  Chinese output live in src\tools\launch_dashboard.py
rem  (console Unicode API, clean under any code page).
rem  Args passed through: rebuild | restart | --no-open
rem ============================================================
set "PY_EXE=python"
if exist "C:\Users\92534\AppData\Local\Programs\Python\Python311\python.exe" set "PY_EXE=C:\Users\92534\AppData\Local\Programs\Python\Python311\python.exe"
"%PY_EXE%" "%~dp0tools\launch_dashboard.py" %*
if errorlevel 1 pause

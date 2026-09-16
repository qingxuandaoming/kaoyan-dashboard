@echo off
setlocal

cd /d "%~dp0"

set "PORT=8080"

echo [INFO] Checking Node.js...
node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js not found.
    echo [ERROR] Please install from https://nodejs.org/
    pause
    exit /b 1
)
for /f "tokens=*" %%a in ('node --version') do echo [OK] Node.js %%a

echo [INFO] Checking port %PORT%...
netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo [INFO] Server already running on port %PORT%.
    goto OPEN
)

echo [INFO] Starting server...
start /min cmd /c "cd /d "%~dp0" && node serve.js"

echo [INFO] Waiting 5 seconds...
timeout /t 5 /nobreak >nul

echo [INFO] Verifying server...
netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul
if errorlevel 1 (
    echo [ERROR] Server failed to start.
    echo [ERROR] Please run manually:
    echo.
    echo    cd "%~dp0"
    echo    node serve.js
    echo.
    pause
    exit /b 1
)

echo [OK] Server started.

:OPEN
echo [INFO] Opening browser...
start http://localhost:%PORT%

echo.
echo =========================================
echo    Morning Review Server Ready
echo =========================================
echo  http://localhost:%PORT%
echo =========================================
echo.
echo Press any key to close this window...
pause >nul
exit /b 0

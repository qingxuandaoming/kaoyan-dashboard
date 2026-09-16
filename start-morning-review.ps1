# 早间回顾 - PowerShell 启动脚本
# 功能：检测 Node.js -> 启动代理服务器 -> 打开浏览器
# 使用方式：双击 start-ps.bat（推荐），或在 PowerShell 中执行: .\start-morning-review.ps1

param(
    [switch]$NoBrowser
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Port = 8080
$Url = "http://localhost:$Port"

function Write-Status($msg, $type) {
    $color = switch ($type) {
        "ok"    { "Green" }
        "error" { "Red" }
        "warn"  { "Yellow" }
        default { "White" }
    }
    $prefix = switch ($type) {
        "ok"    { "[OK] " }
        "error" { "[ERROR] " }
        "warn"  { "[WARN] " }
        default { "[INFO] " }
    }
    Write-Host "$prefix$msg" -ForegroundColor $color
}

# 1. 检测 Node.js
Write-Status "Checking Node.js..." "info"
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    Write-Status "Node.js not found. Please install from https://nodejs.org/" "error"
    exit 1
}
$nodeVersion = & node --version
Write-Status "Node.js $nodeVersion" "ok"

# 2. 检测端口
Write-Status "Checking port $Port..." "info"
$ns = netstat -ano | Select-String ":$Port\s+.*LISTENING"
if ($ns) {
    Write-Status "Server already running on port $Port." "ok"
    if (-not $NoBrowser) {
        Write-Status "Opening browser..." "info"
        Start-Process $Url
    }
    Write-Status "Server ready. Press Enter to close this window." "ok"
    Read-Host
    exit 0
}

# 3. 启动服务器
Write-Status "Starting server..." "info"
$proc = Start-Process -FilePath "cmd" -ArgumentList "/c", "cd /d `"$ScriptDir`" && node serve.js" -WindowStyle Minimized -PassThru

# 4. 等待启动
Write-Status "Waiting 5 seconds..." "info"
Start-Sleep -Seconds 5

# 5. 验证
$ns2 = netstat -ano | Select-String ":$Port\s+.*LISTENING"
if (-not $ns2) {
    Write-Status "Server failed to start." "error"
    Write-Status "Please run manually:" "warn"
    Write-Host ""
    Write-Host "    cd `"$ScriptDir`""
    Write-Host "    node serve.js"
    Write-Host ""
    Read-Host
    exit 1
}

Write-Status "Server started." "ok"

# 6. 打开浏览器
if (-not $NoBrowser) {
    Write-Status "Opening browser..." "info"
    Start-Process $Url
}

# 7. 显示状态
Write-Host ""
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "    Morning Review Server Ready" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  $Url" -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""
Write-Status "Press Enter to close this window." "info"
Read-Host
exit 0

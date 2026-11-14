# launch_sys.ps1 —— 用「正式版 Chrome」启动扩展（指纹最干净）
# ---------------------------------------------------------------------------
# 为什么不用 Chrome for Testing（CfT）：
#   实测 2026-11-09，两者的 Client Hints 不一样：
#       正式版: brands = "Not:A-Brand/8 | Chromium/156 | Google Chrome/156"
#       CfT   : brands = "Not:A-Brand/8 | Chromium/156"        ← 少了 Google Chrome
#   CfT 会自报成 Chromium，任何查 navigator.userAgentData.brands 的检测点都能看出来。
#
# 但正式版 Chrome 137+ 禁用了 --load-extension，所以扩展只能【手动加载一次】；
# 加载后 profile 会记住，以后启动不用再加任何参数。
#
# 用法：
#   1) 首次：跑本脚本，它会提示你手动加载扩展（约 30 秒）
#   2) 之后：直接跑本脚本即可
param(
    [string]$Url = "https://www.gov.hk/sc/apps/tddiabs.htm",
    [string]$Profile = "chrome_sys"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$extDir = Join-Path $root "extension"
$profDir = Join-Path $root $Profile

$chrome = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) { throw "找不到正式版 Chrome" }

if (-not (Test-Path $profDir)) { New-Item -ItemType Directory -Path $profDir | Out-Null }

# 1) 检查扩展是否已经加载进这个 profile
$extRoot = Join-Path $profDir "Default\Extensions"
$loaded = $false
if (Test-Path $extRoot) {
    $ids = Get-ChildItem $extRoot -Directory -ErrorAction SilentlyContinue
    if ($ids) { $loaded = $true }
}

if (-not $loaded) {
    Write-Host "这个 profile 里还没有扩展，需要手动加载一次（只需一次）。" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  1) 在弹出的窗口里，右上角打开「开发者模式」" -ForegroundColor Cyan
    Write-Host "  2) 点「加载已解压的扩展程序」" -ForegroundColor Cyan
    Write-Host "  3) 选择目录： $extDir" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "加载完关闭那个窗口，再重新运行本脚本。" -ForegroundColor Yellow
    & $chrome "--user-data-dir=$profDir" "--no-first-run" "--no-default-browser-check" "chrome://extensions/"
    exit 1
}

# 2) 本地服务必须在线（扩展靠它拿配置/图片/OTP）
$svcOk = $false
try { $p = Invoke-RestMethod "http://127.0.0.1:8765/ping" -TimeoutSec 3; $svcOk = [bool]$p.ok } catch { }
if (-not $svcOk) {
    Write-Host "⚠ 本地服务没在跑：另开窗口执行 python ext_server.py" -ForegroundColor Yellow
} else {
    Write-Host "本地服务：OK（127.0.0.1:8765）" -ForegroundColor Green
}

# 3) 启动（不加任何自动化/调试参数）
Write-Host "内核：正式版 Chrome（brands 含 Google Chrome）" -ForegroundColor Green
Write-Host "打开：$Url" -ForegroundColor Cyan
Start-Process -FilePath $chrome -ArgumentList @(
    "--user-data-dir=$profDir",
    "--no-first-run",
    "--no-default-browser-check",
    $Url
) | Out-Null

Start-Sleep -Seconds 6
Write-Host "进度： Get-Content runs\ext_status.jsonl -Tail 20" -ForegroundColor Cyan

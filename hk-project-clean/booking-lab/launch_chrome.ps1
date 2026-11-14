# 用「普通方式」启动真 Chrome 并开放调试端口，供 fill_form.py --attach 接管
# 关键点：浏览器本身是用户日常的 Chrome（完整组件、真 GPU、无自动化启动参数），
#         Playwright 只是通过 CDP 连上去操作，不负责启动浏览器。
$ErrorActionPreference = "Stop"

$candidates = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) { throw "没找到 Google Chrome，请先安装或改用 --channel chrome 的自动模式" }

$profileDir = Join-Path $PSScriptRoot "chrome_profile"
$port = 9222

# 端口已被占用就别重复起
$busy = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "端口 $port 已在监听，直接复用。" -ForegroundColor Yellow
} else {
    $args = @(
        "--remote-debugging-port=$port",
        "--user-data-dir=$profileDir",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=CalculateNativeWinOcclusion",
        "about:blank"
    )
    Start-Process -FilePath $chrome -ArgumentList $args | Out-Null
    Write-Host "已启动真 Chrome（独立 profile: $profileDir）" -ForegroundColor Green
}

# 等调试端口起来
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 400
    try {
        $v = Invoke-RestMethod "http://127.0.0.1:$port/json/version" -TimeoutSec 2
        Write-Host "CDP 就绪：$($v.Browser)" -ForegroundColor Green
        Write-Host "接管命令： python fill_form.py --workers 1 --attach http://127.0.0.1:$port"
        exit 0
    } catch { }
}
Write-Host "调试端口没起来，检查是否有其它 Chrome 实例占用了同一个 user-data-dir。" -ForegroundColor Red
exit 1

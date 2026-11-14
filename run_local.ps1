# run_local.ps1 —— 「本地 IP + 单实例 + 全程留痕」一键启动器
# =============================================================================
# 为什么用本地 IP：IPWO 那批香港住宅代理在大陆来源 IP 上被 403
# （"access denied, china IP is not allow"），见 代理_来源IP限制_结论.md。
# 所以这条路走本机家宽出口直连：浏览器不带 --proxy-server，本地服务走 --no-proxy-server。
#
# 做了什么：
#   1) 确保 127.0.0.1:8765 的本地服务是**新代码**（带 /shot 截图接口）
#   2) 打印当前出口 IP（确认不是代理/VPN/机房）
#   3) 起一个 CfT 浏览器（带扩展、无 CDP、无代理）
#   4) 之后每一步：扩展 → 本地服务 → runs/steps/<日期>/*.png + runs/journal/*.jsonl
#
# 用法（明天就这一条）：
#   pwsh -ExecutionPolicy Bypass -File .\run_local.ps1 -RestartService
#   pwsh -ExecutionPolicy Bypass -File .\run_local.ps1 -Instance w1 -RestartService
#
# 只看不进站（自检扩展/截图链路，不消耗任何入队机会）：
#   pwsh -ExecutionPolicy Bypass -File .\run_local.ps1 -DryRun -RestartService

param(
    [string]$Instance = "w1",
    [string]$Url = "https://www.gov.hk/sc/apps/tddiabs.htm",
    [string]$Proxy = "",      # 出口代理，如 http://222.216.120.151:59449（空 = 本机家宽直连）
    [switch]$RestartService,
    [switch]$KeepProfile,     # 保留旧 profile（cookie/WAF token）；默认重建，最稳
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

function Test-Svc {
    try { return [bool](Invoke-RestMethod "http://127.0.0.1:8765/ping" -TimeoutSec 2).ok }
    catch { return $false }
}

# ---------------------------------------------------------------- 0) 旧服务
if ($RestartService) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'ext_server\.py' } |
        ForEach-Object {
            Write-Host "停掉旧服务 pid=$($_.ProcessId)" -ForegroundColor Yellow
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    Start-Sleep -Seconds 1
}

# ---------------------------------------------------------------- 1) 本地服务
# 注意：不能用 Start-Process 起服务 —— 如果本脚本是被某个 job / 后台任务拉起来的，
# 杀掉那个 job 会连带把服务一起收走（2026-11-11 实测：服务静默消失，扩展全部上报失败，
# 现象是「浏览器在跑但一条日志都没有」）。用 WMI 创建进程，父进程是 WmiPrvSE，
# 完全脱离调用方进程树。
if (-not (Test-Svc)) {
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $py) { throw "找不到 python（PATH 里没有）" }
    New-Item -ItemType Directory -Force runs | Out-Null
    $inner = "`"$py`" -X utf8 ext_server.py >> runs\ext_server.log 2>> runs\ext_server.err"
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine      = "cmd.exe /c $inner"
        CurrentDirectory = $root
    }
    if ($r.ReturnValue -ne 0) { throw "启动本地服务失败，ReturnValue=$($r.ReturnValue)" }
    $r.ProcessId | Set-Content (Join-Path $root "runs\ext_server.pid")
    for ($i = 0; $i -lt 25 -and -not (Test-Svc); $i++) { Start-Sleep -Milliseconds 400 }
}
if (-not (Test-Svc)) { throw "本地服务 8765 起不来，看 runs\ext_server.err" }
Write-Host "本地服务 OK：http://127.0.0.1:8765（独立进程，pid=$(Get-Content runs\ext_server.pid -EA SilentlyContinue)）" -ForegroundColor Green

# ---------------------------------------------------------------- 2) 出口 IP
# 注意：PowerShell 的 Invoke-RestMethod 默认走 IE/系统代理。这台机器上
# 127.0.0.1:10809 挂着 v2rayN，会把它当成出口 → 误报成 Cloudflare 荷兰机房。
# 浏览器是 --no-proxy-server 直连的，所以这里必须用 curl.exe（不吃系统代理）。
try {
    if ($Proxy) {
        # 指定代理时，"本机家宽"不是本次出口 —— 必须看代理那一侧的 IP，
        # 否则日志会写着 14.154.x 却实际从 120.229.x 出去，票和出口对不上。
        $raw = ((& curl.exe -s -x $Proxy --max-time 15 "https://myip.ipip.net") -join " ").Trim()
        Write-Host "代理出口（$Proxy）：$raw" -ForegroundColor Cyan
    } else {
        $raw = ((& curl.exe -s --max-time 10 "https://myip.ipip.net") -join " ").Trim()
        Write-Host "直连出口：$raw" -ForegroundColor Cyan
    }
    if (-not $raw) { $raw = ((& curl.exe -s --max-time 10 "http://ip.3322.net") -join " ").Trim() }
    if ($raw -notmatch "中国") {
        Write-Host "⚠ 出口不在中国内地 —— 若开了 VPN/WARP/代理，先关掉再抢号" -ForegroundColor Yellow
    }
    # 2026-11-13 实测：token_pool.py 的 Chromium 没有 --no-proxy-server，
    # 系统代理开着时它的“直连票”会从 WARP 出口（104.28.x）铸出来，票和出口就不同源了。
    $inet = Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -EA SilentlyContinue
    if ($inet.ProxyEnable -eq 1) {
        $color = if ($Proxy) { "DarkGray" } else { "Yellow" }
        Write-Host "⚠ 系统代理仍开着（ProxyEnable=1，$($inet.ProxyServer)）。" -ForegroundColor $color
        if ($Proxy) {
            Write-Host "  浏览器已显式走 $Proxy，不受影响；但 token_pool.py 铸票会吃系统代理 ——" -ForegroundColor $color
            Write-Host "  开跑前先把它关掉，否则铸出来的票出口是 WARP，和浏览器不同源、直接作废。" -ForegroundColor $color
        } else {
            Write-Host "  浏览器加了 --no-proxy-server 不受影响；但 token_pool.py 铸票会吃系统代理。" -ForegroundColor $color
            Write-Host "  开跑前先把它关掉（备份在 runs\inetsettings.bak.json）。" -ForegroundColor $color
        }
    }
} catch {
    Write-Host "出口 IP 查询失败（不影响继续）" -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 3) 浏览器
# 默认重建 profile：反反复复复用同一个 profile 时，一旦缓存的 service worker
# 与源码不一致，就会出现「扩展静默不加载」（页面一条注入日志都没有）。
# 单实例场景 cookie 丢了只是重过一次 WAF 挑战，不值得为它冒风险。
$splat = @{ Instance = $Instance }
if (-not $KeepProfile) { $splat.FreshProfile = $true }
if ($Proxy) {
    # 票（WAF cookie）与出口 IP 绑定：指定代理时浏览器必须全程同一个出口，
    # launch_ext.ps1 会自动摘掉 --no-proxy-server 并给 127.0.0.1 加 bypass。
    $splat.Proxy = $Proxy
}
if ($DryRun) {
    $splat.DryRun = $true
} else {
    $splat.Url = $Url
}
& (Join-Path $root "launch_ext.ps1") @splat

# ---------------------------------------------------------------- 4) 提示
$day = Get-Date -Format "yyyy-MM-dd"
Write-Host ""
Write-Host "================ 开跑，每一步自动留痕 ================" -ForegroundColor Green
Write-Host "  实时现场截图    http://127.0.0.1:8765/shot/latest"
Write-Host "  已存步骤列表    http://127.0.0.1:8765/steps?n=20"
Write-Host "  截图目录        runs\steps\$day\"
Write-Host "  事件时间线      python journal.py --timeline --min-gap 3"
Write-Host "  阶段耗时        python journal.py --stages"
Write-Host "  最终报告        python journal.py --report"
Write-Host ""
Write-Host "验证码：滑块由扩展自动解；日志出现 need_human / waf_captcha（求解失败）时，" -ForegroundColor Yellow
Write-Host "        到浏览器窗口里手动拖完，扩展检测到阶段推进会自动继续。" -ForegroundColor Yellow

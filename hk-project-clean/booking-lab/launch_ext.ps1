# launch_ext.ps1 —— 用 Chrome for Testing 启动「带扩展、无 CDP」的浏览器
# ---------------------------------------------------------------------------
# 为什么用 Chrome for Testing：
#   Chrome 稳定版从 137 起禁用了 --load-extension（实测 156 会静默忽略），
#   而 CfT 保留该能力，且版本与正式版一致（158 → 156 系列）。
#
# 为什么不开 --remote-debugging-port：
#   本方案的全部意义就是「没有 CDP」。开了调试端口等于把 CDP 又请回来。
#   进度请从本地服务看：http://127.0.0.1:8765/  (或 runs/ext_status.jsonl)
#
# 用法：
#   powershell -ExecutionPolicy CompatPath -File .\launch_ext.ps1
#   powershell -ExecutionPolicy CompatPath -File .\launch_ext.ps1 -Url "https://abs3.example.test/?lang=sc"

param(
    [string]$Url = "https://portal.example.test/apps/booking.htm",
    [string]$Profile = "",
    [string]$Instance = "",     # w1 / w2 …：写进 URL hash，扩展按窗口区分上报
    [string]$Proxy = "",        # 出口代理，如 http://user:pass@1.2.3.4:8080
    [switch]$NoExtension,
    [switch]$FreshProfile,      # 重建 profile（丢 cookie/缓存）：扩展改了代码后最稳的做法
    [switch]$DryRun             # 自检：只开本地测试页，完全不碰政府站点（不消耗入队机会）
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$extDir = Join-Path $root "extension"

# 1) 找 CfT，找不到就退回系统 Chrome（并提示扩展会失效）
$cft = Join-Path $root "cft\chrome-win64\chrome.exe"
$system = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (Test-Path $cft) {
    $chrome = $cft
    Write-Host "内核：Chrome for Testing（支持 --load-extension）" -ForegroundColor Green
} elseif ($system) {
    $chrome = $system
    Write-Host "⚠ 没找到 Chrome for Testing，退回系统 Chrome —— 稳定版会忽略 --load-extension，扩展不会生效" -ForegroundColor Yellow
    Write-Host "  先跑：.\get_cft.ps1  下载 CfT" -ForegroundColor Yellow
} else {
    throw "找不到任何 Chrome"
}

if (-not (Test-Path $extDir)) { throw "扩展目录不存在：$extDir" }

# 2) 检查本地服务是否在跑（扩展要靠它拿配置/图片/OTP）
$svcOk = $false
try {
    $p = Invoke-RestMethod "http://127.0.0.1:8765/ping" -TimeoutSec 3
    $svcOk = [bool]$p.ok
} catch { }
if (-not $svcOk) {
    Write-Host "⚠ 本地服务没在跑，扩展拿不到配置。另开一个窗口跑：python ext_server.py" -ForegroundColor Yellow
} else {
    Write-Host "本地服务：OK（127.0.0.1:8765）" -ForegroundColor Green
}

# 自检模式：换成本地测试页，绝不触碰政府站点
if ($DryRun) {
    $Url = "http://127.0.0.1:8765/test_captcha"
    if (-not $Profile) { $Profile = if ($Instance) { "chrome_dry_$Instance" } else { "chrome_dry" } }
    Write-Host "自检模式：只开本地测试页（不消耗入队机会）" -ForegroundColor Cyan
}

# 3) 启动（刻意不给 --remote-debugging-port）
if (-not $Profile) {
    $Profile = if ($Instance) { "chrome_ext_$Instance" } else { "chrome_ext" }
}
$profDir = Join-Path $root $Profile
# 重建 profile：2026-11-11 实测，反复删 Default\Service Worker 会让 profile 进入
# 「扩展静默不加载」的状态（chrome://extensions 看不到、页面无注入、日志全无）。
# 与其猜哪里坏了，不如每次干净重建 —— 单实例场景 cookie 丢了也只是重过一次 WAF 挑战。
if ($FreshProfile -and (Test-Path $profDir)) {
    Write-Host "重建 profile（丢弃旧 cookie/缓存）：$profDir" -ForegroundColor Yellow
    Remove-Item $profDir -Recurse -Force -ErrorAction SilentlyContinue
}
if (-not (Test-Path $profDir)) { New-Item -ItemType Directory -Path $profDir | Out-Null }

# ── 扩展源码变了怎么办：
#    --load-extension 加载的是 unpacked 目录，但 MV3 的 service worker 另有一份
#    ScriptCache（profile/Default/Service Worker）。**反复删这个目录会把 profile 弄坏**
#    （2026-11-11 实测：删完之后扩展静默不加载，页面一条注入日志都没有）。
#    所以现在只做「检测 + 建议」，不再自动删：源码比缓存新就提示用 -FreshProfile 重启。
$extCache = Join-Path $profDir "Default\Extensions"
$swCache = Join-Path $profDir "Default\Service Worker"
if ((Test-Path $extCache) -or (Test-Path $swCache)) {
    $srcNewest = (Get-ChildItem $extDir -Recurse -File -ErrorAction SilentlyContinue |
                  Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
    $times = @()
    foreach ($p in @($extCache, $swCache)) {
        if (Test-Path $p) {
            $t = (Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue |
                  Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
            if ($t) { $times += $t }
        }
    }
    if ($times.Count -and $srcNewest -gt ($times | Measure-Object -Maximum).Maximum -and -not $FreshProfile) {
        Write-Host "⚠ 扩展源码比 profile 缓存新 —— 建议这次用 -FreshProfile 重建，否则可能跑旧脚本" -ForegroundColor Yellow
    }
}

# 窗口代号写进 hash（不会发给服务器），扩展据此区分是哪个窗口弹了验证码
if ($Instance -and $Url -notmatch "#") {
    $Url = "$Url#inst=$Instance"
}

$args = @(
    "--user-data-dir=$profDir",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=CalculateNativeWinOcclusion",
    # CfT 解压版在这台机器上 sandbox 无法访问自身可执行文件（实测报
    # "Sandbox cannot access executable ... 拒绝访问"），不加这个参数页面会停在
    # about:blank、连本地服务都连不上。对本用途（本机自用）没有副作用。
    "--no-sandbox",
    # 系统里有个已失效的代理（127.0.0.1:10809），不兼容处理它会连 localhost 都打不开
    "--no-proxy-server"
)

# 出口代理：门票（cookie）与 IP 绑定，所以一条会话必须全程同一个出口。
# 一旦指定代理，就不能再带 --no-proxy-server（两者冲突），改用 --proxy-server
# 并把本地服务地址排除在代理之外（否则扩展连不上 127.0.0.1:8765）。
if ($Proxy) {
    $args = $args | Where-Object { $_ -ne "--no-proxy-server" }
    $args += "--proxy-server=$Proxy"
    # 关键：不能让 127.0.0.1 走代理，否则扩展访问本地服务（127.0.0.1:8765）的
# /status /snapshot /net 全部会被塞进代理直接丢掉 —— 页面正常、日志全空。
$args += "--proxy-compat-path-list=127.0.0.1;localhost;<local>"
    Write-Host "出口代理：$Proxy（本地服务走直连）" -ForegroundColor Cyan
}
if (-not $NoExtension) {
    $args += "--load-extension=$extDir"
    # 不要加 --disable-extensions-except：稳定版会忽略它，CfT 上实测会导致起不来
}
$args += $Url

Write-Host "打开：$Url" -ForegroundColor Cyan
Write-Host "profile：$profDir"
Start-Process -FilePath $chrome -ArgumentList $args | Out-Null

# 4) 等 profile 里出现扩展记录，确认真的加载了
$extRoot = Join-Path $profDir "Default\Extensions"
for ($i = 0; $i -lt 25; $i++) {
    Start-Sleep -Milliseconds 600
    if (Test-Path $extRoot) {
        $ids = Get-ChildItem $extRoot -Directory -ErrorAction SilentlyContinue
        if ($ids) {
            Write-Host "扩展已加载：$($ids.Name -join ', ')" -ForegroundColor Green
            Write-Host "进度查看：Get-Content runs\ext_status.jsonl -Tail 20" -ForegroundColor Cyan
            exit 0
        }
    }
}
Write-Host "⚠ 没在 profile 里看到扩展记录 —— 扩展可能没加载" -ForegroundColor Yellow
Write-Host "  可手动确认：打开 chrome://extensions 看有没有 ‘BOOKING Auto (no-CDP)’" -ForegroundColor Yellow
exit 1

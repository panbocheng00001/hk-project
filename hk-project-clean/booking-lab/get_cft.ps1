# get_cft.ps1 —— 下载 Chrome for Testing（保留 --load-extension 能力）
# Chrome 稳定版 137+ 已禁用 --load-extension，扩展方案必须用 CfT。
# 用法：powershell -ExecutionPolicy CompatPath -File .\get_cft.ps1
param(
    [string]$Version = ""      # 留空 = 自动取最新 stable
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$dst = Join-Path $root "cft"
New-Item -ItemType Directory -Path $dst -Force | Out-Null

if (Test-Path (Join-Path $dst "chrome-win64\chrome.exe")) {
    Write-Host "已存在：$dst\chrome-win64\chrome.exe" -ForegroundColor Green
    & (Join-Path $dst "chrome-win64\chrome.exe") --version
    exit 0
}

$ProgressPreference = "SilentlyContinue"
$env:NO_PROXY = "*"

if (-not $Version) {
    Write-Host "查询 CfT 版本清单…"
    $j = (Invoke-WebRequest "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json" -TimeoutSec 30 -UseBasicParsing).Content | ConvertFrom-Json
    $stable = $j.channels.Stable
    $Version = $stable.version
    $url = ($stable.downloads.chrome | Where-Object platform -eq "win64").url
    Write-Host "最新 Stable：$Version"
} else {
    $url = "https://storage.googleapis.com/chrome-for-testing-public/$Version/win64/chrome-win64.zip"
}

$zip = Join-Path $dst "chrome-win64.zip"
Write-Host "下载：$url"
Invoke-WebRequest $url -OutFile $zip -TimeoutSec 2400 -UseBasicParsing

Write-Host "解压…"
Expand-Archive -Path $zip -DestinationPath $dst -Force
Remove-Item $zip -Force -ErrorAction SilentlyContinue

$exe = Join-Path $dst "chrome-win64\chrome.exe"
if (-not (Test-Path $exe)) { throw "解压后没找到 chrome.exe" }
Write-Host "完成：$exe" -ForegroundColor Green
& $exe --version

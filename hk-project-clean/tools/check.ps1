# tools/check.ps1 —— 一条命令跑完全部检查（文案 lint + 两套测试）
#
#   pwsh -File tools/check.ps1            # 全部
#   pwsh -File tools/check.ps1 -SkipKernel  # 只跑宿主侧
#
# 退出码：0 全绿；1 有步骤失败。CI 直接用它。
param(
    [switch]$SkipLint,
    [switch]$SkipKernel
)

$ErrorActionPreference = 'Continue'

# 控制台默认代码页不是 UTF-8 时，Write-Host 的中文会变成乱码（Windows PowerShell 5.1 常见）
try {
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [Console]::OutputEncoding = $utf8
    $OutputEncoding = $utf8
} catch { }

$root = Split-Path -Parent $PSScriptRoot
$results = @()

function Step {
    param([string]$Name, [string]$WorkDir, [string[]]$ArgList)

    Write-Host ""
    Write-Host "=== $Name ===" -ForegroundColor Cyan
    Push-Location $WorkDir
    try {
        & python @ArgList
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    $script:results += [pscustomobject]@{
        Name   = $Name
        Code   = $code
        Status = if ($code -eq 0) { 'PASS' } else { "FAIL($code)" }
    }
}

if (-not $SkipLint) {
    Step -Name 'wording-lint' -WorkDir $root -ArgList @('tools/wording_lint.py', '--root', '.')
}

Step -Name 'booking-lab tests' -WorkDir (Join-Path $root 'booking-lab') `
     -ArgList @('-m', 'pytest', 'captcha_flow/tests', 'tests', '-q')

if (-not $SkipKernel) {
    Step -Name 'captcha-lab tests' -WorkDir (Join-Path $root 'captcha-lab') `
         -ArgList @('-m', 'pytest', '-q')
}

Write-Host ""
Write-Host "=== summary ===" -ForegroundColor Cyan
$results | Format-Table -AutoSize | Out-String | Write-Host

$failed = @($results | Where-Object { $_.Code -ne 0 })
if ($failed.Count -gt 0) {
    Write-Host ("FAILED: " + (($failed | ForEach-Object { $_.Name }) -join ', ')) -ForegroundColor Red
    exit 1
}
Write-Host "ALL GREEN" -ForegroundColor Green
exit 0

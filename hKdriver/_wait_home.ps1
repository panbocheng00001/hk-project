# _wait_home.ps1 -- wait for home egress 443 to recover, then run R2 once
param(
    [int]$DeadlineMin = 26,
    [int]$IntervalSec = 40,
    [int]$RunMinutes  = 18
)
$ErrorActionPreference = 'Continue'
$deadline = (Get-Date).AddMinutes($DeadlineMin)
$n = 0
while ((Get-Date) -lt $deadline) {
    $n++
    $ts = Get-Date -Format 'HH:mm:ss'
    $code = curl.exe -s -o NUL -w "%{http_code}" --max-time 6 --noproxy "*" "https://abs3.td.gov.hk/?lang=sc" 2>$null
    if (-not $code) { $code = "000" }
    Write-Output "$ts probe#$n http_code=$code"
    if ($code -ne "000") {
        Write-Output "$ts GOT $code -> run R2 for $RunMinutes min"
        & python r2_flow.py --minutes $RunMinutes --ip-id home3 2>&1
        Write-Output "$ts R2 done exit=$LASTEXITCODE"
        break
    }
    Start-Sleep -Seconds $IntervalSec
}
Write-Output "$(Get-Date -Format 'HH:mm:ss') watcher stopped after $n probes"

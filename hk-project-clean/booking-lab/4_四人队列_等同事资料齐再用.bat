@echo off
cd /d "%~dp0"
title BOOKING 4 queue

echo ==================================================
echo  BOOKING four applicants (w1 w2 w3 w4)
echo  Dir : %CD%
echo.
echo  Each window = one separate queue + one identity.
echo  They start 40s apart on purpose: 4 sessions from
echo  the same IP hitting at once looks like a bot.
echo ==================================================
echo.

for %%W in (w1 w2 w3 w4) do (
  echo.
  echo [start] %%W
  powershell -NoProfile -ExecutionPolicy CompatPath -File "%~dp0launch_ext.ps1" -Instance %%W
  if not "%%W"=="w4" (
    echo waiting 40s before next window ...
    timeout /t 40 /nobreak >nul
  )
)

echo.
echo All four launched.
echo progress : Get-Content runs\ext_status.jsonl -Tail 30
echo remind   : if a slider captcha appears, DingTalk will tell you which window.
echo press any key to close...
pause >nul

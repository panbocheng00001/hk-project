@echo off
cd /d "%~dp0"
title TDABS dual window (w1 + w2)

echo ==================================================
echo  TDABS dual queue
echo  Dir : %CD%
echo  This opens TWO independent Chrome profiles:
echo    w1 -> D:\...\chrome_ext_w1
echo    w2 -> D:\...\chrome_ext_w2
echo  Each gets its own queue ticket.
echo ==================================================
echo.

echo [1/2] starting w1 ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_ext.ps1" -Instance w1
echo waiting 25s for w1 to settle before starting w2 ...
timeout /t 25 /nobreak >nul

echo.
echo [2/2] starting w2 ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_ext.ps1" -Instance w2

echo.
echo Both launched. Check progress:
echo    Get-Content runs\ext_status.jsonl -Tail 20
echo.
echo REMINDER: if a window shows a slider captcha, ring will sound.
echo press any key to close...
pause >nul

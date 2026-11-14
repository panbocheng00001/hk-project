@echo off
cd /d "%~dp0"
title BOOKING launch w1

echo ==================================================
echo  BOOKING - single window (w1)
echo  Dir : %CD%
echo.
echo  16:28 run this. It will keep probing until the
echo  maintenance window ends, then enter the queue.
echo ==================================================
echo.

powershell -NoProfile -ExecutionPolicy CompatPath -File "%~dp0launch_ext.ps1" -Instance w1

echo.
echo window launched. progress:
echo    Get-Content runs\ext_status.jsonl -Tail 20
echo press any key to close...
pause >nul

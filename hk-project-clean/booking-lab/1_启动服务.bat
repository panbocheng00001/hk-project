@echo off
cd /d "%~dp0"
title BOOKING local service
echo ==================================================
echo  BOOKING local service  (keep this window open)
echo  Dir : %CD%
echo  URL : http://127.0.0.1:8765
echo ==================================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3.9 -X utf8 ext_server.py
) else (
    python -X utf8 ext_server.py
)
echo.
echo [service stopped] press any key to close...
pause >nul

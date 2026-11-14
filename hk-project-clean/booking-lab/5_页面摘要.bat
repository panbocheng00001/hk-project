@echo off
chcp 65001 >nul
cd /d %~dp0
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
set "URLFILE=%USERPROFILE%\.hk-project\pagekit_url.txt"
if not exist "%URLFILE%" (
  echo 先把目标地址写进 %URLFILE%（一行，URL），再运行本脚本。
  pause
  exit /b 2
)
set /p URL=<"%URLFILE%"
echo 打开：%URL%
python -u -m pagekit open --url "%URL%" --name live-home --headed --hold 0 --profile "runs/pagekit/profile"
echo.
echo 会话已结束（上面一行写着结束原因）。
pause

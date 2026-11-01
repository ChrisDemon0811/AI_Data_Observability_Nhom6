@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
    echo Chua co .venv. Hay chay run_demo.bat de cai dat.
    exit /b 1
)
echo NHOM 6 - APScheduler worker. Ctrl+C de dung an toan.
echo Mac dinh 300 giay; dung start_scheduler.bat --interval 15 de demo.
".venv\Scripts\python.exe" -m observability.scheduler %*
exit /b %errorlevel%

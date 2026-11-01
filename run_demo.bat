@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
if not defined SCHEDULER_INTERVAL_SECONDS set "SCHEDULER_INTERVAL_SECONDS=300"
if not "%~1"=="" set "SCHEDULER_INTERVAL_SECONDS=%~1"
set "PYCMD="
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYCMD=py -3.12"
if not defined PYCMD (
    py -3.13 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PYCMD=py -3.13"
)
if not defined PYCMD (
    py -3.11 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PYCMD=py -3.11"
)
if not defined PYCMD (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PYCMD=python"
)
if not defined PYCMD (
    echo Khong tim thay Python. Hay cai Python 3.12 va bat Add to PATH.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Dang tao moi truong Python...
    %PYCMD% -m venv .venv
    if errorlevel 1 goto fail
)
echo [2/4] Cai thu vien, chi can ket noi mang lan dau...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo [3/4] Tao data demo neu chua co...
.venv\Scripts\python.exe -m observability.cli seed
if errorlevel 1 goto fail
echo [4/4] Gui lenh khoi dong FastAPI, Streamlit va APScheduler...
start "" "%~dp0start_api.bat"
timeout /t 3 /nobreak >nul
start "" "%~dp0start_dashboard.bat"
start "" "%~dp0start_scheduler.bat"
echo.
echo API: http://127.0.0.1:8000/docs
echo Dashboard: http://127.0.0.1:8501
echo Scheduler: interval %SCHEDULER_INTERVAL_SECONDS% giay; logs\scheduler.log
echo Trang thai: da gui lenh. Xem terminal tung thanh phan de xac nhan khoi dong.
echo Kiem tra API: http://127.0.0.1:8000/api/health
echo Scheduler tu choi khoi dong neu worker cua project da chay.
echo Dung: Ctrl+C trong CA BA cua so FastAPI, Streamlit, Scheduler; doi job ket thuc.
echo Chi dong cua so sau khi tien trinh da dung. Khong taskkill Python hang loat.
pause
exit /b 0
:fail
echo Co loi trong qua trinh cai dat. Vui long xem thong bao phia tren.
pause
exit /b 1

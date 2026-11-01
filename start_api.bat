@echo off
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
echo NHOM 6 - FastAPI backend
.venv\Scripts\python.exe -m uvicorn observability.api:app --host 127.0.0.1 --port 8000
if errorlevel 1 pause

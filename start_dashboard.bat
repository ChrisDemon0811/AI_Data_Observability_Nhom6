@echo off
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
echo NHOM 6 - Streamlit dashboard
.venv\Scripts\python.exe -m streamlit run dashboard.py --server.address 127.0.0.1 --server.port 8501
if errorlevel 1 pause

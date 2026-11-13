@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
echo Сайт: http://localhost:8000
.venv\Scripts\python -m uvicorn server.app:app --host 0.0.0.0 --port 8000

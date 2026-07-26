@echo off
chcp 65001 >nul
cd /d "%~dp0"
call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)
set PYTHONPATH=src
echo Dashboard Go Progress sur http://127.0.0.1:8787
echo (Ctrl+C pour arreter)
python -m goprogress serve

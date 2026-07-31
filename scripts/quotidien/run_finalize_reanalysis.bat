@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)
set PYTHONPATH=src
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
echo Enrichissements finaux 2024+ (Human SL, patterns, rapport)...
python -m goprogress reanalysis --since-year 2024 --finalize-only
pause

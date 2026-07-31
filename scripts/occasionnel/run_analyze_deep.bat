@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)
set PYTHONPATH=src
set PYTHONUNBUFFERED=1
echo Analyse PROFONDE KataGo (12k-24k visits, ~20-40s/coup suspect)
echo Fermez Lizzie avant de lancer.
echo.
python -m goprogress analyze --deep %*
pause

@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)
set PYTHONPATH=src
set PYTHONUNBUFFERED=1
echo Construction index patch GoGoD
echo Peut prendre plusieurs heures selon la taille du corpus.
echo.
python -m goprogress build-pro-index %*
pause

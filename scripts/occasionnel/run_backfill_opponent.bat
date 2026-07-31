@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)
set PYTHONPATH=src
set PYTHONUNBUFFERED=1
echo Complément stats adversaire (parties deja analysees)...
python -m goprogress analyze --backfill-opponent --limit 10 %*

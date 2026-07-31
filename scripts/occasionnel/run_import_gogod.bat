@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)
set PYTHONPATH=src
set PYTHONUNBUFFERED=1
echo Import GoGoD depuis Downloads vers data\pro_corpus\gogod\
echo Cela peut prendre 5-15 minutes (136k+ SGF)...
python -m goprogress import-gogod
if errorlevel 1 pause
echo.
echo Termine. Manifeste: data\pro_corpus\gogod\manifest.json
pause

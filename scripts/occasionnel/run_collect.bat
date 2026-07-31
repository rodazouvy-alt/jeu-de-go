@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)
set PYTHONPATH=src
echo ========================================
echo  Collecte KGS complete (reprise auto)
echo  Peut prendre plusieurs heures/jours
echo ========================================
echo.
python -m goprogress sync --all-months --zip
echo.
python -m goprogress status
pause

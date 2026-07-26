@echo off
chcp 65001 >nul
cd /d "%~dp0"
call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)
set PYTHONPATH=src

echo [1/4] Sync 5 dernieres parties KGS...
python -m goprogress sync --limit 5

echo.
echo [2/4] Analyse KataGo (3 parties, mode rapide)...
echo       Fermez Lizzie si l analyse echoue (GPU occupe)
python -m goprogress analyze --limit 3

echo.
echo [3/4] Generation du rapport HTML...
python -m goprogress report --open

echo.
echo [4/4] Statut
python -m goprogress status

echo.
echo Test Phase 0 termine !
pause

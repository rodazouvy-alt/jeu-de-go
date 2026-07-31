@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)
set PYTHONPATH=src
set PYTHONUNBUFFERED=1

echo ========================================
echo  Restauration analyses depuis JSON
echo  (apres reset accidentel de la base)
echo ========================================
echo.

python -m goprogress restore-analysis --since-year 2024
if errorlevel 1 goto :err

echo.
echo OK. Relancez run_analysis_loop.bat pour continuer.
echo Puis scripts\occasionnel\run_backfill_human_sl.bat pour remplir Human SL.
pause
exit /b 0

:err
echo ERREUR.
pause
exit /b 1

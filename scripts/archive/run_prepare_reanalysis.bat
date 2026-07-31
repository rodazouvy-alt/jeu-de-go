@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)
set PYTHONPATH=src
set PYTHONUNBUFFERED=1

echo ========================================
echo  ATTENTION — SCRIPT DANGEREUX
echo  Preparation re-analyse complete 2024+
echo ========================================
echo.
echo  1. Sync KGS 2024-2026 (ZIP)
echo  2. Reset de toutes les analyses
echo.
echo  N utilisez ce script QUE pour repartir de zero.
echo  Sinon vous perdez toutes les analyses deja faites.
echo.
echo  Pour reprendre apres reset : run_analysis_loop.bat
echo  Pour restaurer apres reset : scripts\archive\run_restore_reanalysis.bat
echo.
echo  Fermez Lizzie avant l analyse.
echo.
pause

python -m goprogress sync --since-year 2024 --zip
if errorlevel 1 goto :err

python -m goprogress reset-analysis --yes
if errorlevel 1 goto :err

echo.
echo Preparation terminee.
echo Lancez run_analysis_loop.bat pour analyser en continu.
pause
exit /b 0

:err
echo ERREUR.
pause
exit /b 1

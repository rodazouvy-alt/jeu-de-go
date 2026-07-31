@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)
set PYTHONPATH=src
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

echo ========================================
echo  Reset complet pipeline v2 (option B)
echo  Perimetre : parties 2024+ (~2100)
echo ========================================
echo.
echo  Efface les coups analyses 2024+ et remet les flags a zero.
echo  Les parties avant 2024 ne sont pas touchees.
echo  Les SGF et la liste des parties sont conserves.
echo.
echo  APRES ce script, lancez :
echo    scripts\quotidien\run_analysis_loop.bat
echo.
pause

python -m goprogress prepare-v2-analysis --full --yes --since-year 2024
if errorlevel 1 goto :err

echo.
echo Preparation terminee. Lancez run_analysis_loop.bat maintenant.
pause
exit /b 0

:err
echo ERREUR.
pause
exit /b 1

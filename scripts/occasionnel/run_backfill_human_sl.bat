@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)
set PYTHONPATH=src
set PYTHONUNBUFFERED=1

echo ========================================
echo  Human SL — remplissage parties analysees
echo  (apres restore ou import sans SL)
echo ========================================
echo Fermez Lizzie avant de lancer.
echo.

python -m goprogress human-sl --by-game --since-year 2024 --limit 500
if errorlevel 1 goto :err

echo.
echo Termine. Rafraichissez le dashboard.
pause
exit /b 0

:err
echo ERREUR.
pause
exit /b 1

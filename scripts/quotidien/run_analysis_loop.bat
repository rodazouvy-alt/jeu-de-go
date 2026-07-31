@echo off

chcp 65001 >nul

cd /d "%~dp0\..\.."

call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)

set PYTHONPATH=src

set PYTHONUNBUFFERED=1

set PYTHONIOENCODING=utf-8



echo ========================================

echo  Analyse complete 2024+ (~2106 parties)

echo  Pipeline lot optimise : quick x4 puis fuseki+SL

echo  RTX 3060 12 Go — 1 moteur GPU actif (stable)

echo  KataGo redemarre toutes les heures

echo ========================================

echo.

echo  Fermez Lizzie. Ctrl+C pour arreter.

echo.



python -m goprogress reanalysis --since-year 2024 --loop --katago-restart-hours 1

if errorlevel 1 (

  echo.

  echo ERREUR — voir le message ci-dessus.

  pause

  exit /b 1

)



echo.

echo Termine. Lancez run_finalize_reanalysis.bat pour pro-patterns + rapport.

pause


@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)
set PYTHONPATH=src
set PYTHONUNBUFFERED=1
echo ========================================
echo  Pipeline nocturne (ANCIEN — remplace par run_analysis_loop)
echo  analyse -^> human SL -^> patterns -^> pro-patterns -^> rapport
echo ========================================
echo Fermez Lizzie avant de lancer.
echo.
python -m goprogress analyze --limit 10
if errorlevel 1 goto :err
echo.
python -m goprogress human-sl --report
if errorlevel 1 goto :err
echo.
python -m goprogress patterns
if errorlevel 1 goto :err
echo.
python -m goprogress pro-patterns
if errorlevel 1 goto :err
echo.
python -m goprogress report
if errorlevel 1 goto :err
echo.
echo Pipeline termine.
pause
exit /b 0

:err
echo.
echo ERREUR pendant le pipeline.
pause
exit /b 1

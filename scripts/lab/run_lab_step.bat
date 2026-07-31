@echo off
REM Labo Deep Visits — lance une etape (0-3)
REM Usage: run_lab_step.bat 0
cd /d "%~dp0\..\.."
call venv\Scripts\activate.bat
set PYTHONPATH=src

if "%1"=="0" python scripts\lab\00_setup_lab.py %2 %3 %4
if "%1"=="1" python scripts\lab\01_oracle_ref.py %2 %3 %4
if "%1"=="2" python scripts\lab\02_run_strategies.py %2 %3 %4
if "%1"=="3" python scripts\lab\03_generate_report.py %2 %3 %4

if "%1"=="" (
  echo Usage: run_lab_step.bat [0^|1^|2^|3] [options]
  echo   0 = setup    1 = oracle    2 = strategies    3 = rapport
)

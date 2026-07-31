@echo off
cd /d "%~dp0\..\.."
if "%~1"=="" (
  echo Usage: run_review.bat MOVE_ID [lizzie^|katrain]
  echo Exemple: run_review.bat 42 lizzie
  exit /b 1
)
set TOOL=lizzie
if not "%~2"=="" set TOOL=%~2
echo Fermez l analyse batch avant d ouvrir Lizzie (GPU partage).
call venv\Scripts\activate.bat 2>nul
set PYTHONPATH=src
python -m goprogress review --move-id %1 --open --tool %TOOL%
pause

@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo  Installation Go Progress (Phase 0)
echo ========================================
echo.

if not exist venv (
    echo Creation de l environnement Python...
    python -m venv venv
)

call venv\Scripts\activate.bat
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo.
echo Installation terminee.
echo.
echo Prochaines etapes :
echo   1. setup.bat          (deja fait si vous relancez)
echo   2. run_test.bat       (sync + analyse + rapport sur 3 parties)
echo   3. start.bat          (dashboard local)
echo.
pause

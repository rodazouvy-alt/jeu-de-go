@echo off

chcp 65001 >nul

cd /d "%~dp0\..\.."

call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)

set PYTHONPATH=src



for /f "delims=" %%P in ('python -c "from goprogress.config import load_config; print(load_config()['dashboard']['port'])"') do set DASH_PORT=%%P

if not defined DASH_PORT set DASH_PORT=8787



echo ========================================

echo  Dashboard Go Progress

echo  http://127.0.0.1:%DASH_PORT%

echo ========================================

echo.

echo  - Une ligne par partie (OK / err. / blunders)

echo  - Bouton Coups : erreurs par ordre (ouverture 1-30 puis suite) + patterns

echo  - Explorateur patterns : http://127.0.0.1:%DASH_PORT%/patterns

echo  - Lizzie : ouvre la position au bon coup

echo.

echo  Le navigateur s ouvre automatiquement.

echo  Fermez la fenetre du serveur (Ctrl+C) pour arreter.

echo.



set "ROOT=%~dp0\..\.."

start "Dashboard Go Progress" cmd /k cd /d "%ROOT%" ^& call venv\Scripts\activate.bat ^& set PYTHONPATH=src ^& python -m goprogress serve



timeout /t 3 /nobreak >nul

start http://127.0.0.1:%DASH_PORT%



echo Navigateur ouvert sur http://127.0.0.1:%DASH_PORT%

echo Les logs du serveur sont dans la fenetre "Dashboard Go Progress".

pause


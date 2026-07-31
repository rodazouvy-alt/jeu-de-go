@echo off

chcp 65001 >nul

cd /d "%~dp0\..\.."

call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)

set PYTHONPATH=src



for /f "delims=" %%P in ('python -c "from goprogress.config import load_config; print(load_config()['dashboard']['port'])"') do set DASH_PORT=%%P

if not defined DASH_PORT set DASH_PORT=8787



where cloudflared >nul 2>&1 || (

  echo cloudflared introuvable.

  echo Telechargez : https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/

  echo Placez cloudflared.exe dans le PATH ou dans ce dossier.

  pause

  exit /b 1

)



netstat -an | findstr ":%DASH_PORT%" | findstr "LISTENING" >nul 2>&1

if errorlevel 1 (

  echo Le dashboard local n est pas actif — lancement du serveur...

  set "ROOT=%~dp0\..\.."

  start "Dashboard Go Progress" cmd /k cd /d "%ROOT%" ^& call venv\Scripts\activate.bat ^& set PYTHONPATH=src ^& python -m goprogress serve

  echo Attente du serveur (5 s)...

  timeout /t 5 /nobreak >nul

)



echo ========================================

echo  Tunnel Cloudflare (trycloudflare)

echo  Dashboard local : http://127.0.0.1:%DASH_PORT%

echo ========================================

echo.

echo  Une URL publique (https://....trycloudflare.com) s affiche ci-dessous.

echo  Partagez cette URL pour acceder depuis mobile ou un autre PC.

echo  Le tunnel fonctionne tant que cette fenetre reste ouverte.

echo  Fermez aussi la fenetre du serveur pour arreter completement.

echo.



cloudflared tunnel --url http://127.0.0.1:%DASH_PORT%

pause


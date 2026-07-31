@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
echo === Verification Human SL KataGo ===
echo.

set HUMAN=E:\lizzie\KataGo18human.gz
set KATAGO=E:\lizzie\katago.exe

if not exist "%KATAGO%" (
  echo ERREUR: katago.exe introuvable dans E:\lizzie\
  pause
  exit /b 1
)

if exist "%HUMAN%" (
  echo OK  Modele deja present: %HUMAN%
) else (
  echo Telechargement b18c384nbt-humanv0.bin.gz ...
  powershell -Command "Invoke-WebRequest -Uri 'https://github.com/lightvector/KataGo/releases/download/v1.15.0/b18c384nbt-humanv0.bin.gz' -OutFile '%HUMAN%' -UseBasicParsing"
  if errorlevel 1 (
    echo Echec. Telechargez manuellement depuis:
    echo   https://github.com/lightvector/KataGo/releases/download/v1.15.0/b18c384nbt-humanv0.bin.gz
    echo Renommez en KataGo18human.gz dans E:\lizzie\
    pause
    exit /b 1
  )
)

echo.
"%KATAGO%" version
echo.
call venv\Scripts\activate.bat 2>nul
set PYTHONPATH=src
set PYTHONUNBUFFERED=1
python -m goprogress human-sl --limit 1 --report
echo.
echo Si 1 coup traite ci-dessus, Human SL est operationnel.
echo Ensuite: scripts\occasionnel\run_human_sl.bat --limit 100
pause

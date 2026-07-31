@echo off

chcp 65001 >nul

cd /d "%~dp0\..\.."

call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)

set PYTHONPATH=src



echo ========================================

echo  Miroir Cloudflare Pages (dashboard 24/7)

echo ========================================

echo.

echo  Copie locale -^> data\publish\ -^> Cloudflare

echo  Prerequis : config.local.yaml (voir config.local.yaml.example)

echo.



python -m goprogress sync-cloudflare

pause


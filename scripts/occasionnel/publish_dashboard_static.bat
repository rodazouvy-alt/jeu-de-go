@echo off

chcp 65001 >nul

cd /d "%~dp0\..\.."

call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)

set PYTHONPATH=src

echo Export snapshot statique vers data\publish\

python -m goprogress publish

echo.

echo Deploiement Cloudflare Pages : uploadez data\publish\ ou wrangler pages deploy data\publish

pause


@echo off

chcp 65001 >nul

cd /d "%~dp0\..\.."

call venv\Scripts\activate.bat 2>nul || (echo Lancez setup.bat d abord && pause && exit /b 1)

set PYTHONPATH=src



echo ========================================

echo  Deploy Cloudflare Pages (site mobile 24/7)

echo ========================================

echo.

echo  1. Export snapshot HTML

python -m goprogress publish

if errorlevel 1 pause & exit /b 1



echo.

for /f "delims=" %%P in ('python -c "from goprogress.config import load_config; print(load_config().get('cloudflare',{}).get('pages_project',''))"') do set CF_PROJECT=%%P



if not defined CF_PROJECT (

  echo.

  echo  Projet Cloudflare non configure.

  echo  1. Creez un compte gratuit sur https://dash.cloudflare.com

  echo  2. Pages - Create project - Upload assets

  echo  3. Uploadez le dossier data\publish\

  echo  4. Ou renseignez cloudflare.pages_project dans config.yaml

  echo     puis installez wrangler : npm install -g wrangler

  echo     et activez cloudflare.auto_deploy_pages: true

  echo.

  echo  Voir docs\CLOUDFLARE.md pour le guide complet.

  pause

  exit /b 0

)



where wrangler >nul 2>&1 || (

  echo wrangler introuvable. Uploadez data\publish\ manuellement sur Cloudflare Pages.

  pause

  exit /b 1

)



echo  2. Deploy vers projet %CF_PROJECT%

wrangler pages deploy data\publish --project-name %CF_PROJECT%

pause


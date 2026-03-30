@echo off
setlocal
cd /d "%~dp0"
set "ROOT=%CD%"
set "VPY=%ROOT%\.venv\Scripts\python.exe"

if not exist "%VPY%" (
  echo No .venv found. Creating virtual environment...
  py -3.12 -m venv .venv --upgrade-deps 2>nul
  if errorlevel 1 py -3.12 -m venv .venv 2>nul
  if errorlevel 1 python -m venv .venv --upgrade-deps 2>nul
  if errorlevel 1 python -m venv .venv
  if not exist "%VPY%" (
    echo Could not create .venv. Install Python 3.12, then run:
    echo   python -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -e ".[webui]"
    pause
    exit /b 1
  )
  echo Virtual environment created.
)

echo.
echo Ensuring pip is installed in the venv...
"%VPY%" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo pip was missing — bootstrapping with ensurepip...
  "%VPY%" -m ensurepip --upgrade
  if errorlevel 1 (
    echo Retrying with ensurepip --default-pip...
    "%VPY%" -m ensurepip --default-pip
  )
  "%VPY%" -m pip --version >nul 2>&1
  if errorlevel 1 (
    echo.
    echo pip still missing. Your Python build may omit ensurepip ^(use the full installer from python.org^).
    echo Try deleting the venv and recreating with bundled pip:
    echo   rmdir /s /q .venv
    echo   py -3.12 -m venv .venv --upgrade-deps
    echo Then run this script again.
    pause
    exit /b 1
  )
)

echo Upgrading pip...
"%VPY%" -m pip install --upgrade pip
if errorlevel 1 (
  echo pip upgrade failed.
  pause
  exit /b 1
)

echo.
echo Installing UltraSinger + WebUI packages ^(optional dependency: webui^)...
"%VPY%" -m pip install -e ".[webui]"
if errorlevel 1 (
  echo.
  echo Install failed. If you see errors about brackets, try from this folder in PowerShell:
  echo   .\.venv\Scripts\python.exe -m pip install -e '.[webui]'
  pause
  exit /b 1
)

echo.
echo Updating yt-dlp to latest...
"%VPY%" -m pip install -U yt-dlp
if errorlevel 1 (
  echo yt-dlp update failed.
  pause
  exit /b 1
)

echo.
echo Verifying uvicorn...
"%VPY%" -c "import uvicorn; print('uvicorn OK:', uvicorn.__version__)"
if errorlevel 1 (
  echo Verification failed.
  pause
  exit /b 1
)

echo.
echo Done. Start the UI with: start_ultrasinger_webui.bat
pause

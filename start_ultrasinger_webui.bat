@echo off
setlocal
cd /d "%~dp0"
set "ROOT=%CD%"
set "VPY=%ROOT%\.venv\Scripts\python.exe"

if not exist "%VPY%" (
  echo No virtual environment found. Run install_webui.bat in this folder first.
  pause
  exit /b 1
)

REM Use venv python directly — do not rely on ERRORLEVEL after activate.bat
"%VPY%" -c "import uvicorn" 2>nul
if errorlevel 1 (
  echo Web UI packages are not installed in this venv.
  echo Run install_webui.bat from this folder ^(it uses the venv Python directly^), then try again.
  echo.
  echo Or run manually:
  echo   "%VPY%" -m pip install -e ".[webui]"
  pause
  exit /b 1
)

call .venv\Scripts\activate.bat
set "PYTHONPATH=%CD%"
echo Starting UltraSinger WebUI ^(repository root: %CD%^) ...
"%VPY%" -m webui
if errorlevel 1 pause

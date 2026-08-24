@echo off
cd /d "%~dp0"
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set "CHECK_ONLY="
if /i "%~1"=="--check" set "CHECK_ONLY=1"

set "CFD_PYTHON=.venv\Scripts\python.exe"
if not exist "%CFD_PYTHON%" (
  set "CFD_PYTHON=python"
  where python > nul 2>&1
  if errorlevel 1 (
    echo [ERROR] Python environment not found. Run install_cfd.bat first.
    pause
    exit /b 1
  )
)

"%CFD_PYTHON%" -c "import ezdxf, shapely, numpy, matplotlib" > nul 2>&1
if errorlevel 1 (
  echo [ERROR] Required libraries are missing. Run install_cfd.bat first.
  pause
  exit /b 1
)

if defined CHECK_ONLY echo MEP CFD Studio launcher: ready
if defined CHECK_ONLY exit /b 0

"%CFD_PYTHON%" cfd_studio.py
if errorlevel 1 (
  echo.
  echo [ERROR] The app failed to start. Review the message above.
  pause
)

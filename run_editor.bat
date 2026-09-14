@echo off
cd /d "%~dp0"
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
if "%~1"=="" (
  echo Usage: run_editor.bat ^<drawing.dxf or project.mep folder^>
  pause
  exit /b 1
)
python pascal_host\run_host.py "%~1" --runtime pascal_runtime --open
if errorlevel 1 (
  echo.
  echo [Error] The editor did not start. Check the message above.
  pause
)

@echo off
cd /d "%~dp0"
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python cfd_studio.py
if errorlevel 1 (
  echo.
  echo [Error] Run failed. Check python installation and PATH.
  pause
)

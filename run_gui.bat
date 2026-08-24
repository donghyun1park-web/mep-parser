@echo off
cd /d "%~dp0"
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set "CFD_PYTHON=.venv\Scripts\python.exe"
if not exist "%CFD_PYTHON%" set "CFD_PYTHON=python"
"%CFD_PYTHON%" mep_gui.py
if errorlevel 1 (
  echo.
  echo [ERROR] Launch failed. Run install_cfd.bat first.
  pause
)

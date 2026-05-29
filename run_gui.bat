@echo off
REM MEP Parser GUI 실행 (더블클릭). CLI 몰라도 됨.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python mep_gui.py
if errorlevel 1 (
  echo.
  echo [오류] 실행 실패. python 설치/PATH 확인.
  pause
)

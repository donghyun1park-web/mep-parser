@echo off
setlocal
cd /d "%~dp0"
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo ============================================================
echo   MEP CFD Studio - One-Click Setup
echo ============================================================
echo.
echo This runs every setup step in order:
echo   1. Python environment  (install_cfd.bat)
echo   2. WSL2 + OpenFOAM v2606  (install_openfoam2606.bat)
echo   3. Launch MEP CFD Studio  (run_cfd.bat)
echo.
echo If Windows needs to restart partway through, just double-click
echo this same file again after it restarts. Already-finished steps
echo are skipped automatically.
echo.
pause

echo.
echo [Step 1/3] Python environment...
echo ------------------------------------------------------------
call install_cfd.bat --no-pause
if errorlevel 1 (
  echo.
  echo [ERROR] Step 1 failed. See the message above, fix it, then
  echo run this file again.
  pause
  exit /b 1
)

echo.
echo [Step 2/3] WSL2 + OpenFOAM v2606...
echo ------------------------------------------------------------
call install_openfoam2606.bat --no-pause
set "OF_RC=%errorlevel%"
if "%OF_RC%"=="2" (
  echo.
  echo ============================================================
  echo   Windows must restart once to finish installing WSL.
  echo   Restart Windows now, then double-click this same file
  echo   ^(0_전체설치.bat^) again. Step 1 will be skipped and step 2
  echo   will continue automatically.
  echo ============================================================
  pause
  exit /b 2
)
if not "%OF_RC%"=="0" (
  echo.
  echo [ERROR] Step 2 failed. See the message above, fix it, then
  echo run this file again.
  pause
  exit /b 1
)

echo.
echo [Step 3/3] All setup finished. Starting MEP CFD Studio...
echo ------------------------------------------------------------
echo A browser window will open automatically in a few seconds.
echo Keep this window open while you use the program.
echo.
call run_cfd.bat

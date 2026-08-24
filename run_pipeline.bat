@echo off
cd /d "%~dp0"
chcp 65001 > nul
echo [INFO] The legacy FreeCAD pipeline is integrated into MEP CFD Studio.
echo Select the DXF drawing directly in the browser.
echo.
call run_cfd.bat

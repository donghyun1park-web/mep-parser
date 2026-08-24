@echo off
setlocal
cd /d "%~dp0"
chcp 65001 > nul
set "CHECK_ONLY="
set "NO_PAUSE="
set "ELEVATED="

:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--check" set "CHECK_ONLY=1"
if /i "%~1"=="--no-pause" set "NO_PAUSE=1"
if /i "%~1"=="--elevated" set "ELEVATED=1"
shift
goto :parse_args

:args_done

echo ============================================================
echo   MEP CFD Studio - OpenFOAM v2606 Setup
echo ============================================================
echo.
echo Existing CFD projects will not be deleted.
echo This installs or updates WSL, Ubuntu-24.04, and OpenFOAM v2606.
echo.

call :probe_distro
if not defined DISTRO_READY goto :wsl_missing

if defined CHECK_ONLY goto :verify

echo [1/4] Preparing Ubuntu package tools...
wsl.exe -d Ubuntu-24.04 -u root -- bash -lc "set -e; apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates"
if errorlevel 1 goto :failed

echo.
echo [2/4] Downloading the official OpenFOAM repository setup...
wsl.exe -d Ubuntu-24.04 -u root -- bash -lc "set -e; curl -fsSL https://dl.openfoam.com/add-debian-repo.sh -o /tmp/openfoam-add-debian-repo.sh; bash /tmp/openfoam-add-debian-repo.sh"
if errorlevel 1 goto :failed

echo.
echo [3/4] Installing or updating the OpenFOAM v2606 runtime...
wsl.exe -d Ubuntu-24.04 -u root -- apt-get install -y openfoam2606
if errorlevel 1 goto :failed

echo.
:verify
echo [4/4] Verifying required solver commands...
set "OFBIN=/usr/lib/openfoam/openfoam2606/platforms/linux64GccDPInt32Opt/bin"
set "VERIFY_OK="
for /f "delims=" %%L in ('wsl.exe -d Ubuntu-24.04 -u root -- bash -lc "set -e; test -x %OFBIN%/blockMesh; test -x %OFBIN%/snappyHexMesh; test -x %OFBIN%/simpleFoam; test -x %OFBIN%/buoyantBoussinesqPimpleFoam; echo OpenFOAM-v2606-ready" 2^>nul') do if "%%L"=="OpenFOAM-v2606-ready" set "VERIFY_OK=1"
if not defined VERIFY_OK goto :failed
echo OpenFOAM-v2606-ready

echo.
echo Setup and verification completed. Run run_cfd.bat next.
if not defined NO_PAUSE pause
exit /b 0

:wsl_missing
if defined CHECK_ONLY goto :missing_check
fltmc > nul 2> nul
if not errorlevel 1 goto :install_wsl
if defined ELEVATED goto :admin_failed
echo Windows administrator approval is needed once to install WSL.
echo A standard Windows approval window will open now.
set "MEP_INSTALLER=%~f0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p=Start-Process -FilePath $env:MEP_INSTALLER -ArgumentList '--elevated','--no-pause' -Verb RunAs -Wait -PassThru; exit $p.ExitCode"
set "ELEVATE_RC=%errorlevel%"
if not defined NO_PAUSE pause
exit /b %ELEVATE_RC%

:install_wsl
echo [1/4] Installing WSL and Ubuntu-24.04...
wsl.exe --install -d Ubuntu-24.04 --no-launch
if errorlevel 1 goto :failed
call :probe_distro
if defined DISTRO_READY goto :args_done
echo.
echo Windows must restart once to finish WSL installation.
echo Restart Windows, then double-click this same file again.
if not defined NO_PAUSE pause
exit /b 2

:missing_check
echo [ERROR] Ubuntu-24.04 WSL was not found.
echo Double-click this file without --check to install it automatically.
if not defined NO_PAUSE pause
exit /b 1

:admin_failed
echo [ERROR] Administrator approval was not granted.
echo Run this installer again and approve the Windows prompt.
if not defined NO_PAUSE pause
exit /b 1

:probe_distro
set "DISTRO_READY="
for /f "delims=" %%L in ('wsl.exe -d Ubuntu-24.04 -u root -- bash -lc "echo MEP_WSL_READY" 2^>nul') do if "%%L"=="MEP_WSL_READY" set "DISTRO_READY=1"
exit /b 0

:failed
echo.
echo [ERROR] Setup did not complete. Check the internet connection and retry.
if not defined NO_PAUSE pause
exit /b 1

@echo off
setlocal
cd /d "%~dp0"
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set "NO_PAUSE="
if /i "%~1"=="--no-pause" set "NO_PAUSE=1"

echo ============================================================
echo   MEP CFD Studio - Setup and Repair
echo ============================================================
echo.
echo Existing drawings and CFD projects will not be deleted.
echo Running this file again safely repairs only the Python environment.
echo.

set "BOOTSTRAP_PYTHON=python"
where python > nul 2> nul
if not errorlevel 1 goto :python_found
where py > nul 2> nul
if not errorlevel 1 goto :py_launcher_found
if exist "setup\python-3.12.8-amd64.exe" goto :install_bundled_python
goto :python_missing

:py_launcher_found
set "BOOTSTRAP_PYTHON=py -3"
goto :python_found

:install_bundled_python
echo Python was not found. Installing the bundled Python 3.12.8 ^(from setup\^)...
echo This installs for the current user only ^(no administrator rights needed^).
"setup\python-3.12.8-amd64.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1 Include_test=0
set "BOOTSTRAP_PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"
if not exist "%BOOTSTRAP_PYTHON%" goto :failed_bundled_python
echo Bundled Python installed successfully.
echo.

:python_found
%BOOTSTRAP_PYTHON% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 goto :python_old

echo [1/3] Preparing the project-local Python environment...
if exist ".venv\Scripts\python.exe" goto :venv_ready
%BOOTSTRAP_PYTHON% -m venv .venv
if errorlevel 1 goto :failed_venv

:venv_ready
echo [2/3] Installing or repairing Python libraries...
set PIP_DISABLE_PIP_VERSION_CHECK=1
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed_pip

echo.
echo [3/3] Verifying the installation...
".venv\Scripts\python.exe" -c "import ezdxf, shapely, numpy, matplotlib; print('Python libraries: ready')"
if errorlevel 1 goto :failed_verify

echo.
echo Setup completed. Double-click run_cfd.bat next.
echo CFD solving also requires WSL2 and OpenFOAM; the app checks them on startup.
goto :success

:python_missing
echo [ERROR] Python was not found.
echo Install Python 3.11 or newer, then run this file again.
echo https://www.python.org/downloads/windows/
goto :failed

:python_old
echo [ERROR] Python 3.11 or newer is required.
echo Install a current Python release, then run this file again.
goto :failed

:failed_bundled_python
echo.
echo [ERROR] The bundled Python installer did not complete successfully.
echo Install Python 3.11 or newer manually, then run this file again.
echo https://www.python.org/downloads/windows/
goto :failed

:failed_venv
echo.
echo [ERROR] Could not create the project-local Python environment.
echo Check whether security software blocks this folder, then retry.
goto :failed

:failed_pip
echo.
echo [ERROR] Library installation failed. Check the internet connection and retry.
goto :failed

:failed_verify
echo [ERROR] Installation verification failed.

:failed
if not defined NO_PAUSE pause
exit /b 1

:success
if not defined NO_PAUSE pause
exit /b 0

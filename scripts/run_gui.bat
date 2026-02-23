@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_DIR=%%~fI"
set "ENV_NAME=keithley_labtools"

set "CONDA_BAT="
call "%SCRIPT_DIR%find_conda.bat"

if not defined CONDA_BAT (
  echo.
  echo Could not find conda.
  echo Checked PATH/CONDA_EXE and common Miniforge/Miniconda/Anaconda locations.
  echo Please install Miniforge or add conda to PATH, then re-run install_windows.bat.
  echo.
  pause
  exit /b 1
)

echo.
echo Environment diagnostics:
call "%CONDA_BAT%" run -n %ENV_NAME% python -c "import importlib.util, sys, qcodes; spec=importlib.util.find_spec('qcodes.dataset'); print('python exe:', sys.executable); print('qcodes version:', qcodes.__version__); print('qcodes path:', qcodes.__file__); print('qcodes.dataset module:', spec.origin if spec else 'missing'); sys.exit(0 if spec else 1)"
if errorlevel 1 (
  echo.
  echo Environment check failed for "%ENV_NAME%".
  echo Please re-run install_windows.bat to repair the environment.
  pause
  exit /b 1
)

echo.
echo Launching Keithley GUI...
echo (If it fails, errors will be shown here.)
echo.

pushd "%REPO_DIR%"
call "%CONDA_BAT%" run -n %ENV_NAME% python "%REPO_DIR%\k_gui.py"
set "RC=%ERRORLEVEL%"
popd

if not "%RC%"=="0" (
  echo.
  echo Keithley GUI exited with error code %RC%.
  echo Press any key to close this window.
  pause >nul
  exit /b %RC%
)

exit /b 0

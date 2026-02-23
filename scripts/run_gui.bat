@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_DIR=%%~fI"

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

call "%CONDA_BAT%" activate keithley_labtools
if errorlevel 1 exit /b 1

set "PYTHON_EXE=python"

echo.
echo Launching Keithley GUI...
echo (If it fails, errors will be shown here.)
echo.

pushd "%REPO_DIR%"
%PYTHON_EXE% "%REPO_DIR%\k_gui.py"
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

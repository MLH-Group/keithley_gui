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

start "" /D "%REPO_DIR%" "%CONDA_BAT%" run -n %ENV_NAME% pythonw "%REPO_DIR%\k_plotter.py"
exit /b 0

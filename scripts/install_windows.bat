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
  echo Install Miniforge or add your existing conda install to PATH, then retry.
  echo https://conda-forge.org/miniforge/
  echo.
  pause
  exit /b 1
)

echo.
echo Using conda at: %CONDA_BAT%

call "%CONDA_BAT%" env list | findstr /I /C:"keithley_labtools" >nul
if errorlevel 1 (
  echo.
  echo Creating conda environment "keithley_labtools"...
  call "%CONDA_BAT%" env create -n keithley_labtools -f "%REPO_DIR%\environment.yml"
) else (
  echo.
  echo Updating conda environment "keithley_labtools"...
  call "%CONDA_BAT%" env update -n keithley_labtools -f "%REPO_DIR%\environment.yml" --prune
)
if errorlevel 1 goto :error

echo.
echo Verifying setuptools (pkg_resources)...
call "%CONDA_BAT%" run -n keithley_labtools python -c "import pkg_resources; print('pkg_resources ok')"
if errorlevel 1 (
  echo.
  echo pkg_resources missing. Installing setuptools...
  call "%CONDA_BAT%" install -n keithley_labtools setuptools -y
)
if errorlevel 1 goto :error

set "REPO_DIR=%REPO_DIR%"
call "%CONDA_BAT%" run -n keithley_labtools python -c "import os, site, pathlib; p=pathlib.Path(site.getsitepackages()[0])/'keithley_gui.pth'; p.write_text(os.environ['REPO_DIR'])"
if errorlevel 1 goto :error

echo.
echo Creating desktop shortcuts...
PowerShell -ExecutionPolicy Bypass -File "%REPO_DIR%\scripts\make_shortcuts.ps1"
if errorlevel 1 goto :error

echo.
echo Install completed successfully.
echo Press any key to close this window.
pause >nul
exit /b 0

:error
echo.
echo Install failed. Please review the messages above.
pause
exit /b 1

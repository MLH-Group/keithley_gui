@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_DIR=%%~fI"

set "CONDA_ACTIVATE="
if exist "%USERPROFILE%\miniforge3\Scripts\activate.bat" set "CONDA_ACTIVATE=%USERPROFILE%\miniforge3\Scripts\activate.bat"
if not defined CONDA_ACTIVATE if exist "%USERPROFILE%\miniconda3\Scripts\activate.bat" set "CONDA_ACTIVATE=%USERPROFILE%\miniconda3\Scripts\activate.bat"
if not defined CONDA_ACTIVATE if exist "%USERPROFILE%\anaconda3\Scripts\activate.bat" set "CONDA_ACTIVATE=%USERPROFILE%\anaconda3\Scripts\activate.bat"

if not defined CONDA_ACTIVATE (
  echo.
  echo Could not find conda activation script.
  echo Please install Miniforge or Miniconda, then re-run install_windows.bat.
  echo.
  pause
  exit /b 1
)

call "%CONDA_ACTIVATE%" keithley_labtools
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

@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_DIR=%%~fI"

set "CONDA_BAT="
for %%I in (conda.bat) do set "CONDA_BAT=%%~$PATH:I"
if not defined CONDA_BAT if exist "%USERPROFILE%\miniforge3\Scripts\conda.bat" set "CONDA_BAT=%USERPROFILE%\miniforge3\Scripts\conda.bat"
if not defined CONDA_BAT if exist "%USERPROFILE%\miniconda3\Scripts\conda.bat" set "CONDA_BAT=%USERPROFILE%\miniconda3\Scripts\conda.bat"
if not defined CONDA_BAT if exist "%USERPROFILE%\anaconda3\Scripts\conda.bat" set "CONDA_BAT=%USERPROFILE%\anaconda3\Scripts\conda.bat"

if not defined CONDA_BAT (
  echo.
  echo Could not find conda. Please install Miniforge or Miniconda first.
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

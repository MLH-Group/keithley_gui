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

where pythonw >nul 2>nul
if errorlevel 1 (
  set "PYTHON_EXE=python"
) else (
  set "PYTHON_EXE=pythonw"
)

start "" /D "%REPO_DIR%" %PYTHON_EXE% "%REPO_DIR%\k_plotter.py"
exit /b 0

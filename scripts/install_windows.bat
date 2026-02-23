@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_DIR=%%~fI"
set "ENV_NAME=keithley_labtools"
set "SETUPTOOLS_SPEC=setuptools<81"

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

call "%CONDA_BAT%" env list | findstr /I /C:"%ENV_NAME%" >nul
if errorlevel 1 (
  echo.
  echo Creating conda environment "%ENV_NAME%"...
  call "%CONDA_BAT%" env create -n %ENV_NAME% -f "%REPO_DIR%\environment.yml"
) else (
  echo.
  echo Updating conda environment "%ENV_NAME%"...
  call "%CONDA_BAT%" env update -n %ENV_NAME% -f "%REPO_DIR%\environment.yml" --prune
)
if errorlevel 1 goto :error

echo.
echo Verifying setuptools (pkg_resources)...
call :verify_pkg_resources
if errorlevel 1 (
  echo.
  echo pkg_resources missing. Attempting conda repair install...
  call "%CONDA_BAT%" install -n %ENV_NAME% "%SETUPTOOLS_SPEC%" -y
  if errorlevel 1 goto :error

  call :verify_pkg_resources
  if errorlevel 1 (
    echo.
    echo pkg_resources still missing after conda repair.
    echo Attempting conda force-reinstall of %SETUPTOOLS_SPEC%...
    call "%CONDA_BAT%" install -n %ENV_NAME% --force-reinstall "%SETUPTOOLS_SPEC%" -y
    if not errorlevel 1 (
      call :verify_pkg_resources
    )

    if errorlevel 1 (
      echo.
      echo Attempting pip force-reinstall of %SETUPTOOLS_SPEC%...
      call "%CONDA_BAT%" run -n %ENV_NAME% python -m pip install --upgrade --force-reinstall "%SETUPTOOLS_SPEC%"
      if errorlevel 1 goto :error

      call :verify_pkg_resources
      if errorlevel 1 goto :pkg_resources_error
    )
  )
)
if errorlevel 1 goto :error

set "REPO_DIR=%REPO_DIR%"
call "%CONDA_BAT%" run -n %ENV_NAME% python -c "import os, site, pathlib; p=pathlib.Path(site.getsitepackages()[0])/'keithley_gui.pth'; p.write_text(os.environ['REPO_DIR'])"
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

:verify_pkg_resources
call "%CONDA_BAT%" run -n %ENV_NAME% python -c "import importlib.util, setuptools, sys; spec=importlib.util.find_spec('pkg_resources'); print('setuptools version:', setuptools.__version__); print('pkg_resources module:', spec.origin if spec else 'missing'); sys.exit(0 if spec else 1)"
exit /b %errorlevel%

:pkg_resources_error
echo.
echo pkg_resources is still unavailable after repair attempts.
echo Please send this output to support so we can diagnose the Python environment.
goto :error

:error
echo.
echo Install failed. Please review the messages above.
pause
exit /b 1

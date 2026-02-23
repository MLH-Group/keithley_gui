@echo off
setlocal

set "FOUND_CONDA_BAT="

rem 1) If current shell knows CONDA_EXE, prefer that.
if defined CONDA_EXE (
  for %%I in ("%CONDA_EXE%") do (
    if exist "%%~dpIconda.bat" set "FOUND_CONDA_BAT=%%~dpIconda.bat"
    if not defined FOUND_CONDA_BAT if exist "%%~dpI..\condabin\conda.bat" set "FOUND_CONDA_BAT=%%~dpI..\condabin\conda.bat"
  )
)

rem 2) If conda.bat is directly on PATH.
if not defined FOUND_CONDA_BAT (
  for /f "delims=" %%I in ('where conda.bat 2^>nul') do (
    if not defined FOUND_CONDA_BAT set "FOUND_CONDA_BAT=%%I"
  )
)

rem 3) If conda.exe is on PATH, derive sibling/condabin conda.bat.
if not defined FOUND_CONDA_BAT (
  for /f "delims=" %%I in ('where conda.exe 2^>nul') do (
    if not defined FOUND_CONDA_BAT if exist "%%~dpIconda.bat" set "FOUND_CONDA_BAT=%%~dpIconda.bat"
    if not defined FOUND_CONDA_BAT if exist "%%~dpI..\condabin\conda.bat" set "FOUND_CONDA_BAT=%%~dpI..\condabin\conda.bat"
  )
)

rem 4) Check common install roots (user + system-wide).
if not defined FOUND_CONDA_BAT if exist "%USERPROFILE%\miniforge3\condabin\conda.bat" set "FOUND_CONDA_BAT=%USERPROFILE%\miniforge3\condabin\conda.bat"
if not defined FOUND_CONDA_BAT if exist "%USERPROFILE%\miniforge3\Scripts\conda.bat" set "FOUND_CONDA_BAT=%USERPROFILE%\miniforge3\Scripts\conda.bat"
if not defined FOUND_CONDA_BAT if exist "%USERPROFILE%\miniconda3\condabin\conda.bat" set "FOUND_CONDA_BAT=%USERPROFILE%\miniconda3\condabin\conda.bat"
if not defined FOUND_CONDA_BAT if exist "%USERPROFILE%\miniconda3\Scripts\conda.bat" set "FOUND_CONDA_BAT=%USERPROFILE%\miniconda3\Scripts\conda.bat"
if not defined FOUND_CONDA_BAT if exist "%USERPROFILE%\anaconda3\condabin\conda.bat" set "FOUND_CONDA_BAT=%USERPROFILE%\anaconda3\condabin\conda.bat"
if not defined FOUND_CONDA_BAT if exist "%USERPROFILE%\anaconda3\Scripts\conda.bat" set "FOUND_CONDA_BAT=%USERPROFILE%\anaconda3\Scripts\conda.bat"

if not defined FOUND_CONDA_BAT if exist "%LOCALAPPDATA%\miniforge3\condabin\conda.bat" set "FOUND_CONDA_BAT=%LOCALAPPDATA%\miniforge3\condabin\conda.bat"
if not defined FOUND_CONDA_BAT if exist "%LOCALAPPDATA%\miniforge3\Scripts\conda.bat" set "FOUND_CONDA_BAT=%LOCALAPPDATA%\miniforge3\Scripts\conda.bat"
if not defined FOUND_CONDA_BAT if exist "%LOCALAPPDATA%\miniconda3\condabin\conda.bat" set "FOUND_CONDA_BAT=%LOCALAPPDATA%\miniconda3\condabin\conda.bat"
if not defined FOUND_CONDA_BAT if exist "%LOCALAPPDATA%\miniconda3\Scripts\conda.bat" set "FOUND_CONDA_BAT=%LOCALAPPDATA%\miniconda3\Scripts\conda.bat"
if not defined FOUND_CONDA_BAT if exist "%LOCALAPPDATA%\anaconda3\condabin\conda.bat" set "FOUND_CONDA_BAT=%LOCALAPPDATA%\anaconda3\condabin\conda.bat"
if not defined FOUND_CONDA_BAT if exist "%LOCALAPPDATA%\anaconda3\Scripts\conda.bat" set "FOUND_CONDA_BAT=%LOCALAPPDATA%\anaconda3\Scripts\conda.bat"

if not defined FOUND_CONDA_BAT if exist "%ProgramData%\miniforge3\condabin\conda.bat" set "FOUND_CONDA_BAT=%ProgramData%\miniforge3\condabin\conda.bat"
if not defined FOUND_CONDA_BAT if exist "%ProgramData%\miniforge3\Scripts\conda.bat" set "FOUND_CONDA_BAT=%ProgramData%\miniforge3\Scripts\conda.bat"
if not defined FOUND_CONDA_BAT if exist "%ProgramData%\Miniconda3\condabin\conda.bat" set "FOUND_CONDA_BAT=%ProgramData%\Miniconda3\condabin\conda.bat"
if not defined FOUND_CONDA_BAT if exist "%ProgramData%\Miniconda3\Scripts\conda.bat" set "FOUND_CONDA_BAT=%ProgramData%\Miniconda3\Scripts\conda.bat"
if not defined FOUND_CONDA_BAT if exist "%ProgramData%\Anaconda3\condabin\conda.bat" set "FOUND_CONDA_BAT=%ProgramData%\Anaconda3\condabin\conda.bat"
if not defined FOUND_CONDA_BAT if exist "%ProgramData%\Anaconda3\Scripts\conda.bat" set "FOUND_CONDA_BAT=%ProgramData%\Anaconda3\Scripts\conda.bat"

if not defined FOUND_CONDA_BAT if exist "C:\miniforge3\condabin\conda.bat" set "FOUND_CONDA_BAT=C:\miniforge3\condabin\conda.bat"
if not defined FOUND_CONDA_BAT if exist "C:\miniforge3\Scripts\conda.bat" set "FOUND_CONDA_BAT=C:\miniforge3\Scripts\conda.bat"
if not defined FOUND_CONDA_BAT if exist "C:\Miniconda3\condabin\conda.bat" set "FOUND_CONDA_BAT=C:\Miniconda3\condabin\conda.bat"
if not defined FOUND_CONDA_BAT if exist "C:\Miniconda3\Scripts\conda.bat" set "FOUND_CONDA_BAT=C:\Miniconda3\Scripts\conda.bat"
if not defined FOUND_CONDA_BAT if exist "C:\Anaconda3\condabin\conda.bat" set "FOUND_CONDA_BAT=C:\Anaconda3\condabin\conda.bat"
if not defined FOUND_CONDA_BAT if exist "C:\Anaconda3\Scripts\conda.bat" set "FOUND_CONDA_BAT=C:\Anaconda3\Scripts\conda.bat"

endlocal & (
  if defined FOUND_CONDA_BAT (
    set "CONDA_BAT=%FOUND_CONDA_BAT%"
    exit /b 0
  )
)
exit /b 1

@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher py.exe was not found. Install Python 3 first.
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

echo.
echo Bootstrap complete.
echo Optional CUDA runtime:
echo   .venv\Scripts\python.exe -m pip install -r requirements-cuda.txt
echo.
echo If mpv is not detected, install mpv or set M1_MPV_PATH.

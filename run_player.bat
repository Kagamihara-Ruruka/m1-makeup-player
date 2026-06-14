@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

if not exist ".venv\Scripts\pythonw.exe" (
    call bootstrap_windows.bat
    if errorlevel 1 exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "scripts\run_ui.py"

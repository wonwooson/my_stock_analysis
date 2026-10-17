@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" launch.py %*
    goto finished
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 launch.py %*
    goto finished
)
where python >nul 2>nul
if not errorlevel 1 (
    python launch.py %*
    goto finished
)
echo Python 3.12 or newer is required: https://www.python.org/downloads/
:finished
pause

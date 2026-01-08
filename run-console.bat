@echo off
title GTA Business Manager - Console Mode
cd /d "%~dp0"

echo ============================================
echo    GTA Business Manager - CONSOLE MODE
echo ============================================
echo.
echo Running in console mode (no GUI, minimal overhead).
echo Money and activity tracking will display in this window.
echo.
echo Press Ctrl+C to stop.
echo ============================================
echo.

:: Check if virtual environment exists and use it
if exist "venv\Scripts\python.exe" (
    call venv\Scripts\activate.bat
)

python -m src.main --console

echo.
echo Application stopped.
pause

@echo off
title GTA Business Manager - Debug Mode
cd /d "%~dp0"

echo ============================================
echo    GTA Business Manager - DEBUG MODE
echo ============================================
echo.
echo Debug logging is enabled. More detailed
echo information will be shown.
echo.

:: Check if virtual environment exists and use it
if exist "venv\Scripts\python.exe" (
    echo Using virtual environment...
    call venv\Scripts\activate.bat
)

echo Starting in debug mode...
echo.
python -m src.main --debug

echo.
echo ============================================
echo Application exited. Check logs in:
echo %LOCALAPPDATA%\GTABusinessManager\logs\
echo ============================================
pause

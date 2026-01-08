@echo off
title GTA Business Manager
cd /d "%~dp0"

echo ============================================
echo    GTA Online Business Manager
echo ============================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo.
    echo Please install Python from: https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: Check if virtual environment exists and use it
if exist "venv\Scripts\python.exe" (
    echo Using virtual environment...
    call venv\Scripts\activate.bat
)

:: Check if dependencies are installed
python -c "import mss" >nul 2>&1
if errorlevel 1 (
    echo Dependencies not installed. Running install.bat first...
    echo.
    call install.bat
    if errorlevel 1 (
        echo Installation failed. Please run install.bat manually.
        pause
        exit /b 1
    )
)

echo Starting GTA Business Manager...
echo.
echo The app will appear in your system tray (near the clock).
echo Press Ctrl+Shift+G to toggle the overlay.
echo.
echo Close this window or press Ctrl+C to stop.
echo ============================================
echo.

python -m src.main

if errorlevel 1 (
    echo.
    echo ============================================
    echo An error occurred. Check the output above.
    echo ============================================
    pause
)

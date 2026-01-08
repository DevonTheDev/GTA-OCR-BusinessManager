@echo off
title GTA Business Manager - Installation
cd /d "%~dp0"

echo ============================================
echo    GTA Business Manager - Setup
echo ============================================
echo.

:: Check if Python is installed
echo Checking for Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Python is not installed or not in PATH.
    echo.
    echo Please follow these steps:
    echo 1. Go to https://www.python.org/downloads/
    echo 2. Download Python 3.11 or newer
    echo 3. Run the installer
    echo 4. IMPORTANT: Check the box "Add Python to PATH"
    echo 5. Complete the installation
    echo 6. Run this install.bat again
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo Found Python %PYVER%
echo.

:: Check Python version is 3.11+
python -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
    echo WARNING: Python 3.11 or newer is recommended.
    echo You have Python %PYVER%
    echo Some features may not work correctly.
    echo.
    choice /C YN /M "Continue anyway"
    if errorlevel 2 exit /b 1
)

:: Ask about virtual environment
echo.
echo Virtual environments keep this app's packages separate from other Python projects.
choice /C YN /M "Create a virtual environment (recommended)"
if errorlevel 2 goto :skip_venv

echo.
echo Creating virtual environment...
python -m venv venv
if errorlevel 1 (
    echo Failed to create virtual environment. Installing globally instead.
    goto :skip_venv
)

echo Activating virtual environment...
call venv\Scripts\activate.bat
echo Virtual environment created and activated.
echo.

:skip_venv

:: Upgrade pip
echo Upgrading pip...
python -m pip install --upgrade pip >nul 2>&1

:: Install dependencies
echo.
echo Installing dependencies...
echo This may take a few minutes...
echo.

pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo ============================================
    echo ERROR: Failed to install some dependencies.
    echo ============================================
    echo.
    echo Try running these commands manually:
    echo   pip install mss winocr opencv-python Pillow numpy
    echo   pip install SQLAlchemy PyQt6 pyqtgraph PyYAML keyboard
    echo.
    pause
    exit /b 1
)

:: Verify key dependencies
echo.
echo Verifying installation...

python -c "import mss; print('  mss: OK')"
python -c "import cv2; print('  OpenCV: OK')"
python -c "import PIL; print('  Pillow: OK')"
python -c "import numpy; print('  NumPy: OK')"
python -c "import sqlalchemy; print('  SQLAlchemy: OK')"
python -c "import PyQt6; print('  PyQt6: OK')"
python -c "import yaml; print('  PyYAML: OK')"
python -c "import keyboard; print('  keyboard: OK')"

:: Check winocr (may fail if not on Windows or Windows version too old)
python -c "import winocr; print('  winocr: OK')" 2>nul
if errorlevel 1 (
    echo   winocr: Not available (Windows 10+ required)
    echo.
    echo WARNING: OCR features will be limited without winocr.
)

echo.
echo ============================================
echo    Installation Complete!
echo ============================================
echo.
echo You can now run the app by double-clicking:
echo    run.bat
echo.
echo Or from command line:
echo    python -m src.main
echo.
if exist "venv\Scripts\python.exe" (
    echo Note: A virtual environment was created.
    echo The run.bat script will automatically use it.
)
echo.
pause

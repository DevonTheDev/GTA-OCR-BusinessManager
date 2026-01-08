@echo off
title GTA Business Manager - Uninstall
cd /d "%~dp0"

echo ============================================
echo    GTA Business Manager - Uninstall
echo ============================================
echo.
echo This will remove the virtual environment and cached files.
echo Your settings and data will NOT be deleted.
echo.
echo Data location: %LOCALAPPDATA%\GTABusinessManager\
echo.

choice /C YN /M "Continue with uninstall"
if errorlevel 2 exit /b 0

echo.
echo Removing virtual environment...
if exist "venv\" (
    rmdir /s /q venv
    echo Virtual environment removed.
) else (
    echo No virtual environment found.
)

echo.
echo Removing Python cache...
for /d /r . %%d in (__pycache__) do (
    if exist "%%d" (
        rmdir /s /q "%%d" 2>nul
    )
)
echo Python cache removed.

echo.
echo Removing build artifacts...
if exist "build\" rmdir /s /q build
if exist "dist\" rmdir /s /q dist
echo Build artifacts removed.

echo.
echo ============================================
echo    Uninstall Complete
echo ============================================
echo.
echo To completely remove the app, delete this folder.
echo.
echo To delete your settings and data, remove:
echo %LOCALAPPDATA%\GTABusinessManager\
echo.
pause

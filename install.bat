@echo off
title WalletSnapshot — Installer
color 0B

echo.
echo  ============================================================
echo    WalletSnapshot  —  First-Time Setup
echo  ============================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR]  Python not found.
    echo.
    echo  Please install Python 3.10 or newer from:
    echo  https://www.python.org/downloads/
    echo.
    echo  Make sure to tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

echo  [1/3]  Installing Python packages...
python -m pip install --upgrade pip --quiet
python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo  [ERROR]  pip install failed. Check your internet connection.
    pause
    exit /b 1
)

echo.
echo  [2/3]  Installing Playwright browsers (Chromium)...
python -m playwright install chromium
if errorlevel 1 (
    echo.
    echo  [ERROR]  Playwright browser install failed.
    pause
    exit /b 1
)

echo.
echo  [3/3]  Installing Playwright system dependencies...
python -m playwright install-deps chromium 2>nul
:: install-deps may fail on Windows — that's fine, chromium still works

echo.
echo  ============================================================
echo    Setup complete!  Run  run.bat  to start WalletSnapshot.
echo  ============================================================
echo.
pause

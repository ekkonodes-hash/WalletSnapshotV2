@echo off
title WalletSnapshot
color 0B

echo.
echo  ============================================================
echo    WalletSnapshot  —  Starting...
echo  ============================================================
echo.

:: Move to the folder that contains this .bat file
cd /d "%~dp0"

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR]  Python not found. Run install.bat first.
    pause
    exit /b 1
)

:: Check Flask is installed
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo  [ERROR]  Dependencies missing. Run install.bat first.
    pause
    exit /b 1
)

echo  Opening http://localhost:5000 in your browser...
echo  Press Ctrl+C (or close this window) to stop the server.
echo.

python app.py

echo.
echo  Server stopped.
pause

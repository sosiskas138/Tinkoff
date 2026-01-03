@echo off
setlocal enabledelayedexpansion
REM Script to run web interface on Windows
REM Add current directory to PYTHONPATH
set PYTHONPATH=%PYTHONPATH%;%CD%

REM Check if INVEST_TOKEN is set
if "%INVEST_TOKEN%"=="" (
    echo [ERROR] Environment variable INVEST_TOKEN is not set
    echo.
    echo Set sandbox token with command:
    echo   set INVEST_TOKEN=your_sandbox_token_here
    echo.
    echo Or set it via System Settings:
    echo   Control Panel - System - Environment Variables
    echo.
    pause
    exit /b 1
)

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH
    echo.
    echo Install Python 3.8+ from https://www.python.org/
    echo Make sure to check "Add Python to PATH" during installation
    echo.
    pause
    exit /b 1
)

echo [INFO] Checking dependencies...
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies for web interface...
    python -m pip install --upgrade pip
    python -m pip install flask plotly
)

echo.
echo [INFO] Starting web server...
echo [INFO] Open in browser: http://localhost:8080
echo [INFO] Press Ctrl+C to stop
echo.

REM Run web application
python web_app.py

if errorlevel 1 (
    echo.
    echo [ERROR] Error starting web server
    echo.
    pause
)

endlocal

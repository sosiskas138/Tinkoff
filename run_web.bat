@echo off
setlocal enabledelayedexpansion
REM Script to run web interface on Windows
REM ============================================
REM SET YOUR TOKEN HERE (replace your_sandbox_token_here with your actual token):
REM ============================================
set INVEST_TOKEN=your_sandbox_token_here
REM ============================================

REM Add current directory to PYTHONPATH
set PYTHONPATH=%PYTHONPATH%;%CD%

REM Check if INVEST_TOKEN is set (and not the default value)
if "%INVEST_TOKEN%"=="" (
    echo [ERROR] INVEST_TOKEN is not set
    echo.
    echo Please edit this file and set your token on line 7:
    echo   set INVEST_TOKEN=your_actual_token_here
    echo.
    pause
    exit /b 1
)

if "%INVEST_TOKEN%"=="your_sandbox_token_here" (
    echo [ERROR] Please set your actual token in this file
    echo.
    echo Edit run_web.bat and replace "your_sandbox_token_here" with your actual token
    echo on line 7: set INVEST_TOKEN=your_actual_token_here
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

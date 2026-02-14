@echo off
REM Quick setup script for Windows

echo ========================================
echo River Level Monitor - Setup Script
echo ========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8 or higher
    pause
    exit /b 1
)

echo [1/4] Creating virtual environment...
python -m venv venv
if errorlevel 1 (
    echo ERROR: Failed to create virtual environment
    pause
    exit /b 1
)

echo [2/4] Activating virtual environment...
call venv\Scripts\activate.bat

echo [3/4] Installing dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo [4/4] Setup complete!
echo.
echo ========================================
echo Next steps:
echo   1. Run: python setup_wizard.py
echo   2. Configure your monitoring location
echo   3. Run: python river_monitor.py
echo ========================================
echo.
echo Virtual environment is activated.
echo To deactivate, type: deactivate
echo.
pause

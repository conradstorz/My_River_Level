#!/bin/bash
# Quick setup script for Linux/Mac

echo "========================================"
echo "River Level Monitor - Setup Script"
echo "========================================"
echo

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is not installed"
    echo "Please install Python 3.8 or higher"
    exit 1
fi

echo "[1/4] Creating virtual environment..."
python3 -m venv venv
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to create virtual environment"
    exit 1
fi

echo "[2/4] Activating virtual environment..."
source venv/bin/activate

echo "[3/4] Installing dependencies..."
pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to install dependencies"
    exit 1
fi

echo "[4/4] Setup complete!"
echo
echo "========================================"
echo "Next steps:"
echo "  1. Activate venv: source venv/bin/activate"
echo "  2. Run: python setup_wizard.py"
echo "  3. Configure your monitoring location"
echo "  4. Run: python river_monitor.py"
echo "========================================"
echo
echo "Virtual environment created in ./venv"
echo

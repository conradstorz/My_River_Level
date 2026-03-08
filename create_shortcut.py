"""
Create a desktop shortcut for the River Monitor launcher.

Run once (as any user — no admin required):
    python create_shortcut.py

The shortcut will:
  - Execute launch.py via pythonw.exe (no console window)
  - Use the Python icon from the active virtual environment
  - Be placed on the current user's Desktop
"""

import os
import sys

try:
    import win32com.client
except ImportError:
    print("pywin32 is required: pip install pywin32")
    sys.exit(1)

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
PYTHONW = os.path.join(os.path.dirname(PYTHON), "pythonw.exe")
LAUNCH_SCRIPT = os.path.join(PROJECT_DIR, "launch.py")
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
SHORTCUT_PATH = os.path.join(DESKTOP, "River Monitor.lnk")

EXECUTOR = PYTHON  # use python.exe so the terminal window is visible

# Icon: prefer python.exe icon from the venv
ICON_PATH = EXECUTOR if os.path.exists(EXECUTOR) else PYTHON


def create():
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(SHORTCUT_PATH)
    shortcut.TargetPath = EXECUTOR
    shortcut.Arguments = f'"{LAUNCH_SCRIPT}"'
    shortcut.WorkingDirectory = PROJECT_DIR
    shortcut.IconLocation = f"{ICON_PATH},0"
    shortcut.Description = "River Monitor control tool — update, start, and open portal"
    shortcut.Save()
    print(f"Shortcut created: {SHORTCUT_PATH}")
    print(f"  Runs: {EXECUTOR} \"{LAUNCH_SCRIPT}\"")


if __name__ == "__main__":
    create()

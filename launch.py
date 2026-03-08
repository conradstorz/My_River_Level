"""
River Monitor Launcher

Double-click or run via desktop shortcut to:
  - Start the River Monitor service if it is stopped
  - Launch in debug mode (new console) if the service is not installed
  - Open the web portal in the default browser once it is responding

Run without a console window by pointing the shortcut at pythonw.exe.
"""

import os
import sys
import subprocess
import shutil
import time
import webbrowser
import urllib.request

from rich.console import Console
from rich.panel import Panel

console = Console()

PORTAL_URL = "http://localhost:5743"
SERVICE_NAME = "RiverMonitor"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
PYTHONW = os.path.join(os.path.dirname(PYTHON), "pythonw.exe")


def purge_pyc(project_dir):
    """Delete all .pyc files and __pycache__ dirs under project_dir."""
    for root, dirs, files in os.walk(project_dir):
        for name in files:
            if name.endswith(".pyc"):
                try:
                    os.remove(os.path.join(root, name))
                except OSError:
                    pass
        if "__pycache__" in dirs:
            cache_path = os.path.join(root, "__pycache__")
            try:
                shutil.rmtree(cache_path)
            except OSError:
                pass
            dirs.remove("__pycache__")  # don't recurse into it


# ── Helpers ───────────────────────────────────────────────────────────────────

def service_state():
    """Return 'RUNNING', 'STOPPED', 'NOT_FOUND', or 'UNKNOWN'."""
    try:
        result = subprocess.run(
            ["sc", "query", SERVICE_NAME],
            capture_output=True, text=True
        )
        out = result.stdout
        if result.returncode == 1060 or "does not exist" in out:
            return "NOT_FOUND"
        if "RUNNING" in out:
            return "RUNNING"
        if "STOPPED" in out:
            return "STOPPED"
        return "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def start_windows_service():
    subprocess.run(["net", "start", SERVICE_NAME], capture_output=True)


def pip_install():
    """Install/update dependencies from requirements.txt."""
    req = os.path.join(PROJECT_DIR, "requirements.txt")
    subprocess.run(
        [PYTHON, "-m", "pip", "install", "-r", req],
        cwd=PROJECT_DIR
    )


def restart_service():
    """Stop then start the Windows service."""
    subprocess.run(["net", "stop", SERVICE_NAME], capture_output=True)
    time.sleep(2)
    subprocess.run(["net", "start", SERVICE_NAME], capture_output=True)


def launch_debug_mode():
    """Start service.py debug in a new console window."""
    subprocess.Popen(
        [PYTHON, os.path.join(PROJECT_DIR, "service.py"), "debug"],
        cwd=PROJECT_DIR,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


def portal_responding():
    try:
        urllib.request.urlopen(PORTAL_URL, timeout=2)
        return True
    except Exception:
        return False


def wait_for_portal(timeout_seconds=45):
    """Poll until the portal responds or timeout expires."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if portal_responding():
            return True
        time.sleep(2)
    return False


def git_has_updates():
    """Fetch from origin and return True if remote HEAD differs from local."""
    try:
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=PROJECT_DIR, capture_output=True, check=False
        )
        local = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_DIR, capture_output=True, text=True
        ).stdout.strip()
        remote = subprocess.run(
            ["git", "rev-parse", "@{upstream}"],
            cwd=PROJECT_DIR, capture_output=True, text=True
        ).stdout.strip()
        return bool(local and remote and local != remote)
    except Exception:
        return False


def git_pull():
    """Pull latest commits; return the old HEAD hash (for diff checks)."""
    old_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_DIR, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "pull"], cwd=PROJECT_DIR, capture_output=True)
    return old_head


def requirements_changed(old_head):
    """Return True if requirements.txt changed between old_head and HEAD."""
    result = subprocess.run(
        ["git", "diff", old_head, "HEAD", "--name-only"],
        cwd=PROJECT_DIR, capture_output=True, text=True
    )
    return "requirements.txt" in result.stdout


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    console.print(Panel("[bold cyan]River Monitor Control[/bold cyan]", expand=False))

    # ── Check for updates ────────────────────────────────────────────────────
    console.print("[dim]Checking for updates...[/dim]")
    has_updates = git_has_updates()

    updated = False
    if has_updates:
        console.print("[yellow]Updates available — pulling...[/yellow]")
        old_head = git_pull()
        console.print("[green]Code updated.[/green]")

        console.print("[dim]Purging stale .pyc files...[/dim]")
        purge_pyc(PROJECT_DIR)
        console.print("[green]Bytecode cache cleared.[/green]")

        if requirements_changed(old_head):
            console.print("[yellow]requirements.txt changed — installing packages...[/yellow]")
            pip_install()
            console.print("[green]Packages installed.[/green]")

        updated = True
    else:
        console.print("[green]Already up to date.[/green]")

    # ── Manage service ───────────────────────────────────────────────────────
    state = service_state()

    if state == "RUNNING" and updated:
        console.print("[yellow]Restarting service to apply updates...[/yellow]")
        restart_service()
        console.print("[green]Service restarted.[/green]")

    elif state == "RUNNING":
        console.print("[green]Service is running.[/green]")

    elif state == "STOPPED":
        console.print("[yellow]Starting service...[/yellow]")
        start_windows_service()

    else:
        # NOT_FOUND or UNKNOWN — no service installed, use debug mode
        if not portal_responding():
            console.print("[yellow]Service not installed — launching in debug mode...[/yellow]")
            launch_debug_mode()

    # ── Wait for portal ──────────────────────────────────────────────────────
    if not portal_responding():
        console.print("[dim]Waiting for portal to respond...[/dim]")
        if not wait_for_portal():
            console.print("[red]Portal did not respond in time — opening browser anyway.[/red]")
        else:
            console.print("[green]Portal is ready.[/green]")

    # ── Open browser ─────────────────────────────────────────────────────────
    console.print(f"[cyan]Opening {PORTAL_URL}[/cyan]")
    webbrowser.open(PORTAL_URL)

    console.print("\n[dim]Press Enter to close this window.[/dim]")
    input()


if __name__ == "__main__":
    main()

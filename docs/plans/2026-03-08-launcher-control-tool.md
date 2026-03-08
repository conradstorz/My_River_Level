# Smart Launcher Control Tool Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform `launch.py` into a smart terminal control tool that checks for git updates, purges stale `.pyc` files, manages the Windows service, and opens the browser — all automatically.

**Architecture:** Modify `launch.py` in place, adding git/pyc/pip helpers and rewriting `main()` to follow the smart flow. Use `rich` for styled terminal output. Update `create_shortcut.py` to use `python.exe` instead of `pythonw.exe` so the terminal window appears.

**Tech Stack:** Python stdlib (subprocess, os, shutil), `rich` (Console, Panel, Live, Spinner), `pywin32` (existing, for shortcut only)

---

### Task 1: Add `rich` to requirements and install it

**Files:**
- Modify: `requirements.txt`

**Step 1: Add rich to requirements.txt**

In `requirements.txt`, under the `# Interactive setup` section, replace:
```
# Interactive setup
colorama
```
with:
```
# Interactive setup
colorama
rich
```

**Step 2: Install it**

Run:
```
pip install rich
```
Expected: `Successfully installed rich-...` or `Requirement already satisfied`

**Step 3: Commit**

```bash
git add requirements.txt
git commit -m "feat: add rich to requirements for launcher UI"
```

---

### Task 2: Implement and test `purge_pyc()`

**Files:**
- Modify: `launch.py`
- Create: `tests/test_launcher.py`

**Step 1: Write the failing test**

Create `tests/test_launcher.py`:

```python
import os
import sys
import pytest

# launch.py is at the project root, not in a package — import directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import launch


def test_purge_pyc_removes_pyc_files(tmp_path):
    # Create a .pyc file inside a subdir
    pkg = tmp_path / "monitor"
    pkg.mkdir()
    pyc = pkg / "polling.cpython-311.pyc"
    pyc.write_text("fake bytecode")

    launch.purge_pyc(str(tmp_path))

    assert not pyc.exists()


def test_purge_pyc_removes_pycache_dirs(tmp_path):
    cache = tmp_path / "monitor" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "polling.cpython-311.pyc").write_text("fake")

    launch.purge_pyc(str(tmp_path))

    assert not cache.exists()


def test_purge_pyc_leaves_py_files_intact(tmp_path):
    src = tmp_path / "monitor" / "polling.py"
    src.parent.mkdir()
    src.write_text("# source")

    launch.purge_pyc(str(tmp_path))

    assert src.exists()
```

**Step 2: Run to verify it fails**

```
pytest tests/test_launcher.py -v
```
Expected: `FAILED` — `ImportError` or `AttributeError: module 'launch' has no attribute 'purge_pyc'`

**Step 3: Implement `purge_pyc` in `launch.py`**

Add after the existing imports at the top of `launch.py`:

```python
import shutil
```

Add this function after the module-level constants (before the `# ── Helpers` section):

```python
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
```

**Step 4: Run tests to verify they pass**

```
pytest tests/test_launcher.py -v
```
Expected: 3 PASSED

**Step 5: Commit**

```bash
git add launch.py tests/test_launcher.py
git commit -m "feat: purge_pyc helper with tests"
```

---

### Task 3: Implement and test git helpers

**Files:**
- Modify: `launch.py`
- Modify: `tests/test_launcher.py`

**Step 1: Write failing tests**

Append to `tests/test_launcher.py`:

```python
def test_git_has_updates_returns_true_when_hashes_differ(mocker):
    mocker.patch("launch.subprocess.run", side_effect=[
        mocker.MagicMock(returncode=0),          # git fetch
        mocker.MagicMock(stdout="abc123\n"),      # HEAD
        mocker.MagicMock(stdout="def456\n"),      # @{upstream}
    ])
    assert launch.git_has_updates() is True


def test_git_has_updates_returns_false_when_up_to_date(mocker):
    mocker.patch("launch.subprocess.run", side_effect=[
        mocker.MagicMock(returncode=0),
        mocker.MagicMock(stdout="abc123\n"),
        mocker.MagicMock(stdout="abc123\n"),
    ])
    assert launch.git_has_updates() is False


def test_git_has_updates_returns_false_on_error(mocker):
    mocker.patch("launch.subprocess.run", side_effect=Exception("no git"))
    assert launch.git_has_updates() is False


def test_git_pull_returns_old_head(mocker):
    mock_run = mocker.patch("launch.subprocess.run")
    mock_run.return_value = mocker.MagicMock(stdout="abc123\n", returncode=0)
    old = launch.git_pull()
    assert old == "abc123"


def test_requirements_changed_true(mocker):
    mocker.patch("launch.subprocess.run",
                 return_value=mocker.MagicMock(stdout="requirements.txt\n"))
    assert launch.requirements_changed("abc123") is True


def test_requirements_changed_false(mocker):
    mocker.patch("launch.subprocess.run",
                 return_value=mocker.MagicMock(stdout=""))
    assert launch.requirements_changed("abc123") is False
```

**Step 2: Run to verify they fail**

```
pytest tests/test_launcher.py -v
```
Expected: new tests FAIL with `AttributeError`

**Step 3: Implement the git helpers in `launch.py`**

Add these functions in the `# ── Helpers` section:

```python
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
```

**Step 4: Run tests**

```
pytest tests/test_launcher.py -v
```
Expected: all tests PASSED

**Step 5: Commit**

```bash
git add launch.py tests/test_launcher.py
git commit -m "feat: git helpers (fetch, pull, requirements diff) with tests"
```

---

### Task 4: Implement `pip_install` and `restart_service`

**Files:**
- Modify: `launch.py`
- Modify: `tests/test_launcher.py`

**Step 1: Write failing tests**

Append to `tests/test_launcher.py`:

```python
def test_pip_install_calls_pip(mocker):
    mock_run = mocker.patch("launch.subprocess.run")
    launch.pip_install()
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "pip" in " ".join(args) or args[1] == "-m"
    assert "requirements.txt" in " ".join(args)


def test_restart_service_stops_then_starts(mocker):
    mock_run = mocker.patch("launch.subprocess.run")
    launch.restart_service()
    calls = [c[0][0] for c in mock_run.call_args_list]
    assert any("stop" in " ".join(c).lower() for c in calls)
    assert any("start" in " ".join(c).lower() for c in calls)
```

**Step 2: Run to verify they fail**

```
pytest tests/test_launcher.py::test_pip_install_calls_pip tests/test_launcher.py::test_restart_service_stops_then_starts -v
```
Expected: FAIL with `AttributeError`

**Step 3: Implement in `launch.py`**

Add after `start_windows_service()`:

```python
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
```

**Step 4: Run all tests**

```
pytest tests/test_launcher.py -v
```
Expected: all PASSED

**Step 5: Commit**

```bash
git add launch.py tests/test_launcher.py
git commit -m "feat: pip_install and restart_service helpers with tests"
```

---

### Task 5: Rewrite `main()` with smart flow and rich output

**Files:**
- Modify: `launch.py`

This task has no unit tests — the smart flow is an integration of all the helpers already tested. Manual verification covers it.

**Step 1: Add rich imports at the top of `launch.py`**

Replace the existing imports block with:

```python
import os
import sys
import subprocess
import time
import webbrowser
import urllib.request
import shutil

from rich.console import Console
from rich.panel import Panel

console = Console()
```

**Step 2: Replace `main()` entirely**

Replace the existing `main()` function with:

```python
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
```

**Step 3: Run all tests to confirm nothing broke**

```
pytest tests/test_launcher.py -v
```
Expected: all PASSED

**Step 4: Manual smoke test**

Run directly:
```
python launch.py
```
Expected: Rich panel appears, update check runs, service starts or opens browser.

**Step 5: Commit**

```bash
git add launch.py
git commit -m "feat: smart launch flow with rich terminal output"
```

---

### Task 6: Update `create_shortcut.py` to show a terminal window

**Files:**
- Modify: `create_shortcut.py`

**Step 1: Switch from pythonw.exe to python.exe**

In `create_shortcut.py`, change the `EXECUTOR` line from:
```python
EXECUTOR = PYTHONW if os.path.exists(PYTHONW) else PYTHON
```
to:
```python
EXECUTOR = PYTHON  # use python.exe so the terminal window is visible
```

**Step 2: Update the shortcut description**

Change:
```python
shortcut.Description = "Open the River Level Monitor web portal"
```
to:
```python
shortcut.Description = "River Monitor control tool — update, start, and open portal"
```

**Step 3: Recreate the shortcut**

```
python create_shortcut.py
```
Expected: `Shortcut created: ...\Desktop\River Monitor.lnk` with `python.exe` in the runs line.

**Step 4: Manual test**

Double-click the desktop shortcut. Expected: a terminal window opens and runs the smart flow.

**Step 5: Commit**

```bash
git add create_shortcut.py
git commit -m "feat: shortcut uses python.exe for visible terminal window"
```

---

### Task 7: Run full test suite

**Step 1: Run all tests**

```
pytest -v
```
Expected: all existing tests + new launcher tests pass, no regressions.

**Step 2: Commit CLAUDE.md changes if any**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md to reflect launcher changes"
```

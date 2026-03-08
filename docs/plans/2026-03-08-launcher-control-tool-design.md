# Design: Smart Launcher / Control Tool

**Date:** 2026-03-08
**File:** `launch.py` (modified in place)

## Goal

Transform the desktop shortcut launcher into a full-featured interactive control tool. When double-clicked, it opens a terminal, checks for code updates, ensures the service is running with the latest code, then opens the browser.

## Approach

Modify `launch.py` directly. Switch `create_shortcut.py` to use `python.exe` instead of `pythonw.exe` so a terminal window appears. Use `rich` for styled output.

## Smart Flow

```
1. Print banner ("River Monitor Control")
2. git fetch origin                          → show "Checking for updates..."
3. Compare HEAD vs origin HEAD
4. If updates available:
     a. git pull                             → show commit summary
     b. Purge all .pyc files and __pycache__ dirs under project root
     c. If requirements.txt changed between old HEAD and new HEAD:
          pip install -r requirements.txt    → show output
5. Check service state (RUNNING / STOPPED / NOT_FOUND / UNKNOWN)
6. Decide action:
     - RUNNING  + updates pulled  → stop service, start service (restart)
     - RUNNING  + no updates      → nothing (open browser)
     - STOPPED  + any             → start service
     - NOT_FOUND / UNKNOWN        → launch debug mode (new console)
7. Wait for portal to respond (http://localhost:5743), up to 45s
8. Open browser
9. Print summary line and pause briefly so output is readable
```

## Components

### `launch.py` changes

- Add `rich` imports (`Console`, `Panel`, `Spinner`, `Text`)
- Add `git_has_updates()` — runs `git fetch`, compares `HEAD` vs `origin/<branch>`
- Add `git_pull()` — runs `git pull`, returns old HEAD for requirements diff check
- Add `purge_pyc()` — walks project tree, deletes `*.pyc` and `__pycache__` dirs
- Add `requirements_changed(old_head)` — checks if `requirements.txt` differs between old HEAD and current HEAD via `git diff`
- Add `pip_install()` — runs `pip install -r requirements.txt`
- Add `restart_service()` — stops then starts the Windows service
- Existing helpers (`service_state`, `start_windows_service`, `launch_debug_mode`, `portal_responding`, `wait_for_portal`) retained with minor updates
- `main()` rewritten to follow smart flow above

### `create_shortcut.py` changes

- `EXECUTOR` preference switches from `pythonw.exe` to `python.exe` so the terminal window appears

## Error Handling

- Git not available or fetch fails → skip update check, warn user, continue to service start
- `pip install` fails → print warning, continue (don't abort)
- Service restart fails → fall back to debug mode
- Portal never responds → open browser anyway (shows error page)

## Dependencies

- `rich` — already likely in requirements; add if missing
- `pywin32` — already used by `create_shortcut.py`

## Testing

Manual: run `python launch.py` directly and verify each branch:
- Service running, no updates → browser opens immediately
- Service running, updates available → pull + pyc purge + restart + browser
- Service not running → start + browser
- Service not installed → debug mode + browser

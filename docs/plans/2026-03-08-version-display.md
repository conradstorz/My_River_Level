# Version Display on Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Show the running project version and release date on the dashboard page.

**Architecture:** A new `version.py` at the project root holds `VERSION` and `RELEASE_DATE` as constants. The dashboard route imports them and passes them to the template. The dashboard template renders them below the `<h1>` heading. A `v1.0.0` git tag is created after the commit.

**Tech Stack:** Python, Flask/Jinja2, Bootstrap 5

---

### Task 1: Create `version.py`

**Files:**
- Create: `version.py`
- Test: `tests/test_version.py`

**Step 1: Write the failing test**

Create `tests/test_version.py`:

```python
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import version


def test_version_string():
    assert version.VERSION == "1.0.0"


def test_release_date_string():
    assert version.RELEASE_DATE == "2026-03-08"
```

**Step 2: Run to verify it fails**

Run: `venv/Scripts/python -m pytest tests/test_version.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'version'`

**Step 3: Create `version.py`**

```python
VERSION = "1.0.0"
RELEASE_DATE = "2026-03-08"
```

**Step 4: Run to verify it passes**

Run: `venv/Scripts/python -m pytest tests/test_version.py -v`
Expected: 2 PASSED

**Step 5: Commit**

```bash
git add version.py tests/test_version.py
git commit -m "feat: version.py with VERSION and RELEASE_DATE constants"
```

---

### Task 2: Pass version to dashboard route

**Files:**
- Modify: `web/routes.py` (line 1 imports, line 55 render_template call)
- Test: `tests/web/test_dashboard.py`

**Step 1: Write the failing test**

Append to `tests/web/test_dashboard.py`:

```python
def test_dashboard_shows_version(client):
    response = client.get("/")
    assert b"1.0.0" in response.data


def test_dashboard_shows_release_date(client):
    response = client.get("/")
    assert b"2026-03-08" in response.data
```

**Step 2: Run to verify they fail**

Run: `venv/Scripts/python -m pytest tests/web/test_dashboard.py::test_dashboard_shows_version tests/web/test_dashboard.py::test_dashboard_shows_release_date -v`
Expected: FAIL — version/date not in response

**Step 3: Update `web/routes.py`**

Add import at the top of `web/routes.py` (after the existing imports):

```python
from version import VERSION, RELEASE_DATE
```

Change the dashboard `render_template` call (currently line 55) from:

```python
        return render_template("dashboard.html", sites=sites, recent_notifications=recent_notifications)
```

to:

```python
        return render_template(
            "dashboard.html",
            sites=sites,
            recent_notifications=recent_notifications,
            version=VERSION,
            release_date=RELEASE_DATE,
        )
```

**Step 4: Run to verify they still fail** (template not updated yet)

Run: `venv/Scripts/python -m pytest tests/web/test_dashboard.py::test_dashboard_shows_version -v`
Expected: still FAIL — template doesn't render them yet

**Step 5: Update `web/templates/dashboard.html`**

Replace the `<h1>` line:

```html
<h1 class="mb-4">Dashboard</h1>
```

with:

```html
<h1 class="mb-1">Dashboard</h1>
<p class="text-muted small mb-4">v{{ version }} &mdash; released {{ release_date }}</p>
```

**Step 6: Run all dashboard tests**

Run: `venv/Scripts/python -m pytest tests/web/test_dashboard.py -v`
Expected: all 5 PASSED

**Step 7: Commit**

```bash
git add web/routes.py web/templates/dashboard.html
git commit -m "feat: show version and release date on dashboard"
```

---

### Task 3: Tag v1.0.0 and run full suite

**Step 1: Run full test suite**

Run: `venv/Scripts/python -m pytest -q`
Expected: all tests pass

**Step 2: Create git tag**

```bash
git tag -a v1.0.0 -m "Release v1.0.0"
```

**Step 3: Push tag**

```bash
git push origin v1.0.0
```

# Design: Version Display on Dashboard

**Date:** 2026-03-08

## Goal

Show the running project version and release date on the dashboard page.

## Approach

Hardcoded `version.py` at project root. Dashboard route passes values to template. Git tag `v1.0.0` created to match.

## Components

### `version.py` (new)
```python
VERSION = "1.0.0"
RELEASE_DATE = "2026-03-08"
```

### `web/routes.py`
Dashboard route imports `VERSION` and `RELEASE_DATE` from `version.py` and passes them to the template context.

### `web/templates/dashboard.html`
Displays version and release date — small, unobtrusive (e.g. footer or header subtitle).

### Git tag
Commit tagged `v1.0.0` after implementation.

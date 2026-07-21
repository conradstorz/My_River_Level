# Settings Sub-Pages by Type Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the single flat Settings form into three sub-pages by type (Monitoring, Alert Thresholds, Notification Channels), each its own URL with save-per-page.

**Architecture:** Replace the flat `SETTINGS_FIELDS` with a grouped `SETTINGS_GROUPS` structure; `/settings` redirects to the first group; `/settings/<slug>` renders one group (with a tab nav) and saves only that group's keys. The template renders the active group's sectioned fields.

**Tech Stack:** Python 3, Flask, Jinja2, Bootstrap 5, pytest.

## Global Constraints

- Never chain shell commands (`&&`, `||`, `|`, `;`). One command per Bash call.
- **Tests run only in Docker** (plain pytest can't reach the DB):
  `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest <path> -v`
- No change to which settings exist, their keys, or storage; keep the existing password-prefill behavior and `autocomplete="new-password"`.
- Keep the `settings` endpoint name (the `base.html` nav link points at `/settings`).

---

### Task 1: Group settings into sub-pages (data + routes + template + tests)

**Files:**
- Modify: `web/routes.py:25-38` (`SETTINGS_FIELDS` → `SETTINGS_GROUPS` + flattened alias) and `web/routes.py:386-401` (the `settings` route → `settings` redirect + `settings_group`)
- Modify: `web/templates/settings.html` (tab nav + sectioned form)
- Test: `tests/web/test_settings.py`, `tests/web/test_app.py`

**Interfaces:**
- Produces: `SETTINGS_GROUPS` (list of `{slug, title, sections:[{subtitle, fields:[(key,label,type)]}]}`); routes `settings` (GET → redirect) and `settings_group` (GET|POST `/settings/<slug>`). Template consumes `groups`, `active`, `current`.

- [ ] **Step 1: Write/replace the failing tests**

Replace the body of `tests/web/test_settings.py` (keep the imports + `client` fixture at the top, lines 1-11) with these tests:

```python
def test_settings_redirects_to_first_group(client):
    response = client.get("/settings")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings/monitoring")


def test_monitoring_page_shows_fields_and_all_tabs(client):
    response = client.get("/settings/monitoring")
    assert response.status_code == 200
    body = response.data.decode()
    assert "Poll Interval" in body
    # tab nav lists all three groups
    assert "Monitoring" in body
    assert "Alert Thresholds" in body
    assert "Notification Channels" in body


def test_channels_page_shows_provider_subtitles(client):
    body = client.get("/settings/channels").data.decode()
    assert "Telegram" in body
    assert "Twilio" in body
    assert "Facebook" in body


def test_unknown_group_is_404(client):
    assert client.get("/settings/bogus").status_code == 404


def test_post_group_saves_only_its_fields(client, tmp_db):
    response = client.post("/settings/monitoring", data={
        "poll_interval_minutes": "30",
        "historical_start_year": "1980",
        "search_radius_miles": "25",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert get_setting("poll_interval_minutes", tmp_db) == "30"


def test_saving_one_group_leaves_other_groups_untouched(client, tmp_db):
    # Seed a monitoring value, then save the channels group.
    from db.models import set_setting
    set_setting("poll_interval_minutes", "42", tmp_db)
    client.post("/settings/channels", data={
        "telegram_bot_token": "tok",
        "twilio_account_sid": "",
        "twilio_auth_token": "",
        "twilio_sms_number": "",
        "twilio_whatsapp_number": "",
        "facebook_page_token": "",
        "facebook_verify_token": "",
    }, follow_redirects=True)
    assert get_setting("poll_interval_minutes", tmp_db) == "42"  # untouched
    assert get_setting("telegram_bot_token", tmp_db) == "tok"
```

In `tests/web/test_app.py`, update `test_settings_page_returns_200` (lines 25-27) to follow the redirect:

```python
def test_settings_page_returns_200(client):
    response = client.get("/settings", follow_redirects=True)
    assert response.status_code == 200
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web/test_settings.py tests/web/test_app.py -v`
Expected: FAIL — `/settings` still returns 200 (not 302), `/settings/monitoring` and `/settings/bogus` are 404 (routes don't exist yet).

- [ ] **Step 3: Replace `SETTINGS_FIELDS` with `SETTINGS_GROUPS`**

In `web/routes.py`, replace the `SETTINGS_FIELDS = [...]` block (lines 25-38, through the `facebook_verify_token` entry) with:

```python
SETTINGS_GROUPS = [
    {
        "slug": "monitoring",
        "title": "Monitoring",
        "sections": [
            {"subtitle": None, "fields": [
                ("poll_interval_minutes", "Poll Interval (minutes)", "number"),
                ("historical_start_year", "Historical Start Year", "number"),
                ("search_radius_miles", "Search Radius (miles)", "number"),
            ]},
        ],
    },
    {
        "slug": "thresholds",
        "title": "Alert Thresholds",
        "sections": [
            {"subtitle": None, "fields": [
                ("low_percentile", "Low Flow Percentile", "number"),
                ("high_percentile", "High Flow Percentile", "number"),
                ("very_low_percentile", "Very Low Percentile", "number"),
                ("very_high_percentile", "Very High Percentile", "number"),
                ("reminder_low_high_hours", "Reminder Interval: LOW/HIGH (hours)", "number"),
                ("reminder_severe_hours", "Reminder Interval: SEVERE (hours)", "number"),
            ]},
        ],
    },
    {
        "slug": "channels",
        "title": "Notification Channels",
        "sections": [
            {"subtitle": "Telegram", "fields": [
                ("telegram_bot_token", "Telegram Bot Token", "password"),
            ]},
            {"subtitle": "Twilio (SMS / WhatsApp)", "fields": [
                ("twilio_account_sid", "Twilio Account SID", "text"),
                ("twilio_auth_token", "Twilio Auth Token", "password"),
                ("twilio_sms_number", "Twilio SMS Number", "text"),
                ("twilio_whatsapp_number", "Twilio WhatsApp Number", "text"),
            ]},
            {"subtitle": "Facebook Messenger", "fields": [
                ("facebook_page_token", "Facebook Page Token", "password"),
                ("facebook_verify_token", "Facebook Verify Token", "text"),
            ]},
        ],
    },
]

# Flattened view — kept so the old module-level name still resolves.
SETTINGS_FIELDS = [f for g in SETTINGS_GROUPS
                   for s in g["sections"] for f in s["fields"]]
```

- [ ] **Step 4: Replace the `settings` route with a redirect + `settings_group`**

In `web/routes.py`, replace the entire `settings` function (the `@app.route("/settings", ...)` block, lines ~386-401) with:

```python
    @app.route("/settings")
    def settings():
        """GET /settings — redirect to the first settings sub-page."""
        return redirect(url_for("settings_group", slug=SETTINGS_GROUPS[0]["slug"]))

    @app.route("/settings/<slug>", methods=["GET", "POST"])
    def settings_group(slug):
        """GET|POST /settings/<slug> — view or save one group of settings.

        GET renders the group's sections plus the tab nav. POST persists only
        this group's fields and redirects back to the same tab. Unknown slug → 404.
        """
        from flask import abort
        group = next((g for g in SETTINGS_GROUPS if g["slug"] == slug), None)
        if not group:
            abort(404)
        db_path = current_app.config["DB_PATH"]
        keys = [f[0] for s in group["sections"] for f in s["fields"]]
        if request.method == "POST":
            for key in keys:
                set_setting(key, request.form.get(key, ""), db_path)
            flash("Settings saved.", "success")
            return redirect(url_for("settings_group", slug=slug))
        current = {key: get_setting(key, db_path, default="") for key in keys}
        return render_template("settings.html", groups=SETTINGS_GROUPS,
                               active=group, current=current)
```

- [ ] **Step 5: Rewrite `settings.html` with tabs + sections**

Replace the whole of `web/templates/settings.html` with:

```html
{% extends "base.html" %}
{% block title %}Settings — {{ active.title }}{% endblock %}
{% block content %}
<h1 class="mb-4">Settings</h1>
<ul class="nav nav-tabs mb-4">
  {% for g in groups %}
  <li class="nav-item">
    <a class="nav-link {% if g.slug == active.slug %}active{% endif %}"
       href="{{ url_for('settings_group', slug=g.slug) }}">{{ g.title }}</a>
  </li>
  {% endfor %}
</ul>
<form method="post" action="{{ url_for('settings_group', slug=active.slug) }}">
  {% for section in active.sections %}
    {% if section.subtitle %}<h5 class="mt-4 mb-3">{{ section.subtitle }}</h5>{% endif %}
    {% for key, label, input_type in section.fields %}
    <div class="mb-3 row">
      <label class="col-sm-4 col-form-label">{{ label }}</label>
      <div class="col-sm-6">
        <input type="{{ input_type }}" name="{{ key }}"
               value="{{ current[key] }}" class="form-control"
               {% if input_type == 'password' %}autocomplete="new-password"{% endif %}>
      </div>
    </div>
    {% endfor %}
  {% endfor %}
  <button type="submit" class="btn btn-primary">Save {{ active.title }}</button>
</form>
{% endblock %}
```

- [ ] **Step 6: Run the settings + app tests to verify they pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest tests/web/test_settings.py tests/web/test_app.py -v`
Expected: PASS — redirect, per-group render, tabs, subtitles, 404, per-group save, and cross-group isolation all green.

- [ ] **Step 7: Commit**

```bash
git add web/routes.py web/templates/settings.html tests/web/test_settings.py tests/web/test_app.py
git commit -m "feat: group settings into sub-pages by type (monitoring/thresholds/channels)"
```

---

### Task 2: Full suite

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest -q`
Expected: PASS — all tests green, no regressions (no other route/test references the old single-form `/settings` POST).

- [ ] **Step 2: Commit (empty) to mark verification**

```bash
git commit --allow-empty -m "chore: settings sub-pages verified (full suite green)"
```

---

## Self-Review

**Spec coverage:**
- Three groups (Monitoring/Thresholds/Channels), channels sub-grouped → Task 1 `SETTINGS_GROUPS`. ✓
- Separate URLs, save-per-page → Task 1 `settings_group` (saves only `keys`). ✓
- `/settings` redirects, `settings` endpoint kept → Task 1. ✓
- Tab nav + sectioned template → Task 1 Step 5. ✓
- Password prefill/behavior unchanged → template preserves the input markup + `autocomplete`. ✓
- Tests (redirect, render, tabs, subtitles, 404, per-group save, isolation, test_app update) → Task 1 Step 1. ✓

**Placeholder scan:** none — all code and tests are complete.

**Type consistency:** the template reads `groups`, `active` (`.title`, `.slug`, `.sections`), `active.sections[].subtitle`/`.fields`, and `current[key]`; the route passes exactly those (`groups=SETTINGS_GROUPS, active=group, current=current`). `settings_group` is referenced by `url_for` in both the redirect and the template tab links and form action. The flattened `SETTINGS_FIELDS` alias preserves the old name. ✓

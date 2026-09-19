#!/usr/bin/env python
"""Consistency checks for the operator manual under docs/.

Run: python scripts/check_docs.py
Exit 0 when every check passes. The script reads source files as text so it
needs no dependencies and no database.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

ENV_VARS = [
    "DATABASE_URL", "TEST_DATABASE_URL", "TEST_DB_SUFFIX", "FLASK_SECRET_KEY",
    "ADMIN_USERNAME", "ADMIN_PASSWORD", "ADMIN_PASSWORD_HASH", "TRUSTED_PROXY_COUNT",
]

# Real identifiers from the known deployment that must never appear in docs.
FORBIDDEN = ["hpz440", "gte@", "conradstorz", "conrad@", "Conrad"]

INVENTORY = [
    "README.md",
    "tutorials/first-run.md",
    "howto/this-deployment.md", "howto/upgrade.md", "howto/backup-restore.md",
    "howto/secrets.md", "howto/telegram-bot.md", "howto/twilio-and-facebook.md",
    "howto/add-gauges-as-admin.md", "howto/reverse-proxy.md", "howto/diagnose.md",
    "howto/run-tests.md",
    "reference/settings.md", "reference/environment.md", "reference/database.md",
    "reference/http-routes.md", "reference/telegram-commands.md", "reference/alerts.md",
    "reference/threads.md", "reference/cli.md",
    "explanation/architecture.md", "explanation/usgs-classification.md",
    "explanation/noaa-flood-categories.md", "explanation/gauge-quality-grading.md",
    "explanation/alert-routing-and-sensitivity.md", "explanation/pin-discovery.md",
    "explanation/source-retirement.md", "explanation/security-model.md",
]

failures = []


def fail(msg):
    failures.append(msg)
    print("FAIL:", msg)


def read(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


def check_items(label, items, doc, fmt):
    text = read(DOCS / doc)
    if not text:
        fail(f"{doc} is missing")
        return
    for item in items:
        if fmt.format(item) not in text:
            fail(f"{label} {item!r} not documented in {doc}")


def settings_keys():
    src = read(ROOT / "db" / "models.py")
    block = src.split("DEFAULT_SETTINGS = {", 1)[1].split("\n}", 1)[0]
    return re.findall(r'^\s*"([a-z_]+)":', block, re.M)


def routes():
    out = []
    for name in ("web/routes.py", "web/health.py"):
        out += re.findall(r'@app\.route\("([^"]+)"', read(ROOT / name))
    return out


def tables():
    return re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", read(ROOT / "db" / "models.py"))


def commands():
    out = []
    for name in ("monitor/adapters/telegram.py", "monitor/adapters/telegram_commands.py"):
        out += re.findall(r'CommandHandler\("([a-z]+)"', read(ROOT / name))
    return out


def check_links():
    files = list(DOCS.rglob("*.md")) + [ROOT / "README.md"]
    link = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    for md in files:
        if "superpowers" in md.parts or "plans" in md.parts:
            continue
        for target in link.findall(read(md)):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            rel = target.split("#", 1)[0]
            if not rel:
                continue
            if not (md.parent / rel).resolve().exists():
                fail(f"{md.relative_to(ROOT)} links to missing {target}")


def check_forbidden():
    for md in DOCS.rglob("*.md"):
        if "superpowers" in md.parts or "plans" in md.parts:
            continue
        text = read(md)
        for word in FORBIDDEN:
            if word in text:
                fail(f"{md.relative_to(ROOT)} contains forbidden identifier {word!r}")


def check_inventory():
    for rel in INVENTORY:
        if not (DOCS / rel).exists():
            fail(f"missing document docs/{rel}")


def check_verified_lines():
    for md in (DOCS / "reference").glob("*.md"):
        if "Verified against commit " not in read(md).strip().splitlines()[-1]:
            fail(f"{md.relative_to(ROOT)} lacks a final 'Verified against commit' line")


def main():
    check_inventory()
    check_items("setting", settings_keys(), "reference/settings.md", "`{}`")
    check_items("route", routes(), "reference/http-routes.md", "`{}`")
    check_items("table", tables(), "reference/database.md", "`{}`")
    check_items("command", commands(), "reference/telegram-commands.md", "`/{}`")
    check_items("env var", ENV_VARS, "reference/environment.md", "`{}`")
    check_links()
    check_forbidden()
    check_verified_lines()
    if failures:
        print(f"{len(failures)} failure(s)")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

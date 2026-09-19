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

# The six official placeholder tokens used throughout the operator manual.
PLACEHOLDERS = [
    "docker-host", "docker-user", "portal-host", "db-host", "db-password",
    "bot-username",
]

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


def doc_files():
    """Every markdown file the checks operate on, excluding scratch/plan docs."""
    files = list(DOCS.rglob("*.md")) + [ROOT / "README.md"]
    return [md for md in files if "superpowers" not in md.parts and "plans" not in md.parts]


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


# ---------------------------------------------------------------------------
# Doc -> code direction: every item *documented* as a setting/route/table/
# command must actually exist in the code, not just the reverse.
# ---------------------------------------------------------------------------

def doc_settings_keys():
    text = read(DOCS / "reference/settings.md")
    return re.findall(r"^\|\s*`([a-z_]+)`\s*\|", text, re.M)


def doc_routes():
    text = read(DOCS / "reference/http-routes.md")
    return re.findall(r"^\|[^|]+\|\s*`(/[^`]*)`", text, re.M)


def doc_tables():
    text = read(DOCS / "reference/database.md")
    return re.findall(r"^##\s+`([a-z_]+)`\s*$", text, re.M)


def doc_commands():
    text = read(DOCS / "reference/telegram-commands.md")
    return re.findall(r"^\|\s*`(/[a-z]+)`", text, re.M)


def check_doc_to_code():
    code_settings = set(settings_keys())
    for key in doc_settings_keys():
        if key not in code_settings:
            fail(f"documented setting {key!r} not in code")

    code_routes = set(routes())
    for path in doc_routes():
        if path not in code_routes:
            fail(f"documented route {path!r} not in code")

    code_tables = set(tables())
    for table in doc_tables():
        if table not in code_tables:
            fail(f"documented table {table!r} not in code")

    code_commands = {f"/{c}" for c in commands()}
    for cmd in doc_commands():
        if cmd not in code_commands:
            fail(f"documented command {cmd!r} not in code")


def heading_slugs(text):
    """GitHub-style slugs for every #/##/### heading in `text`."""
    slugs = []
    for line in text.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)$", line.strip())
        if not m:
            continue
        heading = m.group(2).strip().lower()
        heading = re.sub(r"[^a-z0-9\s-]", "", heading)
        heading = re.sub(r"\s+", "-", heading).strip("-")
        slugs.append(heading)
    return slugs


def check_links():
    link = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    for md in doc_files():
        for target in link.findall(read(md)):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if "#" in target:
                rel, frag = target.split("#", 1)
            else:
                rel, frag = target, None
            if rel:
                target_path = (md.parent / rel).resolve()
                if not target_path.exists():
                    fail(f"{md.relative_to(ROOT)} links to missing {target}")
                    continue
            else:
                target_path = md
            if frag and target_path.suffix == ".md":
                if frag not in heading_slugs(read(target_path)):
                    fail(
                        f"{md.relative_to(ROOT)} links to {target} but "
                        f"#{frag} is not a heading in {target_path.relative_to(ROOT)}"
                    )


def check_forbidden():
    for md in doc_files():
        text = read(md)
        for word in FORBIDDEN:
            if word in text:
                fail(f"{md.relative_to(ROOT)} contains forbidden identifier {word!r}")


def check_inventory():
    if not (ROOT / "README.md").exists():
        fail("missing document README.md")
    for rel in INVENTORY:
        if not (DOCS / rel).exists():
            fail(f"missing document docs/{rel}")


def check_verified_lines():
    for md in (DOCS / "reference").glob("*.md"):
        if "Verified against commit " not in read(md).strip().splitlines()[-1]:
            fail(f"{md.relative_to(ROOT)} lacks a final 'Verified against commit' line")


# A backticked token immediately used as markdown link text
# (`[`token`](target)`) is already validated — by target, not by this token —
# in check_links; strip those spans out before looking for bare file mentions.
_LINK_SPAN = re.compile(r"\[[^\]]*\]\([^)]*\)")
_FILE_TOKEN = re.compile(
    r"`((?:scripts/|docs/|web/|db/|monitor/|tests/)?[A-Za-z0-9_./-]+\.(?:py|yml|yaml|md|html|example))`"
)


def check_referenced_files():
    for md in doc_files():
        stripped = _LINK_SPAN.sub("", read(md))
        for tok in _FILE_TOKEN.findall(stripped):
            if "<" in tok or "*" in tok:
                continue
            if not (ROOT / tok).exists():
                fail(f"{md.relative_to(ROOT)} references missing file {tok!r}")


def _letters_only(s):
    return re.sub(r"[^a-z]", "", s.lower())


def check_placeholder_spelling():
    official_letters = {_letters_only(p) for p in PLACEHOLDERS}
    token_re = re.compile(r"<([a-z0-9_-]+)>")
    for md in DOCS.rglob("*.md"):
        if "superpowers" in md.parts or "plans" in md.parts:
            continue
        for tok in token_re.findall(read(md)):
            if tok in PLACEHOLDERS:
                continue
            if _letters_only(tok) in official_letters:
                fail(f"{md.relative_to(ROOT)} uses near-miss placeholder <{tok}>")


def main():
    check_inventory()
    check_items("setting", settings_keys(), "reference/settings.md", "`{}`")
    check_items("route", routes(), "reference/http-routes.md", "`{}`")
    check_items("table", tables(), "reference/database.md", "`{}`")
    check_items("command", commands(), "reference/telegram-commands.md", "`/{}`")
    check_items("env var", ENV_VARS, "reference/environment.md", "`{}`")
    check_doc_to_code()
    check_links()
    check_forbidden()
    check_verified_lines()
    check_referenced_files()
    check_placeholder_spelling()
    if failures:
        print(f"{len(failures)} failure(s)")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""Smoke-check the public edge: only the public pages answer, nothing else.

Run from the CLI machine:
    python scripts/smoke_public_edge.py [--base-url URL] [--view-token TOKEN]

Expects 200 from /healthz, /pin (redirect followed) and /view/<token>, and 404
from /, /settings, /webhook/twilio and /pages/new (page creation is
maintainer-only, so the edge must not route it). Prints one line per check
and exits 1 on any mismatch; the view token is printed only as its first three characters.
Standard library only. Note: each run's /pin request creates one pending page
and counts against that route's per-IP rate limit.
"""
import argparse
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://river.ohr3023.org"
USER_AGENT = "river-monitor-smoke-check/1"

# (path, label, expected status); None as path means "the /view/<token> check".
# The label is what gets printed: a page's public token is its only access
# control, so it never reaches the terminal in full.
PUBLIC = 200
BLOCKED = 404


def build_checks(view_token):
    view = (f"/view/{view_token}", f"/view/{view_token[:3]}...") if view_token else (None, None)
    return [
        ("/healthz", "/healthz", PUBLIC),
        ("/pin", "/pin", PUBLIC),
        (*view, PUBLIC),
        ("/", "/", BLOCKED),
        ("/settings", "/settings", BLOCKED),
        ("/webhook/twilio", "/webhook/twilio", BLOCKED),
        ("/pages/new", "/pages/new", BLOCKED),
    ]


def fetch_status(url, timeout):
    """Final HTTP status for a GET of `url` (redirects followed)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as err:
        return err.code


def run(base_url, view_token, timeout):
    base_url = base_url.rstrip("/")
    failures = 0
    for path, label, expected in build_checks(view_token):
        if path is None:
            print("SKIP /view/<token>: no --view-token given")
            continue
        try:
            status = fetch_status(base_url + path, timeout)
        except (urllib.error.URLError, OSError) as err:
            reason = getattr(err, "reason", err)
            print(f"FAIL {label}: expected {expected}, got error: {reason}")
            failures += 1
            continue
        if status == expected:
            print(f"PASS {label}: {status}")
        else:
            print(f"FAIL {label}: expected {expected}, got {status}")
            failures += 1
    print(f"{failures} failure(s) against {base_url}")
    return 1 if failures else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"edge to check (default {DEFAULT_BASE_URL})")
    parser.add_argument("--view-token",
                        help="public_token of a live page, for the /view/<token> check")
    parser.add_argument("--timeout", type=float, default=10.0,
                        help="seconds per request (default 10)")
    args = parser.parse_args(argv)
    return run(args.base_url, args.view_token, args.timeout)


if __name__ == "__main__":
    sys.exit(main())

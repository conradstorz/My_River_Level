"""Tests for scripts/smoke_public_edge.py, run against a local stub HTTP server."""
import importlib.util
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke_public_edge.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("smoke_public_edge", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load_script()

TOKEN = "pubtok123"

# What a correctly published edge serves: public routes open, everything else 404.
GOOD_EDGE = {
    "/healthz": 200,
    "/pin": 200,
    f"/view/{TOKEN}": 200,
}


@pytest.fixture
def edge():
    """Start a stub server; yields (base_url, routes) where routes is mutable."""
    routes = dict(GOOD_EDGE)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status = routes.get(self.path, 404)
            if isinstance(status, tuple):  # (302, location)
                self.send_response(status[0])
                self.send_header("Location", status[1])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b"ok" if status == 200 else b"nope"
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", routes
    server.shutdown()
    server.server_close()


def _lines(capsys):
    return [line for line in capsys.readouterr().out.splitlines() if line.strip()]


def test_healthy_edge_passes_every_check_and_exits_zero(edge, capsys):
    base, _ = edge
    code = smoke.main(["--base-url", base, "--view-token", TOKEN])
    lines = _lines(capsys)

    assert code == 0
    checks = [line for line in lines if line.startswith(("PASS", "FAIL"))]
    assert len(checks) == 8
    assert all(line.startswith("PASS") for line in checks)
    for path in ("/healthz", "/pin", "/view/", "/settings", "/webhook/twilio",
                 "/pages/new", "/admin/pages"):
        assert any(path in line for line in checks), path


def test_view_token_is_never_printed_in_full(edge, capsys):
    # The public token is a page's only access control; keep it off the
    # terminal (and out of scrollback and logs) whether the check passes or fails.
    base, routes = edge
    code = smoke.main(["--base-url", base, "--view-token", TOKEN])
    out = capsys.readouterr().out
    assert code == 0
    assert TOKEN not in out
    assert "PASS /view/pub...: 200" in out

    routes[f"/view/{TOKEN}"] = 500
    smoke.main(["--base-url", base, "--view-token", TOKEN])
    out = capsys.readouterr().out
    assert TOKEN not in out
    assert "FAIL /view/pub...: expected 200, got 500" in out


def test_admin_route_exposed_fails_that_check_and_exits_nonzero(edge, capsys):
    base, routes = edge
    routes["/settings"] = 401  # portal reachable from the internet

    code = smoke.main(["--base-url", base, "--view-token", TOKEN])
    lines = _lines(capsys)

    assert code != 0
    failed = [line for line in lines if line.startswith("FAIL")]
    assert len(failed) == 1
    assert "/settings" in failed[0]
    assert "401" in failed[0]
    assert sum(line.startswith("PASS") for line in lines) == 7


def test_page_creation_exposed_fails_that_check(edge, capsys):
    # Page creation is maintainer-only; the edge must not route /pages/new.
    base, routes = edge
    routes["/pages/new"] = 401

    code = smoke.main(["--base-url", base, "--view-token", TOKEN])
    failed = [line for line in _lines(capsys) if line.startswith("FAIL")]

    assert code != 0
    assert len(failed) == 1
    assert failed[0].startswith("FAIL /pages/new:")
    assert "401" in failed[0]


def test_admin_subscriber_pages_exposed_fails_that_check(edge, capsys):
    # Hand-adding page subscribers lives under /admin/pages, maintainer-only.
    base, routes = edge
    routes["/admin/pages"] = 401

    code = smoke.main(["--base-url", base, "--view-token", TOKEN])
    failed = [line for line in _lines(capsys) if line.startswith("FAIL")]

    assert code != 0
    assert len(failed) == 1
    assert failed[0].startswith("FAIL /admin/pages:")


def test_root_served_publicly_fails(edge, capsys):
    base, routes = edge
    routes["/"] = 200

    code = smoke.main(["--base-url", base, "--view-token", TOKEN])
    failed = [line for line in _lines(capsys) if line.startswith("FAIL")]

    assert code != 0
    assert len(failed) == 1
    assert failed[0].startswith("FAIL /:")
    assert "200" in failed[0]


def test_public_route_blocked_fails(edge, capsys):
    base, routes = edge
    del routes["/healthz"]

    code = smoke.main(["--base-url", base, "--view-token", TOKEN])
    failed = [line for line in _lines(capsys) if line.startswith("FAIL")]

    assert code != 0
    assert len(failed) == 1
    assert "/healthz" in failed[0]


def test_pin_redirect_is_followed_to_the_map(edge, capsys):
    # /pin creates a pending page and redirects to /pin/<edit_token>.
    base, routes = edge
    routes["/pin"] = (302, "/pin/edittok")
    routes["/pin/edittok"] = 200

    code = smoke.main(["--base-url", base, "--view-token", TOKEN])

    assert code == 0


def test_without_view_token_the_view_check_is_skipped(edge, capsys):
    base, _ = edge

    code = smoke.main(["--base-url", base])
    lines = _lines(capsys)

    assert code == 0
    assert sum(line.startswith("PASS") for line in lines) == 7
    assert any(line.startswith("SKIP") and "/view/" in line for line in lines)


def test_unreachable_host_fails_every_check(capsys):
    # Port 9 on loopback: nothing listens, connection is refused.
    code = smoke.main(["--base-url", "http://127.0.0.1:9", "--view-token", TOKEN, "--timeout", "2"])
    lines = _lines(capsys)

    assert code != 0
    assert sum(line.startswith("FAIL") for line in lines) == 8
    assert not any(line.startswith("PASS") for line in lines)


def test_default_base_url_is_the_public_hostname():
    assert smoke.DEFAULT_BASE_URL == "https://river.ohr3023.org"

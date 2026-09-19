# Environment variables

Variables read from the container environment (compose passes them from `.env`); everything else is a database setting.

| variable | required | default | read by | notes |
|---|---|---|---|---|
| `ADMIN_PASSWORD` | One of `ADMIN_PASSWORD` or `ADMIN_PASSWORD_HASH` | none | `web/auth.py` (`admin_credentials`) | Plaintext fallback, hashed on first use. With neither `ADMIN_PASSWORD` nor `ADMIN_PASSWORD_HASH` set, every admin route returns 503. `docker-compose.yml` does not currently forward this variable into the container — set `ADMIN_PASSWORD_HASH` instead, or add `ADMIN_PASSWORD` to its `environment:` block. |
| `ADMIN_PASSWORD_HASH` | One of `ADMIN_PASSWORD` or `ADMIN_PASSWORD_HASH` (preferred) | none | `web/auth.py` (`admin_credentials`) | A werkzeug `method$salt$hash`; every `$` must be written `$$` in `.env` or docker compose interpolation eats the salt — see [`../howto/secrets.md`](../howto/secrets.md). With neither `ADMIN_PASSWORD` nor `ADMIN_PASSWORD_HASH` set, every admin route returns 503. A hash that is set but malformed also returns 503. |
| `ADMIN_USERNAME` | No | `admin` | `web/auth.py` (`admin_credentials`) | HTTP Basic login name for the portal. |
| `DATABASE_URL` | Yes (`docker-compose.yml` fails loudly with `:?` if unset) | `postgresql://river:river@db:5432/rivermonitor` | `db/models.py` (every DB helper) | The application's PostgreSQL connection string. In Docker this points at the shared server's `postgres` hostname. |
| `FLASK_SECRET_KEY` | Yes (`docker-compose.yml` fails loudly with `:?` if unset) | `river-monitor-dev-secret` | `web/app.py` (`create_app`) | Flask session signing key. |
| `TEST_DATABASE_URL` | Required by the Docker test overlay (`:?`); optional for a host `pytest` run | `postgresql://river:river@localhost:5432/river_test` | `tests/conftest.py` | Used only by the test suite, never by the running service. |
| `TEST_DB_SUFFIX` | No (compose only) | `` (empty) | `docker-compose.test.yml` | Appended to `TEST_DATABASE_URL` by the test overlay, so parallel test runs can target distinct databases, e.g. `TEST_DB_SUFFIX=_x`. |
| `TRUSTED_PROXY_COUNT` | No | `0` | `web/app.py` (`create_app`) | Number of reverse proxies whose `X-Forwarded-For` / `X-Forwarded-Proto` to trust; `0` disables `ProxyFix` entirely — see [`../howto/reverse-proxy.md`](../howto/reverse-proxy.md). `docker-compose.yml` does not currently forward this variable into the container either. |

Verified against commit c12d91c

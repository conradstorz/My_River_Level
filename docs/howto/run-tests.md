# Running the test suite

1. Prerequisite: the shared-postgres server is up (a separate project).
   ```bash
   docker compose up -d
   ```
   Run this in the **shared-postgres** project directory. Expected: its
   `docker compose ps` shows the postgres container running; `river_test` is
   auto-created if missing.

2. Run the whole suite, from this project's directory.
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest -q
   ```
   Expected: pytest output ending in `N passed`, no failures. The whole suite
   takes about 9 minutes.

3. Run one file (or any pytest path / `-k` expression) instead.
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest -q tests/monitor/test_polling.py
   ```
   Expected: only that file's tests run, in well under a minute.

4. Run against an isolated database for a parallel run.
   ```bash
   TEST_DB_SUFFIX=_x docker compose -f docker-compose.yml -f docker-compose.test.yml run --build --rm test pytest -q
   ```
   Expected: the suite runs against `river_test_x` instead of `river_test`, so
   this run doesn't collide with another one using the default suffix.

`--build` is mandatory on every run, not just the first: `Dockerfile.test`
`COPY`s the source and `tests/` into the image at build time, so a `run`
without `--build` reuses whatever image was built last — stale code, stale
tests, or no image at all.

The classic builder in use here (no buildx on the CLI machine) ignores
`Dockerfile.test.dockerignore`, so `tests/` deliberately stays out of the
main `.dockerignore` — removing it would starve `Dockerfile.test` of the
tests it needs to `COPY`. The production `Dockerfile` is what keeps `tests/`
out of the runtime image, with `RUN rm -rf tests` after its own `COPY`.

5. Reading a failure: the pytest output names the failing test id and
   traceback as usual. A `psycopg2` connection error instead of a test
   failure usually means step 1 wasn't done, or `TEST_DATABASE_URL` isn't set
   — see [`../reference/environment.md`](../reference/environment.md).

## Plain pytest

```bash
pytest
```

Only works with a `TEST_DATABASE_URL` this machine can actually open a TCP
connection to — the Docker-only default in `db/models.py` (`localhost`) has
no meaning on a machine that isn't the Docker host. On the Windows CLI
machine driving a remote daemon, that generally means this path only works
run from `<docker-host>` (or wherever else the `postgres` service is
reachable), not from the CLI machine itself; use the Docker overlay above
from the CLI machine instead.

## If it went wrong

- `pytest` can't find the `tests` directory — [`diagnose.md#tests-cannot-find-tests-directory`](diagnose.md#tests-cannot-find-tests-directory)
- Tests can't reach the database — [`diagnose.md#tests-cannot-reach-the-database`](diagnose.md#tests-cannot-reach-the-database)

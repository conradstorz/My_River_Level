FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# tests/ rides along in the build context so Dockerfile.test can use it (see
# .dockerignore); it has no business in the runtime image.
RUN rm -rf tests

RUN mkdir -p logs

# Run as a non-root user. /app/logs must be owned by `river` before the named
# volume is declared, so a freshly created volume inherits that ownership.
# An EXISTING root-owned app_logs volume will not be re-chowned by Docker —
# see the upgrade note in README.md.
RUN useradd --create-home --uid 10001 river && chown -R river:river /app
USER river

EXPOSE 5743

CMD ["python", "main.py"]

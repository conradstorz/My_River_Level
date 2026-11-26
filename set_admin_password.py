"""Set ADMIN_PASSWORD_HASH in .env, escaped for docker compose interpolation.

Run:  uv run --with werkzeug python set_admin_password.py

Compose interpolates `$name` in docker-compose.yml, so every `$` in the
werkzeug hash must be written as `$$` in .env or the salt is eaten. Doing the
escaping here — instead of in a shell one-liner — keeps PowerShell from
expanding `$$` (its "last token of previous command" variable) to nothing.
"""

import getpass
import pathlib
import sys

from werkzeug.security import check_password_hash, generate_password_hash

DOLLAR = chr(36)
ENV = pathlib.Path(__file__).with_name(".env")
KEY = "ADMIN_PASSWORD_HASH="

password = getpass.getpass("New portal password: ")
if not password:
    sys.exit("Empty password; nothing written.")
if password != getpass.getpass("Confirm: "):
    sys.exit("Passwords differ; nothing written.")

hash_ = generate_password_hash(password)
assert check_password_hash(hash_, password), "sanity check failed"
escaped = hash_.replace(DOLLAR, DOLLAR * 2)

lines = ENV.read_text(encoding="utf-8").splitlines()
for i, line in enumerate(lines):
    if line.startswith(KEY):
        lines[i] = KEY + escaped
        break
else:
    lines.append(KEY + escaped)
ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")

print(f"Wrote {ENV} ({hash_.count(DOLLAR)} separators escaped).")
print("Now run: docker compose up -d --force-recreate app")

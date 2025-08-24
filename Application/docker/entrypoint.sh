#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] Starting pay4 web…"

# Defaults (compose/env can override)
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_SSLMODE:=require}"
: "${DJANGO_SETTINGS_MODULE:=pay4.settings}"

echo "[entrypoint] DB host=$POSTGRES_HOST port=$POSTGRES_PORT db=$POSTGRES_DB user=$POSTGRES_USER sslmode=$POSTGRES_SSLMODE"

# Wait for RDS to accept SSL connections
echo "[entrypoint] Waiting for Postgres to be reachable…"
python - <<'PY'
import os, time, sys, psycopg
host=os.environ["POSTGRES_HOST"]
port=os.environ.get("POSTGRES_PORT","5432")
db=os.environ["POSTGRES_DB"]
user=os.environ["POSTGRES_USER"]
pwd=os.environ["POSTGRES_PASSWORD"]
sslmode=os.environ.get("POSTGRES_SSLMODE","require")

for i in range(60):  # up to ~60*2s = 120s
    try:
        with psycopg.connect(f"host={host} port={port} dbname={db} user={user} password={pwd} sslmode={sslmode}", connect_timeout=5) as conn:
            with conn.cursor() as cur: cur.execute("select 1")
        print("[entrypoint] Postgres is reachable.")
        sys.exit(0)
    except Exception as e:
        print(f"[entrypoint] DB not ready yet ({e}); retrying…")
        time.sleep(2)

print("[entrypoint] Timed out waiting for Postgres.", file=sys.stderr)
sys.exit(1)
PY

# Run migrations (safe to retry)
echo "[entrypoint] Running migrations…"
python manage.py migrate

# Finally run the server
echo "[entrypoint] Launching gunicorn…"
exec gunicorn --bind 0.0.0.0:8000 pay4.wsgi:application

#!/usr/bin/env sh
# Production entrypoint: apply migrations, then serve. Fails fast if the database is unreachable.
set -e
python -m alembic upgrade head
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8020}" \
  --proxy-headers --forwarded-allow-ips="*" --no-server-header

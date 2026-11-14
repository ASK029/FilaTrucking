#!/usr/bin/env bash
# Run the Django test suites locally on SQLite with throw-away fake settings (never touches a real DB).
# usage: qa/run_tests.sh [labels...]   (default: qa.tests shipments vehicles customers drivers)
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Prefer a venv built from requirements.txt (qa/tools/audit/venv_deps: python -m venv ... && pip install -r requirements.txt)
# over the developer's .venv, which may lag behind the pinned versions.
PY="${PY:-$ROOT/qa/tools/audit/venv_deps/Scripts/python}"
[ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python"; [ -x "$PY" ] || PY="$ROOT/.venv/bin/python"
export PYTHONPATH="$ROOT"
export DOTENV_PATH="$ROOT/qa/.no-such-env"   # hermetic: never read a developer's real .env
export DATABASE_URL="sqlite:///:memory:"
export SECRET_KEY="qa-fake-secret-key-not-for-production-0123456789abcdef0123456789abcdef"
export WHATSAPP_INGEST_SECRET="qa-fake-ingest-secret-0123456789"
export CELERY_BROKER_URL="memory://"
export DEBUG="True" ALLOWED_HOSTS="testserver,localhost,127.0.0.1"
cd "$ROOT/FilaTrucking"
if [ $# -eq 0 ]; then set -- qa.tests; fi
"$PY" manage.py test "$@" --noinput

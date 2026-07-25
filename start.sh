#!/usr/bin/env bash
# tradr — start the backend (FastAPI) and frontend (Vite) locally.
# Both bind to 127.0.0.1. Ctrl-C stops both. See ARCHITECTURE.md.
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3.11}"
API_HOST="127.0.0.1"
API_PORT="8000"
WEB_PORT="5173"

# --- .env ---
if [ ! -f .env ]; then
  echo "→ no .env found; copying from .env.example (edit it for secrets/provider)"
  cp .env.example .env
fi

# --- Python venv + backend deps ---
if [ ! -d .venv ]; then
  echo "→ creating virtualenv (.venv) with $PYTHON"
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
if ! python -c "import fastapi, uvicorn, sqlalchemy" >/dev/null 2>&1; then
  echo "→ installing backend dependencies"
  pip install -q --upgrade pip
  pip install -q -e ".[dev]"
fi

# --- Frontend deps ---
if [ ! -d frontend/node_modules ]; then
  echo "→ installing frontend dependencies (npm install)"
  (cd frontend && npm install --no-audit --no-fund)
fi

# --- Launch both; kill both on exit ---
pids=()
cleanup() {
  echo ""
  echo "→ shutting down…"
  for pid in "${pids[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "→ starting API on http://$API_HOST:$API_PORT  (docs at /docs)"
PYTHONPATH=src uvicorn tradr.api.main:app --host "$API_HOST" --port "$API_PORT" &
pids+=($!)

echo "→ starting web UI on http://$API_HOST:$WEB_PORT"
(cd frontend && npm run dev) &
pids+=($!)

echo ""
echo "tradr is running.  Web UI → http://$API_HOST:$WEB_PORT"
echo "(To run the Telegram bot, set TELEGRAM_* in .env, then: PYTHONPATH=src python -m tradr.bot)"
echo "Press Ctrl-C to stop."
wait

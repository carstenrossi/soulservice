#!/usr/bin/env bash
#
# restart.sh — rebuild and restart the full Soulservice stack.
#
# Stops any running instances (local dev processes + Docker containers),
# rebuilds the images, applies database migrations, and brings everything
# back up: Postgres, MCP server, Web UI, and Mailpit.
#
# The Postgres data volume is preserved across restarts. Pass --reset-db
# to wipe it and start from an empty database.
#
# Usage:
#   ./restart.sh            # rebuild + restart everything
#   ./restart.sh --reset-db # also drop the Postgres volume (DESTROYS DATA)

set -euo pipefail

# Always operate from the repository root (where this script lives).
cd "$(dirname "$0")"

RESET_DB=0
for arg in "$@"; do
  case "$arg" in
    --reset-db) RESET_DB=1 ;;
    -h|--help)
      sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*" >&2; }
die() { printf '\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

# --- Preconditions ---------------------------------------------------------

command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH."
docker compose version >/dev/null 2>&1 || die "the 'docker compose' plugin is required."
[ -f .env ] || die ".env not found. Copy .env.example to .env and fill it in."
command -v uv >/dev/null 2>&1 || die "uv is required on the host to run database migrations."

# Load .env so host-side migrations get POSTGRES_PASSWORD, SOULSERVICE_MASTER_KEY, etc.
set -a
# shellcheck disable=SC1091
. ./.env
set +a

: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set in .env}"
: "${SOULSERVICE_MASTER_KEY:?SOULSERVICE_MASTER_KEY must be set in .env}"

# Host connects to Postgres on the published port 6000 (container 5432).
export DATABASE_URL="postgresql+asyncpg://soulservice:${POSTGRES_PASSWORD}@localhost:6000/soulservice"

# --- 1. Stop running instances --------------------------------------------

log "Stopping local dev processes (if any)"
# These only match host processes, never anything inside the containers.
pkill -f "soulservice.mcp.server" 2>/dev/null && echo "  killed local MCP server" || true
pkill -f "soulservice.web" 2>/dev/null && echo "  killed local web server" || true

log "Stopping Docker containers"
if [ "$RESET_DB" -eq 1 ]; then
  warn "--reset-db: dropping the Postgres volume (all data will be lost)."
  docker compose --profile web down --remove-orphans --volumes
else
  docker compose --profile web down --remove-orphans
fi

# --- 2. Start Postgres and wait for it to be healthy -----------------------

log "Starting Postgres"
docker compose up -d postgres

log "Waiting for Postgres to accept connections"
for i in $(seq 1 30); do
  if docker compose exec -T postgres pg_isready -U soulservice >/dev/null 2>&1; then
    echo "  Postgres is ready."
    break
  fi
  if [ "$i" -eq 30 ]; then
    die "Postgres did not become ready in time. Check 'docker compose logs postgres'."
  fi
  sleep 1
done

# --- 3. Apply database migrations (from the host) --------------------------

log "Applying database migrations (alembic upgrade head)"
uv run alembic upgrade head

# --- 4. Rebuild and start the rest of the stack ----------------------------

log "Building and starting MCP server, Web UI, and Mailpit"
docker compose --profile web up -d --build

# --- 5. Summary ------------------------------------------------------------

log "Stack status"
docker compose --profile web ps

cat <<'EOF'

Soulservice is up:
  - MCP server : http://localhost:6001
  - Web UI     : http://localhost:6002
  - Mailpit    : http://localhost:6004  (magic-link inbox)
  - Postgres   : localhost:6000

Tail logs with:  docker compose --profile web logs -f
EOF

# ============================================================
# Soulservice – Multi-Stage Dockerfile
# Targets: mcp, web
# ============================================================

FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

COPY src/ ./src/
COPY george.yaml ./george.yaml

# --- MCP Server ---
FROM base AS mcp
EXPOSE 8000
CMD ["uv", "run", "python", "-m", "soulservice.mcp.server"]

# --- Web UI (Phase 3) ---
FROM base AS web
EXPOSE 8000
CMD ["uv", "run", "python", "-m", "soulservice.web"]

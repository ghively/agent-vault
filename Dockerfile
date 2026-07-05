# syntax=docker/dockerfile:1
#
# Multi-stage build: compile the web UI with Node, then ship it inside a slim
# Python runtime that serves both the API and the built SPA (see api/app.py,
# which serves web/dist when present).

# ---- Stage 1: build the web UI ----
FROM node:20-slim AS webbuild
WORKDIR /web
# Copy manifests first for better layer caching on dependency-only changes.
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# Install the package. Editable install keeps the source tree at /app so the
# SPA-serving path in api/app.py (parents[2] / "web" / "dist") resolves to
# /app/web/dist. Copy metadata + source before the dist for layer caching.
COPY pyproject.toml README.md LICENSE ./
COPY agent_vault/ ./agent_vault/
RUN pip install --no-cache-dir -e .

# Bring in the built SPA so "/" serves the UI.
COPY --from=webbuild /web/dist ./web/dist

# Bind on all interfaces so the container is reachable when its port is mapped.
# NOTE: with AGENT_VAULT_HOST=0.0.0.0 the service is *fail-closed* — it refuses
# to start unless a token is configured (VAULT_TOKEN / registry/tokens.yaml /
# VAULT_TOKENS) or AGENT_VAULT_ALLOW_OPEN=1 is set. This is deliberate: an
# unauthenticated vault must never be exposed on the network by default.
ENV AGENT_VAULT_HOST=0.0.0.0 \
    AGENT_VAULT_PORT=7778 \
    AGENT_VAULT_PATH=/vault
EXPOSE 7778

# Run as an unprivileged user; /vault is the mount point for vault data.
RUN useradd -u 10001 -m appuser \
    && mkdir -p /vault \
    && chown -R appuser /vault /app
USER appuser

# Liveness probe against the open health endpoint (no auth needed).
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7778/api/health').status==200 else 1)"

CMD ["agent-vault-serve"]

# Deploying Agent Vault

The service is a single FastAPI app (`agent-vault-serve`) that serves both the
JSON API under `/api` and the built web UI at `/`. It reads a vault directory
(`entities/`, `registry/`, `raw/`, `discovery/`) from `AGENT_VAULT_PATH`. There
is no database and no external service to run — just the process and the vault
files on disk.

## Quick start (Docker Compose)

```sh
# 1. Put your vault under ./vault (entities/, registry/, raw/, discovery/)
# 2. Generate a token
export VAULT_TOKEN=$(head -c 32 /dev/urandom | base64)
# 3. Build and run
docker compose up --build
```

The UI is then at `http://localhost:7778/`; paste the token into the gate. The
API is at `http://localhost:7778/api`.

The image is a multi-stage build (`Dockerfile`): Node compiles the UI, then a
slim Python runtime serves the API + `web/dist` as an unprivileged user.

## Configuration

All configuration is via environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `AGENT_VAULT_HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` in a container. |
| `AGENT_VAULT_PORT` | `7778` | TCP port. |
| `AGENT_VAULT_PATH` | `.` | Vault data directory. |
| `VAULT_TOKEN` | _(unset)_ | Single full-access bearer token. |
| `VAULT_TOKENS` | _(unset)_ | JSON `{"<token>": {"actor": "...", "scopes": [...]}}` for scoped multi-token auth. |
| `AGENT_VAULT_ALLOW_OPEN` | _(unset)_ | Set to `1` to deliberately run with **no auth** on a non-loopback bind. |
| `AGENT_VAULT_LOCK_TIMEOUT_S` | `600` | Bounded wait for the vault write lock (`0` = wait forever). |

Tokens can also be configured in `registry/tokens.yaml` inside the vault. The
three sources (`VAULT_TOKEN`, `registry/tokens.yaml`, `VAULT_TOKENS`) are merged.

## Authentication is fail-closed on a network bind

When **no** token is configured, every `/api` call is open (actor `anonymous`,
all scopes). That is fine on the default `127.0.0.1` loopback bind, but a silent
data-exposure risk on a routable address. The service therefore **refuses to
start** if you bind a non-loopback host (`0.0.0.0`, a LAN IP, …) with auth
disabled. To proceed you must either:

- configure a token (recommended), or
- set `AGENT_VAULT_ALLOW_OPEN=1` to opt into an open bind deliberately, or
- keep the default loopback bind and expose it via a reverse proxy.

## TLS / reverse proxy

The bearer token travels in the `Authorization` header, so **terminate TLS in
front of the service** — do not expose plain HTTP to untrusted networks. Bind
the app to loopback (or an internal network) and put nginx/Caddy/Traefik ahead
of it. Example nginx:

```nginx
server {
    listen 443 ssl;
    server_name vault.example.com;
    ssl_certificate     /etc/letsencrypt/live/vault.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/vault.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:7778;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # SSE (job log streaming) needs buffering off:
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
```

## Scaling

The vault is a single-host, file-based store. `agent-vault-serve` runs one
uvicorn process; that is the intended deployment (a home NAS / single box with
sequential cadences). Notes:

- **Do not run multiple hosts against one vault over NFS.** The write lock is a
  POSIX `flock` and degrades to best-effort on some NFS mounts (it warns loudly
  on stderr when it can't lock). Concurrent mutating passes across hosts are not
  serialized safely.
- For more read throughput on one host, front it with the reverse proxy and, if
  needed, run additional read-only replicas pointed at a read-only copy of the
  vault. Writers must remain single.

## Operations

- **Health:** `GET /api/health` is unauthenticated and returns `{"ok": true}` —
  use it for liveness probes (the Docker image already has a `HEALTHCHECK`).
- **Backups:** `agent-vault-backup export|restore|list` snapshots the vault.
  Run `export` on a schedule and copy the snapshot off-box.
- **Schema migrations:** `agent-vault-migrate check` / `apply` walks the vault
  from its stored schema version up to the code's `SCHEMA_VERSION`. Run `check`
  after upgrading the image; `apply` before serving if it reports pending steps.
- **Audit trail:** every mutating/resolve `/api` call is recorded with the
  caller's resolved actor (never bodies/secrets) — see `agent_vault/api/audit.py`.

## CI parity

`.github/workflows/ci.yml` gates backend (ruff, mypy, pytest with a coverage
floor), the optional `[mcp]` extra path, and frontend (typecheck, lint, test,
build, production-dependency audit). The Docker image is not built in CI; build
it locally or add a `docker build` job if you want that gated too.

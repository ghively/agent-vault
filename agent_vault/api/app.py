"""FastAPI application factory for Agent Vault API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from agent_vault.api.auth import create_auth_dependency
from agent_vault.api.config import Settings
from agent_vault.api import creds
from agent_vault.api import history
from agent_vault.api import jobs
from agent_vault.api import reads
from agent_vault.api import review
from agent_vault.api import settings as settings_api


def create_app(settings: Settings) -> FastAPI:
    """Create and configure the FastAPI application.

    Auth is scoped to ``/api`` only (attached to each /api route/include), so a
    browser can load the SPA UI openly; the UI's token gate then sends the bearer
    on its /api calls. The UI itself is served open from the built ``web/dist``.
    """
    # Auth applies to /api only (see docstring) — empty list = open (no token set).
    api_deps: list[Any] = []
    if settings.token:
        api_deps.append(Depends(create_auth_dependency(settings.token)))

    app = FastAPI(
        title="Agent Vault API",
        description="HTTP API for Agent Vault document wiki",
        version="0.1.0",
    )
    app.state.settings = settings

    @app.get("/api/health", dependencies=api_deps)
    def health() -> JSONResponse:
        """Health check."""
        return JSONResponse(content={"ok": True})

    # /api routers — original per-include registration (prefix=/api) + auth dep.
    app.include_router(reads.router, prefix="/api", dependencies=api_deps)
    app.include_router(creds.router, prefix="/api", dependencies=api_deps)
    app.include_router(review.router, prefix="/api", dependencies=api_deps)
    app.include_router(jobs.router, prefix="/api", dependencies=api_deps)
    app.include_router(history.router, prefix="/api", dependencies=api_deps)
    app.include_router(settings_api.router, prefix="/api", dependencies=api_deps)

    # Serve the built vault UI (web/dist) when present — open (no auth) so a
    # browser can load it; the UI's token gate handles /api authorization.
    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if dist.is_dir():
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/favicon.ico")
        async def favicon() -> Response:
            ico = dist / "favicon.ico"
            return FileResponse(ico) if ico.exists() else Response(status_code=204)

        @app.get("/{full_path:path}")
        async def spa(full_path: str) -> FileResponse:
            # SPA fallback: any non-/api, non-/assets path serves index.html.
            return FileResponse(dist / "index.html")

    return app

"""FastAPI application factory for Agent Vault API."""

from __future__ import annotations


from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from agent_vault.api.auth import create_auth_dependency
from agent_vault.api.config import Settings
from agent_vault.api import reads
from agent_vault.api import creds
from agent_vault.api import review
from agent_vault.api import jobs
from agent_vault.api import history


def create_app(settings: Settings) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings: Configuration settings (host, port, vault_path, token)

    Returns:
        Configured FastAPI application instance
    """
    # Build dependencies list
    dependencies = []
    if settings.token:
        dependencies.append(Depends(create_auth_dependency(settings.token)))

    app = FastAPI(
        title="Agent Vault API",
        description="HTTP API for Agent Vault document wiki",
        version="0.1.0",
        dependencies=dependencies,
    )

    # Store settings in app state for dependency injection
    app.state.settings = settings

    @app.get("/api/health")
    def health() -> JSONResponse:
        """Health check endpoint.

        Returns:
            JSON response with {"ok": true}
        """
        return JSONResponse(content={"ok": True})

    # Include read endpoints
    app.include_router(reads.router, prefix="/api")

    # Include credential resolve and recompile endpoints
    app.include_router(creds.router, prefix="/api")

    # Include review action endpoints
    app.include_router(review.router, prefix="/api")

    # Include async job runner endpoints
    app.include_router(jobs.router, prefix="/api")

    # Include history endpoints
    app.include_router(history.router, prefix="/api")

    return app

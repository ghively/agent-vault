"""Authentication middleware for Agent Vault API.

Optional bearer-token auth: if VAULT_TOKEN is set, all requests must include
Authorization: Bearer <token>. If VAULT_TOKEN is empty, auth is disabled.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


from typing import Callable


def create_auth_dependency(token: str) -> Callable[[], None]:
    """Factory to create a FastAPI Depends callable for auth.

    Args:
        token: The configured token (empty = no auth)

    Returns:
        Dependency callable for use with FastAPI Depends()
    """

    def auth_dependency(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Security(HTTPBearer(auto_error=False))] = None,
    ) -> None:
        """Validate bearer token if configured.

        Args:
            credentials: The parsed Authorization header (None if missing)

        Raises:
            HTTPException: 401 if token is required but missing/invalid
        """
        # If no token configured, allow all requests
        if not token:
            return None

        # Token required - validate it
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authorization header",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if credentials.credentials != token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return None

    return auth_dependency

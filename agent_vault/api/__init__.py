"""Agent Vault HTTP API.

FastAPI service exposing vault capabilities over HTTP with optional bearer-token auth.
"""

__all__ = ["create_app", "Settings"]

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings

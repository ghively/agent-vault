"""Tests for serve.py's fail-closed open-bind guard."""

import pytest

from agent_vault import serve
from agent_vault.api.config import Settings


def test_loopback_open_is_allowed():
    """Default loopback bind with no auth is fine — no exposure."""
    serve._guard_open_bind(Settings(host="127.0.0.1", token=""))  # no raise


def test_nonloopback_open_is_refused(monkeypatch):
    """A routable bind with auth disabled must fail closed."""
    monkeypatch.delenv("AGENT_VAULT_ALLOW_OPEN", raising=False)
    monkeypatch.delenv("VAULT_TOKENS", raising=False)
    with pytest.raises(SystemExit):
        serve._guard_open_bind(Settings(host="0.0.0.0", token=""))


def test_nonloopback_with_token_is_allowed():
    """A routable bind is fine once a token is configured."""
    serve._guard_open_bind(Settings(host="0.0.0.0", token="secret"))  # no raise


def test_nonloopback_open_with_explicit_optin_is_allowed(monkeypatch):
    """The AGENT_VAULT_ALLOW_OPEN=1 escape hatch permits a deliberate open bind."""
    monkeypatch.setenv("AGENT_VAULT_ALLOW_OPEN", "1")
    monkeypatch.delenv("VAULT_TOKENS", raising=False)
    serve._guard_open_bind(Settings(host="0.0.0.0", token=""))  # no raise

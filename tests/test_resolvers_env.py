"""Coverage for the env resolver backend and the dispatcher's success/failure
branches. tests/test_resolvers.py covers parse_ref (the security boundary) and
the unknown-scheme rejection; this fills the rest:

  - env.py: var_name mapping (join/uppercase/sanitize/prefix) and resolve
    (happy path + missing-var ResolverError);
  - resolve() dispatch happy path (import backend, call resolve, return secret);
  - _import_backend failure (module not importable -> ResolverError);
  - the non-string-secret guard and the missing-resolve()-function guard.

No network or subprocess: the env backend reads os.environ, and the dispatch
branches use an injected config plus a synthetic in-memory backend module.
"""
import sys
import types

import pytest

from agent_vault import resolvers
from agent_vault.resolvers import ResolverError, parse_ref, resolve
from agent_vault.resolvers import env as env_backend


# ---------------------------------------------------------------------------
# env.var_name — pure ref -> ENV_VAR derivation
# ---------------------------------------------------------------------------

def test_var_name_basic_mapping():
    r = parse_ref("env://app/bofa-token")
    assert env_backend.var_name(r) == "APP_BOFA_TOKEN"          # join, upper, sanitize '-'


def test_var_name_multi_segment_and_prefix():
    r = parse_ref("env://ci/deploy/key.v2")
    assert env_backend.var_name(r) == "CI_DEPLOY_KEY_V2"        # '.' -> '_'
    assert env_backend.var_name(r, {"prefix": "GV_"}) == "GV_CI_DEPLOY_KEY_V2"


# ---------------------------------------------------------------------------
# env.resolve — reads os.environ
# ---------------------------------------------------------------------------

def test_env_resolve_returns_value(monkeypatch):
    monkeypatch.setenv("APP_BOFA_TOKEN", "s3cr3t-value")
    r = parse_ref("env://app/bofa-token")
    assert env_backend.resolve(r, {}) == "s3cr3t-value"


def test_env_resolve_missing_var_raises(monkeypatch):
    monkeypatch.delenv("APP_BOFA_TOKEN", raising=False)
    r = parse_ref("env://app/bofa-token")
    with pytest.raises(ResolverError, match="not set"):
        env_backend.resolve(r, {})


# ---------------------------------------------------------------------------
# dispatch — resolve() happy path + guards
# ---------------------------------------------------------------------------

def test_dispatch_happy_path_via_env_backend(monkeypatch):
    monkeypatch.setenv("APP_TOK", "top-secret")
    cfg = {"resolvers": {"env": {"module": "agent_vault.resolvers.env"}}}
    assert resolve("env://app/tok", config=cfg) == "top-secret"


def test_dispatch_backend_not_importable():
    cfg = {"resolvers": {"env": {"module": "agent_vault.resolvers.does_not_exist"}}}
    with pytest.raises(ResolverError, match="not importable"):
        resolve("env://app/tok", config=cfg)


def _install_fake_backend(monkeypatch, name, resolve_fn):
    """Register a synthetic backend module in sys.modules for the duration of a
    test so the dispatcher can import it by name."""
    mod = types.ModuleType(name)
    if resolve_fn is not None:
        mod.resolve = resolve_fn
    monkeypatch.setitem(sys.modules, name, mod)
    return name


def test_dispatch_non_string_secret_is_rejected(monkeypatch):
    name = _install_fake_backend(monkeypatch, "fake_backend_nonstr",
                                 lambda ref, cfg: 12345)   # not a str
    cfg = {"resolvers": {"env": {"module": name}}}
    with pytest.raises(ResolverError, match="non-string secret"):
        resolve("env://app/tok", config=cfg)


def test_dispatch_backend_without_resolve_fn(monkeypatch):
    name = _install_fake_backend(monkeypatch, "fake_backend_noresolve", None)
    cfg = {"resolvers": {"env": {"module": name}}}
    with pytest.raises(ResolverError, match="no resolve"):
        resolve("env://app/tok", config=cfg)


# ---------------------------------------------------------------------------
# safe_stderr — child-stderr sanitizer used in backend error messages
# ---------------------------------------------------------------------------

def test_safe_stderr_collapses_and_truncates():
    assert resolvers.safe_stderr(b"line one\n  line two\t") == "line one line two"
    long = resolvers.safe_stderr(b"x" * 500, limit=200)
    assert len(long) == 201 and long.endswith("…")
    assert resolvers.safe_stderr(None) == ""

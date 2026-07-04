"""Coverage for locking.py (the vault-wide advisory write lock) and serve.py
(the uvicorn entry point) — both previously had zero direct tests.

locking is the coarse lock every mutating pass depends on, so its acquire /
contention / release behavior is worth pinning. serve.main() is smoke-tested by
stubbing uvicorn.run so no server actually binds.
"""
import os

import pytest

from agent_vault import locking, serve
from agent_vault.api.config import Settings

fcntl = pytest.importorskip("fcntl", reason="POSIX flock only")


def test_lock_acquire_and_release_creates_file(tmp_path):
    vault = str(tmp_path)
    with locking.vault_lock(vault):
        assert os.path.exists(os.path.join(vault, "registry", locking.LOCK_NAME))
    # after release, a fresh acquisition succeeds (no lingering hold)
    with locking.vault_lock(vault):
        pass


def test_lock_contention_times_out(tmp_path):
    vault = str(tmp_path)
    lock_path = os.path.join(vault, "registry", locking.LOCK_NAME)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    # Hold the lock on an independent fd (separate open file description ->
    # flock conflicts even within one process).
    holder = open(lock_path, "w")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(locking.LockTimeout):
            with locking.vault_lock(vault, timeout_s=1):
                pass
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
    # once released, acquisition succeeds again
    with locking.vault_lock(vault, timeout_s=1):
        pass


def test_lock_timeout_env_var_bad_value_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_LOCK_TIMEOUT_S", "not-a-number")
    lk = locking.vault_lock(str(tmp_path))
    assert lk.timeout_s == locking.DEFAULT_TIMEOUT_S


def test_serve_main_starts_uvicorn_with_settings(monkeypatch):
    captured = {}

    def fake_run(app, **kwargs):
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.setattr(serve.uvicorn, "run", fake_run)
    monkeypatch.setattr(serve.Settings, "from_env",
                        classmethod(lambda cls: Settings(host="0.0.0.0", port=1234, token="")))

    serve.main()
    assert captured["app"] is not None
    assert captured["kwargs"]["host"] == "0.0.0.0"
    assert captured["kwargs"]["port"] == 1234

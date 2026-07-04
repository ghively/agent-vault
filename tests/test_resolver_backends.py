"""Coverage for the resolver backend modules (beyond env, covered elsewhere).

Two kinds of coverage:

  1. REAL encrypt->resolve round trips for `age` and `gpg` when those binaries
     are present (they are the recommended NAS backends). This is what DOCS §2
     claims exists; it now actually does. Skipped cleanly where the binary is
     absent (e.g. `age` is not preinstalled on CI runners).

  2. For the CLI-only backends that can't run here (op/bw/pass/vault/
     secret-tool/security): the pure ref->argv mapping functions are tested
     directly, and resolve() is driven with a monkeypatched subprocess.run to
     assert argv construction, the non-zero-rc -> ResolverError path, and the
     binary-not-found guard. No real 1Password/Bitwarden/Vault account is touched.
"""
import os
import shutil
import subprocess
import types

import pytest

from agent_vault.resolvers import ResolverError, parse_ref
from agent_vault.resolvers import (
    age_file, gpg_file, op, pass_store, vault, secret_tool, macos_keychain,
    bitwarden,
)


def _fake_proc(returncode=0, stdout=b"", stderr=b""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _patch_run(monkeypatch, module, capture, *, returncode=0, stdout=b"secret\n",
               stderr=b""):
    """Patch <module>.subprocess.run to record argv/kwargs and return a fake."""
    def fake_run(args, **kwargs):
        capture["args"] = args
        capture["kwargs"] = kwargs
        return _fake_proc(returncode, stdout, stderr)
    monkeypatch.setattr(module.subprocess, "run", fake_run)


def _patch_which(monkeypatch, module, path="/usr/bin/tool"):
    monkeypatch.setattr(module.shutil, "which", lambda name: path)


# ===========================================================================
# age — real round trip
# ===========================================================================

@pytest.mark.skipif(not (shutil.which("age") and shutil.which("age-keygen")),
                    reason="age/age-keygen not installed")
def test_age_encrypt_resolve_round_trip(tmp_path):
    keyfile = tmp_path / "age.key"
    subprocess.run(["age-keygen", "-o", str(keyfile)], check=True,
                   capture_output=True)
    # public key is a `# public key: age1...` comment line in the keyfile
    pub = next(line.split(":", 1)[1].strip()
               for line in keyfile.read_text().splitlines()
               if "public key:" in line)

    store = tmp_path / "store"
    (store / "infra").mkdir(parents=True)
    enc = store / "infra" / "synology-ssh.age"
    subprocess.run(["age", "--encrypt", "--recipient", pub, "-o", str(enc)],
                   input=b"hunter2-the-ssh-key", check=True, capture_output=True)

    ref = parse_ref("age://infra/synology-ssh")
    cfg = {"keyfile": str(keyfile), "store_dir": str(store)}
    assert age_file.resolve(ref, cfg) == "hunter2-the-ssh-key"


def test_age_missing_config_and_file(tmp_path):
    ref = parse_ref("age://infra/x")
    with pytest.raises(ResolverError, match="store_dir not configured"):
        age_file.resolve(ref, {})
    with pytest.raises(ResolverError, match="keyfile not configured"):
        age_file.resolve(ref, {"store_dir": str(tmp_path)})
    with pytest.raises(ResolverError, match="keyfile not found"):
        age_file.resolve(ref, {"store_dir": str(tmp_path), "keyfile": str(tmp_path / "nope")})


# ===========================================================================
# gpg — real symmetric round trip (gpg ships on CI runners)
# ===========================================================================

@pytest.mark.skipif(not (shutil.which("gpg") or shutil.which("gpg2")),
                    reason="gpg not installed")
def test_gpg_symmetric_round_trip(tmp_path, monkeypatch):
    gnupg = tmp_path / "gnupg"
    gnupg.mkdir(mode=0o700)
    monkeypatch.setenv("GNUPGHOME", str(gnupg))
    monkeypatch.setenv("GPG_PASSPHRASE", "s3kr3t-pass")

    store = tmp_path / "store"
    (store / "infra").mkdir(parents=True)
    enc = store / "infra" / "synology-ssh.gpg"
    gpg = shutil.which("gpg") or shutil.which("gpg2")
    subprocess.run(
        [gpg, "--batch", "--yes", "--pinentry-mode", "loopback",
         "--passphrase", "s3kr3t-pass", "--symmetric", "--cipher-algo", "AES256",
         "-o", str(enc)],
        input=b"gpg-protected-secret\n", check=True, capture_output=True)

    ref = parse_ref("gpg://infra/synology-ssh")
    cfg = {"store_dir": str(store), "passphrase_env": "GPG_PASSPHRASE"}
    assert gpg_file.resolve(ref, cfg) == "gpg-protected-secret"


def test_gpg_encrypted_path_containment(tmp_path):
    ref = parse_ref("gpg://infra/x")
    p = gpg_file.encrypted_path(ref, str(tmp_path))
    assert p.endswith(os.path.join("infra", "x.gpg"))


def test_gpg_missing_passphrase_env(tmp_path, monkeypatch):
    monkeypatch.delenv("GPG_PASSPHRASE", raising=False)
    store = tmp_path / "store"
    (store / "infra").mkdir(parents=True)
    (store / "infra" / "x.gpg").write_bytes(b"ignored")
    ref = parse_ref("gpg://infra/x")
    with pytest.raises(ResolverError, match="passphrase_env"):
        gpg_file.resolve(ref, {"store_dir": str(store), "passphrase_env": "GPG_PASSPHRASE"})


# ===========================================================================
# op (1Password) — pure mapping + mocked argv
# ===========================================================================

def test_op_uri_mapping():
    assert op.op_uri(parse_ref("onepassword://Private/bofa")) == "op://Private/bofa/password"
    assert op.op_uri(parse_ref("onepassword://Private/bofa/pin")) == "op://Private/bofa/pin"
    assert op.op_uri(parse_ref("onepassword://Private/bofa"),
                     {"default_field": "credential"}) == "op://Private/bofa/credential"
    with pytest.raises(ResolverError):
        op.op_uri(parse_ref("onepassword://onlyvault"))


def test_op_resolve_argv_and_account(monkeypatch):
    cap = {}
    _patch_which(monkeypatch, op, "/usr/bin/op")
    _patch_run(monkeypatch, op, cap, stdout=b"the-password")
    out = op.resolve(parse_ref("onepassword://Private/bofa"),
                     {"account": "my.1password.com"})
    assert out == "the-password"
    assert cap["args"] == ["/usr/bin/op", "read", "--no-newline",
                           "op://Private/bofa/password", "--account", "my.1password.com"]


def test_op_resolve_error_and_missing_binary(monkeypatch):
    cap = {}
    _patch_which(monkeypatch, op, "/usr/bin/op")
    _patch_run(monkeypatch, op, cap, returncode=1, stderr=b"item not found")
    with pytest.raises(ResolverError, match="failed"):
        op.resolve(parse_ref("onepassword://Private/bofa"), {})
    monkeypatch.setattr(op.shutil, "which", lambda name: None)
    with pytest.raises(ResolverError, match="not found on PATH"):
        op.resolve(parse_ref("onepassword://Private/bofa"), {})


# ===========================================================================
# pass — pure mapping + mocked argv + first-line convention
# ===========================================================================

def test_pass_entry_name_and_first_line(monkeypatch):
    assert pass_store.entry_name(parse_ref("pass://banking/bofa/login")) == "banking/bofa/login"
    cap = {}
    _patch_which(monkeypatch, pass_store, "/usr/bin/pass")
    _patch_run(monkeypatch, pass_store, cap, stdout=b"the-secret\nmeta: extra\n")
    out = pass_store.resolve(parse_ref("pass://banking/bofa"), {})
    assert out == "the-secret"                       # first line only
    assert cap["args"] == ["/usr/bin/pass", "show", "banking/bofa"]


def test_pass_store_dir_sets_env(monkeypatch):
    cap = {}
    _patch_which(monkeypatch, pass_store, "/usr/bin/pass")
    _patch_run(monkeypatch, pass_store, cap, stdout=b"x\n")
    pass_store.resolve(parse_ref("pass://a/b"), {"store_dir": "/custom/store"})
    assert cap["kwargs"]["env"]["PASSWORD_STORE_DIR"] == "/custom/store"


# ===========================================================================
# vault — field-selection mapping + mocked argv
# ===========================================================================

def test_vault_kv_target():
    assert vault.kv_target(parse_ref("vault://secret/bofa/password")) == ("secret/bofa", "password")
    assert vault.kv_target(parse_ref("vault://secret/bofa")) == ("secret/bofa", "password")
    assert vault.kv_target(parse_ref("vault://secret/bofa/data"),
                           {"default_field": "value"}) == ("secret/bofa/data", "value")
    with pytest.raises(ResolverError):
        vault.kv_target(parse_ref("vault://onlymount"))


def test_vault_resolve_argv(monkeypatch):
    cap = {}
    _patch_which(monkeypatch, vault, "/usr/bin/vault")
    _patch_run(monkeypatch, vault, cap, stdout=b"vault-secret\n")
    out = vault.resolve(parse_ref("vault://secret/bofa/password"), {})
    assert out == "vault-secret"
    assert cap["args"] == ["/usr/bin/vault", "kv", "get", "-field=password", "secret/bofa"]


# ===========================================================================
# secret-tool / keychain — attribute arg mapping
# ===========================================================================

def test_secret_tool_lookup_args_and_too_many_segments():
    assert secret_tool.lookup_args(parse_ref("secret-tool://email/me@x.com")) == \
        ["lookup", "service", "email", "account", "me@x.com"]
    assert secret_tool.lookup_args(parse_ref("secret-tool://wifi")) == \
        ["lookup", "service", "wifi"]
    with pytest.raises(ResolverError, match="too many path segments"):
        secret_tool.lookup_args(parse_ref("secret-tool://a/b/c"))


def test_keychain_security_args():
    assert macos_keychain.security_args(parse_ref("keychain://github.com/me")) == \
        ["find-generic-password", "-s", "github.com", "-a", "me", "-w"]
    assert macos_keychain.security_args(parse_ref("keychain://wifi-home")) == \
        ["find-generic-password", "-s", "wifi-home", "-w"]
    with pytest.raises(ResolverError, match="too many path segments"):
        macos_keychain.security_args(parse_ref("keychain://a/b/c"))


# ===========================================================================
# bitwarden — plan + direct/custom field + session_env
# ===========================================================================

def test_bitwarden_plan():
    assert bitwarden.plan(parse_ref("bitwarden://bofa-login")) == ("bofa-login", "password")
    assert bitwarden.plan(parse_ref("bitwarden://bofa-login/username")) == ("bofa-login", "username")
    with pytest.raises(ResolverError, match="too many path segments"):
        bitwarden.plan(parse_ref("bitwarden://a/b/c"))


def test_bitwarden_direct_field_argv(monkeypatch):
    cap = {}
    _patch_which(monkeypatch, bitwarden, "/usr/bin/bw")
    _patch_run(monkeypatch, bitwarden, cap, stdout=b"bw-password\n")
    out = bitwarden.resolve(parse_ref("bitwarden://bofa-login"), {})
    assert out == "bw-password"
    assert cap["args"] == ["/usr/bin/bw", "get", "password", "bofa-login"]


def test_bitwarden_custom_field_reads_item_json(monkeypatch):
    _patch_which(monkeypatch, bitwarden, "/usr/bin/bw")
    item_json = b'{"fields":[{"name":"pin","value":"1234"}]}'
    monkeypatch.setattr(bitwarden.subprocess, "run",
                        lambda args, **k: _fake_proc(0, item_json))
    out = bitwarden.resolve(parse_ref("bitwarden://bofa-login/pin"), {})
    assert out == "1234"


def test_bitwarden_rejects_literal_session(monkeypatch):
    _patch_which(monkeypatch, bitwarden, "/usr/bin/bw")
    monkeypatch.setattr(bitwarden.subprocess, "run",
                        lambda args, **k: _fake_proc(0, b"x"))
    with pytest.raises(ResolverError, match="literal `session:`"):
        bitwarden.resolve(parse_ref("bitwarden://item"), {"session": "leaked"})


def test_bitwarden_session_env_injected(monkeypatch):
    cap = {}
    _patch_which(monkeypatch, bitwarden, "/usr/bin/bw")
    _patch_run(monkeypatch, bitwarden, cap, stdout=b"pw\n")
    monkeypatch.setenv("MY_BW_SESSION", "tok-123")
    bitwarden.resolve(parse_ref("bitwarden://item"), {"session_env": "MY_BW_SESSION"})
    assert cap["kwargs"]["env"]["BW_SESSION"] == "tok-123"

"""Coverage for synapse.py — the retrieval CLI's command handlers.

Before this file only main()'s help/error shell was tested (test_package.py,
test_console_script.py); every actual command handler ran uncovered. This drives
each handler directly with a synthetic index and captures stdout/stderr:

  find / list / due / expiring        — pure filters over the index list
  show / creds / resolve              — read the entity file (synapse.VAULT)
  resolve_query_to_slug               — alias / exact / unique-substring / ambiguous
  days_until, _opt_days               — helpers, including the error exits
"""
import datetime

import pytest

from agent_vault import synapse


def _today(offset):
    return (datetime.date.today() + datetime.timedelta(days=offset)).isoformat()


def _ents():
    return [
        {"slug": "bofa-checking", "type": "account", "subtype": "checking",
         "title": "BofA Checking", "tags": ["banking"],
         "_match": ["bofa", "bank of america"],
         "path": "entities/account/bofa-checking.md",
         "has_credential": True,
         "dates": {"due": _today(5)}},
        {"slug": "carrier-furnace", "type": "asset", "subtype": "hvac",
         "title": "Carrier Furnace", "tags": ["heating"], "_match": ["furnace"],
         "path": "entities/asset/carrier-furnace.md",
         "dates": {"expires": _today(40), "renews": _today(400)}},
        {"slug": "old-warranty", "type": "document", "subtype": "warranty",
         "title": "Old Warranty", "tags": [], "_match": [],
         "path": "entities/document/old-warranty.md",
         "dates": {"expires": _today(-3)}},   # already expired
    ]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def test_days_until():
    assert synapse.days_until(_today(10)) == 10
    assert synapse.days_until("not-a-date") is None


def test_opt_days_default_and_parse():
    assert synapse._opt_days([], 30) == 30
    assert synapse._opt_days(["--days", "7"], 30) == 7


def test_opt_days_bad_value_exits():
    with pytest.raises(SystemExit):
        synapse._opt_days(["--days", "abc"], 30)


def test_resolve_query_to_slug_paths():
    ents = _ents()
    aliases = {"boa": "bofa-checking"}
    assert synapse.resolve_query_to_slug("boa", ents, aliases) == "bofa-checking"        # alias
    assert synapse.resolve_query_to_slug("carrier-furnace", ents, aliases) == "carrier-furnace"  # exact slug
    assert synapse.resolve_query_to_slug("furnace", ents, aliases) == "carrier-furnace"  # unique substring
    assert synapse.resolve_query_to_slug("", ents, aliases) is None


def test_resolve_query_to_slug_ambiguous(capsys):
    # "shared" is a SUBSTRING of both _match values but an exact element of
    # neither, so it falls through to the ambiguous-substring branch.
    ents = [
        {"slug": "acct-one", "_match": ["shared-alias-a"], "title": "One"},
        {"slug": "acct-two", "_match": ["shared-alias-b"], "title": "Two"},
    ]
    assert synapse.resolve_query_to_slug("shared", ents, {}) is None
    assert "ambiguous" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# pure-filter handlers
# ---------------------------------------------------------------------------

def test_cmd_find_hit_and_miss(capsys):
    ents = _ents()
    synapse.cmd_find(["furnace"], ents, {})
    out = capsys.readouterr().out
    assert "carrier-furnace" in out and "1 match(es)" in out

    synapse.cmd_find(["nonexistent-xyz"], ents, {})
    assert "no matches" in capsys.readouterr().out


def test_cmd_find_empty_exits():
    with pytest.raises(SystemExit):
        synapse.cmd_find([], _ents(), {})


def test_cmd_list_all_and_filtered(capsys):
    ents = _ents()
    synapse.cmd_list([], ents, {})
    assert "3 entities" in capsys.readouterr().out
    synapse.cmd_list(["account"], ents, {})
    out = capsys.readouterr().out
    assert "bofa-checking" in out and "1 entity" in out


def test_cmd_due_window(capsys):
    synapse.cmd_due(["--days", "10"], _ents(), {})
    out = capsys.readouterr().out
    assert "BofA Checking" in out and "1 item" in out
    # nothing within a tiny window
    synapse.cmd_due(["--days", "1"], _ents(), {})
    assert "nothing due within 1 days" in capsys.readouterr().out


def test_cmd_expiring_covers_expires_and_marks_expired(capsys):
    synapse.cmd_expiring(["--days", "90"], _ents(), {})
    out = capsys.readouterr().out
    assert "Carrier Furnace" in out          # expires in 40d, within window
    assert "EXPIRED" in out                    # old-warranty is 3 days overdue
    assert "2 item" in out                     # the 400d renews is outside the window


# ---------------------------------------------------------------------------
# file-reading handlers (need synapse.VAULT + entity files)
# ---------------------------------------------------------------------------

def _vault_with_files(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "entities" / "account").mkdir(parents=True)
    (vault / "entities" / "account" / "bofa-checking.md").write_text(
        "---\nslug: bofa-checking\ncredential_ref: env://app/bofa-token\n---\n\nProse.\n",
        encoding="utf-8")
    monkeypatch.setattr(synapse, "VAULT", str(vault))
    return vault


def test_cmd_show_reads_file_and_missing(tmp_path, monkeypatch, capsys):
    _vault_with_files(tmp_path, monkeypatch)
    ents = _ents()
    synapse.cmd_show(["bofa"], ents, {"bofa": "bofa-checking"})
    assert "credential_ref: env://app/bofa-token" in capsys.readouterr().out
    synapse.cmd_show(["does-not-exist"], ents, {})
    assert "no entity" in capsys.readouterr().out


def test_cmd_creds_shows_reference_only(tmp_path, monkeypatch, capsys):
    _vault_with_files(tmp_path, monkeypatch)
    ents = _ents()
    synapse.cmd_creds(["bofa-checking"], ents, {})
    out = capsys.readouterr().out
    assert "env://app/bofa-token" in out
    assert "backend   : env" in out
    assert "never stored in the vault" in out
    # entity without a credential
    synapse.cmd_creds(["carrier-furnace"], ents, {})
    assert "has no credential_ref" in capsys.readouterr().out


def test_cmd_resolve_prints_secret_to_stdout(tmp_path, monkeypatch, capsys):
    _vault_with_files(tmp_path, monkeypatch)
    # stub the resolver so no external store is needed; the handler contract is
    # "secret to stdout, context to stderr, nothing persisted".
    from agent_vault import resolvers
    monkeypatch.setattr(resolvers, "resolve", lambda ref, vault: "TOP-SECRET")
    synapse.cmd_resolve(["bofa-checking"], _ents(), {})
    cap = capsys.readouterr()
    assert cap.out == "TOP-SECRET\n"           # ONLY the secret on stdout
    assert "bofa-token" in cap.err             # context on stderr


def test_cmd_resolve_error_exits_nonzero(tmp_path, monkeypatch, capsys):
    _vault_with_files(tmp_path, monkeypatch)
    from agent_vault import resolvers
    def boom(ref, vault):
        raise resolvers.ResolverError("store unreachable")
    monkeypatch.setattr(resolvers, "resolve", boom)
    with pytest.raises(SystemExit) as ei:
        synapse.cmd_resolve(["bofa-checking"], _ents(), {})
    assert ei.value.code == 1
    assert "could not resolve" in capsys.readouterr().err

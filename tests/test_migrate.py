"""Tests for O8 — schema versioning gate + migration runner."""

import re
import shutil
from pathlib import Path

import pytest

from agent_vault import migrate, validate


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "v"
    (vault / "entities").mkdir(parents=True)
    shutil.copytree("registry", vault / "registry")
    return vault


def _set_schema_version(vault: Path, line: str) -> None:
    p = vault / "registry" / "schema.yaml"
    p.write_text(re.sub(r"^version:.*$", line, p.read_text(), count=1, flags=re.M),
                 encoding="utf-8")


# --- version gate in validate.py -------------------------------------------

def test_current_schema_is_up_to_date(tmp_path):
    vault = _vault(tmp_path)
    assert migrate.current_version(str(vault)) == validate.SCHEMA_VERSION
    st = migrate.check(str(vault))
    assert st["status"] == "up_to_date"


def test_validate_rejects_newer_schema(tmp_path):
    vault = _vault(tmp_path)
    _set_schema_version(vault, "version: 999")
    errs = validate.validate_schema_version(validate.load_schema(str(vault)))
    assert errs and "newer" in errs[0]


def test_validate_rejects_missing_version(tmp_path):
    vault = _vault(tmp_path)
    _set_schema_version(vault, "")
    errs = validate.validate_schema_version(validate.load_schema(str(vault)))
    assert errs and "missing" in errs[0]


def test_validate_rejects_non_integer_version(tmp_path):
    vault = _vault(tmp_path)
    _set_schema_version(vault, "version: abc")
    errs = validate.validate_schema_version(validate.load_schema(str(vault)))
    assert errs and "integer" in errs[0]


def test_full_validate_flags_version_drift(tmp_path):
    """The schema-version gate is wired into the main validate pass."""
    import sys
    vault = _vault(tmp_path)
    (vault / "entities" / "asset").mkdir()
    _set_schema_version(vault, "version: 999")
    argv = sys.argv
    sys.argv = ["validate", str(vault)]
    try:
        rc = validate.main()
    finally:
        sys.argv = argv
    assert rc == 1  # non-zero: version drift is an error


# --- migration runner -------------------------------------------------------

def test_apply_is_noop_at_current_version(tmp_path):
    vault = _vault(tmp_path)
    result = migrate.apply(str(vault))
    assert result["applied"] == []
    assert result["final"] == validate.SCHEMA_VERSION


def test_apply_refuses_code_too_old(tmp_path):
    vault = _vault(tmp_path)
    _set_schema_version(vault, "version: 999")
    with pytest.raises(SystemExit):
        migrate.apply(str(vault))


def test_migration_runner_applies_registered_steps(tmp_path, monkeypatch):
    """Framework check: a registered migration is run and the version bumped."""
    vault = _vault(tmp_path)
    calls = []
    # Pretend the code understands version 2 with a registered 1->2 step.
    monkeypatch.setattr(validate, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(migrate, "SCHEMA_VERSION", 2)
    monkeypatch.setitem(migrate.MIGRATIONS, 1, lambda vlt: calls.append(vlt))

    result = migrate.apply(str(vault))
    assert result["applied"] == [2]
    assert calls == [str(vault)]
    assert migrate.current_version(str(vault)) == 2


def test_migration_dry_run_does_not_write(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    monkeypatch.setattr(validate, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(migrate, "SCHEMA_VERSION", 2)
    monkeypatch.setitem(migrate.MIGRATIONS, 1, lambda vlt: None)
    result = migrate.apply(str(vault), dry_run=True)
    assert result["applied"] == [2]
    assert migrate.current_version(str(vault)) == 1  # unchanged on disk

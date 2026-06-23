"""Resolver ref-parsing is a security boundary: it must reject path traversal and
CLI-flag injection before any backend touches a child process. Pure, no network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from resolvers import ResolverError, parse_ref, resolve  # noqa: E402


def test_parse_ref_valid():
    r = parse_ref("age://infra/synology-ssh")
    assert r.scheme == "age"
    assert r.store == "infra"
    assert r.path == ("synology-ssh",)


def test_parse_ref_rejects_path_traversal():
    with pytest.raises(ResolverError):
        parse_ref("age://infra/../../etc/passwd")
    with pytest.raises(ResolverError):
        parse_ref("age://infra/./x")


def test_parse_ref_rejects_cli_flag_injection():
    # A leading-dash segment would be parsed as a flag by op/bw/pass/vault/etc.
    with pytest.raises(ResolverError):
        parse_ref("op://vault/-flag")
    with pytest.raises(ResolverError):
        parse_ref("op://-store/item")


def test_parse_ref_rejects_control_chars_and_backslash():
    with pytest.raises(ResolverError):
        parse_ref("age://infra/a\\b")
    with pytest.raises(ResolverError):
        parse_ref("age://infra/a\x00b")


@pytest.mark.parametrize("bad", ["notauri", "://nopath", "age://", "age://   ", 123, None])
def test_parse_ref_rejects_malformed(bad):
    with pytest.raises(ResolverError):
        parse_ref(bad)


def test_resolve_unknown_scheme_raises():
    cfg = {"resolvers": {"env": {"module": "resolvers.env"}}}
    with pytest.raises(ResolverError):
        resolve("bogus://store/path", config=cfg)

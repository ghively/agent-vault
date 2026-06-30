#!/usr/bin/env python3
"""
resolvers â€” credential resolution (spec Â§7).

A `credential_ref` is a URI:  scheme://store/path
    e.g.  age://infra/synology-ssh
          env://app/bofa-token
          vaultwarden://Banking/bofa-login

The scheme names a *resolver*. Resolvers are pluggable backends declared in
`registry/resolvers.yaml`; each is a module exposing one function:

    resolve(ref: Ref, config: dict) -> str

This package is the dispatcher. It parses a ref, looks up the backend for the
scheme in resolvers.yaml, imports the named module, and calls it.

CONTRACT (spec Â§7, never bends):
  - The vault stores only the REFERENCE. A plaintext secret never sits in a file.
  - Resolution happens here, ON DEMAND, at query time.
  - Resolved plaintext is NEVER written back to disk or the index. This package
    returns it to the caller and forgets it; callers (e.g. `synapse resolve`)
    print it to stdout and nothing else.

Adding a backend = drop one module in this package + add one stanza to
resolvers.yaml. No schema change, no edit here.
"""
import os, sys, importlib
from collections import namedtuple

__all__ = ["Ref", "ResolverError", "parse_ref", "load_config", "resolve", "safe_stderr"]


def safe_stderr(stderr_bytes, limit=200):
    """Sanitize a child CLI's stderr for inclusion in an error message: collapse
    whitespace/newlines and truncate. Child stderr is untrusted â€” a verbose tool
    could echo item names, URIs, or partial material â€” so bound the exposure."""
    s = (stderr_bytes or b"").decode("utf-8", "replace")
    s = " ".join(s.split())
    return (s[:limit] + "â€¦") if len(s) > limit else s


class ResolverError(Exception):
    """Any failure to resolve a credential_ref. Message must never contain the
    secret itself (we raise before/without ever holding plaintext, or wrap the
    backend's own non-secret error text)."""


# scheme://store/path  ->  Ref(scheme, store, path=(seg, ...), raw)
Ref = namedtuple("Ref", ["scheme", "store", "path", "raw"])


def parse_ref(ref):
    """Parse a `scheme://store/path` credential reference into a Ref.

    Rejects anything that isn't a well-formed ref, and rejects path segments
    that could escape a backend's store (``.``/``..``/empty) so a malicious or
    buggy ref can't traverse out of `store_dir`. Backends MUST still do their
    own containment check â€” this is defense in depth, not the only line."""
    if not isinstance(ref, str) or "://" not in ref:
        raise ResolverError(
            f"malformed credential_ref {ref!r}: expected scheme://store/path")
    scheme, rest = ref.split("://", 1)
    scheme = scheme.strip().lower()
    rest = rest.strip()
    if not scheme or not rest:
        raise ResolverError(
            f"malformed credential_ref {ref!r}: empty scheme or path")
    if "\x00" in rest or "\\" in rest or any(ord(c) < 0x20 for c in rest):
        raise ResolverError(f"illegal character in credential_ref {ref!r}")
    segments = [s for s in rest.split("/") if s != ""]
    if not segments:
        raise ResolverError(f"credential_ref {ref!r} has no store/path")
    for seg in segments:
        if seg in (".", ".."):
            raise ResolverError(
                f"credential_ref {ref!r} contains a path-traversal segment")
        # Reject leading-dash segments: backends pass these as positional argv to
        # CLIs (op/bw/pass/vault/secret-tool/security), where a leading '-' is
        # parsed as a FLAG â€” argument injection. (Backends also add `--` where
        # supported; this is the central guard.)
        if seg.startswith("-"):
            raise ResolverError(
                f"credential_ref {ref!r} has a segment starting with '-' "
                f"(would be parsed as a CLI flag); not allowed")
    return Ref(scheme=scheme, store=segments[0], path=tuple(segments[1:]), raw=ref)


def load_config(vault="."):
    """Load registry/resolvers.yaml. Returns the parsed mapping (with keys
    `resolvers`, `default_resolver`, ...). Raises ResolverError if absent."""
    try:
        import yaml
    except ImportError:  # pragma: no cover - pyyaml is a hard dep elsewhere
        raise ResolverError("pyyaml required to read resolvers.yaml")
    path = os.path.join(vault, "registry", "resolvers.yaml")
    if not os.path.exists(path):
        raise ResolverError(f"no resolver registry at {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _import_backend(module_name, scheme, vault):
    """Import the backend module named in resolvers.yaml. Ensures the vault dir
    is importable so the configured `resolvers.<name>` package path resolves
    regardless of cwd (synapse may run with AGENT_VAULT_PATH pointing elsewhere)."""
    if not module_name:
        module_name = f"resolvers.{scheme}"
    vpath = os.path.abspath(vault)
    if vpath not in sys.path:
        sys.path.insert(0, vpath)
    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        raise ResolverError(
            f"resolver backend {module_name!r} for scheme {scheme!r} is not "
            f"importable: {e}")


def resolve(ref, vault=".", config=None):
    """Resolve a credential_ref string to its plaintext secret.

    `config` (the parsed resolvers.yaml mapping) may be injected for testing;
    otherwise it's read from the vault. Returns the secret string. Raises
    ResolverError on any failure. The returned value is the ONLY place the
    plaintext exists â€” do not persist it.
    """
    parsed = parse_ref(ref)
    cfg = config if config is not None else load_config(vault)
    backends = cfg.get("resolvers") or {}
    backend = backends.get(parsed.scheme)
    if not backend:
        known = ", ".join(sorted(backends)) or "(none configured)"
        raise ResolverError(
            f"no resolver configured for scheme {parsed.scheme!r} "
            f"(known: {known}); add a stanza to registry/resolvers.yaml")
    module = _import_backend(backend.get("module"), parsed.scheme, vault)
    fn = getattr(module, "resolve", None)
    if not callable(fn):
        raise ResolverError(
            f"resolver module for scheme {parsed.scheme!r} has no resolve() function")
    secret = fn(parsed, backend)
    if not isinstance(secret, str):
        raise ResolverError(
            f"resolver for scheme {parsed.scheme!r} returned a non-string secret")
    return secret

#!/usr/bin/env python3
"""
resolvers — credential resolution (spec §7).

A `credential_ref` is a URI:  scheme://store/path
    e.g.  age://infra/synology-ssh
          env://app/bofa-token
          vaultwarden://Banking/bofa-login

The scheme names a *resolver*. Resolvers are pluggable backends declared in
`registry/resolvers.yaml`; each is a module exposing one function:

    resolve(ref: Ref, config: dict) -> str

This package is the dispatcher. It parses a ref, looks up the backend for the
scheme in resolvers.yaml, imports the named module, and calls it.

CONTRACT (spec §7, never bends):
  - The vault stores only the REFERENCE. A plaintext secret never sits in a file.
  - Resolution happens here, ON DEMAND, at query time.
  - Resolved plaintext is NEVER written back to disk or the index. This package
    returns it to the caller and forgets it; callers (e.g. `synapse resolve`)
    print it to stdout and nothing else.

Adding a backend = drop one module in this package + add one stanza to
resolvers.yaml. No schema change, no edit here.
"""

import os
import sys
import importlib
import importlib.util
from collections import namedtuple

__all__ = ["Ref", "ResolverError", "parse_ref", "load_config", "resolve", "safe_stderr"]


def safe_stderr(stderr_bytes, limit=200):
    """Sanitize a child CLI's stderr for inclusion in an error message: collapse
    whitespace/newlines and truncate. Child stderr is untrusted — a verbose tool
    could echo item names, URIs, or partial material — so bound the exposure."""
    s = (stderr_bytes or b"").decode("utf-8", "replace")
    s = " ".join(s.split())
    return (s[:limit] + "…") if len(s) > limit else s


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
    own containment check — this is defense in depth, not the only line."""
    if not isinstance(ref, str) or "://" not in ref:
        raise ResolverError(f"malformed credential_ref {ref!r}: expected scheme://store/path")
    scheme, rest = ref.split("://", 1)
    scheme = scheme.strip().lower()
    rest = rest.strip()
    if not scheme or not rest:
        raise ResolverError(f"malformed credential_ref {ref!r}: empty scheme or path")
    if "\x00" in rest or "\\" in rest or any(ord(c) < 0x20 for c in rest):
        raise ResolverError(f"illegal character in credential_ref {ref!r}")
    segments = [s for s in rest.split("/") if s != ""]
    if not segments:
        raise ResolverError(f"credential_ref {ref!r} has no store/path")
    for seg in segments:
        if seg in (".", ".."):
            raise ResolverError(f"credential_ref {ref!r} contains a path-traversal segment")
        # Reject leading-dash segments: backends pass these as positional argv to
        # CLIs (op/bw/pass/vault/secret-tool/security), where a leading '-' is
        # parsed as a FLAG — argument injection. (Backends also add `--` where
        # supported; this is the central guard.)
        if seg.startswith("-"):
            raise ResolverError(
                f"credential_ref {ref!r} has a segment starting with '-' "
                f"(would be parsed as a CLI flag); not allowed"
            )
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
    """Import the backend module named in resolvers.yaml.

    Supports the documented extension pattern (drop ``resolvers/<name>.py`` into
    a vault and reference it as ``resolvers.<name>``) WITHOUT exposing the whole
    vault directory to the global import system. Previously this prepended the
    vault dir to ``sys.path[0]`` unconditionally, which let any ``.py`` at the
    vault root shadow stdlib/installed packages — a write-scope → code-execution
    pivot that defeated the per-agent scope model.

    The fix: only load a vault-local module whose dotted name starts with
    ``resolvers.``, and do so by direct file location so no untrusted directory
    ever enters ``sys.path``. Module names that don't look like a vault-local
    resolver (e.g. the installed ``agent_vault.resolvers.env``) import normally
    from the package install path, exactly as before.
    """
    if not module_name:
        module_name = f"resolvers.{scheme}"
    # Vault-local resolver extension: resolvers.<name> resolved against <vault>/resolvers/<name>.py
    if module_name.startswith("resolvers.") and len(module_name) > len("resolvers."):
        leaf = module_name[len("resolvers.") :]
        # Conservative leaf check — module names are identifiers, nothing path-shaped.
        if leaf.isidentifier() and not leaf.startswith("__"):
            # Cache: a vault-local backend is loaded once, not re-read from disk
            # on every credential resolve (resolve() runs per /api/creds request).
            cached = sys.modules.get(module_name)
            if cached is not None:
                return cached
            candidate = os.path.join(os.path.abspath(vault), "resolvers", leaf + ".py")
            if os.path.isfile(candidate):
                spec = importlib.util.spec_from_file_location(module_name, candidate)
                if spec is not None and spec.loader is not None:
                    mod = importlib.util.module_from_spec(spec)
                    # Register under the requested dotted name so a subsequent
                    # call hits the cache above instead of re-reading the file,
                    # and so the module's __name__ matches resolvers.yaml.
                    sys.modules[module_name] = mod
                    try:
                        spec.loader.exec_module(mod)
                    except Exception as e:
                        # A syntax error / module-level crash in a vault-local
                        # resolver must surface as a clean ResolverError, not a
                        # raw traceback, so callers (CLI resolve, /api/creds)
                        # report "could not resolve..." instead of crashing.
                        sys.modules.pop(module_name, None)
                        raise ResolverError(
                            f"resolver backend {module_name!r} for scheme "
                            f"{scheme!r} failed to load: {e}"
                        ) from e
                    return mod
                # File exists but spec failed to build — fall through to the
                # normal import, which will raise a clear ImportError.
    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        raise ResolverError(
            f"resolver backend {module_name!r} for scheme {scheme!r} is not importable: {e}"
        )


def resolve(ref, vault=".", config=None):
    """Resolve a credential_ref string to its plaintext secret.

    `config` (the parsed resolvers.yaml mapping) may be injected for testing;
    otherwise it's read from the vault. Returns the secret string. Raises
    ResolverError on any failure. The returned value is the ONLY place the
    plaintext exists — do not persist it.
    """
    parsed = parse_ref(ref)
    cfg = config if config is not None else load_config(vault)
    backends = cfg.get("resolvers") or {}
    backend = backends.get(parsed.scheme)
    if not backend:
        known = ", ".join(sorted(backends)) or "(none configured)"
        raise ResolverError(
            f"no resolver configured for scheme {parsed.scheme!r} "
            f"(known: {known}); add a stanza to registry/resolvers.yaml"
        )
    module = _import_backend(backend.get("module"), parsed.scheme, vault)
    fn = getattr(module, "resolve", None)
    if not callable(fn):
        raise ResolverError(
            f"resolver module for scheme {parsed.scheme!r} has no resolve() function"
        )
    secret = fn(parsed, backend)
    if not isinstance(secret, str):
        raise ResolverError(f"resolver for scheme {parsed.scheme!r} returned a non-string secret")
    return secret

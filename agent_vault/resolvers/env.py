#!/usr/bin/env python3
"""
resolvers.env — resolve a ref from an environment variable.

For non-secret-ish refs and local development (per spec §7). Do NOT use this for
real secrets in production — environment variables leak into process listings,
crash dumps, and child processes. It exists so a vault can be exercised without
standing up a real secret store.

Config (registry/resolvers.yaml):

    env:
      module: resolvers.env
      # optional: prefix: GREGORY_   (prepended to every derived var name)

Ref → variable mapping:

    env://app/bofa-token   ->   APP_BOFA_TOKEN
    env://ci/deploy-key    ->   CI_DEPLOY_KEY

i.e. join store + path segments with '_', uppercase, and replace any character
that isn't a letter or digit with '_'. With `prefix: GREGORY_`, the first
example resolves GREGORY_APP_BOFA_TOKEN instead.
"""
import os
import re
from . import ResolverError


def var_name(ref, config=None):
    """Derive the environment variable name for a ref (pure, testable)."""
    prefix = (config or {}).get("prefix") or ""
    raw = "_".join([ref.store, *ref.path])
    name = re.sub(r"[^A-Za-z0-9]", "_", raw).upper()
    return f"{prefix}{name}"


def resolve(ref, config):
    name = var_name(ref, config)
    if name not in os.environ:
        raise ResolverError(
            f"env resolver: environment variable {name!r} is not set "
            f"(derived from {ref.raw!r})")
    return os.environ[name]

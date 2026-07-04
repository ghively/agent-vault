#!/usr/bin/env python3
"""embeddings.py — pluggable text embedders for semantic retrieval (O4.2).

Mirrors the compiler's client/mock split so semantic search is fully testable
offline and never forces a network dependency:

  - `OllamaEmbedder` (default) POSTs to a local Ollama `/api/embeddings`
    endpoint (`OLLAMA_EMBED_MODEL`, default `nomic-embed-text`), with the same
    bounded-retry / clear-error posture as the compile client.
  - `MockEmbedder` is deterministic and offline: a hashed bag-of-words vector
    that captures lexical overlap, so tests exercise the real ranking/plumbing
    without a model. Used by CI.

Selected by `AGENT_VAULT_EMBEDDER=ollama|mock` (default `ollama`). All vectors
are L2-normalized on return, so cosine similarity is a plain dot product.
No vault state is written here — embedding is a read-side accelerator, and the
core invariant (the compiler is the only LLM *writer*) is untouched.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class Embedder:
    """Interface: embed a batch of texts into L2-normalized vectors."""

    name = "abstract"
    dim = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class MockEmbedder(Embedder):
    """Deterministic, offline, hashed bag-of-words embedder.

    Not semantically smart, but stable and lexically meaningful: texts sharing
    tokens land near each other, which is enough to test ranking, storage, and
    the hybrid merge without a live model.
    """

    name = "mock-embed"
    dim = 256

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in _TOKEN_RE.findall((text or "").lower()):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            v[h % self.dim] += 1.0
        return _l2_normalize(v)


class OllamaEmbedder(Embedder):
    """Embeddings via a local Ollama `/api/embeddings` endpoint."""

    name = "ollama-embed"
    MAX_RESPONSE_BYTES = 8 * 1024 * 1024

    def __init__(self, host: str | None = None, model: str | None = None,
                 timeout: int = 120):
        self.host = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.model = model or os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
        self.timeout = timeout
        self.name = f"ollama-embed:{self.model}"
        self.dim = 0  # discovered from the first response
        self.retries = max(0, _env_int("OLLAMA_RETRIES", 2))
        self.retry_backoff_s = _env_float("OLLAMA_RETRY_BACKOFF_S", 1.0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            vec = self._embed_one(t)
            if self.dim == 0 and vec:
                self.dim = len(vec)
            out.append(_l2_normalize(vec))
        return out

    def _embed_one(self, text: str) -> list[float]:
        url = self.host.rstrip("/") + "/api/embeddings"
        body = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"})
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read(self.MAX_RESPONSE_BYTES).decode("utf-8")
                payload = json.loads(raw)
                if isinstance(payload, dict) and payload.get("error"):
                    raise RuntimeError(f"Ollama embed error: {payload['error']}")
                vec = payload.get("embedding") if isinstance(payload, dict) else None
                if not isinstance(vec, list) or not vec:
                    raise RuntimeError("Ollama embeddings response had no 'embedding'")
                return [float(x) for x in vec]
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"Ollama embeddings HTTP {e.code} at {self.host}: "
                                   f"{e.reason}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last_exc = e
                if attempt < self.retries:
                    time.sleep(self.retry_backoff_s * (attempt + 1))
                    continue
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Ollama embeddings returned non-JSON: {e}") from e
        raise RuntimeError(
            f"Ollama at {self.host} unreachable after {self.retries + 1} "
            f"attempt(s): {last_exc}") from last_exc


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def get_embedder(model_override: str | None = None) -> Embedder:
    """Return the embedder selected by AGENT_VAULT_EMBEDDER (default ollama)."""
    choice = os.environ.get("AGENT_VAULT_EMBEDDER", "ollama").lower()
    if choice == "mock":
        return MockEmbedder()
    if choice == "ollama":
        return OllamaEmbedder(model=model_override)
    raise ValueError(f"unknown AGENT_VAULT_EMBEDDER={choice!r} (expected: ollama, mock)")


def cosine(a: list[float], b: list[float]) -> float:
    """Dot product of two vectors (equals cosine when both are L2-normalized)."""
    n = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(n))

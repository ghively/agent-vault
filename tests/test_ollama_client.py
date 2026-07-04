"""Coverage for compiler.OllamaClient — the real-AI HTTP path.

This exercises the client without a running Ollama server by monkeypatching
the one transport call it makes (`urllib.request.urlopen`). Before this file
the only compile client with tests was MockClient; DOCS.md §8 called out the
OllamaClient as untested. Covered here:

  - happy path: a well-formed /api/generate response is parsed into
    {prose, proposals, model, raw};
  - request construction: URL, method payload (model/system/prompt/stream),
    and host-trailing-slash normalisation;
  - the three failure modes the client converts to RuntimeError —
    unreachable host (URLError), timeout, and a non-JSON body;
  - an empty `response` field yields empty prose (not a crash);
  - get_client() dispatch across mock / ollama / unknown.

The transport is the only thing stubbed — parse_response, prompt handling,
and error wrapping all run for real.
"""
import json
import urllib.error

import pytest

from agent_vault import compiler


class _FakeResponse:
    """Minimal stand-in for the object urllib.request.urlopen returns as a
    context manager: supports `with ... as resp:` and `resp.read()`."""

    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def _patch_urlopen(monkeypatch, *, body=None, exc=None, capture=None):
    """Replace urllib.request.urlopen. If `exc` is set, raise it; otherwise
    return a _FakeResponse wrapping `body`. If `capture` (a dict) is given,
    record the outgoing Request and timeout into it for assertions."""

    def fake_urlopen(req, timeout=None):
        if capture is not None:
            capture["url"] = req.full_url
            capture["timeout"] = timeout
            capture["headers"] = dict(req.header_items())
            capture["body"] = json.loads(req.data.decode("utf-8"))
        if exc is not None:
            raise exc
        return _FakeResponse(body)

    monkeypatch.setattr(compiler.urllib.request, "urlopen", fake_urlopen)


# ---------------------------------------------------------------------------
# happy path + request construction
# ---------------------------------------------------------------------------

def test_ollama_happy_path_parses_prose_and_proposals(monkeypatch):
    raw = ('<prose>The furnace was installed in 2021.</prose>\n'
           '<proposals>[{"kind": "tag", "name": "hvac"}]</proposals>')
    _patch_urlopen(monkeypatch, body=json.dumps({"response": raw}))

    client = compiler.OllamaClient(host="http://nas:11434", model="test-model")
    out = client.compile("SYS", "USER")

    assert out["prose"] == "The furnace was installed in 2021."
    assert out["proposals"] == [{"kind": "tag", "name": "hvac"}]
    assert out["model"] == "ollama:test-model"
    assert out["raw"] == raw


def test_ollama_request_is_well_formed(monkeypatch):
    cap: dict = {}
    _patch_urlopen(monkeypatch, body=json.dumps({"response": "<prose>ok</prose>"}),
                   capture=cap)

    # trailing slash on host must be normalised away (host.rstrip("/"))
    client = compiler.OllamaClient(host="http://nas:11434/", model="m",
                                   timeout=42)
    client.compile("SYSTEM-PROMPT", "USER-PROMPT")

    assert cap["url"] == "http://nas:11434/api/generate"
    assert cap["timeout"] == 42
    assert cap["headers"].get("Content-type") == "application/json"
    body = cap["body"]
    assert body["model"] == "m"
    assert body["system"] == "SYSTEM-PROMPT"
    assert body["prompt"] == "USER-PROMPT"
    assert body["stream"] is False


def test_ollama_empty_response_yields_empty_prose(monkeypatch):
    # A model that returns an empty (or missing) `response` field must not
    # crash — parse_response("") gives empty prose and no proposals.
    _patch_urlopen(monkeypatch, body=json.dumps({"response": ""}))
    out = compiler.OllamaClient().compile("s", "u")
    assert out["prose"] == ""
    assert out["proposals"] == []

    _patch_urlopen(monkeypatch, body=json.dumps({"other": "x"}))  # no "response"
    out = compiler.OllamaClient().compile("s", "u")
    assert out["prose"] == ""


# ---------------------------------------------------------------------------
# failure modes -> RuntimeError (compile_all catches these per-entity)
# ---------------------------------------------------------------------------

def test_ollama_unreachable_host_raises_runtimeerror(monkeypatch):
    _patch_urlopen(monkeypatch, exc=urllib.error.URLError("connection refused"))
    client = compiler.OllamaClient(host="http://localhost:11434")
    with pytest.raises(RuntimeError, match="unreachable"):
        client.compile("s", "u")


def test_ollama_timeout_raises_runtimeerror(monkeypatch):
    # TimeoutError is in the client's except tuple alongside URLError.
    _patch_urlopen(monkeypatch, exc=TimeoutError("timed out"))
    with pytest.raises(RuntimeError, match="unreachable"):
        compiler.OllamaClient().compile("s", "u")


def test_ollama_non_json_body_raises_runtimeerror(monkeypatch):
    _patch_urlopen(monkeypatch, body="<html>502 Bad Gateway</html>")
    with pytest.raises(RuntimeError, match="non-JSON"):
        compiler.OllamaClient().compile("s", "u")


# ---------------------------------------------------------------------------
# get_client dispatch
# ---------------------------------------------------------------------------

def test_get_client_dispatch(monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_COMPILER", "mock")
    assert isinstance(compiler.get_client(), compiler.MockClient)

    monkeypatch.setenv("AGENT_VAULT_COMPILER", "ollama")
    c = compiler.get_client(model_override="llama3")
    assert isinstance(c, compiler.OllamaClient)
    assert c.model == "llama3"                 # --model override wins

    monkeypatch.setenv("AGENT_VAULT_COMPILER", "bogus")
    with pytest.raises(ValueError, match="unknown AGENT_VAULT_COMPILER"):
        compiler.get_client()


def test_ollama_defaults_from_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://box:9999")
    monkeypatch.setenv("OLLAMA_MODEL", "custom:latest")
    c = compiler.OllamaClient()
    assert c.host == "http://box:9999"
    assert c.model == "custom:latest"
    assert c.name == "ollama:custom:latest"

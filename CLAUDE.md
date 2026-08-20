# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Agent Vault — a **standalone, file-based household knowledge wiki**. Three surfaces over
one data store (`registry/`, `raw/`, `entities/`):

- **CLI**: `agent-vault` (retrieval + pipeline ops)
- **HTTP service**: `agent-vault-serve` (FastAPI on `:7778`)
- **React UI**: `web/` (Vite; served from `web/dist` by the same FastAPI app in prod)

**Authoritative structure reference:** [`AGENTS.md`](AGENTS.md) — read it. It is the
source of truth for the core invariant, the write-ownership map, the pipeline, lint checks,
and the region-ownership rules. [`DOCS.md`](DOCS.md) is the plain-language overview;
[`llm-wiki-schema-spec.md`](llm-wiki-schema-spec.md) is the schema "constitution".

> Note: AGENTS.md describes a `.claude/` *automation suite* (vault-keeper/vault-doctor
> agents, `/vault-*` skills, `vault_guard`/`vault_gate` hooks). That suite is **external to
> this repo** — it consumes this app. Do not expect those files here.

## Commands

### Backend (Python ≥3.11, repo root)

```bash
pip install -e .[dev]          # editable install → agent-vault + agent-vault-serve CLIs
ruff check .                   # lint (line-length 100)
mypy agent_vault               # type check (strict for new code — see Conventions)
pytest -v                      # full suite  (testpaths=["tests"])
pytest tests/test_validate.py -v                       # one file
pytest tests/api/test_creds.py -k "test_name" -v       # one test / by keyword
AGENT_VAULT_COMPILER=mock pytest -v                     # deterministic offline compiler (CI path)
```

Tests build a tempdir sandbox per test (see `tests/test_validate.py::_sandbox`); they never
touch the shipped `entities/`. The CI workflow (`.github/workflows/ci.yml`) runs
`ruff check .` → `mypy agent_vault` → `pytest -v` for the backend job.

### Run the service / CLI

```bash
agent-vault-serve              # or: python -m agent_vault.serve
# env: AGENT_VAULT_HOST (127.0.0.1) · AGENT_VAULT_PORT (7778)
#      AGENT_VAULT_PATH (.)      · VAULT_TOKEN ("" = no auth)

agent-vault <command>          # find|show|due|expiring|creds|resolve|list|compact
# CLI reads the vault root from AGENT_VAULT_PATH (default: cwd).
```

### Frontend (`web/`)

```bash
cd web && npm install
npm run dev                    # Vite on :5173, proxies /api → http://localhost:7778
npm run build                  # tsc -b && vite build → web/dist (served by FastAPI)
npm test                       # vitest run
npm run test:watch
npx vitest run src/screens/Creds.test.tsx   # single file
# env: VITE_VAULT_API_URL · VITE_VAULT_API_PORT (default 7778)
```

### Pipeline + cadences (vault dir is the first arg)

`ingest.py`, `compiler.py`, `promote.py`, `review.py`, `build_index.py`, `validate.py`,
`lint.py` each take the **vault directory as `argv[1]`** (cadences `cd` in and pass `.`):

```bash
python -m agent_vault.validate .            # exit 0 = valid schema (the gate)
python -m agent_vault.lint .                # exit 1 if any findings
python cadences/run_cadence.py daily .      # | weekly | monthly  (cross-platform)
```

`review.py` verbs: `list | show <id> | approve <id> | reject <id> |
approve-entity <type/slug> | reject-entity <type/slug>` (each takes `--reason`).

## Architecture

### The core invariant (do not break)

**The LLM writes prose only — and only in one place: `compiler.py`.** Everything else
(facts, classification, vocabulary, retrieval, registry mutations) is **deterministic
Python**. The LLM proposes; deterministic code commits. Every change here must preserve
this — never add a second LLM write path, and never let the LLM edit frontmatter, LINKS
blocks, or registry files.

### Two tiers of code

1. **Legacy pipeline scripts** (`ingest.py`, `compiler.py`, `promote.py`, `review.py`,
   `synapse.py`, `validate.py`, `lint.py`, `build_index.py`, `compact.py`, …) — compact,
   one-liner style; mypy-*eased* and ruff allows `E701`/`E702` (see `pyproject.toml`).
2. **HTTP API** under `agent_vault/api/` — **mypy-strict, fully typed**. This is the newer
   surface; new code defaults to strict.

`agent_vault/api/app.py::create_app` wires all routers under `/api` with optional bearer
auth, and (when `web/dist` exists) serves the built SPA with a catch-all fallback. Auth is
scoped to `/api` only so the browser can load the UI openly; the UI's token gate adds the
bearer on its `/api` calls. **The integration boundary is HTTP + JSON** — no filesystem or
subprocess coupling to consumers.

Heavy operations (`jobs.py`) run as **subprocesses** via `sys.executable -m <module>`, with
`AGENT_VAULT_PATH` pinned in the child env so callers can't redirect it.

### Data flow

```
raw/ (append-only) → ingest.py  (deterministic classify + field extract → entity stubs)
                   → compiler.py (THE LLM TOUCHPOINT: stub → prose; AGENT_VAULT_COMPILER=ollama|mock)
                   → promote.py  (proposals → registry; deterministic commit)
                   → review.py   (human approve/reject gate)
                     build_index.py → _index.json / _index.md
```

### Entity files have three regions, each with one author

- **Frontmatter** → Python (`ingest`/`promote`); the LLM may set *only* `status` and
  `compiled_from_hash`.
- **`LINKS:BEGIN/END` block** → Python, regenerated from `related:` each run; LLM never edits.
- **Prose body** → the LLM (`compiler.py`); never invents facts — uses `[NEEDS SOURCE: …]`.

### Registry write-ownership (enforced, not just convention)

`registry/schema.yaml`, `aliases.yaml`, and `field_mappings.yaml` are written by
`promote.py`/`review.py` **only** — never hand-edit (grow via `/vault-add-entity-type` or the
promote/review path). `patterns.yaml`, `resolvers.yaml`, and `_entity-template.md` are
human-writable. `raw/**` and `discovery/*.jsonl` are **append-only** (idempotent via hashes
+ manifest). `_index.*` is regenerated by `build_index.py` only.

## Conventions & gotchas

- **`validate.py` must stay green** — exit 0 is the gate; any in-app change must keep it
  clean. `lint.py` findings are notifications, not blockers, but each `kind` has a known
  deterministic repair (table in AGENTS.md).
- **Compiler contract versioning**: `compiler.py` stamps `PROMPT_CONTRACT_VERSION` (currently
  2.0). Changing the prompt or proposal shape requires a version bump.
- **Secrets are referenced, never stored** — `credential_ref:` URIs only; `validate.py` +
  `secret_scan.py` reject plaintext-shaped secrets; resolution is on-demand and never
  persisted. Resolver backends live in `resolvers/` (config `registry/resolvers.yaml`).
- **The `.venv` already has the package + console scripts installed**; reuse it rather than
  re-installing unless deps changed.
- Frontend is a window-manager-style desktop shell: `web/src/wm/` (the shell) hosts "apps"
  in `web/src/screens/`. Tests are colocated as `*.test.tsx` and run under jsdom via vitest.

# AGENTS.md - `agent-vault` (Agent Vault: the knowledge-wiki app)

> The authoritative **structure + invariant reference** for Agent Vault. Deep
> docs: [`README.md`](README.md), [`DOCS.md`](DOCS.md) (CLI/pipeline),
> [`docs/API.md`](docs/API.md) (FastAPI HTTP service), [`web/README.md`](web/README.md)
> (React desktop UI), [`llm-wiki-schema-spec.md`](llm-wiki-schema-spec.md) (the "constitution").
>
> There is currently **no `.claude/` automation suite** (no custom agents,
> skills, or hooks) in this repo — an earlier version of this file described
> one in detail, but it was never actually committed (see Gotchas). Don't
> assume `/vault-*` skills or guardian hooks exist; the rules below are
> convention, enforced by code review and tests, not by tooling.

A **standalone**, file-based household knowledge wiki. It runs as:
- **CLI**: `agent-vault` console script (retrieval: `find`/`show`/`due`/`expiring`/
  `creds`/`resolve`/`list`/`compact`) + `python -m agent_vault.<module>` for
  every pipeline stage (`ingest`, `compiler`, `promote`, `build_index`,
  `validate`, `lint`, `review`, `reclassify_apply`, `collections_importer`) —
  those have no console-script entry point.
- **Service**: `agent-vault-serve` console script — FastAPI HTTP service with
  28 routes (entities incl. `GET /api/search`, credentials, review, jobs, run
  history, status/ask, config, settings), not just credential resolution. Full
  reference: [`docs/API.md`](docs/API.md).
- **UI**: React web interface in `web/` — a window-manager desktop with 8
  apps. Full reference: [`web/README.md`](web/README.md).
- **MCP**: `agent-vault-mcp` console script (optional `[mcp]` extra) — a Model
  Context Protocol server exposing the vault to agents as tools (`vault_search`,
  `vault_ask`, `vault_get`, `vault_list`, `vault_status`, `vault_submit_source`,
  `vault_resolve_credential`). Thin FastMCP wiring (`mcp_server.py`) over pure,
  tested tool logic (`mcp_tools.py`); reads are lock-free, the only write
  (`submit_source`) is append-only into `raw/` with actor attribution, and
  secret resolution is opt-in (`AGENT_VAULT_MCP_ALLOW_RESOLVE=1`). Retrieval/
  answer stack: `search.py` (FTS5) + `semantic.py` (embeddings) + `rag.py`
  (cited answers); embedders/answerers are pluggable (Ollama or offline mock).

Formerly embedded in SynapseNAS; now a standalone product consumed over HTTP.

## The core invariant (never break this)

The **LLM writes prose only** (one touchpoint: `compiler.py`). Everything else - facts,
classification, vocabulary, retrieval - is **deterministic Python**. **The LLM proposes;
deterministic code commits.** This eliminates hallucination risk and keeps the vault
auditable. Every automation that touches this app exists to uphold the invariant, not
erode it.

## Region ownership (per entity `.md` file)

| Region | Author | Rule |
|--------|--------|------|
| Frontmatter | Python (`ingest`/`promote`) | the LLM may set ONLY `status` and `compiled_from_hash` |
| `LINKS:BEGIN/END` block | Python (regenerated each run from `related:`) | the LLM never edits it |
| Prose body | LLM (`compiler.py`) | never invents facts - uses `[NEEDS SOURCE: ...]` |

## Write-ownership map (who may write each path)

This is **convention, not code-enforced** — there is no guardian hook in this
repo today. Nothing stops a human or agent from hand-editing a "BLOCKED" path;
respecting this table is a matter of not breaking the propose→promote
invariant, not a technical guarantee.

| Path | Writer | Hand-edit by an agent? |
|------|--------|------------------------|
| `registry/schema.yaml` | `promote.py` / `review.py` ONLY | **BLOCKED** - grow via a `new_type` compiler proposal + `review.py approve <id>` (always human-gated; see README's "Follow-up C" for the flow) |
| `registry/aliases.yaml` | `promote.py` ONLY | **BLOCKED** - grow via promote/review |
| `registry/patterns.yaml` | human + promotion-append | allowed (classifier vocabulary — billers + shapes) |
| `registry/field_mappings.yaml` | `promote.py` ONLY | **BLOCKED** - learned field mappings; grow via promote + human review approval path |
| `registry/resolvers.yaml` | human only | allowed (credential backend config) |
| `registry/_entity-template.md` | human | allowed (structural template) |
| `raw/**` | append-only (sources) | adding new files OK; **never modify/delete** existing - WARNED |
| `discovery/*.jsonl` | append-only (scripts) | **never hand-edit** (corrupts audit/proposals) - WARNED |
| `entities/**/*.md` | ingest (frontmatter/LINKS) + compiler (prose) | edit only via the pipeline / recipes, honoring region ownership |
| `_index.json` / `_index.md` | `build_index.py` only | regenerated; never hand-edit |

## Pipeline + exact CLI signatures

Every stage below lives under `agent_vault/` and is run as `python -m
agent_vault.<module> <vault-dir>` — **none of these modules have a console-
script entry point** except `synapse` (→ `agent-vault`) and `serve` (→
`agent-vault-serve`); `pyproject.toml`'s `[project.scripts]` declares exactly
those two.

```
raw/ (append-only)
  -> python -m agent_vault.ingest .       classify deterministically -> entity stubs (facts, no prose)
                                            + extract fields via registry/field_mappings.yaml (additive)
  -> python -m agent_vault.compiler .     THE LLM TOUCHPOINT: stub -> prose   (AGENT_VAULT_COMPILER=ollama|mock)
                                            PROMPT_CONTRACT_VERSION 2.0 (teaches new_biller/new_shape/new_field_mapping)
  -> python -m agent_vault.promote .      learn vocabulary: proposals -> registry (deterministic commit)
                                            also graduates billers/shapes into patterns.yaml + field mappings into
                                            field_mappings.yaml (new_field_mapping: always human-gated + regex gate)
  -> python -m agent_vault.review . ...   human approve/reject (gated types/proposals/entities)
                                            re-runs the field_mapping deterministic validation gate on approval
  python -m agent_vault.build_index .     (re)build _index.json
  agent-vault <cmd>                       query CLI: find|show|expiring|due|creds|resolve|list|compact
  python -m agent_vault.validate <vault>  schema gate (exit 0 = valid) — also validates patterns.yaml + field_mappings.yaml
  python -m agent_vault.lint <vault>      operational audit (exit 1 if any findings)
```

All modules take the vault dir as `argv` (cadences `cd` into the vault and
pass `.`). `review` verbs: `list` / `show <id>` / `approve <id>` / `reject
<id>` / `approve-entity <type/slug>` / `reject-entity <type/slug>` (each
takes `--reason`) — e.g. `python -m agent_vault.review . approve-entity
account/chase-mortgage`.

## Cadences (`cadences/*.sh VAULT_DIR`)

- `daily.sh` - `ingest` + `validate` (cheap; safe hourly).
- `weekly.sh` - `ingest -> compile -> promote -> validate` (the LLM pass; each step runs
  even if a prior fails; last failing rc is the run rc).
- `monthly.sh` - `validate` + `lint` (read-only audit; lint findings are notifications).

All four invoke `python3 -m agent_vault.<module> .` internally (fixed to match
the `agent_vault/` package layout — they used to call now-nonexistent flat
files like `ingest.py` and would fail on a fresh checkout; if you see that
bug again, `cadences/_common.sh`'s `resolve_vault()` plus each `.sh` file is
where to look).

`run_cadence.py <kind> [VAULT_DIR]` is the **cross-platform** (Windows-safe)
equivalent: pure Python, runs each stage via `sys.executable -m
agent_vault.<module>` and appends the same `discovery/_runs.jsonl` record.

**The web UI's job runner (`agent_vault/api/jobs.py`) does NOT call
`run_cadence.py` or the `.sh` wrappers.** `POST /api/jobs/run` directly
subprocess-invokes one of `python -m agent_vault.{ingest,compiler,promote,
reclassify_apply}` per request (see [`docs/API.md`](docs/API.md)) — it's a
third, independent invocation path, not a wrapper around the cadence
scripts. All three paths (`.sh`, `run_cadence.py`, `api/jobs.py`) ultimately
run the same underlying `agent_vault.*` modules, just with different
sequencing/triggering.

## Lint checks (`lint.py`) and their deterministic repairs

| Check `kind` | Meaning | Repair |
|--------------|---------|--------|
| `broken_ref` | a `related:` target has no entity file | re-run `python -m agent_vault.ingest .` (auto-stubs missing targets) |
| `stub_with_prose` | `status: stub` but the body has prose | flip status via `review` / recompile |
| `compiled_without_prose` | `status: compiled` but no prose | recompile (`python -m agent_vault.compiler .`) |
| `compile_drift` | `sources_hash` != `compiled_from_hash` | recompile the drifted entities |
| `aging_needs_review` | a `needs-review` entity is old | surface to a human (`review approve-entity`/`reject-entity`) |
| `aging_queue` | a queued proposal is old | surface to a human (`review list`/`approve`/`reject`) |
| `stuck_reclassify` | a queued reclassify not applied | `python -m agent_vault.reclassify_apply .` (human-approved) |
| `raw_not_ingested` | a `raw/` file never ingested | `python -m agent_vault.ingest .` |
| `manifest_orphan` | manifest record with no raw file | investigate (file moved/removed) |
| `ingest_error` | a file errored during ingest | inspect the source; fix extractor/patterns |

## Secrets

Referenced, never stored: `scheme://store/path` URIs only (`credential_ref:`).
`validate.py` + `secret_scan.py` reject plaintext-shaped secrets. Resolution is
on-demand (`agent-vault resolve <slug>` or `POST /api/creds/{slug}/resolve`)
and never persisted. Resolver backends live in `agent_vault/resolvers/` (9
modules — `age`, `env`, `onepassword`, `bitwarden`/`vaultwarden`, `pass`,
`gpg`, `secret-tool`, `keychain`, `vault`; config in `registry/resolvers.yaml`,
human-only). Full scheme reference: [`DOCS.md §7`](DOCS.md#7-using-it-on-your-own-files).

## Compiler contract

`compiler.py` stamps proposals with `PROMPT_CONTRACT_VERSION` + model identity. The contract
is at **version 2.0** — it now teaches the LLM to emit three new proposal kinds:
`new_biller`, `new_shape`, `new_field_mapping` (learned detection signals + field extraction).
Changing the prompt/proposal shape requires a version bump so changes are
audited — bump `PROMPT_CONTRACT_VERSION` in `agent_vault/compiler.py` and
document the new proposal kind(s) here and in `DOCS.md` §5.
`AGENT_VAULT_COMPILER=mock` is the deterministic offline path (CI); it's the
only compiler client with test coverage today — `OllamaClient` (the real AI
path) has none (see `DOCS.md` §8).

## Integration boundary (HTTP)

The vault is a **standalone HTTP service** (`agent-vault-serve`). Consumers
(like SynapseNAS) call it over the network — no file-system or subprocess
coupling, the boundary is HTTP + JSON. It exposes far more than credential
resolution: 27 routes across entities, credentials, review, jobs (including
SSE log streaming for running pipeline stages from a browser), run history,
status/ask, config, and settings. **Full reference: [`docs/API.md`](docs/API.md).**
The two simplest:

- **Health**: `http://127.0.0.1:7778/api/health`
- **Credential resolve**: `http://127.0.0.1:7778/api/creds/{slug}/resolve`
- **Auth**: Optional `VAULT_TOKEN` bearer token, scoped to `/api/*` only —
  the SPA shell and FastAPI's `/docs`/`/redoc`/`/openapi.json` are never gated.

## Gotchas

- `raw/` and `discovery/*.jsonl` are append-only; everything is idempotent (hashes,
  manifest, append-only logs). Preserve both.
- Tests run via **`pytest`** from the repo root (`[tool.pytest.ini_options]`
  in `pyproject.toml` points at `tests/`) — there is no `run_tests.sh`. CI
  (`.github/workflows/ci.yml`) runs `ruff check .`, `mypy agent_vault`, then
  `pytest -v` for the backend, and `npm test -- --run` + `npm run build` for
  `web/`. `AGENT_VAULT_COMPILER=mock` is used wherever tests touch the
  compiler. Any in-app change must keep `python -m agent_vault.validate .` clean.
- `tests/api/test_jobs.py` (SSE job streaming) can hang in some
  sandboxed/CI-constrained environments — this reproduces on an unmodified
  checkout too, so it's a pre-existing environment sensitivity, not something
  your change broke. `pytest-timeout` is a dev dependency and
  `timeout = 120` is set in `pyproject.toml`, so a hung test now fails with a
  traceback instead of stalling the run; if it trips, check that file in
  isolation (`--ignore=tests/api/test_jobs.py` for the rest).
- Running a pipeline module directly via `python -m agent_vault.<module>` may
  print a `RuntimeWarning: '...' found in sys.modules after import of package
  'agent_vault' ...` — harmless (caused by `agent_vault/__init__.py` eagerly
  importing every submodule), not a functional bug; exit codes and output are
  correct despite the warning.
- Never add a non-deterministic writer of vault state; the only LLM write is
  `compiler.py` prose.
- This file previously described a `.claude/` automation suite (custom
  agents/skills/hooks) in detail. It never existed as committed code — `git
  log --all` shows the commit that added that section touched only this file.
  If you're asked to build that tooling, treat it as new work, not a restore.

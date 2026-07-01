# AGENTS.md - `agent-vault` (Agent Vault: the knowledge-wiki app)

> The authoritative **structure + invariant reference** for Agent Vault. The vault
> Claude Code automations (vault-keeper, vault-doctor, the `/vault-*` skills, the
> guardian hooks) read THIS file as their source of truth. Deep docs: [`README.md`](README.md),
> [`DOCS.md`](DOCS.md), [`llm-wiki-schema-spec.md`](llm-wiki-schema-spec.md) (the "constitution").

A **standalone**, file-based household knowledge wiki. It runs as:
- **CLI**: `agent-vault` commands for retrieval and pipeline operations
- **Service**: `agent-vault-serve` (FastAPI HTTP service for credential resolution)
- **UI**: React web interface in `web/`

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

This is what the guardian hook (`.claude/hooks/vault_guard.py`) enforces.

| Path | Writer | Hand-edit by an agent? |
|------|--------|------------------------|
| `registry/schema.yaml` | `promote.py` / `review.py` ONLY | **BLOCKED** - grow via `/vault-add-entity-type` |
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

```
raw/ (append-only)
  -> ingest.py .       classify deterministically -> entity stubs (facts, no prose)
                        + extract fields via registry/field_mappings.yaml (additive)
  -> compiler.py .     THE LLM TOUCHPOINT: stub -> prose   (AGENT_VAULT_COMPILER=ollama|mock)
                        PROMPT_CONTRACT_VERSION 2.0 (teaches new_biller/new_shape/new_field_mapping)
  -> promote.py .      learn vocabulary: proposals -> registry (deterministic commit)
                        also graduates billers/shapes into patterns.yaml + field mappings into
                        field_mappings.yaml (new_field_mapping: always human-gated + regex gate)
  -> review.py . ...   human approve/reject (gated types/proposals/entities)
                        re-runs the field_mapping deterministic validation gate on approval
  build_index.py .     (re)build _index.json
  synapse.py <cmd>     query CLI: find|show|expiring|due|creds|resolve|list|compact
  validate.py <vault>  schema gate (exit 0 = valid) — also validates patterns.yaml + field_mappings.yaml
  lint.py <vault>      operational audit (exit 1 if any findings)
```

All scripts take the vault dir as `argv` (cadences `cd` into the vault and pass `.`).
`review.py` verbs: `list` / `show <id>` / `approve <id>` / `reject <id>` /
`approve-entity <type/slug>` / `reject-entity <type/slug>` (each takes `--reason`).

## Cadences (`cadences/*.sh VAULT_DIR`)

- `daily.sh` - `ingest` + `validate` (cheap; safe hourly).
- `weekly.sh` - `ingest -> compile -> promote -> validate` (the LLM pass; each step runs
  even if a prior fails; last failing rc is the run rc).
- `monthly.sh` - `validate` + `lint` (read-only audit; lint findings are notifications).

`run_cadence.py <kind> [VAULT_DIR]` is the **cross-platform** (Windows-safe) equivalent:
pure Python, runs each stage via `sys.executable` and appends the same `discovery/_runs.jsonl`
record. The GUI (`server/jobs.py`) invokes this, not the `.sh` wrappers; the `.sh` files remain
for cron/systemd on the Linux appliance.

## Lint checks (`lint.py`) and their deterministic repairs

| Check `kind` | Meaning | Repair |
|--------------|---------|--------|
| `broken_ref` | a `related:` target has no entity file | re-run `ingest.py .` (auto-stubs missing targets) |
| `stub_with_prose` | `status: stub` but the body has prose | flip status via `review.py` / recompile |
| `compiled_without_prose` | `status: compiled` but no prose | recompile (`compiler.py .`) |
| `compile_drift` | `sources_hash` != `compiled_from_hash` | recompile the drifted entities |
| `aging_needs_review` | a `needs-review` entity is old | surface to a human (`review.py approve-entity`/`reject-entity`) |
| `aging_queue` | a queued proposal is old | surface to a human (`review.py list`/`approve`/`reject`) |
| `stuck_reclassify` | a queued reclassify not applied | `reclassify_apply.py` (human-approved) |
| `raw_not_ingested` | a `raw/` file never ingested | `ingest.py .` |
| `manifest_orphan` | manifest record with no raw file | investigate (file moved/removed) |
| `ingest_error` | a file errored during ingest | inspect the source; fix extractor/patterns |

## Secrets

Referenced, never stored: `scheme://store/path` URIs only (`credential_ref:`).
`validate.py` + `secret_scan.py` reject plaintext-shaped secrets. Resolution is on-demand
(`synapse resolve <slug>`) and never persisted. Resolver backends live in `resolvers/`
(config in `registry/resolvers.yaml`, human-only).

## Compiler contract

`compiler.py` stamps proposals with `PROMPT_CONTRACT_VERSION` + model identity. The contract
is at **version 2.0** — it now teaches the LLM to emit three new proposal kinds:
`new_biller`, `new_shape`, `new_field_mapping` (learned detection signals + field extraction).
Changing the prompt/proposal shape requires a version bump (see `/vault-extend-compiler`) so
changes are audited. `AGENT_VAULT_COMPILER=mock` is the deterministic offline path (CI).

## Integration boundary (HTTP)

The vault is now a **standalone HTTP service**. Consumers (like SynapseNAS) call it
over the network:

- **Endpoint**: `http://127.0.0.1:7778/api/creds/{slug}/resolve` (credential resolution)
- **Health**: `http://127.0.0.1:7778/api/health`
- **Auth**: Optional `VAULT_TOKEN` bearer token

No file-system or subprocess coupling — the boundary is HTTP + JSON.

## The vault automation suite (`.claude/`)

These manage/protect/develop/extend this app (they are NOT app code):
- Agents: `vault-keeper` (structure-aware guardian/lead), `vault-doctor` (troubleshoot).
- Skills: `/vault-run` (operate the pipeline), `/vault-doctor`, `/vault-add-entity-type`,
  `/vault-add-source` (new file-type/media extractor), `/vault-add-resolver`,
  `/vault-extend-compiler`, `/vault-spawn-agent`.
- Hooks: `vault_guard` (block hand-edits to schema/aliases; warn on append-only) +
  `vault_gate` (run `validate.py`+`lint.py` after any `agent-vault/` change).

## Gotchas

- `raw/` and `discovery/*.jsonl` are append-only; everything is idempotent (hashes,
  manifest, append-only logs). Preserve both.
- Tests run via the repo-root `run_tests.sh` (plain Python, not pytest); CI uses
  `AGENT_VAULT_COMPILER=mock`. Any in-app change must keep `validate.py` clean.
- Never add a non-deterministic writer of vault state; the only LLM write is
  `compiler.py` prose.

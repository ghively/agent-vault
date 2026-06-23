# AGENTS.md â€” `agent-vault` (embedded document wiki)

> A harness-independent, **file-based** household knowledge wiki â€” the repo's
> original app, now embedded in SynapseNAS. Parent: [../AGENTS.md](../AGENTS.md).
> Full docs: [`README.md`](README.md), [`DOCS.md`](DOCS.md),
> [`llm-wiki-schema-spec.md`](llm-wiki-schema-spec.md) (the "constitution").

## Core discipline

The **LLM writes prose only** (one touchpoint: `compiler.py`). Everything else â€”
facts, classification, vocabulary, retrieval â€” is **deterministic Python**. This
eliminates hallucination risk and keeps the vault auditable.

**One author per file region** (enforced):

| Region | Author | Rule |
|--------|--------|------|
| Frontmatter | Python (ingest/promote) | LLM may set only `status`, `compiled_from_hash` |
| `LINKS:BEGIN/END` block | Python (regenerated each run) | from `related:` |
| Prose body | LLM (`compiler.py`) | never invents facts â€” uses `[NEEDS SOURCE: â€¦]` |

## Pipeline

```
raw/ (append-only sources)
  â””â”€ ingest.py     classify deterministically â†’ entity stubs (facts, no prose)
  â””â”€ compiler.py   THE LLM TOUCHPOINT: stub â†’ prose  (Ollama, or mock for CI)
  â””â”€ promote.py    learn vocabulary: proposals â†’ registry (deterministic commit)
  â””â”€ review.py     human approve/reject (gated types/proposals)
  build_index.py   reindex; synapse.py = query CLI
```

Cadence scripts wire it to cron/systemd: `cadences/daily.sh` (ingest+validate),
`weekly.sh` (full LLM pass), `monthly.sh` (validate+lint).

## Module inventory

| Module | Role |
|--------|------|
| `ingest.py` | classify `raw/` â†’ stubs; auto-stub missing `related:` targets |
| `compiler.py` | single LLM write (prose); `AGENT_VAULT_COMPILER=ollama|mock` |
| `promote.py` | sole writer of registry vocabulary (auto/queue/defer/reject) |
| `build_index.py` / `synapse.py` | index builder / query CLI |
| `validate.py` / `lint.py` | schema gate / read-only operational audit |
| `review.py` / `reclassify_apply.py` | human approve-reject / apply reclassifications |
| `collections_importer.py` | catalog media (Steam/Goodreads/IMDB/â€¦) |
| `secret_scan.py` / `compact.py` / `locking.py` | secret guard / log compaction / write lock |

## Directories

`registry/` (vocabulary â€” Python-write only, except `resolvers.yaml`), `entities/`
(12-type taxonomy, one `.md` per entity), `raw/` (append-only sources), `discovery/`
(append-only proposals + audit logs), `resolvers/` (9 credential backends),
`cadences/` (shell scripts), `tests/`.

## Integration with SynapseNAS â€” keep this boundary

Agent Vault is **isolated**. The server **never imports** it:

- Reads: `server/vault.py` parses the file contract directly (read-only).
- Mutations: `server/actions.py` **shells out** to `review.py` / `synapse.py`, then
  re-runs `build_index.py`.

The file contract is the only interface. Don't add in-process coupling.

## Gotchas

- Secrets are **referenced, never stored**: `scheme://store/path` URIs only;
  `validate.py` + `secret_scan.py` enforce no plaintext.
- `raw/` is immutable/append-only; everything is idempotent (hashes, manifest,
  append-only logs). Preserve both properties.
- Tests run via the repo-root `run_tests.sh` (plain Python), not pytest; CI uses
  `AGENT_VAULT_COMPILER=mock` (no Ollama).

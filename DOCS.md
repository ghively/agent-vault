# Agent Vault — Full Documentation

This document explains, in plain terms, **what Agent Vault is, what is actually
built versus scaffolded, how the pieces fit together, and how to run it on your
own files.**

It is the practical companion to the two design documents:
- [`llm-wiki-schema-spec.md`](./llm-wiki-schema-spec.md) — the "constitution": file
  format and ownership rules.
- [`llm-wiki-build-guide.md`](./llm-wiki-build-guide.md) — the taxonomy and the
  intended build order.

This doc covers the CLI/pipeline (the vault's core). Two more docs cover the
other two tiers in depth: [`docs/API.md`](./docs/API.md) for the FastAPI HTTP
service, and [`web/README.md`](./web/README.md) for the React desktop UI.

If you read nothing else, read [§1](#1-what-it-is) and
[§2 Implementation status](#2-implementation-status-real-vs-scaffolding).

---

## 1. What it is

You have a NAS (a drive on your home network) full of accumulated files: bank
statements, utility bills, appliance manuals, warranties, insurance policies,
tax documents, photos, exported emails. It is a junk drawer — unsearchable, and
you no longer remember what's in it.

**Agent Vault reads that pile and turns it into an organized, searchable
notebook about your household.** It produces one Markdown page per real-world
thing — your furnace, your checking account, your car — each carrying the facts
that matter (when a warranty expires, when a bill is due, an account's last 4
digits) and cross-linked to the documents that prove them.

Then you ask it questions instead of digging:

```
agent-vault find furnace            # fuzzy search
agent-vault show bofa               # full record for one entity
agent-vault expiring --days 90      # warranties / renewals lapsing soon
agent-vault due                     # bills / tasks coming due
agent-vault creds bofa              # the credential REFERENCE (not the secret)
agent-vault list account            # everything of one type
```

(`agent-vault` is the installed console script; it's built from
`agent_vault/synapse.py`, historically called "synapse" in this repo's design
docs and diagrams — the two names refer to the same retrieval CLI.)

### The one design choice that matters

Almost the entire system is **deterministic, predictable Python — no AI.** A
language model is allowed exactly **one** job: writing the short human-readable
summary paragraph on each page. It may **never** decide how things are filed,
change a date, or edit the vocabulary. This is deliberate: LLMs hallucinate, so
the model is boxed into "prose only," and every fact you query comes from the
actual document, not from a model's guess.

This is enforced by an **ownership model** — three authors, each owning a strict
slice of every file (see [§4](#4-the-data-contract)).

---

## 2. Implementation status: real vs scaffolding

The *machine* is real and tested. The *contents* shipped in this repo are
samples. The *AI step* needs an external server you provide.

| Component | Status | Notes |
|-----------|--------|-------|
| `ingest.py` — read/sort files → entity stubs | ✅ **Real, working** | Rich classification needs optional libs (`pdfplumber`, `Pillow`, `python-magic`). Without them it still runs but text-less files fall to `unknown` (you now get a stderr warning). |
| `build_index.py` — build the search index | ✅ **Real, working** | Only needs `pyyaml`. |
| `synapse.py` — the query CLI | ✅ **Real, working** | Pure lookup over the index. |
| `compiler.py` — **MockClient** | ✅ **Real, working** | Deterministic, offline, no network. Used by all tests/CI. |
| `compiler.py` — **OllamaClient** (the real AI) | ⚙️ **Real code, external dependency** | Calls an [Ollama](https://ollama.com) server over HTTP. You must run that server yourself; nothing is bundled. Its parsing and error handling are unit-tested with a mocked HTTP transport (`tests/test_ollama_client.py`); no live Ollama server is exercised in CI. |
| `promote.py` — learn vocabulary from proposals | ✅ **Real, working** | Atomic, locked, idempotent. |
| `reclassify_apply.py` — apply approved re-filings | ✅ **Real, working** | Two-phase, crash-recoverable. |
| `lint.py` — anomaly report | ✅ **Real, working** | Read-only. |
| `collections_importer.py` — Steam/Goodreads/IMDB/Letterboxd/Discogs/Kindle/Audible/CSV | ✅ **Real, working** | Bookkeeping import. |
| `validate.py` — schema gate | ✅ **Real, working** | The smoke test every stage reuses. Now also gates the `schema.yaml` **version** (`SCHEMA_VERSION`). |
| `search.py` — full-text ranked search | ✅ **Real, working** | SQLite **FTS5** sidecar (`_index.db`) over entity **prose + metadata**, bm25-ranked; substring fallback when FTS5 is absent. Powers `agent-vault find` and `GET /api/search`. Deterministic, no model. |
| `embeddings.py` + `semantic.py` — semantic / hybrid search | ✅ **Real, working** | Pluggable embedder (Ollama `/api/embeddings`, or a deterministic offline `MockEmbedder`); vector sidecar (`_vectors.db`) + cosine; `hybrid` = reciprocal-rank fusion of FTS+semantic. `AGENT_VAULT_EMBEDDER=ollama\|mock`. Read-side only. |
| `rag.py` — grounded, cited answers | ✅ **Real, working** | Retrieves top-k, constrains a **local** model to that prose, returns an answer with **verified citations** (`grounded` flag). Pluggable answerer (Ollama or offline `MockAnswerer`, `AGENT_VAULT_RAG=ollama\|mock`). Powers `GET /api/answer` + the `vault_ask` MCP tool. Never writes vault state. |
| `mcp_tools.py` + `mcp_server.py` — MCP server | ✅ **Real, working** | `agent-vault-mcp` (optional `[mcp]` extra) exposes 7 tools (`vault_search`/`ask`/`get`/`list`/`status`/`submit_source`/`resolve_credential`). Reads lock-free; the only write (`submit_source`) is append-only into `raw/` with actor attribution; secret resolution is opt-in (`AGENT_VAULT_MCP_ALLOW_RESOLVE=1`). |
| `migrate.py` — schema versioning | ✅ **Real, working** | `agent-vault-migrate check/apply`; walks a vault from its stamped `schema.yaml` version up to `SCHEMA_VERSION`. No migrations registered yet (v1 is current); the concrete protection today is the validate gate. |
| `backup.py` — vault snapshot / export / restore | ✅ **Real, working** | `agent-vault-backup export/restore/list`; tar.gz of entities/registry/discovery (+`--include-raw`). Safe extraction (no traversal), refuses to clobber a non-empty vault without `--force`, reindexes on restore. Derived indexes excluded (rebuilt). |
| `api/auth.py` + `api/audit.py` — per-agent auth & access audit | ✅ **Real, working** | Per-agent tokens → `Identity(actor, scopes)` (`read`/`write`/`resolve`) via `VAULT_TOKEN` (legacy admin), `VAULT_TOKENS` env, or `registry/tokens.yaml`. A pure-ASGI middleware audits every write/resolve to `discovery/_access.jsonl` (never bodies/secrets). |
| `cadences/*.sh` — daily/weekly/monthly wrappers | ✅ **Real, working** | Plain POSIX `sh`; wire to any runner. |
| **Credential resolution** (turn a `creds` ref into the actual secret) | ✅ **Real, working** | The `resolvers/` package ships **9 backends**: `age`, `env`, `onepassword`, `bitwarden`/`vaultwarden`, `pass`, `gpg`, `secret-tool` (GNOME keyring), `keychain` (macOS), `vault` (HashiCorp). `synapse resolve <slug>` fetches the secret on demand; `creds` shows only the reference; plaintext is never persisted. Adding another = one module + one stanza. |
| **File extractors** (turn documents into text) | ✅ **Real, working** | PDF, email (+attachments), JPEG/PNG (EXIF), text/CSV, **and now `.docx` / `.xlsx` / `.html`** (pure stdlib). Each degrades to empty text on error, never crashes. |
| **Compile token efficiency** (contract 1.1) | ✅ **Real, working** | Source text is deterministically de-noised + budgeted before the LLM sees it; a fact-bearing line is never dropped; per-entity + aggregate token logging. The LLM still sees all real content. |
| **Write-side secret guard** (keep secrets out of the vault) | ✅ **Real, working** | `secret_scan.py`: ingestion scans each source for secret-shaped strings and downgrades the stub to `needs-review` (never recording the secret); `validate.py` hard-fails any entity with a real credential in a frontmatter field. Two strictness tiers keep false positives out of the schema gate. |
| The 3 files under `entities/` | 🎭 **Sample data** | Hand-written demos (furnace, BofA checking, furnace warranty). Not your data. Tests sandbox-copy these into `tmp_path` rather than shipping a separate synthetic fixture corpus. |
| `raw/*` folders | 📭 **Empty** | Only `.gitkeep` placeholders. This is where *your* documents go. |
| `discovery/`, `raw/collections/` | 📭 **Created on demand** | Do not exist until a run produces them. |
| `registry/schema.yaml` (taxonomy) | ✅ **Real, lightly seeded** | 12 real entity types plus the `unknown` holding-pen type (never compiled); `tags:` has a handful of seed entries. |
| `registry/patterns.yaml` (classifier vocabulary) | ✅ **Real, sizeable** | ~104 billers + 25 document shapes (see §8 for the category breakdown). Still grows as you meet niche/regional vendors. |
| `registry/aliases.yaml` | ✅ **Real, tiny** | 2 seed aliases (`"bofa"`, `"bank of america checking"` → `bofa-checking`). Grows via `promote.py`. |

### What "not built" concretely means for you

- **The AI summaries require setup.** Out of the box, with `AGENT_VAULT_COMPILER=mock`,
  you get deterministic template prose. To get real synthesized summaries you
  run Ollama and point `OLLAMA_HOST`/`OLLAMA_MODEL` at it.
- **There is no real data here.** Point `ingest.py` at a folder of your own files
  to make it useful.

---

## 3. Architecture & data flow

Everything is files on disk. No database, no service, no shared in-process
state. Each script reads files, writes files, and exits. You can run them in any
order, from any runner.

```
        YOUR FILES                        THE WIKI                      YOU
   ┌──────────────────┐           ┌──────────────────────┐      ┌──────────────┐
   │ raw/             │  ingest   │ entities/<type>/*.md  │ index│ synapse find │
   │  statements/     │ ────────► │  (one page per thing) │ ───► │ synapse show │
   │  documents/      │           │                       │      │ synapse due  │
   │  email/  media/  │           │  frontmatter (facts)  │      │ synapse ...  │
   └──────────────────┘           │  LINKS block          │      └──────────────┘
        (append-only)             │  prose body  ◄────────┼── compile (the LLM)
                                   └──────────────────────┘
                                           │  ▲
                          proposals.jsonl  │  │  registry/ (vocabulary)
                                           ▼  │  schema · patterns · aliases
                                      ┌──────────────┐
                                      │  promote     │  ← only writer of vocabulary
                                      └──────────────┘
```

**The pipeline, end to end:**

1. **`ingest.py`** — walk `raw/`, hash each file (so re-runs are no-ops), detect
   its kind, extract text/metadata, classify it (vendor patterns × document
   shapes × file-type priors), and write an entity **stub** (facts only, empty
   prose). Emails fan out: the message and each attachment become separate,
   cross-linked subjects. Missing link targets get minimal `needs-review` stubs
   so the graph stays closed.
2. **`compiler.py`** — for each stub (or any page whose sources changed), call
   the LLM to write the **prose body only**, and append any vocabulary
   **proposals** it makes to `discovery/proposals.jsonl`. Flips `status: stub →
   compiled`.
3. **`promote.py`** — drain those proposals, collapse synonyms through the alias
   map, count "sightings," and graduate the good ones into the registry per the
   thresholds in `schema.yaml`. This is the **only** writer of the vocabulary.
4. **`build_index.py`** + **`synapse.py`** — pre-extract every queryable field
   into `_index.json`, then answer questions by *filtering* it (never by
   reasoning over text).
5. **`validate.py`** / **`lint.py`** — schema gate (hard pass/fail) and a
   read-only operational anomaly report.

Two side branches:
- **`collections_importer.py`** — for catalog media you don't have files for
  (Steam/Goodreads/IMDB/CSV exports) → one `collection` entity per row.
- **`reclassify_apply.py`** — the human-approved step that physically re-files an
  entity across type folders and rewrites every cross-reference.

---

## 4. The data contract

The files **are** the contract. Three authors, each owning a strict region of
every entity page:

```
---
# ── FRONTMATTER ───────────────  author: PYTHON (ingest)   the FACTS
slug: carrier-furnace
type: asset
subtype: hvac
title: Carrier Furnace (Basement)
status: compiled
confidence: 0.95
created: 2026-05-21
sources: [raw/documents/furnace-receipt.pdf]
sources_hash: a1b2c3d4e5f6
compiled_from_hash: a1b2c3d4e5f6     # ← compile may set ONLY this + status
expires: 2031-03-15
related: [document/carrier-furnace-warranty]
---

<!-- LINKS:BEGIN -->                  author: PYTHON   regenerated every run
- Related: [[document/carrier-furnace-warranty]]
<!-- LINKS:END -->

The Carrier furnace in the basement was installed in 2021 and carries a   ← PROSE
10-year parts warranty through 2031. [NEEDS SOURCE: model/serial number].  author: LLM
```

| Region | Author | Rule |
|--------|--------|------|
| Frontmatter | **Python** (ingest/promote) | The LLM must never touch it. Compile may change **only** `status` and `compiled_from_hash`. |
| LINKS block | **Python** | Regenerated from `related:` every run. |
| Prose body | **LLM** (compile) | The model's *only* writable surface. Never invents facts — emits `[NEEDS SOURCE: …]` instead. |

**`status` lifecycle:** `stub` → (compile) → `compiled`; low-confidence files
land in `needs-review` (or `unknown`) for a human to fix before they're worth
compiling; `archived` is retired-but-kept.

**`sources_hash` vs `compiled_from_hash`:** the first is a fingerprint of the
source set; the second records what the prose was last built from. When they
diverge, the compile pass knows to rewrite the prose — that's how new evidence
triggers a fresh summary.

### The registry (the vocabulary, under `registry/`)

| File | Author | Purpose |
|------|--------|---------|
| `schema.yaml` | promotion only | The 12 types, their subtypes, page templates, gating flags, promotion thresholds, and graduated tags. |
| `patterns.yaml` | human + promotion | The classifier's vocabulary: known billers and document shapes. **Edit this directly** to teach it a new vendor. Promotion also appends graduated billers/shapes here (evidence-backed + audited). |
| `aliases.yaml` | promotion only | Surface form → slug (`"boa"` → `bofa-checking`). |
| `field_mappings.yaml` | promotion only | Learned field-extraction regexes (amounts, dates, IDs). **Promote-only writer** — each one is human-approved and passes a deterministic validation gate. |
| `resolvers.yaml` | human | Credential backend config (scheme → backend module). 9 backends implemented (see §2). |
| `tokens.yaml` (optional) | human | Per-agent API tokens → `{actor, scopes}` for the HTTP service (`read`/`write`/`resolve`). Parallel to `resolvers.yaml`; absent = single `VAULT_TOKEN` / open. |
| `_entity-template.md` | human | The three-region page template, fully commented. |

**Derived + audit artifacts** (never hand-edit — regenerated or append-only):

| Path | Writer | Notes |
|------|--------|-------|
| `_index.json` / `_index.md` | `build_index.reindex()` | Structured search index (rebuilt after every mutating pass). git-ignored. |
| `_index.db` | `build_index` / `search` | SQLite **FTS5** full-text sidecar. Derived, rebuildable, git-ignored. |
| `_vectors.db` | `semantic build` | Embedding vector index for semantic search. Derived, git-ignored. |
| `discovery/proposals.jsonl` | compiler (append) | LLM vocabulary proposals awaiting promotion. |
| `discovery/promoted.jsonl` | promote/review (append) | Every promotion/approval decision (audit). |
| `discovery/_runs.jsonl` | cadences + API jobs (append) | Pipeline run history (cadence/op, rc, duration). |
| `discovery/_access.jsonl` | `api/audit.py` (append) | Per-agent write/resolve audit (actor, method, path, status — never secrets). |
| `discovery/_submissions.jsonl` | MCP `submit_source` (append) | Agent source contributions (actor, path, sha256). |

**The anti-rot rule:** the LLM *proposes* vocabulary additions; only the
deterministic `promote.py` *commits* them. A non-deterministic component never
writes the vocabulary it later reads. The vault now learns at **three levels**
through this same gate: **taxonomy** (types/subtypes/tags/aliases → schema.yaml),
**detection** (billers/shapes → patterns.yaml), and **field extraction** (regex
mappings → field_mappings.yaml). All three flow through the propose →
deterministic-validate → promote pipeline.

#### Learned field-mapping safety

A promoted field mapping runs against **every** future ingest, so a bad regex —
catastrophic backtracking (ReDoS), a capture that never fires, or one that
silently captures a secret — corrupts intake across many documents before anyone
notices. That's why `new_field_mapping` is **always human-gated** (`auto: false`)
and must pass a **deterministic validation gate** before promotion:

- the regex **compiles** and is bounded (≤ 200 chars, ≤ 2 capture groups);
- it is **ReDoS-safe** (no nested quantifier — an AST walk rejects `(x+)+`);
- it **matches its cited evidence** and yields a non-empty capture at the named group;
- the captured value **parses** per its declared type (`text`/`int`/`float`/`iso-date`);
- the captured value is **not secret-shaped** (the `secret_scan` module);
- `id` and `field` are valid slugs.

`promote.py` runs this gate before queuing; `review.py` **re-runs it at approval
time** — a human click can never bypass it. If a mapping fails, it is rejected
with the precise reason. Field extraction is also **strictly additive** to
built-in extractors and **secret-scanned** at ingest.

### Rules that never bend (from the spec)

1. One author per file region.
2. `raw/` is append-only — source documents are immutable.
3. The LLM proposes; deterministic code commits.
4. Secrets are **referenced** (`scheme://store/path`), never stored in plaintext.

---

## 5. Script reference

Every module below lives under `agent_vault/` and is invoked as
**`python -m agent_vault.<module> [vault-dir] [args...]`** (default vault dir:
current directory) — pass `-h`/`--help` for its own usage. Five modules have
installed console-script entry points (`pyproject.toml`'s `[project.scripts]`):
`agent-vault` (→ `synapse`), `agent-vault-serve` (→ `serve`), `agent-vault-mcp`
(→ `mcp_server`, optional `[mcp]` extra), `agent-vault-backup` (→ `backup`), and
`agent-vault-migrate` (→ `migrate`). Everything else has **no** console entry —
the `python -m agent_vault.<module>` form is the only way to run it directly.

| Module | Invocation | What it does | LLM? | Writes |
|--------|------------|---------------|:----:|--------|
| `ingest` | `python -m agent_vault.ingest .` | `raw/` → entity stubs; hashes for idempotency; classifies; auto-stubs missing link targets | no | `entities/`, `raw/_manifest.jsonl`, `_index.json` |
| `compiler` | `python -m agent_vault.compiler .` | Writes prose for stubs / drifted pages; logs proposals | **yes** | prose body, `status`+`compiled_from_hash`, `discovery/proposals.jsonl` |
| `promote` | `python -m agent_vault.promote .` | Drains proposals → graduates vocabulary by threshold | no | `registry/schema.yaml`, `registry/aliases.yaml`, `registry/patterns.yaml` (billers/shapes), `registry/field_mappings.yaml`, `discovery/promoted.jsonl` |
| `build_index` | `python -m agent_vault.build_index .` | Walks entities → `_index.json` (+ human `_index.md`) + the FTS5 sidecar (`_index.db`). Exposes `reindex(vault)` that every mutating pass calls (O7 freshness) | no | `_index.json`, `_index.md`, `_index.db` |
| `search` | (library; drives `agent-vault find` + `GET /api/search`) | Ranked full-text search over prose+metadata (FTS5 bm25; metadata fallback); `search(mode=fts\|semantic\|hybrid)` | no | nothing (builds `_index.db` via `build_index`) |
| `embeddings` + `semantic` | `python -m agent_vault.semantic build\|query .` | Vector index (`_vectors.db`) + cosine / hybrid semantic search. Pluggable embedder (`AGENT_VAULT_EMBEDDER=ollama\|mock`) | no* | `_vectors.db` (derived; embedder may call Ollama) |
| `rag` | `python -m agent_vault.rag "<question>" .` | Grounded, **cited** answer over the vault (retrieve → local model → verified citations). `AGENT_VAULT_RAG=ollama\|mock` | **yes** (read-only; never writes vault) | nothing |
| `mcp_server` | `agent-vault-mcp` (console; `[mcp]` extra) | MCP server exposing 7 tools to agents (search/ask/get/list/status/submit_source/resolve_credential) | no† | via tools only: `submit_source` appends to `raw/` + `discovery/_submissions.jsonl` |
| `backup` | `agent-vault-backup export\|restore\|list` | Vault snapshot: tar.gz of entities/registry/discovery (+`--include-raw`); safe restore (reindexes) | no | the archive; restore writes `entities/`,`registry/`,`discovery/` |
| `migrate` | `agent-vault-migrate check\|apply .` | Schema-version drift report + migration runner (bumps `schema.yaml` version) | no | `registry/schema.yaml` version line (on `apply`) |
| `synapse` | `agent-vault <cmd>` (console script) | Query CLI: `find`/`show`/`due`/`expiring`/`creds`/`resolve`/`list`/`compact` | no | nothing read-only (except `compact --apply`) |
| `compact` | `agent-vault compact [--apply]` or `python -m agent_vault.compact .` | Bounds the append-only discovery logs without changing outcomes | no | rewrites `discovery/*.jsonl` (+ `.bak`) only with `--apply` |
| `locking` | (library, not run directly) | One vault-wide advisory write lock (used by all mutating passes) | no | `registry/_vault.lock` |
| `resolvers/` | (library, not run directly) | Credential resolution: dispatcher + 9 backends (`age`, `env`, `onepassword`, `bitwarden`/`vaultwarden`, `pass`, `gpg`, `secret-tool`, `keychain`, `vault`) | no | nothing (reads external secret stores on demand) |
| `validate` | `python -m agent_vault.validate .` | Schema validator (incl. no-plaintext-secret gate); exit 1 on any error | no | nothing |
| `secret_scan` | (library, not run directly) | Detects secret-shaped strings; used by ingest (flag) + validate (reject) | no | nothing (library) |
| `lint` | `python -m agent_vault.lint .` | Operational anomaly report (9 checks); exit 1 on findings | no | optional JSON report only |
| `reclassify_apply` | `python -m agent_vault.reclassify_apply .` | Applies human-approved reclassify proposals; re-files + rewrites refs | no | entity files, `discovery/promoted.jsonl` |
| `review` | `python -m agent_vault.review . <cmd>` | Human approve/reject: queued proposals (incl. new types) + needs-review entities | no | registry (on approve), entity `status:` lines, `discovery/promoted.jsonl` |
| `collections_importer` | `python -m agent_vault.collections_importer . --source <path>` | Library exports → `collection` entities | no | `entities/collection/`, `raw/collections/_imports.jsonl` |
| `serve` | `agent-vault-serve` (console script) | FastAPI HTTP service — see [`docs/API.md`](./docs/API.md) | no | nothing (reads vault on demand) |

The core invariant is unchanged: **`compiler.py` is the only LLM writer of vault
state.** `semantic`/`rag` may call a local model for embeddings/answers, but only
on the **read** side — they rank and answer, they never author facts or
vocabulary. Both ship a deterministic offline mock so CI never needs a model.

**Cadence wrappers** (`cadences/`, plain `sh` — wire to cron/systemd/anything):

| Wrapper | Runs | Cost |
|---------|------|------|
| `daily.sh` | ingest + validate | cheap, idempotent |
| `weekly.sh` | ingest + compile + promote + validate | the LLM cadence |
| `monthly.sh` | validate + lint | read-only audit |
| `import_collections.sh` | the collections importer | manual, on demand |

---

## 6. Setup & dependencies

**Required:** Python 3.11+ and the package itself — there's no separate
`requirements.txt`; dependencies live in `pyproject.toml`.

```
pip install -e .          # pyyaml, fastapi, uvicorn, sse-starlette + the 5 console scripts
pip install -e ".[dev]"   # + pytest, ruff, mypy — needed to run the test suite (see §5 above and CI)
pip install -e ".[mcp]"   # + the Model Context Protocol SDK — needed only for `agent-vault-mcp`
```

Full-text search (`agent-vault find`, `GET /api/search`) uses Python's stdlib
`sqlite3` **FTS5** extension when present; if your SQLite lacks FTS5, retrieval
transparently falls back to a metadata substring scan (no error). Semantic
search / RAG additionally need an embedder/model — see below.

**Recommended** (without these, PDFs and images yield no text and pile up in
`unknown` — `ingest` warns you on stderr when they're absent):

```
pip install pdfplumber pillow python-magic
```

`.docx` / `.xlsx` / `.html` need no extra packages — they're parsed with the
standard library.

To run the test suite: `pytest` (or `pytest -v`) from the repo root —
`[tool.pytest.ini_options]` in `pyproject.toml` points it at `tests/`. `ruff
check .` and `mypy agent_vault` are the lint/typecheck commands CI also runs
(`.github/workflows/ci.yml`).

**For real AI summaries** (otherwise use the offline mock):

```
# install and run Ollama (https://ollama.com), then:
export AGENT_VAULT_COMPILER=ollama
export OLLAMA_HOST=http://localhost:11434
export OLLAMA_MODEL=qwen2.5:7b-instruct
```

The Ollama client's sampling/runtime and resilience knobs are **config, not
code** (same ethos as `OLLAMA_HOST`/`OLLAMA_MODEL`) — all optional:

| Env var | Default | Purpose |
|---|---|---|
| `OLLAMA_TEMPERATURE` | `0.2` | Sampling temperature; low for faithful, low-variance prose. |
| `OLLAMA_NUM_CTX` | `max(8192, ⌈(total_source_chars+4000)/3.5⌉)` | Context window. The default tracks `AGENT_VAULT_TOTAL_SOURCE_CHARS`, so raising the source budget for a big-context model isn't silently truncated by Ollama at 8192. |
| `OLLAMA_RETRIES` | `2` | Extra attempts on a **transient** connection/timeout fault (HTTP 4xx/5xx and bad JSON are never retried). The weekly cadence is the only LLM pass, so a momentary Ollama restart shouldn't drop entities until next week. |
| `OLLAMA_RETRY_BACKOFF_S` | `1.0` | Linear backoff base between retries. |

**Retrieval / RAG** (semantic search + cited answers — all optional; each has a
deterministic offline mock so CI needs no model):

| Env var | Default | Purpose |
|---|---|---|
| `AGENT_VAULT_EMBEDDER` | `ollama` | Embedder for semantic search: `ollama` (real) or `mock` (deterministic, offline). |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Ollama embedding model for `AGENT_VAULT_EMBEDDER=ollama`. |
| `AGENT_VAULT_RAG` | `ollama` | Answerer for `GET /api/answer` / `vault_ask`: `ollama` or `mock`. |
| `AGENT_VAULT_RAG_CONTEXT_CHARS` | `6000` | Max chars of retrieved prose fed to the RAG model. |

**Auth (HTTP service)** — see [`docs/API.md`](./docs/API.md#auth-model) for the
full model:

| Env var | Default | Purpose |
|---|---|---|
| `VAULT_TOKEN` | `""` (open) | Legacy single admin token (all scopes). |
| `VAULT_TOKENS` | `""` | JSON `{"<token>": {"actor","scopes":[read\|write\|resolve]}}` for per-agent tokens (or use `registry/tokens.yaml`). |
| `AGENT_VAULT_MCP_ALLOW_RESOLVE` | unset | Set to `1` to let the MCP `vault_resolve_credential` tool return secrets (off by default). |

To run fully offline / in CI, use the deterministic mock clients:

```
export AGENT_VAULT_COMPILER=mock   # prose compiler
export AGENT_VAULT_EMBEDDER=mock   # semantic search
export AGENT_VAULT_RAG=mock        # cited answers
```

---

## 7. Using it on your own files

```
# 0. one-time: install deps (see §6)

# 1. drop your documents into raw/ (append-only; organize into the
#    subfolders if you like — the classifier doesn't require it)
cp ~/scans/*.pdf  agent-vault/raw/documents/
cp ~/exports/*.eml agent-vault/raw/email/

# 2. ingest — read, classify, write stubs
python -m agent_vault.ingest .

# 3. compile — write the human summaries (needs Ollama, or use mock)
AGENT_VAULT_COMPILER=mock python -m agent_vault.compiler .       # offline
#   or: AGENT_VAULT_COMPILER=ollama python -m agent_vault.compiler .

# 4. promote — fold any new vocabulary into the registry
python -m agent_vault.promote .

# 5. check & query
python -m agent_vault.validate .
agent-vault find <text>
agent-vault expiring --days 90
```

Then automate steps 2–5 by pointing a runner at the cadence scripts:

```cron
@hourly  /path/to/agent-vault/cadences/daily.sh   /path/to/agent-vault
@weekly  /path/to/agent-vault/cadences/weekly.sh  /path/to/agent-vault
@monthly /path/to/agent-vault/cadences/monthly.sh /path/to/agent-vault
```

**Credentials (optional).** Record *where* a secret lives, never the secret:

```yaml
# in an entity's frontmatter (Python-owned):
credential_ref: age://banking/bofa-login     # scheme://store/path
```

Configure the backend once in `registry/resolvers.yaml`, then:

```
agent-vault creds bofa-checking      # shows the REFERENCE + backend only
agent-vault resolve bofa-checking    # fetches the SECRET on demand -> stdout
```

`resolve` prints the secret to stdout and nothing else (so it pipes cleanly);
context goes to stderr; the plaintext is never written back into the vault.

Shipped backends (pick per ref by scheme) — all 9 modules under
`agent_vault/resolvers/`:

| Scheme | Ref shape | Needs | Notes |
|--------|-----------|-------|-------|
| `age` | `age://store/path` | `age` binary + keyfile + `store_dir` of `.age` files | recommended for NAS; has a real encrypt→resolve round-trip test |
| `onepassword` | `onepassword://vault/item[/field]` | `op` CLI, signed in (or `OP_SERVICE_ACCOUNT_TOKEN`) | field defaults to `password` |
| `bitwarden` | `bitwarden://item[/field]` | `bw` CLI, unlocked (`BW_SESSION`) | built-in or custom fields |
| `vaultwarden` | `vaultwarden://item[/field]` | `bw` CLI pointed at your server | same backend module as bitwarden |
| `pass` | `pass://category/entry` | `pass` (the standard Unix password manager) CLI | `pass show category/entry`; first line = the secret |
| `gpg` | `gpg://store/path` | `gpg` binary + `store_dir` of `.gpg` files | has a real encrypt→resolve round-trip test |
| `secret-tool` | `secret-tool://service[/account]` | `secret-tool` CLI (GNOME keyring) | Linux desktop only |
| `keychain` | `keychain://service[/account]` | `security` CLI (macOS Keychain) | macOS only |
| `vault` | `vault://mount/path[/field]` | HashiCorp Vault CLI, authenticated via `VAULT_ADDR`/`VAULT_TOKEN` | `vault kv get -field=<field> <mount>/<path>` |
| `env` | `env://store/path` → `STORE_PATH` | — | dev / non-secret only |

The CLI-based backends (`op`/`bw`/`pass`/`secret-tool`/`security`/`vault`) are
tested with ref-parsing/security unit tests (`tests/test_resolvers.py`) plus
per-backend ref→argv mapping and mocked-`subprocess.run` argv/error tests
(`tests/test_resolver_backends.py`); the env backend and the dispatcher's
success/failure branches are in `tests/test_resolvers_env.py`. `age` and `gpg`
additionally get real encrypt→resolve round trips (`tests/test_resolver_backends.py`,
skipped where the binary isn't installed). None have been verified against live
1Password/Bitwarden/Vault accounts yet.

Auth is the operator's job (sign into `op`, unlock `bw`); the resolver just
invokes the already-authenticated CLI. Add another backend by dropping a module
in `agent_vault/resolvers/` and a stanza in `resolvers.yaml` — no schema change.

**Reviewing the machine's guesses:** anything it wasn't sure about is written as
`status: needs-review`. Open the file, fix `type`/`subtype` (or merge a
duplicate), and let the next compile pass pick it up. `lint` will nag you
about review items that have been sitting too long.

---

## 8. Known gaps / not yet built

- **Nine resolver backends ship** — see the table in §7 for schemes and
  requirements. None have been verified against live 1Password/Bitwarden/
  Vault accounts; `age` and `gpg` are the only two with a real
  encrypt→resolve round trip in the test suite.
- **`OllamaClient` (the real AI path) is unit-tested, but never against a
  live Ollama server.** `tests/test_ollama_client.py` mocks the HTTP transport
  (`urllib.request.urlopen`) and covers the happy path, request construction,
  the unreachable-host / timeout / non-JSON / empty-response failure modes, and
  `get_client` dispatch. What is *not* covered is a real round trip against a
  running Ollama instance — that still requires you to stand one up. If you're
  touching `agent_vault/compiler.py`'s `OllamaClient`
  (`agent_vault/compiler.py:530`), extend the mocked-transport tests alongside
  your change.
- **The pattern library covers ~104 billers** (banks, brokerages, card
  issuers, P&C + health insurers, telecoms, utilities, streaming, subscriptions,
  retailers, pharmacies, travel) and **25 document shapes** (statements, bills,
  warranties, paystubs, W2/1099, mortgage, HOA, medical EOB, lab results, lease,
  invoice, vehicle registration, …) in `registry/patterns.yaml`. Still expect to
  add niche/regional vendors — a few lines of YAML;
  `agent_vault/validate.py`'s `validate_patterns()` (run by `python -m
  agent_vault.validate .` and exercised via `tests/test_validate.py`) guards
  that every `default_tag` exists in the schema and every shape maps to a
  real subtype.
- **No real data ships here.** The 3 entities under `entities/` are the only
  sample content; there is no separate synthetic fixture corpus — the test
  suite sandbox-copies this same sample data into a `tmp_path` per test.

### Operational hardening (production posture)

- **Concurrency:** every mutating entry point (ingest / compile / promote /
  reclassify / collections-import) takes one vault-wide advisory lock
  (`locking.py`), so overlapping cadence runs serialize instead of corrupting
  shared state (single writer host; `flock` is per-host).
- **Index freshness (O7):** every mutating pass calls `build_index.reindex()`
  (JSON + MD + FTS sidecar), so readers never serve a stale index after a write;
  `GET /api/status` reports an `index` freshness block (`stale` flag) that flags
  the direct-file-edit edge that bypassed the pipeline.
- **Auth & audit:** the HTTP service supports per-agent tokens with
  `read`/`write`/`resolve` scopes (`VAULT_TOKENS` / `registry/tokens.yaml`), and
  a pure-ASGI middleware records every write/resolve to `discovery/_access.jsonl`
  (actor + method + path + status; never bodies or secrets).
- **Backup & migration:** `agent-vault-backup export/restore` snapshots the
  authored state (safe restore, no traversal, reindexes); `validate.py` gates the
  `schema.yaml` version and `agent-vault-migrate` is the upgrade runner.
- **Resilience:** a poison file is isolated (recorded as a manifest `error`, run
  continues); oversized files are skipped (`AGENT_VAULT_MAX_RAW_MB`, default 50);
  docx/xlsx have a decompression-bomb guard. `lint`'s `ingest_errors` check
  surfaces anything skipped.
- **Durability:** all vault writes are `tmp + fsync + os.replace` (atomic, and
  survive power loss before the rename).
- **Observability:** each cadence — and each web-triggered job — appends a JSON
  run record to `discovery/_runs.jsonl` (cadence/op, ts, rc, duration), surfaced
  by `GET /api/runs` and the dashboard's `last_run`.
- **Log growth:** the append-only discovery logs are bounded on demand by
  `agent-vault compact --apply` (or `python -m agent_vault.compact . --apply`),
  which dedups `proposals.jsonl`/`promoted.jsonl` **without changing any
  promotion outcome** (writes `.bak` backups first). Run it quarterly or wire
  it into a cadence.
- **Packaging:** dependencies and the five console scripts (`agent-vault`,
  `-serve`, `-mcp`, `-backup`, `-migrate`) are declared in `pyproject.toml` (no
  separate `requirements.txt`; MCP is the optional `[mcp]` extra). `pytest` runs
  the backend suite; `web/` has `npm run lint` (ESLint) + `vitest` + `build`.
  CI (`.github/workflows/ci.yml`) gates both. Python 3.11+.

---

## 9. Glossary

- **Entity** — one Markdown page representing one real-world thing (an account, an
  appliance, a document).
- **Stub** — a freshly ingested entity: facts filled in, prose empty, awaiting
  compile.
- **Compile** — the single LLM step that writes an entity's prose body.
- **Promote** — the deterministic step that graduates proposed vocabulary
  (tags/aliases/subtypes) into the registry.
- **Registry** — the canonical vocabulary under `registry/` (types, patterns,
  aliases).
- **Proposal** — a structured suggestion the LLM appends to
  `discovery/proposals.jsonl`; never applied directly.
- **Sighting** — one occurrence of a proposed concept; promotion is threshold-
  based on counts.
- **Cadence** — a scheduled wrapper script (daily/weekly/monthly).
- **Credential reference** — a `scheme://store/path` URI pointing at where a
  secret lives; the secret itself is never stored in the vault.

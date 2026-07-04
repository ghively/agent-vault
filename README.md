# Agent Vault

A **standalone** knowledge-wiki service for managing household information. Stores
documents, credentials, and structured data as files with a deterministic pipeline
(intake → classify → compile → promote). Can be run as a CLI, an HTTP service,
or with a React UI.

**New here?** Read [`DOCS.md`](./DOCS.md) — plain-language overview, an honest
real-vs-scaffolding status table, the architecture, a full script reference, and
setup/usage instructions. For the HTTP API surface, see [`docs/API.md`](./docs/API.md);
for the web frontend's architecture, see [`web/README.md`](./web/README.md).

## What it is

- **Package**: Install via `pip install -e .` — provides the `agent-vault` CLI
- **Service**: Run `agent-vault-serve` to start the FastAPI HTTP service (credential resolution)
- **UI**: Run `cd web && npm install && npm run dev` for the React frontend
- **MCP**: Install `pip install -e ".[mcp]"` and run `agent-vault-mcp` to expose
  the vault to AI agents over the Model Context Protocol (search / get / list /
  status / submit-source / resolve-credential tools)

The vault is now a **standalone product** (severed from SynapseNAS). It can be used
independently, consumed by other services over HTTP, or plugged into a fleet of
agents over MCP as a shared, deterministic knowledgebase.

### Retrieval: ranked, prose-aware search

`agent-vault find <text>` and `GET /api/search?q=` run **ranked full-text search
that reads inside the compiled prose bodies** (SQLite FTS5, bm25), not just a
metadata substring match — so "forced-air heating" finds the furnace page even
though that phrase appears only in its prose. Deterministic and rebuildable
(`_index.db`, git-ignored); degrades to a metadata scan where FTS5 is
unavailable. No model involved — this is read-side ranking, not generation.

## Install

```bash
git clone <repo-url>
cd agent-vault
pip install -e .          # installs the CLI and service
```

## Usage

### CLI (retrieval and pipeline)

```bash
agent-vault <command> [args]      # run from your vault dir, or set AGENT_VAULT_PATH
```

Commands: `find`, `show`, `due`, `expiring`, `creds`, `resolve`, `list`, `compact`
(see `agent-vault --help`). The vault root (entities, registry, …) is read from
`AGENT_VAULT_PATH`, defaulting to the current directory.

### HTTP Service

```bash
agent-vault-serve          # starts the FastAPI service
# Environment:
#   AGENT_VAULT_HOST (default: 127.0.0.1)
#   AGENT_VAULT_PORT (default: 7778)
#   AGENT_VAULT_PATH (default: ./)
#   VAULT_TOKEN (optional bearer token for auth)
```

The service exposes 27 routes across entities, credentials, review, jobs,
run history, status/ask, config, and settings — see [`docs/API.md`](./docs/API.md)
for the full reference, including the SSE job-streaming mechanism the web UI uses to run
pipeline stages from the browser. `/api/health` (liveness) and
`/api/creds/{slug}/resolve` (credential resolution) are the two simplest.
Interactive docs are also auto-served at `/docs` (Swagger UI) once the
service is running.

### Web UI

```bash
cd web
npm install
npm run dev               # Vite dev server at http://localhost:5173
# Environment:
#   VITE_VAULT_API_URL (default: http://127.0.0.1:7778)
```

The React UI is a window-manager-style desktop with 8 apps (Browse, Wiki,
Vault, Credentials, Review, Pipeline, Command Deck, Settings) — see
[`web/README.md`](./web/README.md) for the architecture. All eight apps are
wired to live endpoints (Command Deck runs off `GET /api/ask` + `GET /api/status`);
the only remaining stubs are two cosmetic QuickPanel glyphs (power/restart)
carried over from the shell it was ported from.

## Current state: Stages 0–6 complete (all follow-ups closed)

**Built (Stage 0):**
- Full directory tree (`registry/`, `raw/`, `entities/<12 types>/`, `discovery/`)
- `registry/schema.yaml` — the 12-type taxonomy, subtypes, page templates, gating flags, promotion thresholds
- `registry/aliases.yaml` — surface-form → slug map
- `registry/resolvers.yaml` — pluggable credential backends (`age` seeded as default)
- `registry/_entity-template.md` — the three-region entity page template
- 3 hand-written sample entities (asset/furnace, account/bofa-checking, document/warranty)
- `validate.py` — schema validator + smoke-test

**Built (Stage 1) — retrieval, pure Python, NO LLM:**
- `build_index.py` — walks entities → `_index.json` (+ human `_index.md`)
- `synapse.py` — the CLI: `find`, `show`, `due`, `expiring`, `creds`, `list`

**Verified:** schema validates (exit 0); index builds; Agent Vault answers `find furnace`,
`expiring --days N`, `show bofa` (via alias), `creds bofa` (reference only, secret never stored).
The schema is queryable — every test question answered with no awkward workarounds.

**Built (Stage 2) — ingestion, pure Python, NO LLM:**
- `ingest.py` — walks `raw/`, hashes for idempotency via `raw/_manifest.jsonl`,
  detects type (python-magic + extension), extracts text (pdfplumber, email, PIL/EXIF),
  classifies via `registry/patterns.yaml` (biller patterns × shape patterns × file-type
  priors), extracts dates/amounts/IDs, writes entity stubs (frontmatter + link block,
  empty prose), refreshes `_index.json`.
- `registry/patterns.yaml` — classifier vocabulary: ~104 billers (banks, brokerages,
  card issuers, insurers, telecoms, utilities, streaming, subscriptions, retailers)
  + ~25 shapes + file-type priors. Same anti-rot rule as schema/aliases: promotion
  step writes; the LLM never does. `validate.py`'s `validate_patterns` pass guards
  integrity (every `default_tag` exists in the schema; shape types map to real
  schema subtypes; ids unique).
- Email fan-out: an `.eml` with attachments emits one stub per logical subject (the
  email + each attachment as `path#fragment`), each classified independently.
- `tests/test_ingest.py` — regression tests for the ingest writers and extractors
  (YAML round-trip safety for ambiguous scalars, HTML-only email bodies, corrupt-PDF
  degradation, duplicate-content manifest records).

**Verified (Stage 2, historical):** during pre-standalone development, a 25-file
synthetic fixture corpus produced 27 stubs (2 email attachments fan out), 20/27
confident (≥0.75), 5/27 needs-review, 2/27 unknown; re-ingest was a no-op and all
30 entities passed `validate.py`. That fixture corpus did not survive the split
from SynapseNAS and does not ship in this repo (see `DOCS.md` §8).

**Built (Stage 3) — compilation, the ONE LLM touchpoint:**
- `compiler.py` — versioned prompt contract (`PROMPT_CONTRACT_VERSION = 2.0`),
  swappable client interface, driver that finds `status: stub` entities AND
  any compiled entity whose `sources_hash` has drifted from
  `compiled_from_hash`. Writes prose only. Sanitizes any frontmatter or
  LINKS markers the model tries to emit. Appends structured proposals to
  `discovery/proposals.jsonl` with model identity + contract version stamped
  for audit.
- Clients: `OllamaClient` (default; POSTs to `OLLAMA_HOST`, model
  `OLLAMA_MODEL=qwen2.5:7b-instruct` by default) and `MockClient`
  (deterministic, offline, template-only — emits only frontmatter-grounded
  prose with `[NEEDS SOURCE:]` markers, used for tests and CI).
- Selector: `AGENT_VAULT_COMPILER=ollama|mock` (defaults to `ollama`).
- Token efficiency (contract 1.1): source text is deterministically de-noised
  (blank/dup/blob/quote-chain/signature stripping — never a line carrying an
  extracted fact) and budgeted across sources (`AGENT_VAULT_PER_SOURCE_CHARS`=4000,
  `AGENT_VAULT_TOTAL_SOURCE_CHARS`=12000) so a many-source entity can't blow the
  context window. The compile summary logs a ~token estimate per entity and in
  aggregate. The LLM still sees all real content — scripts only trim noise.
- Seed entities are pre-stamped with `compiled_from_hash` so the very first
  compile run doesn't clobber their hand-authored prose. Once ingestion
  changes their sources, the drift is detected and a recompile runs — that
  is the spec's intent (richer sources warrant fresh prose).

**Verified (Stage 3):** in the sandbox test, all 20 stubs flip to compiled
in one mock-compile pass; re-compile is a no-op; bumping one entity's
`sources_hash` triggers exactly one targeted recompile; link blocks remain
byte-identical pre/post; the only frontmatter line-stem allowed to change
is `compiled_from_hash`; needs-review and unknown entities are skipped;
30/30 still validate after the pass.

**Built (Stage 4) — promotion, the deterministic learning loop:**
- `promote.py` — drains `discovery/proposals.jsonl`, normalizes each proposal
  through the alias map (so `BoA`/`bofa`/`Bank of America` collapse to one
  identity before counting sightings), aggregates by concept, then decides
  per the thresholds already in `registry/schema.yaml`'s `promotion:` block.
- Five decision paths: `auto_promote` (writes the registry), `queue_for_review`
  (human-gated: new top-level types, low-confidence aliases, missing-target
  aliases, all reclassifications), `deferred_below_threshold` (e.g. tag at
  fewer than 3 sightings — wait for more), `rejected_duplicate` (already in
  the registry from a previous pass or hand-seed).
- Registry edits are targeted text inserts: a new tag lands in the `tags:`
  block, a new subtype joins its parent's `subtypes:` list, a new alias
  appends to `aliases.yaml`. Comments in `schema.yaml` are preserved.
- Audit log: every decision (auto, queue, defer, duplicate) is appended to
  `discovery/promoted.jsonl` with model identity, contract version, sightings,
  max confidence, evidence count, and reason. Nothing is lost.
- `--dry-run` shows what would happen without writing.

**Verified (Stage 4):** in the sandbox test, 15 fabricated proposals (mix of
tags, aliases, subtypes, types, reclassifies) collapse to 10 distinct
concepts after normalization; 3 auto-promote (tag at 3 sightings, subtype
under existing parent, high-confidence alias whose target exists), 4 queue
(low-conf alias, missing-target alias, new top-level type, reclassify), 1
defers (tag at 1 sighting), 2 rejected as duplicates of already-promoted
seed vocabulary. Re-running the pass makes zero further registry edits.
`validate.py` continues to pass on the resulting tree.

**Built (Stage 5) — cadences, runner-agnostic:**
- `cadences/daily.sh` — ingest + validate. Cheap, idempotent. Run hourly
  if you like.
- `cadences/weekly.sh` — ingest + compile + promote + validate. The LLM
  cadence (one Ollama call per stub-or-drifted entity). Drains discoveries
  into the registry.
- `cadences/monthly.sh` — validate + lint (read-only audit). Exits non-zero
  on findings so the runner can wake a human.
- `lint.py` — read-only anomaly report. Nine checks: broken `related:`
  refs, stub-with-prose / compiled-without-prose mismatches, compile drift
  (sources_hash diverged from compiled_from_hash), aging needs-review
  entities, aging queued proposals, stuck reclassifies, raw files not yet
  ingested, manifest records pointing at deleted stubs, and ingest errors
  (poison/oversized files ingest skipped). Writes nothing back to the vault;
  emits a JSON report to `discovery/_lint_report.json` and a human summary
  to stdout (silent when clean — cron-friendly).

The three scripts are POSIX `sh`, take an optional vault dir as `$1`, and
default to the parent directory of the script. Pick any runner (cron,
systemd timer, Hermes trigger, n8n workflow, Makefile, manual shell): the
file contract is what stays put.

**Verified (Stage 5, historical):** during pre-standalone development, a
cadences smoke test ran all three cadences cleanly in order against a
fresh sandbox (daily → 27 stubs, weekly → 22 compiled via mock client,
monthly → lint report; each injected anomaly type surfaced by lint).
That smoke test lived in the SynapseNAS tree and does not ship here —
the cadence scripts are currently exercised manually.

**Built (Stage 6) — collections importer for catalog media:**
- `collections_importer.py` — turns library exports (Steam, Goodreads,
  IMDB, generic CSV/JSON) into `entities/collection/<slug>.md` files.
  Bookkeeping records, not narrative ones: written with `status: compiled`
  and matching hashes so the LLM compile pass leaves them alone.
- Format detection is by header sniff: presence of `appid` → Steam,
  `ISBN` → Goodreads, `Title Type` → IMDB, `subtype` column → generic.
- Idempotent: re-importing the same export is a no-op (slug-level merge,
  identifier-aware collision handling).
- `cadences/import_collections.sh` — wrapper for manual invocation when
  you drop a new export file in `raw/collections/`. Not on a schedule.

**Verified (Stage 6):** 4 export files (12 total rows: 4 Steam games,
3 Goodreads books, 1 movie + 2 TV shows from IMDB, 1 music + 1 comic
from generic JSON) all import to the correct subtypes. Re-running
imports zero new rows. Unrecognized formats are flagged, not crashed.
`validate.py` passes on the resulting tree.

**Built (Follow-up A) — auto-stub missing related targets in ingest:**
- After the main ingestion loop, `ingest.py` walks every entity to
  collect emitted `related:` refs. Any ref pointing at a type/slug
  that has no entity file gets a minimal `needs-review` stub with
  `confidence: 0.40`, `sources: []`, `inferred: true`, and back-refs
  to the entities that triggered its creation.
- Subtype defaults are intelligent: `vendor/comcast` infers
  `subtype: utility`; `financial-institution/fidelity` infers
  `subtype: brokerage`; `account/chase-mortgage` infers `subtype: loan`.
  Slug-substring heuristics drive it.
- Result: on a fresh vault, the lint pass no longer reports 27 broken
  refs from biller patterns pointing at uncreated vendors/institutions.
  It now reports 0 (those entities exist as low-confidence stubs the
  human can refine or merge).

**Built (Follow-up B) — `reclassify_apply.py`:**
- Stage 4 queues every reclassify proposal for human review (touches
  entity files, not the registry). This is the deliberate apply step:
  drains queued reclassifies, moves the entity file across type dirs
  via `os.replace()`, rewrites `type:`/`subtype:` in its frontmatter,
  finds every OTHER entity with a `related:` ref pointing at the old
  `type/slug` and rewrites it (plus its LINKS block) to the new one.
- Append-only audit: each apply logs a `reclassify_applied` record to
  `discovery/promoted.jsonl`, so re-running is a no-op.
- `--dry-run` to preview the plan. `--slug X` to apply only one.

**Verified (Follow-ups A+B, historical):** the pre-standalone smoke test
reported 18 inferred entities created on a fresh vault, eliminating all
biller-pattern broken refs, and exercised both a same-type (subtype-only)
reclassify and a cross-type move with cross-reference rewriting
(re-running a no-op, `validate.py` passing). As above, that harness does
not ship in this repo.

**Built (Follow-up C) — the human review loop (`review.py`):**
- The spec's missing approve/reject mechanism. Two queues, one tool:
  queued registry proposals (`list` / `show` / `approve` / `reject`) and
  needs-review entities (`entities` / `approve-entity` / `reject-entity`).
  Approvals reuse promote.py's deterministic registry writers — a human
  DECIDES, code writes — and every decision is an append-only
  `review_approved` / `review_rejected` / `entity_approved` /
  `entity_rejected` record in `discovery/promoted.jsonl`.
- Rejections stand until NEW evidence arrives: promote re-queues a rejected
  concept only when its distinct-entity sighting count grows. A rejected
  reclassify also blocks `reclassify_apply.py` until re-queued.
- **`new_type` proposal path (contract 1.3):** the compile contract now
  offers `{"kind": "type", ...}` — the spec's self-expansion story. Always
  human-gated (`new_type: auto: false`); approving writes a minimal valid
  type block into `schema.yaml` (refine description/subtypes by hand) and
  creates its `entities/<type>/` dir.
- **`human_gated` enforcement:** ingest now downgrades any confident stub of
  a `human_gated: true` type (financial-institution, person, property) to
  `needs-review` — the compile pass skips it until a human approves it via
  `review.py approve-entity` (flips to `stub`; a page that already carries
  prose flips to `compiled` with hashes stamped so the prose survives).

**Verified (Follow-up C):** a fabricated type proposal queues, approves into
a validating schema, and promote re-runs are ledger-silent; rejected alias +
reclassify proposals stay rejected at the same sighting count, are skipped
by a bare `reclassify_apply` run, and re-queue when a second entity sights
them; a 0.95-confidence gated stub lands as needs-review, approve-entity
flips it, and human prose is preserved byte-for-byte on approval.

## Architecture

The vault is a **three-tier standalone product**:

1. **CLI tier** (`agent_vault/`) — Python modules for ingestion, classification,
   compilation, and retrieval. All deterministic except the prose-generation step.
   Only `synapse.py` (retrieval) and `serve.py` have console-script entry points
   (`agent-vault`, `agent-vault-serve`); every other pipeline module is run via
   `python -m agent_vault.<module>` — see [`docs/API.md`](./docs/API.md) and
   the script reference in [`DOCS.md`](./DOCS.md#5-script-reference) for exact
   invocations.
2. **Service tier** (`agent_vault/api/`) — FastAPI HTTP service exposing 27
   routes (entities, credentials, review queue, pipeline jobs, run history,
   status/ask, config, settings) — see [`docs/API.md`](./docs/API.md) for the full reference.
3. **UI tier** (`web/`) — React/TypeScript window-manager-style desktop — see
   [`web/README.md`](./web/README.md) for the architecture.

```
agent-vault/                    # Repo root == the vault root (registry/, raw/, entities/ live here)
├── agent_vault/                # Python package (CLI + service)
│   ├── api/                    # FastAPI service — routers are flat files, no routes/ subfolder
│   │   ├── app.py              # Service factory (mounts routers + serves web/dist)
│   │   ├── config.py           # Settings (env vars: AGENT_VAULT_HOST/PORT/PATH, VAULT_TOKEN)
│   │   ├── auth.py             # Bearer-token dependency
│   │   ├── reads.py, creds.py, review.py, jobs.py, history.py, settings.py
│   ├── *.py                    # Pipeline modules (ingest, compiler, promote, ...) — no console entry, use `python -m agent_vault.<name>`
│   └── resolvers/               # Credential backends (9 modules)
├── web/                        # React UI (Vite + TypeScript)
│   ├── src/                    # Components
│   └── package.json
├── cadences/                   # Scheduled scripts
└── tests/                      # Test suite
```

## Daily/typical commands

Run from the vault directory (or set `AGENT_VAULT_PATH`), after
`pip install -e .`:

```
python -m agent_vault.build_index .   # rebuild index after any entity change
agent-vault find <text>               # fuzzy search
agent-vault show <slug>               # full record (alias-resolved)
agent-vault expiring                  # warranties/registrations/licenses lapsing soon
agent-vault due                       # tasks/bills due soon
agent-vault creds <slug>              # credential REFERENCE (never the secret)
agent-vault list [type]               # everything, or one type
```

`agent-vault` is the installed console script (`agent_vault.synapse:main`) —
it only covers retrieval/maintenance (`find`/`show`/`due`/`expiring`/`creds`/
`resolve`/`list`/`compact`). `build_index` and every pipeline stage below have
no console entry point and are invoked as `python -m agent_vault.<module>`.

## Layout

```
agent-vault/
├── registry/
│   ├── schema.yaml          ← canonical vocabulary. PROMOTION STEP writes only.
│   ├── aliases.yaml         ← surface form → slug. PROMOTION STEP writes only.
│   ├── patterns.yaml        ← classifier vocabulary (billers + shapes). HUMAN + PROMOTION writes.
│   ├── field_mappings.yaml  ← learned field-extraction regexes. PROMOTION STEP writes only.
│   ├── resolvers.yaml       ← credential scheme → backend. HUMAN writes.
│   └── _entity-template.md  ← page template (3 ownership-fenced regions)
├── raw/                     ← immutable source documents. APPEND-ONLY. (system boundary)
├── entities/<type>/         ← the wiki proper, one file per entity
└── discovery/               ← LLM proposals.jsonl (Stage 4). APPEND-ONLY.
```

(`validate.py` isn't a vault-dir file — it's `agent_vault/validate.py` in the
package, run as `python -m agent_vault.validate <vault-dir>`.)

## Wiring it up

Pick a runner (cron / systemd timer / Hermes trigger / n8n / Makefile),
call the cadence scripts:

```cron
# crontab example
@hourly  /path/to/agent-vault/cadences/daily.sh   /path/to/agent-vault
@weekly  /path/to/agent-vault/cadences/weekly.sh  /path/to/agent-vault
@monthly /path/to/agent-vault/cadences/monthly.sh /path/to/agent-vault
# import_collections.sh is manual — run it when you drop a new export.
```

```ini
# systemd timer example (agentvault-daily.timer)
[Timer]
OnCalendar=daily
Persistent=true
[Install]
WantedBy=timers.target
```

The runner is the swap edge. Nothing in the file contract depends on which
one calls these scripts; the data layer is identical no matter what.

## Manual flow (when not using cadences)

```
python -m agent_vault.ingest .                       # walk raw/, write stubs; auto-stub missing refs
python -m agent_vault.compiler .                     # the one LLM touchpoint; writes prose only
python -m agent_vault.promote .                      # drain discoveries into the registry
python -m agent_vault.review . list                  # human queue: approve/reject proposals
python -m agent_vault.review . entities               # human queue: needs-review entities
python -m agent_vault.reclassify_apply .              # apply queued reclassify proposals (manual)
python -m agent_vault.collections_importer . \        # bookkeeping import for catalog media
    --source raw/collections/
python -m agent_vault.validate .                      # exit 0 means every entity passes the schema
python -m agent_vault.lint .                          # operational anomaly report (read-only)
agent-vault find <text>                               # search the vault (console script)
```

Each script never shares state in process with another — the contract is
the files on disk. Run them in any order, from any harness; the data layer
is identical.

## What's left

Nothing structural. All six stages and all three follow-ups (auto-stubbing,
reclassify apply, the human review loop) are shipped and tested. Future
work, if it shows up, is *content* (more biller patterns, more import
formats, more lint checks) rather than architecture — the file contract is
what stays put.

## Pattern library

`registry/patterns.yaml` is the classifier's vocabulary. Add a new biller when you
see a vendor your file collection cares about — same shape as the seeded entries:

```yaml
- id: kroger
  matches: ["kroger", "kroger.com"]
  vendor_slug: kroger
  confidence: 0.92
```

## Rules that never bend (from the spec)
1. One author per file region (Python: frontmatter+links; LLM: prose only; promotion: registry).
2. `raw/` is append-only.
3. The LLM proposes; deterministic code commits. No non-deterministic write to the vocabulary.
4. Secrets are referenced (`scheme://store/path`), never stored plaintext.

## The learning loop

The vault learns at **three levels**, all through the same propose →
deterministic-validate → promote gate:

1. **Taxonomy** — new types, subtypes, tags, and aliases → `schema.yaml` / `aliases.yaml`.
2. **Detection** — new billers and shapes → `patterns.yaml` (hand-written entries are the
   trusted base; promote-appended entries are evidence-backed + audited).
3. **Field extraction** — learned regex mappings → `field_mappings.yaml` (promote-only writer).

### Learned field-mapping safety

A promoted field mapping runs against every future ingest, so a bad regex
(catastrophic backtracking, a capture that never fires, or one that captures a
secret) silently corrupts intake. That's why `new_field_mapping` is **always
human-gated** and must pass a **deterministic validation gate** before promotion:
compiles, bounded (≤ 200 chars / ≤ 2 groups), ReDoS-safe (no nested quantifier),
matches its evidence, parses per its declared type, not secret-shaped, valid
slugs. `review.py` re-runs this gate at approval time — a human click can never
bypass it. Field extraction is strictly additive to built-ins and secret-scanned
at ingest. The core invariant is unchanged: the LLM only appends proposals;
`promote.py` is the sole registry writer.

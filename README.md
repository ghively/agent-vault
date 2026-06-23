# Agent Vault

A harness-independent LLM wiki for managing a household. The contract is the files;
everything around them (intake, runner, compile model, secret backend) is swappable.

See `llm-wiki-schema-spec.md` (the constitution â€” file format & ownership rules) and
`llm-wiki-build-guide.md` (the taxonomy & full build order) for the design.

**New here?** Read [`DOCS.md`](./DOCS.md) â€” plain-language overview, an honest
real-vs-scaffolding status table, the architecture, a full script reference, and
setup/usage instructions.

## Current state: Stages 0â€“6 complete (all follow-ups closed)

**Built (Stage 0):**
- Full directory tree (`registry/`, `raw/`, `entities/<12 types>/`, `discovery/`)
- `registry/schema.yaml` â€” the 12-type taxonomy, subtypes, page templates, gating flags, promotion thresholds
- `registry/aliases.yaml` â€” surface-form â†’ slug map
- `registry/resolvers.yaml` â€” pluggable credential backends (`age` seeded as default)
- `registry/_entity-template.md` â€” the three-region entity page template
- 3 hand-written sample entities (asset/furnace, account/bofa-checking, document/warranty)
- `validate.py` â€” schema validator + smoke-test

**Built (Stage 1) â€” retrieval, pure Python, NO LLM:**
- `build_index.py` â€” walks entities â†’ `_index.json` (+ human `_index.md`)
- `synapse.py` â€” the CLI: `find`, `show`, `due`, `expiring`, `creds`, `list`

**Verified:** schema validates (exit 0); index builds; Agent Vault answers `find furnace`,
`expiring --days N`, `show bofa` (via alias), `creds bofa` (reference only, secret never stored).
The schema is queryable â€” every test question answered with no awkward workarounds.

**Built (Stage 2) â€” ingestion, pure Python, NO LLM:**
- `ingest.py` â€” walks `raw/`, hashes for idempotency via `raw/_manifest.jsonl`,
  detects type (python-magic + extension), extracts text (pdfplumber, email, PIL/EXIF),
  classifies via `registry/patterns.yaml` (biller patterns Ã— shape patterns Ã— file-type
  priors), extracts dates/amounts/IDs, writes entity stubs (frontmatter + link block,
  empty prose), refreshes `_index.json`.
- `registry/patterns.yaml` â€” classifier vocabulary: ~64 billers (banks, brokerages,
  card issuers, insurers, telecoms, utilities, streaming, subscriptions, retailers)
  + 12 shapes + file-type priors. Same anti-rot rule as schema/aliases: promotion
  step writes; the LLM never does. `tests/test_patterns.py` guards integrity
  (every `default_tag` exists in the schema; cross-link slugs valid; ids unique).
- Email fan-out: an `.eml` with attachments emits one stub per logical subject (the
  email + each attachment as `path#fragment`), each classified independently.
- `tests/generate_fixtures.py` â€” deterministic synthetic fixtures (~25 files: bank
  statements, utility bills, warranties, order-confirmation emails, EXIF-tagged photos).
- `tests/test_ingest.py` â€” sandbox driver: copies registry + fixtures into a tempdir,
  runs ingest twice, validates everything, runs Agent Vault against the result.

**Verified (Stage 2):** 25 fixtures â†’ 27 stubs (2 email attachments fan out).
20/27 confident (â‰¥0.75) â€” banks, utilities, insurance, warranties, taxes, orders, one image-as-document.
5/27 needs-review (photos at EXIF-only confidence, the personal "mom" email, a CSV mentioning BofA).
2/27 unknown (a `.txt` note + a random binary blob). Re-ingest is a no-op.
All 30 entities (3 seed + 27 ingested) pass `validate.py`.

**Built (Stage 3) â€” compilation, the ONE LLM touchpoint:**
- `compiler.py` â€” versioned prompt contract (`PROMPT_CONTRACT_VERSION = 1.3`),
  swappable client interface, driver that finds `status: stub` entities AND
  any compiled entity whose `sources_hash` has drifted from
  `compiled_from_hash`. Writes prose only. Sanitizes any frontmatter or
  LINKS markers the model tries to emit. Appends structured proposals to
  `discovery/proposals.jsonl` with model identity + contract version stamped
  for audit.
- Clients: `OllamaClient` (default; POSTs to `OLLAMA_HOST`, model
  `OLLAMA_MODEL=qwen2.5:7b-instruct` by default) and `MockClient`
  (deterministic, offline, template-only â€” emits only frontmatter-grounded
  prose with `[NEEDS SOURCE:]` markers, used for tests and CI).
- Selector: `AGENT_VAULT_COMPILER=ollama|mock` (defaults to `ollama`).
- Token efficiency (contract 1.1): source text is deterministically de-noised
  (blank/dup/blob/quote-chain/signature stripping â€” never a line carrying an
  extracted fact) and budgeted across sources (`AGENT_VAULT_PER_SOURCE_CHARS`=4000,
  `AGENT_VAULT_TOTAL_SOURCE_CHARS`=12000) so a many-source entity can't blow the
  context window. The compile summary logs a ~token estimate per entity and in
  aggregate. The LLM still sees all real content â€” scripts only trim noise.
- Seed entities are pre-stamped with `compiled_from_hash` so the very first
  compile run doesn't clobber their hand-authored prose. Once ingestion
  changes their sources, the drift is detected and a recompile runs â€” that
  is the spec's intent (richer sources warrant fresh prose).

**Verified (Stage 3):** in the sandbox test, all 20 stubs flip to compiled
in one mock-compile pass; re-compile is a no-op; bumping one entity's
`sources_hash` triggers exactly one targeted recompile; link blocks remain
byte-identical pre/post; the only frontmatter line-stem allowed to change
is `compiled_from_hash`; needs-review and unknown entities are skipped;
30/30 still validate after the pass.

**Built (Stage 4) â€” promotion, the deterministic learning loop:**
- `promote.py` â€” drains `discovery/proposals.jsonl`, normalizes each proposal
  through the alias map (so `BoA`/`bofa`/`Bank of America` collapse to one
  identity before counting sightings), aggregates by concept, then decides
  per the thresholds already in `registry/schema.yaml`'s `promotion:` block.
- Five decision paths: `auto_promote` (writes the registry), `queue_for_review`
  (human-gated: new top-level types, low-confidence aliases, missing-target
  aliases, all reclassifications), `deferred_below_threshold` (e.g. tag at
  fewer than 3 sightings â€” wait for more), `rejected_duplicate` (already in
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

**Built (Stage 5) â€” cadences, runner-agnostic:**
- `cadences/daily.sh` â€” ingest + validate. Cheap, idempotent. Run hourly
  if you like.
- `cadences/weekly.sh` â€” ingest + compile + promote + validate. The LLM
  cadence (one Ollama call per stub-or-drifted entity). Drains discoveries
  into the registry.
- `cadences/monthly.sh` â€” validate + lint (read-only audit). Exits non-zero
  on findings so the runner can wake a human.
- `lint.py` â€” read-only anomaly report. Nine checks: broken `related:`
  refs, stub-with-prose / compiled-without-prose mismatches, compile drift
  (sources_hash diverged from compiled_from_hash), aging needs-review
  entities, aging queued proposals, stuck reclassifies, raw files not yet
  ingested, manifest records pointing at deleted stubs, and ingest errors
  (poison/oversized files ingest skipped). Writes nothing back to the vault;
  emits a JSON report to `discovery/_lint_report.json` and a human summary
  to stdout (silent when clean â€” cron-friendly).

The three scripts are POSIX `sh`, take an optional vault dir as `$1`, and
default to the parent directory of the script. Pick any runner (cron,
systemd timer, Hermes trigger, n8n workflow, Makefile, manual shell): the
file contract is what stays put.

**Verified (Stage 5):** in the smoke test, all three cadences run cleanly
in order against a fresh sandbox: daily produces 27 stubs from 25 raw
files, weekly compiles 22 of them (mock client) and runs an empty
promote pass, monthly writes its report. In a separate phase the test
intentionally injects each of the seven anomaly types and confirms
lint surfaces every one.

**Built (Stage 6) â€” collections importer for catalog media:**
- `collections_importer.py` â€” turns library exports (Steam, Goodreads,
  IMDB, generic CSV/JSON) into `entities/collection/<slug>.md` files.
  Bookkeeping records, not narrative ones: written with `status: compiled`
  and matching hashes so the LLM compile pass leaves them alone.
- Format detection is by header sniff: presence of `appid` â†’ Steam,
  `ISBN` â†’ Goodreads, `Title Type` â†’ IMDB, `subtype` column â†’ generic.
- Idempotent: re-importing the same export is a no-op (slug-level merge,
  identifier-aware collision handling).
- `cadences/import_collections.sh` â€” wrapper for manual invocation when
  you drop a new export file in `raw/collections/`. Not on a schedule.

**Verified (Stage 6):** 4 export files (12 total rows: 4 Steam games,
3 Goodreads books, 1 movie + 2 TV shows from IMDB, 1 music + 1 comic
from generic JSON) all import to the correct subtypes. Re-running
imports zero new rows. Unrecognized formats are flagged, not crashed.
`validate.py` passes on the resulting tree.

**Built (Follow-up A) â€” auto-stub missing related targets in ingest:**
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

**Built (Follow-up B) â€” `reclassify_apply.py`:**
- Stage 4 queues every reclassify proposal for human review (touches
  entity files, not the registry). This is the deliberate apply step:
  drains queued reclassifies, moves the entity file across type dirs
  via `os.replace()`, rewrites `type:`/`subtype:` in its frontmatter,
  finds every OTHER entity with a `related:` ref pointing at the old
  `type/slug` and rewrites it (plus its LINKS block) to the new one.
- Append-only audit: each apply logs a `reclassify_applied` record to
  `discovery/promoted.jsonl`, so re-running is a no-op.
- `--dry-run` to preview the plan. `--slug X` to apply only one.

**Verified (Follow-ups A+B):** the cadences smoke test now reports
18 inferred entities created on a fresh vault, eliminating all biller-
pattern broken refs. The reclassify test exercises both a same-type
(subtype-only) change and a cross-type move with cross-reference
rewriting; re-running is a no-op; `validate.py` passes on the
resulting tree.

**Built (Follow-up C) â€” the human review loop (`review.py`):**
- The spec's missing approve/reject mechanism. Two queues, one tool:
  queued registry proposals (`list` / `show` / `approve` / `reject`) and
  needs-review entities (`entities` / `approve-entity` / `reject-entity`).
  Approvals reuse promote.py's deterministic registry writers â€” a human
  DECIDES, code writes â€” and every decision is an append-only
  `review_approved` / `review_rejected` / `entity_approved` /
  `entity_rejected` record in `discovery/promoted.jsonl`.
- Rejections stand until NEW evidence arrives: promote re-queues a rejected
  concept only when its distinct-entity sighting count grows. A rejected
  reclassify also blocks `reclassify_apply.py` until re-queued.
- **`new_type` proposal path (contract 1.3):** the compile contract now
  offers `{"kind": "type", ...}` â€” the spec's self-expansion story. Always
  human-gated (`new_type: auto: false`); approving writes a minimal valid
  type block into `schema.yaml` (refine description/subtypes by hand) and
  creates its `entities/<type>/` dir.
- **`human_gated` enforcement:** ingest now downgrades any confident stub of
  a `human_gated: true` type (financial-institution, person, property) to
  `needs-review` â€” the compile pass skips it until a human approves it via
  `review.py approve-entity` (flips to `stub`; a page that already carries
  prose flips to `compiled` with hashes stamped so the prose survives).

**Verified (Follow-up C):** a fabricated type proposal queues, approves into
a validating schema, and promote re-runs are ledger-silent; rejected alias +
reclassify proposals stay rejected at the same sighting count, are skipped
by a bare `reclassify_apply` run, and re-queue when a second entity sights
them; a 0.95-confidence gated stub lands as needs-review, approve-entity
flips it, and human prose is preserved byte-for-byte on approval.

## Daily/typical commands

```
python3 build_index.py .          # rebuild index after any entity change
python3 synapse.py find <text>    # fuzzy search
python3 synapse.py show <slug>    # full record (alias-resolved)
python3 synapse.py expiring       # warranties/registrations/licenses lapsing soon
python3 synapse.py due            # tasks/bills due soon
python3 synapse.py creds <slug>   # credential REFERENCE (never the secret)
python3 synapse.py list [type]    # everything, or one type
```

## Layout

```
agent-vault/
â”œâ”€â”€ registry/
â”‚   â”œâ”€â”€ schema.yaml          â† canonical vocabulary. PROMOTION STEP writes only.
â”‚   â”œâ”€â”€ aliases.yaml         â† surface form â†’ slug. PROMOTION STEP writes only.
â”‚   â”œâ”€â”€ resolvers.yaml       â† credential scheme â†’ backend. HUMAN writes.
â”‚   â””â”€â”€ _entity-template.md  â† page template (3 ownership-fenced regions)
â”œâ”€â”€ raw/                     â† immutable source documents. APPEND-ONLY. (system boundary)
â”œâ”€â”€ entities/<type>/         â† the wiki proper, one file per entity
â”œâ”€â”€ discovery/               â† LLM proposals.jsonl (Stage 4). APPEND-ONLY.
â””â”€â”€ validate.py              â† schema validator / smoke-test
```

## Wiring it up

Pick a runner (cron / systemd timer / Hermes trigger / n8n / Makefile),
call the cadence scripts:

```cron
# crontab example
@hourly  /path/to/agent-vault/cadences/daily.sh   /path/to/agent-vault
@weekly  /path/to/agent-vault/cadences/weekly.sh  /path/to/agent-vault
@monthly /path/to/agent-vault/cadences/monthly.sh /path/to/agent-vault
# import_collections.sh is manual â€” run it when you drop a new export.
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
python3 ingest.py .                       # walk raw/, write stubs; auto-stub missing refs
python3 compiler.py .                     # the one LLM touchpoint; writes prose only
python3 promote.py .                      # drain discoveries into the registry
python3 review.py . list                  # human queue: approve/reject proposals
python3 review.py . entities              # human queue: needs-review entities
python3 reclassify_apply.py .             # apply queued reclassify proposals (manual)
python3 collections_importer.py . \       # bookkeeping import for catalog media
    --source raw/collections/
python3 validate.py .                     # exit 0 means every entity passes the schema
python3 lint.py .                         # operational anomaly report (read-only)
python3 synapse.py find <text>            # search the vault
```

Each script never shares state in process with another â€” the contract is
the files on disk. Run them in any order, from any harness; the data layer
is identical.

## What's left

Nothing structural. All six stages and all three follow-ups (auto-stubbing,
reclassify apply, the human review loop) are shipped and tested. Future
work, if it shows up, is *content* (more biller patterns, more import
formats, more lint checks) rather than architecture â€” the file contract is
what stays put.

## Pattern library

`registry/patterns.yaml` is the classifier's vocabulary. Add a new biller when you
see a vendor your file collection cares about â€” same shape as the seeded entries:

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

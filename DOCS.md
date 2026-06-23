# Agent Vault â€” Full Documentation

This document explains, in plain terms, **what Agent Vault is, what is actually
built versus scaffolded, how the pieces fit together, and how to run it on your
own files.**

It is the practical companion to the two design documents:
- [`llm-wiki-schema-spec.md`](./llm-wiki-schema-spec.md) â€” the "constitution": file
  format and ownership rules.
- [`llm-wiki-build-guide.md`](./llm-wiki-build-guide.md) â€” the taxonomy and the
  intended build order.

If you read nothing else, read [Â§1](#1-what-it-is) and
[Â§2 Implementation status](#2-implementation-status-real-vs-scaffolding).

---

## 1. What it is

You have a NAS (a drive on your home network) full of accumulated files: bank
statements, utility bills, appliance manuals, warranties, insurance policies,
tax documents, photos, exported emails. It is a junk drawer â€” unsearchable, and
you no longer remember what's in it.

**Agent Vault reads that pile and turns it into an organized, searchable
notebook about your household.** It produces one Markdown page per real-world
thing â€” your furnace, your checking account, your car â€” each carrying the facts
that matter (when a warranty expires, when a bill is due, an account's last 4
digits) and cross-linked to the documents that prove them.

Then you ask it questions instead of digging:

```
synapse find furnace            # fuzzy search
synapse show bofa               # full record for one entity
synapse expiring --days 90      # warranties / renewals lapsing soon
synapse due                     # bills / tasks coming due
synapse creds bofa              # the credential REFERENCE (not the secret)
synapse list account            # everything of one type
```

### The one design choice that matters

Almost the entire system is **deterministic, predictable Python â€” no AI.** A
language model is allowed exactly **one** job: writing the short human-readable
summary paragraph on each page. It may **never** decide how things are filed,
change a date, or edit the vocabulary. This is deliberate: LLMs hallucinate, so
the model is boxed into "prose only," and every fact you query comes from the
actual document, not from a model's guess.

This is enforced by an **ownership model** â€” three authors, each owning a strict
slice of every file (see [Â§4](#4-the-data-contract)).

---

## 2. Implementation status: real vs scaffolding

The *machine* is real and tested. The *contents* shipped in this repo are
samples. The *AI step* needs an external server you provide. And one advertised
feature â€” credential resolution â€” is described but not built.

| Component | Status | Notes |
|-----------|--------|-------|
| `ingest.py` â€” read/sort files â†’ entity stubs | âœ… **Real, working** | Rich classification needs optional libs (`pdfplumber`, `Pillow`, `python-magic`). Without them it still runs but text-less files fall to `unknown` (you now get a stderr warning). |
| `build_index.py` â€” build the search index | âœ… **Real, working** | Only needs `pyyaml`. |
| `synapse.py` â€” the query CLI | âœ… **Real, working** | Pure lookup over the index. |
| `compiler.py` â€” **MockClient** | âœ… **Real, working** | Deterministic, offline, no network. Used by all tests/CI. |
| `compiler.py` â€” **OllamaClient** (the real AI) | âš™ï¸ **Real code, external dependency** | Calls an [Ollama](https://ollama.com) server over HTTP. You must run that server yourself; nothing is bundled. Not exercised in this repo's tests. |
| `promote.py` â€” learn vocabulary from proposals | âœ… **Real, working** | Atomic, locked, idempotent. |
| `reclassify_apply.py` â€” apply approved re-filings | âœ… **Real, working** | Two-phase, crash-recoverable. |
| `lint.py` â€” anomaly report | âœ… **Real, working** | Read-only. |
| `collections_importer.py` â€” Steam/Goodreads/IMDB/Letterboxd/Discogs/Kindle/Audible/CSV | âœ… **Real, working** | Bookkeeping import. |
| `validate.py` â€” schema gate | âœ… **Real, working** | The smoke test every stage reuses. |
| `cadences/*.sh` â€” daily/weekly/monthly wrappers | âœ… **Real, working** | Plain POSIX `sh`; wire to any runner. |
| **Credential resolution** (turn a `creds` ref into the actual secret) | âœ… **Real, working** | The `resolvers/` package ships **9 backends**: `age`, `env`, `onepassword`, `bitwarden`/`vaultwarden`, `pass`, `gpg`, `secret-tool` (GNOME keyring), `keychain` (macOS), `vault` (HashiCorp). `synapse resolve <slug>` fetches the secret on demand; `creds` shows only the reference; plaintext is never persisted. Adding another = one module + one stanza. |
| **File extractors** (turn documents into text) | âœ… **Real, working** | PDF, email (+attachments), JPEG/PNG (EXIF), text/CSV, **and now `.docx` / `.xlsx` / `.html`** (pure stdlib). Each degrades to empty text on error, never crashes. |
| **Compile token efficiency** (contract 1.1) | âœ… **Real, working** | Source text is deterministically de-noised + budgeted before the LLM sees it; a fact-bearing line is never dropped; per-entity + aggregate token logging. The LLM still sees all real content. |
| **Write-side secret guard** (keep secrets out of the vault) | âœ… **Real, working** | `secret_scan.py`: ingestion scans each source for secret-shaped strings and downgrades the stub to `needs-review` (never recording the secret); `validate.py` hard-fails any entity with a real credential in a frontmatter field. Two strictness tiers keep false positives out of the schema gate. |
| The 3 files under `entities/` | ðŸŽ­ **Sample data** | Hand-written demos (furnace, BofA checking, furnace warranty). Not your data. |
| `tests/fixtures/` (~25 files) | ðŸŽ­ **Synthetic test data** | Fake statements, photos, emails generated to prove the pipeline. |
| `raw/*` folders | ðŸ“­ **Empty** | Only `.gitkeep` placeholders. This is where *your* documents go. |
| `discovery/`, `raw/collections/` | ðŸ“­ **Created on demand** | Do not exist until a run produces them. |
| `registry/schema.yaml` (taxonomy) | âœ… **Real, lightly seeded** | The 12-type taxonomy is complete; `tags:` has a handful of seed entries. |
| `registry/patterns.yaml` (classifier vocabulary) | âœ… **Real, small** | ~13 billers + ~12 shapes. Designed to grow as you meet real vendors. |
| `registry/aliases.yaml` | âœ… **Real, tiny** | 2 seed aliases. Grows via `promote.py`. |

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
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”           â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”      â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚ raw/             â”‚  ingest   â”‚ entities/<type>/*.md  â”‚ indexâ”‚ synapse find â”‚
   â”‚  statements/     â”‚ â”€â”€â”€â”€â”€â”€â”€â”€â–º â”‚  (one page per thing) â”‚ â”€â”€â”€â–º â”‚ synapse show â”‚
   â”‚  documents/      â”‚           â”‚                       â”‚      â”‚ synapse due  â”‚
   â”‚  email/  media/  â”‚           â”‚  frontmatter (facts)  â”‚      â”‚ synapse ...  â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜           â”‚  LINKS block          â”‚      â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
        (append-only)             â”‚  prose body  â—„â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€ compile (the LLM)
                                   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                                           â”‚  â–²
                          proposals.jsonl  â”‚  â”‚  registry/ (vocabulary)
                                           â–¼  â”‚  schema Â· patterns Â· aliases
                                      â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                                      â”‚  promote     â”‚  â† only writer of vocabulary
                                      â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

**The pipeline, end to end:**

1. **`ingest.py`** â€” walk `raw/`, hash each file (so re-runs are no-ops), detect
   its kind, extract text/metadata, classify it (vendor patterns Ã— document
   shapes Ã— file-type priors), and write an entity **stub** (facts only, empty
   prose). Emails fan out: the message and each attachment become separate,
   cross-linked subjects. Missing link targets get minimal `needs-review` stubs
   so the graph stays closed.
2. **`compiler.py`** â€” for each stub (or any page whose sources changed), call
   the LLM to write the **prose body only**, and append any vocabulary
   **proposals** it makes to `discovery/proposals.jsonl`. Flips `status: stub â†’
   compiled`.
3. **`promote.py`** â€” drain those proposals, collapse synonyms through the alias
   map, count "sightings," and graduate the good ones into the registry per the
   thresholds in `schema.yaml`. This is the **only** writer of the vocabulary.
4. **`build_index.py`** + **`synapse.py`** â€” pre-extract every queryable field
   into `_index.json`, then answer questions by *filtering* it (never by
   reasoning over text).
5. **`validate.py`** / **`lint.py`** â€” schema gate (hard pass/fail) and a
   read-only operational anomaly report.

Two side branches:
- **`collections_importer.py`** â€” for catalog media you don't have files for
  (Steam/Goodreads/IMDB/CSV exports) â†’ one `collection` entity per row.
- **`reclassify_apply.py`** â€” the human-approved step that physically re-files an
  entity across type folders and rewrites every cross-reference.

---

## 4. The data contract

The files **are** the contract. Three authors, each owning a strict region of
every entity page:

```
---
# â”€â”€ FRONTMATTER â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€  author: PYTHON (ingest)   the FACTS
slug: carrier-furnace
type: asset
subtype: hvac
title: Carrier Furnace (Basement)
status: compiled
confidence: 0.95
created: 2026-05-21
sources: [raw/documents/furnace-receipt.pdf]
sources_hash: a1b2c3d4e5f6
compiled_from_hash: a1b2c3d4e5f6     # â† compile may set ONLY this + status
expires: 2031-03-15
related: [document/carrier-furnace-warranty]
---

<!-- LINKS:BEGIN -->                  author: PYTHON   regenerated every run
- Related: [[document/carrier-furnace-warranty]]
<!-- LINKS:END -->

The Carrier furnace in the basement was installed in 2021 and carries a   â† PROSE
10-year parts warranty through 2031. [NEEDS SOURCE: model/serial number].  author: LLM
```

| Region | Author | Rule |
|--------|--------|------|
| Frontmatter | **Python** (ingest/promote) | The LLM must never touch it. Compile may change **only** `status` and `compiled_from_hash`. |
| LINKS block | **Python** | Regenerated from `related:` every run. |
| Prose body | **LLM** (compile) | The model's *only* writable surface. Never invents facts â€” emits `[NEEDS SOURCE: â€¦]` instead. |

**`status` lifecycle:** `stub` â†’ (compile) â†’ `compiled`; low-confidence files
land in `needs-review` (or `unknown`) for a human to fix before they're worth
compiling; `archived` is retired-but-kept.

**`sources_hash` vs `compiled_from_hash`:** the first is a fingerprint of the
source set; the second records what the prose was last built from. When they
diverge, the compile pass knows to rewrite the prose â€” that's how new evidence
triggers a fresh summary.

### The registry (the vocabulary, under `registry/`)

| File | Author | Purpose |
|------|--------|---------|
| `schema.yaml` | promotion only | The 12 types, their subtypes, page templates, gating flags, promotion thresholds, and graduated tags. |
| `patterns.yaml` | human + promotion | The classifier's vocabulary: known billers and document shapes. **Edit this directly** to teach it a new vendor. |
| `aliases.yaml` | promotion only | Surface form â†’ slug (`"boa"` â†’ `bofa-checking`). |
| `resolvers.yaml` | human | Credential backend config (scheme â†’ backend module). 9 backends implemented (see Â§2). |
| `_entity-template.md` | human | The three-region page template, fully commented. |

**The anti-rot rule:** the LLM *proposes* vocabulary additions; only the
deterministic `promote.py` *commits* them. A non-deterministic component never
writes the vocabulary it later reads.

### Rules that never bend (from the spec)

1. One author per file region.
2. `raw/` is append-only â€” source documents are immutable.
3. The LLM proposes; deterministic code commits.
4. Secrets are **referenced** (`scheme://store/path`), never stored in plaintext.

---

## 5. Script reference

Run any script with `-h`/`--help` for its own usage. All take an optional vault
directory as the first argument (default: current directory).

| Script | What it does | LLM? | Writes |
|--------|--------------|:----:|--------|
| `ingest.py` | `raw/` â†’ entity stubs; hashes for idempotency; classifies; auto-stubs missing link targets | no | `entities/`, `raw/_manifest.jsonl`, `_index.json` |
| `compiler.py` | Writes prose for stubs / drifted pages; logs proposals | **yes** | prose body, `status`+`compiled_from_hash`, `discovery/proposals.jsonl` |
| `promote.py` | Drains proposals â†’ graduates vocabulary by threshold | no | `registry/schema.yaml`, `registry/aliases.yaml`, `discovery/promoted.jsonl` |
| `build_index.py` | Walks entities â†’ `_index.json` (+ human `_index.md`) | no | `_index.json`, `_index.md` |
| `synapse.py` | Query CLI: `find`/`show`/`due`/`expiring`/`creds`/`resolve`/`list`/`compact` | no | nothing read-only (except `compact --apply`) |
| `compact.py` | Bounds the append-only discovery logs without changing outcomes | no | rewrites `discovery/*.jsonl` (+ `.bak`) only with `--apply` |
| `locking.py` | One vault-wide advisory write lock (used by all mutating passes) | no | `registry/_vault.lock` |
| `resolvers/` | Credential resolution: dispatcher + 9 backends (`age`, `env`, `onepassword`, `bitwarden`/`vaultwarden`, `pass`, `gpg`, `secret-tool`, `keychain`, `vault`) | no | nothing (reads external secret stores on demand) |
| `validate.py` | Schema validator (incl. no-plaintext-secret gate); exit 1 on any error | no | nothing |
| `secret_scan.py` | Detects secret-shaped strings; used by ingest (flag) + validate (reject) | no | nothing (library) |
| `lint.py` | Operational anomaly report (9 checks); exit 1 on findings | no | optional JSON report only |
| `reclassify_apply.py` | Applies human-approved reclassify proposals; re-files + rewrites refs | no | entity files, `discovery/promoted.jsonl` |
| `review.py` | Human approve/reject: queued proposals (incl. new types) + needs-review entities | no | registry (on approve), entity `status:` lines, `discovery/promoted.jsonl` |
| `collections_importer.py` | Library exports â†’ `collection` entities | no | `entities/collection/`, `raw/collections/_imports.jsonl` |

**Cadence wrappers** (`cadences/`, plain `sh` â€” wire to cron/systemd/anything):

| Wrapper | Runs | Cost |
|---------|------|------|
| `daily.sh` | ingest + validate | cheap, idempotent |
| `weekly.sh` | ingest + compile + promote + validate | the LLM cadence |
| `monthly.sh` | validate + lint | read-only audit |
| `import_collections.sh` | the collections importer | manual, on demand |

---

## 6. Setup & dependencies

**Required:** Python 3 and `pyyaml`.

```
pip install pyyaml
```

**Recommended** (without these, PDFs and images yield no text and pile up in
`unknown` â€” `ingest.py` warns you on stderr when they're absent):

```
pip install pdfplumber pillow python-magic
```

`.docx` / `.xlsx` / `.html` need no extra packages â€” they're parsed with the
standard library.

**For real AI summaries** (otherwise use the offline mock):

```
# install and run Ollama (https://ollama.com), then:
export AGENT_VAULT_COMPILER=ollama
export OLLAMA_HOST=http://localhost:11434
export OLLAMA_MODEL=qwen2.5:7b-instruct
```

To run fully offline / in CI, use the deterministic mock client:

```
export AGENT_VAULT_COMPILER=mock
```

---

## 7. Using it on your own files

```
# 0. one-time: install deps (see Â§6)

# 1. drop your documents into raw/ (append-only; organize into the
#    subfolders if you like â€” the classifier doesn't require it)
cp ~/scans/*.pdf  agent-vault/raw/documents/
cp ~/exports/*.eml agent-vault/raw/email/

# 2. ingest â€” read, classify, write stubs
python3 ingest.py .

# 3. compile â€” write the human summaries (needs Ollama, or use mock)
AGENT_VAULT_COMPILER=mock python3 compiler.py .       # offline
#   or: AGENT_VAULT_COMPILER=ollama python3 compiler.py .

# 4. promote â€” fold any new vocabulary into the registry
python3 promote.py .

# 5. check & query
python3 validate.py .
python3 synapse.py find <text>
python3 synapse.py expiring --days 90
```

Then automate steps 2â€“5 by pointing a runner at the cadence scripts:

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
python3 synapse.py creds bofa-checking      # shows the REFERENCE + backend only
python3 synapse.py resolve bofa-checking    # fetches the SECRET on demand -> stdout
```

`resolve` prints the secret to stdout and nothing else (so it pipes cleanly);
context goes to stderr; the plaintext is never written back into the vault.

Shipped backends (pick per ref by scheme):

| Scheme | Ref shape | Needs | Notes |
|--------|-----------|-------|-------|
| `age` | `age://store/path` | `age` binary + keyfile + `store_dir` of `.age` files | recommended for NAS |
| `onepassword` | `onepassword://vault/item[/field]` | `op` CLI, signed in (or `OP_SERVICE_ACCOUNT_TOKEN`) | field defaults to `password` |
| `bitwarden` | `bitwarden://item[/field]` | `bw` CLI, unlocked (`BW_SESSION`) | built-in or custom fields |
| `vaultwarden` | `vaultwarden://item[/field]` | `bw` CLI pointed at your server | same backend as bitwarden |
| `env` | `env://store/path` â†’ `STORE_PATH` | â€” | dev / non-secret only |

Auth is the operator's job (sign into `op`, unlock `bw`); the resolver just
invokes the already-authenticated CLI. Add another backend by dropping a module
in `resolvers/` and a stanza in `resolvers.yaml` â€” no schema change.

**Reviewing the machine's guesses:** anything it wasn't sure about is written as
`status: needs-review`. Open the file, fix `type`/`subtype` (or merge a
duplicate), and let the next compile pass pick it up. `lint.py` will nag you
about review items that have been sitting too long.

---

## 8. Known gaps / not yet built

- **Nine resolver backends ship** (`age`, `env`, `onepassword`, `bitwarden`/
  `vaultwarden`, `pass`, `gpg`, `secret-tool`, `keychain`, `vault`). The CLI-based
  ones (`op`/`bw`/`pass`/`secret-tool`/`security`/`vault`) are tested with stub
  CLIs + mapping unit tests; `age` and `gpg` have real encryptâ†’resolve round
  trips. Not yet verified against live 1Password/Bitwarden/Vault accounts.
- **OllamaClient plumbing is tested; real-model quality isn't.** `tests/
  test_ollama_client.py` exercises the HTTP request construction, response
  parsing, and every error path (unreachable / HTTP 500 / non-JSON) against a
  mocked transport, and confirms `compile_all` isolates a raising client. What
  remains environment-dependent is the *quality* of a real model's prose â€” that
  depends on your `OLLAMA_MODEL` choice, not the harness.
- **The pattern library covers ~100 common US billers** (banks, brokerages, card
  issuers, P&C + health insurers, telecoms, utilities, streaming, subscriptions,
  retailers, pharmacies, travel) and ~25 document shapes (statements, bills,
  warranties, paystubs, W2/1099, mortgage, HOA, medical EOB, lab results, lease,
  invoice, vehicle registration, â€¦). Still expect to add niche/regional vendors â€”
  a few lines of YAML; `tests/test_patterns.py` guards that every `default_tag`
  exists in the schema and every shape maps to a real subtype.
- **No real data ships here.** The 3 entities and `tests/fixtures/` are samples.

### Operational hardening (production posture)

- **Concurrency:** every mutating entry point (ingest / compile / promote /
  reclassify / collections-import) takes one vault-wide advisory lock
  (`locking.py`), so overlapping cadence runs serialize instead of corrupting
  shared state. Reclassify rebuilds `_index.json` after moving files.
- **Resilience:** a poison file is isolated (recorded as a manifest `error`, run
  continues); oversized files are skipped (`AGENT_VAULT_MAX_RAW_MB`, default 50);
  docx/xlsx have a decompression-bomb guard. `lint`'s `ingest_errors` check
  surfaces anything skipped.
- **Durability:** all vault writes are `tmp + fsync + os.replace` (atomic, and
  survive power loss before the rename).
- **Observability:** each cadence appends a JSON run record to
  `discovery/_runs.jsonl` (cadence, ts, rc, duration) â€” cron-debuggable.
- **Log growth:** the append-only discovery logs are bounded on demand by
  `python3 compact.py . --apply` (or `synapse compact --apply`), which dedups
  `proposals.jsonl`/`promoted.jsonl` **without changing any promotion outcome**
  (writes `.bak` backups first). Run it quarterly or wire it into a cadence.
- **Packaging:** `requirements.txt` (PyYAML required; pdfplumber/Pillow/
  python-magic optional) and `run_tests.sh` (one command runs the whole
  no-pytest suite). Python 3.8+.

---

## 9. Glossary

- **Entity** â€” one Markdown page representing one real-world thing (an account, an
  appliance, a document).
- **Stub** â€” a freshly ingested entity: facts filled in, prose empty, awaiting
  compile.
- **Compile** â€” the single LLM step that writes an entity's prose body.
- **Promote** â€” the deterministic step that graduates proposed vocabulary
  (tags/aliases/subtypes) into the registry.
- **Registry** â€” the canonical vocabulary under `registry/` (types, patterns,
  aliases).
- **Proposal** â€” a structured suggestion the LLM appends to
  `discovery/proposals.jsonl`; never applied directly.
- **Sighting** â€” one occurrence of a proposed concept; promotion is threshold-
  based on counts.
- **Cadence** â€” a scheduled wrapper script (daily/weekly/monthly).
- **Credential reference** â€” a `scheme://store/path` URI pointing at where a
  secret lives; the secret itself is never stored in the vault.
</content>
</invoke>

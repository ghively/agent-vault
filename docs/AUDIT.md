# Agent Vault — Completeness Audit & Feature-Gap Analysis

_Date: 2026-07-04 · Scope: full repo (`agent_vault/` pipeline + API, `web/` UI,
`cadences/`, `tests/`, docs) · Method: read-only code audit + two parallel
sub-audits (backend/frontend) + web survey of comparable systems._

This document has three parts:

1. **[Health snapshot](#1-health-snapshot)** — what's green today.
2. **[Gaps & defects](#2-gaps--defects)** — confirmed bugs and completeness gaps,
   by tier and severity, each with a file reference and a concrete fix.
3. **[Feature opportunities](#3-feature-opportunities)** — functionality that
   comparable systems ship and Agent Vault could adopt, with a prioritized roadmap.

Nothing in the codebase was modified to produce this report.

---

## 1. Health snapshot

The system is in genuinely good shape. It is disciplined, well-documented, and
the core invariant ("the LLM writes prose only; deterministic code commits") is
upheld structurally, not just by convention.

| Check | Result |
|---|---|
| Backend tests (`python -m pytest`, `test_jobs` isolated) | **286 passed, 1 skipped** |
| `ruff check .` | **clean** |
| `mypy agent_vault` | **clean (38 files)** |
| Frontend tests (`npm test -- --run`) | **118 passed (22 files)** |
| Frontend build (`tsc -b && vite build`) | **clean (289 kB JS / 85 kB gzip)** |

Verified-solid subsystems (audited, **not** gaps):

- **Atomic writes everywhere** — `tmp + fsync + os.replace`; proposals are
  appended *before* the status flip so a crash can't lose discoveries.
- **Vault locking** (`locking.py`) — coarse advisory `flock` around every
  mutator, bounded wait, degrades **loudly** (never silently) on `ENOLCK`.
- **Resolver hardening** — `parse_ref` rejects `..`/`.`/backslash/control-chars/
  leading-dash (argv-flag injection); `age`/`gpg` add `realpath` containment;
  all subprocess calls use argv lists (no `shell=True`), bounded timeouts, and
  truncated stderr.
- **Job subprocess allowlist** (`api/jobs.py`) — per-op flag/value regex
  allowlist; `AGENT_VAULT_PATH` pinned so a caller can't redirect the vault.
- **Secret scanning** (`secret_scan.py`) — two-tier (branded strict + labeled
  broad), Luhn-validated PANs, non-reversible fingerprints (never stores the
  secret).
- **Compiler trust boundary** — drops everything outside `<prose>`/`<proposals>`,
  strips injected frontmatter/LINKS delimiters, surfaces malformed JSON on
  stderr.
- **Auth** — constant-time token compare; frontend auto-clears token on 401 and
  validates against `/api/health` before trusting it.

---

## 2. Gaps & defects

### 2.1 Confirmed defects (fix first)

#### D1 — `POST /api/entities/{slug}/recompile` self-deadlocks in production · **HIGH · confirmed**

`api/creds.py:166` opens `with vault_lock(str(vault)):` and then, still holding
it, calls `compiler.compile_all(...)` at `creds.py:193`, which at
`compiler.py:784` unconditionally re-acquires `with vault_lock(vault):`. `flock`
is **not** re-entrant across two `open()` handles in the same process: the second
acquisition blocks (`BlockingIOError`), retries for `AGENT_VAULT_LOCK_TIMEOUT_S`
(default **600 s**), then raises `LockTimeout` → **503**. The endpoint is
effectively always broken.

Worse: `creds.py:182-190` flips the entity's `status:` to `stub` **while keeping
its prose body** and writes it to disk *before* the compile call — so the failed
recompile leaves the entity in a `stub_with_prose` state that `lint.py` then
flags.

- **Why CI is green anyway:** every recompile test (`tests/api/test_creds.py:234,
  300,345`) patches `compiler.compile_all` with a `Mock`, so the real lock-taking
  path never runs. The endpoint's single most important behavior is untested.
- **Fix (clean, already scaffolded):** a lock-free `_compile_all_locked` entry
  point already exists at `compiler.py:788`. Do the status rewrite under the
  lock, release it, then call the lock-free compile — exactly the pattern
  `edit.py:14-18` and `review.cmd_approve` already use. Add an integration test
  (mock compiler, `pytest-timeout`) that drives the real path.

#### D2 — Fire-and-forget job tasks can be GC'd; runs are unpersisted & unrecorded · **HIGH**

`api/jobs.py:358` does `asyncio.create_task(run_job(...))` and discards the
handle. CPython keeps only a *weak* reference to a bare task, so a running job
can be garbage-collected before completion. Additionally: (a) `JobRegistry` is
in-memory only (`jobs.py:143-151`) — all job state is lost on restart; (b) API-
triggered runs are **never appended to `discovery/_runs.jsonl`**, so `GET
/api/runs` and the dashboard `last_run` silently omit every run started from the
web UI.

- **Fix:** retain a strong reference (module-level `set` of tasks, discard in a
  done-callback); append a `_runs.jsonl` record on completion so the web path
  shares the run-history contract the cadence paths already honor.

### 2.2 Backend completeness & robustness gaps

| # | Sev | File | Gap | Fix |
|---|-----|------|-----|-----|
| B1 | MED | `api/config.py:29-47`, `serve.py:20-21` | No startup validation: `vault_path` existence unchecked (→ confusing 404s); non-numeric `AGENT_VAULT_PORT` throws unhandled `ValueError` at boot. | Validate at startup with actionable errors. |
| B2 | MED | `api/vault_config.py:173` | `POST /api/config/apply` does `os.environ.update(...)` on the live process — shared mutable state, not persisted, leaks into every future subprocess. | Persist to a file or scope per-request. |
| B3 | MED | `cadences/run_cadence.py` (263 lines), `cadences/*.sh` | The cross-platform runner that writes the `_runs.jsonl` the dashboard depends on has **no tests**; the `.sh` wrappers previously shipped a "calls nonexistent module" regression a smoke test would have caught. | Add an end-to-end `run_cadence.py daily <tmp>` test with the mock compiler. |
| B4 | MED | `secret_scan.py:30-47` | The **strict** write-side gate matches only branded formats (AWS/`ghp_`/`xox`/JWT/PEM/Luhn). A generic high-entropy DB password with no prefix passes and can be written into frontmatter via `PATCH .../entities/{slug}` or `PUT .../raw`. | Apply the labeled/entropy heuristic on human-edit write endpoints (false positives are recoverable there). |
| B5 | MED | `api/creds.py:104-123` | `POST /api/creds/{slug}/resolve` returns plaintext secrets over HTTP but writes **no audit record** of who resolved what, when. | Add an audit log (slug + timestamp + outcome, never the secret). |
| B6 | MED | `api/auth.py`, `api/app.py:33` | One static bearer token gates all 26 routes incl. credential resolution — no scopes, no rotation, no rate limiting. Anyone with the token can exfiltrate every resolvable secret. | Rate-limit `/creds/*/resolve`; consider a separate higher-bar credential for resolution. |
| B7 | LOW | `api/jobs.py:323-365` | No `DELETE /jobs/{id}` (can't kill a runaway subprocess); no concurrency guard, so overlapping mutating ops each block up to 600 s on `flock` then fail rather than queue. | Add cancellation + a single-flight 409 ("vault busy"). |
| B8 | LOW | `api/reads.py:140,166` | `list_entities` parses `_index.json` twice per request (row scan + `len(load_index(vault))`). | Bind once, reuse `len(...)`. |
| B9 | LOW | `api/reads.py:48`, `creds.py:58`, `compiler.py:694` | Unguarded `path.read_text()` / bare `open().read()`: a file removed by a concurrent job → 500 not 404; fd relies on refcount GC. | Guard `OSError` → 404; use `with`. |

### 2.3 Frontend completeness & UX gaps

| # | Sev | File | Gap | Fix |
|---|-----|------|-----|-----|
| F1 | HIGH | `web/src/main.tsx:17` | No top-level `ErrorBoundary`: a render throw in `Waybar`/`Launcher`/`CommandBar`/`QuickPanel`/`Desktop` white-screens the whole app (the only boundary is *inside* each window). | Wrap `<Desktop/>` (and `TokenGate`) in an `ErrorBoundary`. |
| F2 | HIGH | `screens/Pipeline.tsx:91`, `api/jobs.ts:88` | SSE `streamJob` is called without the `signal` it accepts: no Cancel button, no client stall-timeout (a missing terminal `end` hangs `running` forever), and closing the window leaves the `fetch` stream running. | Wire an `AbortController`, add cancel + stall timeout, abort on unmount. |
| F3 | MED | `wm/AppIcon.tsx:6` | `import.meta.glob(..., {eager:true})` bundles all **48** icons; only 8 are referenced — ~40 SynapseNAS leftovers ship as dead weight. | Import only the 8 used icons. |
| F4 | MED | `wm/QuickPanel.tsx:120,176-241` | Theme/density/backend toggles + collapse are `<span onClick>` with no `role`/`tabIndex`/`onKeyDown` — keyboard/SR users can't operate them (Settings has real `<button>`s). | Make them buttons or add role+key handlers. |
| F5 | MED | `screens/Creds.tsx:182`, Wiki/Review dialogs | Modals set `role=dialog aria-modal` + Escape but **don't trap Tab** — focus escapes to the window behind. | Trap Tab, restore focus to trigger on close. |
| F6 | MED | `wm/Waybar.tsx:26` | Count badges render `...?.length ?? 0` — an API error shows a confident "0". Also `Pipeline.runOps` invalidates only `runs`/`ledgers`, so review/status badges stay stale after a compile/promote job. | Show an error glyph on `isError`; invalidate `status`/`review` on job completion. |
| F7 | MED | `screens/Browse.tsx:6` | Type filter chips are hardcoded (`["all","asset",...]`) instead of using `useSchema()` — drifts silently if the taxonomy changes. | Derive chips from `/api/schema`. |
| F8 | MED | `web/package.json` | No ESLint config or `lint` script (README confirms); `tsc -b` is the only static gate — far weaker than the backend's ruff+mypy, and CI runs no lint. | Add typescript-eslint + react-hooks + a CI step. |
| F9 | MED | `screens/Review.tsx`, `api/mutations.ts` | Review queue has no bulk (select-all) actions and no undo; reclassify/raw-save are effectively irreversible from the UI. | Add multi-select bulk actions + undo for reversible mutations. |
| F10 | LOW | `wm/QuickPanel.tsx:114-119`, `Pipeline.tsx:44` | Dead chrome: power/reload glyphs `title="not implemented yet"`; profile hardcodes `root@synapse`/`schema v1`; the "REGISTRY" pipeline card is decorative. | Remove stubs or wire to `/api/status`. |
| F11 | LOW | `store/windows.ts`, shell | No job-completion toast and no window-state persistence — a run finishing while minimized gives no feedback; refresh returns to the Vault hub. | Surface a completion toast; persist minimal window state. |

### 2.4 Cross-cutting completeness gaps

These are absences rather than bugs — capabilities a "household system of record"
is expected to have that don't exist anywhere in the 27 routes or the CLI:

- **No backup / restore / export.** The only backup is `compact.py`'s `.bak`
  files for JSONL ledgers. There is no vault snapshot, no export, no documented
  restore path for the user's system of record. _(→ see [O1](#o1--vault-snapshot--export))_
- **No schema migration/versioning.** `registry/schema.yaml` has no
  `schema_version`; a renamed/removed type silently invalidates existing
  entities with no upgrade tooling. _(→ [O2](#o2--schema-versioning--migration))_
- **No reminders/notifications.** `expiring` and `due` exist as *pull* CLI
  queries, but nothing *pushes* — no email/webhook/calendar when a warranty
  lapses or a bill comes due. This is the single most-cited feature of every
  comparable product. _(→ [O3](#o3--reminders--notifications))_
- **No metrics/observability.** Only `uvicorn` request logs; no `/metrics`, no
  structured logging, no counters.
- **Search is substring-only, metadata-only.** `synapse.cmd_find` (`synapse.py:111`)
  is a plain `if q in hay` over concatenated slug/title/type/tags — the docstring
  calls it "fuzzy" but there is no ranking, no typo tolerance, and it **never
  searches the prose bodies**. _(→ [O4](#o4--better-search-full-text--semantic))_
- **Doc drift:** `AGENTS.md` claims `OllamaClient` "has none [no test coverage]",
  but `tests/test_ollama_client.py` (162 lines) covers its parsing/error paths
  against a mocked transport (only a *live* server is, correctly, unexercised).
  `DOCS.md §2` is right; `AGENTS.md` is stale — reconcile.

---

## 3. Feature opportunities

Surveyed against Paperless-ngx (self-hosted DMS), home-inventory apps (Under My
Roof, HomeZada, Spullio), local-RAG stacks (Ollama + Chroma/Qdrant), and bill/
subscription trackers (Rocket Money, Monarch, MoneyPatrol). Agent Vault's
deterministic-facts model is a genuine differentiator; these are the gaps
relative to what users of those tools expect. Ordered by value/effort.

### O3 — Reminders & notifications _(highest value)_
Every home-inventory and bill tracker's flagship feature is a *push* when a
warranty/registration/bill approaches. Agent Vault already **computes** this
(`expiring`, `due`) — it only lacks delivery. Add a `notify` cadence that runs
the existing date queries and dispatches via a pluggable sink (email/SMTP,
webhook, or an ICS calendar feed the user subscribes to). Fits the file-contract
model: read-only over entities, append a `discovery/_notified.jsonl` for
idempotency. **Low effort, transformative.**

### O4 — Better search (full-text + optional semantic)
Two tiers, both incremental over today's substring match:
1. **Full-text over prose bodies** with ranking (stdlib: build an inverted index
   in `build_index.py`, or ship a SQLite FTS5 sidecar index — still deterministic,
   still rebuildable). Immediately fixes "search never looks inside the page."
2. **Optional semantic search** — Ollama is *already a dependency* for compile;
   reuse it for embeddings (`nomic-embed-text`/`mxbai-embed-large`) into a local
   vector index (Chroma/sqlite-vec) for "find my HVAC warranty" natural-language
   recall. Keep it **read-side only** so the core invariant is untouched — this
   never writes vault facts, it only ranks retrieval.

### O5 — Ask/RAG answers over the vault
The `GET /api/ask` endpoint exists but answers from status/date math. A local-RAG
layer (retrieve top-k entities via O4, feed to the same Ollama client, cite the
source entities) turns "when does my furnace warranty expire and who installed
it?" into a grounded, cited answer — the headline capability of every 2025 local
document-Q&A stack, and a natural extension since the model client already exists.
**Keep answers extractive + cited** to stay honest to the no-hallucination ethos.

### O1 — Vault snapshot / export
`agent-vault export --out vault-backup.tar.zst` (tar of `entities/` + `registry/`
+ `discovery/`, excluding `raw/` by default with a `--include-raw` flag), plus a
matching import/restore and a documented procedure. Optionally an API route.
Comparable DMS tools treat this as table stakes for a system of record.

### O6 — More ingestion on-ramps (email + folder watch)
Paperless-ngx's most-used intake is **IMAP polling** and **watched-folder
consume**. Agent Vault already parses `.eml` with attachment fan-out — add a
`fetch-mail` cadence (IMAP → `raw/email/`) and a folder-watch mode so documents
flow in without manual copying. Pure orchestration around existing extractors.

### O7 — More collection importers & biller patterns _(content, not architecture)_
The importer already covers Steam/Goodreads/IMDB/Letterboxd/Discogs/Kindle/
Audible/CSV. Cheap wins: Plex/Jellyfin libraries, bank OFX/QFX for
account-statement ingest, and continued growth of `patterns.yaml` billers for
regional vendors. This is the "future work is content" the README already
anticipates.

### O2 — Schema versioning & migration
Add `schema_version` to `schema.yaml` and a migration shim (even a documented
manual procedure + a `reclassify_apply`-style bulk retype) so a breaking taxonomy
change has an upgrade path instead of silently invalidating entities.

### O8 — Barcode / serial / receipt capture (mobile-adjacent)
Home-inventory apps lean on barcode + serial-number capture and per-item receipt
photos. Agent Vault's asset entities could carry `serial`/`barcode`/`purchase`
fields and link receipt images already in `raw/` — a schema + extractor
enrichment, no new architecture.

### Feature-parity matrix

| Capability | Paperless-ngx | Home-inventory apps | Bill trackers | **Agent Vault today** |
|---|:--:|:--:|:--:|:--:|
| OCR / text extraction | ✅ | partial | — | ✅ (PDF/img EXIF/office/html) |
| Auto-tagging / classification | ✅ (ML) | manual | — | ✅ (deterministic patterns) |
| Full-text search of contents | ✅ | — | — | ❌ (metadata substring only) |
| Semantic / NL Q&A | partial | — | — | ⚠️ scaffolded (`/api/ask`, no RAG) |
| **Expiry/bill reminders (push)** | via tags | ✅ | ✅ | ❌ (compute-only, no delivery) |
| Recurring-charge detection | — | — | ✅ | ❌ |
| Email/folder auto-ingest | ✅ | — | — | ⚠️ (`.eml` parse, no poller) |
| Backup / export / restore | ✅ | ✅ (cloud) | ✅ | ❌ |
| Credential vault (referenced) | — | — | — | ✅ (9 backends, unique strength) |
| No-hallucination / auditable facts | — | — | — | ✅ (core differentiator) |

---

## 4. Recommended sequence

1. **D1** (recompile deadlock) + its integration test — a confirmed production
   defect that CI hides. Small, isolated, high impact.
2. **D2** (job task GC + `_runs.jsonl` recording) — correctness of the whole web
   pipeline path.
3. **O3** (reminders) — highest user-facing value, lowest effort, reuses existing
   date queries.
4. **F1/F2** (top-level error boundary + SSE cancel/abort) — the two frontend
   robustness gaps that most affect real use.
5. **O4 → O5** (full-text search → cited RAG answers) — the biggest capability
   leap; sequence them since RAG builds on the search index.
6. **O1/O6/O2** (backup, mail/folder ingest, schema versioning) — operational
   maturity for a system of record.
7. Content backlog: **B3** cadence tests, **F8** ESLint/CI, doc-drift fixes,
   **O7** importers/patterns.

---

_Sub-audit method: two independent read-only agents covered backend and frontend
in parallel; the headline defect (D1) and the health snapshot were re-verified
by hand against the source. No code was changed._

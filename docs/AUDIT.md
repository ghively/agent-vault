# Agent Vault — Completeness Audit & Feature-Gap Analysis

_Date: 2026-07-04 · Scope: full repo (`agent_vault/` pipeline + API, `web/` UI,
`cadences/`, `tests/`, docs) · Method: read-only code audit + two parallel
sub-audits (backend/frontend) + web survey of comparable systems._

**Purpose the audit is graded against:** Agent Vault is a **shared LLM wiki — a
unified knowledgebase that multiple agents read from and write to**, with a
deterministic, no-hallucination fact model. Priorities below reflect *that* goal:
concurrency correctness, retrieval quality, and a clean machine read/write surface.
Consumer features (human reminders, mobile capture, bill tracking) are out of
scope.

This document has three parts:

1. **[Health snapshot](#1-health-snapshot)** — what's green today.
2. **[Gaps & defects](#2-gaps--defects)** — confirmed bugs and completeness gaps,
   by tier and severity, each with a file reference and a concrete fix.
3. **[Feature opportunities](#3-feature-opportunities)** — functionality a shared
   multi-agent knowledgebase needs, with a prioritized roadmap.

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

#### D1 — `POST /api/entities/{slug}/recompile` self-deadlocks in production · **HIGH · confirmed · ✅ FIXED**

> **Resolved.** The endpoint now calls the lock-free `compiler._compile_all_locked`
> inside the single lock it already holds (`api/creds.py`), so the status flip and
> the compile stay atomic under one acquisition and never re-enter `flock`. A new
> real-path regression test (`tests/api/test_creds.py::test_recompile_entity_real_compile_no_deadlock`)
> drives the endpoint with the offline MockClient and a 5 s lock timeout — it
> compiles and returns instead of stalling, and would fail fast if the deadlock
> returned. The three shape/auth tests now mock the function actually called.


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

#### D2 — Fire-and-forget job tasks can be GC'd; runs are unpersisted & unrecorded · **HIGH · ✅ PARTLY FIXED**

> **Resolved (task GC + run recording).** `run_job_endpoint` now retains a strong
> reference to the background task in a module-level `_background_tasks` set and
> drops it via a done-callback, so a running job can't be garbage-collected
> mid-execution. `run_job` appends a completion record to `discovery/_runs.jsonl`
> in a `finally` (every terminal outcome, success or failure), matching the
> `cadences/run_cadence.py` record shape plus additive `source: "api-job"` /
> `job_id` fields — so `GET /api/runs` and the dashboard `last_run` now include
> web-triggered runs. Covered by four new tests in `tests/api/test_jobs.py`.
> **Still open:** the `JobRegistry` remains in-memory, so live job *status* (not
> the run-history record) is still lost on a service restart — acceptable for the
> single-host posture but noted for a future durable-registry pass.


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

The intended use is a **shared LLM wiki — a unified knowledgebase that multiple
agents read from and write to.** Judged against *that* purpose, these are the
capabilities missing anywhere in the 27 routes or the CLI. (Note: this reframes
the priorities below — human-facing "reminders/notifications" are explicitly
**out of scope**; concurrency, retrieval quality, and a clean machine read/write
surface are what matter.)

- **Search is substring-only, metadata-only — the biggest limiter for agent
  readers.** `synapse.cmd_find` (`synapse.py:111`) is a plain `if q in hay` over
  concatenated slug/title/type/tags — the docstring calls it "fuzzy" but there is
  no ranking, no typo tolerance, and it **never searches the prose bodies**. An
  agent asking the wiki a question gets keyword-substring recall at best.
  _(→ [O4](#o4--agent-facing-retrieval-full-text--semantic--rag))_
- **No first-class agent write/contribute path with attribution.** Today the only
  writers are `ingest` (facts from `raw/`), the single `compiler` LLM touchpoint
  (prose), and `promote` (vocabulary). A *second* agent that wants to add
  knowledge must drop a file in `raw/` and wait for a pipeline pass, or hand-edit
  via `PATCH/PUT` — and **nothing records which agent wrote what.** A shared
  multi-writer KB needs a defined contribution API and per-actor attribution in
  the audit trail. _(→ [O5](#o5--agent-write-path--attribution))_
- **Auth cannot distinguish agents.** One static bearer token gates all 26 routes
  (`api/auth.py`). With N agents there is no per-agent identity, no read-vs-write
  scoping, and no way to attribute or revoke a single agent. _(→ [O6](#o6--per-agent-identity-scoping--audit))_
- **Index staleness under concurrent writers.** `_index.json` is rebuilt only by
  `build_index`; a write that doesn't trigger a rebuild leaves every reader
  (`find`/`show`/`/api/entities`) serving stale results. With multiple agents
  writing asynchronously there is no freshness contract or auto-rebuild hook.
  _(→ [O7](#o7--index-freshness--multi-writer-consistency))_
- **Single-host locking.** The `flock` mutex (`locking.py`) serializes writers
  **within one host**. Agents driving the HTTP service on one box are fine (jobs
  serialize in-process), but concurrent *direct file access* from multiple
  machines on a shared NAS mount would not serialize — worth an explicit
  "one writer host" note or an advisory-lock-over-HTTP story.
- **No backup / restore / export.** The only backup is `compact.py`'s `.bak`
  files for JSONL ledgers. A shared system of record has no vault snapshot, no
  export, no documented restore path. _(→ [O2](#o2--vault-snapshot--export))_
- **No schema migration/versioning.** `registry/schema.yaml` has no
  `schema_version`; a renamed/removed type silently invalidates existing
  entities with no upgrade tooling. _(→ [O8](#o8--schema-versioning--migration))_
- **No metrics/observability.** Only `uvicorn` request logs; no `/metrics`, no
  structured logging, no counters — thin for a service several agents depend on.
- **Doc drift:** `AGENTS.md` claims `OllamaClient` "has none [no test coverage]",
  but `tests/test_ollama_client.py` covers its parsing/error paths against a
  mocked transport (only a *live* server is, correctly, unexercised).
  `DOCS.md §2` is right; `AGENTS.md` is stale — reconcile.

### 2.5 Built-in agent (compiler) hardening — ✅ done

The single LLM touchpoint (`compiler.py`, the local-model "agent") got a
pure-code optimization pass — **no prompt/proposal-shape change, so
`PROMPT_CONTRACT_VERSION` stays 2.0** and the invariant is untouched:

- **Resilience:** `OllamaClient` now has bounded retry on *transient*
  connection/timeout faults only (the weekly cadence is the one LLM pass, so a
  momentary Ollama restart no longer silently drops entities); HTTP status
  errors and bad JSON surface clearly and are *not* retried; an Ollama `error`
  field (HTTP 200 model-not-found etc.) is surfaced verbatim; the response read
  is byte-capped.
- **Config-not-code:** `temperature`, `num_ctx`, retry count/backoff are env
  knobs; `num_ctx` now defaults to cover the whole source-char budget so raising
  `AGENT_VAULT_TOTAL_SOURCE_CHARS` for a big-context model isn't silently
  truncated at 8192.
- **Correctness:** guarded the empty-`sources_hash` recompile loop (a compiled
  entity missing its hash was treated as drifted *forever*, re-appending
  proposals and burning tokens every pass); proposals are now parsed only from
  the region after `</prose>` so prose quoting `<proposals>` can't mis-slice and
  drop a real proposal set; fixed the one bare `open().read()` fd leak in
  `find_pending`.
- **Coverage:** added tests for parse-response slicing, the drift guard, retry/
  HTTP-error/`error`-field paths, and env-configurable options.

_Still open (deferred, needs a contract bump): cross-source shingle dedup and
trimming the proposal scaffold in the system prompt — isolated for a future
`PROMPT_CONTRACT_VERSION` 2.1 so the audit trail stays clean._

---

## 3. Feature opportunities

**Framing:** Agent Vault is a **shared LLM wiki — a single knowledgebase that
multiple agents read from and write to.** So the yardstick is not consumer DMS/
inventory apps (human reminders, mobile capture, bill negotiation are *not* the
point); it is *how good an interface this is for a fleet of agents to query and
contribute knowledge, safely, concurrently, and auditably.* The survey of local-
RAG stacks (Ollama + Chroma/Qdrant/sqlite-vec) and self-hosted knowledge tools
(Paperless-ngx's API, MCP knowledge servers) informs the list below. The
deterministic-facts / no-hallucination core is the differentiator worth
protecting through all of it. Ordered by value to the multi-agent use case.

### O1 — MCP server: the native multi-agent read/write surface _(highest value)_
The most direct expression of "multiple agents read and write" is to expose the
vault as an **MCP server**, so any agent (Claude, a local model, another service)
gets first-class tools without hand-rolling HTTP: `vault.search`, `vault.get`,
`vault.list`, `vault.resolve_credential`, `vault.submit_source` (drop into
`raw/`), `vault.propose` (append a discovery proposal). It wraps the existing
service — no new invariant, no new writer — and turns the wiki into a
plug-in knowledge tool for a whole fleet. This is the unifying integration and
should anchor the roadmap.

### O4 — Agent-facing retrieval: full-text → semantic → RAG _(highest value)_
The current substring-over-metadata search is the biggest limiter for agent
readers. Three incremental tiers, all **read-side only** so the core invariant is
untouched:
1. **Full-text over prose bodies** with ranking — a SQLite FTS5 sidecar index (or
   a stdlib inverted index) built in `build_index.py`, still deterministic and
   rebuildable. Immediately fixes "search never looks inside the page."
2. **Semantic search** — Ollama is *already a dependency*; reuse it for
   embeddings (`nomic-embed-text`/`mxbai-embed-large`) into a local vector index
   (sqlite-vec/Chroma) so an agent's natural-language query recalls the right
   entities even without keyword overlap.
3. **Cited RAG answers** — upgrade `GET /api/ask` (today it answers from status/
   date math) to retrieve top-k via tiers 1–2, feed the same Ollama client, and
   return a grounded answer **that cites the source entities**. Keep it extractive
   + cited to stay honest to the no-hallucination ethos. This is the headline
   capability an agent expects from a shared KB.

### O5 — Agent write path + attribution
Define how a *second* agent contributes knowledge, and record who did. Today
writes are `ingest`/`compiler`/`promote` only; other agents must drop files in
`raw/` or hand-edit via `PATCH/PUT` with no actor record. Make the contribution
path explicit (an MCP/HTTP `submit_source` + `propose` that funnel into the
existing append-only, deterministic-commit pipeline) and **stamp every write with
an agent identity** in `discovery/*.jsonl` — the audit ledger already exists; it
just needs an `actor` field. Preserves the invariant (agents still only *propose*;
`promote` still commits) while making multi-writer provenance first-class.

### O6 — Per-agent identity, scoping & audit
Replace the single static bearer token with per-agent tokens (read-only vs
contributor scopes), so writes are attributable and one agent can be revoked
without rotating everyone. Add an access log for `POST /creds/{slug}/resolve`
(slug + actor + timestamp + outcome, never the secret) — with several agents able
to resolve secrets, this is the one endpoint where an audit trail matters most.
Pairs directly with O5's attribution.

### O7 — Index freshness / multi-writer consistency
Give the shared store a freshness contract so concurrent writers don't serve
stale reads: auto-rebuild `_index.json` (or incrementally update it) at the end of
any mutating pass / job, expose an index build-stamp via `/api/status`, and let
readers detect staleness. Document the single-writer-host boundary of the `flock`
mutex (or add an advisory-lock-over-HTTP path) for agents spread across machines.

### O2 — Vault snapshot / export
`agent-vault export --out vault-backup.tar.zst` (tar of `entities/` + `registry/`
+ `discovery/`, `--include-raw` optional), plus a matching import/restore and a
documented procedure. Table stakes for a shared system of record several agents
depend on; also the safety net before any O8 migration.

### O3 — Ingestion on-ramps (email + folder watch)
Agent Vault already parses `.eml` with attachment fan-out — add an IMAP poller
(→ `raw/email/`) and a watched-folder consume mode so knowledge flows in without
an agent copying files by hand. Pure orchestration around existing extractors.

### O8 — Schema versioning & migration
Add `schema_version` to `schema.yaml` and a migration shim (even a documented
procedure + a `reclassify_apply`-style bulk retype) so a breaking taxonomy change
has an upgrade path instead of silently invalidating entities — more pressing
when many agents share one evolving vocabulary.

### O9 — More importers & biller patterns _(content, not architecture)_
Cheap ongoing wins: more collection formats (Plex/Jellyfin), bank OFX/QFX for
statement ingest, and continued `patterns.yaml` growth. The "future work is
content" the README already anticipates.

### Fit against the actual purpose (shared multi-agent LLM wiki)

| Capability for a shared agent KB | Agent Vault today | Opportunity |
|---|:--:|:--:|
| Native agent tool interface (MCP) | ✅ (7 tools inc. search/ask) | ~~O1~~ ✅ |
| Full-text search of page contents | ✅ (FTS5 bm25) | ~~O4·1~~ ✅ |
| Semantic / NL retrieval | ✅ (embeddings + hybrid) | ~~O4·2~~ ✅ |
| Grounded, **cited** Q&A over the KB | ✅ (`/api/answer`, `vault_ask`) | ~~O4·3~~ ✅ |
| Defined agent write/contribute path | ✅ (`submit_source` + audit) | ~~O5~~ ✅ |
| Per-write **attribution** (which agent) | ✅ (`_access.jsonl`) | ~~O5·O6~~ ✅ |
| Per-agent identity / scoping / revoke | ✅ (token registry + scopes) | ~~O6~~ ✅ |
| Read-freshness under concurrent writes | ⚠️ (manual reindex) | O7 |
| Concurrency serialization (writers) | ✅ (single-host `flock`) | O7 (multi-host) |
| Deterministic, auditable, no-hallucination facts | ✅ | _protect_ |
| Referenced-secret credential vault (9 backends) | ✅ | O6 (audit) |
| Backup / export / restore | ❌ | O2 |

---

## 4. Recommended sequence

Ordered for a **shared multi-agent LLM wiki** — concurrency correctness first,
then the read/write interface agents actually need, then operational maturity.

1. ~~**D1** (recompile deadlock) + its integration test~~ — ✅ **done** (lock-free
   `_compile_all_locked` under the held lock; real-path regression test added).
2. ~~**D2** (job task GC + `_runs.jsonl` recording)~~ — ✅ **done** (strong task
   ref + `finally` run-history append; four tests). Durable job *registry* still open.
3. ~~**O4·1** (full-text search over prose)~~ — ✅ **done** (`agent_vault/search.py`:
   FTS5 sidecar, bm25 ranking, prose-aware, metadata fallback; `GET /api/search`;
   ranked `synapse find`). Unblocks O4·2/O4·3.
4. ~~**O1** (MCP server)~~ — ✅ **done** (`agent-vault-mcp`, optional `[mcp]`
   extra: `vault_search`/`get`/`list`/`status`/`submit_source`/`resolve_credential`;
   pure tested logic in `mcp_tools.py`, thin FastMCP wiring in `mcp_server.py`;
   append-only `submit_source` with actor attribution; opt-in secret resolution).
5. ~~**O5 + O6** (agent write path with attribution + per-agent identity/scoping)~~
   — ✅ **done**. O6: `auth.py` resolves each caller to an `Identity(actor,
   scopes)` from `VAULT_TOKEN` (legacy admin), `VAULT_TOKENS` env, or
   `registry/tokens.yaml`; scopes (`read`/`write`/`resolve`) gate by request
   shape (403 on missing scope, 401 on bad token), so a read-only agent can't
   write and only `resolve`-scoped tokens fetch secrets. O5: a pure-ASGI
   `AuditMiddleware` appends every write/resolve to `discovery/_access.jsonl`
   with the actor (never bodies/secrets) — closing the B5 "resolve unaudited"
   gap; MCP `submit_source` already stamps `_submissions.jsonl`.
6. ~~**O7** (index freshness / multi-writer consistency)~~ — ✅ **done**
   (`build_index.reindex()` is the single argv-free rebuild every mutating pass
   calls; the compile pass + recompile endpoint now reindex too, so status never
   reads stale; `GET /api/status` carries an `index` freshness block with a
   `stale` flag for the direct-file-edit edge). Also B1 (startup config
   validation), B8 (single index parse in list), B9 (race-guarded entity reads →
   404 not 500).
7. **O4·2 → O4·3** (semantic retrieval → cited RAG answers) — the biggest
   capability leap; sequence after the FTS index and MCP surface exist.
   - ~~**O4·2** (semantic + hybrid retrieval)~~ — ✅ **done** (`embeddings.py`
     pluggable embedder + offline MockEmbedder; `semantic.py` vector index
     (`_vectors.db`) + cosine + RRF hybrid; `search(mode=)`, `GET /api/search?mode=`,
     `vault_search(mode=)`).
   - ~~**O4·3** (cited RAG answers)~~ — ✅ **done** (`rag.py`: pluggable answerer
     + offline MockAnswerer; retrieval-grounded, citations verified against the
     retrieved context, `grounded` flag; `GET /api/answer` + `vault_ask` MCP tool).
8. **F1/F2** (top-level error boundary + SSE cancel/abort) — frontend robustness
   for the human operator/reviewer of the shared KB.
9. **O2 / O8** (backup+export, schema versioning) — safety net + upgrade path for
   a shared system of record.
   - ~~**O2** (vault snapshot / export / restore)~~ — ✅ **done**
     (`agent-vault-backup export|restore|list`: tar.gz of entities/registry/
     discovery, `--include-raw`; safe extraction rejects path traversal, refuses
     to overwrite a non-empty vault without `--force`, reindexes on restore).
10. Backlog: **O3** (mail/folder ingest), **B3** cadence tests, **F8** ESLint/CI,
    **O9** importers/patterns, doc-drift fixes.

_Explicitly out of scope for this system: human-facing reminders/notifications,
mobile capture, and consumer bill-tracking — Agent Vault is a knowledgebase for
agents, not a household-alerting product._

---

_Sub-audit method: two independent read-only agents covered backend and frontend
in parallel; the headline defect (D1) and the health snapshot were re-verified
by hand against the source. No code was changed._

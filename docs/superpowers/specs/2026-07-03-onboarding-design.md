# Onboarding Knowledge Base — Design Spec

- **Date:** 2026-07-03
- **Status:** Approved (awaiting implementation plan)
- **Owner:** ghively
- **Supersedes stale claims in:** `README.md`, `DOCS.md` (both predate the scripts→`agent_vault/` package refactor)

## Context

Agent Vault is a standalone, file-based household knowledge wiki with three surfaces (CLI, FastAPI
HTTP service, React UI) over one deterministic pipeline. The architecture is sound — the core
invariant ("the LLM writes prose **only** in `compiler.py`; everything else is deterministic Python;
the LLM proposes, deterministic code commits") holds under inspection, and the three-region entity
ownership and registry write-ownership map match what `AGENTS.md` documents.

The problem is **onboarding reliability**: the operational documentation has drifted from the code,
and a future coding agent that trusts it will issue commands that fail or edit the wrong files.
Examples already verified by analysis:

- `run_tests.sh` is referenced in `AGENTS.md:138` and `DOCS.md:425` but **does not exist**; the real
  runner is `pytest -v`.
- The `.claude/` automation suite described in `AGENTS.md:124-132` is **external to this repo**.
- `AGENTS.md:80` cites `server/jobs.py` (real path: `agent_vault/api/jobs.py`).
- `README.md` says contract `1.3` (actual `2.0`), "12 types" (actual 13), "6 CLI commands" (actual 8),
  and cites 4 test files that do not exist.
- `CLAUDE.md` (written 2026-07-02) contains a wrong command: `python -m agent_vault.run_cadence`
  (the runner lives at `cadences/run_cadence.py`, outside the package).
- The scripts→package refactor left **three live breakages** (cadence module paths, a dead
  secret-scan import, a broken `resolve` import — see TECH_DEBT P0).

Additionally, the deterministic core — the thing the "no hallucination" guarantee rests on — is
largely untested (`ingest.py`, 1721 LOC, has zero tests), and the frontend carries dead code plus a
latent theme bug. None of this is captured anywhere a future agent would find it.

## Goals

1. Future coding agents can orient in the repo accurately and quickly without breaking the core
   invariant or the ownership rules.
2. The current truth lives where agents look (auto-loaded `CLAUDE.md`, the `AGENTS.md` structure
   reference) and in two new focused documents.
3. Every technical-debt item is captured with a file:line, a severity, and a suggested direction,
   so remediation work can be planned without re-investigation.
4. The documentation we produce is itself accurate — every factual claim cited to `file:line`, and
   load-bearing claims live-verified before being stated as fact.

## Non-goals (explicitly out of scope)

- **No production code changes.** The three refactor breakages are catalogued, not repaired.
- **No new tests.** Test gaps are catalogued only.
- **No restructuring of `AGENTS.md`.** Content corrections only — external tooling (the `.claude/`
  vault-keeper suite) may parse it as a "source of truth."
- **No rewrite of the `README.md` / `DOCS.md` historical narratives.** Banner + key-fact fixes only.
- **No fixes to frontend dead code** (`App.tsx`, `MobileShell`, `TabbedApp`, i18n) — catalogued only.

## Decisions

- **Scope (chosen):** Documentation + surgical fixes to stale docs. No code/test changes.
- **Form (chosen):** Surgical corrections in place + two new standalone docs. (Rationale:
  `AGENTS.md` structure must be preserved for external consumers; the detailed map and the 60+ item
  debt list are too large for `CLAUDE.md`/`AGENTS.md`.)
- **README/DOCS handling (chosen):** Accuracy banner at the top of each pointing to
  `docs/ARCHITECTURE.md` as current truth + surgical fixes to concrete operational facts only.

## Deliverables

### New files

#### `docs/ARCHITECTURE.md` — the read-this-first map

Section plan:

1. **How to use this doc** — current truth; supersedes stale `README.md`/`DOCS.md` claims.
2. **System at a glance** — ASCII pipeline/layers diagram + one-paragraph thesis (deterministic core,
   single LLM touchpoint).
3. **The core invariant** — LLM proposes → deterministic commits; prose-only in `compiler.py`; the
   #1 thing not to break.
4. **Layers & data flow** — `raw/ → ingest → compiler → promote → review → build_index`; CLI, HTTP,
   and web surfaces.
5. **Data model** — three-region entity files; the `status` lifecycle
   (`stub → compiled`, drift recompile, `needs-review`/`unknown`/`archived`); registry files and the
   write-ownership map; on-disk formats (`raw/_manifest.jsonl`, `discovery/proposals.jsonl`,
   `discovery/promoted.jsonl`, `discovery/_runs.jsonl`, `_index.json`/`_index.md`).
6. **Python modules** — one tight paragraph each with entry-point signature: `ingest`, `compiler`,
   `promote`, `review`, `reclassify_apply`, `collections_importer`, `compact`, `build_index`,
   `synapse`, `validate`, `lint`, `secret_scan`, `locking`, `resolvers`.
7. **HTTP API** — full endpoint table (method/path/auth/params/response/driver); auth model (bearer
   scoped to `/api`, SPA served open, UI token gate); `Settings` flow; subprocess job model
   (`sys.executable -m <module>`, allowlist, `AGENT_VAULT_PATH` pin, SSE); static UI serving + SPA
   fallback.
8. **Frontend** — bootstrap (`main.tsx → TokenGate → Desktop`), the `wm/` shell, zustand stores
   (`windows`, `auth`), the `api/` layer (`vaultFetch`, hooks, mutations, jobs/SSE, resolve), the
   token gate, the 8 screens, and build/dev/prod.
9. **Build / test / CI** — accurate commands; what `.github/workflows/ci.yml` runs; the two-tier
   mypy/ruff policy (strict for `api/`, eased legacy scripts) with the exact per-module overrides.
10. **Invariants — "do not break"** — consolidated short list (the gate contract, region ownership,
    append-only `raw`/`discovery`, secrets-referenced-never-stored, `validate.py` ⇄ `promote.py`
    mirror, `AGENT_VAULT_PATH` pin, auth scoping).
11. **Current state** — seed/demo reality (`raw/_manifest.jsonl` empty, `discovery/` absent on disk,
    only 3 hand-written seed entities; the learning loop has never run against this tree).
12. **Known breakages** — pointer to `docs/TECH_DEBT.md` P0.
13. **External boundary** — `.claude/` automation suite is external to this repo; consumers
    integrate over HTTP+JSON only.

#### `docs/TECH_DEBT.md` — prioritized, dated remediation backlog

Structure: priority tiers P0→P3. **Every item: `file:line` + symptom + severity + suggested
direction**, plus a **"verify before fixing"** flag where the finding came from static analysis and
must be live-confirmed. Each tier:

- **P0 — live breakages (the package refactor fallout):**
  - Cadences call bare `python3 validate.py .` etc. but the scripts live under `agent_vault/`
    (`cadences/*.sh`, `cadences/run_cadence.py`). Must be `python -m agent_vault.<module>`.
  - `validate.py` and `promote.py` load the secret-scanner via `sys.path.insert(vault); import
    secret_scan`, which fails silently (the module is `agent_vault.secret_scan`) → the "no plaintext
    secrets" gate is silently disabled.
  - `agent-vault resolve` is broken from the CLI (`registry/resolvers.yaml` uses bare
    `module: resolvers.<scheme>`; the backend only imports as `agent_vault.resolvers.<scheme>`).
    **CLI failure already exec-verified by analysis.**
  - **VERIFY during execution:** whether the HTTP `POST /api/creds/{slug}/resolve` path is *also*
    affected (analyses disagreed). Likely yes in real deployment (the API test only passes because
    the fixture writes a top-level `<vault>/resolvers/test.py`). Settle by reading
    `resolvers/__init__.py::_import_backend` and running a live resolve against a seed entity.
- **P1 — correctness / security:** resolver dispatcher `sys.path.insert(vault)` → potential RCE via a
  malicious `<vault>/resolvers/*.py`; `jobs.py` passes caller `args` verbatim into subprocess argv
  with no leading-dash guard (the resolver layer has one); token compared with `!=` (not
  constant-time); frontend theme/density toggles update the store but never write the
  `data-theme`/`data-density` attribute to `<html>` → light/compact CSS is inert; false-confidence
  tests (`App.test.tsx` tests dead code and asserts 7 screens).
- **P2 — doc accuracy:** the full itemized stale-claims list (the `run_tests.sh`/`.claude/`/
  `server/jobs.py` references; README contract/counts/command/test-file claims; spec `_index.json`
  `facts:` example and resolver examples; `httpx2` vs `httpx` dev-dep; off-by-one "9 checks" vs 10
  lint kinds; etc.).
- **P3 — test gaps:** `ingest.py` (zero tests), `promote.py` registry-writers (only `decide()`
  covered), all 9 resolver backends, `reclassify_apply.py`, `lint.py`, `secret_scan.py`,
  `api/settings.py`, and frontend loaded-state coverage (Pipeline/Review/Wiki/Browse assert only the
  loading string). Plus code-quality: duplicated validation logic between `validate.py` and
  `promote.py`; `get_settings` duplicated ×6; magic strings/regexes duplicated across modules;
  frontend dead code (`App.tsx`, `MobileShell`, `TabbedApp`/`consolidation`, i18n stub); `patterns.yaml`
  scale (104 billers, linear scan).

### Edited files (surgical, content only)

#### `CLAUDE.md`
- Fix the `run_cadence` command: `python -m agent_vault.run_cadence daily .` →
  `python cadences/run_cadence.py daily .`.
- Add pointers to `docs/ARCHITECTURE.md` and `docs/TECH_DEBT.md`.
- Add caveats: the tree is a seed/demo (`raw/` empty, `discovery/` absent, 3 seed entities); three
  known refactor breakages exist (see TECH_DEBT P0); the `.claude/` suite is external.

#### `AGENTS.md` (content fixes only — preserve structure)
- `:80` — `server/jobs.py` → `agent_vault/api/jobs.py`.
- `:118-121` — the "Integration boundary (HTTP)" section understates the API (says only
  `/api/creds/{slug}/resolve` + `/api/health`); expand to reference the full surface, or point to
  `docs/ARCHITECTURE.md` §7.
- `:138-139` — `run_tests.sh` / "plain Python, not pytest" → `pytest -v`.
- `:124-132` — add a one-line note that the `.claude/` automation suite is **external** to this repo.
- Pipeline/cadence notes — add a known-issue pointer to TECH_DEBT P0 rather than restructuring.

#### `README.md` and `DOCS.md` (banner + key facts)
- Add a concise accuracy banner at the top of each: "This document predates the scripts→`agent_vault/`
  package refactor. For current truth see `docs/ARCHITECTURE.md`; known inaccuracies are tracked in
  `docs/TECH_DEBT.md` P2." Then list the highest-impact stale items inline.
- Fix concrete operational facts only (commands, counts, nonexistent files). Do not rewrite the
  Stage 0–6 historical narrative.

## Method (accuracy safeguards)

1. Read the full pipeline-core analysis (52 KB, previously only previewed) before writing
   `docs/ARCHITECTURE.md` §6.
2. **Live-verify** the load-bearing claims before stating them as fact:
   - `python -m agent_vault.validate .` — confirm exit code (expected 0 on shipped data).
   - `agent-vault resolve <seed-slug>` — confirm the CLI failure.
   - Read `resolvers/__init__.py::_import_backend` and run a live HTTP resolve against a seed entity
     to settle the disputed HTTP-resolve status.
   - `pytest -v`, `ruff check .`, `mypy agent_vault` — capture the *current* green/red state for
     TECH_DEBT.
3. Cite `file:line` for every factual claim in both new docs; mark anything still unverified as
   "VERIFY" rather than asserting it.

## Verify-before-trusting (findings that need live confirmation)

- HTTP `resolve` path status (P0) — disputed between analyses.
- Whether `pytest` / `mypy` / `ruff` are currently green (capture actual state, don't assume).
- `httpx2>=0.2` vs `httpx` (P2) — confirm whether the dev-dep is wrong or a compatibility shim.
- Frontend theme-attribute bug (P1) — confirm no code writes `data-theme`/`data-density` to
  `documentElement`.

## Success criteria

- A new coding agent reading only `CLAUDE.md` + `docs/ARCHITECTURE.md` can (a) run the correct
  build/test/serve commands, (b) state the core invariant and the ownership rules, and (c) avoid the
  three known breakages — without reading `README.md`/`DOCS.md`.
- Every command and count in `CLAUDE.md` and `AGENTS.md` is accurate against the code.
- `docs/TECH_DEBT.md` contains every item surfaced by the six subsystem analyses, each actionable,
  with disputed findings clearly flagged for verification.
- No production code, tests, or `AGENTS.md` structure are changed.

## Risks

- **Introducing new inaccuracies** in the very docs meant to fix them — mitigated by the
  file:line-citation rule and the live-verification step (Method §2). (One inaccuracy was already
  shipped in `CLAUDE.md` last turn — the `run_cadence` command — which this work corrects.)
- **External `AGENTS.md` consumers** — mitigated by preserving its structure and fixing content only.
- **Scope creep into code fixes** — mitigated by the explicit Non-goals; the P0 breakages are
  deliberately left for a follow-up effort.

## Next step

Invoke the `writing-plans` skill to produce a step-by-step implementation plan for authoring the two
new docs and applying the surgical corrections, with the live-verification tasks sequenced first.

# SPEC: Multi-Tenant Agent Vault (MTAV)

**Status:** Reviewed v1.0 — passed 3-round MoA review (Codex + Claude Code)
**Author:** Agent A (Hermes agent) for Gene
**Date:** 2026-07-05
**Base:** `ghively/agent-vault` @ 01d880e (post PR #6 merge)
**Goal:** Extend Agent Vault from single-vault to multi-tenant: many isolated
knowledge bases + a credential broker, behind one unified UI/service, with
token-scoped access to both knowledge and secrets.

---

## 1. Vision

Each agent gets a private **enclave** — its own knowledge base + secret refs —
auto-provisioned on first connect. Agents hold only a Vault token; the Vault is
the sole owner of the 1Password backend. A human (Gene) grants cross-enclave
access (shared secrets, shared KBs) to tokens as roles demand. Eventually a
**librarian** agent reads across all enclaves to reconcile drift and compile a
unified view — but it is explicitly Phase 3 (out of scope for this build).

Three properties must hold:
1. **Real isolation.** An agent writing to its KB physically cannot touch
   another agent's files or secrets. Isolation is filesystem + token-grant
   enforced, not logical/namepace.
2. **Unified UX.** One service, one port, one UI. The browser sees a workspace
   switcher, not a portal of links.
3. **Self-service onboarding with a scope ceiling.** A new agent connects,
   mints a token, gets a private enclave — zero human setup. But self-minted
   tokens are maximally restricted; elevating scope requires an existing
   elevated token.

---

## 2. Glossary

| Term | Meaning |
|------|---------|
| **Vault (instance)** | One running Agent Vault service process (this build stays at one process on vault-host) |
| **Enclave / KB** | One isolated knowledge-base tree (`entities/`, `raw/`, `discovery/`, `registry/`) on disk |
| **Token** | A bearer credential mapping to an `{actor, grants}` identity |
| **Grant** | A permission entry on a token: `{vault, scopes}` where scopes ⊆ {read, write, resolve, admin} |
| **Secret entity** | A KB entity with a `credential_ref:` URI resolved through the 1P backend |
| **Librarian** | A future read-mostly reconciler agent (Phase 3, out of scope here) |

---

## 3. Current State (post PR #6)

- One service, one KB at `~/.agent-vault/`, loopback `:7778`, systemd unit.
- Auth: one admin token (`VAULT_TOKEN`) + optional per-agent tokens
  (`registry/tokens.yaml`, `VAULT_TOKENS` env) mapping to `{actor, scopes}`.
- Scopes: `read`/`write`/`resolve`/`admin`. Gating on `/api/*` only.
- Credential resolve: `POST /api/creds/{slug}/resolve` calls the configured
  resolver (1Password via `op`), returns plaintext, never stores.
- KB pipeline: `raw/` → ingest → compiler (mock by default) → promote → review.
  FTS5 search, RAG answer, status/dashboard endpoints.
- Isolation model today: **none** — one global vault_path bound at startup.

What already takes `vault_path` as a parameter (enabling multi-tenancy without
rewriting the pipeline): every module in `agent_vault/*.py` (ingest, compiler,
search, resolve, validate, lint) plus every API handler that calls them.

---

## 4. Data Model

### 4.1 Enclave layout on disk

```
~/.agent-vaults/
  household/            # shared family knowledge (migrated from ~/.agent-vault)
  agent-a/              # Agent A agent private enclave
  agent-b/              # Agent B agent private enclave
  unified/              # librarian compile output (Phase 3, created empty now)
  _tenant/              # MTAV control plane (registry, NOT a KB)
    vaults.yaml         # enclave registry: name -> path + owner + visibility
    tokens.yaml         # token -> {actor, grants[], system?}
    grants.yaml         # (optional) human-managed grant overrides / shared-secret map
```

Each enclave is a complete vault tree (`entities/`, `raw/`, `discovery/`,
`registry/`, `_index.*`). The control plane (`_tenant/`) is the only thing
that is NOT a KB.

### 4.2 `vaults.yaml` — enclave registry

```yaml
# Human-authored + agent-extensible (auto-create on self-mint).
vaults:
  household:
    path: ~/.agent-vaults/household
    owner: gene            # human-owned
    visibility: shared     # readable by all authenticated tokens (read scope)
    description: "Household/family knowledge"
  agent-a:
    path: ~/.agent-vaults/agent-a
    owner: agent-a         # agent-owned (auto-created at first mint)
    visibility: private    # only tokens with an explicit grant
    description: "Agent A agent private KB"
  agent-b:
    path: ~/.agent-vaults/agent-b
    owner: agent-b
    visibility: private
  unified:
    path: ~/.agent-vaults/unified
    owner: librarian
    visibility: private
    description: "Librarian reconciled compile (Phase 3)"
```

`visibility`:
- `private` — requires an explicit grant on the token.
- `shared` — any authenticated token with `read` scope may read; write/resolve
  still need explicit grant.

### 4.3 `tokens.yaml` — token identity + grants

```yaml
tokens:
  # System bootstrap token (one-time, created at install, kept in 1P).
  # Has admin on every vault incl. control plane. Used to mint elevated tokens.
  mtav-bootstrap:
    actor: mtav-admin
    system: true
    grants:
      - {vault: "*", scopes: [admin]}    # all vaults, all scopes

  # Agent A's self-minted token. Minimal scope ceiling by default.
  # NOTE: tokens are stored HASHED in this file (sha256); 1P holds plaintext.
  agent-a-<hash>:
    actor: agent-a
    grants:
      - {vault: agent-a, scopes: [read, write, resolve]}   # own enclave only
      - {vault: household, scopes: [read]}                  # shared read (auto)
      # NO write on household — household is a separate, dedicated enclave
      # (see §5.4). Agent A maintains its own KB, not household.

  # Agent B's token.
  agent-b-<hash>:
    actor: agent-b
    grants:
      - {vault: agent-b, scopes: [read, write, resolve]}
      - {vault: household, scopes: [read]}
```

### 4.4 Secret entities + grants

Secrets are KB entities with a `credential_ref`. Resolution is gated by:
(1) the token has `resolve` scope on the vault where the entity lives, AND
(2) for cross-vault resolution, an explicit grant on that secret.

A secret grant is additive on the token:
```yaml
  agent-a-<random>:
    actor: agent-a
    grants:
      - {vault: agent-a, scopes: [read, write, resolve]}
      - {vault: household, scopes: [read]}
      - {vault: household, secret: tailscale-api-key, scopes: [resolve]}
```
This lets Gene give an agent access to one specific shared secret without
opening the whole vault.

---

## 5. Access Control Model

### 5.1 Scope semantics (per-vault)

| Scope | What it allows |
|-------|----------------|
| `read` | GET /api/* within the vault (search, status, entity reads, /answer) |
| `write` | POST/PUT/PATCH/DELETE within the vault (jobs, edits, reclassify) |
| `resolve` | POST /api/creds/{slug}/resolve within the vault (plaintext secrets) |
| `admin` | all above + manage the vault's own tokens/grants (NOT control-plane) |

`admin` on a vault lets that token mint/rotate tokens scoped **only to that
vault** (delegation, useful for a supervisor agent provisioning sub-agents).
`admin` on `vault: "*"` (bootstrap only) can touch any vault + the control
plane.

### 5.2 Self-mint scope ceiling (the load-bearing rule)

When an unauthenticated agent calls `POST /api/tenant/register`, it:
1. Must supply an **enrollment secret** (a pre-shared string in env
   `MTAV_ENROLL_SECRET`, rotated by Gene, NOT a token). This gates who can
   onboard at all — strangers can't self-mint.
2. Receives a **minimally-scoped token**: `read`+`write`+`resolve` on a newly
   created, empty private enclave named after the actor it declares.
3. Auto-gets `read` on every `visibility: shared` vault (household today).
4. Gets NOTHING else — no resolve on shared secrets, no access to other
   private enclaves, no control-plane.

This ceiling is the anti-escalation guarantee. An agent can do no damage
beyond its own empty enclave until a human (or a token with admin on the
target) elevates it.

### 5.3 Enforcement point

All access control is enforced in **one place**: a tenant-aware auth
dependency that replaces the current `create_auth_dependency`. It resolves
the token → identity → grants, then checks the request's target vault (from
`X-Vault` header or `/api/vaults/<name>/` path prefix) against the grants.
No endpoint checks scopes itself — the dependency is the only gate.

### 5.4 Household is a dedicated enclave (Gene's design decision)

Household is **not** a shared write surface that agents maintain. It is the
sensitive core — the reason the vault was built — and stands separate from
per-agent KBs:

- **Household** (`household/`) is curated by a **dedicated household agent**
  (or by Gene directly). It holds the sensitive family knowledge and
  credential refs that are the vault's primary purpose. Agent KBs do NOT
  write to it.
- **Per-agent KBs** (`agent-a/`, `agent-b/`, etc.) are each agent's private
  working space — their own research, notes, state, scratch knowledge. They
  are peers to household, not subordinates that merge into it.
- Agents get **read** on household by default (so they can reference shared
  facts, look up accounts, resolve shared secrets they're granted), but
  **never write** unless Gene explicitly grants it for a specific role.

This means the self-mint ceiling (§5.2) grants `read` on household only —
no agent gets household `write` through self-service. The household agent
itself is provisioned with an explicit `write` grant by Gene.

The Librarian (Phase 3) reads across all enclaves including household but
writes only to `unified/` — it never merges agent KBs into household.

### 5.5 Resolved design decisions (Gene, 2026-07-05)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Token storage | **Hashed** (sha256) in tokens.yaml; 1P holds plaintext | File read doesn't leak tokens; 1P is recovery path. Avoid bcrypt (lookup must scan all entries; sha256 is fine since tokens are high-entropy 40+ chars). |
| Delegation depth | **Flat** — only bootstrap + vault-admin tokens can mint | Prevents privilege sprawl. Revisit only if a real supervisor-agent use case emerges. |
| Agent-authored secrets | **Configurable**, default OFF (`MTAV_REQUIRE_SECRET_APPROVAL=0`) | Auto-approve + rate-limit + audit-flag for trusted agents (Agent A, Agent B). Flip ON when onboarding less-trusted agents. |
| Household write access | **Dedicated enclave** — agents get read only; a separate household agent (or Gene) curates it | Household is the sensitive core, not a shared write surface. See §5.4. |
| Enrollment secret rotation | **On-demand only** (no fixed cadence) | Minimal-scope ceiling means a leaked enroll secret only produces empty private enclaves — low blast radius. Rotation adds operational complexity for little gain. |

---

## 6. API Surface Changes

### 6.1 Vault routing (PRECISE — resolves §6.1/§6.4 contradiction)

Every `/api/*` request must identify its target vault. Resolution order:

1. **Explicit header:** `X-Vault: <name>` → use that vault (must be in grants).
2. **Explicit path prefix:** `/api/vaults/{vault}/...` → use that vault.
3. **Default fallback:** the request's **default vault**, defined as:
   - If the token has a grant with `scopes` including `write` on exactly one
     vault → that vault.
   - Else if the token has `admin` on `vault: "*"` (bootstrap) → `household`.
   - Else → the first `read`-granted vault in registry order, or `household`
     if none (which a token with zero grants will then 403 on, cleanly).

This gives: bootstrap/Hermes clients with no header → `household` (backward
compat); self-minted agents with no header → their own private enclave (their
only write grant); multi-vault agents must send the header. One rule, no
contradiction, deterministic for every token shape.

### 6.2 New endpoints (control plane)

| Method | Path | Scope | Purpose |
|--------|------|-------|---------|
| `POST` | `/api/tenant/register` | enrollment secret | Self-mint a token + create private enclave |
| `GET` | `/api/tenant/whoami` | any token | Return actor + grants (for UI + agents) |
| `GET` | `/api/tenant/vaults` | any token | List vaults the token can see |
| `POST` | `/api/tenant/tokens` | admin on target vault | Mint a delegated token scoped to that vault |
| `DELETE` | `/api/tenant/tokens/{id}` | admin or self | Revoke a token |
| `POST` | `/api/tenant/grants` | admin on target vault | Add a grant to a token. **SI-1: requested grant MUST be a subset of the caller's own grants on that vault.** A vault-admin cannot grant scopes it doesn't hold, cannot grant access to other vaults, and cannot mint tokens broader than its own. Bootstrap (`*`) is the only exception. |
| `DELETE` | `/api/tenant/grants/{id}` | admin on target vault | Remove a grant. **SI-6: takes effect next request (cache invalidation).** |

### 6.3 Cross-vault search (Phase 2 UI, Phase 1 API)

`GET /api/search?q=...&vaults=agent-a,household` — fan-out FTS across the
listed vaults, merge-ranked. Restricted to vaults the token has `read` on;
others silently dropped.

### 6.4 Backward compatibility

Existing single-vault calls (no `X-Vault`, no path prefix) resolve via the
§6.1 default-fallback rule. For the bootstrap token (the one in the existing
Hermes `.env` `AGENT_VAULT_TOKEN`), this resolves to `household`, so the
current UI + Hermes env keep working unchanged through the migration. This is
*one* rule (§6.1), not a separate compat path — no contradiction.

---

## 6a. Entrypoint Tenant-Scoping (covers Jobs, MCP, Cadences)

Every code path that reads or writes vault state must resolve a target vault.
Round 1 review flagged that jobs, MCP, cadences, run-history, config, and
index-building were unspecified. This section closes those gaps.

### 6a.1 Jobs (api/jobs.py) — the highest-privilege write surface

**Problem:** `run_job` (jobs.py:305) builds a subprocess command and passes
`AGENT_VAULT_PATH=<global settings.vault_path>` via `_vault_env` (jobs.py:254).
Under MTAV this is wrong — a job must run against the request's target vault.

**Fix:** The tenant router resolves the target vault *before* the job endpoint
runs (§5.3 enforcement point). The job endpoint receives the resolved
`vault_path` (injected via the request's resolved identity, not read from
global settings). `_vault_env` and `_record_run` use that resolved path.
`ALLOWED_OPS` validation is unchanged (the ops themselves are vault-agnostic).

The job subprocess (`python -m agent_vault.<module>`) already takes the vault
dir as argv — so passing the resolved path is a one-line change in
`_build_command` / `_vault_env`. The search index built by a job lands in the
correct vault tree automatically.

### 6a.1a Job ownership + cross-tenant isolation (R3 finding)

**Problem:** `JobRegistry` (jobs.py:145) is an in-memory dict keyed only by a
12-hex job_id. `GET/DELETE/stream /jobs/{job_id}` (jobs.py:419,453,482) do no
grant check — any authenticated token that learns a job_id (via a shared log,
browser history, or colluding process) can poll, cancel, or stream a job it
didn't start. This breaks the §1 isolation property.

**Fix:** `Job` gains a `vault: str` field set at creation. The three
job-control endpoints resolve the caller's grants and 403 if the caller has
no grant on `job.vault`. Jobs are also evicted only from the caller's visible
vaults in registry listing. This is a small additive change (one field + one
check per endpoint) and is part of Phase 1, not deferred.

### 6a.2 MCP server (mcp_server.py / mcp_tools.py)

**Problem:** MCP tools (`vault_search`, `vault_ask`, etc.) currently read a
single global vault. Under MTAV they must be tenant-aware.

**Fix:** MCP authentication gains an optional bearer token (FastMCP supports
custom auth). The token resolves to an identity + grants exactly as HTTP
does. Each tool takes an optional `vault` arg; if omitted, the §6.1
default-fallback rule applies. The `submit_source` write tool requires `write`
on the target vault. `vault_resolve_credential` requires `resolve` on the
target vault — this closes the MCP gap Claude flagged (MCP was entirely
outside the grant model).

MCP is a Phase 2 concern (not blocking Phase 1 isolation). Phase 1 ships
with MCP disabled or single-vault-pinned to household; Phase 2 makes it
tenant-aware.

### 6a.3 Cadences (daily/weekly/monthly + run_cadence.py)

**Problem:** Cadence scripts call `python -m agent_vault.<module> .` against
one vault dir. Under MTAV, each vault needs its own cadence schedule.

**Fix:** Cadences become **per-vault**. The systemd timer (or cron) runs the
cadence with `AGENT_VAULT_PATH=<vault>` for each registered vault. A new
helper `run_cadence.py --all` iterates `vaults.yaml` and runs the cadence
against each vault sequentially (not in parallel — vault locks are per-tree
but CPU/IO should serialize for a homelab). Household gets daily+weekly;
private enclaves get daily only (lighter — they're smaller). This is config
(deploy), not code — the cadence scripts already take the vault dir as argv.

### 6a.4 Run history, config/settings, semantic/RAG, index building

All of these already take `vault_path` as a parameter internally. Under MTAV:
- **Run history** (`_runs.jsonl`) lives in each vault's `discovery/` — already
  correct, one log per vault.
- **Config/settings endpoints** (`/api/settings`) report per-vault settings
  for the resolved vault.
- **Semantic/RAG** (`/api/answer`) queries the resolved vault's `_vectors.db`
  + `_index.db`.
- **Index building** (`build_index`) writes to the resolved vault's tree.

No code changes beyond routing the resolved `vault_path` through — which is
exactly what the §5.3 enforcement point does for every endpoint.

---

## 6b. Settings Evolution (config.py)

**Problem:** `Settings` (config.py:14) is a frozen dataclass with a single
`vault_path: str`. Under MTAV it must hold a vault *registry*, not one path.

**Fix:** `Settings` gains MTAV fields while preserving backward compat:

```python
@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 7778
    vault_path: str = "."          # KEPT — single-vault mode + bootstrap vault
    token: str = ""
    # MTAV (Phase 1+):
    multi_tenant: bool = False     # AGENT_VAULT_MULTI_TENANT=1
    vaults_root: str = ""          # MTAV_VAULTS_ROOT (~/.agent-vaults)
    enroll_secret: str = ""        # MTAV_ENROLL_SECRET
    require_secret_approval: bool = False
```

- When `multi_tenant=False`: behavior is identical to today (single
  `vault_path`). Zero risk to existing deployments.
- When `multi_tenant=True`: `vault_path` is ignored for routing; the tenant
  router reads `vaults.yaml` from `<vaults_root>/_tenant/` and resolves per
  request. `vault_path` is kept only as the bootstrap fallback for the
  fail-closed `_guard_open_bind` check (serve.py:23), which now validates
  that `vaults_root` exists and contains at least one vault.
- `validate()` (config.py:59) gains: if multi_tenant, assert `vaults_root`
  is a dir + `_tenant/vaults.yaml` exists + at least one vault registered.

`load_token_registry` (auth.py:70) evolves into `load_tenant_registry`
which reads `_tenant/tokens.yaml` + the legacy `VAULT_TOKEN`/`VAULT_TOKENS`
sources (backward compat), producing the grant-aware identity model.

---

## 7. Credential Broker (the reason agents never touch 1P)

### 7.1 Architecture

```
agent --(token)--> Vault :7778 --(op)--> 1Password --> plaintext back to agent
                      |
                      +-- if agent STORES a new secret, Vault writes the
                         material to 1P and stores only the ref in the KB.
```

The Vault process holds the single `OP_SERVICE_ACCOUNT_TOKEN`. Agents never
see it. Resolve checks token grants before calling `op`.

### 7.2 Agent-authored secrets (gated)

An agent with `write`+`resolve` on its enclave MAY create a secret entity.
The flow:
1. Agent `POST /api/entities/{slug}` (or raw editor) with a proposed secret
   value in a special `credential_value:` field.
2. Vault does NOT write the value to the KB. Instead it writes the material
   to 1P (new item, or update), stores only the `credential_ref:` in the entity.
3. Audit logs both the store and every subsequent resolve.

To prevent the exfiltration channel discussed (agent encodes sensitive data
as a "secret" for a colluding foothold to resolve), secret-creation is
**rate-limited** and **audit-flagged** for human review. Gene can set
`MTAV_REQUIRE_SECRET_APPROVAL=1` to force propose→promote on secret creation.

### 7.3 Secret grants (cross-enclave sharing)

A secret lives once, in its owner's enclave. To share it without duplicating:
```
POST /api/tenant/grants
{token: "agent-a-xxx", vault: "agent-b", secret: "tailscale-key", scopes: ["resolve"]}
```
The resolve path checks: token has `resolve` on `{vault, secret}` OR
`resolve` on `{vault}` (whole-vault).

---

## 8. Self-Service Onboarding Flow

```
1. Operator (Gene) sets MTAV_ENROLL_SECRET once (stored in 1P, in env).
2. New agent connects:
   POST /api/tenant/register
   {enroll_secret: "...", actor: "researcher-7"}
3. Vault:
   a. validates enroll_secret
   b. creates ~/.agent-vaults/researcher-7/ (full vault tree)
   c. mints token T with grants: [{researcher-7: [read,write,resolve]},
                                  {household: [read]}]
   d. registers vault in vaults.yaml (visibility: private, owner: researcher-7)
   e. returns {token: T, vault: "researcher-7", addr: "http://127.0.0.1:7778"}
4. Agent stores T, starts working immediately in its enclave.
5. Gene later:
   POST /api/tenant/grants {token: T, vault: "household", scopes: ["write"]}
   — or via the UI grant manager.
```

---

## 8a. Librarian (Phase 3 — DO NOT BUILD NOW, spec only)

A librarian is a token with `read` on every private enclave + `write` on
`unified`. It diffs corresponding entities across enclaves, flags conflicts
(Agent A says gateway=X, Agent B says gateway=Y), compiles a merged view into
`unified/`, and surfaces a reconciliation queue to Gene. It NEVER writes into
an agent's private enclave. Explicitly deferred — but the data model
(provenance = owner field on every entity, cross-vault search API) is built
now to make it possible later.

---

## 9. UI Changes

### 9.1 Workspace-aware shell

The React shell gains:
- **Vault switcher** (sidebar dropdown): lists vaults the token can see;
  switching sets `X-Vault` on all subsequent API calls.
- **Cross-vault search** bar: query fans out to selected vaults, results
  tagged with source vault + owner.
- **Grant manager** (admin only): table of tokens → grants, add/remove.
- **Whoami indicator**: shows the logged-in actor + its grants.

The same app, same port. No portal-of-links.

### 9.2 Enrollment screen

A "Register new agent" form (enroll secret + actor name) that calls
`/api/tenant/register` and displays the minted token once.

---

## 10. Migration Plan

### 10.1 Data migration (one-time, scripted)

```
~/.agent-vault/  -->  ~/.agent-vaults/household/
```
- Move the tree.
- Write `vaults.yaml` with `household` + empty `agent-a`/`agent-b`/`unified`.
- Convert the existing `VAULT_TOKEN` to the bootstrap admin token in
  `tokens.yaml` (system: true, grants: ["*": admin]).
- The existing Hermes `.env` `AGENT_VAULT_TOKEN` becomes the bootstrap token
  (backward compat: no X-Vault header → household vault).

### 10.2 Deployment migration

- systemd unit: add `MTAV_ENROLL_SECRET`, `MTAV_VAULTS_ROOT=~/.agent-vaults`.
- New env: `AGENT_VAULT_MULTI_TENANT=1` enables the tenant router. Off by
  default → single-vault behavior preserved for anyone else running this.

### 10a. Safe Migration Procedure (R3 finding — copy + verify + cutover)

The §10.1 migration must NOT be a raw `mv`. It must be copy + verify + cutover
with a rollback path, because a failed halfway move leaves the service
pointing at a nonexistent path and no rollback.

**Procedure** (implemented as `agent-vault-migrate-to-mtav` script):

1. **Pre-checks:** service stopped; `~/.agent-vault` exists and validates;
   target `~/.agent-vaults/` does not yet exist (or is empty).
2. **Copy** `~/.agent-vault` → `~/.agent-vaults/household` (full tree copy,
   preserving mtimes). Do NOT delete the source.
3. **Write control plane:** create `~/.agent-vaults/_tenant/{vaults.yaml,
   tokens.yaml}` with household registered + bootstrap token converted.
4. **Validate** the new tree: `python -m agent_vault.validate
   ~/.agent-vaults/household` must exit 0. If it fails, abort — source is
   untouched.
5. **Dry-run the service** against the new layout with
   `AGENT_VAULT_MULTI_TENANT=1`, confirm health + one read.
6. **Cutover:** update systemd unit env, restart service.
7. **Keep `~/.agent-vault` as backup** for 7 days (or until Gene confirms),
   then remove. The migration script leaves a `.migrated-on-<date>` marker.

**Rollback:** stop service, revert systemd env, `cp -a
~/.agent-vaults/household ~/.agent-vault` (or just point at the preserved
source), restart. The source is never deleted during cutover, so rollback is
always one config change away.

---

## 11. Security Posture & Threat Model

### 11.0 Security Invariants (MUST hold — verified by tests, enforced in code)

These are the non-negotiable rules surfaced by Round 2 MoA review. Each maps
to a test in the Phase 1+2 test suite.

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| **SI-1** | A delegated token/grant minted by an admin-on-vault token MUST be a subset of the minter's own grants on that vault. | Grant API validates `requested_grant ⊆ minter_grants` before writing. Reject + audit if not. |
| **SI-2** | Vault names and actor names are validated slugs (`^[a-z0-9][a-z0-9._-]{0,63}$`); vault path is ALWAYS resolved by registry lookup, NEVER by string-joining client input. | Tenant router: `X-Vault`/actor → registry → path. No client input reaches `os.path.join` for a vault path. |
| **SI-3** | Every filesystem path the server reads or writes MUST resolve (via `os.path.realpath`) under the target vault's root. | Applied at: entity reads, raw writes, ingest/compile input, job cwd, resolver store_dir. Symlinks that escape → rejected. |
| **SI-4** | Secret plaintext NEVER touches disk, the FTS/vector index, compiler input, job logs, or the audit log. The ONLY path to plaintext is `POST /api/creds/{slug}/resolve` with `resolve` scope. | `credential_value:` is intercepted at the write boundary (never written to the entity file); resolved values are returned in-memory only. |
| **SI-5** | Grant check happens BEFORE existence check. A denied request and a nonexistent target return the same error class (403) with no distinguishable timing or message. | Resolve/search/detail endpoints check grants first; constant error shape. |
| **SI-6** | Token registry changes (mint, revoke, grant, ungrant) take effect on the NEXT request — the registry is reloaded per request or is an in-memory store mutated atomically. | `load_tenant_registry` becomes a cached-but-invalidated store; mutation endpoints invalidate the cache. Concurrent writes use a file lock (`_tenant/.lock`). |
| **SI-7** | Agent-authored secrets in 1P are namespaced under `AgentVault/<vault>/<slug>` and may NEVER update an item outside that prefix. | The 1P write path enforces the prefix; existing items outside it are read-only. |
| **SI-8** | `/api/tenant/register` is rate-limited (max N registrations per IP per window), capped on total enclaves (configurable `MTAV_MAX_ENCLAVES`, default 20), and idempotent on actor name (re-register returns existing token if same enroll_secret, else 409). | Register endpoint + config. |

### 11.1 Threat model

| Threat | Mitigation |
|--------|------------|
| Compromised agent mints admin token | Self-mint ceiling (§5.2): self-minted tokens are minimal; only bootstrap/elevated tokens can grant up |
| Delegated admin grants beyond own scope | **SI-1**: grant API enforces `requested ⊆ minter` subset invariant |
| Path traversal via X-Vault / actor name | **SI-2**: registry lookup only, never string-join client input; slug validation |
| Symlink escape from enclave | **SI-3**: `realpath` containment on every read/write path |
| Secret plaintext leaks via search/RAG/logs | **SI-4**: plaintext never touches disk/index/logs; resolve is the only path |
| Existence oracle (403 vs 404 probing) | **SI-5**: grant check before existence check; constant error shape |
| Stale registry after revoke | **SI-6**: per-request reload or cache invalidation; atomic file writes |
| 1P namespace collision / item overwrite | **SI-7**: `AgentVault/<vault>/<slug>` prefix enforced on writes |
| Register spam / enclave flooding | **SI-8**: rate-limit + `MTAV_MAX_ENCLAVES` cap + idempotency |
| Compromised agent resolves secrets outside its grant | Resolve checks token grants per (vault, secret); 1P unreachable from agents directly |
| Vault host compromised | Vault host (vault-host) is a tier-1 trust anchor; tight egress, dedicated 1P service account scoped to only what the vault needs, documented rotation path |
| Exfiltration via agent-authored "secret" | Rate-limit + audit-flag secret creation; optional propose→promote gate (`MTAV_REQUIRE_SECRET_APPROVAL`) |
| Token leak | Tokens are random 40+ chars, stored hashed in tokens.yaml (plaintext only in 1P + agent env); revocation via DELETE grant/token |
| Enrollment secret leak | `MTAV_ENROLL_SECRET` rotated by Gene; rate-limit register endpoint |
| Privilege escalation via grant API | Grant API requires admin on the *target* vault; **SI-1** subset check; bootstrap required for control-plane writes |
| Concurrent registry corruption | **SI-6**: file lock on `_tenant/.lock` around all registry mutations |

### 11.1 Trust anchors

- **vault-host** (Vault host): tier-1. Compromise = compromise of 1P write + all
  enclaves. Must be hardened (also runs other self-hosted services on the same box).
- **Bootstrap token**: tier-1. Lives in 1P + Hermes env only.
- **Enrollment secret**: tier-2. Gate on onboarding, not on data access.

---

## 12. Phased Rollout

### Phase 1 — Multi-vault router + per-vault auth (core isolation)
- Tenant-aware auth dependency + vault router (X-Vault / path prefix)
- `vaults.yaml`, `tokens.yaml` with grants
- **Reloadable tenant registry** (SI-6): cache-invalidated store, not a
  boot-time closure — built now so Phase 2 mutation endpoints extend it
  rather than rewrite it
- Migrate `~/.agent-vault` → `~/.agent-vaults/household` (copy + verify +
  cutover, NOT raw move — see §10a)
- Audit middleware rewritten to read the resolved vault from ASGI scope
  state (not the static app-construction-time vault_path)
- Job registry gains a `vault` field + grant check on GET/DELETE/stream
  (prevents cross-tenant job control — see §6a.1a)
- Cross-vault search API
- Tests: isolation, grant enforcement, backward compat, SI-1..SI-8
- **Deliverable:** two real vaults (household + agent-a) behind one service

### Phase 2 — Credential broker + self-service onboarding
- Vault holds `OP_SERVICE_ACCOUNT_TOKEN`; agents never see it
- `/api/tenant/register` with enrollment secret + scope ceiling (SI-8)
- `/api/tenant/grants` (add/remove, per-vault + per-secret, SI-1 subset check)
- Agent-authored secret store → 1P (gated + audited, SI-4 + SI-7)
- MCP made tenant-aware (§6a.2)
- UI: grant manager, enrollment screen
- **Deliverable:** Agent B onboarded via self-service; shared secrets granted

### Phase 3 — Librarian (future, spec only)
- Read across private enclaves, write to `unified` only
- Conflict detection, reconciliation queue
- Unified browse view for Gene

---

## 13. Configuration Summary

| Env var | Default | Purpose |
|---------|---------|---------|
| `AGENT_VAULT_MULTI_TENANT` | `0` | Enable MTAV mode (single-vault if off) |
| `MTAV_VAULTS_ROOT` | `~/.agent-vaults` | Root dir for all enclave trees |
| `MTAV_ENROLL_SECRET` | (none, required if MTAV on) | Pre-shared onboarding secret |
| `MTAV_REQUIRE_SECRET_APPROVAL` | `0` | Force propose→promote on secret creation |
| `OP_SERVICE_ACCOUNT_TOKEN` | (existing) | The ONE 1P token, held by Vault only |
| `AGENT_VAULT_TOKEN` | (existing) | Becomes bootstrap admin token under MTAV |

---

## 14. Open Questions — RESOLVED (2026-07-05, Gene)

All open questions from the draft have been resolved. See §5.5 for the
decision table. Summary:

1. ~~Scope ceiling completeness~~ → SI-1..SI-8 (§11.0) close all paths.
2. ~~Backward compat~~ → §6.1 unified rule (no contradiction).
3. ~~Threat model gaps~~ → §11.0 + §11.1 threat table expanded.
4. ~~Secret-store gating~~ → **Configurable, default OFF** (§5.5).
5. ~~Token storage~~ → **Hashed (sha256), 1P holds plaintext** (§5.5).
6. ~~Librarian feasibility~~ → Data model (§4 + §8a) enables it without rewrite.
7. ~~Delegation chains~~ → **Flat** (§5.5): bootstrap + vault-admin only.
8. ~~Cross-vault search performance~~ → Acceptable at homelab scale (≤10 vaults).

---

## Appendix: MoA Review Record (3 rounds, 2026-07-05)

**Reviewers:** Codex (codex-cli 0.142.5) + Claude Code (2.1.201), fanned out
in parallel each round. Codex's sandbox couldn't execute in the env but
reasoned from spec + code honestly; Claude verified every finding against
source with line numbers.

### Round 1 — Scope/Completeness (CONVERGENT)
Both flagged the same three gaps:
1. Jobs, MCP, cadences outside the tenant model → **fixed: added §6a**
2. §6.1 vs §6.4 default-vault contradiction → **fixed: unified §6.1 rule**
3. Settings/config evolution unspecified → **fixed: added §6b**

### Round 2 — Security/Access Control (CONVERGENT)
Both flagged the same three critical issues:
1. Grant/token-mint delegation escalation (no subset invariant) → **fixed: SI-1**
2. Path traversal via unvalidated client input → **fixed: SI-2, SI-3**
3. Stale token registry / no revocation + no locking → **fixed: SI-6**
Plus: secret plaintext leakage (SI-4), existence oracle (SI-5), 1P namespace
collision (SI-7), register DoS (SI-8). All addressed in §11.0.

### Round 3 — Implementability/Integration
Both confirmed Phase 1 is buildable as an additive change (no rewrite) but
flagged three concrete integration risks, all addressed:
1. Audit middleware bound to static vault_path at app construction → **fixed:
   Phase 1 rewrites it to read ASGI scope state**
2. Job registry has no vault/ownership field → **fixed: §6a.1a**
3. Token registry is a boot-time closure, not reloadable → **fixed: Phase 1
   builds the reloadable store (SI-6) now so Phase 2 extends it**

### Verdict
Spec is **buildable as-phased**. Phase 1 is additive (new tenant layer +
systematic per-request vault_path threading), not a rewrite. All 8 security
invariants are testable. Migration is copy+verify+cutover with rollback.
Ready for implementation.

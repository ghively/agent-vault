# LLM Wiki ("Agent Vault") — Complete Taxonomy & Operational Build Guide

> Companion to `llm-wiki-schema-spec.md` (the constitution). That document defines the *file format and ownership rules*. This document defines **the default taxonomy** (researched, not guessed) and **the full build order** — every component, in the sequence to build it.
>
> Harness-independent by design. Nothing here depends on Hermes, OpenClaw, cron, or any specific runtime. The contract is the files.

> **This is a pre-implementation planning document** — the build order it describes is history now.
> All the stages it plans shipped, **and the vault has since grown well beyond this 7-stage plan**:
> a full retrieval stack (FTS5 full-text → embedding/semantic → cited RAG answers), an MCP server
> for multi-agent read/write, per-agent auth scopes + an access audit, backup/restore, and schema
> versioning. This document has no notion of those layers — do not treat its roadmap as the full
> system. Hermes/OpenClaw were candidate runners considered at design time; the repo ships
> `cadences/*.sh` + `cadences/run_cadence.py` instead, runner-agnostic as intended. For current,
> accurate operational docs use [`README.md`](./README.md), [`DOCS.md`](./DOCS.md),
> [`AGENTS.md`](./AGENTS.md), and [`docs/AUDIT.md`](./docs/AUDIT.md) — not this file.

---

## Part 1 — The Default Taxonomy (researched)

### 1.1 The core insight from prior art

Every serious home-inventory and asset system converges on the same structural move, and it's the one most people get wrong: **separate what a thing *is* (its type) from where it is, what state it's in, and when things happen to it.** Those last three are *attributes*, not categories.

A water heater is not a "garage thing." It is an **asset** that currently *lives in* the garage, is *owned*, was *purchased* on a date, and has a *warranty* expiring on another date. Rooms get rebuilt, things move, warranties lapse — if you bake location or status into the top-level category, you reorganize the whole wiki every time life changes. So:

- **Types** = the stable backbone. What a thing fundamentally is. Small, slow-growing, mostly human-gated.
- **Attributes** = cross-cutting dimensions every entity can carry. Location, status, dates, value, tags. These do the expressive work.

This is why the top level stays small and the system still describes everything. The home-inventory apps that sprawl into 40 categories did it by promoting attributes to categories. We won't.

The second universal pattern: **the asset and the proof about it are different entities.** Your dishwasher is one entity; its receipt, its manual, and its warranty are separate document entities *linked* to it. This is what lets one receipt cover three items, or one warranty claim pull up the manual and the purchase proof together.

### 1.2 The top-level types (the default backbone)

Twelve types. This is deliberately the complete set — expansive enough to swallow everything you listed and more, small enough to stay legible. (We arrived here by *removing* one — `license` collapsed into `document` — and *adding* one — `collection` for catalog media; net still twelve, and simpler for it.) Each has `human_gated` set per the §5 promotion rules in the spec.

| Type | What lives here | Example subtypes | Gated? |
|------|----------------|------------------|--------|
| **asset** | Singular things you own with value/lifecycle (physical *or* intangible) | appliance, electronics, computer, furniture, tool, hvac, fixture, jewelry, collectible, equipment, software | no |
| **vehicle** | Anything that drives/rolls/flies | car, truck, motorcycle, trailer, rv, boat, ebike, lawn-equipment | no |
| **property** | Real estate & the structure itself | residence, land, structure, room, outbuilding | yes |
| **financial-institution** | Where money/credit lives | bank, credit-union, brokerage, lender, insurer | yes |
| **account** | A specific account/login/policy | checking, savings, credit-card, loan, investment, insurance-policy, utility-account, login | no |
| **vendor** | An entity you pay or buy from | utility, streaming, subscription, insurance, retailer, service-provider, contractor, hoa | no |
| **collection** | Catalog media you browse/filter rather than track one-by-one | game, music, film, book, tv, comic | no |
| **document** | Proof, records, paperwork (incl. the rights/keys that prove entitlement) | receipt, warranty, manual, contract, statement, id-document, tax, medical, resume, certificate, photo-scan, license | no |
| **media** | Personal media files (yours, not catalog) | family-photo, video, screenshot, audio | no |
| **person** | Humans (household + contacts) | household, contact, professional, emergency | yes |
| **maintenance** | Service events & schedules | service-record, repair, inspection, recurring-task, recall | no |
| **event** | Time-anchored happenings | purchase, claim, milestone, appointment, incident | no |

**Why each earns top-level status** (the reasoning, so you can argue with any of it):

- **asset** is the heart of a home wiki — appliances, computers, tools, electronics. Subtype carries the variety so the type stays one thing. `computer` is a subtype, not its own type, because it behaves exactly like every other asset (has a serial, a warranty, a value, a location); only its *fields* differ, and fields live in the page, not the taxonomy.
- **vehicle** *is* split out from asset despite being "an asset that rolls," because it has a genuinely distinct field set (VIN, plate, registration, mileage, title) and a distinct lifecycle (registration renewal, recurring service intervals). When a type's mandatory fields and recurring events diverge sharply, it earns its own type. This is the test for "subtype vs. type."
- **property** is gated because creating a new residence/structure is rare and structural — it anchors location for everything else.
- **financial-institution vs. account** is the asset/proof split applied to money: the *bank* is the institution entity; your *checking account* there is an `account` entity linked to it. One bank, many accounts.
- **software vs. its license** is the asset/proof split applied to software, and it's why there is *no* top-level `license` type. The *software* (Ableton, Photoshop, a tool) is `asset/software` — a singular thing you track. The *license/key/seat* that proves you may run it is a `document/license` linked to it — proof, exactly like a warranty. This keeps one retrieval path for everything that lapses (registrations, domains, software seats all read as license/warranty documents) and lets one subscription license many apps (one `document/license` → five `asset/software`). An earlier draft made `license` its own type on the logic that it "has an expiry and a renewal" — but that's an *attribute* (`expires:`/`renews:` fields), not an identity, so by the escalation ladder (§1.4) it never earned a type. Walked back.
- **collection vs. asset** is the singular-vs-catalog split, and it's why `collection` *does* earn a top-level type (unlike `license`). A furnace is one `asset` with a serial, value, and warranty. An 800-title game library is not 800 assets — catalog items have a different field set (title/creator/format/platform/genre, not serial/value/warranty), a different lifecycle (acquired/played/read, not maintained/depreciated), and a different interaction pattern (browse/filter/recommend, not track-and-service). That triple divergence is exactly the type test. Subtypes carry the media kind (`game`, `music`, `film`, `book`, `tv`); genre/platform/format/publisher/completion are tags and fields. The line between `asset/software` and `collection/game`: *do I track this one specifically, or is it one of many I browse?*
- **document** is the universal "proof" type. Receipts, warranties, manuals, contracts, tax records, IDs — and now licenses/keys. Almost everything else links *to* documents.
- **maintenance** and **event** are time-anchored types that make Agent Vault able to answer "when is the next furnace service" and "when did I buy this" — they're what turn a static inventory into something that answers temporal questions.

### 1.3 Cross-cutting attributes (frontmatter fields on ANY entity)

These are the expressive dimensions. Because they're attributes, not types, any entity can carry any of them, and adding one never reorganizes the tree.

| Attribute | Field | Notes |
|-----------|-------|-------|
| Location | `location:` | slug ref to a `property/room` entity. A thing's *where*, decoupled from its *what*. |
| Status | `status:` | active / archived / sold / disposed / lapsed / lost / needs-review |
| Lifecycle dates | `acquired:`, `expires:`, `renews:`, `serviced:`, `due:` | the temporal hooks Agent Vault queries |
| Value | `value:`, `value_as_of:` | for insurance/estate use; one of the top reasons these systems exist |
| Identity | `serial:`, `model:`, `vin:`, `last4:` | identifiers; secrets still go to `credential_ref` |
| Money link | `credential_ref:` | pluggable secret reference (spec §7) |
| Free tags | `tags: []` | the organic-growth layer; auto-promoted per spec §5 |
| Relations | `related: []` | typed links to other entities |

### 1.4 The decision rule (so the taxonomy self-governs)

When the system (or you) hits something new, this is the order of escalation — cheapest first:

1. **Is it just a value of an existing attribute?** → it's a tag or a field value. (e.g. "stainless steel" → tag. "kitchen" → `location`.) Most new things stop here.
2. **Is it an existing type with a new flavor?** → it's a **subtype**. (e.g. "smart speaker" → `asset/electronics`.) Auto-promotes at low threshold.
3. **Does it have a genuinely distinct mandatory field set AND a distinct lifecycle?** → only then is it a **new type**. Human-gated, rare.

This rule is what keeps the top level at ~12 forever while the system still absorbs anything. The LLM proposes against this ladder (spec §5); promotion enforces it.

---

## Part 2 — The Full Build Order

Build in this sequence. Each stage produces something testable before the next depends on it. Resist wiring everything at once — that's the #1 way these die.

### Stage 0 — Substrate (½ day)
The files, before any code.
- Scaffold the directory tree (spec §1).
- Write `registry/schema.yaml` seeded with the 12 types above, `registry/aliases.yaml` empty, `registry/resolvers.yaml` with at least one resolver you actually have (start with `age://` — an age-encrypted file store on the NAS is the lowest-dependency option; add Vaultwarden/1Password later).
- Write the entity page template (spec §2) — the three fenced regions.
- **Test:** a hand-written sample entity for each of 3 types validates against the schema. No code yet, just prove the format holds.

### Stage 1 — Retrieval first (Agent Vault), against hand-made pages (1–2 days)
Counterintuitive, but build the *consumer* before the *producer*. It forces the schema to be queryable and gives you a working tool on day two.
- `_index.json` generator: walks `entities/`, emits the machine index (spec §8).
- `synapse` CLI: pure Python. Parses query → normalizes through `aliases.yaml` → filters `_index.json` → returns. **No LLM.** Commands like `synapse find <text>`, `synapse due`, `synapse expiring`, `synapse show <slug>`, `synapse creds <slug>` (resolves `credential_ref` on demand, never persists plaintext).
- **Test:** hand-make 8–10 entity pages across types. Confirm Agent Vault answers "what's expiring this month," "show my BoA account," "where's the furnace manual." If a question is awkward to answer, the *schema* is wrong — fix it now, while pages are hand-made and cheap.

### Stage 2 — Ingestion (Layer 1, deterministic Python) (3–5 days, the core investment)
The robust, LLM-free classifier. This is where you spend real effort.

> **The system boundary is `raw/`.** The contract begins the moment a file is sitting in `raw/`. *How* it got there — a watched folder, an inbox monitor, a forward-to address, an agent dropping it, something not invented yet — is a deliberately swappable front-end, exactly like the runner, the compile model, and the secret backend are swappable. Don't hard-wire intake into the classifier; the classifier must never know or care which pipe delivered the bytes. Build intake mechanisms as independent feeders that all terminate in `raw/`, and add/replace them freely over time.

- **Intake & manifest:** files land in `raw/`, get hashed, appended to `raw/_manifest.jsonl`. Append-only, idempotent (re-running never double-ingests). The hash is also what detects an already-seen file arriving by a second route (e.g. forwarded *and* in a synced folder).
- **File-type detection:** `python-magic` for true type, not extension. PDF text via `pdfplumber`/`pymupdf`; EXIF via `exifread`; email via `email`/`extract_msg`.
- **Email splitting:** an ingested email is often *two+ things* — the message itself (which may be the record, e.g. an order confirmation → `event/purchase`) plus each attachment (a separate `document`, e.g. the warranty PDF the email is evidence for). Split into the email/event entity + attachment entities and link them. This is the one intake shape that fans out into multiple entities; everything else is one file → one (or zero) entities.
- **Entity extraction & classification:** dictionary + regex + heuristics. A pattern library (`patterns/`) of known billers, banks, vendors. Confidence score on every classification (spec §6). Below threshold → `type: unknown`, `status: needs-review`, still stubbed and findable.
- **Alias normalization:** every surface form resolved through `aliases.yaml` to a stable slug *before* a page is created — this is what prevents duplicate entities.
- **Stub writer:** create/update entity files — frontmatter + regenerated link block, **empty prose body, `status: stub`**. Never writes prose.
- **Index refresh** at the end of every run.
- **Test:** feed it 20–30 real household files (documents, photos, a few emails — the core set you're starting with). Inspect the stubs. Measure: what % classified confidently, what landed in `needs-review`. Tune the pattern library. Agent Vault should already find the stubs (just no prose yet).

### Stage 3 — Compilation (Layer 2, the one LLM touchpoint) (2–3 days)
Stub → prose. Model-agnostic by contract.
- **The compile contract** (a prompt file, versioned): input is one stub + its linked source files + the current registry vocabulary. Output is *only* the prose body. Hard rules in the prompt, mirroring the spec: never edit frontmatter, never invent facts (`[NEEDS SOURCE]` instead), prefer existing vocabulary, append discoveries to `proposals.jsonl`. Written tight enough that a *small local model* can follow it (short, explicit, low-freedom) — but any model can run it.
- **Driver:** finds `status: stub` (or pages whose `sources_hash` changed), calls the model per stub, writes prose, flips to `status: compiled`. One entity's material in context at a time = token-efficient and private.
- **Discovery capture:** the model's proposals land in `discovery/proposals.jsonl`. Nothing in the registry changes yet.
- **Test:** compile the Stage-2 stubs. Read the prose for fidelity (does it match sources, no hallucination?). Inspect `proposals.jsonl` — are the discoveries sane?

### Stage 4 — The learning loop (promotion step, deterministic) (1–2 days)
Close the loop that lets the schema grow itself safely.
- **Promotion script** (spec §5.2): drains `proposals.jsonl`, applies thresholds, normalizes (collapses `bofa`/`BoA`→`bank-of-america`), graduates tags/subtypes/aliases automatically, parks new *types* and low-confidence items in a human review queue, stamps `schema.yaml`/`aliases.yaml`.
- **Review queue:** a generated markdown file listing parked proposals with evidence + an `approve`/`reject` mechanism (even just editing a YAML file Agent Vault reads).
- **Test:** run it against Stage-3 proposals. Confirm a new tag auto-promotes at threshold, a new type parks for you, an alias collapses a duplicate. Re-run ingestion — confirm the next pass is measurably smarter (builds a page it couldn't before).

### Stage 5 — Cadence & automation (½ day, but let it soak a week first)
Only now wire schedules — and **only one runner-agnostic entry point per cadence.**
- Three scripts, never combined (per spec, mirrors the proven three-cadence model): **daily** = ingest only (raw + index, never prose), **weekly** = compile + promote (the only thing that writes prose/schema), **monthly** = lint/report only (flags staleness, contradictions, broken links — writes a report, changes nothing).
- Each is a plain script with a clean CLI. The runner — Hermes, cron, systemd, n8n, whatever — just *calls* them. Swapping runners is editing one line, because the runner never contains logic, only a schedule + a path. **This is the harness independence, made concrete.**
- **Test:** run each cadence manually end-to-end (`--dry-run` first). Read the log. Let it run a week before adding anything.

### Stage 6 — Deferred ingestion sources: media collections (later, separate project)
Explicitly *after* the document/photo/email core soaks. Your game library — and later music, film, books, TV — is a **`collection`-type ingestion source bolted onto the side**, not a change to the core. It's deferred for a real reason: catalog ingestion is a *different ingestion problem*. It doesn't parse PDFs and emails — it pulls structured metadata from launcher/library exports and lookup APIs (Steam/GOG exports, a Plex/Jellyfin or library scan, ISBN/MusicBrainz enrichment). Building that style alongside document parsing would stall both. So:
- The taxonomy already reserves the slot (`collection` + its subtypes), so **nothing gets restructured** when you add it — that's why it's in the schema from day one despite being built last.
- Each media kind is its own feeder that classifies into `collection/<kind>` and terminates in the same `raw/` boundary (or writes catalog stubs directly, since the metadata is already structured — a design choice for when you get there).
- The compile pass, retrieval, and learning loop are **unchanged** — a `collection/game` stub compiles and gets queried by the exact same machinery as a `document/warranty`. Genre/platform/completion are just tags and fields.
- Same principle as everywhere: a new ingestion source is as swappable as the runner, the model, and the secret backend. The core never moves to accommodate it.

### Stage 7 — Home Assistant + voice (later, separate project)
Also *after* the core soaks. HA and Whisper are just *another consumer* of Agent Vault, exactly like the CLI — they shell out to the same `synapse` query layer. Build nothing special in the core for them. When you get here: expose `synapse` over a tiny local HTTP shim, point an HA intent + Whisper at it. The core doesn't change. (This is the payoff of building the wiki harness-independent: voice is a 1-day bolt-on, not a re-architecture.)

> **The shape of the whole system, restated:** a stable file-format core, with *four* independently swappable edges — **intake sources** (folder/inbox/forward/agent → `raw/`), the **runner** (cron/systemd/Hermes/n8n), the **compile model** (local mini/Claude/Hermes), and the **secret backend** (age/Vaultwarden/1Password). Media collections (Stage 6) are new intake sources; voice (Stage 7) is a new consumer. Nothing on any edge ever forces a change to the core. That's the entire design goal, and it's why this survives the churn that kills most home-grown systems.

---

## Part 3 — Component & Tooling Checklist

**Files (the contract — survives everything):**
- `registry/schema.yaml`, `aliases.yaml`, `resolvers.yaml`
- entity page template (3 fenced regions)
- `discovery/proposals.jsonl`, review queue
- `_index.json`, `_index.md`

**Python (the deterministic layers):**
- ingestion: `python-magic`, `pdfplumber`/`pymupdf`, `exifread`, `extract_msg`, `pyyaml`
- a `patterns/` library (your biller/bank/vendor dictionaries — this grows over time and is half the system's intelligence)
- `synapse` CLI (stdlib + pyyaml is enough)
- promotion script (stdlib)
- resolver modules (one per secret backend; `age` first)

**The single LLM touchpoint:**
- compile prompt contract (versioned text file)
- a thin model client that's swappable (local mini model via Ollama, or Claude, or Hermes — behind one interface so the model is a config value, not a dependency)

**Automation (thin, runner-agnostic):**
- three cadence scripts (daily/weekly/monthly), each a clean CLI entry point
- a runner config that only *schedules* them (start with cron or systemd timers; move to Hermes later by changing the schedule, not the scripts)

**Secrets (pluggable, never plaintext):**
- start: `age` file store on the NAS
- later: add Vaultwarden, 1Password, etc. as additional resolvers — no schema change

---

## Part 4 — The Rules That Keep It Alive (don't skip)

1. **One author per file region.** Python owns frontmatter + links; the LLM owns prose only; the promotion step alone owns the registry. (Spec §2, §5.)
2. **Raw is append-only.** Never edited. This is what makes unattended automation safe.
3. **The LLM proposes; deterministic code commits.** No non-deterministic process ever writes the vocabulary it later reads. This is the anti-rot valve.
4. **Secrets are referenced, never stored.** Ingestion actively refuses to write secret-shaped strings into frontmatter.
5. **Build the consumer (Agent Vault) before the producer.** A schema you can't query cleanly is a broken schema — find that out on day two, not month two.
6. **Let it soak a week between stages.** Every dead version of this kind of system died from wiring four things at once.
7. **The taxonomy self-governs by the escalation ladder** (Part 1.4): value→tag→subtype→type, cheapest first. The top level stays ~12 forever.
8. **Read the logs.** Self-auditing only works if a human reads the audit. Put it on a recurring reminder.

---

## Part 5 — What to do tomorrow morning

Stage 0 + the first half of Stage 1, in one sitting:
1. Scaffold the tree.
2. Seed `schema.yaml` with the 12 types.
3. Hand-write 3 entity pages (one `asset`, one `account`, one `document` — e.g. your furnace, your BoA checking, a warranty PDF's record).
4. Write the `_index.json` generator and the bare `synapse find` / `synapse show` commands.
5. Ask one of those hand-made pages a real question.

If that question is awkward to answer, the schema needs adjusting — and you'll know before a single line of ingestion code exists. That's the whole point of the order.

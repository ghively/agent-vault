# LLM Wiki — Schema Specification (v1.0)

> The constitution. Pure data, no runtime. Every script, every agent, every harness obeys this.
> Nothing in this document depends on a specific scheduler, agent framework, or model. It defines
> *what the files look like and who is allowed to write to which part of them* — nothing else.

---

## 0. Design axioms (the non-negotiables)

These are restated throughout because each layer must hold them independently.

1. **The wiki is files on disk with a known structure.** Not a service, not an agent, not a harness. Any agent in any harness can read it because it is just well-structured text + YAML.
2. **The LLM touches the system in exactly one place: stub → compiled page.** Everything before (gather, classify, link) and everything after (query, return) is deterministic Python.
3. **A non-deterministic component never gets direct write access to the deterministic layer's vocabulary.** The LLM *proposes*; a deterministic promotion step *commits*. One author per file region.
4. **Raw is append-only and immutable.** Source material is never edited. Ever.
5. **Secrets are never stored — only referenced.** Plaintext credentials never sit in a markdown file.

---

## 1. Directory layout

```
wiki/
├── registry/
│   ├── schema.yaml            ← canonical vocabulary (types, subtypes, tags). PYTHON-WRITE ONLY.
│   ├── aliases.yaml           ← surface-form → slug map. PYTHON-WRITE ONLY.
│   └── resolvers.yaml         ← credential resolver registry (scheme → backend). HUMAN-WRITE.
├── raw/                       ← immutable source documents. APPEND-ONLY.
│   ├── documents/, email/, statements/, media/, misc/
│   └── _manifest.jsonl        ← one line per ingested item (hash, path, ingested_at)
├── entities/                  ← the wiki proper. One file per entity.
│   ├── financial-institution/
│   ├── vendor/
│   ├── document/
│   ├── account/
│   ├── person/
│   └── <type>/...
├── discovery/
│   └── proposals.jsonl        ← LLM's findings. APPEND-ONLY. LLM-WRITE, PYTHON-READ.
├── _index.md                  ← human-readable master index. PYTHON-WRITE ONLY.
└── _index.json                ← machine index for Gregory. PYTHON-WRITE ONLY.
```

**Ownership at a glance** — the single most important table in this document:

| Path | Written by | Read by |
|------|-----------|---------|
| `raw/**` | Ingestion (append-only) | LLM (compile), Python |
| `registry/schema.yaml` | **Promotion step only** | everyone |
| `registry/aliases.yaml` | **Promotion step only** | everyone |
| `registry/resolvers.yaml` | Human | Gregory (resolve) |
| `entities/*` frontmatter | Python (ingest) | everyone |
| `entities/*` link block | Python (regenerated each run) | everyone |
| `entities/*` prose body | **LLM (compile) only** | everyone |
| `discovery/proposals.jsonl` | **LLM (append-only)** | Promotion step |
| `_index.*` | Python | Gregory, humans |

If two things write the same region, the design is wrong. There is exactly one author per region.

---

## 2. Entity file anatomy

Every entity is one markdown file with three fenced regions. The fences are load-bearing — they are how the LLM and Python avoid clobbering each other.

```markdown
---
# ============ FRONTMATTER — PYTHON OWNS THIS. LLM MUST NOT EDIT. ============
slug: bank-of-america                 # stable filesystem identity, never changes
type: financial-institution           # must exist in registry/schema.yaml
subtype: bank
title: Bank of America
tags: [banking, checking, primary]    # all must exist in registry/schema.yaml
aliases: [BofA, "BANK OF AMERICA, N.A."]   # surface forms; mirrored in aliases.yaml
credential_ref: vaultwarden://Banking/bofa-login   # reference only, never a secret
confidence: 0.97                      # classifier confidence (see §6)
status: compiled                      # stub | compiled | needs-review | unknown
sources:                              # every raw file this entity draws from
  - raw/statements/bofa-2026-01.pdf
  - raw/email/bofa-alert-2026-02-03.eml
created: 2026-05-01
sources_hash: 8f3a...                 # hash of the source set; prose recompiles only if this changes
---

<!-- ============ LINK BLOCK — PYTHON OWNS THIS. Regenerated every run. ============ -->
<!-- LINKS:BEGIN -->
- Related entities: [[account/bofa-checking]], [[person/gene]]
- Documents: [[document/bofa-resume-2026]]
- Backlinks: [[job-application/bofa-devops-role]]
<!-- LINKS:END -->

<!-- ============ PROSE BODY — LLM OWNS THIS. Python must not edit. ============ -->
Bank of America is the primary checking institution for the household...
(LLM-written synthesis of the linked sources goes here)
```

**Rules:**
- Python writes/updates everything between `---...---` and between `<!-- LINKS:BEGIN -->` / `<!-- LINKS:END -->`.
- The LLM writes everything *below* `<!-- LINKS:END -->` and nothing above it.
- The LLM recompiles the prose body only when `sources_hash` changed since last compile. Otherwise the human-or-prior prose stands. (No gratuitous recompiles, no token burn.)
- A **stub** is a valid entity file with frontmatter + link block but an empty prose body and `status: stub`. The compile step's only job is stub → `status: compiled` with prose filled in.

---

## 3. The schema registry (`registry/schema.yaml`)

The canonical vocabulary. **One author: the promotion step (§5).** The LLM may *read* this so it prefers reusing existing vocabulary over inventing new; it may never write it.

> The example below is partial illustration. The **full default taxonomy** (the twelve top-level types, their subtypes, and the reasoning) lives in the companion build guide, Part 1 — that document is the source of truth for *what types exist*; this section is the source of truth for *what the registry file looks like*. Note `license` is a `document` subtype and `software` is an `asset` subtype — there is no top-level `license` type.

```yaml
version: 1
types:
  financial-institution:
    subtypes: [bank, credit-union, brokerage, lender]
    page_template: institution        # which prose structure the LLM should follow
    human_gated: true                  # new instances of this TYPE need human approval
  vendor:
    subtypes: [utility, streaming, insurance, subscription, retailer]
    page_template: vendor
    human_gated: false
  document:
    subtypes: [resume, contract, warranty, manual, receipt, statement, id-document, license]
    page_template: document
    human_gated: false
  job-application:                     # ← graduated from a discovery proposal
    subtypes: [submitted, draft, offer]
    page_template: document
    human_gated: true
  account: { subtypes: [checking, savings, credit-card, loan, login], page_template: account, human_gated: false }
  person:  { subtypes: [household, contact], page_template: person, human_gated: true }
  media:   { subtypes: [family-photo, scan, screenshot], page_template: media, human_gated: false }

tags:                                  # flat namespace; auto-promoted at threshold (§5)
  banking:    { promoted: 2026-05-01, count: 14 }
  checking:   { promoted: 2026-05-01, count: 9 }
  job-search: { promoted: 2026-05-12, count: 3 }
```

---

## 4. Identity & aliases (`registry/aliases.yaml`)

Solves the "Bank of America" / "BofA" / "BANK OF AMERICA, N.A." = one entity problem. **One author: the promotion step.** Every surface form points at exactly one slug.

```yaml
version: 1
aliases:
  "bank of america": bank-of-america
  "bofa": bank-of-america
  "bank of america, n.a.": bank-of-america
  "boa": bank-of-america
  "duke energy": duke-energy
  "duke energy corp": duke-energy
```

- Match is case-insensitive, whitespace-normalized.
- The slug is the filesystem identity and never changes once assigned, even if the display title does.
- The LLM proposes new aliases via the discovery log (§5); the promotion step commits them here.

---

## 5. The learning loop — discovery → proposal → promotion

This is the heart of the design. It lets the schema expand itself *without* a non-deterministic process authoring the deterministic layer.

### 5.1 Discovery (LLM, during compile)

While compiling a page the LLM sees content with real intelligence — and so it is the natural place to catch what Python's deterministic classifier missed. When it does, it does **not** edit any registry file and does **not** invent vocabulary into the frontmatter. It appends a structured proposal to `discovery/proposals.jsonl`:

```jsonl
{"kind":"new_type","value":"job-application","subtype":"submitted","evidence":["raw/documents/bofa-resume-2026.pdf"],"reason":"Resume tailored to a BofA DevOps role; appears to be a job application, not a generic document.","confidence":"high","linked_entity":"bank-of-america","proposed_at":"2026-05-12T13:22:00Z"}
{"kind":"new_tag","value":"job-search","evidence":["raw/documents/bofa-resume-2026.pdf"],"confidence":"high","proposed_at":"2026-05-12T13:22:00Z"}
{"kind":"new_alias","surface":"B of A","slug":"bank-of-america","confidence":"high","proposed_at":"2026-05-12T13:22:00Z"}
{"kind":"reclassify","entity":"document/bofa-resume-2026","from":"document/resume","to":"job-application/submitted","reason":"...","confidence":"medium","proposed_at":"..."}
```

Proposal kinds: `new_type`, `new_subtype`, `new_tag`, `new_alias`, `reclassify`. Every proposal carries **evidence** (source files) and a **confidence**. No proposal ever mutates live state.

### 5.2 Promotion (Python, deterministic, runs before each ingest)

Python drains `proposals.jsonl` and applies promotion rules. This is the valve. Defaults:

| Proposal kind | Auto-promote rule | Otherwise |
|---------------|-------------------|-----------|
| `new_tag` | seen ≥ **3** times across proposals → add to `schema.yaml` | park in review queue |
| `new_alias` | confidence `high` AND target slug exists → add to `aliases.yaml` | park |
| `new_subtype` | parent type exists AND seen ≥ **2** → add | park |
| `new_type` | **never auto** — always human-gated | always park for approval |
| `reclassify` | confidence `high` AND target type already in registry → apply | park |

- **Normalization happens here, not in the LLM.** If the LLM proposed tag `bofa` and tag `bank-of-america` already maps the same entity, the promotion step collapses them via the alias map. The deterministic layer is the only thing that decides canonical form, so `bofa`/`BoA`/`bank-of-america` can never splinter into three live tags.
- Promoted items are stamped with date + count in `schema.yaml`. Parked items wait in a review queue Python writes for the human.
- Thresholds live in one config block. Trust grows → raise auto-promote, shrink human-gating. Day one you eyeball; later you step back.

### 5.3 The full cycle

```
Python ingest (classifies what it knows; new file types/sources pull in new structure)
        ↓ writes stubs + frontmatter + links
LLM compile (reads stub + sources; writes prose; appends discoveries to proposals.jsonl)
        ↓
Promotion step (drains proposals; normalizes; graduates good ones into registry; parks the rest)
        ↓
Next Python ingest is now smarter — builds the new entity pages, wires the new cross-links
```

Your resume example, end to end: ingest files the resume as a generic `document`. Compile reads it, recognizes a BofA-targeted job application, appends a `new_type: job-application` proposal + `reclassify` proposal with evidence. Promotion parks `job-application` for your approval (it's a new *type*, human-gated). You approve. Next ingest creates `entities/job-application/bofa-devops-role.md`, links it to `bank-of-america`, your resume, and you. Self-expansion — gated so it can't eat itself.

---

## 6. Confidence & graceful degradation

The deterministic classifier never silently misfiles. Every classification carries a confidence score, and low confidence is a first-class state, not an error.

- `confidence ≥ high-threshold` → classify normally.
- `below threshold` → `type: unknown`, `status: needs-review`, parked in the review queue. The entity still gets a stub and is still findable; it just isn't asserted to be something it might not be.
- `unknown` entities are exactly what the LLM compile pass is best at triaging — it reads them and proposes a real type via the discovery log. So "Python doesn't recognize this" flows naturally into "LLM takes a first look and suggests," which flows into "human or threshold promotes." Brittleness becomes a queue, not a failure.

---

## 7. Credential references — pluggable, never plaintext

A `credential_ref` is a URI: **`scheme://store/path`**. The scheme names a *resolver*; resolvers are pluggable backends declared in `registry/resolvers.yaml`. The vault holds only the reference. Resolution happens at query time, in the resolver module, never written back into a file.

```yaml
# registry/resolvers.yaml — human-authored
version: 1
resolvers:
  vaultwarden:
    module: resolvers.vaultwarden
    endpoint: https://vault.lan
  age:
    module: resolvers.age_file
    keyfile: ~/.config/wiki/age.key
    store_dir: /volume1/secrets
  env:
    module: resolvers.env          # for non-secret-ish refs / dev
  onepassword:
    module: resolvers.op
# add bitwarden, pass, hashicorp-vault, etc. — same shape
```

Examples of refs that can coexist in one vault, each routed to the right backend by scheme:

```
credential_ref: vaultwarden://Banking/bofa-login        # household login
credential_ref: age://infra/synology-ssh                # an SSH key, age-encrypted on the NAS
credential_ref: onepassword://Personal/recovery-phrase  # high-sensitivity, separate store
```

**Rules:**
- A field named `credential_ref` (or any value matching a known scheme) must **never** contain a plaintext secret. Ingestion actively scans for secret-shaped strings in `raw/` and refuses to write them into frontmatter — it writes a `needs-review` flag instead.
- Gregory resolves a ref only on demand and only to answer the user; resolved plaintext is never persisted to disk or index.
- Adding a new backend = adding one resolver module + one stanza here. No schema change. This is the backend-independence you asked for.

---

## 8. The index (`_index.json`) — what makes Gregory LLM-free

Gregory is a pure lookup because Python maintains a machine index covering every queryable field. Retrieval is "filter the index," not "reason over text."

```json
{
  "generated": "2026-05-12T14:00:00Z",
  "entities": [
    {
      "slug": "bank-of-america",
      "type": "financial-institution",
      "subtype": "bank",
      "title": "Bank of America",
      "tags": ["banking", "checking", "primary"],
      "aliases": ["bofa", "bank of america, n.a.", "boa"],
      "has_credential": true,
      "path": "entities/financial-institution/bank-of-america.md",
      "facts": { "next_bill_due": "2026-05-28", "account_last4": "REDACTED-ref" }
    }
  ]
}
```

`gregory when is my bofa bill due` → normalize "bofa" through aliases → slug `bank-of-america` → read `facts.next_bill_due` → return. No model, no thinking, no hallucination surface. If fuzzy phrasing ever defeats the alias map, a *tiny* optional model is a fallback for query parsing only — never for answering. The design target is that it's never needed.

---

## 9. What this spec deliberately does NOT define

To stay harness-independent, this document says nothing about:
- *Which* scheduler runs the cadences (cron, systemd, n8n, Hermes, OpenClaw — all fine; the files don't care).
- *Which* LLM does the compile (Claude, a local mini model, Hermes — the prose body doesn't care who wrote it).
- *Which* language the scripts are in (Python is the assumed default, but the file format is language-agnostic).

The contract is the files. Swap everything around them freely. That is the whole point.

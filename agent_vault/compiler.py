#!/usr/bin/env python3
"""
compiler.py — Stage 3. THE single LLM touchpoint.

This is the only file in the system that talks to a language model. Everything
else is deterministic. The compile pass turns `status: stub` entities (and any
already-compiled entity whose `sources_hash` has drifted from
`compiled_from_hash`) into compiled pages by writing PROSE ONLY.

What this script may write:
  - the prose body (everything below the LINKS:END marker)
  - two frontmatter fields ONLY: `status` and `compiled_from_hash`
  - append-only entries to `discovery/proposals.jsonl`

What it must NEVER write:
  - any other frontmatter field (slug, type, subtype, sources, tags, ...)
  - the LINKS block (Python ingestion owns that)
  - registry/* (only the promotion step writes the vocabulary)

The compile contract is versioned (PROMPT_CONTRACT_VERSION) and the model
identity is stamped onto every proposal so we can audit which model said
what later.

Client selection (swap edge):
  AGENT_VAULT_COMPILER=ollama   (default)  -> OllamaClient (HTTP localhost:11434)
  AGENT_VAULT_COMPILER=mock                -> MockClient (deterministic, offline)
  OLLAMA_HOST=http://nas:11434         -> override Ollama endpoint
  OLLAMA_MODEL=qwen2.5:7b-instruct     -> default model; override anytime

Model selection precedence (highest first):
  --model NAME on the command line
  OLLAMA_MODEL=...  in env
  built-in default "qwen2.5:7b-instruct"

Trying a new model is a config change, not a code change:
  $ OLLAMA_MODEL=llama3.1:8b python -m agent_vault.compiler .
  $ python -m agent_vault.compiler --model phi4 .
"""
import sys
import os
import re
import json
import datetime
import urllib.request
import urllib.error

# Force UTF-8 stdout/stderr so entity titles and LLM-proposed content with
# Unicode don't crash on Windows consoles that default to cp1252.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required:  pip install pyyaml --break-system-packages")

# vault-wide write lock (overlapping mutating runs serialize; readers don't lock)
try:
    from .locking import vault_lock
except Exception:
    from contextlib import nullcontext
    def vault_lock(_v):
        return nullcontext()


# ----------------------------------------------------------------------------
# Versioned compile contract.
# Bump the version when the prompt changes meaningfully so older proposals
# can be audited against the contract they were written under.
# ----------------------------------------------------------------------------
PROMPT_CONTRACT_VERSION = "2.0"   # 1.1: deterministic source de-noise + total
                                  # budget + truncation/blob markers in <sources>.
                                  # 1.2: alias proposals carry a confidence field
                                  # (promote.py's `require_confidence: high` gate
                                  # for new_alias was unreachable without it).
                                  # 1.3: `type` proposal kind — the spec's
                                  # self-expansion path (always human-gated by
                                  # the registry's `new_type: auto: false`).
                                  # 2.0: three NEW proposal kinds added to the
                                  # <proposals> contract (learned detection +
                                  # field extraction): biller, shape,
                                  # field_mapping. The proposal-kind set
                                  # changed meaningfully, so older proposals are
                                  # now audited under the 1.x contract.

# SYSTEM_PROMPT is intentionally a byte-stable module constant: Ollama/llama.cpp
# amortize the KV cache for an identical leading `system` prompt across calls, so
# the ~450-token system overhead is effectively free after the first entity.
# Do NOT template it per-entity — that would defeat the server-side prefix cache.
SYSTEM_PROMPT = """You are the compile pass of Agent Vault, a harness-independent file-based wiki.

You are given one entity stub (its frontmatter + linked source materials) and
must write the PROSE BODY of its page. You are the ONLY non-deterministic
component in the pipeline. The vault's correctness depends on you following
the rules below to the letter.

HARD RULES (violations corrupt the vault):
1. OUTPUT ONLY the two tagged blocks specified below. No preamble, no
   commentary, no markdown around them.
2. NEVER output YAML frontmatter (`---` blocks), the LINKS:BEGIN/END block,
   or any field name like `slug:` / `type:`. Python owns those.
3. NEVER invent facts. If a useful detail isn't in <sources>, write
   `[NEEDS SOURCE: what specifically you would need]` inline in the prose.
4. Prefer the vocabulary in <vocabulary>. Do NOT introduce new types,
   subtypes, or tags in your prose. If you believe one is warranted, emit
   a structured proposal in the <proposals> block — never in the prose.
5. Be concise. 1–3 short paragraphs total. The title and date fields are
   already in the frontmatter; don't restate them mechanically. Synthesize.

OUTPUT FORMAT (exactly this shape, nothing else):
<prose>
... your prose body here ...
</prose>
<proposals>
[
  {"kind": "tag",        "name": "...",     "evidence": "...", "confidence": 0.0},
  {"kind": "alias",      "from": "...",     "to": "<existing-slug>", "evidence": "...", "confidence": 0.0},
  {"kind": "subtype",    "type": "<existing-type>", "name": "...",   "evidence": "..."},
  {"kind": "type",       "name": "...",     "evidence": "...", "confidence": 0.0},
  {"kind": "reclassify", "to_type": "...",  "to_subtype": "...",     "evidence": "...", "confidence": 0.0},
  {"kind": "biller",        "id": "<new-slug>", "matches": ["<surface form>"], "vendor_slug": "<existing-slug-or-omit>", "institution_slug": "<existing-slug-or-omit>", "account_slug": "<existing-slug-or-omit>", "default_tags": ["<existing-tag>"], "confidence": 0.0, "evidence": "...", "evidence_text": "<quoted snippet>"},
  {"kind": "shape",         "id": "<new-slug>", "type": "<existing-type>", "subtype": "<existing-subtype>", "keywords": ["<co-occurring phrase>"], "min_matches": 2, "applies_to": ["pdf"], "confidence": 0.0, "evidence": "...", "evidence_text": "<quoted snippet>"},
  {"kind": "field_mapping", "id": "<new-slug>", "applies_to": ["pdf", "email"], "pattern": "Label:\\s*(\\S+)", "field": "<field-name>", "group": 1, "parse": "text", "require_digit": false, "confidence": 0.0, "evidence": "...", "evidence_text": "<quoted snippet>"}
]
</proposals>

If you have no proposals, output exactly: <proposals>[]</proposals>.
Proposal evidence MUST quote or paraphrase a specific phrase from <sources>.
A "type" proposal (a NEW top-level entity type) is a structural change and is
always reviewed by a human — propose one ONLY when no existing type in
<vocabulary> could reasonably hold this entity, and prefer a "subtype" or
"reclassify" proposal whenever one fits.

Three ADDITIONAL proposal kinds let you grow the detection vocabulary. Use
them ONLY when you see something genuinely new in <sources> (never invent):

  - biller: you saw a NEW vendor/biller surface form (a domain or brand)
    that is NOT already in <vocabulary>. Propose the `matches` list (the exact
    surface strings you saw, kept specific so short tokens do not false-fire),
    a stable kebab-case `id`, and at most ONE of vendor_slug / institution_slug
    / account_slug to link into the existing entity graph. `default_tags`, if
    given, MUST already exist in <vocabulary>. Prefer the existing vocabulary;
    only propose a new biller when no current biller covers what you saw.

  - shape: you saw a NEW document structure characterized by CO-OCCURRING
    keywords. Propose the `keywords` list, `min_matches` (how many must appear
    to fire), and the existing `type` + `subtype` it implies (both MUST already
    exist in <vocabulary>). Optionally restrict it to file kinds via
    `applies_to` (e.g. ["jpeg", "png"]). A shape is vendor-agnostic.

  - field_mapping: you saw a structured value (an id / date / amount /
    number) with a label that is NOT already captured by the built-in
    extractors. Propose a SIMPLE, BOUNDED regex with EXACTLY ONE capture group,
    the `field` name to store it under, the capture `group` (default 1), and a
    `parse` type ("text", "float", "int", or "iso-date"). Quote the
    `evidence_text` snippet you matched against. The regex MUST be simple,
    bounded, and single-group: no unbounded greedy `.*` across lines, no
    alternations with multiple groups, no catastrophic backtracking. A field
    mapping a human cannot eyeball as safe will be rejected, so keep it minimal.

All three new kinds REQUIRE a quoted `evidence_text` snippet from <sources>
(the exact substring that triggered the proposal) in addition to the
paraphrased `evidence`. Every proposal still carries a `confidence` (0-1).
"""


# ----------------------------------------------------------------------------
# Token-efficiency layer (deterministic, conservative). The LLM stays the smart
# part; these helpers only feed it CLEANER, SMALLER input — never summarize, and
# never drop a line that carries an extracted fact. (Karpathy-wiki ethos.)
# ----------------------------------------------------------------------------
PER_SOURCE_CHAR_CAP = int(os.environ.get("AGENT_VAULT_PER_SOURCE_CHARS", "4000"))
TOTAL_SOURCE_CHAR_BUDGET = int(os.environ.get("AGENT_VAULT_TOTAL_SOURCE_CHARS", "12000"))

_FACT_RES_CACHE = None
_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=]{200,}$|^[0-9a-fA-F]{200,}$")


def _fact_regexes():
    """The ingest fact-regexes whose matches pin a line as must-keep. Imported
    from .ingest so the denoiser and the extractor never disagree on 'a fact'."""
    global _FACT_RES_CACHE
    if _FACT_RES_CACHE is None:
        from . import ingest
        _FACT_RES_CACHE = [r for r in (
            ingest.DUE_RE, ingest.EXPIRES_RE, ingest.RENEWS_RE, ingest.PERIOD_RE,
            ingest.AMOUNT_RE, ingest.ORDER_RE, ingest.POLICY_RE, ingest.PARCEL_RE,
            ingest.VIN_RE, ingest.LAST4_RE, ingest.SERIAL_RE, ingest.MODEL_RE,
            ingest.DATE_RE) if r is not None]
    return _FACT_RES_CACHE


def collect_protected_values(stub_fm):
    """Stringified field values that MUST survive denoise/truncation (so the
    prose can still cite them): dates, amount, ids, last4, serial, model."""
    vals = []
    for k in ("due", "expires", "renews", "value", "vin", "last4", "serial",
              "model", "acquired", "serviced", "created"):
        v = stub_fm.get(k)
        if v not in (None, "", []):
            vals.append(str(v))
    return vals


def _line_is_fact_bearing(line, fact_res, protected_values):
    if any(pv and pv in line for pv in protected_values):
        return True
    return any(rx.search(line) for rx in fact_res)


def denoise_text(text, protected_values=(), fact_res=None):
    """Conservative, deterministic, idempotent cleanup of one source's text.
    Removes noise (blank runs, adjacent dup lines, long base64/hex blobs, email
    quote chains + signature blocks) but NEVER touches a fact-bearing line and
    never empties non-empty input."""
    if not text:
        return text
    if fact_res is None:
        fact_res = _fact_regexes()
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    out, blanks, prev, quote_run = [], 0, None, 0
    for ln in raw_lines:
        ln = ln.rstrip()
        fact = _line_is_fact_bearing(ln, fact_res, protected_values)
        if ln == "":
            blanks += 1
            quote_run = 0
            if blanks <= 1:
                out.append(ln)
            prev = ln
            continue
        blanks = 0
        if not fact:
            # long opaque blob -> marker
            if _BLOB_RE.match(ln):
                out.append(f"[binary blob omitted: {len(ln)} chars]")
                prev = None
                continue
            # email quote chain: drop runs of 3+ quoted lines
            if ln.lstrip().startswith(">"):
                quote_run += 1
                if quote_run >= 3:
                    continue
            else:
                quote_run = 0
            # adjacent exact duplicate
            if ln == prev:
                continue
        else:
            quote_run = 0
        out.append(ln)
        prev = ln

    # signature block: drop everything after a standalone '-- ' sig delimiter,
    # but only if nothing below it is fact-bearing.
    for i, ln in enumerate(out):
        if ln.strip() == "--" or ln == "-- ":
            tail = out[i + 1:]
            if not any(_line_is_fact_bearing(t, fact_res, protected_values) for t in tail):
                out = out[:i]
                break

    cleaned = "\n".join(out).strip("\n")
    # never empty a non-empty input
    return cleaned if cleaned.strip() else text.strip()


def allocate_source_budget(lengths, total_budget, per_source_cap):
    """Water-filling: each source capped at per_source_cap; short sources keep
    all their text and donate surplus to longer ones; sum(result) <= total_budget.
    Deterministic, integer-only."""
    n = len(lengths)
    if n == 0:
        return []
    caps = [0] * n
    remaining = min(total_budget, per_source_cap * n)
    need = [min(L, per_source_cap) for L in lengths]
    unfilled = set(range(n))
    while remaining > 0 and unfilled:
        share = max(1, remaining // len(unfilled))
        progressed = False
        for i in sorted(unfilled):
            if remaining <= 0:
                break
            give = min(share, need[i] - caps[i], remaining)
            if give <= 0:
                unfilled.discard(i)
                continue
            caps[i] += give
            remaining -= give
            progressed = True
            if caps[i] >= need[i]:
                unfilled.discard(i)
        if not progressed:
            break
    return caps


def priority_truncate(text, cap, fact_res=None, protected_values=()):
    """Truncate to `cap` chars but guarantee fact-bearing lines survive: keep the
    head, then append any fact lines that fell past the cut under a marker."""
    if cap <= 0 or len(text) <= cap:
        return text
    if fact_res is None:
        fact_res = _fact_regexes()
    head = text[:cap]
    tail_facts = [ln.strip() for ln in text[cap:].split("\n")
                  if _line_is_fact_bearing(ln, fact_res, protected_values)]
    out = head.rstrip() + f"\n[source truncated: showing {cap} of {len(text)} chars]"
    if tail_facts:
        out += "\n[key facts retained]:\n" + "\n".join(tail_facts)
    return out


def estimate_tokens(text):
    """Cheap ~chars/4 token estimate (NOT a real tokenizer). Observability only."""
    return max(1, len(text) // 4) if text else 0


def render_user_prompt(stub_fm, sources, vocab):
    """Compose the per-entity user prompt from the stub's frontmatter, the
    extracted source materials, and a snapshot of the registry vocabulary.

    Source text is budgeted across all sources (water-filling, total cap
    TOTAL_SOURCE_CHAR_BUDGET) so a many-source entity can't blow the context
    window; truncated sources get a visible marker and keep their facts."""
    parts = ["<vocabulary>"]
    parts.append(f"types: {', '.join(sorted(vocab['types']))}")
    for t in sorted(vocab["subtypes_by_type"]):
        parts.append(f"  {t} subtypes: {', '.join(vocab['subtypes_by_type'][t])}")
    parts.append(f"promoted tags: {', '.join(sorted(vocab['tags']))}")
    parts.append("</vocabulary>\n")

    parts.append("<stub>")
    for k in ("slug", "type", "subtype", "title", "tags", "due", "expires",
              "renews", "value", "vin", "last4", "location", "related"):
        if k in stub_fm and stub_fm[k] not in (None, "", []):
            v = stub_fm[k]
            if isinstance(v, list):
                parts.append(f"{k}:")
                for item in v:
                    parts.append(f"  - {item}")
            else:
                parts.append(f"{k}: {v}")
    parts.append("</stub>\n")

    parts.append("<sources>")
    texts = [(s.get("text") or "").strip() for s in sources]
    caps = allocate_source_budget([len(t) for t in texts],
                                  TOTAL_SOURCE_CHAR_BUDGET, PER_SOURCE_CHAR_CAP)
    fact_res = _fact_regexes()
    protected = collect_protected_values(stub_fm)
    for s, text, cap in zip(sources, texts, caps):
        parts.append(f"[source: {s['ref']}]")
        if not text:
            parts.append("(no extractable text)")
        elif len(text) <= cap:
            parts.append(text)
        else:
            # over budget: keep the head + a marker, but never lose a fact line
            parts.append(priority_truncate(text, cap, fact_res, protected))
        parts.append("")
    parts.append("</sources>")

    parts.append("\nWrite the prose body now, following the contract exactly.")
    return "\n".join(parts)


# ----------------------------------------------------------------------------
# Response parsing — the trust boundary. We accept ONLY the prose between
# <prose>...</prose> and a JSON array between <proposals>...</proposals>.
# Anything else the model said is silently dropped.
# ----------------------------------------------------------------------------
# Greedy to the LAST closing tag so prose that legitimately quotes "</prose>"
# isn't truncated at the first occurrence. A lenient fallback recovers prose when
# the model opened <prose> but never closed it (take to end / to <proposals>).
PROSE_RE = re.compile(r"<prose>(.*)</prose>", re.S | re.I)
PROSE_OPEN_RE = re.compile(r"<prose>(.*?)(?:<proposals>|$)", re.S | re.I)
PROPOSALS_RE = re.compile(r"<proposals>(.*)</proposals>", re.S | re.I)


def parse_response(text):
    """Return (prose_str, proposals_list). Drops anything outside the tags.
    A malformed proposals block yields [] (and is surfaced via warning). A
    missing prose block yields '' (the driver records a compile failure)."""
    text = text or ""
    pm = PROSE_RE.search(text)
    if pm:
        prose = pm.group(1).strip()
    else:
        # unclosed <prose>: recover rather than silently lose the whole body
        fm = PROSE_OPEN_RE.search(text)
        prose = fm.group(1).strip() if fm else ""
    # Defensive sanitization: even if the model violates the contract and
    # emits frontmatter delimiters or a LINKS block inside the prose,
    # strip them out so they can't corrupt the entity file structure.
    if prose:
        prose = re.sub(r"<!--\s*LINKS:(BEGIN|END)\s*-->", "", prose)
        prose = re.sub(r"^---\s*$", "", prose, flags=re.M)
    proposals = []
    pp = PROPOSALS_RE.search(text)
    if pp:
        body = pp.group(1).strip()
        if body and body != "[]":
            try:
                parsed = json.loads(body)
                if isinstance(parsed, list):
                    # No allowlist of `kind` values here: any dict carrying a
                    # `kind` key is surfaced. The v2.0 contract adds
                    # biller / shape / field_mapping kinds; they
                    # flow through unchanged so the promotion step (promote.py)
                    # can gate them. A malformed proposal dict (no `kind`) is
                    # the only thing dropped here.
                    proposals = [p for p in parsed if isinstance(p, dict) and "kind" in p]
            except json.JSONDecodeError:
                # Surface rather than silently drop — a model emitting malformed
                # proposal JSON is a contract problem the operator should see.
                print(f"warn: dropping malformed <proposals> JSON "
                      f"(first 120 chars: {body[:120]!r})", file=sys.stderr)
    return prose, proposals


# ----------------------------------------------------------------------------
# Compile clients — the swap edge. All must implement .compile(req) -> dict
# returning {"prose": str, "proposals": [...], "model": str, "raw": str}.
# ----------------------------------------------------------------------------
class CompileClient:
    name = "abstract"

    def compile(self, system_prompt, user_prompt):
        raise NotImplementedError


class MockClient(CompileClient):
    """Deterministic, offline, no network. Produces template-based prose that
    references frontmatter facts only — so the contract's no-invented-facts
    rule is mechanically satisfied. Used for tests and as a fallback.

    Returns a prose body that:
      - opens with the title and identifies the entity by type/subtype
      - lists any frontmatter dates and ties them to their fields
      - names every `related:` link as either a peer or a source
      - emits [NEEDS SOURCE: ...] for one or two narrative gaps so
        downstream tooling can verify the marker is recognized
    """
    name = "mock-v1"

    def compile(self, system_prompt, user_prompt):
        stub = self._parse_stub_block(user_prompt)
        sources = self._parse_source_refs(user_prompt)

        title = stub.get("title", stub.get("slug", "this entity"))
        t = stub.get("type", "entity")
        st = stub.get("subtype", "")
        related = stub.get("related") or []
        tags = stub.get("tags") or []

        lines = []
        opener = f"{title} is a {t}"
        if st: opener += f" ({st})"
        opener += " in the household vault."
        lines.append(opener)

        for k, label in (("due", "due"), ("expires", "expiring"),
                         ("renews", "renewing"), ("value", "valued at")):
            v = stub.get(k)
            if v:
                lines.append(f"It is {label} {v}.")

        if related:
            rel_str = ", ".join(f"[[{r}]]" for r in related[:6])
            lines.append(f"Linked entities: {rel_str}.")
        if tags:
            lines.append(f"Tagged: {', '.join(tags)}.")
        if sources:
            lines.append(f"Drawn from {len(sources)} source"
                         f"{'s' if len(sources) != 1 else ''}.")
        else:
            lines.append("[NEEDS SOURCE: at least one raw source for this stub]")

        lines.append("")
        lines.append("[NEEDS SOURCE: narrative detail beyond what frontmatter "
                     "captures — the mock compiler intentionally does not "
                     "synthesize claims from source text.]")

        prose = "\n".join(lines)
        body = f"<prose>\n{prose}\n</prose>\n<proposals>[]</proposals>"
        return {"prose": prose, "proposals": [],
                "model": self.name, "raw": body}

    @staticmethod
    def _parse_stub_block(user_prompt):
        m = re.search(r"<stub>(.*?)</stub>", user_prompt, re.S)
        if not m: return {}
        # Use yaml so list fields parse correctly
        try:
            return yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            return {}

    @staticmethod
    def _parse_source_refs(user_prompt):
        return re.findall(r"\[source:\s*([^\]]+)\]", user_prompt)


class OllamaClient(CompileClient):
    """POSTs to a local Ollama HTTP endpoint. Default host/model can be
    overridden via OLLAMA_HOST / OLLAMA_MODEL.

    Why Ollama: matches the harness-independent ethos — the model runs on
    your hardware, no cloud dependency, no household data leaving the LAN.
    Swap by setting AGENT_VAULT_COMPILER=mock or implementing another client.
    """
    name = "ollama"

    def __init__(self, host=None, model=None, timeout=120):
        self.host = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")
        self.timeout = timeout
        self.name = f"ollama:{self.model}"

    def compile(self, system_prompt, user_prompt):
        url = self.host.rstrip("/") + "/api/generate"
        body = json.dumps({
            "model": self.model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "options": {"temperature": 0.2, "num_ctx": 8192},
        }).encode("utf-8")
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw_body = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            # Network failures bubble up as RuntimeError; compile_all catches
            # this per-entity so one timeout doesn't kill the whole pass.
            raise RuntimeError(f"Ollama at {self.host} unreachable: {e}") from e
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Ollama returned non-JSON response "
                               f"(first 200 chars: {raw_body[:200]!r}): {e}") from e
        raw = payload.get("response") or ""
        prose, proposals = parse_response(raw)
        return {"prose": prose, "proposals": proposals,
                "model": self.name, "raw": raw}


def get_client(model_override=None):
    """Return the compile client selected by AGENT_VAULT_COMPILER env var.
    Defaults to ollama; mock for offline/CI use.

    `model_override` (e.g. from --model on the CLI) takes precedence over
    OLLAMA_MODEL env var. Trying a new model is a one-line invocation,
    no code or config edit required."""
    choice = os.environ.get("AGENT_VAULT_COMPILER", "ollama").lower()
    if choice == "mock":
        return MockClient()
    if choice == "ollama":
        return OllamaClient(model=model_override)
    raise ValueError(f"unknown AGENT_VAULT_COMPILER={choice!r} (expected: ollama, mock)")


# ----------------------------------------------------------------------------
# Vault I/O — frontmatter + link block are READ ONLY here (we only mutate
# `status` and `compiled_from_hash`); the prose body is rewritten wholesale.
# ----------------------------------------------------------------------------
FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
LINKS_RE = re.compile(r"(<!-- LINKS:BEGIN -->.*?<!-- LINKS:END -->)", re.S)


def split_entity(text):
    """Return (frontmatter_text, links_block, prose_body). All three are
    plain strings so the writer can put them back together byte-for-byte
    around a new prose body without re-serializing frontmatter."""
    m = FM_RE.match(text)
    if not m:
        return None, None, None
    fm_text = m.group(1)
    body = m.group(2)
    lm = LINKS_RE.search(body)
    if not lm:
        return fm_text, "", body
    links_block = lm.group(1)
    prose = body[lm.end():].lstrip("\n")
    return fm_text, links_block, prose


def write_entity(path, fm_text, links_block, prose):
    """Reassemble the entity. fm_text/links_block are passed through verbatim
    (we did not re-serialize them, so no field gets dropped/reordered).
    Atomic via tmp+rename so an interrupt can't leave half-written prose."""
    out = f"---\n{fm_text}\n---\n\n{links_block}\n\n{prose.rstrip()}\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(out)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


# Same-line whitespace only: \s would match newlines too, letting the regex
# pathologically eat past `status:` into the following key on a degenerate
# `status:\n  other: foo` (validate would catch that input, but constraining
# the regex is cheaper than relying on upstream validation).
# Match ONLY the status token, so a substitution replaces the value and leaves
# any trailing ` # comment` on the line intact.
STATUS_LINE_RE = re.compile(r"^(status:[ \t]*)\S+", re.M)
COMPILED_FROM_LINE_RE = re.compile(r"^compiled_from_hash:[ \t]*[^\n]*$", re.M)


def update_status_fields(fm_text, new_status, compiled_from_hash):
    """Mutate ONLY `status:` (in-place replace) and ensure a
    `compiled_from_hash:` line exists, set to the given value. Every other
    frontmatter field, including comments and ordering, is left untouched.
    This is the ONLY frontmatter mutation Stage 3 is allowed to make.

    Raises if `status:` isn't found — silent no-op would be a false
    idempotency that hides a malformed entity from the operator."""
    if not STATUS_LINE_RE.search(fm_text):
        raise RuntimeError("compile contract violation: no `status:` line "
                           "found in frontmatter — refusing to write")
    new_fm = STATUS_LINE_RE.sub(f"\\g<1>{new_status}", fm_text, count=1)
    if COMPILED_FROM_LINE_RE.search(new_fm):
        new_fm = COMPILED_FROM_LINE_RE.sub(
            f"compiled_from_hash: {compiled_from_hash}", new_fm, count=1)
    else:
        new_fm = new_fm.rstrip() + f"\ncompiled_from_hash: {compiled_from_hash}"
    return new_fm


# ----------------------------------------------------------------------------
# Driver — finds work, builds prompts, calls client, writes prose.
# ----------------------------------------------------------------------------
def load_vocabulary(vault):
    """Snapshot of the registry the compile pass is allowed to reference."""
    schema_path = os.path.join(vault, "registry", "schema.yaml")
    with open(schema_path, encoding="utf-8") as f:
        schema = yaml.safe_load(f) or {}
    types = list((schema.get("types") or {}).keys())
    subtypes_by_type = {t: (schema["types"][t].get("subtypes") or [])
                        for t in types}
    tags = list((schema.get("tags") or {}).keys())
    return {"types": types, "subtypes_by_type": subtypes_by_type, "tags": tags}


def collect_source_materials(vault, fm):
    """Read each path in fm['sources'] and re-extract its text. Email
    attachments (path#fragment) reuse the parent .eml's extractor.

    Importing ingest.py here is deliberate — the compile pass mustn't
    invent its own extractors; the source-of-truth for "what text does
    Python see in this raw file" is the ingestion module."""
    from . import ingest

    fact_res = _fact_regexes()
    protected = collect_protected_values(fm)
    materials = []
    for ref in (fm.get("sources") or []):
        path_part = ref.split("#", 1)[0]
        full = os.path.join(vault, path_part)
        if not os.path.exists(full):
            materials.append({"ref": ref, "text": "", "raw_chars": 0})
            continue
        kind = ingest.detect_kind(full)
        ex = ingest.EXTRACTORS.get(kind, ingest.extract_binary)(full)
        text = ex.get("text", "")
        if "#" in ref:
            # email attachment: pull just this part's text from the .eml
            wanted = ref.split("#", 1)[1]
            for a in (ex.get("attachments") or []):
                if a.get("filename") == wanted:
                    sub_kind = ingest._kind_from_filename(a["filename"],
                                                          a.get("content_type", ""))
                    text, _meta = ingest._extract_inmemory(a["bytes"], sub_kind)
                    break
        raw_chars = len(text or "")
        text = denoise_text(text, protected, fact_res)
        materials.append({"ref": ref, "text": text, "raw_chars": raw_chars})
    return materials


def find_pending(vault):
    """Yield (path, raw, fm_text, fm) for every entity that needs compile:
    either status==stub, OR sources_hash drifted from compiled_from_hash."""
    for d, _, files in os.walk(os.path.join(vault, "entities")):
        for fn in sorted(files):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(d, fn)
            raw = open(path, encoding="utf-8").read()
            fm_text, _links, _prose = split_entity(raw)
            if fm_text is None:
                continue
            try:
                fm = yaml.safe_load(fm_text) or {}
            except yaml.YAMLError:
                continue
            status = fm.get("status")
            if status == "stub":
                yield path, raw, fm_text, fm
            elif status == "compiled":
                if fm.get("compiled_from_hash") != fm.get("sources_hash"):
                    yield path, raw, fm_text, fm
            # needs-review / unknown / archived are intentionally skipped:
            # a human curates those before they're worth the compile cost.


def append_proposals(vault, entity_slug, source_refs, model, proposals):
    """Append each proposal to discovery/proposals.jsonl with provenance."""
    if not proposals:
        return 0
    path = os.path.join(vault, "discovery", "proposals.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for p in proposals:
            record = {
                "ts": ts,
                "from_entity": entity_slug,
                "from_sources": source_refs,
                "model": model,
                "contract_version": PROMPT_CONTRACT_VERSION,
                "proposal": p,
            }
            f.write(json.dumps(record) + "\n")
            n += 1
        f.flush(); os.fsync(f.fileno())
    return n


def compile_one(vault, path, raw, fm_text, fm, client, vocab):
    """Drive the LLM call for one entity. Returns a result dict."""
    sources = collect_source_materials(vault, fm)
    user_prompt = render_user_prompt(fm, sources, vocab)
    prompt_chars = len(user_prompt)
    prompt_tokens_est = estimate_tokens(user_prompt) + estimate_tokens(SYSTEM_PROMPT)
    src_raw = sum(s.get("raw_chars", len(s.get("text", ""))) for s in sources)
    src_sent = sum(len(s.get("text", "")) for s in sources)

    try:
        result = client.compile(SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        return {"ok": False, "slug": fm.get("slug", "?"), "error": str(e)}

    prose = (result.get("prose") or "").strip()
    if not prose:
        return {"ok": False, "slug": fm.get("slug", "?"),
                "error": "model returned no <prose> block"}

    # Append proposals BEFORE flipping the entity to compiled: if we crashed
    # in between the other way round, the entity would be `compiled` and its
    # discoveries lost forever. This order at worst re-appends the same
    # proposals on a retry, which promote dedups (sightings count distinct
    # from_entities, and promoted.jsonl rejects already-decided concepts).
    n_proposals = append_proposals(
        vault, fm.get("slug", "?"),
        fm.get("sources") or [],
        result.get("model", client.name),
        result.get("proposals") or []
    )

    # Preserve the link block verbatim — Python owns it; compile must never touch it.
    _fm_text, links_block, _old_prose = split_entity(raw)
    new_fm = update_status_fields(fm_text, "compiled",
                                  fm.get("sources_hash", ""))
    write_entity(path, new_fm, links_block, prose)

    return {"ok": True, "slug": fm.get("slug", "?"),
            "model": result.get("model", client.name),
            "proposals": n_proposals,
            "prose_chars": len(prose),
            "prompt_chars": prompt_chars,
            "prompt_tokens_est": prompt_tokens_est,
            "source_chars_raw": src_raw,
            "source_chars_sent": src_sent}


def compile_all(vault, client=None, limit=None, only=None):
    with vault_lock(vault):
        return _compile_all_locked(vault, client, limit, only)


def _compile_all_locked(vault, client=None, limit=None, only=None):
    """Walk pending entities and compile each one. Returns a summary dict."""
    client = client or get_client()
    vocab = load_vocabulary(vault)
    out = {"client": client.name, "contract_version": PROMPT_CONTRACT_VERSION,
           "compiled": [], "failed": [], "proposals_logged": 0,
           "total_prompt_tokens_est": 0, "total_prompt_chars": 0,
           "total_source_chars_raw": 0, "total_source_chars_sent": 0}
    pending = list(find_pending(vault))
    if only:
        pending = [t for t in pending if (t[3].get("slug") == only)]
    if limit:
        pending = pending[:limit]
    for path, raw, fm_text, fm in pending:
        try:
            r = compile_one(vault, path, raw, fm_text, fm, client, vocab)
        except Exception as e:
            # One malformed entity (e.g. frontmatter without a `status:` line)
            # must not abort the whole pass — record it and move on.
            r = {"ok": False, "slug": fm.get("slug", os.path.basename(path)),
                 "error": f"{type(e).__name__}: {e}"}
        if r["ok"]:
            out["compiled"].append(r)
            out["proposals_logged"] += r["proposals"]
            out["total_prompt_tokens_est"] += r.get("prompt_tokens_est", 0)
            out["total_prompt_chars"] += r.get("prompt_chars", 0)
            out["total_source_chars_raw"] += r.get("source_chars_raw", 0)
            out["total_source_chars_sent"] += r.get("source_chars_sent", 0)
        else:
            out["failed"].append(r)
    return out


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main():
    args = sys.argv[1:]
    vault = "."
    limit = None
    only = None
    model_override = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            print(__doc__); return 0
        elif a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1]); i += 2; continue
        elif a == "--model" and i + 1 < len(args):
            model_override = args[i + 1]; i += 2; continue
        elif a == "--only" and i + 1 < len(args):
            only = args[i + 1]; i += 2; continue
        elif not a.startswith("-"):
            vault = a
        i += 1

    client = get_client(model_override=model_override)
    print(f"compile pass: client={client.name}, contract={PROMPT_CONTRACT_VERSION}, vault={vault}")
    summary = compile_all(vault, client=client, limit=limit, only=only)

    if not summary["compiled"] and not summary["failed"]:
        print("nothing to compile (no stubs, no drifted sources_hash)")
        return 0

    print(f"\ncompiled {len(summary['compiled'])} entit"
          f"{'y' if len(summary['compiled']) == 1 else 'ies'}, "
          f"{len(summary['failed'])} failed, "
          f"{summary['proposals_logged']} proposal(s) logged")
    for r in summary["compiled"]:
        print(f"  ok    {r['slug']:40}  {r['prose_chars']:5}c  "
              f"~{r.get('prompt_tokens_est', 0):5}tok  "
              f"{r['proposals']} prop  ({r['model']})")
    for r in summary["failed"]:
        print(f"  FAIL  {r['slug']:40}  {r['error']}")
    saved = summary["total_source_chars_raw"] - summary["total_source_chars_sent"]
    print(f"\ntotal prompt cost: ~{summary['total_prompt_tokens_est']} tokens "
          f"across {len(summary['compiled'])} entit"
          f"{'y' if len(summary['compiled']) == 1 else 'ies'} "
          f"({summary['total_prompt_chars']} prompt chars; "
          f"denoise+budget trimmed {saved} source chars)")
    # Exit non-zero ONLY on total failure (nothing compiled but some failed),
    # so a single per-entity timeout doesn't abort the weekly cadence — the
    # entities that DID compile still get drained by the promote step.
    return 1 if (summary["failed"] and not summary["compiled"]) else 0


if __name__ == "__main__":
    sys.exit(main())

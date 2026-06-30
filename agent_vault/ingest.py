#!/usr/bin/env python3
"""
ingest.py â€” Stage 2. Walk raw/, classify deterministically, write entity stubs.

NO LLM. NO NETWORK. Pure Python.

The flow:
    raw/ file ->  hash (idempotent intake)
              ->  detect kind (python-magic, extension fallback)
              ->  extract text + metadata (pdf/email/jpeg/text)
              ->  for email: also yield each attachment as a sub-subject
              ->  classify (biller patterns + shape patterns + file priors)
              ->  write stub (frontmatter + empty link block + empty prose)
              ->  append to _manifest.jsonl
    end       ->  rebuild _index.json via build_index.main()

Ownership: this script writes frontmatter + the link block; never prose.
A low-confidence classification still produces a findable stub, flagged
`status: needs-review` so a human can correct it before the compile pass.

Usage:
    python3 ingest.py [VAULT_DIR]
"""
import sys, os, re, json, hashlib, datetime, email, email.policy, io

# Force UTF-8 stdout/stderr so entity slugs/titles with Unicode don't crash on
# Windows consoles that default to cp1252.
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

try:
    import secret_scan          # spec Â§7 write-side guard; shipped alongside
except ImportError:             # degrade to no-op if somehow absent
    secret_scan = None


# --- defaults / paths -------------------------------------------------------

VAULT = "."
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NEEDS_REVIEW_BELOW = 0.75
UNKNOWN_BELOW = 0.40
# Skip oversized raw files (avoid OOM / zip-bomb decompression). Override with
# AGENT_VAULT_MAX_RAW_MB. The file is recorded in the manifest as skipped so it isn't
# retried every run; raise the cap or split the file to ingest it.
MAX_RAW_BYTES = int(os.environ.get("AGENT_VAULT_MAX_RAW_MB", "50")) * 1024 * 1024
# Cap decompressed Office-XML to defuse zip bombs (a small .docx/.xlsx can
# inflate to GBs). Cap the classification haystack â€” signals live in the header /
# first page, so scanning megabytes against ~100 billers is wasted work.
MAX_DECOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_HAYSTACK_CHARS = 65536
MAX_EMAIL_ATTACHMENTS = 50


# --- optional-extractor availability ---------------------------------------

def missing_extractors():
    """Return the list of optional libraries that aren't importable. Without
    them ingestion still runs, but PDFs/images yield no text and fall back to
    file-type priors â€” so a vault that should classify richly will instead
    pile up in `unknown`. We surface this once at the top of a run rather than
    letting the operator wonder why every statement landed in needs-review."""
    missing = []
    for lib, what in (("pdfplumber", "PDF text extraction"),
                      ("PIL", "JPEG/PNG EXIF extraction"),
                      ("magic", "content-sniffing for extension-less files")):
        try:
            __import__(lib)
        except Exception:
            # Broad by design: python-magic can raise non-ImportError errors
            # when the underlying libmagic C library is missing/misconfigured.
            # detect_kind() guards its real magic import the same way, so the
            # probe must not be stricter than the code path it's predicting.
            missing.append((lib, what))
    return missing


def warn_missing_extractors():
    """Print a one-time stderr notice listing absent optional extractors."""
    missing = missing_extractors()
    if not missing:
        return
    print("warn: optional extractors unavailable â€” classification will be "
          "degraded (text-less sources fall through to `unknown`):",
          file=sys.stderr)
    for lib, what in missing:
        print(f"      - {lib}: {what}", file=sys.stderr)
    pkgs = " ".join("pillow" if lib == "PIL" else
                    "python-magic" if lib == "magic" else lib
                    for lib, _ in missing)
    print(f"      install with:  pip install {pkgs}", file=sys.stderr)


# --- file-type detection ----------------------------------------------------

def detect_kind(path):
    """Return a coarse kind: pdf, email, jpeg, png, text, csv, binary."""
    ext = os.path.splitext(path)[1].lower()
    by_ext = {".pdf": "pdf", ".eml": "email", ".jpg": "jpeg", ".jpeg": "jpeg",
              ".png": "png", ".txt": "text", ".csv": "csv", ".md": "text",
              ".docx": "docx", ".xlsx": "xlsx", ".html": "html", ".htm": "html"}
    if ext in by_ext:
        return by_ext[ext]
    # Fall back to libmagic on the first 2KB
    try:
        import magic
        with open(path, "rb") as f:
            head = f.read(2048)
        sig = magic.from_buffer(head).lower()
        if "pdf document" in sig: return "pdf"
        if "jpeg image" in sig:   return "jpeg"
        if "png image" in sig:    return "png"
        if "mail" in sig or "rfc 822" in sig: return "email"
        if "word" in sig or "opendocument text" in sig: return "docx"
        if "excel" in sig or "spreadsheet" in sig: return "xlsx"
        if "html document" in sig: return "html"
        if "ascii text" in sig or "utf-8" in sig: return "text"
    except Exception:
        pass
    return "binary"


# --- extractors -------------------------------------------------------------

def extract_pdf(path):
    try:
        import pdfplumber
    except ImportError:
        return {"text": "", "meta": {"warn": "pdfplumber not installed"}}
    text_parts = []
    with pdfplumber.open(path) as p:
        for page in p.pages:
            t = page.extract_text() or ""
            if t:
                text_parts.append(t)
    return {"text": "\n".join(text_parts), "meta": {"pages": len(text_parts)}}


def extract_text(path):
    with open(path, "rb") as f:
        data = f.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    return {"text": text, "meta": {}}


def extract_email(path):
    """Parse an .eml. Returns the message body text, headers, and any attachments.

    Attachments are kept in-memory as (filename, content_type, bytes) â€” they
    are NOT written back to raw/. Each becomes its own logical sub-subject
    referenced as path#fragment.
    """
    with open(path, "rb") as f:
        msg = email.message_from_bytes(f.read(), policy=email.policy.default)
    headers = {k: str(msg[k]) for k in ("From", "To", "Subject", "Date") if msg[k]}
    body_parts = []
    attachments = []
    for part in msg.walk():
        cd = str(part.get("Content-Disposition", ""))
        ctype = part.get_content_type()
        if "attachment" in cd or part.get_filename():
            if len(attachments) >= MAX_EMAIL_ATTACHMENTS:
                continue  # cap fan-out / memory on pathological emails
            fname = part.get_filename() or "attachment"
            try:
                payload = part.get_payload(decode=True) or b""
            except Exception:
                payload = b""
            if len(payload) > MAX_RAW_BYTES:
                payload = b""  # oversize attachment: skip its bytes, keep the stub
            attachments.append({"filename": fname, "content_type": ctype, "bytes": payload})
        elif ctype == "text/plain":
            try:
                body_parts.append(part.get_content())
            except Exception:
                pass
    text = "\n".join([f"From: {headers.get('From','')}",
                      f"Subject: {headers.get('Subject','')}",
                      "", *body_parts])
    return {"text": text, "meta": {"headers": headers}, "attachments": attachments}


def extract_jpeg(path):
    try:
        from PIL import Image
    except ImportError:
        return {"text": "", "meta": {"warn": "pillow not installed"}}
    meta = {}
    text_bits = []
    with Image.open(path) as img:
        exif = img.getexif()
        # Common EXIF tags by numeric id (avoids PIL version skew on ExifTags names).
        # 271 Make, 272 Model, 306 DateTime, 270 ImageDescription,
        # 36867 DateTimeOriginal, 0x8825 GPSInfo
        if 306 in exif:
            meta["datetime"] = str(exif[306])
        if 36867 in exif:
            meta["datetime_original"] = str(exif[36867])
        if 270 in exif:
            meta["description"] = str(exif[270])
            text_bits.append(meta["description"])
        if 271 in exif:
            meta["make"] = str(exif[271])
        if 272 in exif:
            meta["model"] = str(exif[272])
        gps = exif.get_ifd(0x8825) if hasattr(exif, "get_ifd") else {}
        if gps:
            try:
                lat = _gps_to_decimal(gps.get(2), gps.get(1))
                lon = _gps_to_decimal(gps.get(4), gps.get(3))
                if lat is not None and lon is not None:
                    meta["gps"] = [lat, lon]
            except Exception:
                pass
    return {"text": " ".join(text_bits), "meta": meta}


def _gps_to_decimal(dms, ref):
    if not dms or not ref:
        return None
    d, m, s = [float(x) for x in dms]
    val = d + m / 60.0 + s / 3600.0
    return -val if ref in ("S", "W") else val


def extract_binary(path):
    return {"text": "", "meta": {}}


# --- Office / web extractors (pure stdlib: zipfile + ElementTree + html.parser).
# No python-docx/openpyxl dependency. Each degrades to empty text on any error,
# matching the pdf/PIL posture, so a malformed file never crashes a run.

# Office Open XML uses this namespace for the main wordprocessing content.
_OOXML_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_OOXML_A = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _docx_text_from_bytes(data):
    import io, zipfile
    from xml.etree import ElementTree as ET
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if z.getinfo("word/document.xml").file_size > MAX_DECOMPRESSED_BYTES:
            raise ValueError("docx document.xml exceeds decompressed-size cap")
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(f"{_OOXML_W}p"):
        runs = [t.text or "" for t in p.iter(f"{_OOXML_W}t")]
        line = "".join(runs).strip()
        if line:
            paras.append(line)
    return "\n".join(paras)


def extract_docx(path):
    try:
        with open(path, "rb") as f:
            txt = _docx_text_from_bytes(f.read())
        return {"text": txt, "meta": {"format": "docx"}}
    except Exception as e:
        return {"text": "", "meta": {"warn": f"docx extract failed: {e}"}}


def _xlsx_text_from_bytes(data):
    import io, zipfile
    from xml.etree import ElementTree as ET
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        if sum(i.file_size for i in z.infolist()) > MAX_DECOMPRESSED_BYTES:
            raise ValueError("xlsx exceeds decompressed-size cap")
        # shared strings table (cell text is often stored here by index)
        shared = []
        if "xl/sharedStrings.xml" in names:
            sroot = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in sroot.iter(f"{_OOXML_A}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_OOXML_A}t")))
        rows = []
        sheets = sorted(n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        for sheet in sheets:
            root = ET.fromstring(z.read(sheet))
            for row in root.iter(f"{_OOXML_A}row"):
                cells = []
                for c in row.iter(f"{_OOXML_A}c"):
                    v = c.find(f"{_OOXML_A}v")
                    if v is None or v.text is None:
                        continue
                    if c.get("t") == "s":  # shared-string index
                        try:
                            cells.append(shared[int(v.text)])
                        except (ValueError, IndexError):
                            cells.append("")
                    else:
                        cells.append(v.text)
                if cells:
                    rows.append("\t".join(cells))
    return "\n".join(rows)


def extract_xlsx(path):
    try:
        with open(path, "rb") as f:
            txt = _xlsx_text_from_bytes(f.read())
        return {"text": txt, "meta": {"format": "xlsx"}}
    except Exception as e:
        return {"text": "", "meta": {"warn": f"xlsx extract failed: {e}"}}


class _HTMLTextExtractor:
    """Minimal stdlib HTMLâ†’text: drop script/style, keep visible text."""
    def __init__(self):
        from html.parser import HTMLParser

        class _P(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.parts = []
                self._skip = 0

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style"):
                    self._skip += 1

            def handle_endtag(self, tag):
                if tag in ("script", "style") and self._skip:
                    self._skip -= 1

            def handle_data(self, data):
                if not self._skip and data.strip():
                    self.parts.append(data.strip())

        self._p = _P()

    def feed(self, text):
        self._p.feed(text)
        return "\n".join(self._p.parts)


def extract_html(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1", errors="replace")
        return {"text": _HTMLTextExtractor().feed(text), "meta": {"format": "html"}}
    except Exception as e:
        return {"text": "", "meta": {"warn": f"html extract failed: {e}"}}


EXTRACTORS = {
    "pdf": extract_pdf,
    "email": extract_email,
    "jpeg": extract_jpeg,
    "png": extract_jpeg,
    "text": extract_text,
    "csv": extract_text,
    "docx": extract_docx,
    "xlsx": extract_xlsx,
    "html": extract_html,
    "binary": extract_binary,
}


# --- classification ---------------------------------------------------------

def load_patterns(vault):
    p = os.path.join(vault, "registry", "patterns.yaml")
    if not os.path.exists(p):
        return {"billers": [], "shapes": [], "file_type_priors": {}}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# --- learned field mappings (registry/field_mappings.yaml) -----------------
#
# SAFETY PROPERTY: built-in extractors (DUE_RE / AMOUNT_RE / ...) are trusted —
# they are human-authored Python. Learned field mappings are LLM-proposed via
# the compile pass and then HUMAN-GATED (promote.py) before landing in
# registry/field_mappings.yaml, so they are trusted AT extraction time. But:
#   1. They run through secret_scan exactly like the built-in field values do
#      (build_stub.redact-on-write), AND extract_fields blocks any capture
#      that is itself secret-shaped, so a learned mapping can NEVER write a
#      secret-shaped value into a stub field.
#   2. They are STRICTLY ADDITIVE — a learned mapping never overrides a field a
#      built-in already set (built-ins take precedence).
#   3. A single malformed promoted regex DEGRADES to "drop that one mapping" at
#      load time — it never crashes ingest. A missing/malformed file degrades to
#      built-ins only. A bad learned config must never abort intake.
_FIELD_MAPPINGS_CACHE = {}   # keyed by vault path; per-run cache


def load_field_mappings(vault):
    """Read registry/field_mappings.yaml, compile each mapping's `pattern`
    exactly ONCE (re.compile), and return a list of compiled-mapping dicts.

    Returns [] when the file is absent OR malformed (a bad learned config must
    DEGRADE to built-ins only, never abort intake). One bad promoted regex is
    DROPPED individually with a stderr warn; the rest still load. Cached per-run
    keyed by vault path so extract_fields reuses compiled objects across every
    subject in the same ingest pass.

    Each returned dict has: id, applies_to, cre (compiled regex), field, group,
    parse, require_digit.
    """
    if vault in _FIELD_MAPPINGS_CACHE:
        return _FIELD_MAPPINGS_CACHE[vault]
    path = os.path.join(vault, "registry", "field_mappings.yaml")
    compiled = []
    if not os.path.exists(path):
        _FIELD_MAPPINGS_CACHE[vault] = compiled
        return compiled
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        # A malformed learned config must never abort intake — degrade to
        # built-ins only.
        print(f"warn: registry/field_mappings.yaml is malformed ({e}); "
              f"learned field extraction disabled for this run",
              file=sys.stderr)
        _FIELD_MAPPINGS_CACHE[vault] = compiled
        return compiled
    # Accept either a bare list or a dict with a top-level list under a key.
    entries = []
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        for key in ("mappings", "field_mappings", "learned_mappings"):
            if isinstance(raw.get(key), list):
                entries = raw[key]
                break
    for m in entries:
        if not isinstance(m, dict):
            continue
        pattern = m.get("pattern")
        field = m.get("field")
        if not pattern or not field:
            print(f"warn: learned field mapping missing pattern/field "
                  f"(id={m.get('id')!r}); dropped", file=sys.stderr)
            continue
        try:
            cre = re.compile(pattern)
        except re.error as e:
            # One bad promoted regex must not poison all intake — drop just it.
            print(f"warn: learned field mapping {m.get('id')!r} has bad regex "
                  f"({e}); dropped (one bad promoted regex must not poison "
                  f"intake)", file=sys.stderr)
            continue
        try:
            group = int(m.get("group", 1))
        except (TypeError, ValueError):
            group = 1
        parse_type = m.get("parse") or "text"
        if parse_type not in ("text", "float", "int", "iso-date"):
            parse_type = "text"
        compiled.append({
            "id": m.get("id"),
            "applies_to": m.get("applies_to"),
            "cre": cre,
            "field": field,
            "group": group,
            "parse": parse_type,
            "require_digit": bool(m.get("require_digit", False)),
        })
    _FIELD_MAPPINGS_CACHE[vault] = compiled
    return compiled


def _apply_learned_parse(raw, parse_type):
    """Coerce a captured string into the mapping's declared type. Returns None
    on a parse failure (the caller treats None as "no value").
    - text: stripped string (default)
    - float/int: best-effort numeric coercion (commas tolerated)
    - iso-date: reuse _parse_any_date so learned date captures match built-ins
    """
    if not isinstance(raw, str):
        raw = str(raw)
    s = raw.strip()
    if parse_type == "float":
        try:
            return float(s.replace(",", ""))
        except ValueError:
            return None
    if parse_type == "int":
        try:
            return int(s.replace(",", "").split(".")[0])
        except ValueError:
            return None
    if parse_type == "iso-date":
        return _parse_any_date(s)
    return s


def sources_hash(paths):
    """Stable, short identifier for a set of source paths. Changes when the
    source set changes â€” that's the signal the compile pass uses to recompile.
    Path-based (not content-based) so it survives appending a new source even
    when other source files aren't available locally."""
    joined = "\n".join(sorted(paths))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def classify(text, meta, kind, filename, patterns):
    """Compose biller + shape + file-type signals into one classification result.

    Returns dict:
        type, subtype, confidence, biller (id|None), shape (id|None),
        related (list of typed slug refs to add via `related:`),
        tags (list to seed frontmatter).
    """
    # Cap the haystack: classification signals are in the header / first page,
    # and scanning megabytes against ~100 billers Ã— shapes is wasted work.
    hay = ((text or "")[:MAX_HAYSTACK_CHARS] + " " + filename).lower()
    headers = (meta.get("headers") or {})
    hay += " " + " ".join(str(v).lower() for v in headers.values())

    biller = _match_biller(hay, patterns.get("billers") or [])
    shape = _match_shape(hay, kind, patterns.get("shapes") or [])

    result = {"biller": biller["id"] if biller else None,
              "shape":  shape["id"]  if shape  else None,
              "type": None, "subtype": None, "confidence": 0.0,
              "related": [], "tags": []}

    if shape:
        result["type"] = shape["type"]
        result["subtype"] = shape["subtype"]
        result["confidence"] = float(shape["confidence"])

    if biller:
        # Biller suggests existing entities to link to.
        for key, kind_prefix in (("account_slug", "account"),
                                 ("institution_slug", "financial-institution"),
                                 ("vendor_slug", "vendor"),
                                 ("asset_slug", "asset")):
            slug = biller.get(key)
            if slug:
                result["related"].append(f"{kind_prefix}/{slug}")
        result["tags"].extend(biller.get("default_tags") or [])
        # Combined biller + shape gives a confidence boost (capped 0.97).
        if shape:
            result["confidence"] = min(0.97, max(result["confidence"], float(biller["confidence"])))
        elif kind not in ("jpeg", "png"):
            # Biller name in document-ish text without a recognized shape:
            # guess document/statement, but penalize enough that it lands in
            # needs-review unless something else corroborates. For photos we
            # KEEP the media prior â€” a JPEG that mentions a brand isn't
            # automatically a statement.
            result["type"] = result["type"] or "document"
            result["subtype"] = result["subtype"] or "statement"
            result["confidence"] = max(result["confidence"], float(biller["confidence"]) - 0.25)

    if not result["type"]:
        # Either no shape and no biller, or biller-on-image left type unset.
        prior_key = kind if kind != "email" else "email_personal"
        prior = (patterns.get("file_type_priors") or {}).get(prior_key)
        if prior:
            result["type"] = prior["type"]
            result["subtype"] = prior["subtype"]
            result["confidence"] = max(result["confidence"], float(prior["confidence"]))

    # Backstop: still nothing? mark unknown.
    if not result["type"]:
        result["type"] = "unknown"
        result["subtype"] = "unknown"
        result["confidence"] = 0.10

    return result


def _match_biller(hay, billers):
    """Return the highest-confidence biller pattern whose surface form appears."""
    best = None
    for b in billers:
        for m in (b.get("matches") or []):
            if m.lower() in hay:
                if best is None or b["confidence"] > best["confidence"]:
                    best = b
                break
    return best


def _match_shape(hay, kind, shapes):
    """Return the shape with the most keyword hits (ties: highest confidence).
    A shape with `applies_to:` only fires for files of those detected kinds."""
    best = None
    best_hits = 0
    for s in shapes:
        applies = s.get("applies_to")
        if applies and kind not in applies:
            continue
        hits = sum(1 for k in s.get("keywords", []) if k.lower() in hay)
        if hits >= s.get("min_matches", 1):
            if hits > best_hits or (hits == best_hits and best and s["confidence"] > best["confidence"]):
                best = s
                best_hits = hits
    return best


# --- field extraction (dates, amounts, etc.) -------------------------------

DATE_RE = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")
# A date TOKEN in any common shape: ISO, "Month DD, YYYY", "M/D/YYYY", "Month YYYY".
_DATE_TOKEN = (r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}"
               r"|[A-Z][a-z]{2,8}\.?\s+\d{1,2},?\s+\d{4}"
               r"|\d{1,2}/\d{1,2}/\d{4}"
               r"|[A-Z][a-z]{2,8}\.?\s+\d{4})")
PERIOD_RE = re.compile(r"statement period:\s*(20\d{2})[-/](\d{1,2})", re.I)
DUE_RE     = re.compile(r"(?:payment due date|due date|due)\s*[:\-]?\s*" + _DATE_TOKEN, re.I)
EXPIRES_RE = re.compile(r"(?:warranty expires|expiration date|expires?)\s*[:\-]?\s*" + _DATE_TOKEN, re.I)
RENEWS_RE  = re.compile(r"(?:renewal date|renews? on|renews?)\s*[:\-]?\s*" + _DATE_TOKEN, re.I)
AMOUNT_RE = re.compile(r"(?:total(?: due)?|amount due|premium):\s*\$?([\d,]+\.\d{2})", re.I)
ORDER_RE = re.compile(r"order\s*(?:number|no\.?)?\s*[#:]+\s*([A-Z0-9][A-Z0-9-]{5,})", re.I)
POLICY_RE = re.compile(r"policy\s*(?:number|#)?:?\s*([\d-]{6,})", re.I)
PARCEL_RE = re.compile(r"parcel\s*id:?\s*([\d\w.-]{4,})", re.I)
VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")
# last4 only â€” a masked tail or "ending in NNNN". NEVER captures a full account/PAN.
# The mask run is BOUNDED ({2,12}); an unbounded `{3,}` over a permissive class
# before a required `\d{4}` backtracks catastrophically (ReDoS) on long x-runs.
LAST4_RE = re.compile(r"(?:ending(?:\s+in)?\s*|[*xXâ€¢]{2,12}\s?)(\d{4})\b", re.I)
SERIAL_RE = re.compile(r"serial\s*(?:number|no\.?|#)?\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-]{4,})", re.I)
MODEL_RE = re.compile(r"model\s*(?:number|no\.?|#)?\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-]{2,20})", re.I)


def _parse_any_date(tok):
    """Parse a captured date token (ISO / 'Month DD YYYY' / 'M/D/YYYY' /
    'Month YYYY') into ISO YYYY-MM-DD, or None. Deterministic; tolerant of
    commas, periods, and collapsed whitespace.

    NOTE: numeric 'M/D/YYYY' is parsed US month-first; European 'D/M/YYYY'
    documents will mis-parse (03/04/2026 -> March 4). Unambiguous formats
    (ISO, 'Month DD YYYY') are preferred; a stub date is operator-reviewable."""
    if not tok:
        return None
    s = re.sub(r"\s+", " ", tok.strip().replace(",", "").replace(".", ""))
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%B %d %Y", "%b %d %Y",
                "%d %B %Y", "%d %b %Y", "%B %Y", "%b %Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def extract_fields(text, meta, kind, learned_mappings=None):
    """Pull dates, amounts, ids out of extracted text into a small dict.
    Always returns strings (JSON/YAML safe).

    Learned field mappings (optional) run AFTER the built-in regexes and are
    STRICTLY ADDITIVE: they never overwrite a field a built-in already set
    (built-ins take precedence). Each learned capture is guarded with the SAME
    secret_scan the built-in fields use, so a learned mapping can never write a
    secret-shaped value into a stub field."""
    f = {}
    m = DUE_RE.search(text)
    if m and _parse_any_date(m.group(1)): f["due"] = _parse_any_date(m.group(1))
    m = EXPIRES_RE.search(text)
    if m and _parse_any_date(m.group(1)): f["expires"] = _parse_any_date(m.group(1))
    m = RENEWS_RE.search(text)
    if m and _parse_any_date(m.group(1)): f["renews"] = _parse_any_date(m.group(1))
    m = PERIOD_RE.search(text)
    if m: f["period"] = f"{m.group(1)}-{int(m.group(2)):02d}"
    m = AMOUNT_RE.search(text)
    if m:
        try: f["value"] = float(m.group(1).replace(",", ""))
        except ValueError: pass
    m = ORDER_RE.search(text)
    if m: f["order_id"] = m.group(1)
    m = POLICY_RE.search(text)
    if m: f["policy_id"] = m.group(1)
    m = PARCEL_RE.search(text)
    if m: f["parcel_id"] = m.group(1)
    m = VIN_RE.search(text)
    if m: f["vin"] = m.group(1)
    m = LAST4_RE.search(text)
    if m: f["last4"] = m.group(1)
    m = SERIAL_RE.search(text)
    if m: f["serial"] = m.group(1)
    m = MODEL_RE.search(text)
    if m and re.search(r"\d", m.group(1)): f["model"] = m.group(1)  # require a digit
    # EXIF-derived date for photos
    if kind in ("jpeg", "png"):
        dt = (meta.get("datetime_original") or meta.get("datetime") or "")
        m = re.match(r"(\d{4})[:\-/](\d{2})[:\-/](\d{2})", dt)
        if m: f["created"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # --- learned field mappings (run AFTER built-ins; strictly additive) ---
    # See load_field_mappings for the full SAFETY PROPERTY. In short: built-in
    # extractors are trusted human-authored Python; learned mappings are
    # LLM-proposed + human-gated, trusted AT extraction time, but (1) guarded
    # by secret_scan exactly like built-ins, (2) strictly additive, and (3) a
    # single malformed promoted regex degrades to "drop that mapping" at load
    # time, never crashing ingest.
    if learned_mappings and text:
        for lm in learned_mappings:
            applies_to = lm.get("applies_to")
            if applies_to and kind not in applies_to:
                continue
            field = lm["field"]
            if field in f:
                continue  # built-ins take precedence; learned is additive
            mm = lm["cre"].search(text)
            if not mm:
                continue
            try:
                raw_cap = mm.group(lm["group"])
            except IndexError:
                continue  # group index out of range for this match
            if raw_cap is None:
                continue
            val = _apply_learned_parse(raw_cap, lm["parse"])
            if val is None:
                continue
            if lm["require_digit"] and not re.search(r"\d", str(val)):
                continue
            # Guard: a learned mapping must NEVER write a secret-shaped value
            # into a stub field. Use the SAME secret_scan the built-ins use.
            if secret_scan is not None and isinstance(val, str):
                if secret_scan.scan(val):
                    continue  # block — the captured value looks like a secret
                val = secret_scan.redact(val)
            f[field] = val
    return f


# --- slug derivation --------------------------------------------------------

def make_slug(biller_id, classification, fields, filename, kind):
    """Build a stable, schema-valid slug for a stub.

    Strategy: a slug only gets a biller+shape prefix if there's a temporal/ID
    anchor that disambiguates it (period, due/expires/renews date, or an
    order_id). Without that anchor we'd get collisions like every-receipt
    landing on the same slug, so we fall back to the filename stem instead â€”
    classification info still lives in `type`/`subtype`.

    Preference order:
      <biller>-<shape>-<key>           e.g. bofa-statement-2026-01
      <biller>-<key>                   e.g. amazon-7714238
      <filename-stem>                  e.g. carrier-furnace-warranty
    """
    shape = classification.get("shape")
    key = (fields.get("period")
           or (fields.get("due") and fields["due"][:7])
           or (fields.get("expires") and fields["expires"][:7])
           or (fields.get("renews") and fields["renews"][:7])
           or (fields.get("order_id") and fields["order_id"][-7:].lower()))

    file_stem = os.path.splitext(os.path.basename(filename.split("#")[-1]))[0]
    bits = []
    if biller_id and key:
        bits = [biller_id]
        if shape: bits.append(shape)
        bits.append(key)
    else:
        bits = [file_stem]

    candidate = "-".join(_clean_slug_bit(b) for b in bits if b)
    candidate = re.sub(r"-+", "-", candidate).strip("-")
    return candidate or f"unknown-{hashlib.sha1(filename.encode()).hexdigest()[:8]}"


def _clean_slug_bit(s):
    s = re.sub(r"[^a-z0-9]+", "-", str(s).lower())
    return s.strip("-")


# --- manifest ---------------------------------------------------------------

def load_manifest(vault):
    p = os.path.join(vault, "raw", "_manifest.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line:
            try: out.append(json.loads(line))
            except json.JSONDecodeError: pass
    return out


def append_manifest(vault, entry):
    p = os.path.join(vault, "raw", "_manifest.jsonl")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush(); os.fsync(f.fileno())  # idempotency ledger must survive power loss


# --- stub writer ------------------------------------------------------------

def render_stub(stub):
    """Render a stub dict into the entity-template markdown. Empty prose body."""
    fm_lines = []
    # Required fields first, in canonical order
    fm_lines.append(f"slug: {stub['slug']}")
    fm_lines.append(f"type: {stub['type']}")
    fm_lines.append(f"subtype: {stub['subtype']}")
    fm_lines.append(f"title: {_yaml_str(stub['title'])}")
    fm_lines.append(f"status: {stub['status']}")
    fm_lines.append(f"confidence: {stub['confidence']:.2f}")
    fm_lines.append(f"created: {stub['created']}")
    # Sources
    fm_lines.append("sources:")
    for s in stub["sources"]:
        fm_lines.append(f"  - {_yaml_str(s)}")
    fm_lines.append(f"sources_hash: {stub['sources_hash']}")
    # Optional fields
    if stub.get("tags"):
        # YAML flow-style list of scalars; quote anything that isn't a plain slug.
        tag_strs = [t if re.match(r"^[a-z0-9][a-z0-9-]*$", str(t)) else json.dumps(str(t))
                    for t in stub["tags"]]
        fm_lines.append(f"tags: [{', '.join(tag_strs)}]")
    for k in ("due", "expires", "renews", "acquired", "serviced"):
        if stub.get(k): fm_lines.append(f"{k}: {stub[k]}")
    if stub.get("value") is not None:
        fm_lines.append(f"value: {stub['value']}")
    if stub.get("vin"):
        fm_lines.append(f"vin: {_yaml_str(stub['vin'])}")
    for k in ("serial", "model", "last4"):
        if stub.get(k):
            fm_lines.append(f"{k}: {_yaml_str(stub[k])}")
    if stub.get("location"):
        fm_lines.append(f"location: {_yaml_str(stub['location'])}")
    if stub.get("related"):
        fm_lines.append("related:")
        for r in stub["related"]:
            fm_lines.append(f"  - {_yaml_str(r)}")
    if stub.get("notes"):
        fm_lines.append("# notes (Python-owned; classifier diagnostics):")
        for k, v in stub["notes"].items():
            # notes are written as YAML comments (invisible to a yaml.safe_load
            # secret gate), so redact strict-secret-shaped values here at the
            # single write choke point.
            if secret_scan and isinstance(v, str):
                v = secret_scan.redact(v)
            fm_lines.append(f"#   {k}: {v}")

    links = _render_link_block(stub.get("related") or [])
    return f"---\n{chr(10).join(fm_lines)}\n---\n\n{links}\n"


def _yaml_str(s):
    """Quote a YAML scalar when YAML would otherwise misparse it. Conservative:
    flags leading-special chars (-, ?, !, %, &, *, |, >, @, `), special interior
    chars (:, #, [, ], {, }), and surrounding whitespace; falls back to JSON
    quoting so embedded quotes/backslashes are escaped properly. Aligned with
    collections_importer._yaml_quote â€” same rules in both writers."""
    s = str(s)
    if s == "":
        return '""'
    if s != s.strip():
        return json.dumps(s)
    if s[0] in "-?!%&*|>@`'\"" or s.startswith(("- ", "? ", ": ")):
        return json.dumps(s)
    if re.search(r"[:#\[\]{}\n]", s):
        return json.dumps(s)
    return s


def resolve_stub_path(vault, stub, reserved=None):
    """Decide the final on-disk path for a stub WITHOUT writing. Mutates
    stub['slug'] when disambiguation is needed, so callers can wire `related:`
    refs against the final slug before anything touches disk.

    By the time we reach here, the outer ingest loop has already deduped by
    raw-file hash (manifest). So if the target path exists, it's one of:
      (a) re-ingest of the SAME raw set after manifest cleared
          (identical sources -> overwrite is idempotent and correct);
      (b) two DIFFERENT raw files classified to the same slug
          (real collision -> must disambiguate, not silently overwrite);
      (c) the existing stub was an inferred auto-stub with sources: []
          (this real raw file should TAKE OVER that slug, not collide).
    `reserved` is a set of paths claimed by sibling stubs in this same run
    that haven't been written yet (they collide like on-disk files do).

    Returns (path, should_write); should_write is False when the existing
    entity is compiled/archived (human-owned -> never clobbered).
    """
    reserved = reserved if reserved is not None else set()
    d = os.path.join(vault, "entities", stub["type"])
    path = os.path.join(d, f"{stub['slug']}.md")
    if path in reserved:
        # A sibling stub in this run already claimed the slug.
        stub["slug"] = _disambiguate_slug(d, stub["slug"], reserved)
        path = os.path.join(d, f"{stub['slug']}.md")
    elif os.path.exists(path):
        existing = open(path, encoding="utf-8").read()
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", existing, re.S)
        parsed = None
        if m:
            try:
                parsed = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                parsed = None
        if parsed is None:
            # Existing file is unparseable â€” it may be a hand-edited entity with
            # real prose. NEVER clobber something we can't read; disambiguate.
            stub["slug"] = _disambiguate_slug(d, stub["slug"], reserved)
            path = os.path.join(d, f"{stub['slug']}.md")
        else:
            fm = parsed
            if fm.get("status") not in ("stub", "needs-review", "unknown"):
                return path, False  # compiled/archived: human owns it
            existing_sources = list(fm.get("sources") or [])
            new_sources = list(stub["sources"])
            if fm.get("inferred") and not existing_sources:
                pass  # (c) replace the inferred placeholder
            elif sorted(existing_sources) == sorted(new_sources):
                pass  # (a) identical source set, overwrite is idempotent
            else:
                # (b) genuine collision. Disambiguate.
                stub["slug"] = _disambiguate_slug(d, stub["slug"], reserved)
                path = os.path.join(d, f"{stub['slug']}.md")
    reserved.add(path)
    return path, True


def write_stub(vault, stub, path=None):
    """Write a stub to disk. When `path` is None the slot is resolved here
    (single-stub callers); multi-stub callers resolve all slots up front via
    resolve_stub_path so cross-links use final slugs."""
    if path is None:
        path, should_write = resolve_stub_path(vault, stub)
        if not should_write:
            return path, False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(render_stub(stub))
        f.flush(); os.fsync(f.fileno())  # durability: survive power loss before rename
    os.replace(tmp, path)
    return path, True


def _disambiguate_slug(dir_, base, reserved=None):
    """Append -2, -3, ... until we find a slug unused on disk AND not reserved
    by a sibling stub in the current run."""
    reserved = reserved or set()
    n = 2
    while True:
        candidate = f"{base}-{n}"
        cand_path = os.path.join(dir_, f"{candidate}.md")
        if cand_path not in reserved and not os.path.exists(cand_path):
            return candidate
        n += 1


# --- existing-entity update (frontmatter+link block only; prose untouched) --

PARSE_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
LINKS_RE = re.compile(r"<!-- LINKS:BEGIN -->.*?<!-- LINKS:END -->", re.S)


def update_existing_entity(vault, target_ref, new_source, new_related):
    """Append a raw source path (and an optional back-reference) to an existing
    entity, rehash its sources, regenerate its link block. Prose is left alone.

    target_ref: a "type/slug" ref like "account/bofa-checking".
    new_source: raw path string to add to fm['sources'] if not already present.
    new_related: typed slug ref like "document/bofa-statement-2026-01" to add
                 to fm['related'] (so the link block back-references the new stub).

    Returns True if the file was modified, False otherwise (entity missing, or
    everything was already present so the write would be a no-op).
    """
    if "/" not in target_ref:
        return False
    rtype, rslug = target_ref.split("/", 1)
    path = os.path.join(vault, "entities", rtype, f"{rslug}.md")
    if not os.path.exists(path):
        return False

    raw = open(path, encoding="utf-8").read()
    m = PARSE_RE.match(raw)
    if not m:
        return False
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return False
    body = m.group(2)
    prose = LINKS_RE.sub("", body).strip()

    changed = False
    sources = list(fm.get("sources") or [])
    if new_source and new_source not in sources:
        sources.append(new_source)
        fm["sources"] = sources
        changed = True

    related = list(fm.get("related") or [])
    if new_related and new_related not in related:
        related.append(new_related)
        fm["related"] = related
        changed = True

    if not changed:
        return False

    fm["sources_hash"] = sources_hash(fm.get("sources") or [])

    # Reserialize frontmatter preserving field order from what was there.
    # yaml.safe_dump would reorder; we want deterministic round-trip-ish output.
    new_fm = _dump_frontmatter(fm)
    new_links = _render_link_block(related)
    new_text = f"---\n{new_fm}---\n\n{new_links}\n"
    if prose:
        new_text += "\n" + prose + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_text)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    return True


def _dump_frontmatter(fm):
    """Render a frontmatter dict back to YAML lines in a stable, readable order."""
    order = ["slug", "type", "subtype", "title", "status", "confidence",
             "created", "sources", "sources_hash", "tags", "aliases",
             "location", "value", "value_as_of", "acquired", "expires",
             "renews", "serviced", "due", "serial", "model", "vin", "last4",
             "credential_ref", "related"]
    lines = []
    seen = set()
    for k in order:
        if k in fm:
            lines.append(_yaml_kv(k, fm[k]))
            seen.add(k)
    for k, v in fm.items():
        if k not in seen:
            lines.append(_yaml_kv(k, v))
    return "\n".join(lines) + "\n"


def _yaml_kv(k, v):
    if isinstance(v, list):
        if not v:
            return f"{k}: []"
        out = [f"{k}:"]
        for item in v:
            out.append(f"  - {item if isinstance(item, (int, float, bool)) else _yaml_str(item)}")
        return "\n".join(out)
    if isinstance(v, dict):
        out = [f"{k}:"]
        for kk, vv in v.items():
            out.append(f"  {kk}: {vv if isinstance(vv, (int, float, bool)) else _yaml_str(vv)}")
        return "\n".join(out)
    if isinstance(v, (bool, int, float)):
        return f"{k}: {v}"
    return f"{k}: {_yaml_str(v)}"


def _render_link_block(related):
    inner = ""
    if related:
        inner = "\n".join(f"- Related: [[{r}]]" for r in related) + "\n"
    return f"<!-- LINKS:BEGIN -->\n{inner}<!-- LINKS:END -->"


# --- driver -----------------------------------------------------------------

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_raw_files(vault):
    raw = os.path.join(vault, "raw")
    for d, _, files in os.walk(raw):
        for fn in sorted(files):
            if fn.startswith("_") or fn == ".gitkeep":
                continue
            yield os.path.join(d, fn)


# --- auto-stub for missing related targets ---------------------------------
#
# Biller patterns emit `related: [vendor/amazon]` long before any vendor/amazon
# entity exists. Rather than leaving dangling refs (the lint pass surfaces them
# every month), we create a minimal needs-review stub for each missing target
# at the end of the ingest run. confidence:0.40 makes them sortable as "review
# me", and `inferred: true` documents how they came into being.

# Default subtype per top-level type. Used when we have nothing better.
_DEFAULT_SUBTYPE = {
    "vendor": "retailer",
    "financial-institution": "bank",
    "account": "checking",
    "maintenance": "recurring-task",
    "person": "contact",
    "asset": "appliance",
    "property": "residence",
    "event": "incident",
    "document": "receipt",
    "media": "family-photo",
    "vehicle": "car",
    "collection": "game",
    "unknown": "unknown",
}

# Slug-substring heuristics for picking a better subtype than the default.
# Each entry: (type, substring) -> subtype. First hit wins.
_SUBTYPE_HEURISTICS = [
    ("financial-institution", "fidelity",   "brokerage"),
    ("financial-institution", "vanguard",   "brokerage"),
    ("financial-institution", "schwab",     "brokerage"),
    ("financial-institution", "robinhood",  "brokerage"),
    ("financial-institution", "geico",      "insurer"),
    ("financial-institution", "allstate",   "insurer"),
    ("financial-institution", "state-farm", "insurer"),
    ("financial-institution", "progressive","insurer"),
    ("financial-institution", "liberty-mutual", "insurer"),
    ("financial-institution", "usaa",       "insurer"),
    ("financial-institution", "nationwide", "insurer"),
    ("financial-institution", "farmers",    "insurer"),
    ("financial-institution", "travelers",  "insurer"),
    ("financial-institution", "american-family", "insurer"),
    ("financial-institution", "unitedhealthcare", "insurer"),
    ("financial-institution", "aetna",      "insurer"),
    ("financial-institution", "cigna",      "insurer"),
    ("financial-institution", "blue-cross", "insurer"),
    ("financial-institution", "kaiser",     "insurer"),
    ("financial-institution", "humana",     "insurer"),
    # everything else with a bank-ish name falls to the bank default below
    ("account", "mortgage",       "loan"),
    ("account", "loan",           "loan"),
    ("account", "401k",           "investment"),
    ("account", "ira",            "investment"),
    ("account", "brokerage",      "investment"),
    ("account", "investment",     "investment"),
    ("account", "savings",        "savings"),
    ("account", "checking",       "checking"),
    ("account", "credit-card",    "credit-card"),
    ("account", "card",           "credit-card"),
    ("account", "insurance",      "insurance-policy"),
    ("account", "policy",         "insurance-policy"),
    # vendor: streaming
    ("vendor",  "netflix",        "streaming"),
    ("vendor",  "spotify",        "streaming"),
    ("vendor",  "hulu",           "streaming"),
    ("vendor",  "disney",         "streaming"),
    ("vendor",  "hbo",            "streaming"),
    ("vendor",  "youtube",        "streaming"),
    ("vendor",  "peacock",        "streaming"),
    ("vendor",  "paramount",      "streaming"),
    ("vendor",  "apple-tv",       "streaming"),
    ("vendor",  "twitch",         "streaming"),
    ("vendor",  "streaming",      "streaming"),
    # vendor: subscription / software
    ("vendor",  "audible",        "subscription"),
    ("vendor",  "adobe",          "subscription"),
    ("vendor",  "microsoft",      "subscription"),
    ("vendor",  "google",         "subscription"),
    ("vendor",  "dropbox",        "subscription"),
    ("vendor",  "nyt",            "subscription"),
    ("vendor",  "wsj",            "subscription"),
    ("vendor",  "github",         "subscription"),
    ("vendor",  "openai",         "subscription"),
    ("vendor",  "patreon",        "subscription"),
    ("vendor",  "subscription",   "subscription"),
    # vendor: telecom / internet / utilities -> utility
    ("vendor",  "comcast",        "utility"),
    ("vendor",  "xfinity",        "utility"),
    ("vendor",  "att",            "utility"),
    ("vendor",  "verizon",        "utility"),
    ("vendor",  "t-mobile",       "utility"),
    ("vendor",  "spectrum",       "utility"),
    ("vendor",  "cox",            "utility"),
    ("vendor",  "centurylink",    "utility"),
    ("vendor",  "pge",            "utility"),
    ("vendor",  "duke-energy",    "utility"),
    ("vendor",  "edison",         "utility"),
    ("vendor",  "national-grid",  "utility"),
    ("vendor",  "waste-management", "utility"),
    ("vendor",  "mint-mobile",    "utility"),
    ("vendor",  "visible",        "utility"),
    ("vendor",  "utility",        "utility"),
    # vendor: payments + travel -> service-provider
    ("vendor",  "paypal",         "service-provider"),
    ("vendor",  "venmo",          "service-provider"),
    ("vendor",  "delta",          "service-provider"),
    ("vendor",  "united-airlines","service-provider"),
    ("vendor",  "southwest",      "service-provider"),
    ("vendor",  "marriott",       "service-provider"),
    ("vendor",  "hilton",         "service-provider"),
    ("vendor",  "airbnb",         "service-provider"),
    # vendor: retailers / marketplaces / delivery
    ("vendor",  "walmart",        "retailer"),
    ("vendor",  "target",         "retailer"),
    ("vendor",  "best-buy",       "retailer"),
    ("vendor",  "home-depot",     "retailer"),
    ("vendor",  "lowes",          "retailer"),
    ("vendor",  "kroger",         "retailer"),
    ("vendor",  "ebay",           "retailer"),
    ("vendor",  "etsy",           "retailer"),
    ("vendor",  "instacart",      "retailer"),
    ("vendor",  "doordash",       "retailer"),
    ("vendor",  "uber",           "retailer"),
    ("vendor",  "insurance",      "insurance"),
    ("vendor",  "county",         "government"),
    ("vendor",  "city-of",        "government"),
]


def _pick_subtype(type_, slug, valid_subtypes):
    """Choose a sensible subtype for an inferred entity given (type, slug).
    Falls back to _DEFAULT_SUBTYPE, and finally to the first valid subtype
    in the registry if the default doesn't fit."""
    for t, needle, sub in _SUBTYPE_HEURISTICS:
        if t == type_ and needle in slug and sub in valid_subtypes:
            return sub
    default = _DEFAULT_SUBTYPE.get(type_)
    if default and default in valid_subtypes:
        return default
    return sorted(valid_subtypes)[0] if valid_subtypes else "unknown"


def _slug_to_title(slug):
    """'bank-of-america' -> 'Bank of America' (with the small-word convention)."""
    small = {"of", "the", "and", "for", "in", "on", "to", "a"}
    parts = slug.split("-")
    out = []
    for i, p in enumerate(parts):
        if i > 0 and p in small:
            out.append(p)
        else:
            out.append(p.capitalize())
    return " ".join(out)


def auto_stub_missing_refs(vault):
    """Scan every entity for `related:` refs and create needs-review stubs
    for any target slug that has no entity file. Returns the list of newly
    created (type, slug) pairs."""
    schema = yaml.safe_load(open(os.path.join(vault, "registry", "schema.yaml"),
                                 encoding="utf-8")) or {}
    types = schema.get("types", {})

    # Collect all related refs across all entities.
    edir = os.path.join(vault, "entities")
    refs = {}  # ref -> list of (referring_type, referring_slug)
    for d, _, files in os.walk(edir):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            path = os.path.join(d, fn)
            raw = open(path, encoding="utf-8").read()
            m = PARSE_RE.match(raw)
            if not m: continue
            try: fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError: continue
            ref_type = os.path.basename(d)
            ref_slug = os.path.splitext(fn)[0]
            for r in (fm.get("related") or []):
                r = str(r).strip()
                if "/" not in r:
                    continue
                # Skip back-refs to non-entity content (raw/ paths etc.)
                rtype = r.split("/", 1)[0]
                if rtype not in types:
                    continue
                refs.setdefault(r, []).append(f"{ref_type}/{ref_slug}")

    today = datetime.date.today().isoformat()
    created = []
    # Iterate in sorted order so two ingest runs over the same input set
    # produce byte-identical inferred stubs (deterministic). Also: reject
    # refs whose slug part isn't valid â€” they'd produce filenames that
    # validate.py would then immediately flag as a self-induced lint storm.
    for ref in sorted(refs):
        referrers = refs[ref]
        rtype, rslug = ref.split("/", 1)
        if not SLUG_RE.match(rslug):
            continue  # invalid slug; biller pattern bug, surface via lint
        target = os.path.join(edir, rtype, f"{rslug}.md")
        if os.path.exists(target):
            continue
        if rtype not in types:
            continue
        valid_subs = set(types[rtype].get("subtypes") or [])
        subtype = _pick_subtype(rtype, rslug, valid_subs)
        sh = "inferred-" + hashlib.sha256(f"{rtype}/{rslug}".encode()).hexdigest()[:8]
        os.makedirs(os.path.join(edir, rtype), exist_ok=True)
        fm_lines = [
            "---",
            f"slug: {rslug}",
            f"type: {rtype}",
            f"subtype: {subtype}",
            f"title: {_yaml_str(_slug_to_title(rslug))}",
            "status: needs-review",
            "confidence: 0.40",
            f"created: {today}",
            "sources: []",
            f"sources_hash: {sh}",
            "inferred: true",
            "related:",
        ]
        for back in sorted(set(referrers)):
            fm_lines.append(f"  - {back}")
        fm_lines.append("---")
        fm_lines.append("")
        fm_lines.append(_render_link_block(sorted(set(referrers))))
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(fm_lines) + "\n")
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, target)
        created.append((rtype, rslug))

    return created


def ingest(vault):
    with vault_lock(vault):
        return _ingest_locked(vault)


def load_gated_types(vault):
    """Types flagged `human_gated: true` in the schema: new INSTANCES need
    human approval before they're treated as real (spec Â§3)."""
    try:
        schema = yaml.safe_load(open(os.path.join(vault, "registry", "schema.yaml"),
                                     encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return set()
    return {t for t, spec in (schema.get("types") or {}).items()
            if isinstance(spec, dict) and spec.get("human_gated")}


def enforce_human_gate(stub, gated_types):
    """spec Â§3 enforcement: a stub of a human_gated type can never enter the
    vault as a confident `stub` â€” it lands as `needs-review` (which the
    compile pass skips) until a human approves it (review.py approve-entity
    flips it to `stub`). Mutates and returns the stub."""
    if stub.get("type") in gated_types and stub.get("status") == "stub":
        stub["status"] = "needs-review"
        notes = stub.setdefault("notes", {})
        notes["gated"] = (f"type '{stub['type']}' is human_gated; awaiting "
                          f"approval (review.py approve-entity)")
    return stub


def _ingest_locked(vault):
    warn_missing_extractors()
    patterns = load_patterns(vault)
    learned_field_mappings = load_field_mappings(vault)
    gated_types = load_gated_types(vault)
    manifest = load_manifest(vault)
    # .get(): a hand-edited or foreign manifest record without a hash must not
    # abort the whole run.
    seen_hashes = {m.get("hash") for m in manifest if m.get("hash")}

    today = datetime.date.today().isoformat()
    summary = {"new": 0, "skipped": 0, "stubs": [], "review": [], "unknown": [],
               "inferred": []}

    for path in iter_raw_files(vault):
        rel = os.path.relpath(path, vault).replace("\\", "/")
        try:
            h = sha256_file(path)
        except OSError as e:
            summary.setdefault("errors", []).append((rel, f"hash failed: {e}"))
            continue
        if h in seen_hashes:
            summary["skipped"] += 1
            continue
        # Size guard: skip + record oversized files rather than risk OOM.
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if size > MAX_RAW_BYTES:
            append_manifest(vault, {"path": rel, "hash": h, "kind": "oversize",
                                    "ingested": today, "stubs": [],
                                    "error": f"skipped: {size} bytes > "
                                             f"{MAX_RAW_BYTES} cap"})
            seen_hashes.add(h)
            summary.setdefault("errors", []).append((rel, f"oversize {size}B (skipped)"))
            continue
        try:
            _ingest_one_file(vault, path, rel, h, kind_today=today,
                             patterns=patterns, summary=summary,
                             gated_types=gated_types,
                             learned_mappings=learned_field_mappings)
            seen_hashes.add(h)
        except Exception as e:
            # A poison file must not abort the whole run (and must not be
            # retried forever): record an error manifest entry and move on.
            append_manifest(vault, {"path": rel, "hash": h, "kind": "error",
                                    "ingested": today, "stubs": [],
                                    "error": f"{type(e).__name__}: {e}"})
            seen_hashes.add(h)
            summary.setdefault("errors", []).append((rel, f"{type(e).__name__}: {e}"))
    # Final pass: backfill needs-review stubs for any related: targets that
    # don't yet exist as entity files. This keeps the link graph closed and
    # eliminates the recurring "broken_ref" lint findings on a fresh vault.
    summary["inferred"] = auto_stub_missing_refs(vault)

    refresh_index(vault)
    return summary


def _ingest_one_file(vault, path, rel, h, kind_today, patterns, summary,
                     gated_types=frozenset(), learned_mappings=None):
    """Process one raw file end to end (extract -> classify -> write stubs ->
    back-link -> manifest). Raises on any failure so the caller can isolate a
    poison file without aborting the run."""
    today = kind_today
    kind = detect_kind(path)
    extract = EXTRACTORS.get(kind, extract_binary)
    ex = extract(path)
    attachments = ex.get("attachments") or []

    # Primary subject: the file itself
    subjects = [{"name": rel, "text": ex.get("text", ""), "meta": ex.get("meta") or {},
                 "kind": kind, "source_ref": rel, "is_parent": True}]
    # Sub-subjects: email attachments (in-memory extraction; .eml hash covers them)
    for a in attachments:
        sub_kind = _kind_from_filename(a["filename"], a.get("content_type", ""))
        sub_text, sub_meta = _extract_inmemory(a["bytes"], sub_kind)
        subjects.append({"name": f"{rel}#{a['filename']}",
                         "text": sub_text, "meta": sub_meta,
                         "kind": sub_kind,
                         "source_ref": f"{rel}#{a['filename']}",
                         "is_parent": False})

    # Pass 1: classify every subject and build stub dicts (no writes yet).
    stubs = []
    for sub in subjects:
        c = classify(sub["text"], sub["meta"], sub["kind"], sub["name"], patterns)
        fields = extract_fields(sub["text"], sub["meta"], sub["kind"],
                                learned_mappings=learned_mappings)
        stubs.append((sub, enforce_human_gate(build_stub(sub, c, fields),
                                              gated_types)))

    # Pass 2a: resolve every stub's final on-disk slot BEFORE wiring any
    # cross-links. resolve_stub_path may disambiguate a slug (collision with
    # an existing entity or a sibling in this batch); resolving first means
    # the `related:` refs written below always carry the final slugs.
    reserved = set()
    resolved = []  # (sub, stub, path, should_write)
    for sub, stub in stubs:
        path, should_write = resolve_stub_path(vault, stub, reserved)
        resolved.append((sub, stub, path, should_write))

    # Pass 2b: email cross-linking. If a parent and one or more attachments
    # came from the same .eml, link them bidirectionally via `related:`.
    parents = [r for r in resolved if r[0].get("is_parent")]
    children = [r for r in resolved if not r[0].get("is_parent")]
    if parents and children:
        parent_stub = parents[0][1]
        for _, child_stub, _, _ in children:
            child_ref = f"{child_stub['type']}/{child_stub['slug']}"
            parent_ref = f"{parent_stub['type']}/{parent_stub['slug']}"
            if child_ref not in parent_stub["related"]:
                parent_stub["related"].append(child_ref)
            if parent_ref not in child_stub["related"]:
                child_stub["related"].append(parent_ref)

    # Pass 3: write the stubs and update existing related-target entities.
    stub_slugs = []
    for sub, stub, path, should_write in resolved:
        if not should_write:
            continue  # target is compiled/archived; human owns it
        stub["sources_hash"] = sources_hash(stub["sources"])
        write_stub(vault, stub, path=path)
        stub_slugs.append(stub["slug"])
        bucket = ("stubs" if stub["status"] == "stub"
                  else "unknown" if stub["type"] == "unknown"
                  else "review")
        summary[bucket].append((stub["slug"], stub["type"], stub["subtype"],
                                round(stub["confidence"], 2), sub["source_ref"]))

        # Back-reference: when this stub points to an existing entity
        # (e.g. account/bofa-checking), append our raw path + add a
        # `related:` back-link to that entity. Sibling stubs we just
        # created in this same run are skipped â€” they were built above.
        sibling_refs = {f"{s['type']}/{s['slug']}" for _, s in stubs}
        for ref in stub.get("related") or []:
            if ref in sibling_refs:
                continue
            update_existing_entity(vault, ref,
                                   new_source=sub["source_ref"],
                                   new_related=f"{stub['type']}/{stub['slug']}")

    append_manifest(vault, {"path": rel, "hash": h, "kind": kind,
                            "ingested": today, "stubs": stub_slugs})
    summary["new"] += 1


def _kind_from_filename(filename, content_type):
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf" or "pdf" in content_type: return "pdf"
    if ext in (".jpg", ".jpeg") or "jpeg" in content_type: return "jpeg"
    if ext == ".png" or "png" in content_type: return "png"
    if ext in (".txt", ".md", ".csv") or "text" in content_type: return "text"
    if ext == ".docx" or "word" in content_type: return "docx"
    if ext == ".xlsx" or "sheet" in content_type or "excel" in content_type: return "xlsx"
    if ext in (".html", ".htm") or "html" in content_type: return "html"
    return "binary"


def _extract_inmemory(data, kind):
    """Run an extractor against an in-memory bytes buffer (for email attachments)."""
    if kind == "pdf":
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(data)) as p:
                txt = "\n".join((page.extract_text() or "") for page in p.pages)
            return txt, {"pages": len(txt.splitlines())}
        except Exception as e:
            return "", {"warn": f"pdf in-memory extract failed: {e}"}
    if kind == "text":
        try: return data.decode("utf-8"), {}
        except UnicodeDecodeError: return data.decode("latin-1", errors="replace"), {}
    if kind == "docx":
        try: return _docx_text_from_bytes(data), {"format": "docx"}
        except Exception as e: return "", {"warn": f"docx in-memory extract failed: {e}"}
    if kind == "xlsx":
        try: return _xlsx_text_from_bytes(data), {"format": "xlsx"}
        except Exception as e: return "", {"warn": f"xlsx in-memory extract failed: {e}"}
    if kind == "html":
        try:
            txt = data.decode("utf-8", "replace")
            return _HTMLTextExtractor().feed(txt), {"format": "html"}
        except Exception as e: return "", {"warn": f"html in-memory extract failed: {e}"}
    if kind in ("jpeg", "png"):
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as img:
                ex = img.getexif()
                meta = {270: str(ex.get(270, "")), 306: str(ex.get(306, ""))}
            return meta[270], {"datetime": meta[306]}
        except Exception as e:
            return "", {"warn": str(e)}
    return "", {}


def build_stub(subject, classification, fields):
    """Compose a stub dict from one subject + its classification.
    sources_hash is left empty here â€” the caller stamps it after any
    cross-link mutations to `related`/`sources` are settled."""
    conf = classification["confidence"]
    status = ("stub" if conf >= NEEDS_REVIEW_BELOW
              else "needs-review")
    if conf < UNKNOWN_BELOW:
        classification = {**classification, "type": "unknown", "subtype": "unknown"}

    slug = make_slug(classification.get("biller"), classification, fields,
                     subject["name"], subject["kind"])
    title = _make_title(classification, fields, subject)

    # spec Â§7 (write-side): if the source text carries secret-shaped strings,
    # downgrade a confident stub to needs-review so a human vets it before the
    # compile/surface step, and defensively redact the (Python-written) title.
    # The secret value itself is NEVER recorded â€” only its category + count.
    secret_findings = secret_scan.scan(subject.get("text", "")) if secret_scan else []
    if secret_findings:
        if status == "stub":
            status = "needs-review"
        title = secret_scan.redact(title)

    today = datetime.date.today().isoformat()
    created = fields.get("created", today)

    stub = {
        "slug": slug,
        "type": classification["type"],
        "subtype": classification["subtype"],
        "title": title,
        "status": status,
        "confidence": conf,
        "created": created,
        "sources": [subject["source_ref"]],
        "sources_hash": "",
        "tags": list(classification.get("tags") or []),
        "related": list(classification.get("related") or []),
        "notes": {
            "kind": subject["kind"],
            "biller": classification.get("biller"),
            "shape": classification.get("shape"),
        },
    }
    for k in ("due", "expires", "renews", "value", "vin", "serial", "model", "last4"):
        if k in fields and fields[k] is not None:
            v = fields[k]
            # defensive: never let a strict-secret-shaped value ride into a field
            stub[k] = secret_scan.redact(v) if (secret_scan and isinstance(v, str)) else v
    # surface extracted identifiers in the Python-owned notes (no schema field for them)
    for k in ("order_id", "policy_id", "parcel_id", "period"):
        if fields.get(k):
            stub["notes"][k] = fields[k]
    # Surface LEARNED field-mapping captures (registry/field_mappings.yaml) into
    # the Python-owned notes. These have no schema frontmatter field, so they
    # live in notes exactly like order_id/policy_id do. Only fields NOT already
    # a built-in key reach here (extract_fields is strictly additive and never
    # overrides a built-in). Notes are redacted via secret_scan.redact at write
    # time (render_stub), and extract_fields already blocks secret-shaped
    # captures, so a learned mapping can never leak a secret into a stub.
    _BUILTIN_FIELD_KEYS = frozenset((
        "due", "expires", "renews", "value", "vin", "serial", "model",
        "last4", "order_id", "policy_id", "parcel_id", "period", "created"))
    learned = {k: v for k, v in fields.items() if k not in _BUILTIN_FIELD_KEYS}
    if learned:
        stub["notes"]["learned_fields"] = learned
    if classification["type"] == "media" and (subject["meta"] or {}).get("gps"):
        stub["notes"]["gps"] = subject["meta"]["gps"]
    if secret_findings:
        kinds = sorted({f["kind"] for f in secret_findings})
        stub["notes"]["secret_scan"] = (f"{len(secret_findings)} secret-shaped "
                                        f"string(s) withheld; kinds={kinds}")
    return stub


def _make_title(classification, fields, subject):
    biller = classification.get("biller")
    shape = classification.get("shape")
    when = (fields.get("period") or fields.get("due") or fields.get("expires")
            or fields.get("renews") or fields.get("created") or "")
    pieces = []
    if biller: pieces.append(biller.upper() if len(biller) <= 4 else biller.title())
    if shape:  pieces.append(shape.replace("-", " ").title())
    # When the biller fires but no shape did, the title would be just the
    # biller name â€” add the filename stem so the entity is identifiable.
    if biller and not shape:
        stem = os.path.splitext(os.path.basename(subject["name"].split("#")[-1]))[0]
        pieces.append(stem.replace("-", " ").title())
    if not pieces:
        stem = os.path.splitext(os.path.basename(subject["name"].split("#")[-1]))[0]
        pieces.append(stem.replace("-", " ").title())
    if when: pieces.append(when)
    return " â€” ".join(pieces)


def refresh_index(vault):
    """Run build_index.main() in-process so we don't shell out."""
    try:
        sys.path.insert(0, vault)
        from . import build_index
        # build_index uses sys.argv[1] for vault; emulate.
        saved = sys.argv[:]
        sys.argv = ["build_index.py", vault]
        try:
            build_index.main()
        finally:
            sys.argv = saved
    except Exception as e:
        print(f"warn: build_index failed: {e}")


# --- CLI --------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(__doc__); return 0
    vault = args[0] if args else "."
    s = ingest(vault)
    print(f"\ningest summary: {s['new']} new, {s['skipped']} skipped (already ingested)\n")
    if s["stubs"]:
        print(f"  confident stubs ({len(s['stubs'])}):")
        for slug, t, st, c, src in s["stubs"]:
            print(f"    {c:.2f}  {t}/{st:14}  {slug:40}  <- {src}")
    if s["review"]:
        print(f"\n  needs-review ({len(s['review'])}):")
        for slug, t, st, c, src in s["review"]:
            print(f"    {c:.2f}  {t}/{st:14}  {slug:40}  <- {src}")
    if s["unknown"]:
        print(f"\n  unknown ({len(s['unknown'])}):")
        for slug, t, st, c, src in s["unknown"]:
            print(f"    {c:.2f}  {t}/{st:14}  {slug:40}  <- {src}")
    if s.get("inferred"):
        print(f"\n  inferred from related: refs ({len(s['inferred'])}):")
        for t, slug in s["inferred"]:
            print(f"    needs-review  {t}/{slug}")
    if s.get("errors"):
        print(f"\n  errors ({len(s['errors'])}) â€” recorded in raw/_manifest.jsonl, "
              "will not be retried:", file=sys.stderr)
        for rel, err in s["errors"]:
            print(f"    {rel}: {err}", file=sys.stderr)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

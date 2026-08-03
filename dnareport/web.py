# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""DNA-Report web front door — the HTTP layer reviewers actually hit.

The CLI (dnareport.cli) is for local/operator use; this is the service Cloudflare
fronts. It is deliberately thin: it owns routing decisions (inline vs queued) and
result serving, and delegates all analysis to the orchestrator + engines.

Endpoints:
  GET  /                        -> the upload page (dnareport.landing).
  POST /analyze                 -> small INLINE upload (23andMe, small beta-matrix,
                                   single VCF): detect -> run -> render -> return HTML.
                                   Heavy kinds are refused here with a pointer to the
                                   R2 upload flow (they must not stream through the
                                   front door; see dna-report-deploy/cloudflare).
                                   Errors carry a structured body; a browser gets a
                                   rendered refusal page unless it sends
                                   X-Error-Format: json (the upload page does, so it
                                   can show the reason in place).
  POST /upload/sign             -> {filename, size} -> a presigned PUT URL so the
                                   browser sends the file STRAIGHT to R2. Exists
                                   because the edge inspects request bodies and
                                   refuses anything it cannot parse — which is
                                   every zip and every .gz, i.e. most real
                                   uploads. The key is minted here, never taken
                                   from the caller.
  POST /analyze/r2              -> {key} -> pull that object, detect, run, render,
                                   return the report, delete the object. The
                                   INSTANT path for compressed files: same
                                   pipeline as /analyze, different source of
                                   bytes, and deliberately no queue — a consumer
                                   export interprets in seconds and does not need
                                   a claim link.
  GET  /result/{job_id}         -> serve a finished report (202 + waiting page while
                                   the worker is still running it).
  GET  /disclaimer              -> the single canonical product disclaimer.
  GET  /demo/{blood,buccal}     -> bundled real sample reports.
  GET  /demo/combined           -> methylome + genome merged into one report.
  GET  /health                  -> liveness + what this instance can do.
  POST /enqueue                 -> called by the R2 upload Worker after a big file
                                   lands in R2: {r2_key, kind, n_samples?} -> push a
                                   job on the queue -> return {job_id}. Bearer-token
                                   auth (ENQUEUE_TOKEN), not reviewer-facing.
  GET  /api/openapi.json,
  GET  /api/docs                -> schema + Swagger for the JSON API. Key-gated, and
                                   disabled entirely when no DNAREPORT_API_KEY is set.

Uploaded files live in a per-request scratch dir that is removed before the
response is returned — the reports promise deletion, so nothing may outlive the
request.

Queue + result store are configured by env (the deployment sets them); with no
queue backend the /enqueue path is disabled and only inline analysis runs, so the
app degrades to a standalone analyzer.
"""
from __future__ import annotations
import os, re, sys, json, uuid, shutil, tempfile, html as _html
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from . import __version__
from .detect import detect, InputKind
from .tiering import job_tier, queue_enabled, QUEUED
from .orchestrate import analyze, compare, render, _marker_url
from .tissue import infer_tissue
from .landing import LANDING_HTML
from .serialize import result_to_json
from . import pages
from .uploads import (UploadError, ACCEPTED_FORMATS, INLINE_R2_MAX, stream_to_disk,
                      unwrap_archive, sanitize_note)

RESULT_DIR = os.environ.get("DNAREPORT_RESULT_DIR", tempfile.gettempdir())
QUEUE_URL = os.environ.get("DNAREPORT_QUEUE_URL")
ENQUEUE_TOKEN = os.environ.get("ENQUEUE_TOKEN")
# Build identity, stamped into the image by the build-image workflow. __version__
# is the package version and moves maybe twice a year, so it cannot tell a fresh
# container from one still serving the previous image — after a deploy, only the
# commit distinguishes them. Unset outside a built image (local dev, tests).
BUILD_COMMIT = os.environ.get("DNAREPORT_COMMIT") or "unknown"
BUILD_TIME = os.environ.get("DNAREPORT_BUILD_TIME") or "unknown"
# R2 results bucket: worker-produced reports land here (a worker runs on a
# different machine than the app, so R2 is the shared substrate). The app reads
# from it to serve /result. Only the trusted app holds these read creds — worker
# NODES need only write access. No-op fallback to local disk when R2 is unset
# (the inline /analyze path writes to RESULT_DIR on the same box).
R2_RESULTS_BUCKET = os.environ.get("R2_RESULTS_BUCKET", "dna-report-results")
R2_ENDPOINT = os.environ.get("R2_ENDPOINT")
R2_INCOMING_BUCKET = os.environ.get("R2_BUCKET", "dna-report-incoming")


# Objects for this path land under their own prefix, kept separate from the
# queue's `incoming/`: different lifetimes (deleted the moment analysis returns
# rather than when a worker pulls them), and it keeps /analyze/r2 unable to name
# an object belonging to the queued flow.
R2_INLINE_PREFIX = "inline/"

# Queued uploads keep the `incoming/` prefix the Cloudflare Worker has always
# used: the alien workers pull by r2_key and the lifecycle rule already targets
# it, so changing it would strand both for no gain.
R2_QUEUED_PREFIX = "incoming/"

# R2 requires >= 5 MB for every part except the last. 16 MB matches what the
# Worker-proxied flow used, so a browser's memory profile per part is unchanged.
R2_PART_SIZE = 16 * 1024 * 1024

# Ceiling for a queued upload. Matches the Cloudflare Worker's own MAX_BYTES so
# the two paths cannot disagree about what is acceptable — the Worker route still
# exists and still enforces its own.
MULTIPART_MAX = int(os.environ.get("DNAREPORT_MULTIPART_MAX", 20 * 1024**3))

# Kinds a queued job may declare. Mirrors KINDS in the Worker and the worker's own
# dispatch; the analysis re-detects from the file regardless, so this is a gate on
# what may be enqueued rather than a statement about what the file is.
_QUEUE_KINDS = {"vcf", "vcf-multi", "idat", "modbam", "bedmethyl", "beta_matrix",
                "23andme", "array_genotype"}


def _require_own_key(key: str) -> None:
    """A caller may only name an object under the prefix this service mints.

    Without this, `key` is an arbitrary string handed to complete/sign/delete
    against a bucket that also holds other people's uploads.
    """
    if not key.startswith(R2_QUEUED_PREFIX) or ".." in key:
        raise HTTPException(status_code=400, detail="unknown object")


def _r2_client():
    """S3 client configured for R2.

    region_name='auto' and s3v4 are both required: without them a presigned URL
    is minted happily and then rejected with 401 at PUT time, which reads as a
    credentials problem and is not one. Measured 2026-08-03.
    """
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=R2_ENDPOINT, region_name="auto",
                        config=Config(signature_version="s3v4"))

def _r2_result_html(job_id: str) -> str | None:
    """Fetch a worker-produced report from the R2 results bucket, or None if it
    isn't there yet / R2 isn't configured."""
    if not R2_ENDPOINT:
        return None
    try:
        import boto3
        s3 = boto3.client("s3", endpoint_url=R2_ENDPOINT)   # creds from env
        obj = s3.get_object(Bucket=R2_RESULTS_BUCKET, Key=f"{job_id}.html")
        return obj["Body"].read().decode("utf-8", "replace")
    except Exception as e:
        # "not there yet" is the normal case while a worker is still running the
        # job — stay quiet for it. ANYTHING else (no boto3, bad creds, wrong
        # bucket) is a misconfiguration, and the caller turns None into a 202
        # "still processing" page — so a broken read path looks exactly like a
        # slow job and hides indefinitely. Swallowing this silently is what let a
        # missing boto3 dependency survive to the first real end-to-end test.
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if code not in ("NoSuchKey", "404"):
            print(f"R2 result read FAILED for {job_id}: {type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
        return None
# API key for the JSON surface. Must be set explicitly via the DNAREPORT_API_KEY
# env var — there is deliberately NO default, so a public clone ships no working
# key and the JSON API stays closed until an operator sets one. Interactive HTML
# needs no key. A real key-management / Cloudflare service-token layer goes over
# this later (see PRODUCTION_TODO).
API_KEY = os.environ.get("DNAREPORT_API_KEY")
_DEMO_DIR = Path(__file__).parent / "demo_data"
_DEMOS = {
    "blood":  ("demo_blood_wholeblood.csv", "blood",
               "Whole-blood methylation profile from a healthy adult (public GEO "
               "sample, chronological age 62). All clocks are valid for blood."),
    "buccal": ("demo_buccal_pediatric.csv", "buccal",
               "Buccal (cheek-swab) profile from a children's research cohort. "
               "Shown to demonstrate tissue-mismatch flagging: blood-trained clocks "
               "are marked not-valid rather than shown as misleading numbers."),
}

# OpenAPI schema is generated, but /docs and /openapi.json are served behind the
# API key (see the gated routes below) rather than public — the JSON API is
# key-guarded, so its documentation is too.
app = FastAPI(title="DNA-Report", docs_url=None, redoc_url=None, openapi_url=None)

# Human-facing names for the detected kinds, so a refusal or an empty result can
# say "AncestryDNA-style genotype export" instead of "array_genotype".
_KIND_LABEL = {
    InputKind.TWENTYTHREE_AND_ME: "23andMe raw genotype export",
    InputKind.ARRAY_GENOTYPE: "Consumer genotype export",
    InputKind.VCF: "VCF genome",
    InputKind.BEDMETHYL: "bedMethyl methylation calls",
    InputKind.BETA_MATRIX: "Methylation beta-value table",
    InputKind.IDAT: "Illumina IDAT array file",
    InputKind.MODBAM: "ONT modBAM",
    InputKind.UNKNOWN: "Unrecognised",
}


def _article(kind) -> str:
    """"A" or "An" for a kind label, so the title and the message body agree."""
    return "An" if _KIND_LABEL.get(kind, "")[:1].upper() in "AEIOU" else "A"


@app.exception_handler(UploadError)
async def _upload_error(request: Request, exc: UploadError):
    """One place decides how a rejected upload is presented.

    A browser gets the rendered refusal page; an API caller gets the structured
    JSON body. Either way the STATUS CODE is a real 4xx — the previous behaviour
    of answering "I could not use this" with HTTP 200 is what made a failed
    upload look identical to nothing happening.
    """
    # The page's own fetch asks for errors as JSON explicitly, because it needs
    # the structured body to render its in-page failure panel — it cannot use
    # this HTML page, and it must keep asking for text/html so a SUCCESSFUL
    # response is still the rendered report rather than the key-gated JSON API.
    # Without this signal the app served itself a refusal page it then discarded,
    # showing "The server answered with HTTP 415" instead of the reason.
    if request.headers.get("x-error-format", "").lower() == "json":
        return JSONResponse(exc.body(), status_code=exc.status)
    accept = request.headers.get("accept", "")
    wants_html = "text/html" in accept and "application/json" not in accept
    if wants_html:
        return HTMLResponse(
            pages.refusal_page(code=exc.code, title=exc.title, message=exc.message,
                               hint=exc.hint, filename=getattr(exc, "filename", ""),
                               accepted=ACCEPTED_FORMATS if exc.accepted else None),
            status_code=exc.status)
    return JSONResponse(exc.body(), status_code=exc.status)

# ---- lightweight per-key rate limiter (in-memory token bucket) --------------
# The app is a single instance behind the tunnel, so an in-process limiter is
# sufficient; a Redis-backed limiter is the productization step (PRODUCTION_TODO).
import time as _time
_RATE_MAX = int(os.environ.get("DNAREPORT_RATE_MAX", "60"))        # tokens
_RATE_WINDOW = float(os.environ.get("DNAREPORT_RATE_WINDOW", "60"))  # seconds
_rate_state: dict[str, list] = {}   # key -> [tokens, last_refill_ts]


def _rate_limit(key: str):
    """Token-bucket: DNAREPORT_RATE_MAX requests per DNAREPORT_RATE_WINDOW per key.
    Raises 429 when exhausted."""
    now = _time.time()
    tokens, last = _rate_state.get(key, [_RATE_MAX, now])
    tokens = min(_RATE_MAX, tokens + (now - last) * (_RATE_MAX / _RATE_WINDOW))
    if tokens < 1:
        raise HTTPException(status_code=429, detail="rate limit exceeded; slow down")
    _rate_state[key] = [tokens - 1, now]


def _queue():
    """Lazy redis handle; None when no backend configured (standalone mode)."""
    if not QUEUE_URL:
        return None
    import redis
    return redis.from_url(QUEUE_URL)


def _render_full(result, out_path: str) -> str:
    """Compose the browser report: the highlights section (aging clocks + cited
    reference positions, product layer) + bio-core's finding/disclaimer render.

    Reference positions are pulled OUT of the findings list before bio-core
    renders it — they are the headline of a methylome report, not one card among
    several dozen. The swap is restored afterwards so the JSON surface, which
    does not go through here, still carries them as ordinary findings.
    """
    from .highlights import (split_reference_findings, split_display_findings,
                             highlights_html)

    _, rest = split_reference_findings(list(result.findings or []))
    rest, _withheld = split_display_findings(rest)
    original = result.findings
    result.findings = rest
    try:
        render(result, out_path)                   # bio-core findings + disclaimer
    finally:
        result.findings = original

    body = Path(out_path).read_text()

    # Glossary goes at the END, after the findings it explains — the copy is
    # per-trait while findings are per-marker, so it is written once here and
    # each finding links to its entry rather than restating it.
    from .glossary import glossary_html
    gloss = glossary_html(rest)
    if gloss:
        if "</body>" in body:
            body = body.replace("</body>", gloss + "\n</body>", 1)
        else:
            body += gloss

    top = highlights_html(result)
    if top:
        # inject the highlights section at the top of the document body
        if "<body>" in body:
            body = body.replace("<body>", "<body>\n" + top, 1)
        else:
            body = top + body
        Path(out_path).write_text(body)
    return out_path


def _client_key(request) -> str:
    """Rate-limit bucket for an unauthenticated caller.

    Behind Cloudflare every request arrives from an edge address, so
    request.client.host would put the whole internet in one bucket and let a
    single caller exhaust everyone's allowance. CF-Connecting-IP is the real
    client and is set by the edge, which is also why it is only trustworthy
    while the app is only reachable through it.
    """
    return (request.headers.get("cf-connecting-ip")
            or getattr(getattr(request, "client", None), "host", "")
            or "anon")


# Extensions with a parser behind them. The landing page applies the same list
# before uploading so nobody spends hours sending a file we would refuse; this is
# the server-side half, because a client-side gate is a courtesy and not a
# control. Compression suffixes are stripped first — a .vcf.gz is a .vcf.
_SUPPORTED_EXT = {"txt", "csv", "tsv", "vcf", "bed", "bedmethyl", "idat",
                  "bam", "modbam", "zip"}


def _extension_supported(filename: str) -> bool:
    name = str(filename).lower()
    if name.endswith(".gz"):
        name = name[:-3]
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    return ext in _SUPPORTED_EXT


def _check_api_key(x_api_key: str, key_q: str):
    """Guard the JSON surface. Fail closed: if no key is configured on the server
    the JSON API is disabled entirely (503), never open. Otherwise the caller's
    key must match."""
    if not API_KEY:
        raise HTTPException(status_code=503,
                            detail="JSON API is not enabled on this instance "
                                   "(no DNAREPORT_API_KEY configured).")
    key = (x_api_key or key_q)
    if key != API_KEY:
        raise HTTPException(status_code=401,
                            detail="JSON output requires a valid API key "
                                   "(header X-API-Key or ?api_key=).")
    _rate_limit(key)   # per-key token bucket


def _run_and_respond(local, tissue, filename="", *, want_json=False,
                     x_api_key="", key_q=""):
    """Shared path for uploads and demos: detect, gate heavy kinds, run, then
    return HTML (human) or JSON (agents/products, key-guarded)."""
    kind = detect(local)
    # A file we cannot classify is a REFUSAL, not an empty report. It used to fall
    # through to the analysis path, produce nothing, and return HTTP 200 with a
    # JSON body — which the page showed as one line of grey text, so a wrong file
    # was indistinguishable from a silent failure.
    if kind == InputKind.UNKNOWN:
        raise UploadError(
            "unrecognised_format",
            "We could not tell what kind of file this is",
            "The upload was read successfully, but its contents do not match any "
            "genotype or methylation format we know how to analyse.",
            hint="If this came from a testing service, upload the original file "
                 "exactly as downloaded — re-saving it from a spreadsheet usually "
                 "strips the header lines the format is identified by.",
            status=415, accepted=True)
    if job_tier(kind) == QUEUED:
        raise UploadError(
            "needs_large_file_upload",
            # labels are already singular nouns ("Illumina IDAT array file"), so
            # they take an article rather than a plural
            f"{_article(kind)} {_KIND_LABEL.get(kind, kind.value)} goes through "
            "the large-file upload",
            f"{_article(kind)} {_KIND_LABEL.get(kind, kind.value)} has to be normalised "
            "before it can be interpreted, which is too heavy for the instant path — "
            "send it through the large-file upload instead.",
            hint="Use the large-file upload, which streams the file in parts and "
                 "hands you a link you can bookmark while it runs.",
            status=413)
    if not tissue:
        with open(local, "r", errors="ignore") as fh:
            header = fh.readline()
        tissue = infer_tissue(filename or os.path.basename(local), header).tissue

    # VCF routing: a MULTI-sample VCF is a reconcile-my-tests job (compare);
    # a SINGLE-sample VCF is interpretation (analyze -> ClinVar + traits).
    if kind == InputKind.VCF:
        n = 1
        try:
            from biocore.variants.carried import n_samples
            n = n_samples(local)
        except Exception:
            pass
        res = compare(local) if n >= 2 else analyze(local, tissue=tissue)
    else:
        res = analyze(local, tissue=tissue)

    if want_json:
        _check_api_key(x_api_key, key_q)
        return JSONResponse(result_to_json(res, marker_url=_marker_url))

    if not res.findings and not res.clocks:
        # A readable file that yielded nothing is a RESULT (200), so it is served
        # as a real page that says what was recognised and why a valid file can
        # come back empty — not as a bare JSON object.
        scratch = os.path.dirname(local)
        display = filename or os.path.basename(local)
        notes = [n for n in (sanitize_note(n, scratch, display) for n in res.notes) if n]
        return HTMLResponse(pages.empty_report_page(
            kind_label=_KIND_LABEL.get(kind, kind.value),
            filename=display, notes=notes))
    out = os.path.join(RESULT_DIR, f"{uuid.uuid4().hex}.html")
    _render_full(res, out)
    try:
        return HTMLResponse(Path(out).read_text())
    finally:
        # The inline path returns the report in the response body, so this file
        # is scratch, not storage. Leaving it behind grew RESULT_DIR without
        # bound and left a rendered copy of someone's findings on disk. Queued
        # jobs are unaffected — those are served from R2 or written by a worker.
        try:
            os.unlink(out)
        except OSError:
            pass


def _wants_json(accept: str, fmt: str) -> bool:
    return fmt == "json" or "application/json" in (accept or "")


# Demos are deterministic (fixed bundled inputs), so their rendered HTML is cached
# in-process after the first build — subsequent clicks are instant instead of
# re-running the ~10-13s live-annotation render. JSON requests bypass the cache
# (they're keyed differently and rarely hit). Cleared on process restart.
_DEMO_HTML_CACHE: dict[str, str] = {}


def _cached_demo_html(key: str, build) -> str:
    html = _DEMO_HTML_CACHE.get(key)
    if html is None:
        html = build()
        _DEMO_HTML_CACHE[key] = html
    return html


@app.get("/", response_class=HTMLResponse)
def landing():
    return HTMLResponse(LANDING_HTML)


@app.get("/disclaimer", response_class=PlainTextResponse)
def disclaimer():
    """The single canonical product disclaimer, bundled as package data."""
    p = Path(__file__).parent / "docs" / "DISCLAIMER.md"
    if p.exists():
        return PlainTextResponse(p.read_text())
    return PlainTextResponse(
        "DNA-Report provides research and educational information about DNA "
        "methylation and genetic variants. It is not a medical device and does "
        "not provide medical advice, diagnosis, or treatment. Findings are labelled "
        "by how much scientific support they have; discuss anything health-related "
        "with a qualified clinician who has your full context.")


@app.get("/demo/combined")
def demo_combined(format: str = "", accept: str = Header(default=""),
                  x_api_key: str = Header(default=""), api_key: str = ""):
    """A profile with BOTH a methylome and a genome upload, merged into one
    report — so the source (methylome/genome) bubble and filter are exercised.
    Registered before /demo/{kind} so the static path wins."""
    blood = str(_DEMO_DIR / "demo_blood_wholeblood.csv")
    genome = str(_DEMO_DIR / "demo_genome.vcf")
    if not (os.path.exists(blood) and os.path.exists(genome)):
        raise HTTPException(status_code=500, detail="combined demo data not bundled")

    def _build():
        m = analyze(blood, tissue="blood")      # methylome findings + clocks
        g = analyze(genome)                      # genome (ClinVar) findings
        m.findings += g.findings
        m.engines = tuple(dict.fromkeys(list(m.engines) + list(g.engines)))
        m.notes += g.notes
        return m

    if _wants_json(accept, format):
        _check_api_key(x_api_key, key_q=api_key)
        return JSONResponse(result_to_json(_build(), marker_url=_marker_url))
    def _render():
        out = os.path.join(RESULT_DIR, f"{uuid.uuid4().hex}.html")
        _render_full(_build(), out)
        return Path(out).read_text()
    return HTMLResponse(_cached_demo_html("combined", _render))


@app.get("/demo/{kind}")
def demo(kind: str, format: str = "", accept: str = Header(default=""),
         x_api_key: str = Header(default=""), api_key: str = ""):
    """Load a bundled real demo profile and render its report (HTML or JSON)."""
    if kind not in _DEMOS:
        raise HTTPException(status_code=404, detail=f"no demo '{kind}'")
    fname, tissue, _desc = _DEMOS[kind]
    path = str(_DEMO_DIR / fname)
    if not os.path.exists(path):
        raise HTTPException(status_code=500, detail="demo data not bundled")
    want_json = _wants_json(accept, format)
    if want_json:
        return _run_and_respond(path, tissue, filename=fname, want_json=True,
                                x_api_key=x_api_key, key_q=api_key)
    # cache the deterministic demo HTML so repeat clicks are instant
    def _render():
        resp = _run_and_respond(path, tissue, filename=fname, want_json=False)
        return resp.body.decode("utf-8") if hasattr(resp, "body") else Path(
            resp).read_text()
    return HTMLResponse(_cached_demo_html(f"kind:{kind}", _render))


@app.post("/upload/sign")
async def upload_sign(request: Request):
    """Mint a presigned PUT so the browser can send a file straight to R2.

    Compressed uploads cannot reach the analyser through the front door — the
    edge inspects request bodies and refuses content it cannot parse, which is
    every zip and every .gz. That is the majority of real uploads: 23andMe,
    AncestryDNA, MyHeritage and FTDNA all hand the user a zip.

    So the bytes go to object storage directly and the front door carries only
    small JSON, which is the part where inspection is meaningful. The upload is
    not exempted from anything; it stops travelling through a hop that was never
    able to read it.

    The URL is bound by the signature to one bucket, one key, PUT, and a short
    expiry. The key is generated HERE, never taken from the caller, so this
    cannot be steered at an object it did not create.
    """
    _rate_limit(_client_key(request))
    body = await request.json()
    filename = str(body.get("filename") or "upload")
    try:
        size = int(body.get("size") or 0)
    except (TypeError, ValueError):
        size = 0

    ext_ok = _extension_supported(filename)
    if not ext_ok:
        raise UploadError(
            "unsupported_format", "We do not have a parser for this kind of file",
            f"Nothing in this analyser reads {os.path.splitext(filename)[1] or 'that'} "
            "files, so uploading it could not produce a report.",
            hint="Checked before the upload rather than after it.",
            status=415, accepted=True)
    if size > INLINE_R2_MAX:
        raise UploadError(
            "needs_large_file_upload", "That file goes through the large-file upload",
            f"This upload is {size / (1024*1024):.0f} MB; the instant path accepts up "
            f"to {INLINE_R2_MAX // (1024*1024)} MB.",
            hint="Use the large-file upload, which streams the file in parts and "
                 "hands you a link you can bookmark while it runs.",
            status=413)
    if not R2_ENDPOINT:
        raise UploadError(
            "no_object_store", "This instance cannot accept uploads that way",
            "Direct-to-storage upload is not configured on this deployment.",
            hint="A standalone instance without R2 accepts small uncompressed "
                 "files through the ordinary upload instead.",
            status=503)

    safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename))[:120] or "upload"
    key = f"{R2_INLINE_PREFIX}{uuid.uuid4().hex}/{safe}"
    url = _r2_client().generate_presigned_url(
        "put_object", Params={"Bucket": R2_INCOMING_BUCKET, "Key": key},
        ExpiresIn=900)
    return {"key": key, "url": url, "expires_in": 900}


def _enqueue_job(r2_key: str, kind: str, n_samples: int = 1,
                 notify_email: str = "", newsletter: bool = False) -> str:
    """Push a heavy job and return its id. Shared by /enqueue (called by the
    Cloudflare Worker) and the presigned multipart flow (where the app completes
    the upload itself and there is no Worker in the path at all)."""
    q = _queue()
    if q is None:
        raise HTTPException(status_code=503, detail="no queue backend configured")
    job_id = uuid.uuid4().hex
    job = {"job_id": job_id, "r2_key": r2_key, "kind": kind, "n_samples": n_samples}
    email = (notify_email or "").strip()
    if email and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        job["notify_email"] = email
        job["newsletter"] = bool(newsletter)
    q.rpush("dnareport:jobs", json.dumps(job))
    return job_id


@app.post("/upload/multipart/create")
async def multipart_create(request: Request):
    """Begin a presigned multipart upload for a heavy file.

    The parts used to be PUT to the Cloudflare Worker, which streamed each into
    R2. That put every byte of a whole genome through the zone, where the edge
    inspects request bodies and refuses what it cannot parse — so a compressed
    genome could not be uploaded at all, by any route. Here the browser talks to
    R2 directly and the front door only ever sees small JSON.

    The key is minted here and never taken from the caller.
    """
    _rate_limit(_client_key(request))
    body = await request.json()
    filename = str(body.get("filename") or "upload")
    if not _extension_supported(filename):
        raise UploadError(
            "unsupported_format", "We do not have a parser for this kind of file",
            f"Nothing in this analyser reads {os.path.splitext(filename)[1] or 'that'} files.",
            hint="Checked before the upload rather than after it.",
            status=415, accepted=True)
    if not R2_ENDPOINT:
        raise HTTPException(status_code=503, detail="object store not configured")
    try:
        size = int(body.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size > MULTIPART_MAX:
        raise UploadError(
            "too_large", "That file is larger than we accept",
            f"This upload is {size / (1024**3):.1f} GB; the ceiling is "
            f"{MULTIPART_MAX // (1024**3)} GB.",
            hint="If this is a whole-genome BAM, a VCF of the same sample is "
                 "far smaller and is what the analysis actually reads.",
            status=413)

    safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename))[:120] or "upload"
    key = f"{R2_QUEUED_PREFIX}{uuid.uuid4().hex}/{safe}"
    mp = _r2_client().create_multipart_upload(Bucket=R2_INCOMING_BUCKET, Key=key)
    return {"key": key, "uploadId": mp["UploadId"], "part_size": R2_PART_SIZE}


@app.post("/upload/multipart/sign")
async def multipart_sign(request: Request):
    """Presigned PUT URLs for a batch of part numbers.

    Signed in batches rather than all at once so a multi-gigabyte upload does not
    mint hundreds of URLs up front, all expiring on the same clock while the
    early parts are still going up.
    """
    _rate_limit(_client_key(request))
    body = await request.json()
    key, upload_id = str(body.get("key") or ""), str(body.get("uploadId") or "")
    _require_own_key(key)
    parts = body.get("parts") or []
    if not isinstance(parts, list) or not parts or len(parts) > 100:
        raise HTTPException(status_code=400, detail="parts must be 1-100 numbers")
    s3 = _r2_client()
    urls = {}
    for n in parts:
        n = int(n)
        if n < 1 or n > 10000:
            raise HTTPException(status_code=400, detail="bad part number")
        urls[str(n)] = s3.generate_presigned_url(
            "upload_part",
            Params={"Bucket": R2_INCOMING_BUCKET, "Key": key,
                    "UploadId": upload_id, "PartNumber": n},
            ExpiresIn=3600)
    return {"urls": urls}


@app.post("/upload/multipart/complete")
async def multipart_complete(request: Request):
    """Finish the upload, verify what actually landed, then enqueue.

    This is the checkpoint. Everything before it was asserted by the client: the
    size at create time was a number it supplied, and nothing stopped it sending
    something else. So the object is HEADed here and enqueued only if what is
    really in the bucket is within bounds — otherwise it is deleted and refused.
    """
    _rate_limit(_client_key(request))
    body = await request.json()
    key, upload_id = str(body.get("key") or ""), str(body.get("uploadId") or "")
    _require_own_key(key)
    parts = body.get("parts") or []
    if not isinstance(parts, list) or not parts:
        raise HTTPException(status_code=400, detail="no parts")
    if not R2_ENDPOINT:
        raise HTTPException(status_code=503, detail="object store not configured")

    s3 = _r2_client()
    s3.complete_multipart_upload(
        Bucket=R2_INCOMING_BUCKET, Key=key, UploadId=upload_id,
        MultipartUpload={"Parts": [{"PartNumber": int(p["partNumber"]),
                                    "ETag": str(p["etag"])} for p in parts]})
    try:
        head = s3.head_object(Bucket=R2_INCOMING_BUCKET, Key=key)
    except Exception:
        raise HTTPException(status_code=404, detail="upload not found after completion")
    if head.get("ContentLength", 0) > MULTIPART_MAX:
        s3.delete_object(Bucket=R2_INCOMING_BUCKET, Key=key)
        raise UploadError("too_large", "That file is larger than we accept",
                          "The completed object exceeds the upload ceiling.",
                          hint="Nothing was analysed.", status=413)

    kind = str(body.get("kind") or "")
    if kind not in _QUEUE_KINDS:
        s3.delete_object(Bucket=R2_INCOMING_BUCKET, Key=key)
        raise HTTPException(status_code=400, detail="unknown kind")
    job_id = _enqueue_job(key, kind, int(body.get("n_samples") or 1),
                          str(body.get("notify_email") or ""),
                          bool(body.get("newsletter")))
    return {"job_id": job_id, "status": "queued", "r2_key": key}


@app.post("/analyze/r2")
async def analyze_r2(request: Request):
    """Analyse an object the browser just PUT to R2, and answer with the report.

    This is the instant path for compressed files: same detection, same tiering,
    same renderer as /analyze — the only difference is where the bytes came from.
    It deliberately does NOT enqueue: the common upload is a consumer export that
    interprets in seconds, and handing someone a claim link for that would trade
    the good experience for a worker dependency it does not need.

    The object is deleted as soon as it has been read, whatever the outcome. The
    reports promise the upload does not outlive the request, and an object left
    behind after a failed parse would quietly break that.
    """
    _rate_limit(_client_key(request))
    body = await request.json()
    key = str(body.get("key") or "")
    tissue = str(body.get("tissue") or "")

    # only keys this service minted: our prefix, and no traversal out of it
    if not key.startswith(R2_INLINE_PREFIX) or ".." in key:
        raise HTTPException(status_code=400, detail="unknown object")
    if not R2_ENDPOINT:
        raise HTTPException(status_code=503, detail="object store not configured")

    s3 = _r2_client()
    try:
        head = s3.head_object(Bucket=R2_INCOMING_BUCKET, Key=key)
    except Exception:
        raise HTTPException(status_code=404, detail="upload not found")
    # trust the object, not the caller's earlier claim about it: the size was
    # checked at signing time against a number the client supplied, and nothing
    # stopped it PUTting something else.
    if head.get("ContentLength", 0) > INLINE_R2_MAX:
        s3.delete_object(Bucket=R2_INCOMING_BUCKET, Key=key)
        raise UploadError(
            "needs_large_file_upload", "That file goes through the large-file upload",
            "The uploaded object is larger than the instant path accepts.",
            hint="Use the large-file upload instead.", status=413)

    scratch = tempfile.mkdtemp(prefix="dnr-r2-")
    local = os.path.join(scratch, os.path.basename(key))
    try:
        s3.download_file(R2_INCOMING_BUCKET, key, local)
        # returns (path, note) — a ZIP is unpacked to its genotype member, a .gz
        # is passed through because detect and the parsers read gzip directly
        display = os.path.basename(key)
        local, unwrapped = unwrap_archive(local, scratch)
        if unwrapped:
            display = os.path.basename(local)
        return _run_and_respond(local, tissue or None, filename=display)
    finally:
        # the object goes whether or not the analysis worked
        try:
            s3.delete_object(Bucket=R2_INCOMING_BUCKET, Key=key)
        except Exception as exc:
            print(f"R2 inline cleanup FAILED for {key}: {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
        shutil.rmtree(scratch, ignore_errors=True)


@app.get("/health")
def health():
    """Liveness + what this instance can actually do, so an operator can tell a
    standalone box from a queue-backed one without reading its env — and which
    build it is running, so a deploy can be confirmed with one request instead of
    grepping a rendered report for markers."""
    return {"status": "ok", "version": __version__, "commit": BUILD_COMMIT,
            "built": BUILD_TIME, "queue": queue_enabled(),
            "json_api": bool(API_KEY), "demos": sorted(list(_DEMOS) + ["combined"])}


@app.get("/api/openapi.json")
def api_openapi(x_api_key: str = Header(default=""), api_key: str = ""):
    """OpenAPI schema for the JSON API — key-gated (the API itself is key-gated,
    so its docs are too). Rate-limited like any JSON request."""
    _check_api_key(x_api_key, api_key)
    from fastapi.openapi.utils import get_openapi
    return JSONResponse(get_openapi(title="DNA-Report API", version="1.0",
                                    description="Content-negotiated JSON API. Send "
                                    "Accept: application/json or ?format=json with a "
                                    "valid X-API-Key (or ?api_key=) to /analyze, "
                                    "/demo/{kind}, /demo/combined.",
                                    routes=app.routes))


@app.get("/api/docs", response_class=HTMLResponse)
def api_docs(x_api_key: str = Header(default=""), api_key: str = ""):
    """Swagger UI for the JSON API, pointed at the key-gated schema. The key is
    supplied via the ?api_key= query param so the UI can fetch the schema."""
    _check_api_key(x_api_key, api_key)
    from fastapi.openapi.docs import get_swagger_ui_html
    return get_swagger_ui_html(openapi_url=f"/api/openapi.json?api_key={api_key or x_api_key}",
                               title="DNA-Report API docs")


@app.post("/analyze")
async def analyze_inline(file: UploadFile = File(...), tissue: str = Form(default=""),
                         format: str = "", accept: str = Header(default=""),
                         x_api_key: str = Header(default=""), api_key: str = ""):
    """Small inline uploads only. Returns HTML by default, or JSON (key-guarded)
    when the client sends Accept: application/json or ?format=json.

    Consumer exports arrive as ZIPs (23andMe, AncestryDNA, MyHeritage and FTDNA
    all hand the user one), so an archive is unwrapped here and the genotype file
    inside it is what gets analysed. The browser also unwraps before sending —
    this is the backstop for API callers.
    """
    display = os.path.basename(file.filename or "upload")
    scratch = tempfile.mkdtemp(prefix="dnr-web-")
    # The scratch dir holds the caller's raw genotype data and MUST NOT outlive
    # the request. Every report and refusal page this service prints says the
    # uploaded file is deleted after processing; without the finally below that
    # promise was false, and the front door accumulated one directory of
    # someone's genome per upload, forever. On the archive path both copies are
    # in here (the original .zip and the member extracted from it).
    try:
        # basename() so a crafted filename cannot write outside the scratch dir
        local = os.path.join(scratch, display)
        await stream_to_disk(file, local)

        local, unwrapped = unwrap_archive(local, scratch)
        if unwrapped:
            display = os.path.basename(local)

        return _run_and_respond(local, tissue or None, filename=display,
                                want_json=_wants_json(accept, format),
                                x_api_key=x_api_key, key_q=api_key)
    except UploadError as exc:
        # name the file the user actually chose, so the refusal page can show it
        exc.filename = exc.filename if getattr(exc, "filename", "") else display
        raise
    finally:
        # _run_and_respond has already read the rendered report into the response
        # body, so nothing here is still needed once the request is answered.
        shutil.rmtree(scratch, ignore_errors=True)


@app.post("/enqueue")
def enqueue(payload: dict, authorization: str = Header(default="")):
    """Called by the R2 upload Worker (not reviewers). Push a heavy job."""
    if not ENQUEUE_TOKEN or authorization != f"Bearer {ENQUEUE_TOKEN}":
        raise HTTPException(status_code=401, detail="bad enqueue token")
    # Optional, UNBUNDLED consent (see the upload form): a user may give an email
    # ONLY to be notified their report is ready, and SEPARATELY opt in to the
    # newsletter. The two are independent — an email for delivery is never added
    # to a mailing list unless `newsletter` is also true. Both default off.
    job_id = _enqueue_job(payload["r2_key"], payload["kind"],
                          payload.get("n_samples", 1),
                          payload.get("notify_email") or "",
                          payload.get("newsletter"))
    return {"job_id": job_id, "status": "queued"}


@app.get("/result/{job_id}")
def result(job_id: str):
    """Serve a finished report by its claim link. While the worker is still
    running the job, return a friendly self-refreshing 'processing' page (HTTP
    202) rather than a bare error — the user can bookmark this URL and come back;
    it becomes the report the moment the worker writes it."""
    # job_id is a hex uuid — reject anything else so this can't read arbitrary files
    if not re.fullmatch(r"[0-9a-f]{32}", job_id or ""):
        raise HTTPException(status_code=404, detail="unknown job")
    # worker-produced report in R2 (a worker runs off-box); then the local inline
    # path's disk; else it's still being worked -> friendly 202 page.
    r2_html = _r2_result_html(job_id)
    if r2_html is not None:
        return HTMLResponse(r2_html)
    out = os.path.join(RESULT_DIR, f"{job_id}.html")
    if os.path.exists(out):
        return HTMLResponse(Path(out).read_text())
    return HTMLResponse(pages.waiting_page(job_id), status_code=202)

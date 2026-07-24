# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""DNA-Report web front door — the HTTP layer reviewers actually hit.

The CLI (dnareport.cli) is for local/operator use; this is the service Cloudflare
fronts. It is deliberately thin: it owns routing decisions (inline vs queued) and
result serving, and delegates all analysis to the orchestrator + engines.

Endpoints:
  GET  /health                 -> liveness
  POST /analyze                 -> small INLINE upload (23andMe, small beta-matrix,
                                   single VCF): detect -> run -> render -> return HTML.
                                   Heavy kinds are refused here with a pointer to the
                                   R2 upload flow (they must not stream through the
                                   front door; see dna-report-deploy/cloudflare).
  POST /enqueue                 -> called by the R2 upload Worker after a big file
                                   lands in R2: {r2_key, kind, n_samples?} -> push a
                                   job on the queue -> return {job_id}. Bearer-token
                                   auth (ENQUEUE_TOKEN), not reviewer-facing.
  GET  /result/{job_id}         -> serve a finished report (202 if still running).

Queue + result store are configured by env (the deployment sets them); with no
queue backend the /enqueue path is disabled and only inline analysis runs, so the
app degrades to a standalone analyzer.
"""
from __future__ import annotations
import os, re, json, uuid, tempfile, html as _html
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Form
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from .detect import detect, InputKind
from .tiering import job_tier, queue_enabled, QUEUED
from .orchestrate import analyze, compare, render, _marker_url
from .tissue import infer_tissue
from .landing import LANDING_HTML
from .serialize import result_to_json

RESULT_DIR = os.environ.get("DNAREPORT_RESULT_DIR", tempfile.gettempdir())
QUEUE_URL = os.environ.get("DNAREPORT_QUEUE_URL")
ENQUEUE_TOKEN = os.environ.get("ENQUEUE_TOKEN")
# R2 results bucket: worker-produced reports land here (a worker runs on a
# different machine than the app, so R2 is the shared substrate). The app reads
# from it to serve /result. Only the trusted app holds these read creds — worker
# NODES need only write access. No-op fallback to local disk when R2 is unset
# (the inline /analyze path writes to RESULT_DIR on the same box).
R2_RESULTS_BUCKET = os.environ.get("R2_RESULTS_BUCKET", "dna-report-results")
R2_ENDPOINT = os.environ.get("R2_ENDPOINT")

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
    except Exception:
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


def _clocks_html(result) -> str:
    """Render the epigenetic-clock section (a MethylAsk concept, so composed here
    in the product layer rather than in bio-core's finding renderer). Clocks that
    are not valid for the sample tissue, or implausible, are shown as flagged —
    never as a bare misleading number."""
    if not result.clocks:
        return ""
    tis = result.tissue or "unspecified"
    rows = []
    for c in result.clocks:
        if getattr(c, "valid", c.age is not None):
            val = f"{c.age:.1f} yrs"
            cls = "ok"
        else:
            val = "not reported"
            cls = "flag"
        rows.append(
            f"<tr class='{cls}'><td>{_html.escape(c.clock)}</td>"
            f"<td>{val}</td><td class='note'>{_html.escape(c.note)}</td></tr>")
    return f"""<section class="clocks">
<h2>Epigenetic age (aging clocks)</h2>
<p class="csub">Sample type: <b>{_html.escape(tis)}</b>. A clock is only meaningful
on the tissue it was trained on; clocks that do not apply to this sample are marked
below rather than given a number.</p>
<table><tr><th>Clock</th><th>Estimated age</th><th>Basis</th></tr>
{''.join(rows)}</table>
<style>
.clocks{{margin:0 0 26px}}.clocks table{{width:100%;border-collapse:collapse;font-size:14px}}
.clocks th,.clocks td{{text-align:left;padding:7px 10px;border-bottom:1px solid #e4e4e2;vertical-align:top}}
.clocks .note{{color:#666;font-size:13px}}.clocks tr.flag td{{color:#8a5a00}}
.clocks .csub{{color:#666;font-size:14px;margin:2px 0 12px}}
</style></section>"""


def _render_full(result, out_path: str) -> str:
    """Compose the browser report: clocks section (product layer) + bio-core's
    finding/disclaimer render. Returns the path written."""
    render(result, out_path)                       # bio-core findings + disclaimer
    body = Path(out_path).read_text()
    clocks = _clocks_html(result)
    if clocks:
        # inject the clocks section after the first heading
        if "<body>" in body:
            body = body.replace("<body>", "<body>\n" + clocks, 1)
        else:
            body = clocks + body
        Path(out_path).write_text(body)
    return out_path


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
    if job_tier(kind) == QUEUED:
        raise HTTPException(
            status_code=413,
            detail=(f"{kind.value} is a heavy input; upload it via the large-file "
                    "flow (a pre-signed R2 URL), not this endpoint."))
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
        return JSONResponse({"kind": kind.value, "n_findings": 0, "notes": res.notes})
    out = os.path.join(RESULT_DIR, f"{uuid.uuid4().hex}.html")
    _render_full(res, out)
    return HTMLResponse(Path(out).read_text())


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


@app.get("/health")
def health():
    return {"status": "ok", "queue": queue_enabled()}


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
    when the client sends Accept: application/json or ?format=json."""
    scratch = tempfile.mkdtemp(prefix="dnr-web-")
    local = os.path.join(scratch, file.filename)
    with open(local, "wb") as fh:
        fh.write(await file.read())
    return _run_and_respond(local, tissue or None, filename=file.filename,
                            want_json=_wants_json(accept, format),
                            x_api_key=x_api_key, key_q=api_key)


@app.post("/enqueue")
def enqueue(payload: dict, authorization: str = Header(default="")):
    """Called by the R2 upload Worker (not reviewers). Push a heavy job."""
    if not ENQUEUE_TOKEN or authorization != f"Bearer {ENQUEUE_TOKEN}":
        raise HTTPException(status_code=401, detail="bad enqueue token")
    q = _queue()
    if q is None:
        raise HTTPException(status_code=503, detail="no queue backend configured")
    job_id = uuid.uuid4().hex
    job = {"job_id": job_id, "r2_key": payload["r2_key"], "kind": payload["kind"],
           "n_samples": payload.get("n_samples", 1)}
    # Optional, UNBUNDLED consent (see the upload form): a user may give an email
    # ONLY to be notified their report is ready, and SEPARATELY opt in to the
    # newsletter. The two are independent — an email for delivery is never added
    # to a mailing list unless `newsletter` is also true. Both default off.
    email = (payload.get("notify_email") or "").strip()
    if email and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        job["notify_email"] = email
        job["newsletter"] = bool(payload.get("newsletter"))
    q.rpush("dnareport:jobs", json.dumps(job))
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
    waiting = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>DNA-Report — preparing your report</title>
<meta http-equiv="refresh" content="15">
<style>body{{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;max-width:34em;
margin:12vh auto;padding:0 22px;color:#1a1a1a;line-height:1.6;text-align:center}}
@media(prefers-color-scheme:dark){{body{{background:#141414;color:#eee}}}}
.spin{{width:34px;height:34px;border:3px solid #ccc;border-top-color:#2b6a5b;border-radius:50%;
margin:0 auto 18px;animation:s 1s linear infinite}}@keyframes s{{to{{transform:rotate(360deg)}}}}
.id{{font-family:ui-monospace,monospace;font-size:13px;color:#666}}</style></head><body>
<div class="spin"></div>
<h1>Preparing your report</h1>
<p>Your file is being analysed. This page refreshes itself — you can also
<strong>bookmark this link</strong> and return any time; your report will be here when it's ready.</p>
<p class="id">Job {_html.escape(job_id)}</p>
<p style="font-size:13px;color:#666">Large genome files can take several minutes. Your uploaded
file is deleted after processing.</p>
</body></html>"""
    return HTMLResponse(waiting, status_code=202)

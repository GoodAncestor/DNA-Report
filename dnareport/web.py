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
import os, json, uuid, tempfile, html as _html
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
# Simple API key for the JSON surface. Placeholder auth — a real key-management
# / Cloudflare service-token layer goes over this later (see PRODUCTION_TODO).
API_KEY = os.environ.get("DNAREPORT_API_KEY", "goodancestor")
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

app = FastAPI(title="DNA-Report", docs_url=None, redoc_url=None)


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
    """Guard the JSON surface with the simple placeholder key."""
    if (x_api_key or key_q) != API_KEY:
        raise HTTPException(status_code=401,
                            detail="JSON output requires a valid API key "
                                   "(header X-API-Key or ?api_key=).")


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
    res = compare(local) if kind == InputKind.VCF else analyze(local, tissue=tissue)

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
    return _run_and_respond(path, tissue, filename=fname,
                            want_json=_wants_json(accept, format),
                            x_api_key=x_api_key, key_q=api_key)


@app.get("/health")
def health():
    return {"status": "ok", "queue": queue_enabled()}


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
    q.rpush("dnareport:jobs", json.dumps(job))
    return {"job_id": job_id, "status": "queued"}


@app.get("/result/{job_id}")
def result(job_id: str):
    """Serve a finished report; 202 while the worker is still running it."""
    out = os.path.join(RESULT_DIR, f"{job_id}.html")
    if os.path.exists(out):
        return HTMLResponse(Path(out).read_text())
    raise HTTPException(status_code=202, detail="job still running or not found")

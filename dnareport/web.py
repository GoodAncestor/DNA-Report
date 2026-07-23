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
import os, json, uuid, tempfile
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.responses import HTMLResponse, JSONResponse

from .detect import detect, InputKind
from .tiering import job_tier, queue_enabled, QUEUED
from .orchestrate import analyze, compare, render

RESULT_DIR = os.environ.get("DNAREPORT_RESULT_DIR", tempfile.gettempdir())
QUEUE_URL = os.environ.get("DNAREPORT_QUEUE_URL")
ENQUEUE_TOKEN = os.environ.get("ENQUEUE_TOKEN")

app = FastAPI(title="DNA-Report", docs_url=None, redoc_url=None)


def _queue():
    """Lazy redis handle; None when no backend configured (standalone mode)."""
    if not QUEUE_URL:
        return None
    import redis
    return redis.from_url(QUEUE_URL)


@app.get("/health")
def health():
    return {"status": "ok", "queue": queue_enabled()}


@app.post("/analyze")
async def analyze_inline(file: UploadFile = File(...)):
    """Small inline uploads only. Heavy kinds are refused with a pointer to R2."""
    scratch = tempfile.mkdtemp(prefix="dnr-web-")
    local = os.path.join(scratch, file.filename)
    with open(local, "wb") as fh:
        fh.write(await file.read())
    kind = detect(local)
    if job_tier(kind) == QUEUED:
        raise HTTPException(
            status_code=413,
            detail=(f"{kind.value} is a heavy input; upload it via the R2 flow "
                    "(the upload endpoint mints a pre-signed URL), not this endpoint."))
    res = compare(local) if kind == InputKind.VCF else analyze(local)
    out = os.path.join(RESULT_DIR, f"{uuid.uuid4().hex}.html")
    if not res.findings:
        return JSONResponse({"kind": kind.value, "n_findings": 0, "notes": res.notes})
    render(res, out)
    return HTMLResponse(Path(out).read_text())


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

"""Tests for the HTTP front door (dnareport.web).

Pins the routing contract Cloudflare fronts: health, inline small-upload path,
heavy-kind refusal (must go via R2), and the enqueue token gate. Runs without a
queue backend (standalone mode), so /enqueue is expected to reject.
"""
import os, tempfile
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
import dnareport.web as web

client = TestClient(web.app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_small_beta_matrix_is_inline():
    # a tiny beta-matrix takes the inline path (light); returns HTML or a no-findings json
    beta = "probe,S1\ncg00000029,0.55\ncg00000109,0.72\n"
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        f.write(beta); path = f.name
    with open(path, "rb") as fh:
        r = client.post("/analyze", files={"file": ("sample.csv", fh, "text/csv")})
    os.unlink(path)
    assert r.status_code == 200  # inline, not refused


def test_idat_is_refused_with_r2_pointer():
    # a heavy kind must not stream through the front door
    with tempfile.NamedTemporaryFile("wb", suffix=".idat", delete=False) as f:
        f.write(b"\x00" * 64); path = f.name
    with open(path, "rb") as fh:
        r = client.post("/analyze", files={"file": ("x.idat", fh, "application/octet-stream")})
    os.unlink(path)
    assert r.status_code == 413
    assert "R2" in r.json()["detail"] or "upload" in r.json()["detail"].lower()


def test_enqueue_requires_token():
    # no ENQUEUE_TOKEN set + no queue -> unauthorized (token check is first)
    r = client.post("/enqueue", json={"r2_key": "k", "kind": "idat"})
    assert r.status_code == 401


def test_result_valid_but_unready_is_202():
    # a well-formed (hex uuid) job id that hasn't finished -> friendly 202 page
    import uuid
    r = client.get(f"/result/{uuid.uuid4().hex}")
    assert r.status_code == 202
    assert "Preparing your report" in r.text


def test_result_malformed_id_is_404():
    # a non-hex id can't be a real job and must not reach the filesystem
    assert client.get("/result/nonexistent").status_code == 404
    assert client.get("/result/..%2f..%2fetc%2fpasswd").status_code == 404

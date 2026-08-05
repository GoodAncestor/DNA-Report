"""Every upload goes to a worker — and degrades to inline when it cannot.

The inline path has to finish inside Cloudflare's ~100s ceiling while the browser
holds the connection, and a measured 0.2 MB / 50,000-variant VCF took 94.7s on the
app box. The ceiling is also what forced the finding caps, which is the wrong thing
to trade away in a health report. A worker has no ceiling.

The degrade matters as much as the routing: a standalone deployment with no queue
and no object store must still answer, or this change breaks every install that
isn't ours.
"""
import io
import uuid

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
import dnareport.web as web

client = TestClient(web.app)

BETA = b"probe,S1\ncg00000029,0.55\ncg00000109,0.72\n"


def _files(name="sample.csv", body=BETA):
    return {"file": (name, io.BytesIO(body), "text/csv")}


def test_without_a_queue_the_upload_is_still_answered_inline(monkeypatch):
    """Standalone mode: ALWAYS_QUEUE is intent, `_queue_is_usable` is capability.

    Forced rather than assumed, because this suite is also run inside the deployed
    container, where a queue and an object store really are configured.
    """
    monkeypatch.setattr(web, "_queue_is_usable", lambda: False)
    r = client.post("/analyze", files=_files())
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_with_a_queue_the_upload_becomes_a_job(monkeypatch):
    captured = {}

    def fake_enqueue(r2_key, kind, n_samples=1, notify_email="", newsletter=False):
        captured.update(r2_key=r2_key, kind=kind, notify_email=notify_email,
                        newsletter=newsletter)
        return "f" * 32

    class FakeS3:
        def upload_file(self, local, bucket, key):
            captured["uploaded"] = (bucket, key)

    monkeypatch.setattr(web, "_queue_is_usable", lambda: True)
    monkeypatch.setattr(web, "_enqueue_job", fake_enqueue)
    monkeypatch.setattr(web, "_r2_client", lambda: FakeS3())

    r = client.post("/analyze", files=_files())

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["job_id"] == "f" * 32
    assert body["claim_url"] == "/result/" + "f" * 32
    # the file must reach the bucket the worker pulls from, under the queued prefix
    bucket, key = captured["uploaded"]
    assert bucket == web.R2_INCOMING_BUCKET
    assert key.startswith(web.R2_QUEUED_PREFIX)
    assert captured["r2_key"] == key


def test_an_opt_in_address_survives_the_handoff(monkeypatch):
    """/analyze used to answer immediately, so it dropped these. Now it queues,
    and dropping them would silently ignore someone who asked to be told."""
    captured = {}
    monkeypatch.setattr(web, "_queue_is_usable", lambda: True)
    monkeypatch.setattr(web, "_r2_client",
                        lambda: type("S", (), {"upload_file": lambda *a: None})())
    monkeypatch.setattr(web, "_enqueue_job",
                        lambda k, kind, n=1, email="", nl=False:
                        captured.update(email=email, nl=nl) or "a" * 32)

    client.post("/analyze", files=_files(),
                data={"notify_email": "someone@example.com", "newsletter": "1"})

    assert captured["email"] == "someone@example.com"
    assert captured["nl"] is True


def test_consent_stays_unbundled(monkeypatch):
    """An address for delivery is not a newsletter signup."""
    captured = {}
    monkeypatch.setattr(web, "_queue_is_usable", lambda: True)
    monkeypatch.setattr(web, "_r2_client",
                        lambda: type("S", (), {"upload_file": lambda *a: None})())
    monkeypatch.setattr(web, "_enqueue_job",
                        lambda k, kind, n=1, email="", nl=False:
                        captured.update(email=email, nl=nl) or "a" * 32)

    client.post("/analyze", files=_files(),
                data={"notify_email": "someone@example.com"})

    assert captured["email"] == "someone@example.com"
    assert captured["nl"] is False


def test_the_advisory_kind_never_claims_multi_sample():
    """The app does not open the file, so its label must not be able to send a
    single-sample VCF down the concordance path. Only an explicit request may."""
    assert web._advisory_kind("x.vcf.gz") == "vcf"
    assert web._advisory_kind("AncestryDNA.txt") == "array_genotype"
    assert web._advisory_kind("scan.idat") == "idat"
    for name in ("x.vcf", "a.txt", "b.idat", "c.bam", "d.csv", ""):
        assert web._advisory_kind(name) != "vcf-multi"


def test_the_queue_switch_can_be_turned_off():
    """This route is new in production; reverting it must not need a deploy."""
    import importlib, os
    os.environ["DNAREPORT_ALWAYS_QUEUE"] = "0"
    try:
        importlib.reload(web)
        assert web.ALWAYS_QUEUE is False
        assert web._queue_is_usable() is False
    finally:
        os.environ.pop("DNAREPORT_ALWAYS_QUEUE", None)
        importlib.reload(web)
    assert web.ALWAYS_QUEUE is True

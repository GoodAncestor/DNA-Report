import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from dnareport import explain
from dnareport.detect import InputKind
import dnareport.web as web


client = TestClient(web.app)


def test_only_admin_key_can_pick_a_cli_backend():
    assert web.explain_backend_for("admin", "codex_cli") == "codex_cli"
    assert web.explain_backend_for("admin", "claude_cli") == "claude_cli"
    assert web.explain_backend_for("admin", "nonsense") is None
    assert web.explain_backend_for("partner", "codex_cli") is None
    assert web.explain_backend_for("", "codex_cli") is None


def test_job_payload_carries_the_approved_backend(monkeypatch):
    queued = []

    class Queue:
        def rpush(self, name, payload):
            queued.append((name, json.loads(payload)))

        def set(self, *args, **kwargs):
            pass

    monkeypatch.setattr(web, "_queue", lambda: Queue())
    web._enqueue_job("incoming/x.vcf", "vcf", explain_backend="codex_cli")

    assert queued[0][1]["explain_backend"] == "codex_cli"


def test_enqueue_passes_an_admin_choice_without_passing_the_key(monkeypatch):
    captured = {}
    saved_keys = dict(web.API_KEYS)
    web.API_KEYS.clear()
    web.API_KEYS["admin-secret"] = "admin"
    monkeypatch.setattr(web, "ENQUEUE_TOKEN", "edge-secret")

    def fake_enqueue(*args, **kwargs):
        captured.update(kwargs)
        return "a" * 32

    monkeypatch.setattr(web, "_enqueue_job", fake_enqueue)
    try:
        response = client.post(
            "/enqueue?explain=codex_cli",
            json={"r2_key": "incoming/x.vcf", "kind": "vcf"},
            headers={
                "Authorization": "Bearer edge-secret",
                "X-API-Key": "admin-secret",
            },
        )
    finally:
        web.API_KEYS.clear()
        web.API_KEYS.update(saved_keys)

    assert response.status_code == 200
    assert captured["explain_backend"] == "codex_cli"
    assert "admin-secret" not in json.dumps(captured)


def test_analyze_r2_passes_an_admin_choice_to_its_job(monkeypatch):
    captured = {}
    saved_keys = dict(web.API_KEYS)
    web.API_KEYS.clear()
    web.API_KEYS["admin-secret"] = "admin"
    monkeypatch.setattr(web, "R2_ENDPOINT", "https://example.invalid")
    monkeypatch.setattr(web, "_queue_is_usable", lambda: True)
    monkeypatch.setattr(
        web,
        "_r2_client",
        lambda: type(
            "ObjectStore",
            (),
            {"head_object": lambda *args, **kwargs: {"ContentLength": 10}},
        )(),
    )

    def fake_enqueue(*args, **kwargs):
        captured.update(kwargs)
        return "b" * 32

    monkeypatch.setattr(web, "_enqueue_job", fake_enqueue)
    try:
        response = client.post(
            "/analyze/r2?explain=claude_cli",
            json={"key": f"{web.R2_INLINE_PREFIX}abc/sample.vcf"},
            headers={"X-API-Key": "admin-secret"},
        )
    finally:
        web.API_KEYS.clear()
        web.API_KEYS.update(saved_keys)

    assert response.status_code == 200
    assert captured["explain_backend"] == "claude_cli"


def test_inline_path_reads_the_cache_only(monkeypatch, tmp_path):
    local = tmp_path / "sample.csv"
    local.write_text("probe,S1\ncg00000029,0.55\n")
    result = type("Result", (), {"findings": [], "notes": [], "scan_stats": {}})()
    calls = []

    monkeypatch.setattr(web, "detect", lambda path: InputKind.BETA_MATRIX)
    monkeypatch.setattr(web, "analyze", lambda path, tissue=None: result)
    monkeypatch.setattr(
        explain,
        "explain_promoted",
        lambda actual, **kwargs: calls.append((actual, kwargs)),
    )
    monkeypatch.setattr(web, "report_html", lambda *args, **kwargs: "<html></html>")

    web._run_and_respond(str(local), "blood", filename="sample.csv")

    assert calls == [(result, {"cache_only": True})]

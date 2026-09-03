from pathlib import Path

from fastapi.testclient import TestClient

from dnareport.detect import InputKind
from dnareport.orchestrate import ReportResult
import dnareport.explain as explain
import dnareport.web as web


client = TestClient(web.app)
DEMO = Path(__file__).resolve().parents[1] / "dnareport" / "demo_data" / "demo_genome.vcf"


def _post(monkeypatch, *, age="", sex=""):
    calls = []

    def fake_analyze(path, **kwargs):
        calls.append(kwargs)
        return ReportResult(kind=InputKind.VCF, engines=("geneask",))

    monkeypatch.setattr(web, "_queue_is_usable", lambda: False)
    monkeypatch.setattr(web, "analyze", fake_analyze)
    monkeypatch.setattr(explain, "explain_promoted", lambda result, **kwargs: None)
    monkeypatch.setattr(
        web,
        "report_html",
        lambda result, **kwargs: "\n".join(result.notes) or "report",
    )
    with DEMO.open("rb") as handle:
        response = client.post(
            "/analyze",
            files={"file": ("sample.vcf", handle, "text/plain")},
            data={"age": age, "sex": sex},
        )
    return response, calls


def test_multipart_age_and_sex_reach_analyze(monkeypatch):
    response, calls = _post(monkeypatch, age="52", sex="female")

    assert response.status_code == 200
    assert calls == [{"tissue": "blood", "age": 52.0, "sex": "female"}]


def test_bad_person_inputs_are_ignored_with_notes(monkeypatch):
    response, calls = _post(monkeypatch, age="250", sex="x")

    assert calls[0].get("age") is None
    assert calls[0].get("sex") is None
    assert "Age was not a number between 0 and 120 and was ignored" in response.text
    assert "Sex was not female, male, or other and was ignored" in response.text


def test_nonnumeric_age_is_ignored(monkeypatch):
    response, calls = _post(monkeypatch, age="abc")

    assert calls[0].get("age") is None
    assert "Age was not a number between 0 and 120 and was ignored" in response.text


def test_enqueued_job_carries_validated_age_and_sex(monkeypatch):
    class Queue:
        def __init__(self):
            self.items = []

        def rpush(self, _name, value):
            self.items.append(value)

        def set(self, *args, **kwargs):
            pass

    queue = Queue()
    monkeypatch.setattr(web, "_queue", lambda: queue)

    web._enqueue_job("incoming/sample.vcf", "vcf", age="52", sex="female")

    job = __import__("json").loads(queue.items[0])
    assert job["age"] == 52.0
    assert job["sex"] == "female"

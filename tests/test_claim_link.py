"""A claim link must eventually stop saying "preparing".

Every way a queued job can fail used to look exactly like a slow one: a 202
behind a page that refreshed for ever. A worker that died between leasing a job
and writing its report produced no error anywhere, so the link waited on a report
nobody was writing, and the person watching it was told nothing.
"""
import uuid

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
import dnareport.web as web

client = TestClient(web.app)


def _job():
    return uuid.uuid4().hex


def test_a_running_job_still_says_preparing():
    r = client.get(f"/result/{_job()}")
    assert r.status_code == 202
    assert "Preparing your report" in r.text
    assert r.headers["Retry-After"] == "15"


def test_a_running_job_says_how_long_it_has_waited(monkeypatch):
    """The page looked identical after twenty seconds and after two hours."""
    monkeypatch.setattr(web, "_job_waited", lambda job_id: 240.0)
    r = client.get(f"/result/{_job()}")
    assert r.status_code == 202
    assert "Waiting 4 minutes so far" in r.text


def test_an_overdue_job_gives_up_instead_of_refreshing(monkeypatch):
    monkeypatch.setattr(web, "_job_waited",
                        lambda job_id: web.JOB_OVERDUE_SECONDS + 60)
    r = client.get(f"/result/{_job()}")

    # not a result, and a polling script must be able to tell
    assert r.status_code == 504
    assert "overdue" in r.text.lower()
    # and it must not keep the user in the refresh loop it just escaped
    assert "http-equiv=\"refresh\"" not in r.text
    assert "Upload the file again" in r.text


def test_a_dead_lettered_job_says_so_at_once(monkeypatch):
    """A known failure is answered immediately, not after the overdue timeout."""
    monkeypatch.setattr(web, "_job_dead_lettered",
                        lambda job_id: "RuntimeError: mirror volume is read-only")
    r = client.get(f"/result/{_job()}")

    assert r.status_code == 500
    assert "did not complete" in r.text
    assert "mirror volume is read-only" in r.text


def test_a_finished_report_still_wins_over_every_failure_state(monkeypatch, tmp_path):
    """An overdue or dead-lettered job whose report DID land must serve it — the
    reasons above are only reachable when there is nothing to give."""
    job_id = _job()
    (tmp_path / f"{job_id}.html").write_text("<html>the report</html>")
    monkeypatch.setattr(web, "RESULT_DIR", str(tmp_path))
    monkeypatch.setattr(web, "_job_waited", lambda j: web.JOB_OVERDUE_SECONDS + 999)
    monkeypatch.setattr(web, "_job_dead_lettered", lambda j: "some error")

    r = client.get(f"/result/{job_id}")

    assert r.status_code == 200
    assert "the report" in r.text


def test_elapsed_reads_as_time_not_seconds():
    from dnareport.pages import _elapsed

    assert _elapsed(45) == "45 seconds"
    assert _elapsed(600) == "10 minutes"
    assert _elapsed(3900) == "1 hour 5 minutes"
    assert _elapsed(7200) == "2 hours"
    assert _elapsed(None) == ""

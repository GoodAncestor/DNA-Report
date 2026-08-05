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


# ---------------------------------------------------------------- exports
def test_the_claim_link_serves_markdown(monkeypatch):
    monkeypatch.setattr(web, "_r2_result_html",
                        lambda job, fmt="html": "# DNA-Report\n" if fmt == "md" else None)
    job = _job()
    r = client.get(f"/result/{job}?format=md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert f'{job}.md' in r.headers["content-disposition"]
    assert r.text.startswith("# DNA-Report")


def test_the_claim_link_serves_json(monkeypatch):
    monkeypatch.setattr(web, "_r2_result_html",
                        lambda job, fmt="html": '{"schema_version": "1.2"}'
                        if fmt == "json" else None)
    r = client.get(f"/result/{_job()}?format=json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["schema_version"] == "1.2"


def test_an_agent_can_ask_by_accept_header(monkeypatch):
    """Someone else's agent should not have to know about a query parameter."""
    seen = {}

    def fake(job, fmt="html"):
        seen["fmt"] = fmt
        return "{}" if fmt == "json" else None
    monkeypatch.setattr(web, "_r2_result_html", fake)

    client.get(f"/result/{_job()}", headers={"Accept": "application/json"})
    assert seen["fmt"] == "json"


def test_an_explicit_format_beats_the_accept_header(monkeypatch):
    """A browser sends a long Accept list it did not choose; ?format= is always
    deliberate."""
    assert web._requested_format("md", "application/json") == "md"
    assert web._requested_format("", "application/json") == "json"
    assert web._requested_format("", "text/html,*/*") == "html"


def test_a_missing_export_does_not_masquerade_as_a_missing_report(monkeypatch):
    """If only the JSON is absent, the caller must not be told the job is still
    running for ever — it falls through to the same waiting/overdue logic."""
    monkeypatch.setattr(web, "_r2_result_html", lambda job, fmt="html": None)
    r = client.get(f"/result/{_job()}?format=json")
    assert r.status_code in (202, 500, 504)


# --------------------------------------------------------------- stored gzip
def test_a_compressed_report_is_inflated_on_read():
    """Reports are stored gzipped, and boto3 returns the stored bytes regardless
    of ContentEncoding — so the reader has to inflate them itself."""
    import gzip
    from dnareport.web import _maybe_gunzip
    assert _maybe_gunzip(gzip.compress(b"<html>hi</html>")) == b"<html>hi</html>"


def test_reports_stored_before_compression_still_read():
    """The bucket holds plain objects written before this change; both shapes must
    work from one code path, which is why the magic number is sniffed rather than
    the metadata trusted."""
    from dnareport.web import _maybe_gunzip
    assert _maybe_gunzip(b"<html>plain</html>") == b"<html>plain</html>"


def test_a_corrupt_gzip_does_not_take_the_page_down():
    from dnareport.web import _maybe_gunzip
    assert _maybe_gunzip(b"\x1f\x8btruncated") == b"\x1f\x8btruncated"

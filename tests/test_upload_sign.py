"""The direct-to-R2 instant path: /upload/sign and /analyze/r2.

Compressed uploads cannot reach the analyser through the front door — the edge
refuses request bodies it cannot parse, which is every zip and every .gz, and
that is most real uploads. These two routes send the bytes to object storage
instead and keep the report immediate.

What matters here is the gating. A presigned URL is only safe because of what it
is bound to, so the tests pin the bindings rather than the happy path: the key is
minted server-side and never taken from the caller, unsupported formats and
oversized files are refused before a URL exists at all, and /analyze/r2 will not
name an object outside the prefix this service created.
"""
import os

import pytest

pytest.importorskip("fastapi")
os.environ.setdefault("DNAREPORT_API_KEY", "test-key")
from fastapi.testclient import TestClient

import dnareport.web as web

client = TestClient(web.app)


def _sign(**body):
    return client.post("/upload/sign", json=body)


def test_unsupported_extension_is_refused_before_a_url_exists():
    # the point of the whole route: no URL, so nothing can be uploaded and then
    # rejected hours later
    r = _sign(filename="scan.pdf", size=1000)
    assert r.status_code == 415
    assert "url" not in r.text


def test_bz2_is_refused_since_nothing_reads_it():
    assert _sign(filename="genome.vcf.bz2", size=1000).status_code == 415


def test_oversized_file_is_sent_to_the_large_upload_not_signed():
    r = _sign(filename="genome.vcf.gz", size=web.INLINE_R2_MAX + 1)
    assert r.status_code == 413
    assert "url" not in r.text


def test_compression_suffix_is_stripped_before_the_extension_check():
    # .vcf.gz must be judged as a .vcf — this is the file type that motivated the
    # whole path, so it must not be refused by its own compression
    assert web._extension_supported("consensus.GRCh37.vcf.gz") is True
    assert web._extension_supported("export.zip") is True
    assert web._extension_supported("genome.tsv") is True
    assert web._extension_supported("photo.jpg") is False


def test_analyze_r2_refuses_a_key_outside_the_prefix_it_mints():
    # the caller must not be able to name an object belonging to the queued flow,
    # or anything else in the bucket
    for key in ("incoming/abc/other.vcf", "../secrets", "inline/../incoming/x.vcf"):
        r = client.post("/analyze/r2", json={"key": key})
        assert r.status_code == 400, f"{key!r} should be refused, got {r.status_code}"


def test_analyze_r2_rejects_an_empty_key():
    assert client.post("/analyze/r2", json={}).status_code == 400


@pytest.mark.skipif(not os.environ.get("R2_ENDPOINT"),
                    reason="no object store configured in this environment")
def test_sign_mints_its_own_key_and_ignores_caller_paths():
    r = _sign(filename="../../etc/passwd", size=10)
    assert r.status_code == 200
    key = r.json()["key"]
    assert key.startswith(web.R2_INLINE_PREFIX)
    assert ".." not in key and "/etc/" not in key


class _FakeS3:
    """Enough of the S3 client for /analyze/r2, backed by a local file.

    The route's real failure mode was never about R2: it mis-used
    unwrap_archive's (path, note) return and passed a tuple into detect(). Every
    test covering it skipped without an object store, so a 500 shipped. Faking
    the three calls it makes keeps the download -> unwrap -> analyse path under
    test everywhere.
    """
    def __init__(self, src, size=None):
        self.src, self.size, self.deleted = src, size, []

    def head_object(self, Bucket, Key):
        return {"ContentLength": self.size if self.size is not None
                else os.path.getsize(self.src)}

    def download_file(self, Bucket, Key, dest):
        import shutil
        shutil.copyfile(self.src, dest)

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)


def _fake_r2(monkeypatch, src, size=None):
    fake = _FakeS3(src, size)
    monkeypatch.setattr(web, "_r2_client", lambda: fake)
    monkeypatch.setattr(web, "R2_ENDPOINT", "https://example.invalid")
    return fake


def test_analyze_r2_runs_the_real_pipeline_on_a_downloaded_object(monkeypatch, tmp_path):
    # a small beta-matrix takes the inline path and renders; the point is that
    # the route gets all the way through detect/analyse without a TypeError
    src = tmp_path / "sample.csv"
    src.write_text("probe,S1\ncg00000029,0.55\ncg00000109,0.72\n")
    fake = _fake_r2(monkeypatch, str(src))
    r = client.post("/analyze/r2",
                    json={"key": f"{web.R2_INLINE_PREFIX}abc/sample.csv"})
    assert r.status_code == 200, r.text[:300]


def test_analyze_r2_deletes_the_object_even_when_analysis_fails(monkeypatch, tmp_path):
    # the deletion promise cannot be conditional on success
    src = tmp_path / "junk.txt"
    src.write_text("this is not a genotype file at all\n")
    fake = _fake_r2(monkeypatch, str(src))
    key = f"{web.R2_INLINE_PREFIX}abc/junk.txt"
    r = client.post("/analyze/r2", json={"key": key},
                    headers={"X-Error-Format": "json"})
    assert r.status_code >= 400          # unreadable -> refusal
    assert key in fake.deleted           # ...and still cleaned up


def test_analyze_r2_rechecks_size_against_the_object_not_the_claim(monkeypatch, tmp_path):
    # signing trusted a client-supplied size; nothing stopped it PUTting more
    src = tmp_path / "sample.csv"
    src.write_text("probe,S1\ncg00000029,0.55\n")
    fake = _fake_r2(monkeypatch, str(src), size=web.INLINE_R2_MAX + 1)
    key = f"{web.R2_INLINE_PREFIX}abc/sample.csv"
    r = client.post("/analyze/r2", json={"key": key},
                    headers={"X-Error-Format": "json"})
    assert r.status_code == 413
    assert key in fake.deleted           # oversized object is not left behind


def test_sign_without_an_object_store_says_so_rather_than_failing_obscurely():
    if web.R2_ENDPOINT:
        pytest.skip("this deployment has an object store")
    r = _sign(filename="genome.vcf.gz", size=1000)
    assert r.status_code == 503

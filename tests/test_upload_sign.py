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


def test_sign_without_an_object_store_says_so_rather_than_failing_obscurely():
    if web.R2_ENDPOINT:
        pytest.skip("this deployment has an object store")
    r = _sign(filename="genome.vcf.gz", size=1000)
    assert r.status_code == 503

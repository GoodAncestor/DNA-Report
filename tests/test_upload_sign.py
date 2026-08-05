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
    # the deletion promise cannot be conditional on success. Scoped to the inline
    # path: when the job is queued instead, the object is deliberately left for the
    # worker to pull and delete (see the test below).
    monkeypatch.setattr(web, "_queue_is_usable", lambda: False)
    src = tmp_path / "junk.txt"
    src.write_text("this is not a genotype file at all\n")
    fake = _fake_r2(monkeypatch, str(src))
    key = f"{web.R2_INLINE_PREFIX}abc/junk.txt"
    r = client.post("/analyze/r2", json={"key": key},
                    headers={"X-Error-Format": "json"})
    assert r.status_code >= 400          # unreadable -> refusal
    assert key in fake.deleted           # ...and still cleaned up


def test_analyze_r2_hands_the_object_to_the_worker_instead_of_deleting_it(monkeypatch,
                                                                          tmp_path):
    """On the queued path the object MUST survive the request — the worker pulls it
    from R2 itself. Deleting it here would queue a job whose input is already gone,
    which is a report that can never be produced."""
    src = tmp_path / "sample.csv"
    src.write_text("probe,S1\ncg00000029,0.55\n")
    fake = _fake_r2(monkeypatch, str(src))
    monkeypatch.setattr(web, "_queue_is_usable", lambda: True)
    monkeypatch.setattr(web, "_enqueue_job",
                        lambda key, kind, *a, **kw: "c" * 32)
    key = f"{web.R2_INLINE_PREFIX}abc/sample.csv"

    r = client.post("/analyze/r2", json={"key": key})

    assert r.status_code == 200
    assert r.json()["job_id"] == "c" * 32
    assert key not in fake.deleted


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


class _FakeMultipartS3:
    """The multipart calls, without R2. Records what it was asked to do so the
    gating can be asserted rather than assumed."""
    def __init__(self, size=10):
        self.size, self.completed, self.deleted, self.signed = size, [], [], []

    def create_multipart_upload(self, Bucket, Key):
        return {"UploadId": "upload-1"}

    def generate_presigned_url(self, op, Params, ExpiresIn):
        self.signed.append((op, Params["Key"], Params.get("PartNumber")))
        return f"https://r2.invalid/{Params['Key']}?part={Params.get('PartNumber')}"

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
        self.completed.append(Key)

    def head_object(self, Bucket, Key):
        return {"ContentLength": self.size}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)


def _fake_mp(monkeypatch, size=10):
    fake = _FakeMultipartS3(size)
    monkeypatch.setattr(web, "_r2_client", lambda: fake)
    monkeypatch.setattr(web, "R2_ENDPOINT", "https://example.invalid")
    return fake


def test_multipart_create_mints_its_own_key(monkeypatch):
    _fake_mp(monkeypatch)
    # a supported extension, so it gets past the format gate and actually reaches
    # key minting — the traversal is what is under test here
    r = client.post("/upload/multipart/create",
                    json={"filename": "../../etc/passwd.vcf", "size": 100})
    assert r.status_code == 200
    key = r.json()["key"]
    assert key.startswith(web.R2_QUEUED_PREFIX)
    assert ".." not in key and "/etc/" not in key


def test_multipart_create_refuses_a_format_with_no_parser(monkeypatch):
    _fake_mp(monkeypatch)
    assert client.post("/upload/multipart/create",
                       json={"filename": "x.bz2", "size": 100}).status_code == 415


@pytest.mark.parametrize("key", ["inline/abc/x.vcf", "../x", "other/x.vcf"])
def test_multipart_routes_refuse_keys_they_did_not_mint(monkeypatch, key):
    # sign/complete take a key from the caller; without this it is an arbitrary
    # string aimed at a bucket holding other people's uploads
    _fake_mp(monkeypatch)
    assert client.post("/upload/multipart/sign",
                       json={"key": key, "uploadId": "u", "parts": [1]}).status_code == 400
    assert client.post("/upload/multipart/complete",
                       json={"key": key, "uploadId": "u",
                             "parts": [{"partNumber": 1, "etag": "e"}],
                             "kind": "vcf"}).status_code == 400


def test_multipart_complete_verifies_the_object_then_enqueues(monkeypatch):
    fake = _fake_mp(monkeypatch, size=10)
    seen = {}
    monkeypatch.setattr(web, "_enqueue_job",
                        lambda *a, **k: seen.setdefault("called", True) and "job1" or "job1")
    key = f"{web.R2_QUEUED_PREFIX}abc/genome.vcf.gz"
    r = client.post("/upload/multipart/complete",
                    json={"key": key, "uploadId": "u",
                          "parts": [{"partNumber": 1, "etag": "e"}], "kind": "vcf"})
    assert r.status_code == 200
    assert fake.completed == [key]
    assert seen.get("called") is True


def test_multipart_complete_refuses_an_oversized_object_and_deletes_it(monkeypatch):
    # the size at create time was the client's claim; this is where it is checked
    fake = _fake_mp(monkeypatch, size=web.MULTIPART_MAX + 1)
    key = f"{web.R2_QUEUED_PREFIX}abc/genome.vcf.gz"
    r = client.post("/upload/multipart/complete",
                    json={"key": key, "uploadId": "u",
                          "parts": [{"partNumber": 1, "etag": "e"}], "kind": "vcf"},
                    headers={"X-Error-Format": "json"})
    assert r.status_code == 413
    assert key in fake.deleted


def test_multipart_complete_refuses_an_unknown_kind_and_deletes_it(monkeypatch):
    fake = _fake_mp(monkeypatch)
    key = f"{web.R2_QUEUED_PREFIX}abc/genome.vcf.gz"
    r = client.post("/upload/multipart/complete",
                    json={"key": key, "uploadId": "u",
                          "parts": [{"partNumber": 1, "etag": "e"}], "kind": "wat"})
    assert r.status_code == 400
    assert key in fake.deleted


def test_sign_without_an_object_store_says_so_rather_than_failing_obscurely():
    if web.R2_ENDPOINT:
        pytest.skip("this deployment has an object store")
    r = _sign(filename="genome.vcf.gz", size=1000)
    assert r.status_code == 503


@pytest.mark.parametrize("path", ["/upload/sign", "/upload/multipart/create",
                                  "/upload/multipart/sign",
                                  "/upload/multipart/complete", "/analyze/r2"])
def test_malformed_json_is_a_client_error_not_a_server_error(path):
    # a trailing comma from a caller used to raise JSONDecodeError and surface as
    # 500, which sends whoever is debugging looking for a fault on our side
    r = client.post(path, content=b'{"key":[1,2,3,]}',
                    headers={"content-type": "application/json"})
    assert r.status_code == 400, f"{path} answered {r.status_code}"

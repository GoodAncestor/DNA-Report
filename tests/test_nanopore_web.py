"""Native upload routing and metadata contracts without real queues or storage."""
import json
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import dnareport.web as web
from dnareport.landing import LANDING_HTML
from dnareport.orchestrate import ReportResult
from dnareport.detect import InputKind

META = {"tissue": "saliva", "age": "42", "sex": "female", "sample_id": "sample-01",
        "reference_build": "hg38", "min_coverage": "12", "combined_strands": True}
BED = "1\t10\t11\tm\t5\t.\t10\t11\t0,0,0\t5\t40\t2\t2\t1\t0\t0\t0\t0\n"


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(web, "ONT_UPLOADS_ENABLED", True)
    jobs, stored = [], []
    class Queue:
        def rpush(self, name, payload):
            jobs.append((name, json.loads(payload)))
        def set(self, *args, **kwargs):
            pass
    class Storage:
        def head_object(self, **kwargs):
            return {"ContentLength": 1024}
        def complete_multipart_upload(self, **kwargs):
            stored.append(kwargs)
        def create_multipart_upload(self, **kwargs):
            stored.append(kwargs)
            return {"UploadId": "fixture-upload"}
        def upload_file(self, *args):
            stored.append(args)
    monkeypatch.setattr(web, "_queue", lambda: Queue())
    monkeypatch.setattr(web, "_queue_is_usable", lambda: True)
    monkeypatch.setattr(web, "_r2_client", lambda: Storage())
    monkeypatch.setattr(web, "R2_ENDPOINT", "https://example.invalid")
    monkeypatch.setattr(web, "ENQUEUE_TOKEN", "fixture-only-token")
    monkeypatch.setattr(web, "_rate_limit", lambda *args: None)
    return TestClient(web.app), jobs, stored


def assert_meta(job):
    assert job["tissue"] == "saliva"
    assert job["age"] == 42.0
    assert job["sex"] == "female"
    assert job["sample_id"] == "sample-01"
    assert job["reference_build"] == "GRCh38"
    assert job["min_coverage"] == 12
    assert job["combined_strands"] is True


@pytest.mark.parametrize("kind,extension,queue", [
    ("modbam", "bam", "dnareport:jobs:ont"), ("pod5", "pod5", "dnareport:jobs:ont"),
    ("bedmethyl", "bed.gz", "dnareport:jobs")])
def test_multipart_complete_routes_and_preserves_metadata(transport, kind, extension, queue):
    client, jobs, stored = transport
    response = client.post("/upload/multipart/complete", json={
        "key": web.R2_QUEUED_PREFIX + "fixture/sample." + extension,
        "uploadId": "fixture-upload", "parts": [{"partNumber": 1, "etag": "fixture-etag"}],
        "kind": kind, **META})
    assert response.status_code == 200, response.text
    assert jobs[0][0] == queue
    assert_meta(jobs[0][1])
    assert stored


def test_multipart_metadata_is_checked_before_storage_mutation(transport):
    client, jobs, stored = transport
    response = client.post("/upload/multipart/create", json={"filename": "calls.bed", "size": 100})
    assert response.status_code == 400
    assert "reference" in response.text
    response = client.post("/upload/multipart/create", json={"filename": "sample.pod5", "size": 100})
    assert response.status_code == 400
    assert "label" in response.text
    assert stored == jobs == []


def test_raw_signal_is_an_accepted_upload(transport):
    client, jobs, stored = transport
    response = client.post("/upload/multipart/create", json={"filename": "sample.pod5", "size": 100, **META})
    assert response.status_code == 200, response.text
    assert web._extension_supported("sample.pod5")
    assert web._advisory_kind("sample.pod5") == "pod5"
    assert stored and not jobs


def test_direct_storage_route_preserves_metadata_and_routes_ont(transport):
    client, jobs, _ = transport
    response = client.post("/analyze/r2", json={"key": web.R2_INLINE_PREFIX + "fixture/sample.modbam", **META})
    assert response.status_code == 200, response.text
    assert jobs[0][0] == "dnareport:jobs:ont"
    assert_meta(jobs[0][1])


def test_bedmethyl_post_is_queued_with_metadata(transport):
    client, jobs, _ = transport
    response = client.post("/analyze", files={"file": ("calls.bed", BED)},
                           data={**META, "combined_strands": "true"})
    assert response.status_code == 200, response.text
    assert jobs[0][0] == "dnareport:jobs"
    assert jobs[0][1]["kind"] == "bedmethyl"
    assert_meta(jobs[0][1])


def test_detected_bedmethyl_requires_build_even_with_table_extension(transport):
    client, jobs, stored = transport
    response = client.post("/analyze", files={"file": ("calls.tsv", BED)})
    assert response.status_code == 400
    assert jobs == stored == []


def test_worker_enqueue_preserves_metadata_and_cannot_misroute_bam(transport):
    client, jobs, _ = transport
    response = client.post("/enqueue", headers={"Authorization": "Bearer fixture-only-token"},
                           json={"r2_key": "queued/fixture/sample.bam", "kind": "beta_matrix", **META})
    assert response.status_code == 200, response.text
    assert jobs[0][0] == "dnareport:jobs:ont"
    assert jobs[0][1]["kind"] == "modbam"
    assert_meta(jobs[0][1])


@pytest.mark.parametrize("invalid", [
    {"sample_id": "../sample"}, {"sample_id": ["sample"]}, {"min_coverage": "-1"},
    {"min_coverage": True}, {"combined_strands": "perhaps"}, {"reference_build": "hg19"},
    {"reference_fasta": "/data/reference.fa"}, {"nanopore_config": {"model": "any"}},
    {"tissue": "/data/sample"}, {"kind": []},
])
def test_invalid_or_server_owned_metadata_never_reaches_queue(transport, invalid):
    client, jobs, _ = transport
    response = client.post("/enqueue", headers={"Authorization": "Bearer fixture-only-token"},
                           json={"r2_key": "queued/fixture/sample.bam", "kind": "modbam", **META, **invalid})
    assert response.status_code == 400, response.text
    assert jobs == []


def test_inline_path_forwards_optional_metadata(monkeypatch, tmp_path):
    calls = []
    path = tmp_path / "sample.csv"
    path.write_text("probe,beta\ncg00000001,0.4\n")
    monkeypatch.setattr(web, "analyze", lambda path, **kw: calls.append(kw) or ReportResult(
        kind=InputKind.BETA_MATRIX, engines=("methylask",)))
    monkeypatch.setattr("dnareport.explain.explain_promoted", lambda *args, **kwargs: None)
    monkeypatch.setattr(web, "report_html", lambda *args, **kwargs: "report")
    web._run_and_respond(str(path), "saliva", **{k: v for k, v in META.items() if k != "tissue"})
    assert_meta(calls[0])


def _js_functions(*names):
    js = "\n".join(re.findall(r"<script>(.*?)</script>", LANDING_HTML, re.S))
    parts = []
    for name in names:
        match = re.search(rf"(?:async )?function {name}\(.*?\n \}}", js, re.S)
        assert match, name
        parts.append(match.group(0))
    return "\n".join(parts)


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_browser_multipart_sends_every_metadata_field():
    js = _js_functions("uploadMetadata", "largeUpload", "jsonOrThrow")
    program = """
const elements={sample_id:{value:'sample-01'},reference_build:{value:'GRCh38'},
 min_coverage:{value:'12'},combined_strands:{checked:true},notify_email:{value:''},newsletter:{checked:false}};
const document={getElementById:key=>elements[key]};
const tissue={value:'saliva'},ageIn={value:'42'},sexIn={value:'female'},ontControls={hidden:false};
const heavyKind=()=> 'bedmethyl',setOverlay=()=>{},R2_PART=16;
const sent=[];
async function fetch(url,options){
 if(url==='part-url')return {ok:true,headers:{get:()=> 'etag'}};
 sent.push([url,JSON.parse(options.body)]);
 const data=url.endsWith('/create')?{key:'key',uploadId:'upload',part_size:16}:
  url.endsWith('/sign')?{urls:{'1':'part-url'}}:{job_id:'job'};
 return {ok:true,json:async()=>data};
}
""" + js + "\nlargeUpload({name:'calls.bed',size:3,slice:()=> 'part'}).then(()=>console.log(JSON.stringify(sent)));"
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    bodies = dict(json.loads(result.stdout))
    for route in ("/upload/multipart/create", "/upload/multipart/complete"):
        body = bodies[route]
        assert body["sample_id"] == "sample-01"
        assert body["tissue"] == "saliva" and body["age"] == "42" and body["sex"] == "female"
        assert body["reference_build"] == "GRCh38"
        assert body["min_coverage"] == "12" and body["combined_strands"] is True


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_browser_inline_formdata_uses_shared_metadata():
    page_js = "\n".join(re.findall(r"<script>(.*?)</script>", LANDING_HTML, re.S))
    form_code = re.search(r"const fd=new FormData\(\);.*?(?=\s*// Opt-ins)", page_js, re.S)
    assert form_code
    program = """
const elements={sample_id:{value:'sample-01'},reference_build:{value:'GRCh38'},
 min_coverage:{value:'12'},combined_strands:{checked:false}};
const document={getElementById:key=>elements[key]};
const tissue={value:'saliva'},ageIn={value:'42'},sexIn={value:'female'},ontControls={hidden:false};
const payload=new Blob(['synthetic']);
""" + _js_functions("uploadMetadata") + "\n" + form_code.group(0) + """
console.log(JSON.stringify(Object.fromEntries([...fd.entries()].filter(([key])=>key!=='file'))));
"""
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "sample_id": "sample-01", "reference_build": "GRCh38", "min_coverage": "12",
        "combined_strands": "false", "tissue": "saliva", "age": "42", "sex": "female"}


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_browser_recognizes_real_modkit_rows_and_rejects_truncated_rows():
    cases = [{"name": "calls.bed", "text": BED},
             {"name": "calls.bed", "text": BED.replace("\t", " ")},
             {"name": "calls.bed", "text": "\t".join(BED.split("\t")[:11])},
             {"name": "sample.pod5", "text": ""}]
    page_js = "\n".join(re.findall(r"<script>(.*?)</script>", LANDING_HTML, re.S))
    constants = "\n".join(re.search(rf"const {name}\s*=.*?;", page_js).group(0)
                            for name in ("ALLOWED_EXT", "SNIFF_BINARY", "SNIFF_VCF"))
    js = constants + "\n" + _js_functions("extAllowed", "classifyHead")
    js += "\nconsole.log(JSON.stringify(" + json.dumps(cases) + ".map(c=>({allowed:extAllowed(c.name),label:classifyHead(c.name,c.text)}))));"
    result = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    results = json.loads(result.stdout)
    assert [r["label"] is not None for r in results] == [True, True, False, True]
    assert all(r["allowed"] for r in results)


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_large_gzip_sniff_reads_bounded_decoded_prefix():
    program = "const SNIFF_BYTES=262144;\n" + _js_functions("sniffHead") + """
const zlib=require('node:zlib'),crypto=require('node:crypto');
const prefix=Buffer.from('synthetic bedMethyl prefix\\n');
const compressed=zlib.gzipSync(Buffer.concat([prefix,crypto.randomBytes(1024*1024)]));
const file=new Blob([compressed]);file.name='calls.bed.gz';
sniffHead(file).then(text=>console.log(JSON.stringify({starts:text.startsWith(prefix.toString()),
 bounded:text.length<=SNIFF_BYTES,compressedSize:compressed.length})));
"""
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["starts"] and output["bounded"]
    assert output["compressedSize"] > 262144


@pytest.mark.parametrize('extension', ['bam', 'modbam', 'pod5'])
def test_raw_upload_disabled_before_storage_or_queue(transport, monkeypatch, extension):
    client, jobs, stored = transport
    monkeypatch.setattr(web, 'ONT_UPLOADS_ENABLED', False)
    payload = {'filename': 'sample.' + extension, 'size': 100, **META}
    key = web.R2_QUEUED_PREFIX + 'fixture/sample.' + extension
    requests = [
        ('/upload/sign', payload),
        ('/upload/multipart/create', payload),
        ('/upload/multipart/sign', {'key': key, 'uploadId': 'fixture', 'parts': [1]}),
        ('/upload/multipart/complete', {'key': key, 'uploadId': 'fixture', 'parts': [{'partNumber': 1, 'etag': 'x'}], 'kind': 'modbam', **META}),
        ('/analyze/r2', {'key': web.R2_INLINE_PREFIX + 'fixture/sample.' + extension, **META}),
    ]
    for route, body in requests:
        response = client.post(route, json=body)
        assert response.status_code == 503, (route, response.text)
        assert '/demo/nanopore' in response.text
    response = client.post('/analyze', files={'file': ('sample.' + extension, b'fixture')}, data=META)
    assert response.status_code == 503
    assert jobs == stored == []
    assert client.get('/health').json()['native_uploads_enabled'] is False
    assert 'const ONT_UPLOADS_ENABLED=false;' in client.get('/').text

"""Front-door tests: landing page, demo routes, disclaimer, clock flagging in HTML."""
import os
import pytest
pytest.importorskip("fastapi")
# configure a JSON-API key BEFORE importing the app (API_KEY is read at import)
os.environ["DNAREPORT_API_KEY"] = "test-key"
from fastapi.testclient import TestClient
import dnareport.web as web

client = TestClient(web.app)


def test_landing_has_upload_and_demos():
    r = client.get("/")
    assert r.status_code == 200
    assert "Drop a file here" in r.text
    assert "/demo/blood" in r.text and "/demo/buccal" in r.text
    assert 'id="tissue"' in r.text          # tissue selector present


def test_oversized_file_reaches_the_large_upload_instead_of_dead_ending():
    """A whole-genome file is refused AT THE EDGE, so the app's own 413 — and
    anything keyed to its error code — never runs. The page therefore has to
    decide by size before posting, and to recover on a bare 413 with no JSON
    body. Both are served-page behaviour, so both are asserted on the markup."""
    page = client.get("/").text
    assert "const INLINE_MAX" in page                  # size gate exists
    # the gate must be the SERVER's number, not a second opinion that can drift
    from dnareport.uploads import MAX_UPLOAD_BYTES
    assert f"const INLINE_MAX = {MAX_UPLOAD_BYTES};" in page
    assert "__INLINE_MAX__" not in page                # placeholder substituted
    assert "if(chosen.size>INLINE_MAX){ await runLargeUpload(chosen); return; }" in page
    # the 413 recovery must NOT be narrowed back to one error code: an edge 413
    # carries no body, so requiring err.code is what broke whole-genome upload
    assert "if(r.status===413){" in page
    assert "err.code==='needs_large_file_upload'" not in page


def test_disclaimer_served():
    r = client.get("/disclaimer")
    assert r.status_code == 200
    assert "What this report is" in r.text        # current disclaimer header
    # phrase may wrap across a newline in the source file
    assert "not medical advice" in " ".join(r.text.lower().split())


def test_blood_demo_all_clocks_valid():
    r = client.get("/demo/blood")
    assert r.status_code == 200
    assert "Epigenetic age" in r.text
    # blood: no clock should be flagged not-valid
    assert "not reported" not in r.text
    # real disclaimer, not the bio-core placeholder
    assert "See DISCLAIMER.md" not in r.text


def test_buccal_demo_flags_blood_clocks():
    r = client.get("/demo/buccal")
    assert r.status_code == 200
    # blood-trained clocks on buccal must be flagged, not shown as numbers
    assert "not reported" in r.text
    assert "not valid for this sample type" in r.text


def test_unknown_demo_404():
    assert client.get("/demo/nope").status_code == 404


def test_modality_bubble_present():
    # every finding carries a source-modality bubble; blood demo is methylome-only
    r = client.get("/demo/blood")
    assert "mod-methylome" in r.text


def test_json_requires_key():
    assert client.get("/demo/blood?format=json").status_code == 401


def test_json_with_key_returns_schema():
    r = client.get("/demo/blood?format=json&api_key=test-key")
    assert r.status_code == 200
    j = r.json()
    assert j["schema_version"] == "1.1"
    assert j["tissue"] == "blood"
    assert "by_topic" in j["summary"] and "by_tier" in j["summary"]
    assert j["clocks"] and all("valid" in c for c in j["clocks"])
    # findings carry the enriched fields
    f = j["findings"][0]
    assert set(["marker", "tier", "topic", "links", "stats"]).issubset(f)


def test_combined_demo_has_both_modalities():
    r = client.get("/demo/combined")
    assert r.status_code == 200
    assert "mod-methylome" in r.text
    assert "id='modfilter'" in r.text        # source filter appears when mixed
    assert "BRCA2" in r.text                  # genome ClinVar finding present


def test_combined_demo_json():
    j = client.get("/demo/combined?format=json&api_key=test-key").json()
    # genome ClinVar findings carry gene + cancer topic
    genes = {f.get("gene") for f in j["findings"]}
    assert "BRCA2" in genes
    cancer = [f for f in j["findings"] if f.get("topic") == "cancer"]
    assert any(f.get("gene") in ("BRCA2", "MSH2") for f in cancer)


def test_api_docs_gated():
    assert client.get("/api/openapi.json").status_code == 401
    assert client.get("/api/openapi.json?api_key=test-key").status_code == 200
    assert client.get("/api/docs?api_key=test-key").status_code == 200
    # public schema disabled
    assert client.get("/openapi.json").status_code == 404


def test_array_genotype_detected_and_routed():
    import tempfile, os
    from dnareport.detect import detect, InputKind, ROUTING
    d = tempfile.mkdtemp(); p = os.path.join(d, "ancestry.txt")
    open(p, "w").write("#AncestryDNA raw data download\n"
                       "rsid\tchromosome\tposition\tallele1\tallele2\n"
                       "rs1\t1\t100\tA\tG\n")
    assert detect(p) == InputKind.ARRAY_GENOTYPE
    assert ROUTING[InputKind.ARRAY_GENOTYPE] == ("geneask",)

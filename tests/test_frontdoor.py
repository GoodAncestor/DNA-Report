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
    assert j["schema_version"] == "2.1"
    # scan_stats is what lets a machine reader tell a bounded report from a
    # complete one, so its presence is part of the contract, not a detail
    assert "scan_stats" in j
    assert j["tissue"] == "blood"
    assert "by_topic" in j["summary"] and "by_tier" in j["summary"]
    assert j["clocks"] and all("valid" in c for c in j["clocks"])
    # findings carry the enriched fields
    f = j["findings"][0]
    assert set(["marker", "tier", "topic", "links", "stats"]).issubset(f)


def test_combined_demo_has_both_modalities():
    """Both halves must reach the page. Deliberately NOT asserting the source
    filter here: whether the methylome half survives display-splitting depends on
    which reference mirrors the host has, so on a machine without them this route
    renders a genome-only list and the filter correctly stays away. The filter's
    own rule is pinned in bio-core's test_render, where it can be stated without a
    data dependency."""
    r = client.get("/demo/combined")
    assert r.status_code == 200
    assert "class='mod mod-genome'" in r.text        # the genome half rendered
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


def test_combined_demo_scan_panel_describes_both_halves():
    """The scan summary must cover the whole report, not one half of it.

    The combined demo kept the methylome analysis's stats after merging in the
    genome findings, so the panel read identically to the methylome-only demo —
    "43 markers analysed" printed above a report carrying hundreds more findings
    than that, and a genome that HAD been analysed looked like one that never
    ran. Those numbers are the ones a reader is most likely to trust.
    """
    import re

    def panel(path):
        html = client.get(path).text
        pairs = re.findall(r"<span class='stat-n'>([^<]*)</span>"
                           r"<span class='stat-l'>([^<]*)</span>", html)
        return {label: value for value, label in pairs}

    combined, blood = panel("/demo/combined"), panel("/demo/blood")
    assert combined, "combined demo rendered no scan panel"
    assert blood, "blood demo rendered no scan panel"
    # scan_stats is not on the JSON surface, so this asserts on the rendered
    # panel — which is the thing the reader actually reads anyway
    assert combined != blood, (
        "the combined report's scan panel is identical to the methylome-only "
        f"one, so the genome half is not represented: {combined}")
    # specifically: the merged report counts more than the methylome alone.
    # Deliberately NOT asserted on "Your file" — that value is human-rounded, and
    # a 573-byte demo genome added to a 25 KB methylome still prints "25 KB".
    assert int(combined["Findings"]) > int(blood["Findings"])


def test_genome_demo_is_served_and_listed():
    """The bundled genome demo carries variants whose ClinVar submitters disagree,
    because that is the only class of finding the regulatory-VEP enrichment can
    reach: screen_findings admits a variant only when its significance string
    contains "pathogenic", so plain "Uncertain significance" never becomes a
    finding and never reaches AlphaGenome. Without this profile there was nothing
    on the live site that exercised the feature at all."""
    r = client.get("/demo/genome")
    assert r.status_code == 200
    assert "class='mod mod-genome'" in r.text
    assert "/demo/genome" in client.get("/").text          # reachable from the landing page
    assert "genome" in client.get("/health").json()["demos"]


def test_landing_explains_moderate():
    from dnareport.landing import LANDING_HTML
    assert "<b>Moderate</b>" in LANDING_HTML


def test_landing_asks_for_age_and_sex_optionally_and_posts_them():
    from dnareport.landing import LANDING_HTML
    assert 'id="age"' in LANDING_HTML and 'id="sex"' in LANDING_HTML
    assert "We estimate it from your file" in LANDING_HTML
    assert "fd.append('age',ageIn.value)" in LANDING_HTML
    assert "age:ageIn.value||''" in LANDING_HTML and "sex:sexIn.value||''" in LANDING_HTML

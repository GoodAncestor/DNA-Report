"""Front-door tests: landing page, demo routes, disclaimer, clock flagging in HTML."""
import pytest
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
import dnareport.web as web

client = TestClient(web.app)


def test_landing_has_upload_and_demos():
    r = client.get("/")
    assert r.status_code == 200
    assert "Drop a file here" in r.text
    assert "/demo/blood" in r.text and "/demo/buccal" in r.text
    assert 'id="tissue"' in r.text          # tissue selector present


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
    r = client.get("/demo/blood?format=json&api_key=goodancestor")
    assert r.status_code == 200
    j = r.json()
    assert j["schema_version"] == "1.0"
    assert j["tissue"] == "blood"
    assert "by_topic" in j["summary"] and "by_tier" in j["summary"]
    assert j["clocks"] and all("valid" in c for c in j["clocks"])
    # findings carry the enriched fields
    f = j["findings"][0]
    assert set(["marker", "tier", "topic", "links", "stats"]).issubset(f)

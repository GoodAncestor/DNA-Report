"""Local merged splicing reaches reports only in noncommercial mode."""
import pytest
from biocore.providers.base import Finding, Tier, Category


def _splicing_file(tmp_path):
    pysam = pytest.importorskip("pysam")
    path = tmp_path / "splice.tsv"
    path.write_text("#CHROM\tPOS\tREF\tALT\talphagenome_splicing\nchr19\t44908684\tT\tC\t0.25\n")
    bgz = str(path) + ".gz"
    pysam.tabix_compress(str(path), bgz, force=True)
    pysam.tabix_index(bgz, seq_col=0, start_col=1, end_col=1, zerobased=False, force=True)
    return bgz


def _finding():
    return Finding("19-44908684-T-C", "variant_lookup", "APOE", Tier.SPECULATIVE, [Category.CLINICAL],
                   detail={"reference_build": "GRCh38"})


def test_enrich_attaches_splicing(tmp_path, monkeypatch):
    from dnareport.predictions import enrich
    monkeypatch.delenv("DNAREPORT_OUTPUT_MODE", raising=False)
    monkeypatch.setenv("ALPHAGENOME_ATLAS_SPLICING_FILE", _splicing_file(tmp_path))
    f = _finding()
    status = enrich([f], offline=True)
    assert status["alphagenome_atlas_splicing"]["scored"] == 1
    assert f.detail["alphagenome_atlas_splicing"]["splicing_score"] == 0.25


def test_commercial_mode_strips_splicing(tmp_path, monkeypatch):
    from biocore.licensing import filter_findings_for_output
    from dnareport.predictions import enrich
    monkeypatch.setenv("ALPHAGENOME_ATLAS_SPLICING_FILE", _splicing_file(tmp_path))
    monkeypatch.setenv("DNAREPORT_OUTPUT_MODE", "commercial")
    f = _finding()
    status = enrich([f], offline=True)
    assert status["alphagenome_atlas_splicing"]["status"] == "license_blocked"
    assert "alphagenome_atlas_splicing" not in f.detail
    f.detail["alphagenome_atlas_splicing"] = {"splicing_score": 0.25}
    assert all("alphagenome_atlas_splicing" not in (x.detail or {}) for x in filter_findings_for_output([f]))

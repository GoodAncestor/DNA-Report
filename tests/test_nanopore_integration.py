"""Synthetic same-sample workflow through real engines and all report formats.

Only sequencer/caller executables and reference datasets are substituted. No
personal sequence, network calls, or clinical accuracy claims are involved.
"""
import json
from pathlib import Path
import sqlite3
import sys

import pytest
from tests.test_nanopore import fixture, stub_tools
from dnareport.orchestrate import analyze
from dnareport.report import render_report, compose_result_views


@pytest.fixture
def local_references(tmp_path, monkeypatch):
    from methylask.ingest import nanopore as ingest
    from methylask.providers import ewas_mirror
    from geneask.interpret import clinvar_screen
    from methylask import clocks
    from geneask.annotators import pharmcat
    original = ingest.read_bedmethyl
    probes = {f"cg{i:08}": ("chr1", i * 2) for i in range(45)}
    probes.update({"cg99999998": ("chr1", 92), "cg99999999": ("chr1", 94)})
    monkeypatch.setattr(ingest, "read_bedmethyl", lambda path, **kwargs: original(path, probe_map=probes, **kwargs))
    mirror = tmp_path / "ewas.sqlite"
    with sqlite3.connect(mirror) as db:
        db.execute("CREATE TABLE findings(cpg,trait,gene,beta,se,p,n,tissue,methylation_array,chrpos,pmid,efo)")
        db.executemany("INSERT INTO findings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
            (probe, "body mass index", "SYNTHETIC", "0.1", "0.01", "1e-9", "2000", "blood", "EPIC", "", "123", "")
            for probe in probes
        ])
    monkeypatch.setattr(ewas_mirror, "MIRROR_DB", mirror)
    panel = {"BRCA2": {"variants": [{"variant_id": "1-1-C-T", "gene": "BRCA2", "clinical_significance": "Likely pathogenic", "gold_stars": 2}]}}
    monkeypatch.setattr(clinvar_screen, "load_panel", lambda: panel)
    attempted = []
    def forbidden(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("Array prediction, PGx diplotyping, or network called for native input")
    monkeypatch.setattr(clocks, "run_all", forbidden)
    monkeypatch.setattr(pharmcat, "call_diplotypes", forbidden)
    monkeypatch.setattr(pharmcat, "available", lambda: True)
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    import socket
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    yield probes
    assert not attempted, "Native processing attempted an array prediction, personalized PGx or network call"


def bed_row(pos, depth=10, modified=3, other=2, strand="+"):
    return f"chr1\t{pos}\t{pos+1}\tm\t{depth}\t{strand}\t{pos}\t{pos+1}\t0,0,0\t{depth}\t{100*modified/depth}\t{modified}\t{depth-modified-other}\t{other}\t0\t0\t0\t0\n"


def test_bed_full_profile_depth_missingness_and_exports(tmp_path, local_references):
    bed = tmp_path / "sample.bed"
    bed.write_text("".join(bed_row(i * 2) for i in range(45)) + bed_row(92, 2, 1, 0))
    result = analyze(str(bed), reference_build="GRCh38", sample_id="SYNTHETIC", tissue="blood")
    methyl = result.scan_stats["nanopore"]["methylation"]
    assert methyl["mapped_probes"] == 45  # no legacy first-40 cap
    assert methyl["low_coverage_probes"] == 1
    assert methyl["missing_probes"] == 1
    assert len(result.findings) == 45
    assert result.clocks == []
    assert all(f.source != "marker_reference" for f in result.findings)
    assert all(f.detail["your reading"] == .3 for f in result.findings)  # other mods remain denominator
    assert all(f.detail["methylation_coverage"] == 10 for f in result.findings)
    assert all("from 10 valid reads" in f.interpretation.found for f in result.findings)
    views = compose_result_views(result)
    assert views["json"]["scan_stats"]["nanopore"]["sample_id"] == "SYNTHETIC"
    assert "Native sequencing measurements" in views["markdown"]
    path = tmp_path / "report.html"
    render_report(result, str(path))
    assert "Probes below coverage threshold" in path.read_text()
    assert "reference-group predictions are withheld" in path.read_text()


def test_source_bam_both_real_engines_one_report(fixture, stub_tools, local_references, tmp_path):
    bam, config = fixture
    result = analyze(str(bam), nanopore_config=config, tissue="blood", sample_id="test_sample")
    assert {f.detail["modality"] for f in result.findings} == {"genome", "methylome"}
    native = result.scan_stats["nanopore"]
    assert native["preparation"]["sample_id"] == native["sample_id"] == "test_sample"
    assert native["preparation"]["variant_qc"]["reported_records"] == 1
    assert native["methylation"]["mapped_probes"] == 1
    clinical = next(f for f in result.findings if f.source.startswith("clinvar"))
    assert "Clinical accuracy has not been established" in clinical.interpretation.how_sure
    assert "Pharmacogenomic diplotypes are withheld" in " ".join(result.notes)
    assert result.scan_stats["live_apis_called"] == []
    assert not list(tmp_path.glob("dnareport-ont-*"))
    render_report(result, str(tmp_path / "combined.html"))
    assert "test_sample" in (tmp_path / "combined.html").read_text()
    assert "from 10 valid reads" in (tmp_path / "combined.html").read_text()


def test_zero_matches_still_renders_measurements(tmp_path, local_references):
    bed = tmp_path / "empty-matches.bed"
    bed.write_text(bed_row(92, 2, 1, 0))
    result = analyze(str(bed), reference_build="hg38")
    assert not result.findings
    render_report(result, str(tmp_path / "report.html"))
    assert "Native sequencing measurements" in (tmp_path / "report.html").read_text()


def test_bed_requires_explicit_build(tmp_path):
    bed = tmp_path / "input.bed"
    bed.write_text(bed_row(0))
    with pytest.raises(ValueError, match="build|GRCh38|hg38"):
        analyze(str(bed))


def test_cli_exports_same_run(tmp_path, local_references, monkeypatch, capsys):
    from dnareport.cli import main
    bed = tmp_path / "input.bed"
    bed.write_text(bed_row(0))
    out = tmp_path / "private" / "report.html"
    monkeypatch.setattr(sys, "argv", ["dna-report", "analyze", str(bed), "--reference-build", "GRCh38", "--sample-id", "CLI-SYNTHETIC", "--out", str(out)])
    main()
    response = json.loads(capsys.readouterr().out)
    assert Path(response["exports"]["json"]).exists()
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in (out, out.with_suffix(".json"), out.with_suffix(".md")))
    exported = json.loads(out.with_suffix(".json").read_text())
    assert exported["scan_stats"]["nanopore"]["sample_id"] == "CLI-SYNTHETIC"
    assert "CLI-SYNTHETIC" in out.with_suffix(".md").read_text()
    assert "/result/" not in out.read_text()

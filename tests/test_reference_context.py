"""Published reference context on methylome findings.

MethylAsk positions a sample against curated, PMID-sourced population values.
This wires that into the report and pins the tissue gate: a reference measured
in a different tissue is reported as unavailable, never as a comparison.
"""
import csv
from pathlib import Path
import pytest

from dnareport.orchestrate import _reference_findings

DEMO = Path(__file__).parent.parent / "dnareport" / "demo_data"


def _betas(fname):
    out = {}
    with open(DEMO / fname) as fh:
        r = csv.reader(fh)
        next(r)
        for row in r:
            if row:
                out[row[0]] = float(row[1])
    return out


def test_blood_sample_gets_cited_reference_findings():
    fs = _reference_findings(_betas("demo_blood_wholeblood.csv"), tissue="blood")
    assert fs, "expected reference findings for the blood demo"
    smoking = [f for f in fs if f.marker == "cg05575921"]
    assert len(smoking) == 1
    assert "23691101" in smoking[0].pmids
    assert "never-smoker" in smoking[0].description


def test_reference_finding_names_the_statistic_not_average():
    fs = _reference_findings(_betas("demo_blood_wholeblood.csv"), tissue="blood")
    smoking = [f for f in fs if f.marker == "cg05575921"][0]
    # the source publishes medians; calling them averages misdescribes it
    stats = {g["stat"] for g in smoking.detail["groups"]}
    assert stats == {"median"}
    assert "average" not in smoking.description.lower()


def test_reference_finding_carries_human_copy_not_a_probe_id():
    fs = _reference_findings(_betas("demo_blood_wholeblood.csv"), tissue="blood")
    smoking = [f for f in fs if f.marker == "cg05575921"][0]
    d = smoking.detail
    assert d["label"] == "Tobacco smoke exposure"
    assert d["what_was_read"] and d["what_it_is_not"]
    # the sentence a person reads must not be a bare number dump
    assert "Your value is 0." not in smoking.description
    assert "never-smoker" in smoking.description


def test_reference_finding_carries_every_published_group_for_the_scale():
    fs = _reference_findings(_betas("demo_blood_wholeblood.csv"), tissue="blood")
    smoking = [f for f in fs if f.marker == "cg05575921"][0]
    groups = smoking.detail["groups"]
    assert [g["group"] for g in groups] == ["never-smoker", "former smoker", "current smoker"]
    assert all(0.0 <= g["beta"] <= 1.0 for g in groups)


def test_withheld_card_still_carries_its_human_copy():
    fs = _reference_findings(_betas("demo_buccal_pediatric.csv"), tissue="buccal")
    f = [x for x in fs if x.marker == "cg05575921"][0]
    assert f.detail["label"] == "Tobacco smoke exposure"
    assert f.detail["what_it_is_not"]
    assert not f.detail.get("groups")


def test_buccal_sample_reports_reference_as_unavailable_not_compared():
    from biocore.providers.base import Tier
    fs = _reference_findings(_betas("demo_buccal_pediatric.csv"), tissue="buccal")
    smoking = [f for f in fs if f.marker == "cg05575921"]
    assert len(smoking) == 1
    f = smoking[0]
    assert f.tier is Tier.UNKNOWN
    # must not contain a comparison to any smoking group
    assert "current smoker" not in f.description
    assert "never-smoker" not in f.description
    assert "buccal" in f.description and "whole blood" in f.description


# --- highlights: pulled to the top, merged with epigenetic age --------------

from dnareport.highlights import split_reference_findings, highlights_html


class _R:
    """Minimal stand-in for ReportResult."""
    def __init__(self, findings, tissue=None, clocks=()):
        self.findings, self.tissue, self.clocks = findings, tissue, list(clocks)


def test_reference_findings_are_split_out_of_the_findings_list():
    fs = _reference_findings(_betas("demo_blood_wholeblood.csv"), tissue="blood")
    from biocore.providers.base import Finding, Tier, Category
    plain = Finding(marker="cg00000029", source="ewas_catalog",
                    description="linked to age", tier=Tier.MODERATE,
                    categories=[Category.AGING])
    highlights, rest = split_reference_findings(fs + [plain])
    assert len(highlights) == 3
    assert rest == [plain]


def test_highlights_html_shows_value_group_and_cites_pubmed():
    fs = _reference_findings(_betas("demo_blood_wholeblood.csv"), tissue="blood")
    html = highlights_html(_R(fs, tissue="blood"))
    assert "cg05575921" in html
    assert "never-smoker" in html
    assert "pubmed.ncbi.nlm.nih.gov/23691101" in html


def test_highlights_html_withholds_comparison_for_wrong_tissue():
    fs = _reference_findings(_betas("demo_buccal_pediatric.csv"), tissue="buccal")
    html = highlights_html(_R(fs, tissue="buccal"))
    assert "cg05575921" in html
    assert "current smoker" not in html
    assert "never-smoker" not in html
    # states that nothing is compared, and names both the sample and reference tissue
    assert "No comparison shown" in html
    assert "buccal" in html and "whole blood" in html


def test_no_highlights_section_when_there_are_no_reference_findings():
    assert highlights_html(_R([], tissue="blood")) == ""


# --- EPICv2 replicate suffixes must not break the clocks --------------------

def test_epicv2_suffixed_probe_ids_still_resolve_clock_cpgs(tmp_path):
    """clocks.predict is keyed by BASE probe id. An EPICv2 export carries
    replicate suffixes (cg#####_TC21); if they are not stripped, coverage
    silently drops to zero and the report shows no age at all."""
    import csv as _csv
    from pathlib import Path as _P
    import methylask.clocks as ck
    from dnareport.orchestrate import _run_methylask, InputKind

    cdir = _P(ck.__file__).parent / "data" / "reference" / "clocks"
    probes = [r["Probe"] for r in _csv.DictReader(
        open(cdir / "Hannum2013_Blood.csv", newline="")) if r["Probe"] != "Intercept"]

    p = tmp_path / "epicv2.csv"
    with open(p, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["probe", "sample"])
        for pr in probes:
            w.writerow([f"{pr}_TC21", 0.5])          # EPICv2 replicate suffix

    _, _, clocks = _run_methylask(str(p), InputKind.BETA_MATRIX, tissue="blood")
    hannum = [c for c in clocks if c.clock == "Hannum2013_Blood"][0]
    assert hannum.n_found > 0, "suffixed probe ids did not resolve to clock CpGs"
    assert hannum.coverage > 0.9

"""One finding, every format, one truth.

The BRCA2 demo variant, screened against a panel that carries the v2 fields, is
interpreted, promoted, and must then read the same in HTML, JSON, Markdown and
MCP. The first test covers the data half (no interpretation); the rest cover
the meaning layer on top of it.
"""
from pathlib import Path
from biocore.variants.carried import carried_variants
from geneask.interpret.clinvar_screen import screen_findings
from dnareport.detect import InputKind
from geneask.annotators import cpic_pgx, pharmcat
from dnareport.orchestrate import ReportResult, _interpret_and_promote, _run_geneask
from dnareport.serialize import result_to_json
from dnareport.exports import report_markdown
from dnareport.mcp_server import one_finding, important_findings
from dnareport.report import report_html
from dnareport.triage import REASON_A

_DEMO = Path(__file__).resolve().parents[1] / "dnareport" / "demo_data" / "demo_genome.vcf"
_PANEL = {"BRCA2": {"variants": [{
    "variant_id": "13-32316419-CAG-C", "clinical_significance": "Likely pathogenic",
    "review_status": "criteria provided, multiple submitters, no conflicts", "gold_stars": 2,
    "clinvar_variation_id": "51063",
    "conditions": ["Hereditary breast ovarian cancer syndrome"],
    "condition_ids": ["MedGen:C0677776"], "molecular_consequence": "splice_acceptor_variant",
    "origin": ["germline"], "allele_id": "5001"}]}}
_REASON = REASON_A.format(list="ACMG SF v3.2")
_ID = "d" * 32


def _bare():
    fs = screen_findings(carried_variants(str(_DEMO)), _PANEL)
    assert len(fs) == 1
    r = ReportResult(kind=InputKind.VCF, engines=("geneask",))
    r.findings, r.notes, r.scan_stats = fs, [], {}
    return r


def _result():
    r = _bare()
    _interpret_and_promote(r)
    return r


def test_brca2_survives_every_format():
    doc = result_to_json(_bare())
    finding = doc["findings"][0]
    assert finding["detail"]["conditions"] == ["Hereditary breast ovarian cancer syndrome"]
    assert finding["detail"]["zygosity"] == "het" and finding["detail"]["gold_stars"] == 2
    md = report_markdown(_bare())
    assert "one altered copy" in md and "review stars: 2" in md
    mcp = one_finding(lambda _id: doc, _ID, "13-32316419-CAG-C")
    assert mcp["finding"]["detail"]["condition_ids"] == ["MedGen:C0677776"]


def test_brca2_is_interpreted_and_promoted_by_rule_a():
    r = _result()
    f = r.findings[0]
    assert f.interpretation.condition == "Hereditary breast and ovarian cancer syndrome"
    assert f.interpretation.zygosity == "het"
    assert f.promoted and f.promoted_reason == _REASON
    assert r.read_first == [f]


def test_brca2_reads_the_same_in_html_json_markdown_and_mcp():
    r = _result()
    html = report_html(r)
    assert "Read this first" in html and "Hereditary breast and ovarian cancer syndrome" in html
    assert "one altered copy" in html and _REASON in html
    assert "Confirm the result with a clinical laboratory test" in html
    assert "What was found" in html and "Sensible next step" in html

    doc = result_to_json(r)
    fj = doc["important"][0]
    assert fj["interpretation"]["condition"] == "Hereditary breast and ovarian cancer syndrome"
    assert fj["promoted_reason"] == _REASON
    assert fj["detail"]["zygosity"] == "het" and fj["detail"]["gold_stars"] == 2

    md = report_markdown(r)
    assert "## Read this first" in md and _REASON in md
    assert "Sensible next step" in md and "one altered copy" in md

    imp = important_findings(lambda _id: doc, _ID)
    assert [f["marker"] for f in imp["findings"]] == ["13-32316419-CAG-C"]
    assert imp["findings"][0]["interpretation"]["next_step"].startswith("Confirm")
    assert "diagnosis" in imp["disclaimer"]


def test_array_upload_explains_why_it_has_no_diplotype(monkeypatch):
    sample = Path(__file__).parent / "fixtures" / "sample_23andme.txt"
    monkeypatch.setattr(pharmcat, "available", lambda: True)
    monkeypatch.setattr(
        pharmcat,
        "call_diplotypes",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("An array must not enter PharmCAT.")
        ),
    )

    _findings, notes, _limits = _run_geneask(
        str(sample), InputKind.TWENTYTHREE_AND_ME
    )

    assert (
        "Pharmacogenomic guidance is by gene only: this file type does not "
        "support diplotype calling."
    ) in notes


def test_sequencing_calls_pharmcat_once_and_selects_by_phenotype(monkeypatch):
    calls = []
    selected = []
    monkeypatch.setattr(pharmcat, "available", lambda: True)

    def call_diplotypes(vcf_path, scratch, **kwargs):
        calls.append((vcf_path, scratch))
        return {
            "CYP2C19": {
                "diplotype": "*1/*2",
                "phenotype": "Intermediate Metabolizer",
                "activity_score": 1.0,
                "source": "PharmCAT 3.4.0",
            }
        }

    def recommendations(gene, phenotype, **kwargs):
        selected.append((gene, phenotype, kwargs))
        return []

    monkeypatch.setattr(pharmcat, "call_diplotypes", call_diplotypes)
    monkeypatch.setattr(cpic_pgx, "recommendations_for_phenotype", recommendations)

    _run_geneask(str(_DEMO), InputKind.VCF)

    assert len(calls) == 1
    assert selected[0][0:2] == ("CYP2C19", "Intermediate Metabolizer")
    assert selected[0][2]["diplotype"] == "*1/*2"

"""One finding, three exports, one data record."""
from pathlib import Path

from biocore.variants.carried import carried_variants
from dnareport.detect import InputKind
from dnareport.exports import report_markdown
from dnareport.mcp_server import one_finding
from dnareport.orchestrate import ReportResult
from dnareport.serialize import result_to_json
from geneask.interpret.clinvar_screen import screen_findings


_DEMO = (
    Path(__file__).resolve().parents[1]
    / "dnareport" / "demo_data" / "demo_genome.vcf"
)
_PANEL = {"BRCA2": {"variants": [{
    "variant_id": "13-32316419-CAG-C",
    "clinical_significance": "Likely pathogenic",
    "review_status": "criteria provided, multiple submitters, no conflicts",
    "gold_stars": 2, "clinvar_variation_id": "51063",
    "conditions": ["Hereditary breast ovarian cancer syndrome"],
    "condition_ids": ["MedGen:C0677776"],
    "molecular_consequence": "splice_acceptor_variant",
    "origin": ["germline"], "allele_id": "5001",
}]}}


def test_brca2_survives_every_format():
    carried = carried_variants(str(_DEMO))
    findings = screen_findings(carried, _PANEL)
    assert len(findings) == 1
    result = ReportResult(kind=InputKind.VCF, engines=("geneask",))
    result.findings, result.notes, result.scan_stats = findings, [], {}
    doc = result_to_json(result)
    finding = doc["findings"][0]
    assert finding["detail"]["conditions"] == [
        "Hereditary breast ovarian cancer syndrome"
    ]
    assert finding["detail"]["zygosity"] == "het"
    assert finding["detail"]["gold_stars"] == 2
    markdown = report_markdown(result)
    assert "one altered copy" in markdown
    assert "review stars: 2" in markdown
    mcp = one_finding(
        lambda _report_id: doc, "d" * 32, "13-32316419-CAG-C"
    )
    assert mcp["finding"]["detail"]["condition_ids"] == ["MedGen:C0677776"]

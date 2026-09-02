from biocore.providers.base import Category, Finding, Tier
from dnareport.detect import InputKind
from dnareport.exports import report_markdown
from dnareport.orchestrate import ReportResult
from tests.test_serialize_v2 import _brca2


def _result(findings):
    result = ReportResult(kind=InputKind.VCF, engines=("geneask",))
    result.findings = findings
    result.notes = []
    result.scan_stats = {}
    return result


def test_read_this_first_leads_and_carries_meaning():
    brca2 = _brca2()
    brca2.link = "https://www.ncbi.nlm.nih.gov/clinvar/variation/51063/"
    markdown = report_markdown(_result([brca2]), filename="genome.vcf")
    assert markdown.index("## Read this first") < markdown.index("## Summary")
    assert "Several labs agree this change is pathogenic" in markdown
    assert "Hereditary breast and ovarian cancer syndrome" in markdown
    assert "one altered copy" in markdown
    assert "https://www.ncbi.nlm.nih.gov/clinvar/variation/51063/" in markdown
    for part in (
        "What was found", "What it can mean", "How sure", "Next step",
    ):
        assert part in markdown


def test_same_tier_orders_by_magnitude_then_marker():
    weak = Finding(
        marker="rs1", source="gwas_catalog", description="a", tier=Tier.ROBUST,
        categories=[Category.TRAIT], detail={"p": 1e-9, "topic": "other"},
    )
    strong = Finding(
        marker="rs2", source="gwas_catalog", description="b", tier=Tier.ROBUST,
        categories=[Category.TRAIT], detail={"p": 1e-100, "topic": "other"},
    )
    markdown = report_markdown(_result([weak, strong]))
    assert markdown.index("`rs2`") < markdown.index("`rs1`")

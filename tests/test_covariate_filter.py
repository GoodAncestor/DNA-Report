"""Study-design variables are not findings about the reader.

"Tissue" carries 328,363 associations — the catalog's second-largest trait — and
describes the sample, not the person. Rendering "Tissue — associated with lower
methylation at this site" tells a reader nothing.
"""
from dnareport.highlights import split_display_findings
from biocore.providers.base import Category, Finding, Tier
from dnareport.detect import InputKind
from dnareport.orchestrate import ReportResult


class _F:
    def __init__(self, marker, detail=None, source="ewas_catalog"):
        self.marker, self.source, self.description = marker, source, "d"
        self.detail = dict(detail or {})


def test_covariate_findings_are_withheld_from_the_report():
    keep, dropped = split_display_findings([
        _F("cg1", {"trait": "Tissue", "trait_class": "covariate"}),
        _F("cg2", {"trait": "BMI", "trait_class": "health_trait"}),
    ])
    assert [f.detail["trait"] for f in keep] == ["BMI"]
    assert [f.detail["trait"] for f in dropped] == ["Tissue"]


def test_unclassified_traits_are_kept():
    # only ~400 of 6,515 traits are classified; absence must not mean "drop"
    keep, dropped = split_display_findings([_F("cg1", {"trait": "some rare trait"})])
    assert len(keep) == 1 and not dropped


def test_other_class_is_kept_pending_a_decision():
    # "Sex" and friends were flagged ambiguous, not confirmed covariates
    keep, _ = split_display_findings([_F("cg1", {"trait": "Sex", "trait_class": "other"})])
    assert len(keep) == 1


def test_non_ewas_findings_are_untouched():
    keep, _ = split_display_findings([_F("cg1", {}, source="clinvar")])
    assert len(keep) == 1


def _finding(marker="cg1", detail=None):
    return Finding(
        marker=marker, source="ewas_catalog", description="association",
        tier=Tier.MODERATE, categories=[Category.AGING], detail=detail or {},
    )


def test_covariates_are_withheld_from_json_and_markdown():
    from dnareport.report import compose_result_views

    result = ReportResult(kind=InputKind.BETA_MATRIX, engines=("methylask",))
    result.findings = [
        _finding(detail={"trait_class": "covariate", "topic": "other"}),
        _finding(marker="cg2", detail={"topic": "other"}),
    ]
    views = compose_result_views(result)
    assert [finding["marker"] for finding in views["json"]["findings"]] == ["cg2"]
    assert "cg1" not in views["markdown"]

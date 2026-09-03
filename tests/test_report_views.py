"""The product hands the renderer the outcome view, the actions and the person."""
import types
from biocore.providers.base import Finding, Tier, Category, Interpretation
from dnareport.detect import InputKind
from dnareport.orchestrate import ReportResult
from dnareport.report import report_html


def _brca2():
    return Finding(marker="13-32316419-CAG-C", source="clinvar_mirror", description="x", tier=Tier.ROBUST,
                   categories=[Category.CLINICAL], detail={"gene": "BRCA2", "topic": "cancer"},
                   interpretation=Interpretation(found="f", can_mean="m", how_sure="s", next_step="n"),
                   promoted=True, promoted_reason="why")


def test_report_html_carries_outcomes_actions_and_person():
    r = ReportResult(kind=InputKind.VCF, engines=("geneask",))
    f = _brca2()
    r.findings, r.notes, r.scan_stats, r.read_first = [f], [], {}, [f]
    r.outcomes = [types.SimpleNamespace(kind="condition", key="hboc", label="Hereditary breast and ovarian cancer",
                                        findings=[f], score=None, contributions=[], reference_groups=[], actions=[])]
    r.actions = [types.SimpleNamespace(text="Confirm with a clinical test.", why="why", source_label="ACMG", url=None, outcome_key="hboc")]
    r.age, r.age_source, r.sex, r.sex_source = 52, "user", "female", "guess"
    h = report_html(r)
    assert "data-outcome='hboc'" in h and "href='#view=outcome'" in h
    assert "What people do with results like these" in h and "Confirm with a clinical test." in h
    assert "<section id='read-first'" not in h and 'data-default-view="outcome"' in h


def test_report_html_without_outcomes_has_no_outcome_tab():
    r = ReportResult(kind=InputKind.VCF, engines=("geneask",))
    r.findings, r.notes, r.scan_stats = [_brca2()], [], {}
    h = report_html(r)
    assert "href='#view=outcome'" not in h

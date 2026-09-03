from biocore.providers.base import Category, Finding, Interpretation, Tier
from geneask.annotators import clingen

from dnareport.action_plan import build_actions
from dnareport.detect import InputKind
from dnareport.orchestrate import ReportResult


def _finding(marker, source, detail, *, promoted=False, condition=None, pmids=None):
    finding = Finding(
        marker=marker,
        source=source,
        description="row",
        tier=Tier.ROBUST,
        categories=[Category.CLINICAL],
        detail=detail,
        pmids=pmids or [],
    )
    finding.promoted = promoted
    if condition:
        finding.interpretation = Interpretation(
            found="A finding.",
            can_mean="A meaning.",
            how_sure="A source.",
            next_step="A next step.",
            condition=condition,
        )
    return finding


def _result(*findings):
    return ReportResult(kind=InputKind.VCF, engines=("geneask",), findings=list(findings))


def test_clingen_intervention_becomes_a_cited_action(monkeypatch):
    finding = _finding(
        "13-1-A-G",
        "clinvar_mirror",
        {"gene": "OTHER"},
        promoted=True,
        condition="A condition",
    )
    monkeypatch.setattr(
        clingen,
        "actionability_for",
        lambda gene: {
            "intervention": "Annual imaging surveillance",
            "outcome": "Earlier tumour detection",
            "report_url": "https://clinicalgenome.org/actionability/other",
        },
    )

    actions = build_actions(_result(finding))

    action, = actions
    assert action.text == "Annual imaging surveillance"
    assert action.why == "Earlier tumour detection"
    assert action.source_label == "ClinGen actionability"
    assert action.url


def test_cpic_recommendation_becomes_an_action():
    finding = _finding(
        "CYP2C19",
        "cpic",
        {"drug": "clopidogrel", "recommendation": "Consider another antiplatelet"},
    )

    action, = build_actions(_result(finding))

    assert action.text == "Consider another antiplatelet"
    assert action.source_label == "CPIC"
    assert action.outcome_key == "medicine:clopidogrel"


def test_acmg_gene_becomes_an_action(monkeypatch):
    finding = _finding(
        "13-1-A-G",
        "clinvar",
        {"gene": "BRCA2"},
        promoted=True,
        condition="Hereditary breast and ovarian cancer syndrome",
    )
    monkeypatch.setattr(clingen, "actionability_for", lambda gene: None)

    actions = build_actions(_result(finding))

    assert any("genetics professional can advise on surveillance" in action.text for action in actions)
    assert any(action.source_label.startswith("ACMG SF") for action in actions)


def test_current_smoker_reference_becomes_an_action():
    finding = _finding(
        "cg05575921",
        "marker_reference",
        {"nearest_group": "current smoker", "groups": [{"group": "current smoker"}]},
        pmids=["23691101"],
    )

    action, = build_actions(_result(finding))

    assert action.text.startswith("Stopping smoking is the intervention")
    assert action.url.endswith("/23691101/")
    assert action.outcome_key == "trait:smoking"


def test_ewas_rows_never_create_actions(monkeypatch):
    monkeypatch.setattr(
        clingen,
        "actionability_for",
        lambda gene: (_ for _ in ()).throw(AssertionError("EWAS must not query ClinGen")),
    )
    ewas = _finding(
        "cg1",
        "ewas_catalog",
        {"trait": "Physical activity", "copy_key": "physical_activity"},
    )

    assert build_actions(_result(ewas)) == []


def test_every_action_has_a_source_label_or_url(monkeypatch):
    monkeypatch.setattr(clingen, "actionability_for", lambda gene: None)
    cpic = _finding("CYP2C19", "cpic", {"drug": "clopidogrel", "recommendation": "Use another medicine"})
    acmg = _finding("13-1-A-G", "clinvar", {"gene": "BRCA2"}, promoted=True, condition="A condition")

    assert all(action.url or action.source_label for action in build_actions(_result(cpic, acmg)))

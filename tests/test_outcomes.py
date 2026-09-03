from types import SimpleNamespace

from biocore.providers.base import Category, Finding, Interpretation, Tier
from geneask.interpret.polygenic import CAVEAT, TraitScore

from dnareport.detect import InputKind
from dnareport.orchestrate import ReportResult
from dnareport.outcomes import build_outcomes


def _finding(marker, source, detail, *, promoted=False, condition=None):
    finding = Finding(
        marker=marker,
        source=source,
        description="row",
        tier=Tier.ROBUST,
        categories=[Category.CLINICAL],
        detail=detail,
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


def test_conditions_medicines_and_alias_traits_form_one_outcome_each():
    brca = _finding(
        "13-1-A-G",
        "clinvar_mirror",
        {"gene": "BRCA2"},
        promoted=True,
        condition="Hereditary breast and ovarian cancer syndrome",
    )
    cpic = _finding(
        "CYP2C19",
        "cpic",
        {"drug": "clopidogrel", "gene": "CYP2C19"},
    )
    ewas = _finding(
        "cg1",
        "ewas_catalog",
        {"trait": "Body mass index", "copy_key": "bmi"},
    )
    gwas = _finding(
        "rs1",
        "gwas_catalog",
        {"trait": "BMI", "efo": "EFO_0004340"},
    )
    result = ReportResult(
        kind=InputKind.VCF,
        engines=("geneask",),
        findings=[ewas, cpic, brca, gwas],
    )

    outcomes = build_outcomes(result)

    assert len([outcome for outcome in outcomes if outcome.kind == "condition"]) == 1
    assert len([outcome for outcome in outcomes if outcome.kind == "medicine"]) == 1
    bmi, = [outcome for outcome in outcomes if outcome.kind == "trait"]
    assert {finding.marker for finding in bmi.findings} == {"cg1", "rs1"}


def test_age_follows_promoted_conditions_and_precedes_medicines_and_traits():
    condition = _finding(
        "13-1-A-G",
        "clinvar",
        {"gene": "BRCA2"},
        promoted=True,
        condition="A condition",
    )
    medicine = _finding("CYP2C19", "cpic", {"drug": "clopidogrel"})
    trait = _finding("rs1", "gwas_catalog", {"trait": "BMI", "efo": "EFO_0004340"})
    clock = SimpleNamespace(valid=True, contributions=[("cg1", 1, 0.5, 0.5, 0.5)])
    result = ReportResult(
        kind=InputKind.VCF,
        engines=("geneask",),
        findings=[trait, medicine, condition],
        clocks=[clock],
    )

    outcomes = build_outcomes(result)

    assert [outcome.kind for outcome in outcomes] == [
        "condition",
        "age",
        "medicine",
        "trait",
    ]
    assert outcomes[1].contributions


def test_scored_traits_sort_before_reference_and_direction_only_traits():
    score = TraitScore(
        trait="BMI",
        efo="EFO_0004340",
        n_variants=3,
        n_with_af=3,
        score=1,
        mean=0,
        sd=1,
        z=1,
        percentile=80,
        direction_word="higher",
        caveat=CAVEAT,
    )
    bmi = _finding("rs1", "gwas_catalog", {"trait": "BMI", "efo": "EFO_0004340"})
    smoking = _finding(
        "cg05575921",
        "marker_reference",
        {"label": "Tobacco smoke exposure", "nearest_group": "never-smoker", "groups": [{"group": "never-smoker"}]},
    )
    age = _finding("cg2", "ewas_catalog", {"trait": "Chronological age", "copy_key": "age"})
    result = ReportResult(
        kind=InputKind.BETA_MATRIX,
        engines=("methylask",),
        findings=[age, smoking, bmi],
        trait_scores=[score],
    )

    outcomes = build_outcomes(result)

    traits = [outcome for outcome in outcomes if outcome.kind == "trait"]
    assert [outcome.key for outcome in traits] == ["trait:bmi", "trait:smoking", "trait:age"]
    assert traits[0].score is score
    assert traits[1].reference_groups == [{"group": "never-smoker"}]


def test_package_data_rule_covers_outcome_aliases():
    pyproject = (__import__("pathlib").Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert '"data/*.json"' in pyproject

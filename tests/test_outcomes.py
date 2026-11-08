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


def _cited(marker, detail, pmid, source="ewas_catalog"):
    finding = _finding(marker, source, detail)
    finding.pmids = [pmid]
    return finding


def _trait_outcomes(findings, **kwargs):
    result = ReportResult(kind=InputKind.VCF, engines=("methylask",), findings=findings, **kwargs)
    return [outcome for outcome in build_outcomes(result) if outcome.kind == "trait"]


def test_affymetrix_expression_readouts_share_one_card_and_keep_their_own_trait():
    # HG002 2026-11-08 render: 715 one-finding cards titled "Gene expression of
    # Affymetrix ID: ..." (all EWAS Catalog PMID 33752734)
    rows = [("cg1", "Gene expression of Affymetrix ID: 2316245 (gene symbol: PRKCZ)"),
            ("cg2", "Gene expression of Affymetrix ID: 2318086 (gene symbol: KCNAB2)")]
    card, = _trait_outcomes([_cited(m, {"trait": t}, "33752734") for m, t in rows])
    assert card.key == "trait:gene_expression_affymetrix"
    assert card.label == "Gene expression associations (Affymetrix array)"
    assert [f.detail["trait"] for f in card.findings] == [t for _m, t in rows]


def test_nmr_codes_from_the_curated_study_share_one_card():
    rows = [("cg1", {"trait": "M-VLDL-PL"}), ("cg2", {"trait": "S-VLDL-TG_pct"}),
            ("cg3", {"trait": "IDL.TG_pct"}), ("cg4", {"trait": "FAw3", "efo": "EFO_0010119"})]
    card, = _trait_outcomes([_cited(m, d, "33413638") for m, d in rows])
    assert card.key == "trait:nmr_metabolites"
    assert len(card.findings) == 4


def test_the_same_code_from_another_study_is_not_grouped():
    # "DHA" or "Ile" alone does not establish an NMR measurement
    outcomes = _trait_outcomes([_cited("cg1", {"trait": "DHA"}, "11111111"),
                                _cited("cg2", {"trait": "DHA"}, "33413638")])
    assert sorted(o.key for o in outcomes) == ["trait:dha", "trait:nmr_metabolites"]


def test_group_match_is_literal_not_case_folded_or_fuzzy():
    outcomes = _trait_outcomes([_cited("cg1", {"trait": "m-vldl-pl"}, "33413638"),
                                _cited("cg2", {"trait": "Gene expression"}, "33752734")])
    assert sorted(o.key for o in outcomes) == ["trait:gene-expression", "trait:m-vldl-pl"]


def test_the_two_hepatic_fat_studies_share_a_liver_fat_card():
    # "hepatic fat" (EFO_0010821, PMID 30796027) and "Hepatic Fat"
    # (EFO_0006506, EFO_0008421, PMID 30936141) were two cards on 2026-11-08
    a = _cited("cg1", {"trait": "Hepatic fat", "efo": "EFO_0010821"}, "30796027")
    b = _cited("cg2", {"trait": "Hepatic Fat", "efo": "EFO_0006506, EFO_0008421"}, "30936141")
    card, = _trait_outcomes([b, a])
    assert card.key == "trait:liver_fat" and card.label == "Liver fat"
    assert {f.marker for f in card.findings} == {"cg1", "cg2"}


def test_same_label_with_different_efo_keys_stays_separate_without_curation():
    # no case-folded or label-based merge: only a curated group joins two keys
    outcomes = _trait_outcomes([_cited("cg1", {"trait": "Foo", "efo": "EFO_1"}, "1"),
                                _cited("cg2", {"trait": "FOO", "efo": "EFO_2"}, "2")])
    assert len(outcomes) == 2


def test_a_group_never_absorbs_a_score_or_an_action():
    finding = _cited("cg1", {"trait": "FAw3", "efo": "EFO_0010119"}, "33413638")
    score = SimpleNamespace(efo="EFO_0010119", trait="FAw3")
    outcomes = _trait_outcomes([finding], trait_scores=[score])
    by_key = {o.key: o for o in outcomes}
    assert by_key["trait:nmr_metabolites"].score is None
    assert by_key["trait:efo-0010119"].score is score


def test_malformed_group_specs_are_ignored(tmp_path, monkeypatch):
    import dnareport.outcomes as mod
    path = tmp_path / "groups.json"
    for body in ("[]", '{"x": {"label": "X", "pmids": ["1"], "prefixes": [""]}}',
                 '{"x": {"label": "X", "prefixes": ["a"]}}'):
        path.write_text(body)
        monkeypatch.setattr(mod, "_GROUPS_FILE", path)
        assert mod._load_groups() == []

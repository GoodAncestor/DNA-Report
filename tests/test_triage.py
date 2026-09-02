"""What earns "Read this first" is decided by published lists, and each says why."""
from biocore.providers.base import Finding, Tier, Category
from geneask.annotators import clingen
from dnareport.triage import (
    promote,
    REASON_A,
    REASON_APRIME,
    REASON_B,
    REASON_C,
    REASON_D,
)


def _cv(gene, sig, stars, marker="m", source="clinvar_mirror"):
    return Finding(marker=marker, source=source, description="x", tier=Tier.ROBUST,
                   categories=[Category.CLINICAL],
                   detail={"gene": gene, "clinical_significance": sig, "gold_stars": stars})


def _gwas(carried, p, et, ev, trait="Type 2 diabetes", marker="rs1"):
    return Finding(marker=marker, source="gwas_catalog", description="x", tier=Tier.ROBUST,
                   categories=[Category.TRAIT],
                   detail={"risk_allele_carried": carried, "p": p, "effect_type": et,
                           "effect": ev, "trait": trait})


def test_rule_a_acmg_gene_plp_even_with_one_star():
    f = _cv("BRCA2", "Likely pathogenic", 1)
    assert promote([f]) == [f]
    assert f.promoted and f.promoted_reason == REASON_A.format(list="ACMG SF v3.2")


def test_rule_b_two_stars_non_acmg_and_panel_source():
    f = _cv("MTHFR", "Pathogenic", 2, source="clinvar_panel_157")
    promote([f]); assert f.promoted and f.promoted_reason == REASON_B
    g = _cv("MTHFR", "Pathogenic", 1)
    promote([g]); assert not g.promoted and g.promoted_reason == ""


def test_rule_a_prime_uses_clingen_actionability_between_a_and_b(monkeypatch):
    monkeypatch.setattr(
        clingen,
        "actionability_for",
        lambda gene: {"score": 9} if gene == "RARE1" else None,
    )
    actionable = _cv("RARE1", "Pathogenic", 1, marker="actionable")
    two_stars = _cv("MTHFR", "Pathogenic", 2, marker="two-stars")
    acmg = _cv("BRCA2", "Pathogenic", 1, marker="acmg")

    out = promote([two_stars, actionable, acmg])

    assert actionable.promoted_reason == REASON_APRIME
    assert [finding.marker for finding in out] == ["acmg", "actionable", "two-stars"]


def test_rule_a_prime_needs_a_recorded_score_of_nine(monkeypatch):
    monkeypatch.setattr(clingen, "actionability_for", lambda gene: {"score": None})
    finding = _cv("RARE1", "Pathogenic", 1)
    promote([finding])
    assert not finding.promoted


def test_rule_c_cpic_level_a():
    f = Finding(marker="CYP2C19", source="cpic", description="x", tier=Tier.ROBUST,
                categories=[Category.CLINICAL],
                detail={"gene": "CYP2C19", "cpic_level": "A", "drug": "clopidogrel"})
    promote([f]); assert f.promoted_reason == REASON_C
    g = Finding(marker="X", source="cpic", description="x", tier=Tier.MODERATE,
                categories=[Category.CLINICAL], detail={"gene": "X", "cpic_level": "B"})
    promote([g]); assert not g.promoted


def test_rule_d_large_or_only_when_carried_and_significant():
    f = _gwas(True, 1e-9, "or", 1.6); promote([f])
    assert f.promoted_reason == REASON_D.format(trait="Type 2 diabetes")
    prot = _gwas(True, 1e-9, "or", 0.5); promote([prot]); assert prot.promoted
    for bad in (_gwas(False, 1e-9, "or", 1.6), _gwas(None, 1e-9, "or", 1.6),
                _gwas(True, 1e-6, "or", 1.6), _gwas(True, 1e-9, "or", 1.2),
                _gwas(True, 1e-9, "beta", 9.9), _gwas(True, 1e-9, None, None),
                _gwas(True, "bad", "or", 1.6)):
        promote([bad]); assert not bad.promoted


def test_uncertain_and_benign_never_promote():
    for sig in ("Uncertain significance", "Conflicting classifications of pathogenicity",
                "Benign", "Benign; drug response"):
        f = _cv("BRCA2", sig, 4); promote([f]); assert not f.promoted


def test_order_is_rule_then_stars_then_effect_then_marker():
    vus = _cv("BRCA2", "Uncertain significance", 2, marker="v")
    b3 = _cv("MTHFR", "Pathogenic", 3, marker="b3")
    b2 = _cv("CFTR", "Pathogenic", 2, marker="b2")
    a = _cv("LDLR", "Pathogenic", 1, marker="a")
    d_big = _gwas(True, 1e-9, "or", 2.0, marker="rs9")
    d_small = _gwas(True, 1e-9, "or", 1.6, marker="rs1")
    out = promote([vus, d_small, b2, d_big, b3, a])
    assert not vus.promoted
    assert [f.marker for f in out] == ["a", "b3", "b2", "rs9", "rs1"]

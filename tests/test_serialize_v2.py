import json

from biocore.providers.base import (
    Category, ChainLink, Finding, Health, Interpretation, ProviderStatus, Tier,
)
from dnareport.detect import InputKind
from dnareport.action_plan import Action
from dnareport.outcomes import Outcome
from dnareport.orchestrate import ReportResult
from dnareport.serialize import SCHEMA_VERSION, result_to_json
from geneask.interpret.polygenic import CAVEAT, TraitScore


def _brca2():
    return Finding(
        marker="13-32316419-CAG-C", source="clinvar_mirror",
        description="BRCA2: Likely pathogenic (genotype C/CAG, WGS)",
        tier=Tier.ROBUST, categories=[Category.CLINICAL],
        detail={
            "gene": "BRCA2", "topic": "cancer", "modality": "genome",
            "conditions": ["Hereditary breast ovarian cancer syndrome"],
            "condition_ids": ["MedGen:C0677776"], "zygosity": "het",
            "gold_stars": 2,
            "review_status": "criteria provided, multiple submitters, no conflicts",
            "clinical_significance": "Likely pathogenic", "platform": "WGS",
        },
        interpretation=Interpretation(
            found="f", can_mean="m", how_sure="s", next_step="n",
            condition="Hereditary breast and ovarian cancer syndrome",
            condition_ids=["MedGen:C0677776"], zygosity="het",
        ),
        evidence_chain=[ChainLink(kind="gene", label="BRCA2")],
        promoted=True,
        promoted_reason="Several labs agree this change is pathogenic",
    )


def test_json_is_lossless_and_versioned():
    result = ReportResult(kind=InputKind.VCF, engines=("geneask",))
    result.findings = [_brca2()]
    result.provider_status = [
        ProviderStatus(name="clinvar_mirror", health=Health.OK, version="schema 2")
    ]
    doc = result_to_json(result)
    assert SCHEMA_VERSION == "2.1" and doc["schema_version"] == "2.1"
    finding = doc["findings"][0]
    assert finding["detail"]["conditions"] == [
        "Hereditary breast ovarian cancer syndrome"
    ]
    assert finding["detail"]["zygosity"] == "het"
    assert finding["detail"]["gold_stars"] == 2
    assert finding["interpretation"]["next_step"] == "n"
    assert finding["evidence_chain"][0]["label"] == "BRCA2"
    assert finding["promoted"] is True
    assert finding["magnitude"] is not None and finding["topic"] == "cancer"
    assert "links" in finding
    assert doc["important"][0]["marker"] == "13-32316419-CAG-C"
    assert doc["provider_status"][0]["health"] == "ok"
    json.dumps(doc)


def test_json_carries_person_scores_outcomes_and_actions():
    result = ReportResult(
        kind=InputKind.VCF,
        engines=("geneask",),
        age=48,
        age_source="guess",
        sex="female",
        sex_source="guess",
    )
    score = TraitScore(
        trait="BMI",
        efo="EFO_0004340",
        n_variants=3,
        n_with_af=3,
        score=1.2,
        mean=0.4,
        sd=0.5,
        z=1.6,
        percentile=90,
        direction_word="higher",
        top=[("rs1", "GENE1", 0.5)],
        caveat=CAVEAT,
    )
    action = Action(
        text="Consider another medicine.",
        why="CPIC publishes guidance.",
        source_label="CPIC",
        url="https://cpicpgx.org/",
        outcome_key="medicine:medicine",
    )
    result.trait_scores = [score]
    result.outcomes = [Outcome("trait:bmi", "BMI", "trait", [], score, [], [], [])]
    result.actions = [action]

    doc = result_to_json(result)

    assert doc["person"] == {
        "age": 48,
        "age_source": "guess",
        "sex": "female",
        "sex_source": "guess",
    }
    assert doc["trait_scores"][0]["percentile"] == 90
    assert doc["trait_scores"][0]["caveat"] == CAVEAT
    assert doc["outcomes"][0]["finding_markers"] == []
    assert doc["outcomes"][0]["score"]["trait"] == "BMI"
    assert doc["actions"][0]["source_label"] == "CPIC"

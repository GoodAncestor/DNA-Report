from biocore.providers.base import Category, Finding, Tier
from dnareport.detect import InputKind
from dnareport.action_plan import Action
from dnareport.exports import report_markdown
from dnareport.outcomes import Outcome
from dnareport.orchestrate import ReportResult
from geneask.interpret.polygenic import CAVEAT, TraitScore
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


def test_person_actions_and_outcomes_reach_markdown():
    result = _result([_brca2()])
    result.age = 48
    result.age_source = "guess"
    result.sex = "female"
    result.sex_source = "user"
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
    result.trait_scores = [score]
    result.outcomes = [Outcome("trait:bmi", "BMI", "trait", score=score)]
    result.actions = [
        Action(
            text="Consider another medicine.",
            why="CPIC publishes guidance.",
            source_label="CPIC",
            url="https://cpicpgx.org/",
            outcome_key=None,
        )
    ]

    markdown = report_markdown(result)

    assert "Your age and sex: Age about 48 (estimated from the clocks) · Sex female (your entry)" in markdown
    assert markdown.index("## What people do with results like these") > markdown.index("## Read this first")
    assert "Consider another medicine." in markdown
    assert "## By outcome" in markdown
    assert "higher than about 80% of people in the reference set" in markdown
    assert CAVEAT in markdown


def test_lower_polygenic_position_uses_the_complement_percentile():
    score = TraitScore(
        trait="BMI",
        efo="EFO_0004340",
        n_variants=3,
        n_with_af=3,
        score=-1,
        mean=0,
        sd=1,
        z=-1,
        percentile=20,
        direction_word="lower",
        caveat=CAVEAT,
    )
    result = _result([])
    result.outcomes = [Outcome("trait:bmi", "BMI", "trait", score=score)]

    markdown = report_markdown(result)

    assert "lower than about 80% of people in the reference set" in markdown


def test_person_line_does_not_invent_an_unknown_source():
    result = _result([])
    result.age = 48
    result.sex = "female"

    markdown = report_markdown(result)

    assert "Your age and sex: Age 48 · Sex female" in markdown
    assert "your entry" not in markdown
    assert "estimated" not in markdown

import json

from biocore.providers.base import Category, ChainLink, Finding, Interpretation, Tier

from dnareport.explain import PROMPT_VERSION, build_prompt, cache_key, facts_for


def _f(genotype="C/CAG", zyg="het"):
    ip = Interpretation(
        found=(
            "BRCA2 makes a protein that helps cells repair broken DNA. ClinVar "
            "classifies this change as likely pathogenic for Hereditary breast and "
            "ovarian cancer syndrome."
        ),
        can_mean=(
            "One altered copy was read. One altered copy is enough to raise the chance "
            "of the condition."
        ),
        how_sure="ClinVar rates the classification 2 of 4 review stars.",
        next_step="Confirm the result with a clinical laboratory test before you act on it.",
        condition="Hereditary breast and ovarian cancer syndrome",
        condition_ids=["MedGen:C0677776"],
        zygosity=zyg,
    )
    return Finding(
        marker="13-32316419-CAG-C",
        source="clinvar_mirror",
        description="x",
        tier=Tier.ROBUST,
        categories=[Category.CLINICAL],
        detail={
            "gene": "BRCA2",
            "genotype": genotype,
            "zygosity": zyg,
            "platform": "WGS",
            "clinical_significance": "Likely pathogenic",
            "gold_stars": 2,
            "review_status": "criteria provided, multiple submitters, no conflicts",
            "molecular_consequence": "splice_acceptor_variant",
            "gnomad": {"af": 3.2e-6, "ac": 5, "an": 1560000, "version": "v4.1"},
            "qual": 812.5,
            "gq": 99,
            "dp": 41,
        },
        interpretation=ip,
        promoted=True,
        promoted_reason="Clinicians are told to report changes in this gene (ACMG SF v3.2)",
        evidence_chain=[
            ChainLink(kind="gene", label="BRCA2", url="https://x/g"),
            ChainLink(
                kind="condition",
                label="HBOC",
                id="MedGen:C0677776",
                url="https://x/c",
            ),
        ],
    )


def test_facts_carry_public_facts_and_nothing_personal():
    facts = facts_for(_f())
    json.dumps(facts)
    assert facts["zygosity_class"] == "one altered copy"
    assert facts["condition"] == "Hereditary breast and ovarian cancer syndrome"
    assert facts["frequency"] == {"ac": 5, "an": 1560000, "version": "v4.1"}
    assert facts["chain"][1]["id"] == "MedGen:C0677776"
    serialized = json.dumps(facts)
    for private in ("C/CAG", "812.5", '"gq"', '"dp"', "genotype"):
        assert private not in serialized


def test_cache_key_ignores_genotype_and_tracks_prompt_version():
    a = cache_key(facts_for(_f(genotype="C/CAG")), "openai_compat", "GLM-5.3-MLX-4bit")
    b = cache_key(facts_for(_f(genotype="CAG/C")), "openai_compat", "GLM-5.3-MLX-4bit")
    c = cache_key(facts_for(_f(zyg="hom")), "openai_compat", "GLM-5.3-MLX-4bit")
    d = cache_key(facts_for(_f()), "codex_cli", "gpt-5.6-sol")
    assert a == b and a != c and a != d and len(a) == 64
    assert PROMPT_VERSION in json.dumps(build_prompt(facts_for(_f())))


def test_prompt_is_facts_locked_and_plain():
    system, user = build_prompt(facts_for(_f()))
    assert "160 words" in system and "only the facts" in system.lower()
    assert "BRCA2" in user and "MedGen:C0677776" in user
    assert "diagnos" not in system.lower().replace("no diagnosis", "")
    for banned in ("C/CAG", "genotype"):
        assert banned not in user


def test_extract_dive_takes_the_marked_answer_and_prompt_lists_labels():
    from dnareport.explain import extract_dive
    raw = "Let me plan. Labels: [BRCA2].\n<dive>The change sits in BRCA2 [BRCA2].</dive>"
    assert extract_dive(raw) == "The change sits in BRCA2 [BRCA2]."
    assert extract_dive("plain text [BRCA2]") == "plain text [BRCA2]"
    assert extract_dive("") == ""
    system, user = build_prompt(facts_for(_f()))
    assert "<dive>" in system and "allowed_citation_labels" in user and "\"BRCA2\"" in user

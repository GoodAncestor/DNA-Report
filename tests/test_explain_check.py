from dnareport.explain import check_draft


FACTS = {
    "chain": [
        {"kind": "gene", "label": "BRCA2"},
        {"kind": "assertion", "label": "ClinVar record"},
    ]
}
GOOD = (
    "The change sits in BRCA2 [BRCA2]. BRCA2 helps cells repair DNA. ClinVar "
    "classifies the change as likely pathogenic [ClinVar record]. Two laboratories "
    "agree. Confirm the result with a clinical test."
)


def test_good_draft_passes():
    assert check_draft(GOOD, FACTS) is None


def test_rejections():
    assert check_draft("", FACTS) == "empty"
    assert check_draft("word " * 200 + "[BRCA2]", FACTS) == "too long"
    assert check_draft(GOOD + " You will develop cancer.", FACTS).startswith(
        "forbidden phrase"
    )
    assert check_draft(GOOD + " This is a diagnosis.", FACTS).startswith(
        "forbidden phrase"
    )
    assert check_draft(GOOD + " Importantly, it's not X, it's Y.", FACTS).startswith(
        "banned construction"
    )
    assert check_draft(GOOD + " See [PubMed 999].", FACTS).startswith(
        "unknown citation"
    )
    assert check_draft("The change sits in BRCA2. Confirm it.", FACTS) == "no citation"


def test_banned_list_is_bio_cores():
    """explain.py holds no private copy: it uses bio-core's one validator list."""
    from biocore.copy import check
    from dnareport import explain

    assert explain._BANNED is check.BANNED
    assert explain._FORBIDDEN is check.FORBIDDEN


def test_draft_rejected_by_check_py_for_a_banned_term():
    from biocore.copy import check

    draft = GOOD + " Crucially, two laboratories agree."
    assert check_draft(draft, FACTS) == "banned construction: Crucially"
    problems = check.check_field("terms", "x", "sentence", "Crucially, two laboratories agree.")
    assert any(p.severity == "error" for p in problems)


def test_not_a_capital_is_case_sensitive():
    # bio-core keeps `not a [A-Z]` case-sensitive: "not a measurement" passes.
    assert check_draft(GOOD + " This is not a measurement.", FACTS) is None
    assert check_draft(GOOD + " This is not a Measurement.", FACTS).startswith(
        "banned construction"
    )

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

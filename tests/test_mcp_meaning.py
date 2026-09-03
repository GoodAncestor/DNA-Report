from dnareport.mcp_server import (
    get_actions,
    get_outcomes,
    important_findings,
    one_finding,
    query_findings,
)


_ID = "c" * 32
_DOC = {
    "schema_version": "2.0",
    "findings": [
        {
            "marker": "13-32316419-CAG-C", "tier": "robust", "gene": "BRCA2",
            "topic": "cancer", "magnitude": 8.5, "direction": "adverse",
            "promoted": True,
            "promoted_reason": "Several labs agree this change is pathogenic",
            "detail": {
                "clinical_significance": "Likely pathogenic",
                "conditions": ["HBOC"],
            },
            "interpretation": {"next_step": "Confirm."}, "evidence_chain": [],
        },
        {
            "marker": "rs7903146", "tier": "robust", "gene": "TCF7L2",
            "topic": "other", "magnitude": 6.1, "direction": "",
            "promoted": False, "promoted_reason": "",
            "detail": {"trait": "Type 2 diabetes", "risk_allele_carried": True},
            "interpretation": None, "evidence_chain": [],
        },
    ],
    "important": [], "summary": {}, "scan_stats": {},
    "outcomes": [{"key": "condition:hboc", "label": "HBOC", "kind": "condition"}],
    "actions": [{"text": "Confirm the result.", "source_label": "ACMG"}],
}
_DOC["important"] = [_DOC["findings"][0]]


def _read(_report_id):
    return {"status": "ready", "report": _DOC}


def test_important_returns_promoted_with_reason_and_disclaimer():
    result = important_findings(_read, _ID)
    assert [finding["marker"] for finding in result["findings"]] == [
        "13-32316419-CAG-C"
    ]
    assert "pathogenic" in result["findings"][0]["promoted_reason"]
    assert "diagnosis" in result["disclaimer"]


def test_filters_by_condition_classification_and_carried():
    assert [finding["marker"] for finding in query_findings(
        _read, _ID, condition="hboc"
    )["findings"]] == ["13-32316419-CAG-C"]
    assert [finding["marker"] for finding in query_findings(
        _read, _ID, classification="likely pathogenic"
    )["findings"]] == ["13-32316419-CAG-C"]
    assert [finding["marker"] for finding in query_findings(
        _read, _ID, risk_allele_carried=True
    )["findings"]] == ["rs7903146"]


def test_one_finding_by_marker():
    result = one_finding(_read, _ID, "rs7903146")
    assert result["finding"]["gene"] == "TCF7L2"
    assert "disclaimer" in result
    assert one_finding(_read, _ID, "rs0")["error"].startswith("no finding")


def test_outcomes_and_actions_have_dedicated_tools():
    outcomes = get_outcomes(_read, _ID)
    actions = get_actions(_read, _ID)

    assert outcomes["outcomes"][0]["key"] == "condition:hboc"
    assert actions["actions"][0]["source_label"] == "ACMG"
    assert "disclaimer" in outcomes and "disclaimer" in actions

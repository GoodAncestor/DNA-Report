from pathlib import Path

import pytest

from methylask import aggregate
from dnareport import orchestrate
from dnareport.orchestrate import analyze


def test_beta_matrix_demo_aggregates_ewas_rows():
    demo = (
        Path(__file__).resolve().parents[1]
        / "dnareport"
        / "demo_data"
        / "demo_blood_wholeblood.csv"
    )
    result = analyze(str(demo), tissue="blood")
    ewas = [finding for finding in result.findings if finding.source == "ewas_catalog"]
    if not any(finding.detail.get("n_studies", 0) > 1 for finding in ewas):
        pytest.skip("The local EWAS provider has no repeated demo rows.")
    keys = {(finding.marker, finding.detail.get("trait")) for finding in ewas}
    assert len(keys) == len(ewas)


def test_analyze_aggregates_before_interpretation(monkeypatch):
    demo = (
        Path(__file__).resolve().parents[1]
        / "dnareport"
        / "demo_data"
        / "demo_blood_wholeblood.csv"
    )
    events = []

    def aggregate_findings(findings, sample_tissue):
        events.append(("aggregate", sample_tissue))
        return findings

    monkeypatch.setattr(aggregate, "aggregate_by_trait", aggregate_findings)
    monkeypatch.setattr(
        orchestrate,
        "_interpret_and_promote",
        lambda result: events.append(("interpret", result.tissue)),
    )

    analyze(str(demo), tissue="blood")

    assert events[-2:] == [("aggregate", "blood"), ("interpret", "blood")]

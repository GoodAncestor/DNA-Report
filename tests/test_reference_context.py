"""Published reference context on methylome findings.

MethylAsk positions a sample against curated, PMID-sourced population values.
This wires that into the report and pins the tissue gate: a reference measured
in a different tissue is reported as unavailable, never as a comparison.
"""
import csv
from pathlib import Path
import pytest

from dnareport.orchestrate import _reference_findings

DEMO = Path(__file__).parent.parent / "dnareport" / "demo_data"


def _betas(fname):
    out = {}
    with open(DEMO / fname) as fh:
        r = csv.reader(fh)
        next(r)
        for row in r:
            if row:
                out[row[0]] = float(row[1])
    return out


def test_blood_sample_gets_cited_reference_findings():
    fs = _reference_findings(_betas("demo_blood_wholeblood.csv"), tissue="blood")
    assert fs, "expected reference findings for the blood demo"
    smoking = [f for f in fs if f.marker == "cg05575921"]
    assert len(smoking) == 1
    assert "23691101" in smoking[0].pmids
    assert "never-smoker" in smoking[0].description


def test_reference_finding_names_the_statistic_not_average():
    fs = _reference_findings(_betas("demo_blood_wholeblood.csv"), tissue="blood")
    smoking = [f for f in fs if f.marker == "cg05575921"][0]
    # the source publishes medians; calling them averages misdescribes it
    assert "median" in smoking.description
    assert "average" not in smoking.description.lower()


def test_buccal_sample_reports_reference_as_unavailable_not_compared():
    from biocore.providers.base import Tier
    fs = _reference_findings(_betas("demo_buccal_pediatric.csv"), tissue="buccal")
    smoking = [f for f in fs if f.marker == "cg05575921"]
    assert len(smoking) == 1
    f = smoking[0]
    assert f.tier is Tier.UNKNOWN
    # must not contain a comparison to any smoking group
    assert "current smoker" not in f.description
    assert "never-smoker" not in f.description
    assert "buccal" in f.description and "whole blood" in f.description

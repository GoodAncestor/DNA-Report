"""Markdown and JSON exports of a finished report.

Markdown is for a person keeping or forwarding their results outside a browser.
JSON is for someone else's software, including agents — which is why it has to
carry the same qualifications the page does. A machine reader that cannot see
that a report was truncated will report 1,000 GWAS associations as though they
were all 442,719 of them, which is the same failure as a human reader who never
sees the notice, in a form nobody will catch.
"""
import json
import types

import pytest

from dnareport.detect import InputKind
from dnareport.exports import report_markdown, report_json
from dnareport.orchestrate import ReportResult


class _Tier(str):
    @property
    def value(self):
        return str(self)


def _finding(marker="rs1801133", desc="MTHFR variant", tier="robust",
             detail=None, pmids=()):
    return types.SimpleNamespace(
        marker=marker, description=desc, tier=_Tier(tier),
        detail=detail or {"gene": "MTHFR", "p": 1e-8, "topic": "clinical"},
        source="clinvar", pmids=list(pmids), link=None,
        categories=[types.SimpleNamespace(value="clinical")])


def _result(findings=None, notes=None, stats=None):
    r = ReportResult(kind=InputKind.VCF, engines=("geneask",))
    r.findings = findings if findings is not None else [_finding()]
    r.notes = notes or []
    r.scan_stats = stats or {}
    return r


def test_markdown_carries_the_findings():
    md = report_markdown(_result(), filename="genome.vcf")
    assert "# DNA-Report" in md
    assert "rs1801133" in md
    assert "MTHFR variant" in md
    assert "genome.vcf" in md
    assert "**MTHFR**" in md          # gene leads the line, not the identifier


def test_a_long_indel_identifier_is_kept_whole():
    """An indel's marker id is the whole allele — 330 characters on the demo
    genome. It is ugly, and it is still the thing someone greps for, so it is
    formatted out of the way rather than shortened."""
    long_id = "2-47403171-" + "AGG" * 100 + "-A"
    md = report_markdown(_result(findings=[_finding(marker=long_id)]))
    assert long_id in md
    assert f"`{long_id}`" in md      # in a code span, not bolded at line head


def test_markdown_puts_the_limits_before_the_results():
    """A cap changes what the list below it means. A reader scrolling a long
    document would meet it last, or never."""
    md = report_markdown(_result(
        notes=["GWAS Catalog: showing the 1000 most significant of 442719 found"]))

    assert "442719" in md
    assert md.index("How to read this") < md.index("## Findings")


def test_markdown_says_so_when_there_is_nothing():
    md = report_markdown(_result(findings=[]))
    assert "## Findings" in md
    assert "not a clean bill of health" in md


def test_markdown_carries_the_disclaimer(tmp_path):
    """An exported file is the copy most likely to be forwarded to someone who
    never saw the page it came from."""
    d = tmp_path / "D.md"
    d.write_text("This is not medical advice.")
    md = report_markdown(_result(), disclaimer_path=str(d))
    assert "This is not medical advice." in md


def test_markdown_survives_a_missing_disclaimer(tmp_path):
    md = report_markdown(_result(), disclaimer_path=str(tmp_path / "nope.md"))
    assert "rs1801133" in md          # the report is still produced


def test_json_is_parseable_and_versioned():
    doc = json.loads(report_json(_result()))
    assert doc["schema_version"]
    assert doc["summary"]["n_findings"] == 1
    assert doc["findings"][0]["marker"] == "rs1801133"


def test_json_states_what_was_scanned_and_what_was_left_out():
    """The whole point of the agent-facing view: a consumer must be able to tell a
    bounded report from a complete one without parsing prose."""
    doc = json.loads(report_json(_result(
        notes=["GWAS Catalog: showing the 1000 most significant of 442719 found"],
        stats={"markers_scanned": 650000, "local_dbs_queried": ["clinvar", "gwas_catalog"]})))

    assert doc["scan_stats"]["markers_scanned"] == 650000
    assert "clinvar" in doc["scan_stats"]["local_dbs_queried"]
    assert any("442719" in n for n in doc["notes"])


def test_an_empty_result_still_serialises():
    doc = json.loads(report_json(_result(findings=[])))
    assert doc["summary"]["n_findings"] == 0
    assert doc["findings"] == []


# ------------------------------------------------------- summary + front matter
def test_front_matter_states_the_shape_without_reading_the_body():
    """Prose headings are not a contract; these keys are. An agent must be able to
    learn what this report is, and whether it is complete, without parsing text."""
    md = report_markdown(_result())
    head = md.split("---")[1]
    assert "format: dna-report-markdown/" in head
    assert "input_kind: vcf" in head
    assert "findings: 1" in head
    assert "bounded: false" in head


def test_a_bounded_report_says_so_in_the_front_matter_and_the_body():
    md = report_markdown(_result(
        stats={"limits": {"gwas_catalog": {"shown": 1000, "found": 442719}}}))

    head = md.split("---")[1]
    assert "bounded: true" in head
    assert "shown: 1000" in head and "found: 442719" in head
    assert "This report is bounded" in md
    assert "1,000 of 442,719" in md


def test_the_summary_precedes_everything_it_qualifies():
    md = report_markdown(_result())
    assert md.index("## Summary") < md.index("## Findings")


def test_the_ranking_does_not_claim_clinical_priority():
    """Ranking by evidence is defensible; ranking by importance-to-you is a
    clinical judgement this system is not entitled to make."""
    md = report_markdown(_result())
    assert "Strongest evidence" in md
    assert "not a clinical priority order" in md


def test_the_summary_is_the_same_object_the_json_carries():
    """One summary. Two would be two things that can disagree about one genome."""
    from dnareport.serialize import report_summary
    r = _result(stats={"limits": {"gwas_catalog": {"shown": 5, "found": 90}}})
    sm = report_summary(r)
    doc = json.loads(report_json(r))

    assert doc["summary"]["bounded"] is True
    assert doc["summary"]["limits"] == sm["limits"]
    assert "summary_v2" not in doc          # never two summary blocks

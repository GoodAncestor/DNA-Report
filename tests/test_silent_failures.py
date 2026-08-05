"""A scan that could not look must not report that it found nothing.

Every one of these was a bare `except: pass` that turned a file we failed to read
into a report with zero findings and no stated reason. For a consumer genotype
export — the most common upload there is — that produced an empty report nobody
could argue with, because it cited nothing.
"""
import pytest

pytest.importorskip("geneask")

from dnareport.detect import InputKind
from dnareport.orchestrate import _run_geneask


def test_an_unparseable_genotype_export_says_so(monkeypatch):
    import geneask.parsers as parsers

    def boom(path):
        raise ValueError("column 4 is not a genotype")
    monkeypatch.setattr(parsers, "parse_file", boom)

    findings, notes = _run_geneask("/tmp/whatever.txt",
                                   InputKind.ARRAY_GENOTYPE)

    assert findings == []
    assert any("could not be parsed" in n for n in notes), notes
    assert any("column 4 is not a genotype" in n for n in notes), notes


def test_an_export_with_no_genotype_rows_says_so(monkeypatch):
    import geneask.parsers as parsers

    class Empty:
        records = []
        notes = []
    monkeypatch.setattr(parsers, "parse_file", lambda path: Empty())

    findings, notes = _run_geneask("/tmp/whatever.txt", InputKind.ARRAY_GENOTYPE)

    assert findings == []
    assert any("no genotype rows" in n for n in notes), notes


def test_a_readable_export_gains_no_apology(monkeypatch):
    """The notes are for real problems — a working file must not collect one."""
    import geneask.parsers as parsers

    class Rec:
        rsid, allele1, allele2, is_nocall = "rs1234", "A", "G", False
    class Parsed:
        records = [Rec()]
        notes = []
    monkeypatch.setattr(parsers, "parse_file", lambda path: Parsed())

    _findings, notes = _run_geneask("/tmp/whatever.txt", InputKind.ARRAY_GENOTYPE)

    assert not any("could not be parsed" in n for n in notes), notes
    assert not any("no genotype rows" in n for n in notes), notes

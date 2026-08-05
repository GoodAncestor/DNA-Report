"""Analysis notes must reach the HTML report, not just the JSON API."""
import pytest
pytest.importorskip("fastapi")
from dnareport.scan_notes import notes_html


class _R:
    def __init__(self, notes):
        self.notes = notes


def test_no_notes_renders_nothing():
    assert notes_html(_R([])) == ""
    assert notes_html(_R(None)) == ""


def test_notes_appear_in_the_output():
    html = notes_html(_R(["AlphaMissense: added pathogenicity to 3 missense variants"]))
    assert "added pathogenicity to 3 missense variants" in html


def test_a_truncation_note_is_marked_and_comes_first():
    """A report showing 1,000 of 442,712 associations and saying nothing reads as
    the complete set. The limit has to be visible, and above the provenance
    notes, because it changes how everything below it should be read."""
    html = notes_html(_R([
        "AlphaMissense: added pathogenicity to 3 missense variants",
        "GWAS Catalog: showing the 1000 most statistically significant associations of 442712 found.",
    ]))
    assert 'class="lim"' in html
    assert html.index("442712") < html.index("AlphaMissense")


def test_unliftable_sites_count_as_a_limit():
    html = notes_html(_R(["input build GRCh37: lifted to GRCh38 "
                          "(311987 sites, 338013 unliftable) before ClinVar matching"]))
    assert 'class="lim"' in html


def test_notes_are_escaped():
    html = notes_html(_R(["<script>alert(1)</script>"]))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html

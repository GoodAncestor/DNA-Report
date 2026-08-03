"""The trait glossary: each referenced trait explained once, at the end."""
from dnareport.glossary import glossary_html


class _F:
    def __init__(self, copy_key=None, source="ewas_catalog"):
        self.marker, self.source, self.description = "cg1", source, "d"
        self.detail = {"copy_key": copy_key} if copy_key else {}


def test_one_entry_per_trait_however_often_it_appears():
    # "age" appears 127 times on the blood demo; it must be explained once
    html = glossary_html([_F("age")] * 127)
    assert html.count("id='trait-age'") == 1


def test_entry_carries_all_four_prose_fields_and_sources():
    html = glossary_html([_F("smoking")])
    for heading in ("What it is", "What an association means",
                    "What it is not", "Evidence"):
        assert heading in html
    assert "pubmed.ncbi.nlm.nih.gov" in html


def test_only_traits_present_in_the_report_are_included():
    html = glossary_html([_F("age")])
    assert "id='trait-age'" in html
    assert "id='trait-bmi'" not in html


def test_protein_class_entry_appears_once_for_many_proteins():
    html = glossary_html([_F("_protein_level") for _ in range(30)])
    assert html.count("id='trait-protein-level'") == 1
    assert "Blood level of a protein" in html


def test_no_glossary_when_nothing_has_copy():
    assert glossary_html([_F(None)]) == ""


def test_anchors_match_the_links_findings_emit():
    from biocore.report.render import glossary_anchor
    html = glossary_html([_F("age")])
    assert f"id='{glossary_anchor('age')}'" in html

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


# --- collapsed by default ---------------------------------------------------

def test_entries_are_collapsed_by_default():
    # 23 entries of full prose is ~46KB dumped on the page
    html = glossary_html([_F("age"), _F("bmi")])
    assert html.count("<details") == 2
    assert "<details open" not in html and " open>" not in html


def test_summary_shows_the_trait_name_so_the_list_is_scannable():
    html = glossary_html([_F("smoking")])
    summary = html.split("<summary")[1].split("</summary>")[0]
    assert "Smoking" in summary


def test_prose_is_still_present_just_hidden():
    html = glossary_html([_F("smoking")])
    assert "What it is not" in html
    assert "pubmed.ncbi.nlm.nih.gov" in html


def test_a_targeted_entry_is_opened_on_arrival():
    # a finding links to #trait-age; landing on a collapsed entry must open it,
    # otherwise the link appears to do nothing
    html = glossary_html([_F("age")])
    assert "hashchange" in html or "location.hash" in html

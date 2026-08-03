"""Findings in the main list carry the reader's own value for that probe.

A card that says a site is associated with lower methylation, but never shows
what this person's methylation actually was, gives them nothing to act on.
"""
from dnareport.orchestrate import _attach_sample_readings


class _F:
    def __init__(self, marker, source="ewas_catalog", description="d", detail=None):
        self.marker, self.source = marker, source
        self.description, self.detail = description, dict(detail or {})


def test_reading_is_attached_for_a_probe_present_in_the_sample():
    out = _attach_sample_readings([_F("cg00017842")], {"cg00017842": 0.5226})
    assert out[0].detail["your reading"] == 0.523


def test_reading_is_not_written_into_the_description():
    # bio-core groups findings into one card per marker and renders the reading
    # in the card header; putting it in each description restated one number up
    # to 30 times inside a single card
    out = _attach_sample_readings([_F("cg00017842")], {"cg00017842": 0.5226})
    assert "0.523" not in out[0].description


def test_probe_absent_from_the_sample_is_left_alone():
    f = _F("cg99999999")
    out = _attach_sample_readings([f], {"cg00017842": 0.5})
    assert "your reading" not in out[0].detail
    assert out[0].description == "d"


def test_reference_cards_are_not_double_annotated():
    # the highlight cards already plot the reading on their own scale
    f = _F("cg05575921", source="marker_reference")
    out = _attach_sample_readings([f], {"cg05575921": 0.93})
    assert "your reading" not in out[0].detail
    assert out[0].description == "d"

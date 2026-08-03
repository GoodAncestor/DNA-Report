"""A clock withheld for tissue mismatch must say so in words.

The tiles are deliberately terse, so the reason lives once beneath them. Without
it a tile reads "not reported / trained on blood", which states a fact about the
clock and never states the finding: that the number would have been misleading.
That is the difference the reader needs, so it is pinned here rather than left
to the tile copy.
"""
from types import SimpleNamespace

from dnareport.highlights import _mismatch_note


def _clock(name, mismatch, trained="blood"):
    return SimpleNamespace(clock=name, age=40.0, coverage=0.95,
                           tissue_mismatch=mismatch, trained_tissue=trained,
                           implausible=False, valid=not mismatch, note="")


def _result(clocks, tissue=None):
    return SimpleNamespace(clocks=clocks, tissue=tissue)


def test_mismatched_clocks_are_called_not_valid_for_this_sample_type():
    r = _result([_clock("Hannum2013_Blood", True),
                 _clock("Levine2018_PhenoAge", True),
                 _clock("Horvath2013_PanTissue", False)], tissue="buccal")
    note = _mismatch_note(r)
    assert "not valid for this sample type" in note
    assert "buccal" in note and "blood" in note


def test_no_note_when_every_clock_is_valid_for_the_sample():
    r = _result([_clock("Horvath2013_PanTissue", False)], tissue="blood")
    assert _mismatch_note(r) == ""


def test_a_single_mismatch_reads_as_singular():
    r = _result([_clock("Hannum2013_Blood", True),
                 _clock("Horvath2013_PanTissue", False)], tissue="buccal")
    note = _mismatch_note(r)
    assert "One of these clocks is" in note
    assert "1 of these clocks are" not in note


def test_note_survives_an_unknown_sample_tissue():
    # tissue is optional on the result; the reason must still be stated rather
    # than the sentence collapsing into a dangling "and this sample is ".
    r = _result([_clock("Hannum2013_Blood", True)], tissue=None)
    note = _mismatch_note(r)
    assert "not valid for this sample type" in note
    assert "this sample is" not in note

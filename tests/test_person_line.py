"""The person line, the acceleration on a tile, and the clock-moves strip."""
import types
from dnareport.highlights import person_line, _acceleration_html, clock_moves_html, highlights_html


def _clock(age=52.3, valid=True, acceleration=None, contributions=None, clock="Hannum2013_Blood"):
    return types.SimpleNamespace(clock=clock, age=age, valid=valid, coverage=0.95, note="63/71 CpGs (89%)",
                                 tissue_mismatch=False, implausible=False, acceleration=acceleration,
                                 contributions=contributions or [])


def test_person_line_labels_guesses_and_entries():
    r = types.SimpleNamespace(age=52, age_source="user", sex="female", sex_source="guess")
    h = person_line(r)
    assert "Age 52" in h and "(your entry)" in h
    assert "Sex female" in h and "estimated from your file" in h
    g = types.SimpleNamespace(age=48.4, age_source="guess", sex=None, sex_source=None)
    assert "Age about 48" in person_line(g) and "enter your age" in person_line(g)
    assert person_line(types.SimpleNamespace()) == ""


def test_acceleration_reads_older_younger_or_about():
    assert "+4.2 yrs older than your age" in _acceleration_html(_clock(acceleration=4.2))
    assert "-2.1 yrs younger than your age" in _acceleration_html(_clock(acceleration=-2.1))
    assert "about your age" in _acceleration_html(_clock(acceleration=0.3))
    assert _acceleration_html(_clock(acceleration=None)) == ""


def test_snapshot_carries_person_line_and_tile_acceleration():
    r = types.SimpleNamespace(findings=[], clocks=[_clock(acceleration=4.2)], tissue="blood",
                              age=48, age_source="user", sex=None, sex_source=None)
    h = highlights_html(r)
    assert "class='person'" in h and "Age 48" in h
    assert "+4.2 yrs older than your age" in h


def test_clock_moves_strip_lists_signed_sites(monkeypatch):
    import methylask.clocks as clocks_mod
    monkeypatch.setattr(clocks_mod, "top_contributions",
                        lambda c, n=8: [("cg1", 1.0, 0.8, 0.8, 3.1), ("cg2", -2.0, 0.5, -1.0, -1.4)], raising=False)
    r = types.SimpleNamespace(findings=[], clocks=[_clock(contributions=[("cg1", 1.0, 0.8, 0.8, 3.1)])], tissue="blood")
    h = clock_moves_html(r)
    assert "What moves your clock" in h and "+3.1 yrs" in h and "-1.4 yrs" in h
    assert "relative to a zero reading" in h and "mvbar up" in h and "mvbar down" in h
    assert clock_moves_html(types.SimpleNamespace(clocks=[_clock()])) == ""

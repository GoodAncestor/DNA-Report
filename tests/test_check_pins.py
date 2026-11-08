import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "check_pins", Path(__file__).resolve().parents[1] / "scripts" / "check_pins.py")
check_pins = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_pins)

A = "0a31ed76789a960be9929f87e7b13de4227c1f82"
B = "0afd03d0000000000000000000000000000000000"


def test_pin_parsed_from_requires_dist():
    reqs = ["requests", f"biocore[variants] @ git+https://github.com/GoodAncestor/bio-core.git@{A}"]
    assert check_pins.biocore_pin(reqs) == A
    assert check_pins.biocore_pin(["requests"]) is None


def test_matching_pins_pass():
    assert check_pins.problems({"dna-report": A, "geneask": A, "methylask": A[:7]}) == []


def test_stale_pin_fails():
    out = check_pins.problems({"dna-report": A, "geneask": A, "methylask": B})
    assert out and "differ" in out[0]


def test_missing_pin_fails():
    assert check_pins.problems({"dna-report": A, "geneask": None})


def test_installed_environment_is_consistent():
    """The real install: the three engines and the resolved bio-core agree."""
    assert check_pins.problems(check_pins.installed_pins()) == []

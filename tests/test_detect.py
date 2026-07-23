"""Routing tests — the core of DNA-Report."""
import os
from dnareport.detect import detect, route, InputKind

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def test_detect_23andme():
    assert detect(os.path.join(FIX, "sample_23andme.txt")) == InputKind.TWENTYTHREE_AND_ME


def test_detect_beta_matrix():
    assert detect(os.path.join(FIX, "sample_beta.csv")) == InputKind.BETA_MATRIX


def test_detect_vcf():
    assert detect(os.path.join(FIX, "sample.vcf")) == InputKind.VCF


def test_routing_targets():
    assert route(os.path.join(FIX, "sample_23andme.txt"))[1] == ("geneask",)
    assert route(os.path.join(FIX, "sample_beta.csv"))[1] == ("methylask",)
    assert route(os.path.join(FIX, "sample.vcf"))[1] == ("geneask",)


def test_unknown_is_safe():
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        fh.write("just some random text\nnothing structured\n")
        p = fh.name
    assert detect(p) == InputKind.UNKNOWN
    os.unlink(p)

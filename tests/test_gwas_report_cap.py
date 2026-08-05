"""The GWAS finding ceiling.

A consumer array is selected for GWAS overlap, so nearly every SNP on it carries
associations and each carries several. A 650k-variant AncestryDNA export measured
442,719 GWAS findings and rendered a 495 MB HTML report — unreadable, and on a
slow connection undownloadable. The report is bounded, and the bound is stated.
"""
import pytest
import dnareport.orchestrate as orch


class _F:
    """Minimal stand-in for a Finding: the cap only reads .detail."""
    def __init__(self, p):
        self.detail = {"p": p}


_cap = orch.cap_gwas_findings


def test_the_cap_keeps_the_most_significant_associations():
    """Strongest evidence survives truncation. Keeping an arbitrary slice would
    mean the report's headline depended on file ordering."""
    findings = [_F(p) for p in (1e-2, 1e-30, 1e-9, 1e-4, 1e-20)]
    kept, _ = _cap(findings, 2)
    assert [f.detail["p"] for f in kept] == [1e-30, 1e-20]


def test_a_missing_p_value_sorts_last_rather_than_first():
    """`None` must not sort as the strongest result. Ranking an association with
    no reported p-value above a genome-wide significant one would put the least
    supported claim at the top of the report."""
    findings = [_F(None), _F(1e-9), _F(None), _F(1e-3)]
    kept, _ = _cap(findings, 2)
    assert [f.detail["p"] for f in kept] == [1e-9, 1e-3]


def test_under_the_cap_nothing_is_dropped_and_nothing_is_claimed():
    findings = [_F(1e-9), _F(1e-3)]
    kept, notes = _cap(findings, 10)
    assert len(kept) == 2
    assert not notes, "said it truncated when it did not"


def test_truncation_is_disclosed_with_both_numbers():
    """Silent truncation reads as 'this is everything we found'."""
    _, notes = _cap([_F(1e-9)] * 50, 10)
    assert notes and "10 most statistically significant associations of 50" in notes[0]


def test_the_cap_is_configurable_and_defaults_sanely():
    assert isinstance(orch.MAX_GWAS_FINDINGS, int)
    assert 0 < orch.MAX_GWAS_FINDINGS <= 20000


# ------------------------------- the cap belongs to the document, not the data
def test_analysis_no_longer_truncates(monkeypatch, tmp_path):
    """The cap is a limit on what one web page can hold, not a judgement about the
    science. Applying it during analysis put the truncation into the JSON an agent
    reads and the archive a person downloads — deciding they cannot have their own
    results in order to keep a page small."""
    import inspect
    from dnareport import orchestrate
    src = inspect.getsource(orchestrate._run_geneask)
    assert "cap_gwas_findings" not in src, (
        "analysis must not cap; the HTML composer does")


def test_the_html_composer_still_bounds_the_page():
    from dnareport import report
    import inspect
    assert "cap_gwas_findings" in inspect.getsource(report._render_findings)

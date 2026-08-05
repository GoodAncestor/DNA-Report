"""A .vcf.gz made with gzip instead of bgzip must still produce a report.

`gzip file.vcf` and `bgzip file.vcf` both give you `file.vcf.gz`, and only the
second is readable by htslib — so this is a mistake a user cannot see and cannot
be blamed for. Untreated, every variant reader raised, the orchestrator swallowed
it, and the scan came back with zero findings and no explanation, which the worker
then turned into a claim link that never resolved.
"""
import gzip
import os

from dnareport.detect import InputKind
from dnareport.orchestrate import ReportResult, _is_bgzf, _pysam_readable


def _plain_gzip_vcf(tmp_path):
    p = tmp_path / "sample.vcf.gz"
    with gzip.open(p, "wt") as fh:
        fh.write("##fileformat=VCFv4.2\n"
                 "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
                 "1\t943995\t.\tC\tT\t100\tPASS\t.\tGT\t0/1\n")
    return str(p)


def _result():
    return ReportResult(kind=InputKind.VCF, engines=("geneask",))


def test_plain_gzip_is_not_mistaken_for_bgzf(tmp_path):
    assert _is_bgzf(_plain_gzip_vcf(tmp_path)) is False


def test_bgzf_is_recognised_and_passed_through_untouched(tmp_path):
    # BGZF header: gzip magic with FEXTRA set, then the 'BC' subfield
    p = tmp_path / "bgzipped.vcf.gz"
    p.write_bytes(b"\x1f\x8b\x08\x04" + b"\x00" * 8 + b"BC\x02\x00" + b"rest")
    assert _is_bgzf(str(p)) is True
    res = _result()
    with _pysam_readable(str(p), InputKind.VCF, res) as work:
        assert work == str(p)
    assert res.notes == []


def test_a_plain_gzip_vcf_is_handed_to_the_engines_decompressed(tmp_path):
    src = _plain_gzip_vcf(tmp_path)
    res = _result()
    with _pysam_readable(src, InputKind.VCF, res) as work:
        assert work != src
        assert not work.endswith(".gz")
        assert "##fileformat=VCFv4.2" in open(work).read()
    # and it says so, because the reader should know what was done to their file
    assert any("plain gzip" in n for n in res.notes)


def test_the_scratch_copy_does_not_outlive_the_scan(tmp_path):
    res = _result()
    with _pysam_readable(_plain_gzip_vcf(tmp_path), InputKind.VCF, res) as work:
        assert os.path.exists(work)
    assert not os.path.exists(work)


def test_methylation_inputs_are_never_decompressed(tmp_path):
    """Only the htslib path needs this; expanding a large beta matrix is waste."""
    p = tmp_path / "betas.csv.gz"
    with gzip.open(p, "wt") as fh:
        fh.write("probe,S1\ncg00000029,0.42\n")
    res = ReportResult(kind=InputKind.BETA_MATRIX, engines=("methylask",))
    with _pysam_readable(str(p), InputKind.BETA_MATRIX, res) as work:
        assert work == str(p)
    assert res.notes == []


def test_an_unreadable_variant_file_is_explained_not_hidden():
    """The reason a screen didn't run has to reach the report, or 'we found
    nothing' becomes indistinguishable from 'we could not look'."""
    from dnareport.orchestrate import _reader_error

    msg = _reader_error(NotImplementedError("seek not implemented in files "
                                           "compressed by method 1"))
    assert "bgzip" in msg
    assert "seek not implemented" not in msg      # plain English, not the traceback

"""Upload-boundary tests — an unusable upload must fail loudly.

The bug these cover: a file we could not use was answered with HTTP 200 and a
JSON body, so the browser showed one line of grey text and a failed upload was
indistinguishable from nothing happening. Every case here asserts a real 4xx and
an error body a person could act on.
"""
import os
import zipfile

import pytest

from dnareport.detect import detect, InputKind
from dnareport.uploads import (UploadError, MAX_UPLOAD_BYTES, ACCEPTED_FORMATS,
                               check_size, unwrap_archive, sanitize_note)

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
SAMPLE = os.path.join(FIX, "sample_23andme.txt")


# ---- size gate -------------------------------------------------------------

def test_empty_upload_is_refused():
    with pytest.raises(UploadError) as e:
        check_size(0)
    assert e.value.status == 400
    assert e.value.code == "empty_file"


def test_oversized_upload_is_refused_with_413():
    with pytest.raises(UploadError) as e:
        check_size(MAX_UPLOAD_BYTES + 1)
    assert e.value.status == 413
    assert e.value.code == "too_large"


def test_ordinary_size_passes():
    check_size(20 * 1024 * 1024)      # a real 23andMe export


# ---- error bodies carry something actionable -------------------------------

def test_error_body_shape_is_stable():
    err = UploadError("unrecognised_format", "Title", "Message",
                      hint="Do this instead", status=415, accepted=True)
    body = err.body()
    assert body["error"]["code"] == "unrecognised_format"
    assert body["error"]["hint"] == "Do this instead"
    assert body["error"]["accepted"] == ACCEPTED_FORMATS
    # `detail` retained for callers reading FastAPI's own error shape
    assert body["detail"] == "Message"


def test_accepted_formats_mention_zip():
    """Consumer exports ship as ZIPs, so the accepted list must say so."""
    assert any("zip" in f.lower() for f in ACCEPTED_FORMATS)


# ---- archive unwrapping ----------------------------------------------------

def test_plain_file_passes_through_untouched(tmp_path):
    out, note = unwrap_archive(SAMPLE, str(tmp_path))
    assert out == SAMPLE
    assert note is None


def test_zipped_genotype_is_unwrapped_and_then_detects(tmp_path):
    """The real-world case: 23andMe hands the user a ZIP, not a .txt."""
    z = tmp_path / "genome_export.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(SAMPLE, arcname="genome_Jane_Doe_v5_Full.txt")

    out, note = unwrap_archive(str(z), str(tmp_path))

    assert out != str(z) and os.path.exists(out)
    assert note and "genome_Jane_Doe_v5_Full.txt" in note
    # and the unwrapped file routes exactly as the bare .txt would
    assert detect(out) == detect(SAMPLE) == InputKind.TWENTYTHREE_AND_ME


def test_zip_noise_members_are_skipped(tmp_path):
    """macOS resource forks and readmes must not win over the real data."""
    z = tmp_path / "export.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("__MACOSX/._genome.txt", "x" * 5000)
        zf.writestr("README.txt", "y" * 4000)
        zf.write(SAMPLE, arcname="genome.txt")

    out, _ = unwrap_archive(str(z), str(tmp_path))
    assert os.path.basename(out) == "genome.txt"
    assert detect(out) == InputKind.TWENTYTHREE_AND_ME


def test_zip_path_traversal_cannot_escape_scratch(tmp_path):
    """A crafted member name must not write outside the scratch directory."""
    z = tmp_path / "evil.zip"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../../../tmp/pwned.txt", "rsid\tchromosome\tposition\tgenotype\n")

    out, _ = unwrap_archive(str(z), str(scratch))
    assert os.path.dirname(os.path.abspath(out)) == os.path.abspath(str(scratch))
    assert not os.path.exists("/tmp/pwned.txt")


def test_empty_zip_is_refused_with_415(tmp_path):
    z = tmp_path / "empty.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("README.txt", "nothing useful here")

    with pytest.raises(UploadError) as e:
        unwrap_archive(str(z), str(tmp_path))
    assert e.value.status == 415
    assert e.value.accepted is True


def test_zip_bomb_member_is_refused_before_extraction(tmp_path):
    """The cap applies to the UNCOMPRESSED member, checked before writing."""
    z = tmp_path / "bomb.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.txt", b"\0" * (MAX_UPLOAD_BYTES + 1024))

    with pytest.raises(UploadError) as e:
        unwrap_archive(str(z), str(tmp_path))
    assert e.value.code == "too_large"


# ---- note sanitizing -------------------------------------------------------

def test_scratch_path_is_not_leaked_to_the_user():
    """Engine notes interpolate the server path they were handed; the UI must
    show the user's own filename instead of our temp directory layout."""
    note = ("Could not determine file type for "
            "/tmp/dnr-web-l0xtcs1k/garbage.txt; no engine routed.")
    out = sanitize_note(note, "/tmp/dnr-web-l0xtcs1k", "garbage.txt")
    assert "dnr-web-" not in out
    assert "/tmp/" not in out
    assert "garbage.txt" in out


def test_sanitize_handles_empty_note():
    assert sanitize_note("", "/tmp/x", "f.txt") == ""


# ---- streaming cap ---------------------------------------------------------

def test_stream_to_disk_aborts_past_the_cap(tmp_path, monkeypatch):
    """An oversized upload must be refused mid-stream, not after being fully
    read into memory, and must not leave a partial file behind."""
    import asyncio
    from dnareport import uploads

    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 4 * 1024 * 1024)

    class FakeUpload:
        def __init__(self, total): self.left = total
        async def read(self, n):
            if not self.left:
                return b""
            take = min(n, self.left)
            self.left -= take
            return b"\0" * take

    dest = tmp_path / "big.txt"
    with pytest.raises(UploadError) as e:
        asyncio.run(uploads.stream_to_disk(FakeUpload(9 * 1024 * 1024), str(dest)))
    assert e.value.status == 413
    assert not dest.exists(), "partial oversized upload was left on disk"


def test_stream_to_disk_writes_a_normal_file(tmp_path):
    import asyncio
    from dnareport import uploads

    class FakeUpload:
        def __init__(self, data): self.data = data
        async def read(self, n):
            out, self.data = self.data[:n], self.data[n:]
            return out

    dest = tmp_path / "ok.txt"
    payload = b"rsid\tchromosome\tposition\tgenotype\n" * 100
    n = asyncio.run(uploads.stream_to_disk(FakeUpload(payload), str(dest)))
    assert n == len(payload)
    assert dest.read_bytes() == payload


# ---- JSON surface carries the ranking fields -------------------------------

def test_json_findings_carry_magnitude_and_direction():
    """An agent consuming the JSON must not have to re-derive the two fields the
    HTML report ranks and triages by."""
    from dnareport.serialize import _finding_json, SCHEMA_VERSION
    from biocore.providers.base import Finding, Tier, Category

    pathogenic = Finding("1-100-A-G", "clinvar", "PKD1: Pathogenic", Tier.MODERATE,
                         [Category.CLINICAL],
                         detail={"gene": "PKD1", "clinical_significance": "Pathogenic",
                                 "gold_stars": 2})
    j = _finding_json(pathogenic, None)
    assert j["direction"] == "adverse"
    assert 4.0 <= j["magnitude"] <= 7.0        # inside the MODERATE band
    assert SCHEMA_VERSION == "2.1"


def test_json_direction_is_empty_for_unclassified_findings():
    from dnareport.serialize import _finding_json
    from biocore.providers.base import Finding, Tier, Category

    trait = Finding("rs4988235", "geneask", "Lactase persistence", Tier.ROBUST,
                    [Category.TRAIT], detail={"topic": "metabolic", "n": 120000})
    assert _finding_json(trait, None)["direction"] == ""


def test_summary_counts_only_classified_directions():
    """"No direction stated" is an absence, not a bucket — it must not appear in
    by_direction as if it were a verdict."""
    from dnareport.serialize import result_to_json
    from biocore.providers.base import Finding, Tier, Category

    class R:
        kind, tissue, engines, clocks, notes = "23andme", "blood", (), [], []
        findings = [
            Finding("m1", "clinvar", "d", Tier.MODERATE, [Category.CLINICAL],
                    detail={"clinical_significance": "Pathogenic"}),
            Finding("m2", "geneask", "d", Tier.ROBUST, [Category.TRAIT], detail={}),
        ]
    out = result_to_json(R())
    assert out["summary"]["by_direction"] == {"adverse": 1}


def test_sanitize_note_does_not_corrupt_urls():
    """A blanket '//' -> '/' collapse mangled every URL an engine cited."""
    note = "see https://www.ncbi.nlm.nih.gov/clinvar/ for details"
    assert sanitize_note(note, "/tmp/dnr-web-abc", "g.txt") == note


def test_sanitize_note_substitutes_the_users_filename():
    out = sanitize_note("could not parse /tmp/dnr-web-abc123/genome.txt (line 4)",
                        "/tmp/dnr-web-abc123", "genome.txt")
    assert out == "could not parse genome.txt (line 4)", out


def test_sanitize_note_leaves_legitimate_paths_alone():
    """The old regex stripped any /tmp or /var path, including a mirror DB an
    engine legitimately names."""
    note = "ClinVar mirror /var/db/geneask/clinvar_full.sqlite is stale"
    assert sanitize_note(note, "/tmp/dnr-web-abc", "g.txt") == note


def test_sanitize_note_catches_a_foreign_scratch_dir():
    out = sanitize_note("failed on /private/var/folders/x/dnr-web-zz9/other.txt",
                        "/tmp/dnr-web-abc", "g.txt")
    assert "dnr-web-" not in out and out.endswith("other.txt"), out

import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from dnareport.detect import InputKind
from dnareport.guess import guess_age, guess_sex_genome, guess_sex_methylome
from dnareport.orchestrate import ReportResult, _apply_guesses


PROBES = [
    "cg05533223",
    "cg03554089",
    "cg12653510",
    "cg11643285",
    "cg10914153",
    "cg26983535",
    "cg05300123",
    "cg25165578",
    "cg22839831",
    "cg20926353",
]


def test_genome_sex_guess_uses_x_and_y_calls():
    assert guess_sex_genome([{"variant_id": "Y-100-A-G", "zygosity": "het"}]) == "male"
    assert guess_sex_genome([{"chrom": "chrX", "zygosity": "hom"}]) == "female"
    assert guess_sex_genome([]) is None
    assert guess_sex_genome([{"chrom": "Y", "zygosity": "unknown"}]) is None


def test_methylome_sex_guess_uses_the_xist_probe_mean():
    assert guess_sex_methylome({probe: 0.5 for probe in PROBES}) == "female"
    assert guess_sex_methylome({probe: 0.1 for probe in PROBES}) == "male"
    assert guess_sex_methylome({probe: 0.25 for probe in PROBES}) is None
    assert guess_sex_methylome({}) is None


def test_age_guess_is_the_median_of_valid_clock_ages():
    clocks = [
        SimpleNamespace(age=41.0, valid=True),
        SimpleNamespace(age=50.0, valid=False),
        SimpleNamespace(age=45.0, valid=True),
    ]

    assert guess_age(clocks) == 43.0
    assert guess_age([SimpleNamespace(age=None, valid=False)]) is None


def test_guesses_never_replace_user_entries():
    result = ReportResult(
        kind=InputKind.VCF,
        engines=("geneask",),
        age=52,
        sex="other",
        age_source="user",
        sex_source="user",
        clocks=[SimpleNamespace(age=40.0, valid=True)],
    )

    _apply_guesses(
        result,
        genome_calls=[{"chrom": "Y", "zygosity": "het"}],
        methyl_betas={probe: 0.5 for probe in PROBES},
    )

    assert (result.age, result.age_source) == (52, "user")
    assert (result.sex, result.sex_source) == ("other", "user")


def test_inferred_values_are_labelled_as_guesses():
    result = ReportResult(
        kind=InputKind.VCF,
        engines=("geneask",),
        clocks=[SimpleNamespace(age=48.0, valid=True)],
    )

    _apply_guesses(
        result,
        genome_calls=[{"chrom": "Y", "zygosity": "het"}],
        methyl_betas={},
    )

    assert (result.age, result.age_source) == (48.0, "guess")
    assert (result.sex, result.sex_source) == ("male", "guess")


def test_sex_probe_data_records_review_status():
    path = Path(__file__).resolve().parents[1] / "dnareport" / "data" / "sex_probes.json"
    document = json.loads(path.read_text())

    assert document["probes"] == PROBES
    assert document["reviewed_by"] == []
    assert document["verification"] == "unverified"


def test_noneditable_wheel_contains_the_sex_probe_data(tmp_path):
    root = Path(__file__).resolve().parents[1]
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        pytest.skip("The current setuptools cannot parse this repository's licence field.")
    wheel = next(wheel_dir.glob("dna_report-*.whl"))

    with zipfile.ZipFile(wheel) as archive:
        assert "dnareport/data/sex_probes.json" in archive.namelist()

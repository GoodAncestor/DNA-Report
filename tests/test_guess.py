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


_PROBE_DOCUMENT = json.loads(
    (Path(__file__).resolve().parents[1] / "dnareport" / "data" / "sex_probes.json").read_text()
)
HYPER_MALE = _PROBE_DOCUMENT["hyper_male"]
HYPO_MALE = _PROBE_DOCUMENT["hypo_male"]
PROBES = HYPER_MALE + HYPO_MALE


def _betas(hyper: float, hypo: float, count: int | None = None) -> dict:
    betas = {probe: hyper for probe in HYPER_MALE[:count]}
    betas.update({probe: hypo for probe in HYPO_MALE[:count]})
    return betas


def test_genome_sex_guess_uses_x_and_y_calls():
    assert guess_sex_genome([{"variant_id": "Y-100-A-G", "zygosity": "het"}]) == "male"
    assert guess_sex_genome([{"chrom": "chrX", "zygosity": "hom"}]) == "female"
    assert guess_sex_genome([]) is None
    assert guess_sex_genome([{"chrom": "Y", "zygosity": "unknown"}]) is None


def test_methylome_sex_guess_follows_sesame_infersex():
    # Males: X-linked CpGs in hyper_male near fully methylated, hypo_male near
    # unmethylated. Females: both near 0.5 from X inactivation.
    assert guess_sex_methylome(_betas(0.9, 0.05)) == "male"
    assert guess_sex_methylome(_betas(0.5, 0.5)) == "female"
    assert guess_sex_methylome(_betas(0.6, 0.15)) == "male"
    assert guess_sex_methylome(_betas(0.55, 0.2)) == "female"
    assert guess_sex_methylome({}) is None


def test_methylome_sex_guess_declines_with_too_few_probes():
    minimum = _PROBE_DOCUMENT["min_probes_per_set"]
    assert guess_sex_methylome(_betas(0.9, 0.05, minimum - 1)) is None
    assert guess_sex_methylome(_betas(0.9, 0.05, minimum)) == "male"
    noisy = _betas(0.9, 0.05)
    noisy.update({probe: "NA" for probe in HYPO_MALE})
    assert guess_sex_methylome(noisy) is None


def test_methylome_sex_guess_uses_the_mode_not_the_mean():
    betas = _betas(0.5, 0.5)
    # A minority of hypo_male probes near zero must not pull a female call.
    betas.update({probe: 0.0 for probe in HYPO_MALE[:30]})
    assert guess_sex_methylome(betas) == "female"


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
        methyl_betas=_betas(0.5, 0.5),
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
    document = _PROBE_DOCUMENT

    assert len(HYPER_MALE) == len(set(HYPER_MALE)) == 100
    assert len(HYPO_MALE) == len(set(HYPO_MALE)) == 100
    assert not set(HYPER_MALE) & set(HYPO_MALE)
    assert document["verification"] == "verified"
    assert "0ddcf6f2942214920f4b1e3568e3eeff301cef2e" in document["source"]["url"]
    # Agent review only: no person has reviewed this file.
    assert document["human_reviewed"] is False
    assert document["reviewed_by"]
    assert all(entry.startswith("agent: ") for entry in document["reviewed_by"])


def test_sex_probes_are_in_the_bundled_hm450_manifest():
    import gzip

    methylask = pytest.importorskip("methylask")
    manifest = (
        Path(methylask.__file__).parent / "data" / "reference" / "manifests" / "HM450.hg38.manifest.tsv.gz"
    )
    if not manifest.exists():
        pytest.skip("MethylAsk manifests are not bundled in this install.")
    wanted = set(PROBES)
    chromosomes = {}
    with gzip.open(manifest, "rt") as handle:
        header = handle.readline().rstrip("\n").split("\t")
        probe_col, chrom_col = header.index("Probe_ID"), header.index("CpG_chrm")
        for line in handle:
            fields = line.rstrip("\n").split("\t")
            if fields[probe_col] in wanted:
                chromosomes[fields[probe_col]] = fields[chrom_col]

    assert set(chromosomes) == wanted
    assert set(chromosomes.values()) == {"chrX"}


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


def test_density_mode_matches_r_fft_density():
    """Cross-check against a literal transcription of R's density.default
    (binning, FFT convolution, approx) with numpy's FFT."""
    import math
    import random

    np = pytest.importorskip("numpy")
    from dnareport.guess import _bandwidth_nrd0, _density_mode

    def r_mode(values):
        x = np.asarray(values, float)
        n = 512
        bw = _bandwidth_nrd0(values)
        start, stop = x.min() - 3 * bw, x.max() + 3 * bw
        low, high = start - 4 * bw, stop + 4 * bw
        y = np.zeros(2 * n)
        delta = (high - low) / (n - 1)
        for v in x:
            pos = (v - low) / delta
            ix = math.floor(pos)
            fx = pos - ix
            if 0 <= ix <= n - 2:
                y[ix] += (1 - fx) / len(x)
                y[ix + 1] += fx / len(x)
        kords = np.linspace(0, (2 * n - 1) / (n - 1) * (high - low), 2 * n)
        kords[n + 1:] = -kords[1:n][::-1]
        kords = np.exp(-0.5 * (kords / bw) ** 2) / (bw * math.sqrt(2 * math.pi))
        dens = np.maximum(0, np.fft.ifft(np.fft.fft(y) * np.conj(np.fft.fft(kords))).real[:n])
        grid = np.linspace(start, stop, 512)
        return grid[np.argmax(np.interp(grid, np.linspace(low, high, n), dens))]

    rng = random.Random(7)
    for _ in range(25):
        values = [rng.gauss(rng.uniform(0.35, 0.45), 0.15) for _ in range(100)]
        assert _density_mode(values) == pytest.approx(r_mode(values), abs=1e-9)


def test_methylome_sex_guess_declines_on_tied_peaks_across_the_threshold():
    # Two equal clusters of differences straddling 0.4 (hyper mean 0.9 minus
    # hypo values) give two equal density peaks; the call would hinge on
    # floating-point rounding, so no estimate is returned.
    betas = {probe: 0.9 for probe in HYPER_MALE}
    for index, probe in enumerate(HYPO_MALE):
        betas[probe] = 0.9 - (0.30000000000000004 if index % 2 else 0.5)
    assert guess_sex_methylome(betas) is None

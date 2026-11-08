# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""Conservative age and sex estimates from report inputs."""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path


_PROBE_FILE = Path(__file__).parent / "data" / "sex_probes.json"


def _value(record, name, default=None):
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def _chromosome(record) -> str:
    chrom = _value(record, "chrom") or _value(record, "chromosome")
    if not chrom:
        variant_id = str(_value(record, "variant_id", ""))
        chrom = variant_id.split("-", 1)[0] if variant_id else ""
    chrom = str(chrom).upper()
    return chrom[3:] if chrom.startswith("CHR") else chrom


def guess_sex_genome(carried: list[dict]) -> str | None:
    """Estimate sex from called X and Y records, or return no estimate."""
    has_x = False
    has_y = False
    called_y = False
    for record in carried or []:
        chrom = _chromosome(record)
        if chrom == "X":
            has_x = True
        elif chrom == "Y":
            has_y = True
            called_y = str(_value(record, "zygosity", "unknown")).lower() != "unknown"
            if called_y:
                return "male"
    if has_x and not has_y:
        return "female"
    return None


def _sex_probe_data() -> dict:
    try:
        document = json.loads(_PROBE_FILE.read_text())
    except (OSError, ValueError, TypeError):
        return {}
    return document if isinstance(document, dict) else {}


def _finite_readings(betas: dict, probes) -> list[float]:
    readings = []
    for probe in probes or []:
        value = (betas or {}).get(probe)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            readings.append(value)
    return readings


def _bandwidth_nrd0(values: list[float]) -> float:
    """R's ``bw.nrd0``, the default bandwidth of ``stats::density``."""
    spread = statistics.stdev(values)
    quartiles = statistics.quantiles(values, n=4, method="inclusive")
    low = min(spread, (quartiles[2] - quartiles[0]) / 1.34)
    if not low:
        low = spread or abs(values[0]) or 1.0
    return 0.9 * low * len(values) ** -0.2


def _seq(start: float, stop: float, count: int) -> list[float]:
    """R's ``seq.int(start, stop, length.out = count)``."""
    step = (stop - start) / (count - 1)
    values = [start + index * step for index in range(count)]
    values[-1] = stop
    return values


def _approx(xs: list[float], ys: list[float], at: float) -> float:
    """R's ``approx(xs, ys, at)$y`` (linear, ``xs`` increasing, ``at`` inside)."""
    low, high = 0, len(xs) - 1
    while low < high - 1:
        middle = (low + high) // 2
        if at < xs[middle]:
            high = middle
        else:
            low = middle
    if at == xs[high]:
        return ys[high]
    if at == xs[low]:
        return ys[low]
    return ys[low] + (ys[high] - ys[low]) * ((at - xs[low]) / (xs[high] - xs[low]))


def _density_curve(values: list[float]) -> tuple[list[float], list[float]]:
    """``dd$x[which.max(dd$y)]`` for R's ``dd <- density(values)`` with its
    defaults: gaussian kernel, ``bw.nrd0``, ``n = 512``, ``cut = 3``,
    ``ext = 4``, ``old.coords = FALSE``.

    It follows R's stats ``density.default`` and ``BinDist``: linear binning
    on ``n`` points over ``[from - ext*bw, to + ext*bw]``, convolution with the
    kernel (R does this by FFT; here it is summed directly, which gives the
    same circular sum because the binned mass occupies only the first ``n`` of
    ``2n`` cells), then linear interpolation back onto the ``from..to`` grid.
    """
    n = 512
    bandwidth = _bandwidth_nrd0(values)
    start = min(values) - 3.0 * bandwidth
    stop = max(values) + 3.0 * bandwidth
    low = start - 4.0 * bandwidth
    high = stop + 4.0 * bandwidth
    delta = (high - low) / (n - 1)
    weight = 1.0 / len(values)
    bins = [0.0] * n
    for value in values:
        position = (value - low) / delta
        index = math.floor(position)
        fraction = position - index
        if 0 <= index <= n - 2:
            bins[index] += (1 - fraction) * weight
            bins[index + 1] += fraction * weight
        elif index == -1:
            bins[0] += fraction * weight
        elif index == n - 1:
            bins[index] += (1 - fraction) * weight
    norm = 1.0 / (bandwidth * math.sqrt(2 * math.pi))
    kernel = [norm * math.exp(-0.5 * (offset * delta / bandwidth) ** 2) for offset in range(n)]
    occupied = [(index, mass) for index, mass in enumerate(bins) if mass]
    heights = [
        max(0.0, sum(mass * kernel[abs(index - cell)] for index, mass in occupied))
        for cell in range(n)
    ]
    grid = _seq(low, high, n)
    locations = _seq(start, stop, n)
    return locations, [_approx(grid, heights, location) for location in locations]


def _density_mode(values: list[float]) -> float:
    """``dd$x[which.max(dd$y)]``: the first highest point of ``_density_curve``."""
    locations, heights = _density_curve(values)
    return locations[heights.index(max(heights))]


def guess_sex_methylome(betas: dict) -> str | None:
    """Estimate sex from array beta values with sesame's ``inferSex`` rule.

    sesame (R/sex.R) subtracts each X-linked CpG that is hypomethylated in
    males from the mean of the X-linked CpGs that are hypermethylated in males,
    and calls male when the mode of those differences exceeds 0.4. The probe
    lists and threshold live in ``data/sex_probes.json``. Too few readings in
    either list returns no estimate, and so do density peaks of equal height
    on both sides of the threshold; those two guards are local, not sesame's.

    sesame first runs ``mLiftOver(betas, "HM450")``, which for EPIC/EPICv2
    matches on the base probe id and keeps the first replicate per id. The
    caller passes betas already keyed by base id (``orchestrate`` keeps the
    last EPICv2 replicate), so replicate handling can differ from sesame's.
    """
    document = _sex_probe_data()
    try:
        threshold = float(document["male_mode_threshold"])
        minimum = max(2, int(document.get("min_probes_per_set", 2)))
    except (KeyError, TypeError, ValueError):
        return None
    hyper = _finite_readings(betas, document.get("hyper_male"))
    hypo = _finite_readings(betas, document.get("hypo_male"))
    if len(hyper) < minimum or len(hypo) < minimum:
        return None
    hyper_mean = sum(hyper) / len(hyper)
    locations, heights = _density_curve([hyper_mean - value for value in hypo])
    peak = max(heights)
    mode = locations[heights.index(peak)]
    male = mode > threshold
    # Local, not sesame: when a peak on the other side of the threshold is as
    # high to within floating-point noise, the call depends on rounding.
    tolerance = peak * 1e-9
    if any(
        (location > threshold) != male and height >= peak - tolerance
        for location, height in zip(locations, heights)
    ):
        return None
    return "male" if male else "female"


def guess_age(clocks: list) -> float | None:
    """Return the median valid clock age, or no estimate."""
    ages = []
    for clock in clocks or []:
        age = _value(clock, "age")
        if not _value(clock, "valid", False) or age is None:
            continue
        try:
            age = float(age)
        except (TypeError, ValueError):
            continue
        if math.isfinite(age):
            ages.append(age)
    return float(statistics.median(ages)) if ages else None

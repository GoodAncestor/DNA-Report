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


def _sex_probe_ids() -> list[str]:
    try:
        return list(json.loads(_PROBE_FILE.read_text()).get("probes") or [])
    except (OSError, ValueError, TypeError):
        return []


def guess_sex_methylome(betas: dict) -> str | None:
    """Estimate sex from the available XIST-region probe mean."""
    readings = []
    for probe in _sex_probe_ids():
        value = (betas or {}).get(probe)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            readings.append(value)
    if not readings:
        return None
    mean = sum(readings) / len(readings)
    if mean >= 0.35:
        return "female"
    if mean <= 0.15:
        return "male"
    return None


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

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""Group one report's existing findings by consequence."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


_ALIASES_FILE = Path(__file__).parent / "data" / "outcome_aliases.json"
_CLINVAR = {"clinvar", "clinvar_mirror", "clinvar_panel_157"}


@dataclass
class Outcome:
    key: str
    label: str
    kind: str
    findings: list = field(default_factory=list)
    score: object | None = None
    contributions: list = field(default_factory=list)
    reference_groups: list = field(default_factory=list)
    actions: list = field(default_factory=list)


def _normal(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _slug(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")


def _aliases():
    try:
        rows = json.loads(_ALIASES_FILE.read_text())
    except (OSError, ValueError, TypeError):
        rows = {}
    lookup = {}
    labels = {}
    for key, values in rows.items():
        labels[key] = str((values or [key])[0])
        for value in [key, *(values or [])]:
            lookup[_normal(value)] = key
    return lookup, labels


def _canonical(*values) -> tuple[str, str]:
    lookup, labels = _aliases()
    for value in values:
        key = lookup.get(_normal(value))
        if key:
            return key, labels.get(key, str(value))
    first = next((str(value).strip() for value in values if str(value or "").strip()), "other")
    return _slug(first) or "other", first


def condition_key(label: str) -> str:
    return f"condition:{_slug(label)}"


def medicine_key(drug: str) -> str:
    return f"medicine:{_slug(drug)}"


def trait_key(*values) -> str:
    key, _label = _canonical(*values)
    return f"trait:{key}"


def _condition(finding) -> str:
    interpretation = getattr(finding, "interpretation", None)
    condition = getattr(interpretation, "condition", None)
    if condition:
        return str(condition)
    conditions = (finding.detail or {}).get("conditions") or []
    return str(conditions[0]) if conditions else ""


def _trait_identity(finding) -> tuple[str, str]:
    detail = finding.detail or {}
    if finding.source == "marker_reference" and finding.marker == "cg05575921":
        return "smoking", "Smoking"
    key, label = _canonical(
        detail.get("copy_key"),
        detail.get("efo"),
        detail.get("trait"),
        detail.get("label"),
    )
    display = str(detail.get("trait") or detail.get("label") or label)
    return key, display


def _score_identity(score) -> str:
    key, _label = _canonical(getattr(score, "efo", None), getattr(score, "trait", None))
    return key


def build_outcomes(result) -> list[Outcome]:
    """Build ordered condition, age, medicine, and trait groups."""
    grouped: dict[str, Outcome] = {}
    for finding in result.findings:
        detail = finding.detail or {}
        if finding.source in _CLINVAR:
            condition = _condition(finding)
            if not condition:
                continue
            key = condition_key(condition)
            outcome = grouped.setdefault(key, Outcome(key, condition, "condition"))
            outcome.findings.append(finding)
        elif finding.source == "cpic" and detail.get("drug"):
            drug = str(detail["drug"])
            key = medicine_key(drug)
            outcome = grouped.setdefault(key, Outcome(key, drug, "medicine"))
            outcome.findings.append(finding)
        elif finding.source in {"gwas_catalog", "ewas_catalog", "marker_reference"}:
            canonical, label = _trait_identity(finding)
            key = f"trait:{canonical}"
            outcome = grouped.setdefault(key, Outcome(key, label, "trait"))
            outcome.findings.append(finding)
            if finding.source == "marker_reference":
                outcome.reference_groups.extend(detail.get("groups") or [])

    if result.clocks:
        contributions = []
        try:
            from methylask.clocks import top_contributions

            for clock in result.clocks:
                if getattr(clock, "valid", False):
                    contributions.extend(top_contributions(clock))
        except ImportError:
            pass
        grouped["age"] = Outcome(
            key="age",
            label="Epigenetic age",
            kind="age",
            contributions=contributions,
        )

    for score in result.trait_scores:
        key = f"trait:{_score_identity(score)}"
        if key in grouped:
            grouped[key].score = score
        else:
            grouped[key] = Outcome(key, score.trait, "trait", score=score)

    for action in result.actions:
        if action.outcome_key and action.outcome_key in grouped:
            grouped[action.outcome_key].actions.append(action)

    def sort_key(outcome):
        if outcome.kind == "condition":
            promoted = any(getattr(finding, "promoted", False) for finding in outcome.findings)
            return (0, 0 if promoted else 1, outcome.label.casefold())
        if outcome.kind == "age":
            return (1, 0, outcome.label.casefold())
        if outcome.kind == "medicine":
            return (2, 0, outcome.label.casefold())
        strength = 0 if outcome.score else 1 if outcome.reference_groups else 2
        return (3, strength, outcome.label.casefold())

    return sorted(grouped.values(), key=sort_key)

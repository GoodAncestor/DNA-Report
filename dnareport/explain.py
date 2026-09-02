# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""Build a short, checked explanation of one promoted finding.

The prompt contains public facts about one finding and its zygosity class. It
contains no genotype, upload identifier, file name, job identifier, or other
finding. Cache keys use the same bounded facts, so drafts can be reused safely.
"""
from __future__ import annotations

import hashlib
import json
import os
import re


PROMPT_VERSION = "1"

_ZYGOSITY_CLASS = {
    "het": "one altered copy",
    "hom": "two altered copies",
    "hemi": "one copy on the X or Y chromosome",
}


def facts_for(finding) -> dict:
    """Return JSON-safe public facts without personal or upload data."""
    detail = finding.detail or {}
    interpretation = finding.interpretation
    gnomad = detail.get("gnomad") or {}
    frequency = None
    if gnomad.get("ac") is not None and gnomad.get("an"):
        frequency = {
            "ac": gnomad.get("ac"),
            "an": gnomad.get("an"),
            "version": gnomad.get("version"),
        }

    return {
        "marker": finding.marker,
        "gene": detail.get("gene"),
        "condition": interpretation.condition if interpretation else None,
        "condition_ids": list(interpretation.condition_ids) if interpretation else [],
        "classification": detail.get("clinical_significance"),
        "review_status": detail.get("review_status"),
        "gold_stars": detail.get("gold_stars"),
        "zygosity_class": _ZYGOSITY_CLASS.get(
            detail.get("zygosity") or "", "copies not determined"
        ),
        "platform_class": (
            "array"
            if str(detail.get("platform") or "").upper() == "ARRAY"
            else "sequencing"
        ),
        "consequence": detail.get("molecular_consequence"),
        "frequency": frequency,
        "found": interpretation.found if interpretation else "",
        "can_mean": interpretation.can_mean if interpretation else "",
        "how_sure": interpretation.how_sure if interpretation else "",
        "next_step": interpretation.next_step if interpretation else "",
        "chain": [
            {"kind": link.kind, "label": link.label, "id": link.id, "url": link.url}
            for link in (finding.evidence_chain or [])
        ],
        "promoted_reason": finding.promoted_reason,
        "trait": detail.get("trait"),
        "effect_type": detail.get("effect_type"),
        "effect": detail.get("effect"),
        "drug": detail.get("drug"),
    }


def cache_key(facts: dict, backend: str, model: str) -> str:
    """Return a stable key for public facts, backend, model, and prompt version."""
    stable = {
        "marker": facts.get("marker"),
        "gene": facts.get("gene"),
        "condition_ids": sorted(facts.get("condition_ids") or []),
        "classification": facts.get("classification"),
        "gold_stars": facts.get("gold_stars"),
        "zygosity_class": facts.get("zygosity_class"),
        "platform_class": facts.get("platform_class"),
        "consequence": facts.get("consequence"),
        "trait": facts.get("trait"),
        "effect_type": facts.get("effect_type"),
        "effect": facts.get("effect"),
        "drug": facts.get("drug"),
    }
    raw = json.dumps(
        {
            "v": PROMPT_VERSION,
            "backend": backend,
            "model": model,
            "facts": stable,
            "mirror": os.environ.get("DNAREPORT_MIRROR_VERSION", ""),
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


_SYSTEM = """You write a short explanation of one genetic finding for a person with no medical training.
Use only the facts given.
Do not add facts, numbers, gene functions, or conditions that are absent from the facts.
Use plain English.
Write one idea per sentence.
Use 25 words or fewer per sentence.
Use active voice and present tense.
Never say the person has, will get, or will develop a condition.
Make no diagnosis.
Cite each factual claim with its chain label in square brackets.
For example, write [BRCA2] or [ClinVar record].
Use at most 180 words.
Cover the change first.
Then cover the condition, classification meaning, source certainty, and a sensible next step.
Prompt version {version}."""


def build_prompt(facts: dict) -> tuple[str, str]:
    """Return the system instruction and a user turn containing facts only."""
    return _SYSTEM.format(version=PROMPT_VERSION), json.dumps(
        facts, indent=1, sort_keys=True
    )


_FORBIDDEN = (
    "you have ",
    "you will ",
    "you'll ",
    "diagnos",
    "you are at risk",
    "you suffer",
    "guarantee",
    "certainly",
    "definitely",
)
_BANNED = re.compile(
    r"honest|it's not |not a [A-Z]|the point is|worth (noting|being explicit)"
    r"|in other words|crucially|importantly,|notably,|by design|buckle up|dive in",
    re.I,
)
_CITATION = re.compile(r"\[([^\]]{1,80})\]")
MAX_WORDS = 180


def check_draft(text: str, facts: dict) -> str | None:
    """Return a short rejection reason, or ``None`` for an accepted draft."""
    draft = (text or "").strip()
    if not draft:
        return "empty"
    if len(draft.split()) > MAX_WORDS:
        return "too long"

    lowered = draft.lower()
    for phrase in _FORBIDDEN:
        if phrase in lowered:
            return f"forbidden phrase: {phrase.strip()}"

    banned = _BANNED.search(draft)
    if banned:
        return f"banned construction: {banned.group(0)}"

    labels = {link.get("label") for link in (facts.get("chain") or [])}
    citations = _CITATION.findall(draft)
    if not citations:
        return "no citation"
    for citation in citations:
        if citation not in labels:
            return f"unknown citation: {citation}"
    return None

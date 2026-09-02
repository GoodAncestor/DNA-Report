# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""Which findings a reader should read first, decided by published lists.

The rules are few and deliberate. Each promoted finding carries the reason it
was promoted, in the words a reader sees. The 0-10 magnitude plays no part:
it measures review stars and p-values, which is not consequence.

Rules, in order; the first match gives the reason:
  A  ClinVar P/LP in a gene on the ACMG secondary-findings list
  A' ClinVar P/LP in a gene with a ClinGen actionability score of 9 or more
  B  ClinVar P/LP with two or more review stars
  C  CPIC level A (or A/B) drug-response gene
  D  GWAS: carried risk allele, genome-wide significant, large effect
     (OR >= 1.5 or <= 0.67; beta above a reviewed trait-specific cut)
"""
from __future__ import annotations
import json
from pathlib import Path
from geneask.annotators import clingen
from geneask.interpret.lists import acmg_sf, ACMG_SF_VERSION
from geneask.interpret.copy import classify

REASON_A = "Clinicians are told to report changes in this gene ({list})"
REASON_APRIME = "ClinGen rates this gene as actionable"
REASON_B = "Several labs agree this change is pathogenic"
REASON_C = "Changes how some medicines work (CPIC)"
REASON_D = "Large, replicated effect on {trait}"

_CUTS = Path(__file__).parent / "data" / "gwas_beta_cuts.json"
_CLINVAR = {"clinvar", "clinvar_mirror", "clinvar_panel_157"}
_GWS = 5e-8


def _beta_cut(trait: str) -> float | None:
    try:
        rows = json.loads(_CUTS.read_text()).get("rows", {})
    except (OSError, ValueError):
        return None
    v = rows.get((trait or "").strip().lower())
    return float(v) if v is not None else None


def _num(v) -> float | None:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def rule(f) -> tuple[int, str] | None:
    """(rank, reason) for a finding that earns promotion, else None."""
    d = f.detail or {}
    if f.source in _CLINVAR:
        cls = classify(str(d.get("clinical_significance") or ""))
        if cls != "plp":
            return None
        if acmg_sf(str(d.get("gene") or "")):
            return 0, REASON_A.format(list=f"ACMG {ACMG_SF_VERSION}")
        actionability = clingen.actionability_for(str(d.get("gene") or ""))
        score = _num((actionability or {}).get("score"))
        if score is not None and score >= 9:
            return 1, REASON_APRIME
        if int(_num(d.get("gold_stars")) or 0) >= 2:
            return 2, REASON_B
        return None
    if f.source == "cpic":
        if str(d.get("cpic_level") or "").upper() in ("A", "A/B"):
            return 3, REASON_C
        return None
    if f.source == "gwas_catalog":
        p = _num(d.get("p"))
        if d.get("risk_allele_carried") is not True or p is None or p > _GWS:
            return None
        et, ev = d.get("effect_type"), _num(d.get("effect"))
        trait = str(d.get("trait") or "this trait")
        if et == "or" and ev is not None and (ev >= 1.5 or ev <= 0.67):
            return 4, REASON_D.format(trait=trait)
        if et == "beta" and ev is not None:
            cut = _beta_cut(trait)
            if cut is not None and abs(ev) >= cut:
                return 4, REASON_D.format(trait=trait)
    return None


def promote(findings) -> list:
    """Set promoted / promoted_reason on every finding. Returns the promoted
    ones in display order: rule, then review stars, then effect size, then marker."""
    ranked = []
    for f in findings:
        r = rule(f)
        f.promoted = r is not None
        f.promoted_reason = r[1] if r else ""
        if r:
            ranked.append((r[0], f))

    def key(item):
        rank, f = item
        d = f.detail or {}
        return (rank, -(_num(d.get("gold_stars")) or 0), -abs(_num(d.get("effect")) or 0),
                str(f.marker))

    return [f for _, f in sorted(ranked, key=key)]

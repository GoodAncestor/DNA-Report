# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""Build actions only from the four approved evidence sources."""
from __future__ import annotations

from dataclasses import dataclass

from geneask.annotators import clingen
from geneask.interpret.lists import ACMG_SF_URL, ACMG_SF_VERSION, acmg_sf

from .outcomes import condition_key, medicine_key


_CLINVAR = {"clinvar", "clinvar_mirror", "clinvar_panel_157"}


@dataclass
class Action:
    text: str
    why: str
    source_label: str
    url: str | None
    outcome_key: str | None


def _condition(finding) -> str:
    interpretation = getattr(finding, "interpretation", None)
    condition = getattr(interpretation, "condition", None)
    if condition:
        return str(condition)
    conditions = (finding.detail or {}).get("conditions") or []
    return str(conditions[0]) if conditions else ""


def build_actions(result) -> list[Action]:
    """Return actions supported by ClinGen, CPIC, ACMG, or smoking evidence."""
    actions = []
    for finding in result.findings:
        detail = finding.detail or {}
        if finding.source in _CLINVAR and getattr(finding, "promoted", False):
            gene = str(detail.get("gene") or "")
            condition = _condition(finding)
            row = clingen.actionability_for(gene) if gene else None
            if row and str(row.get("intervention") or "").strip():
                actions.append(
                    Action(
                        text=str(row["intervention"]).strip(),
                        why=str(row.get("outcome") or condition or "ClinGen records this intervention."),
                        source_label="ClinGen actionability",
                        url=row.get("report_url") or None,
                        outcome_key=condition_key(condition) if condition else None,
                    )
                )
            if gene and acmg_sf(gene):
                actions.append(
                    Action(
                        text=("Clinicians are told to report changes in this gene; "
                              "a genetics professional can advise on surveillance."),
                        why=f"ACMG includes {gene} on its secondary findings list.",
                        source_label=f"ACMG {ACMG_SF_VERSION}",
                        url=ACMG_SF_URL,
                        outcome_key=condition_key(condition) if condition else None,
                    )
                )
        elif finding.source == "cpic":
            recommendation = str(detail.get("recommendation") or "").strip()
            drug = str(detail.get("drug") or "").strip()
            if recommendation and drug:
                actions.append(
                    Action(
                        text=recommendation,
                        why=f"CPIC publishes guidance for {drug}.",
                        source_label="CPIC",
                        url=finding.link or "https://cpicpgx.org/genes-drugs/",
                        outcome_key=medicine_key(drug),
                    )
                )
        elif (
            finding.source == "marker_reference"
            and finding.marker == "cg05575921"
            and str(detail.get("nearest_group") or "").casefold() == "current smoker"
        ):
            pmid = str((finding.pmids or [""])[0])
            actions.append(
                Action(
                    text=("Stopping smoking is the intervention with the strongest evidence "
                          "for this marker; methylation here recovers over years."),
                    why="The reading sits nearest the published current-smoker group.",
                    source_label="Published smoking exposure reference",
                    url=(f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else finding.link),
                    outcome_key="trait:smoking",
                )
            )

    unique = []
    seen = set()
    for action in actions:
        key = (action.text, action.source_label, action.outcome_key)
        if key not in seen:
            seen.add(key)
            unique.append(action)
    return unique

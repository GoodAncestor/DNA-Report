# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""Serialize a ReportResult to stable JSON — the agent/product-facing view.

Same structured object the HTML renderer consumes, emitted as JSON so agents and
other products get tiers, topics, entities, and stats as fields rather than
scraping HTML. Schema is versioned (schema_version) so downstream code has a
contract.

The linkout URLs are computed the same way the renderer does (marker/gene/
protein resolvers), so JSON and HTML agree on where an entity points.
"""
from __future__ import annotations

# 1.1 adds `magnitude` and `direction` per finding and `by_direction` to the
# summary. Purely additive, so a 1.0 consumer keeps working — but the version
# moves so one can feature-detect rather than probe for keys.
SCHEMA_VERSION = "1.1"


def _magnitude(f) -> float | None:
    """The report's 0-10 interest score, or None if bio-core isn't importable
    (the CLI can serialize without the renderer installed)."""
    try:
        from biocore.report.render import magnitude
    except ImportError:
        return None
    return magnitude(f)


def _direction(f) -> str | None:
    """Direction of effect where the SOURCE asserts one ("adverse", "benign",
    "protective", "actionable"), else "" — never a valence we invented. See
    biocore.report.render.direction for the precedence rules."""
    try:
        from biocore.report.render import direction
    except ImportError:
        return None
    return direction(f)


def _finding_json(f, marker_url) -> dict:
    d = f.detail or {}
    gene = d.get("gene")
    protein = d.get("protein")
    links = {}
    mu = marker_url(f.marker) if marker_url else None
    if mu:
        links["marker"] = mu
    if gene and gene != "?":
        g0 = str(gene).replace(",", ";").split(";")[0].strip()
        links["gene"] = f"https://www.ncbi.nlm.nih.gov/gene/?term={g0}%5Bsym%5D"
    if protein:
        links["protein"] = f"https://www.uniprot.org/uniprotkb/{protein}"
    if f.link:
        links["source"] = f.link
    return {
        "marker": f.marker,
        "description": f.description,
        "tier": f.tier.value,
        # The two ranking fields the HTML report shows. An agent consuming this
        # would otherwise have to re-derive them from `stats` and re-implement
        # the tier banding and ClinVar precedence rules to sort or triage.
        "magnitude": _magnitude(f),
        "direction": _direction(f),
        "topic": d.get("topic", "other"),
        "gene": gene,
        "protein": protein,
        "trait": d.get("trait"),
        "source": f.source,
        "pmids": list(f.pmids or []),
        "stats": {k: d.get(k) for k in ("beta", "se", "p", "n", "tissue")
                  if d.get(k) not in (None, "")},
        "links": links,
    }


def _clock_json(cl) -> dict:
    return {
        "clock": cl.clock,
        "age": cl.age,
        "valid": getattr(cl, "valid", cl.age is not None),
        "trained_tissue": getattr(cl, "trained_tissue", None),
        "tissue_mismatch": getattr(cl, "tissue_mismatch", False),
        "implausible": getattr(cl, "implausible", False),
        "coverage": getattr(cl, "coverage", None),
        "note": cl.note,
    }


def result_to_json(result, marker_url=None) -> dict:
    """Full structured result. Findings are grouped by marker (mirroring the
    human report), each marker carrying its findings sorted strongest-first is
    left to the client — here they are listed flat with all fields."""
    findings = [_finding_json(f, marker_url) for f in result.findings]
    # topic + tier tallies so a consumer can summarize without re-counting
    from collections import Counter
    topics = Counter(f["topic"] for f in findings)
    tiers = Counter(f["tier"] for f in findings)
    # only the findings a source actually classified; unset ones are not counted
    # as a category, because "no direction stated" is an absence, not a verdict
    directions = Counter(f["direction"] for f in findings if f.get("direction"))
    return {
        "schema_version": SCHEMA_VERSION,
        "input_kind": getattr(result.kind, "value", str(result.kind)),
        "tissue": result.tissue,
        "engines": list(result.engines),
        "summary": {
            "n_findings": len(findings),
            "n_markers": len({f["marker"] for f in findings}),
            "by_topic": dict(topics),
            "by_tier": dict(tiers),
            "by_direction": dict(directions),
        },
        "clocks": [_clock_json(c) for c in result.clocks],
        "findings": findings,
        "notes": list(result.notes or []),
    }

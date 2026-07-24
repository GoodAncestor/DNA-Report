"""Serialize a ReportResult to stable JSON — the agent/product-facing view.

Same structured object the HTML renderer consumes, emitted as JSON so agents and
other products get tiers, topics, entities, and stats as fields rather than
scraping HTML. Schema is versioned (schema_version) so downstream code has a
contract.

The linkout URLs are computed the same way the renderer does (marker/gene/
protein resolvers), so JSON and HTML agree on where an entity points.
"""
from __future__ import annotations

SCHEMA_VERSION = "1.0"


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
        },
        "clocks": [_clock_json(c) for c in result.clocks],
        "findings": findings,
        "notes": list(result.notes or []),
    }

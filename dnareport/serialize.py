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
import os

# 1.1 adds `magnitude` and `direction` per finding and `by_direction` to the
# summary. 1.2 adds `scan_stats`: what was scanned and what was left out. Without
# it an agent cannot tell a bounded report from a complete one — it would read
# 1,000 GWAS associations as all of them, which is the same failure as a human
# reader who never sees the truncation notice. Purely additive, so a 1.0 consumer
# keeps working — the version moves so one can feature-detect rather than probe.
SCHEMA_VERSION = "1.2"


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


#: How many of the strongest findings the summary names. Enough to orient a
#: reader (or a model) without becoming the report again.
SUMMARY_TOP_N = int(os.environ.get("DNAREPORT_SUMMARY_TOP_N", "10"))


def report_summary(result, *, top_n: int = None) -> dict:
    """A short, structured account of the whole report.

    This exists because the report is often far too big to read whole — a consumer
    array yields hundreds of thousands of associations — and because the most
    likely thing anyone does with the export is hand it to a model and ask what
    matters. Without a summary the model sees whatever fits in its context, which
    is the head of an arbitrarily ordered list.

    The strongest findings are ranked by bio-core's existing `magnitude` score, the
    same one the HTML report uses. That is deliberate: "strongest evidence first"
    is a claim this system can defend, and inventing a separate what-matters-most
    ranking here would be a clinical judgement dressed as a summary field.

    `bounded` and `limits` say whether the report is a complete account or a
    truncated one, so a reader that only ever sees this summary is still told.
    """
    top_n = SUMMARY_TOP_N if top_n is None else top_n
    # getattr throughout: this is also called with the lighter result objects the
    # CLI and the tests build, which carry findings but not every field.
    st = getattr(result, "scan_stats", None) or {}
    limits = dict(st.get("limits") or {})
    findings = list(getattr(result, "findings", None) or [])

    tiers: dict[str, int] = {}
    topics: dict[str, int] = {}
    for f in findings:
        tiers[f.tier.value] = tiers.get(f.tier.value, 0) + 1
        t = (f.detail or {}).get("topic", "other")
        topics[t] = topics.get(t, 0) + 1

    top = []
    for f in sorted(findings, key=lambda f: _magnitude(f) or 0.0,
                    reverse=True)[:top_n]:
        d = f.detail or {}
        top.append({"marker": f.marker, "gene": d.get("gene"),
                    "description": f.description, "tier": f.tier.value,
                    "magnitude": _magnitude(f), "source": f.source})

    return {
        "input_kind": getattr(getattr(result, "kind", ""), "value",
                              str(getattr(result, "kind", ""))),
        "n_findings": len(findings),
        "n_markers": len({f.marker for f in findings}),
        "by_tier": tiers,
        "by_topic": topics,
        "clocks": [{"clock": c.clock, "age": c.age,
                    "valid": getattr(c, "valid", c.age is not None)}
                   for c in (getattr(result, "clocks", None) or [])],
        # The two fields a consumer must read before trusting any count above.
        "bounded": bool(limits),
        "limits": limits,
        "databases": list(st.get("local_dbs_queried") or []),
        "markers_scanned": st.get("markers_scanned"),
        "strongest": top,
        "caveats": list(getattr(result, "notes", None) or []),
    }



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
        # ONE summary. It carries the counts AND the whole-report account —
        # `bounded`, `limits`, `strongest` — because two summary blocks in one
        # document are two things that can disagree about the same genome.
        "summary": {
            "n_findings": len(findings),
            "n_markers": len({f["marker"] for f in findings}),
            "by_topic": dict(topics),
            "by_tier": dict(tiers),
            "by_direction": dict(directions),
            **{k: v for k, v in report_summary(result).items()
               if k in ("bounded", "limits", "strongest", "databases",
                        "markers_scanned", "clocks", "input_kind")},
        },
        "clocks": [_clock_json(c) for c in result.clocks],
        "findings": findings,
        "notes": list(result.notes or []),
        "scan_stats": dict(getattr(result, "scan_stats", None) or {}),
    }

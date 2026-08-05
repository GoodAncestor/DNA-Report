# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""The report in the two formats that are not a web page.

**Markdown** is for a person who wants to keep, diff, or paste their results
somewhere that is not a browser — a notes app, a document for a clinician, a
repository. The HTML report is a designed artefact and does not survive being
copied out of; this does.

**JSON** is for someone else's software, including agents. It is the same
structured object the renderer consumes, so an agent reads tiers, directions and
magnitudes as fields instead of scraping a document — and it carries `scan_stats`
and `notes`, which is how a consumer can tell a bounded report from a complete
one. A machine reader that cannot see the truncation notice is the same failure
as a human reader who cannot: it reports 1,000 associations as though they were
all of them.

Both are generated from the SAME ReportResult as the HTML, at the same moment, by
the worker. Regenerating them later from a different run would let the three
disagree about a person's genome, which is worse than not offering them.
"""
from __future__ import annotations
import json
from pathlib import Path

_TIER_ORDER = {"robust": 0, "moderate": 1, "speculative": 2, "unknown": 3}


def report_json(result, marker_url=None) -> str:
    """The structured report, as a JSON string."""
    from .serialize import result_to_json
    return json.dumps(result_to_json(result, marker_url=marker_url),
                      indent=2, sort_keys=False)


def _fmt_stat(k: str, v) -> str:
    if isinstance(v, int) and abs(v) >= 1000:
        return f"{k}: {v:,}"
    return f"{k}: {v}"


def _tier_of(f) -> str:
    return getattr(f.tier, "value", str(f.tier))


def _category_of(f) -> str:
    cats = getattr(f, "categories", None) or []
    for c in cats:
        return getattr(c, "value", str(c)).title()
    return "Other"


def report_markdown(result, *, filename: str = "", title: str = "DNA-Report",
                    disclaimer_path: str | None = None) -> str:
    """The report as Markdown.

    Ordering matches the HTML: what was scanned first, then anything that limits
    how the results should be read, then the results themselves strongest-first,
    then the disclaimer. The notes come BEFORE the findings deliberately — a cap
    or a screen that did not run changes what the list below means, and a reader
    scrolling a long document would meet it last or not at all.
    """
    out: list[str] = [f"# {title}", ""]
    kind = getattr(result.kind, "value", str(result.kind))
    head = [f"Input type: `{kind}`"]
    if filename:
        head.append(f"File: `{filename}`")
    head.append(f"Findings: {len(result.findings):,}")
    if result.tissue:
        head.append(f"Tissue: {result.tissue}")
    out += ["  \n".join(head), ""]

    st = result.scan_stats or {}
    if st:
        out += ["## What was scanned", ""]
        for key, label in (("input_bytes", "Input size (bytes)"),
                           ("markers_scanned", "Markers scanned"),
                           ("findings_total", "Findings"),
                           ("classified", "Classified"),
                           ("uncertain", "Uncertain"),
                           ("reference_variants_scanned",
                            "Reference variants covered")):
            if st.get(key) is not None:
                out.append(f"- {_fmt_stat(label, st[key])}")
        for key, label in (("local_dbs_queried", "Reference databases"),
                           ("live_apis_called", "Live APIs")):
            vals = st.get(key) or []
            if vals:
                out.append(f"- {label}: {', '.join(vals)}")
        out.append("")

    if result.notes:
        # Headed "how to read this" rather than "notes": these state what was
        # capped, lifted, or skipped, and a reader needs them as qualifications on
        # the results rather than as trivia at the end.
        out += ["## How to read this", ""]
        out += [f"- {n}" for n in result.notes]
        out.append("")

    if result.clocks:
        out += ["## Epigenetic age", "",
                "| Clock | Age | Trained on | Note |", "| --- | --- | --- | --- |"]
        for c in result.clocks:
            age = "not computed" if c.age is None else f"{c.age:.1f}"
            note = (c.note or "").replace("|", "\\|")
            if getattr(c, "tissue_mismatch", False):
                note = (note + " " if note else "") + "(trained on a different tissue)"
            out.append(f"| {c.clock} | {age} | "
                       f"{getattr(c, 'trained_tissue', None) or '—'} | {note or '—'} |")
        out.append("")

    out += ["## Findings", ""]
    if not result.findings:
        out += ["No marker in this file crossed the threshold for a finding we "
                "would stand behind. That is a result, not a failure — and it is "
                "not a clean bill of health. See the notes above for anything "
                "that limited the scan.", ""]
    else:
        by_cat: dict[str, list] = {}
        for f in result.findings:
            by_cat.setdefault(_category_of(f), []).append(f)
        for cat in sorted(by_cat):
            out += [f"### {cat}", ""]
            fs = sorted(by_cat[cat],
                        key=lambda f: (_TIER_ORDER.get(_tier_of(f), 9),
                                       str(f.marker)))
            for f in fs:
                d = f.detail or {}
                bits = [f"tier: {_tier_of(f)}"]
                if d.get("gene") and d["gene"] != "?":
                    bits.append(f"gene: {d['gene']}")
                for k in ("p", "beta", "n"):
                    if d.get(k) not in (None, ""):
                        bits.append(f"{k}: {d[k]}")
                # Gene first, identifier in a code span. A marker id is not always
                # short — an indel's is the whole allele, measured at 330
                # characters on the demo genome — and in bold at the head of the
                # line that is a wall of letters where the reader is looking for
                # the gene name. The full id is kept, never abbreviated: this is
                # the file someone greps or hands to a clinician.
                gene = d.get("gene")
                lead = f"**{gene}**" if gene and gene != "?" else "**finding**"
                out.append(f"- {lead} — {f.description}")
                out.append(f"  <br>`{f.marker}`")
                out.append(f"  <br>_{' · '.join(bits)}_")
                if f.pmids:
                    out.append("  <br>PubMed: " + ", ".join(
                        f"[{p}](https://pubmed.ncbi.nlm.nih.gov/{p}/)"
                        for p in f.pmids))
            out.append("")

    path = disclaimer_path
    if path is None:
        from .orchestrate import _disclaimer_path
        path = _disclaimer_path()
    try:
        text = Path(path).read_text().strip()
    except OSError:
        text = ""
    if text:
        # The disclaimer travels with the report. An exported file is the copy most
        # likely to be forwarded to someone who never saw the page it came from.
        out += ["---", "", text, ""]
    return "\n".join(out).rstrip() + "\n"

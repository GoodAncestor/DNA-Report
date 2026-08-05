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

#: Bumped whenever the Markdown's STRUCTURE changes — headings, front-matter keys,
#: ordering. Prose edits do not move it. It exists so an agent parsing this file
#: can refuse a shape it does not know instead of silently mis-reading one.
MARKDOWN_FORMAT_VERSION = "1.0"


def report_json(result, marker_url=None) -> str:
    """The structured report, as a JSON string.

    The `summary` block carries the whole-report account, including whether the
    report is bounded — see serialize.report_summary.
    """
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
    from .serialize import report_summary
    sm = report_summary(result)
    kind = getattr(result.kind, "value", str(result.kind))

    # YAML front matter. Agents are a first-class reader of this file, and prose
    # headings are not a contract — reword one and every consumer breaks silently.
    # These keys are, so a reader can get the shape of the report, and crucially
    # whether it is COMPLETE, without parsing a word of the body.
    fm = [
        "---",
        f"format: dna-report-markdown/{MARKDOWN_FORMAT_VERSION}",
        f"input_kind: {kind}",
        f"findings: {len(result.findings)}",
        f"bounded: {'true' if sm['bounded'] else 'false'}",
    ]
    if filename:
        fm.append(f"file: {json.dumps(filename)}")
    if result.tissue:
        fm.append(f"tissue: {result.tissue}")
    if sm["limits"]:
        fm.append("limits:")
        for src, lim in sorted(sm["limits"].items()):
            fm.append(f"  {src}: {{shown: {lim.get('shown')}, "
                      f"found: {lim.get('found')}}}")
    fm += ["---", ""]

    out: list[str] = fm + [f"# {title}", ""]
    head = [f"Input type: `{kind}`"]
    if filename:
        head.append(f"File: `{filename}`")
    head.append(f"Findings: {len(result.findings):,}")
    if result.tissue:
        head.append(f"Tissue: {result.tissue}")
    out += ["  \n".join(head), ""]

    # The summary comes first because this file is routinely longer than anything
    # that will be read whole — by a person or by a model with a context limit.
    out += ["## Summary", ""]
    if sm["bounded"]:
        for src, lim in sorted(sm["limits"].items()):
            out.append(f"> **This report is bounded.** It shows "
                       f"{lim.get('shown'):,} of {lim.get('found'):,} "
                       f"{src.replace('_', ' ')} associations — the strongest by "
                       f"p-value. Do not read the counts below as a complete "
                       f"account.")
        out.append("")
    if sm["by_tier"]:
        out.append("- By strength of evidence: " + ", ".join(
            f"{k} {v:,}" for k, v in sorted(sm["by_tier"].items())))
    if sm["by_topic"]:
        out.append("- By topic: " + ", ".join(
            f"{k} {v:,}" for k, v in sorted(sm["by_topic"].items())))
    if sm["databases"]:
        out.append("- Screened against: " + ", ".join(sm["databases"]))
    out.append("")
    if sm["strongest"]:
        # Ranked by the same score the report itself uses. This is "strongest
        # evidence", NOT "most important for you" — the second is a clinical
        # judgement and nothing here is entitled to make it.
        out += ["**Strongest evidence in this report** (by the same ranking the "
                "report uses — this is not a clinical priority order):", ""]
        for t in sm["strongest"]:
            gene = f"{t['gene']} — " if t.get("gene") and t["gene"] != "?" else ""
            out.append(f"1. {gene}{t['description']} "
                       f"_(tier {t['tier']}, magnitude {t['magnitude']})_")
        out.append("")

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

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""Compose a report document — the one place a ReportResult becomes HTML.

Three callers render reports: the inline upload path in `web.py`, the batch
worker that writes to R2 for `/result`, and the CLI. They used to render
DIFFERENTLY. The worker called bio-core's finding renderer directly, so a queued
report silently lacked the scan-notes section, the trait glossary and the
highlights block that an inline report of the very same file showed — the reader
got a different document depending on how large their upload happened to be, and
nothing anywhere said so.

Worse, the notes were the part that went missing. A queued scan that could not
read a file, or that capped a result, produced notes explaining exactly that, and
then rendered a document with no place to put them. That is how "we could not
read your VCF" became indistinguishable from "we found nothing in your VCF".

So composition lives here, above the engines and below the transports. Anything
added to a report goes in this module, and every caller gets it.
"""
from __future__ import annotations
import os
import shutil
import tempfile
from pathlib import Path

from .detect import InputKind
from .orchestrate import render

# How each recognised upload is described to the person who sent it.
KIND_LABEL = {
    InputKind.TWENTYTHREE_AND_ME: "23andMe raw genotype export",
    InputKind.ARRAY_GENOTYPE: "Consumer genotype export",
    InputKind.VCF: "VCF genome",
    InputKind.BEDMETHYL: "bedMethyl methylation calls",
    InputKind.BETA_MATRIX: "Methylation beta-value table",
    InputKind.IDAT: "Illumina IDAT array file",
    InputKind.MODBAM: "ONT modBAM",
    InputKind.UNKNOWN: "Unrecognised",
}


def display_notes(result, scratch: str = "", filename: str = "") -> list[str]:
    """Engine notes, with server-side scratch paths replaced by the user's own
    filename. Engines interpolate the path they were handed, which would
    otherwise show the temp directory layout to whoever reads the report."""
    from .uploads import sanitize_note
    out = []
    for n in (result.notes or []):
        n = sanitize_note(n, scratch, filename) if (scratch or filename) else n
        if n:
            out.append(n)
    return out


def _render_findings(result, out_path: str) -> str:
    """The full report: highlights + bio-core's findings/disclaimer + notes +
    glossary.

    Reference positions are pulled OUT of the findings list before bio-core
    renders it — they are the headline of a methylome report, not one card among
    several dozen. The swap is restored afterwards so the JSON surface, which
    does not go through here, still carries them as ordinary findings.
    """
    from .highlights import (split_reference_findings, split_display_findings,
                             highlights_html)

    _, rest = split_reference_findings(list(result.findings or []))
    rest, _withheld = split_display_findings(rest)
    original = result.findings
    result.findings = rest
    try:
        render(result, out_path)                   # bio-core findings + disclaimer
    finally:
        result.findings = original

    body = Path(out_path).read_text()

    def _append(html: str):
        """Put a section at the end of the document, inside <body>."""
        nonlocal body
        if not html:
            return
        if "</body>" in body:
            body = body.replace("</body>", html + "\n</body>", 1)
        else:
            body += html

    # What the scan covered and what it left out. The engines have always
    # produced these notes and the JSON API has always returned them; the HTML
    # report showed none of them, so a bounded report — 1,000 GWAS associations
    # of 442,712 — was indistinguishable from a complete one to the only
    # audience that reads the HTML.
    from .scan_notes import notes_html
    _append(notes_html(result))

    # Glossary goes at the END, after the findings it explains — the copy is
    # per-trait while findings are per-marker, so it is written once here and
    # each finding links to its entry rather than restating it.
    from .glossary import glossary_html
    _append(glossary_html(rest))

    top = highlights_html(result)
    if top:
        # inject the highlights section at the top of the document body
        if "<body>" in body:
            body = body.replace("<body>", "<body>\n" + top, 1)
        else:
            body = top + body
    # Write unconditionally. This used to happen only when there WERE highlights,
    # so on a report without them the glossary was built and then thrown away —
    # the caller re-reads this file, and never saw it.
    Path(out_path).write_text(body)
    return out_path


def render_report(result, out_path: str, *, filename: str = "",
                  scratch: str = "", kind_label: str | None = None) -> str:
    """Write the document for `result` to `out_path`; return `out_path`.

    A result with nothing in it is still a result. It gets the empty-report page —
    which says what was recognised, and carries the notes explaining why a valid
    file can come back empty — rather than a findings report with no findings, and
    never nothing at all. A worker that skipped writing on an empty result left
    the user's claim link refreshing for ever against an object that was never
    uploaded.
    """
    if not result.findings and not result.clocks:
        from . import pages
        label = kind_label or KIND_LABEL.get(result.kind,
                                             getattr(result.kind, "value", "Unrecognised"))
        Path(out_path).write_text(pages.empty_report_page(
            kind_label=label, filename=filename,
            notes=display_notes(result, scratch, filename)))
        return out_path
    return _render_findings(result, out_path)


def report_html(result, *, filename: str = "", scratch: str = "",
                kind_label: str | None = None) -> str:
    """The same document as a string, for a caller that returns it in a response
    body rather than storing it."""
    tmp = tempfile.mkdtemp(prefix="dnr-render-")
    try:
        out = render_report(result, os.path.join(tmp, "report.html"),
                            filename=filename, scratch=scratch,
                            kind_label=kind_label)
        return Path(out).read_text()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

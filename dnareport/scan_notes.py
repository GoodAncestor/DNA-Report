# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""Render the analysis notes into the HTML report.

The engines already produce these — what was lifted, what was capped, which
annotator added what — and the JSON API has always returned them. The HTML
report did not show them anywhere, so a reader was told none of it.

That matters most for the notes that describe a LIMIT. A report that shows the
1,000 strongest of 442,712 GWAS associations and says nothing reads as the
complete set; the reader cannot tell a bounded report from an exhaustive one.
The same applies to unliftable sites and to any annotator that hit a cap.
"""
from __future__ import annotations
import html as _html


def _e(s) -> str:
    return _html.escape(str(s))


# Notes that describe a limit or an omission come first, because they change how
# everything below them should be read. The rest are provenance and can follow.
_LIMIT_WORDS = ("showing the", "unliftable", "cap", "capped", "omitted",
                "truncat", "limit", "only the first")


def _is_limit(note: str) -> bool:
    n = note.lower()
    return any(w in n for w in _LIMIT_WORDS)


def notes_html(result) -> str:
    """The 'what this scan did and did not cover' section, or "" when empty."""
    notes = [n for n in (getattr(result, "notes", None) or []) if str(n).strip()]
    if not notes:
        return ""
    limits = [n for n in notes if _is_limit(n)]
    rest = [n for n in notes if not _is_limit(n)]
    items = "".join(f'<li class="lim">{_e(n)}</li>' for n in limits)
    items += "".join(f"<li>{_e(n)}</li>" for n in rest)
    return f"""<section class="scannotes">
  <h2>What this scan covered</h2>
  <p class="sn-lede">How your file was read, and anything the report left out.</p>
  <ul>{items}</ul>
{_STYLE}
</section>"""


_STYLE = """<style>
.scannotes{margin:34px 0 40px;padding:0 0 6px}
.scannotes h2{font-family:var(--serif);font-size:22px;letter-spacing:-.01em;
  margin:0 0 4px;border-bottom:2px solid var(--ink);padding-bottom:8px}
.scannotes .sn-lede{color:var(--mut);font-size:14px;margin:8px 0 12px}
.scannotes ul{list-style:none;margin:0;padding:0}
.scannotes li{font-size:14px;line-height:1.5;color:var(--mut);
  padding:8px 0 8px 14px;border-left:2px solid var(--line);margin-bottom:6px}
.scannotes li.lim{color:var(--ink);border-left-color:var(--accent);
  background:var(--accent-soft);padding-right:10px}
</style>"""

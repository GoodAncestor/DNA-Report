"""Trait glossary — each trait in the report explained once, at the end.

Report data has two grains. Findings are per MARKER; the explanation of what a
trait means is per TRAIT. Rendering trait copy on each finding therefore restates
one thing dozens of times: on the blood demo "age" alone appears 127 times, and
inlining the copy would add 420,000 characters against 92,000 for a glossary.

So the copy lives here once, and each finding links to its entry. The anchor id
comes from bio-core's glossary_anchor() — one function owns it, so the link and
the entry cannot drift apart.

Pure rendering, no web framework, testable without the server stack.
"""
from __future__ import annotations
import html as _html

from biocore.report.render import glossary_anchor

_SECTIONS = (
    ("what_it_is", "What it is"),
    ("what_an_association_means", "What an association means"),
    ("what_it_is_not", "What it is not"),
    ("typical_evidence", "Evidence"),
)


def _copy_table() -> dict:
    try:
        from methylask.traits import _copy_table as t
        return t()
    except Exception:
        return {}


def referenced_keys(findings: list) -> list:
    """Copy keys actually used by this report, in stable order.

    Only traits present in the report are explained — a glossary of every trait
    in the catalog would be 6,515 entries the reader never encountered.
    """
    seen = []
    for f in findings:
        k = (f.detail or {}).get("copy_key")
        if k and k not in seen:
            seen.append(k)
    return seen


def glossary_html(findings: list) -> str:
    """The glossary section, or "" when no finding in the report has copy."""
    table = _copy_table()
    keys = [k for k in referenced_keys(findings) if k in table]
    if not keys:
        return ""

    # protein-level covers ~69% of associations and is a class rather than a
    # trait, so it reads first; the rest follow alphabetically by label.
    keys.sort(key=lambda k: (k != "_protein_level",
                             str(table[k].get("label", k)).lower()))

    entries = []
    for k in keys:
        v = table[k]
        body = "".join(
            f"<div class='gsec'><span class='glab'>{_html.escape(lab)}</span>"
            f"<p>{_html.escape(v.get(field, ''))}</p></div>"
            for field, lab in _SECTIONS if v.get(field))
        pm = ", ".join(
            f"<a href='https://pubmed.ncbi.nlm.nih.gov/{_html.escape(str(p))}/'>"
            f"{_html.escape(str(p))}</a>" for p in (v.get("pmids") or []))
        cite = f"<div class='gcite'>sources: {pm}</div>" if pm else ""
        # Collapsed by default: expanded, 23 entries is ~46KB of prose in front of
        # a reader who came for their own results. The summary line keeps the list
        # scannable — you can see which traits are explained without reading any.
        entries.append(
            f"<details class='gentry' id='{_html.escape(glossary_anchor(k))}'>"
            f"<summary>{_html.escape(str(v.get('label', k)))}</summary>"
            f"<div class='gbody'>{body}{cite}</div></details>")

    return f"""<section class="glossary">
  <h2>What these traits mean</h2>
  <p class="glede">Every trait named in this report, explained once. These describe
  what researchers measured and what an association does and does not support —
  none of them is a statement about you.</p>
  {''.join(entries)}
{_OPEN_ON_TARGET}
{_STYLE}
</section>"""


# A finding links to #trait-age. Browsers do not reliably open a closed
# <details> when navigating to an id inside it, so the link would appear to do
# nothing — open it explicitly on arrival and on any later hash change.
_OPEN_ON_TARGET = """<script>
(function(){
  function openTarget(){
    var h = location.hash && location.hash.slice(1);
    if(!h) return;
    var el = document.getElementById(h);
    if(el && el.tagName === 'DETAILS'){ el.open = true;
      el.scrollIntoView({block:'center'}); }
  }
  window.addEventListener('hashchange', openTarget);
  document.addEventListener('DOMContentLoaded', openTarget);
  openTarget();
})();
</script>"""

_STYLE = """<style>
.glossary{margin:44px 0 0;padding-top:22px;border-top:2px solid var(--ink)}
.glossary h2{font-family:var(--serif);font-size:26px;margin:0 0 6px}
.glossary .glede{color:var(--mut);font-size:14px;max-width:62ch;margin:0 0 20px}
.glossary .gentry{border-top:1px solid var(--line)}
.glossary .gentry:first-of-type{border-top:none}
.glossary .gentry>summary{font-family:var(--serif);font-size:17px;padding:11px 0;
  cursor:pointer;list-style:none;display:flex;align-items:center;gap:9px}
.glossary .gentry>summary::-webkit-details-marker{display:none}
.glossary .gentry>summary::before{content:"+";font-family:var(--mono);font-size:13px;
  color:var(--accent);width:11px;flex:none}
.glossary .gentry[open]>summary::before{content:"\2013"}
.glossary .gentry>summary:hover{color:var(--accent)}
.glossary .gbody{padding:2px 0 14px 20px}
.glossary .gentry:target{background:var(--accent-soft);border-radius:8px;
  padding-left:13px;padding-right:13px;margin:0 -13px}
.glossary .gsec{margin:0 0 9px}
.glossary .glab{display:block;font-family:var(--mono);font-size:10px;
  text-transform:uppercase;letter-spacing:.1em;color:var(--faint);margin-bottom:2px}
.glossary .gsec p{margin:0;font-size:14px;line-height:1.6;color:var(--mut);
  max-width:74ch}
.glossary .gcite{font-size:12px;color:var(--faint);margin-top:8px}
.glossary .gcite a{color:var(--faint)}
@media print{.glossary .gentry{break-inside:avoid}}
</style>"""

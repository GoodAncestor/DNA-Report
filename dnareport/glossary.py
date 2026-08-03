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


# Trait links open a popover in place. Jumping to the glossary would strand the
# reader at the end of a 40-card report with no way back to where they were.
#
# Progressive enhancement: the anchor keeps its href, so without JS (or in a
# printed copy) the link still lands on the glossary entry, which opens on
# arrival. The popover CLONES the entry already in the document — no innerHTML
# and no second copy of 47KB of prose.
_OPEN_ON_TARGET = """<div id="glosspop" class="glosspop" hidden>
  <button class="glosspop-x" aria-label="Close">&times;</button>
  <div class="glosspop-body"></div>
</div>
<script>
(function(){
  var pop = document.getElementById('glosspop');
  if(!pop) return;
  var body = pop.querySelector('.glosspop-body');
  var last = null;

  function close(){
    pop.hidden = true;
    if(last){ try{ last.focus(); }catch(e){} last = null; }
  }
  function place(a){
    var r = a.getBoundingClientRect();
    pop.hidden = false;                       // measure only once displayed
    var w = pop.offsetWidth, h = pop.offsetHeight;
    var left = Math.min(Math.max(8, r.left + window.scrollX),
                        window.scrollX + document.documentElement.clientWidth - w - 8);
    var below = r.bottom + window.scrollY + 8;
    var above = r.top + window.scrollY - h - 8;
    var room = window.scrollY + window.innerHeight - below;
    pop.style.left = left + 'px';
    // flip above the word when there is no room beneath it
    pop.style.top = (room < h + 16 && above > window.scrollY ? above : below) + 'px';
  }
  function open(a){
    var id = (a.getAttribute('href')||'').replace(/^#/,'');
    var entry = id && document.getElementById(id);
    if(!entry) return false;                  // fall through to the plain jump
    var title = entry.querySelector('summary');
    var content = entry.querySelector('.gbody');
    if(!content) return false;
    while(body.firstChild) body.removeChild(body.firstChild);
    var h4 = document.createElement('h4');
    h4.textContent = title ? title.textContent : '';
    body.appendChild(h4);
    body.appendChild(content.cloneNode(true));
    last = a;
    place(a);
    pop.querySelector('.glosspop-x').focus();
    return true;
  }

  document.addEventListener('click', function(e){
    var a = e.target.closest && e.target.closest('a.glossword');
    if(a){ if(open(a)) e.preventDefault(); return; }
    if(!pop.hidden && !pop.contains(e.target)) close();
  });
  document.addEventListener('keydown', function(e){
    if(e.key === 'Escape' && !pop.hidden) close();
  });
  window.addEventListener('resize', close);

  // Direct hash navigation (no-JS fallback, or a shared link): browsers do not
  // reliably open a closed <details> when the target sits inside it.
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

  // Print/save must carry the definitions. The CSS override alone is not enough:
  // some print engines re-derive layout from the element's real `open` state
  // rather than from computed style, so the entries are opened for real and the
  // reader's own open/closed choices restored afterwards — the same approach the
  // truncated finding sections already take.
  var entries = [].slice.call(document.querySelectorAll('.glossary details.gentry'));
  window.addEventListener('beforeprint', function(){
    entries.forEach(function(d){ d.dataset.wasOpen = d.open ? '1' : ''; d.open = true; });
  });
  window.addEventListener('afterprint', function(){
    entries.forEach(function(d){ d.open = !!d.dataset.wasOpen; });
  });
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
/* popover: the primary way copy is read; the glossary below is the fallback,
   the printable reference, and the browsable index */
.glosspop{position:absolute;z-index:60;max-width:min(430px,calc(100vw - 16px));
  max-height:min(62vh,540px);overflow:auto;background:var(--card);
  border:1px solid var(--hair);border-radius:10px;padding:15px 17px 13px;
  box-shadow:0 10px 34px rgba(0,0,0,.17)}
.glosspop[hidden]{display:none}
.glosspop h4{font-family:var(--serif);font-size:17px;margin:0 26px 9px 0;
  letter-spacing:-.005em}
.glosspop-x{position:absolute;top:7px;right:9px;border:0;background:none;
  font-size:20px;line-height:1;color:var(--faint);cursor:pointer;padding:2px 5px}
.glosspop-x:hover{color:var(--ink)}
.glosspop .gbody{padding:0}
/* Collapsing is a screen convenience. A closed <details> prints as a heading and
   nothing else, so saving the report would produce 23 trait names and none of
   the copy explaining them — the glossary's whole reason to exist. Print gets
   the complete document, matching how truncated finding sections already behave. */
@media print{
  .glossary .gentry{break-inside:avoid}
  .glosspop{display:none}
  .glossary .gentry:not([open])>*:not(summary){display:block !important}
  .glossary .gentry>summary::before{content:""}
}
</style>"""

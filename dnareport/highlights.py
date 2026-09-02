"""The biological snapshot: epigenetic age and cited exposure markers, as one section.

Design notes, since this is the only hand-composed part of the report:

* It is ONE section, not a clocks block with cards bolted underneath. Clock tiles
  and exposure cards share a grid, a type scale and a restraint vocabulary — a
  greyed tile and a withheld card are the same gesture, so "we are not telling you
  this, and here is why" reads as deliberate rather than as an error.
* It borrows the host report's tokens (--paper/--ink/--serif/--accent and the tier
  colours) instead of importing a second design language, so the injected section
  belongs to the document it lands in and follows it into dark mode.
* The reading scale is the one piece of real invention. It plots each published
  group on a 0-1 beta axis with the sample's reading above it. It needs no
  standard deviation, no percentile and no cohort — only the numbers the papers
  actually printed — which is why it can be shown honestly when a sigma cannot.

Pure rendering, no web framework, so it is testable without the server stack.
"""
from __future__ import annotations
import html as _html

REFERENCE_SOURCE = "marker_reference"


def split_reference_findings(findings: list):
    """(highlights, rest). Reference positions render at the top, not in-list."""
    highlights = [f for f in findings if f.source == REFERENCE_SOURCE]
    rest = [f for f in findings if f.source != REFERENCE_SOURCE]
    return highlights, rest


# Trait classes withheld from the rendered report. Only "covariate" — a study
# design variable that describes the sample rather than the person. "other" is
# NOT here: those concepts were flagged ambiguous by curation, not confirmed, and
# suppressing on an unconfirmed guess loses real findings.
_WITHHELD_TRAIT_CLASSES = {"covariate"}


def split_display_findings(findings: list):
    """(keep, dropped). Findings whose trait is a study-design variable are not
    about the reader, so they are withheld from the report.

    Findings with no trait_class are KEPT: only ~400 of 6,515 catalog traits are
    classified, so absence means unclassified, never safe-to-drop.
    """
    keep, dropped = [], []
    for f in findings:
        cls = (f.detail or {}).get("trait_class")
        (dropped if cls in _WITHHELD_TRAIT_CLASSES else keep).append(f)
    return keep, dropped


def _e(v) -> str:
    return _html.escape(str(v))


def _tile_note(c, ok: bool) -> str:
    """Terse tile footnote. The full sentence lives in ClockResult.note, but four
    tiles each carrying one turns the section into a wall of disclaimer — the tile
    says what it is, briefly, and the long form stays available underneath."""
    if ok:
        return c.note
    if getattr(c, "tissue_mismatch", False):
        return f"trained on {c.trained_tissue}"
    if getattr(c, "implausible", False):
        return "outside plausible range"
    return c.note


def _clock_tiles(result) -> str:
    tiles = []
    for c in result.clocks:
        ok = getattr(c, "valid", c.age is not None)
        if ok:
            pct = max(0.0, min(1.0, getattr(c, "coverage", 0.0))) * 100
            tiles.append(f"""<div class="tile">
  <div class="tval">{c.age:.1f}<span class="tunit">yrs</span></div>
  <div class="tname">{_e(c.clock)}</div>{_acceleration_html(c)}
  <div class="tmeter"><i style="width:{pct:.0f}%"></i></div>
  <div class="tnote">{_e(_tile_note(c, True))}</div>
</div>""")
        else:
            tiles.append(f"""<div class="tile muted">
  <div class="tval tvoid">not reported</div>
  <div class="tname">{_e(c.clock)}</div>
  <div class="tnote">{_e(_tile_note(c, False))}</div>
</div>""")
    if not tiles:
        return ""
    return f"""<h3 class="sub">Epigenetic age</h3>
<div class="tiles">{''.join(tiles)}</div>{_mismatch_note(result)}"""


def _acceleration_html(c) -> str:
    """How far this clock sits from the person's actual age. Only when the age
    was given (the composer sets acceleration only then) and the clock is valid."""
    acc = getattr(c, "acceleration", None)
    if acc is None:
        return ""
    if abs(acc) < 0.5:
        return "<div class='tacc even'>about your age</div>"
    word = "older" if acc > 0 else "younger"
    return (f"<div class='tacc {'up' if acc > 0 else 'down'}'>{acc:+.1f} yrs {word} "
            f"than your age</div>")


def person_line(result) -> str:
    """One line above the snapshot: what we know about the person and where it
    came from. A guess is labelled as one, never shown as the person's fact."""
    bits = []
    age = getattr(result, "age", None)
    if age is not None:
        src = getattr(result, "age_source", None)
        if src == "guess":
            bits.append(f"Age about {float(age):.0f} <span class='psrc'>(estimated from the clocks; "
                        f"enter your age on the upload form to see how far they sit from it)</span>")
        else:
            bits.append(f"Age {float(age):.0f} <span class='psrc'>(your entry)</span>")
    sex = getattr(result, "sex", None)
    if sex:
        src = getattr(result, "sex_source", None)
        note = ("estimated from your file; correct it on the upload form if wrong"
                if src == "guess" else "your entry")
        bits.append(f"Sex {_e(str(sex))} <span class='psrc'>({note})</span>")
    if not bits:
        return ""
    return "<p class='person'>" + " <span class='psep'>·</span> ".join(bits) + "</p>"


def clock_moves_html(result) -> str:
    """"What moves your clock": the sites that weigh most in the valid clocks'
    estimates, as signed bars. Contributions are relative to a zero reading."""
    try:
        from methylask.clocks import top_contributions
    except ImportError:
        return ""
    rows = []
    for c in (getattr(result, "clocks", None) or []):
        if not getattr(c, "valid", False) or not getattr(c, "contributions", None):
            continue
        for probe, coef, beta, value, years in top_contributions(c, n=6):
            rows.append((c.clock, probe, years))
    if not rows:
        return ""
    rows.sort(key=lambda r: -abs(r[2]))
    rows = rows[:8]
    scale = max(abs(r[2]) for r in rows) or 1.0
    bars = "".join(
        f"<div class='mv'><a class='mvp' href='#' data-marker='{_e(probe)}'>{_e(probe)}</a>"
        f"<span class='mvc'>{_e(clock.split('_')[0])}</span>"
        f"<span class='mvbar {'up' if yrs > 0 else 'down'}'><i style='width:{abs(yrs) / scale * 100:.0f}%'></i></span>"
        f"<span class='mvv'>{yrs:+.1f} yrs</span></div>"
        for clock, probe, yrs in rows)
    return f"""<h3 class="sub">What moves your clock</h3>
<div class="moves">{bars}</div>
<p class="tmismatch">Contributions are relative to a zero reading at each site, not to other people.
A population reference per site is a later addition.</p>"""


def _mismatch_note(result) -> str:
    """The reason a clock was withheld, stated once beneath the tiles.

    The tiles are terse on purpose — four of them each carrying a full sentence
    is a wall of disclaimer. But terseness dropped the actual finding: a tile
    reading "not reported / trained on blood" never says the number would not be
    meaningful, which is the whole point of flagging a tissue mismatch. Saying it
    once here is what the tile note was always supposed to be short *against*.
    """
    bad = [c for c in result.clocks if getattr(c, "tissue_mismatch", False)]
    if not bad:
        return ""
    trained = sorted({str(getattr(c, "trained_tissue", "blood")) for c in bad})
    tissue = getattr(result, "tissue", None)
    n = f"{len(bad)} of these clocks are" if len(bad) > 1 else "One of these clocks is"
    where = f" and this sample is {_e(tissue)}" if tissue else ""
    return (f"""<p class="tmismatch">{n} <strong>not valid for this sample type</strong>"""
            f""" — they are trained on {_e(' and '.join(trained))}{where}, so their """
            f"""numbers would not be meaningful here and are not reported.</p>""")


def _scale(sample_beta: float, groups: list) -> str:
    """Published group positions on a 0-1 methylation axis, with the reading above.

    Deliberately the full 0-1 axis rather than a zoom fitted to the data: a
    stretched axis makes a small difference look decisive, and these differences
    are what the whole card is trying to keep in proportion.
    """
    # Published groups routinely sit within a few percent of each other (the
    # never-smoker and former-smoker medians differ by 0.033), so labels are
    # dropped onto alternating rows after sorting — otherwise they overprint and
    # the card is unreadable exactly where it is most interesting.
    marks = []
    for lvl, g in enumerate(sorted(groups, key=lambda g: float(g["beta"]))):
        x = max(0.0, min(1.0, float(g["beta"]))) * 100
        marks.append(
            f"<span class='gmark lv{lvl % 2}' style='left:{x:.2f}%'>"
            f"<i></i><em>{_e(g['group'])}</em>"
            f"<b>{float(g['beta']):.3f}</b></span>")
    x = max(0.0, min(1.0, float(sample_beta))) * 100
    return f"""<div class="scale">
  <span class="you" style="left:{x:.2f}%"><b>you {float(sample_beta):.3f}</b><i></i></span>
  <div class="track">{''.join(marks)}</div>
  <span class="cap capl">0</span><span class="cap capr">1</span>
</div>"""


def _card(f) -> str:
    d = f.detail or {}
    withheld = bool(d.get("reference_withheld"))
    label = d.get("label") or f.marker
    pm = " ".join(
        f"<a href='https://pubmed.ncbi.nlm.nih.gov/{_e(p)}/'>PMID {_e(p)}</a>"
        for p in (f.pmids or []))

    if withheld:
        middle = f"<p class='withheldmsg'>{_e(f.description)}</p>"
        foot = ""
    else:
        middle = _scale(d.get("beta", 0.0), d.get("groups") or [])
        bits = []
        if d.get("reference_tissue"):
            bits.append(f"reference tissue {_e(d['reference_tissue'])}")
        if d.get("reference_n"):
            bits.append(f"n={_e(d['reference_n'])}")
        if pm:
            bits.append(pm)
        foot = (f"<p class='reading'>{_e(f.description)}</p>"
                f"<div class='cmeta'>{' · '.join(bits)}</div>")

    return f"""<article class="xcard{' withheld' if withheld else ''}">
  <header>
    <h4>{_e(label)}</h4>
    <span class="probe">{_e(f.marker)}</span>
  </header>
  {middle}
  {foot}
  <div class="pair">
    <div><span class="plab">What was read</span><p>{_e(d.get('what_was_read',''))}</p></div>
    <div><span class="plab">What it is not</span><p>{_e(d.get('what_it_is_not',''))}</p></div>
  </div>
</article>"""


def highlights_html(result) -> str:
    """The merged snapshot section. Empty string when there is nothing to show."""
    highlights, _ = split_reference_findings(list(result.findings or []))
    clocks = _clock_tiles(result) if getattr(result, "clocks", None) else ""
    if not highlights and not clocks:
        return ""
    tis = getattr(result, "tissue", None) or "unspecified"
    cards = "".join(_card(f) for f in highlights)
    exposure = f"""<h3 class="sub">Exposure markers</h3>
<div class="xcards">{cards}</div>""" if highlights else ""

    moves = clock_moves_html(result) if clocks else ""
    return f"""<section class="snapshot">
  {person_line(result)}
  <div class="shead">
    <h2>Biological snapshot</h2>
    <span class="chip">{_e(tis)}</span>
  </div>
  {clocks}
  {moves}
  {exposure}
{_STYLE}
</section>"""


_STYLE = """<style>
.snapshot{margin:0 0 40px;padding:0 0 6px}
.snapshot .shead{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;
  border-bottom:2px solid var(--ink);padding-bottom:8px;margin-bottom:14px}
.snapshot h2{font-family:var(--serif);font-size:30px;letter-spacing:-.01em;margin:0}
.snapshot .chip{font-family:var(--mono);font-size:11px;text-transform:uppercase;
  letter-spacing:.09em;color:var(--accent);border:1px solid var(--accent);
  border-radius:999px;padding:3px 10px}
.snapshot .sub{font-family:var(--mono);font-size:11px;text-transform:uppercase;
  letter-spacing:.13em;color:var(--faint);margin:26px 0 11px;font-weight:500}

.snapshot .tiles{display:grid;gap:12px;
  grid-template-columns:repeat(auto-fit,minmax(178px,1fr))}
.snapshot .tile{background:var(--card);border:1px solid var(--line);border-radius:9px;
  padding:14px 15px 13px}
.snapshot .tile.muted{background:transparent;border-style:dashed}
.snapshot .tval{font-family:var(--serif);font-size:33px;line-height:1.05;
  letter-spacing:-.02em;color:var(--ink)}
.snapshot .tval .tunit{font-size:14px;color:var(--mut);margin-left:5px;
  font-family:var(--sans)}
.snapshot .tval.tvoid{font-family:var(--sans);font-size:15px;color:var(--faint);
  font-style:italic;padding:9px 0 8px}
.snapshot .tname{font-family:var(--mono);font-size:11.5px;color:var(--mut);
  margin:5px 0 8px;word-break:break-word}
.snapshot .tmeter{height:3px;background:var(--accent-soft);border-radius:2px;
  overflow:hidden;margin-bottom:7px}
.snapshot .tmeter i{display:block;height:100%;background:var(--accent)}
.snapshot .tnote{font-size:12px;color:var(--faint);line-height:1.45}
.snapshot .tacc{font:600 12px/1.3 var(--sans);margin:2px 0 7px}
.snapshot .tacc.up{color:#8a4b2a}.snapshot .tacc.down{color:var(--accent)}.snapshot .tacc.even{color:var(--mut)}
.snapshot .person{font:400 13.5px/1.5 var(--sans);color:var(--ink);margin:0 0 10px}
.snapshot .psrc{color:var(--faint)}.snapshot .psep{color:var(--hair);margin:0 6px}
.snapshot .moves{display:grid;gap:5px}
.snapshot .mv{display:grid;grid-template-columns:110px 70px minmax(0,1fr) 64px;gap:10px;align-items:center;font-size:12px}
.snapshot .mvp{font-family:var(--mono);color:var(--mut);text-decoration:none}
.snapshot .mvc{font-family:var(--mono);font-size:10.5px;color:var(--faint)}
.snapshot .mvbar{height:7px;background:var(--line);border-radius:3px;overflow:hidden}
.snapshot .mvbar i{display:block;height:100%}
.snapshot .mvbar.up i{background:#c2683c}.snapshot .mvbar.down i{background:var(--accent)}
.snapshot .mvv{font-family:var(--mono);font-size:11.5px;color:var(--ink);text-align:right}
@media(prefers-color-scheme:dark){.snapshot .tacc.up{color:#d6905f}.snapshot .mvbar.up i{background:#d98a5c}}
/* the withheld-clock reason: readable rather than fine print, since it is the
   difference between "no number" and "a number that would have misled you" */
.snapshot .tmismatch{font-size:13px;color:var(--mut);line-height:1.5;
  margin:11px 0 0;max-width:74ch}
.snapshot .tmismatch strong{color:var(--ink);font-weight:600}

.snapshot .xcards{display:grid;gap:13px}
.snapshot .xcard{background:var(--card);border:1px solid var(--line);
  border-left:3px solid var(--accent);border-radius:9px;padding:14px 16px 12px}
.snapshot .xcard.withheld{border-left-color:var(--speculative);
  background:transparent;border-style:dashed;border-left-style:solid}
.snapshot .xcard header{display:flex;align-items:baseline;gap:10px;
  flex-wrap:wrap;margin-bottom:6px}
.snapshot .xcard h4{font-family:var(--serif);font-size:19px;margin:0;
  letter-spacing:-.005em}
.snapshot .probe{font-family:var(--mono);font-size:11px;color:var(--faint)}

.snapshot .scale{margin:40px 0 12px;position:relative;padding-bottom:78px}
.snapshot .track{position:relative;height:5px;border-radius:3px;
  background:linear-gradient(90deg,var(--accent-soft),var(--hair))}
.snapshot .gmark{position:absolute;top:0;transform:translateX(-50%);text-align:center}
.snapshot .gmark i{display:block;width:1px;background:var(--hair);margin:0 auto}
.snapshot .gmark.lv0 i{height:21px}
.snapshot .gmark.lv1 i{height:46px}
.snapshot .gmark em{display:block;font-style:normal;font-size:10.5px;
  color:var(--mut);white-space:nowrap;margin-top:3px}
.snapshot .gmark b{display:block;font-family:var(--mono);font-size:10px;
  color:var(--faint);font-weight:400}
.snapshot .you{position:absolute;bottom:100%;transform:translateX(-50%);
  text-align:center;white-space:nowrap}
.snapshot .you b{display:inline-block;font-family:var(--mono);font-size:11.5px;
  font-weight:500;color:var(--paper);background:var(--accent);
  border-radius:999px;padding:2px 8px 3px}
.snapshot .you i{display:block;width:1px;height:10px;background:var(--accent);
  margin:0 auto}
.snapshot .cap{position:absolute;top:8px;font-family:var(--mono);font-size:10px;
  color:var(--faint)}
.snapshot .cap.capl{left:0}
.snapshot .cap.capr{right:0}

.snapshot .reading{font-size:14px;margin:2px 0 6px;color:var(--ink)}
.snapshot .withheldmsg{font-size:14.5px;margin:0 0 10px;color:var(--ink)}
.snapshot .cmeta{font-size:12px;color:var(--faint);margin-bottom:6px}
.snapshot .cmeta a{color:var(--faint)}
.snapshot .pair{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
  border-top:1px solid var(--line);padding-top:9px;margin-top:8px}
.snapshot .plab{display:block;font-family:var(--mono);font-size:10px;
  text-transform:uppercase;letter-spacing:.1em;color:var(--faint);margin-bottom:3px}
.snapshot .pair p{margin:0;font-size:13px;line-height:1.55;color:var(--mut)}

@media print{.snapshot .xcard,.snapshot .tile{break-inside:avoid}}
@media (max-width:560px){
  .snapshot .gmark em{font-size:9px}
  .snapshot h2{font-size:25px}
}
</style>"""

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""Standalone pages that are not the report: refusals, empty results, and the
claim-link waiting page.

These exist because a failed or empty upload used to be answered with a JSON
body, which the browser rendered as one line of grey text — indistinguishable
from nothing happening. A refusal deserves the same care as a result: say what
we received, say why it stopped, say what to do next.

Presentation is the same specimen-plate language as the landing page and the
report (warm paper ground, serif masthead, hairline rules, small-caps labels,
one botanical-green accent), so a refusal still reads as part of the same
document rather than a stack trace in a different typeface.

Every asset is inline. No webfont, script, or image is fetched from a third
party — the product promises the upload is shared with no one, and a font-CDN
request would leak the visitor's IP and make that promise false. See the same
note in landing.py; do not "improve" these pages by adding a webfont.
"""
from __future__ import annotations

import html as _html

from . import __version__

# Shared with landing.py and bio-core's report renderer so the front door, the
# refusal and the result read as one printed object.
_TOKENS = """
 :root{
   --paper:#f7f5ef; --card:#fffdf8; --ink:#1b1c18; --mut:#6b6a61; --faint:#939186;
   --line:#ddd9cc; --hair:#c9c4b3; --accent:#2b6a5b; --accent-soft:#e6efe9;
   --warn:#8a4b2a; --warn-soft:#f6ece5;
   --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Charter,Georgia,"Times New Roman",serif;
   --sans:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI","Helvetica Neue",Arial,sans-serif;
   --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
   --a:#4f8f6b; --c:#3d7ea6; --g:#c08a3e; --t:#a8574f;
 }
 @media(prefers-color-scheme:dark){:root{
   --paper:#14150f; --card:#1c1e17; --ink:#ecebe2; --mut:#9e9d92; --faint:#7b7a70;
   --line:#32342a; --hair:#3d3f33; --accent:#63c2a2; --accent-soft:#1d2a24;
   --warn:#e0a179; --warn-soft:#2a1d14;
 }}
 *{box-sizing:border-box}
 html{-webkit-text-size-adjust:100%}
 body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.65 var(--sans)}
 .sheet{max-width:680px;margin:0 auto;padding:0 26px 80px}
 a{color:var(--accent)}
 .eyebrow{font:600 11px/1 var(--sans);letter-spacing:.2em;text-transform:uppercase;
   color:var(--faint);margin:0}
 .rule{font:600 11px/1 var(--sans);letter-spacing:.16em;text-transform:uppercase;
   color:var(--faint);margin:0 0 13px;padding-bottom:9px;border-bottom:1px solid var(--line)}
 h1{font:400 clamp(29px,5.4vw,42px)/1.1 var(--serif);letter-spacing:-.015em;margin:14px 0 0}
 .lede{font:400 18px/1.55 var(--serif);color:var(--mut);margin:14px 0 0}
 .btn{font:500 14px/1 var(--sans);cursor:pointer;border-radius:3px;
   border:1px solid var(--hair);background:transparent;color:var(--ink);
   padding:11px 18px;text-decoration:none;display:inline-block;
   transition:border-color .15s ease,color .15s ease}
 .btn:hover{border-color:var(--accent);color:var(--accent)}
 .primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
 .primary:hover{color:#fff;filter:brightness(1.08)}
 footer{margin:52px 0 0;padding:20px 0 0;border-top:1px solid var(--hair);
   font:11.5px/1.7 var(--mono);color:var(--faint);display:flex;gap:18px;flex-wrap:wrap}
 @keyframes rise{from{opacity:0;transform:translateY(9px)}to{opacity:1;transform:none}}
 header,.plate,.after{animation:rise .5s cubic-bezier(.2,.7,.3,1) both}
 .plate{animation-delay:.08s}.after{animation-delay:.15s}
 @media(prefers-reduced-motion:reduce){*{animation:none !important;transition:none !important}}
"""

# The chromatogram hairline band from the landing page. Reused so a refusal
# still carries the product's one piece of ornament.
_TRACK = """
 .track{height:12px;margin:24px 0 0;border-top:1px solid var(--hair);
   border-bottom:1px solid var(--hair);overflow:hidden;position:relative;opacity:.8}
 .track::before{content:"";position:absolute;inset:0;background:repeating-linear-gradient(90deg,
   var(--a) 0 2px,transparent 2px 9px),repeating-linear-gradient(90deg,
   var(--c) 5px 7px,transparent 7px 23px),repeating-linear-gradient(90deg,
   var(--g) 13px 15px,transparent 15px 37px),repeating-linear-gradient(90deg,
   var(--t) 29px 31px,transparent 31px 53px);
   background-size:auto 68%;background-repeat:repeat-x;background-position:0 50%;
   animation:drift 240s linear infinite}
 @keyframes drift{to{transform:translateX(-53px)}}
"""


def _shell(title: str, body: str, extra_css: str = "") -> str:
    """Wrap page body in the shared document chrome."""
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{_html.escape(title)}</title>
<style>{_TOKENS}{_TRACK}{extra_css}</style></head><body>
<main class="sheet">
{body}
<footer>
  <span>DNA-Report {_html.escape(__version__)}</span>
  <span>AGPL-3.0-or-later</span>
  <span>Good Ancestor Foundation</span>
</footer>
</main></body></html>"""


_REFUSAL_CSS = """
 .plate{margin:30px 0 0;background:var(--card);border:1px solid var(--line);
   border-left:3px solid var(--warn);border-radius:3px;padding:22px 24px;
   box-shadow:0 1px 0 rgba(0,0,0,.03),0 12px 34px -26px rgba(0,0,0,.5)}
 .plate .what{font:500 14px/1.5 var(--mono);word-break:break-all;color:var(--ink)}
 .plate .why{margin:12px 0 0;font-size:15px;line-height:1.6}
 .plate .hint{margin:14px 0 0;padding:12px 14px;background:var(--warn-soft);
   border-radius:2px;font-size:14px;line-height:1.6;color:var(--ink)}
 .code{font:11px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;
   color:var(--faint);margin:0 0 10px}
 .after{margin:38px 0 0}
 .formats{list-style:none;margin:0;padding:0;font-size:14px}
 .formats li{padding:9px 0 9px 22px;border-bottom:1px solid var(--line);
   position:relative;color:var(--mut)}
 .formats li:last-child{border-bottom:0}
 .formats li::before{content:"";position:absolute;left:4px;top:17px;width:7px;height:7px;
   border-radius:50%;background:var(--accent);opacity:.5}
 .actions{margin:32px 0 0;display:flex;gap:12px;flex-wrap:wrap;align-items:center}
"""


def refusal_page(*, code: str, title: str, message: str, hint: str = "",
                 filename: str = "", accepted: list[str] | None = None) -> str:
    """A rejected upload, rendered as a specimen plate rather than an error dump.

    Names the file we were handed, states plainly what stopped, and — when the
    problem is the format — lists what we do read, so the next attempt succeeds.
    """
    esc = _html.escape
    what = (f"<div class='what'>{esc(filename)}</div>" if filename else "")
    hint_html = f"<div class='hint'>{esc(hint)}</div>" if hint else ""
    formats = ""
    if accepted:
        items = "".join(f"<li>{esc(f)}</li>" for f in accepted)
        formats = (f"<section class='after'><p class='rule'>What we can read</p>"
                   f"<ul class='formats'>{items}</ul></section>")
    body = f"""
 <header>
   <p class="eyebrow">Good&nbsp;Ancestor &middot; DNA-Report</p>
   <h1>{esc(title)}</h1>
   <div class="track" aria-hidden="true"></div>
 </header>

 <div class="plate" role="alert">
   <p class="code">Upload stopped &middot; {esc(code)}</p>
   {what}
   <p class="why">{esc(message)}</p>
   {hint_html}
 </div>

 {formats}

 <div class="actions">
   <a class="btn primary" href="/">Try another file</a>
   <a class="btn" href="/demo/blood">Open a sample report instead</a>
 </div>
"""
    return _shell(f"DNA-Report — {title}", body, _REFUSAL_CSS)


_EMPTY_CSS = """
 .plate{margin:30px 0 0;background:var(--card);border:1px solid var(--line);
   border-left:3px solid var(--accent);border-radius:3px;padding:22px 24px;
   box-shadow:0 1px 0 rgba(0,0,0,.03),0 12px 34px -26px rgba(0,0,0,.5)}
 .plate .what{font:500 14px/1.5 var(--mono);word-break:break-all}
 .plate .why{margin:12px 0 0;font-size:15px;line-height:1.6}
 .code{font:11px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;
   color:var(--faint);margin:0 0 10px}
 .after{margin:38px 0 0}
 .reasons{margin:0;padding-left:20px;font-size:14.5px;line-height:1.7;color:var(--mut)}
 .reasons li{margin:0 0 8px}
 .reasons b{color:var(--ink);font-weight:600}
 .notes{margin:16px 0 0;font-size:13.5px;line-height:1.6;color:var(--mut);
   border-left:2px solid var(--line);padding-left:14px}
 .actions{margin:32px 0 0;display:flex;gap:12px;flex-wrap:wrap;align-items:center}
"""


def empty_report_page(*, kind_label: str, filename: str = "",
                      notes: list[str] | None = None) -> str:
    """A file we read successfully that produced nothing worth reporting.

    This is a *result*, not an error — so it says what was recognised and offers
    the honest reasons a genuine file can come back empty, instead of a bare
    zero. Returned with HTTP 200.
    """
    esc = _html.escape
    what = (f"<div class='what'>{esc(filename)}</div>" if filename else "")
    notes_html = ""
    if notes:
        notes_html = ("<div class='notes'>" +
                      "".join(f"<p>{esc(n)}</p>" for n in notes) + "</div>")
    body = f"""
 <header>
   <p class="eyebrow">Good&nbsp;Ancestor &middot; DNA-Report</p>
   <h1>We read your file &mdash; nothing met the reporting bar</h1>
   <p class="lede">The file parsed cleanly and was screened against our reference
     databases. No marker in it crossed the threshold for a finding we would stand
     behind, so there is nothing to report rather than something padded out.</p>
   <div class="track" aria-hidden="true"></div>
 </header>

 <div class="plate">
   <p class="code">Recognised &middot; {esc(kind_label)}</p>
   {what}
   <p class="why">Zero findings. This is a real outcome, not a failure &mdash; and it is
     a far better answer than a page of associations too weak to mean anything.</p>
   {notes_html}
 </div>

 <section class="after">
   <p class="rule">Why a valid file comes back empty</p>
   <ul class="reasons">
     <li><b>The file is a small or partial export.</b> A few hundred markers rarely
       overlap the variants our references actually describe; a full export from a
       consumer testing service carries hundreds of thousands.</li>
     <li><b>Your genotypes are the common ones.</b> Most people carry the ordinary
       allele at most screened positions. An empty report is a common, unremarkable
       result and is not a clean bill of health.</li>
     <li><b>The markers measured do not overlap our panels.</b> Testing services
       each pick a different chip; some have thin coverage of the positions with
       published evidence behind them.</li>
   </ul>
 </section>

 <div class="actions">
   <a class="btn primary" href="/">Analyse another file</a>
   <a class="btn" href="/demo/blood">See what a full report looks like</a>
 </div>
"""
    return _shell("DNA-Report — no findings", body, _EMPTY_CSS)


_WAITING_CSS = """
 .plate{margin:30px 0 0;background:var(--card);border:1px solid var(--line);
   border-radius:3px;padding:26px 24px;text-align:center;
   box-shadow:0 1px 0 rgba(0,0,0,.03),0 12px 34px -26px rgba(0,0,0,.5)}
 .spin{width:32px;height:32px;border:3px solid var(--line);border-top-color:var(--accent);
   border-radius:50%;margin:0 auto 16px;animation:spin 1s linear infinite}
 @keyframes spin{to{transform:rotate(360deg)}}
 .plate p{margin:0 0 10px;font-size:15px;line-height:1.6}
 .plate p:last-child{margin-bottom:0}
 .job{font:12px/1.5 var(--mono);color:var(--faint);margin-top:14px}
 .after{margin:34px 0 0;font-size:14px;line-height:1.7;color:var(--mut)}
"""


def _elapsed(seconds: float | None) -> str:
    """'4 minutes', '1 hour 5 minutes' — how long the person has been waiting."""
    if seconds is None:
        return ""
    s = int(max(0, seconds))
    if s < 90:
        return f"{s} seconds"
    m, h = (s // 60) % 60, s // 3600
    if not h:
        return f"{m} minutes"
    return f"{h} hour{'s' if h > 1 else ''}" + (f" {m} minutes" if m else "")


def waiting_page(job_id: str, waited: float | None = None) -> str:
    """The claim-link page shown while a queued job is still being worked.

    Self-refreshing and bookmarkable: the same URL becomes the report the moment
    the worker writes it, so nobody has to keep a tab open.

    It states how long the wait has been so far. Without that the page looked
    identical after twenty seconds and after two hours, which is what let a job
    that had actually failed keep presenting itself as one about to finish.
    """
    esc = _html.escape
    waited_line = (f"<p class='job'>Waiting {esc(_elapsed(waited))} so far</p>"
                   if waited is not None else "")
    body = f"""
 <header>
   <p class="eyebrow">Good&nbsp;Ancestor &middot; DNA-Report</p>
   <h1>Preparing your report</h1>
   <div class="track" aria-hidden="true"></div>
 </header>

 <div class="plate">
   <div class="spin" role="status" aria-label="Working"></div>
   <p>Your file is being analysed against the reference databases.</p>
   <p>This page refreshes itself. You can also <strong>bookmark this link</strong> and
     come back &mdash; it becomes your report the moment it is ready.</p>
   {waited_line}
   <p class="job">Job {esc(job_id)}</p>
 </div>

 <p class="after">Large genome files can take several minutes. Your uploaded file is
   deleted once the report has been produced.</p>
"""
    page = _shell("DNA-Report — preparing your report", body, _WAITING_CSS)
    # refresh tag goes in the head the shell built
    return page.replace("<title>", '<meta http-equiv="refresh" content="15">\n<title>', 1)


def job_overdue_page(job_id: str, waited: float | None = None) -> str:
    """The claim link has waited longer than any real job should take.

    A give-up point exists because every way a queued job can fail — a worker
    that died between leasing and writing, a report that was never uploaded, a
    queue that lost the job — looked exactly like a slow job on the waiting page,
    and that page refreshed for ever. Telling someone their report is late is a
    worse answer than a report and a much better one than an animation that never
    stops. Deliberately does NOT auto-refresh.
    """
    esc = _html.escape
    waited_line = (f"<p class='job'>No report after {esc(_elapsed(waited))}</p>"
                   if waited is not None else "")
    body = f"""
 <header>
   <p class="eyebrow">Good&nbsp;Ancestor &middot; DNA-Report</p>
   <h1>Your report is overdue</h1>
   <p class="lede">This job has been queued far longer than an analysis takes, so
     rather than keep you watching a spinner: something has probably gone wrong at
     our end, not with your file.</p>
   <div class="track" aria-hidden="true"></div>
 </header>

 <div class="plate">
   {waited_line}
   <p class="job">Job {esc(job_id)}</p>
 </div>

 <section class="after">
   <p class="rule">What to do</p>
   <ul class="reasons">
     <li><b>Reload this page once more.</b> If the analysis did finish in the
       meantime, this link still becomes your report &mdash; it stays valid.</li>
     <li><b>Upload the file again.</b> A fresh job is the fastest fix, and costs
       you nothing but the upload.</li>
     <li><b>Tell us, quoting the job id above.</b> It is how we find what happened
       to this specific run.</li>
   </ul>
   <p>Your uploaded file has already been deleted either way &mdash; we do not keep
     it while a job is stuck.</p>
 </section>

 <div class="actions">
   <a class="btn primary" href="/">Upload the file again</a>
 </div>
"""
    return _shell("DNA-Report — report overdue", body, _EMPTY_CSS)


def job_failed_page(job_id: str, reason: str = "") -> str:
    """A queued job that definitively failed — it reached the dead-letter list.

    A known failure is worth saying plainly and immediately, instead of leaving
    the claim link to time out into the overdue page much later.
    """
    esc = _html.escape
    why = (f"<p class='why'>{esc(reason)}</p>" if reason else "")
    body = f"""
 <header>
   <p class="eyebrow">Good&nbsp;Ancestor &middot; DNA-Report</p>
   <h1>This analysis did not complete</h1>
   <p class="lede">The job stopped with an error and was not retried further. That is
     ours to fix, and it is better said than left to look like a long wait.</p>
   <div class="track" aria-hidden="true"></div>
 </header>

 <div class="plate">
   <p class="code">Failed</p>
   {why}
   <p class="job">Job {esc(job_id)}</p>
 </div>

 <section class="after">
   <p class="rule">What to do</p>
   <ul class="reasons">
     <li><b>Try the upload once more.</b> Some failures are transient &mdash; a node
       dropping out mid-analysis, for instance.</li>
     <li><b>If it fails again, send us the job id.</b> The error above is recorded
       against it, so we can see exactly what stopped.</li>
   </ul>
 </section>

 <div class="actions">
   <a class="btn primary" href="/">Try again</a>
 </div>
"""
    return _shell("DNA-Report — analysis failed", body, _EMPTY_CSS)

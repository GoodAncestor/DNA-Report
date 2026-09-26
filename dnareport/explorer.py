# SPDX-License-Identifier: AGPL-3.0-or-later
"""A no-upload, single-variant research workbench."""
import html
import json
from pathlib import Path


def explorer_html(variant=''):
    page = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Explore a variant · DNA-Report</title>
<style>
:root{color-scheme:light dark;--bg:#f7f5ef;--card:#fffdf8;--ink:#1b1c18;--mut:#62655b;--line:#d7d7cc;--accent:#286652}
@media(prefers-color-scheme:dark){:root{--bg:#14150f;--card:#1c1e17;--ink:#ecebe2;--mut:#b4b6aa;--line:#44483b;--accent:#81d6b8}}
*{box-sizing:border-box}body{background:var(--bg);color:var(--ink);font:16px/1.6 system-ui,sans-serif;max-width:1000px;margin:auto;padding:28px 20px 70px}a{color:var(--accent)}h1,h2{font-family:Georgia,serif;font-weight:400;line-height:1.2}h1{font-size:clamp(32px,6vw,50px)}.intro{max-width:680px;color:var(--mut)}form,.guide{padding:22px;background:var(--card);border:1px solid var(--line);border-radius:8px;margin:24px 0}input,button{font:inherit;padding:10px;border:1px solid var(--line);border-radius:5px}input{width:100%;background:var(--bg);color:var(--ink)}button{background:var(--accent);color:var(--bg);cursor:pointer;margin:12px 8px 0 0}button:disabled{opacity:.6;cursor:wait}label{display:block;font-weight:600}small{display:block;color:var(--mut);margin-top:8px}.models{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:22px}.models h2{font-size:23px}.models p{color:var(--mut)}#message{white-space:pre-wrap}iframe{width:100%;height:1050px;border:1px solid var(--line);border-radius:8px}#exports{margin:15px 0}#exports a{margin-right:22px}:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
</style></head><body><nav><a href="/">DNA-Report</a> · <a href="/demo/ai">Guided AI examples</a></nav>
<h1>Explore a DNA variant</h1><p class="intro">Bring one DNA change. Compare curated evidence with predictions about protein function and gene regulation. No genome upload needed.</p>
<div class="models"><section><h2>AlphaMissense</h2><p>Looks up a precomputed prediction for a protein-changing variant. A missing match does not mean harmless.</p></section><section><h2>AlphaGenome</h2><p>Predicts molecular effects from DNA sequence. Results depend on the scorer, tissue and model; they are not a personal disease probability.</p></section></div>
<form id="explorer"><label for="variant">GRCh38 variant</label><input id="variant" name="variant" value="__VARIANT__" placeholder="22-36201698-A-C" required maxlength="125" autocomplete="off" spellcheck="false"><small>Chromosome-position-reference-alternate. Coordinates are 1-based. Use GRCh38 and verify the reference allele. Example: 22-36201698-A-C.</small><button type="submit">Look up evidence</button><button type="button" id="predict">Request AlphaGenome prediction</button><small>Evidence lookup uses local databases. Requesting a prediction sends this variant and its sequence context to Google’s AlphaGenome service when enabled. Results are research annotations; no sample genotype is inferred.</small></form>
<p id="message" role="status" aria-live="polite"></p><div id="exports" hidden><a id="json-download" download="variant-evidence.json">Download JSON</a><a id="md-download" download="variant-evidence.md">Download Markdown</a></div><iframe id="result" title="Variant evidence report" sandbox="allow-scripts allow-modals allow-popups" hidden></iframe>
<section class="guide"><h2>What to look for</h2><p>Start with what ClinVar records, then compare protein and regulatory predictions. The models address different mechanisms. Disagreement is useful context; agreement alone does not establish a clinical conclusion.</p><p><a href="https://alphagenome.google/">Explore AlphaGenome Atlas</a> for additional precomputed research predictions. Atlas is a separate resource; DNA-Report labels its own model outputs explicitly.</p></section>
<script>
const form=document.querySelector('#explorer'),message=document.querySelector('#message'),frame=document.querySelector('#result');let urls=[];
async function run(predict){
 document.querySelectorAll('button').forEach(b=>b.disabled=true);message.textContent=predict?'Requesting a bounded prediction…':'Looking up evidence…';frame.hidden=true;document.querySelector('#exports').hidden=true;
 try{const response=await fetch('/api/variant',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({variant:document.querySelector('#variant').value,reference_build:'GRCh38',predict})});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'The lookup could not complete. Please try again.');
 frame.srcdoc=data.html;frame.hidden=false;urls.forEach(URL.revokeObjectURL);urls=[];
 for(const [id,text,type] of [['json-download',JSON.stringify(data.report,null,2),'application/json'],['md-download',data.markdown,'text/markdown']]){const url=URL.createObjectURL(new Blob([text],{type}));urls.push(url);document.getElementById(id).href=url;}document.querySelector('#exports').hidden=false;message.textContent='Evidence ready. Check model coverage below to see what was scored or unavailable.';
 }catch(e){message.textContent=e.message;}finally{document.querySelectorAll('button').forEach(b=>b.disabled=false);}}
form.addEventListener('submit',e=>{e.preventDefault();run(false)});document.querySelector('#predict').addEventListener('click',()=>{if(form.reportValidity())run(true)});
</script></body></html>'''
    return page.replace('__VARIANT__', html.escape(str(variant)[:125], quote=True))


def build_ai_demo():
    """Frozen, verified public model outputs; opening a demo never calls an API."""
    from biocore.providers.base import Finding, Tier, Category
    from .orchestrate import ReportResult
    from .detect import InputKind
    from .predictions import provider_statuses
    data = json.loads((Path(__file__).parent/'demo_data'/'ai_predictions.json').read_text())
    findings = []
    for row in data['examples']:
        findings.append(Finding(marker=row['variant_id'], source='variant_lookup',
            description=row['description'], tier=Tier.SPECULATIVE, categories=[Category.CLINICAL],
            detail={**row['detail'], 'modality':'genome', 'measurement_origin':'public_example',
                    'reference_build':'GRCh38'}))
    status = data['coverage']
    return ReportResult(kind=InputKind.VCF, engines=('geneask',), findings=findings,
        provider_status=provider_statuses(status),
        notes=[data['notice'], 'These are separate public research examples, not one person’s genome. Opening this demo makes no live prediction requests.',
               'Export provenance includes retrieval dates and source information.'],
        scan_stats={'context':'public_ai_demo','ai_predictions':status,'ai_demo':data['provenance'],'markers_scanned':len(findings),'findings_total':len(findings)})


def guided_demo_html(result):
    from .report import report_html
    rendered = report_html(result, filename='Verified public AI prediction examples')
    intro = '''<section class="ai-guide"><h2>Explore AI predictions</h2><p>Verified public examples show what each model contributes. AlphaMissense evaluates protein changes; AlphaGenome evaluates regulatory effects. Compare the model evidence with the available ClinVar record, and open each variant in the explorer to investigate further.</p><p>These examples are separate variants, not a person’s genome. Model predictions support research and are not clinical conclusions.</p><p><a href="/explore">Look up your own variant</a> · <a href="/demo/ai?format=json">Download JSON</a> · <a href="/demo/ai?format=markdown">Download Markdown</a></p></section>'''
    return rendered.replace('<body>', '<body><script>if(!location.hash)location.hash="view=site&predictions=only";</script>'+intro, 1)

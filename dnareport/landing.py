# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""The landing page HTML for the DNA-Report front door.

Deliberately restrained: a moderately-technical lay reader should understand it,
and it makes NO definitive health claims. The tone is the intentional opposite of
salesy "quantify your exact pace of cellular degradation" marketing copy — it
states what the tool does, names the evidence tiers, and keeps the single
consolidated disclaimer in one place (not scattered).

Presentation notes (why it looks the way it does):
  - Every asset is inline. No webfont, stylesheet, script or image is fetched
    from a third party, because the page promises the upload is shared with no
    one — a font CDN request would leak every visitor's IP to that CDN and make
    the promise false. Typography is therefore built from locally-installed
    stacks only.
  - The palette and the three tier colours are shared with the rendered report
    (see web.py `_clocks_html` and bio-core's renderer), so the front door and
    the result read as one document.
"""
from __future__ import annotations

from . import __version__

TISSUES = ["blood", "saliva", "buccal", "other"]

_LANDING_TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>DNA-Report — methylation & variant interpretation</title>
<style>
 :root{
   --paper:#f7f5ef; --card:#fffdf8; --ink:#1b1c18; --mut:#6b6a61; --faint:#939186;
   --line:#ddd9cc; --hair:#c9c4b3; --accent:#2b6a5b; --accent-soft:#e6efe9;
   --warn:#8a4b2a; --warn-soft:#f6ece5;
   --robust:#0c7a54; --spec:#b8860b; --unknown:#8a8a8a;
   --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Charter,Georgia,"Times New Roman",serif;
   --sans:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI","Helvetica Neue",Arial,sans-serif;
   --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
   --a:#4f8f6b; --c:#3d7ea6; --g:#c08a3e; --t:#a8574f;
 }
 @media(prefers-color-scheme:dark){:root{
   --paper:#14150f; --card:#1c1e17; --ink:#ecebe2; --mut:#9e9d92; --faint:#7b7a70;
   --line:#32342a; --hair:#3d3f33; --accent:#63c2a2; --accent-soft:#1d2a24;
   --warn:#e0a179; --warn-soft:#2a1d14;
   --robust:#3fbb8a; --spec:#d6a63c; --unknown:#8f8f88;
 }}

 *{box-sizing:border-box}
 html{-webkit-text-size-adjust:100%}
 body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.65 var(--sans)}

 /* --- masthead ------------------------------------------------------- */
 .sheet{max-width:940px;margin:0 auto;padding:0 26px 90px}
 header{padding:46px 0 0}
 .eyebrow{font:600 11px/1 var(--sans);letter-spacing:.2em;text-transform:uppercase;
   color:var(--faint);margin:0 0 18px}
 h1{font:400 clamp(38px,7vw,60px)/1.02 var(--serif);letter-spacing:-.02em;margin:0}
 h1 .dash{color:var(--hair)}
 .lede{font:400 clamp(17px,2.3vw,20px)/1.55 var(--serif);color:var(--mut);
   max-width:33em;margin:16px 0 0}

 /* the sequence track: a chromatogram-ish hairline band, drawn in CSS only.
    It drifts extremely slowly, and stops entirely for anyone who has asked
    for reduced motion. */
 .track{height:14px;margin:26px 0 0;border-top:1px solid var(--hair);
   border-bottom:1px solid var(--hair);overflow:hidden;position:relative;opacity:.85}
 .track::before{content:"";position:absolute;inset:0;background:repeating-linear-gradient(90deg,
   var(--a) 0 2px,transparent 2px 9px),repeating-linear-gradient(90deg,
   var(--c) 5px 7px,transparent 7px 23px),repeating-linear-gradient(90deg,
   var(--g) 13px 15px,transparent 15px 37px),repeating-linear-gradient(90deg,
   var(--t) 29px 31px,transparent 31px 53px);
   background-size:auto 68%;background-repeat:repeat-x;background-position:0 50%;
   animation:drift 240s linear infinite}
 @keyframes drift{to{transform:translateX(-53px)}}

 /* --- layout --------------------------------------------------------- */
 .band{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);
   gap:40px;align-items:start;margin:38px 0 0}
 @media(max-width:760px){.band{grid-template-columns:1fr;gap:30px}}

 .rule{font:600 11px/1 var(--sans);letter-spacing:.16em;text-transform:uppercase;
   color:var(--faint);margin:0 0 14px;padding-bottom:9px;border-bottom:1px solid var(--line)}

 /* --- upload panel --------------------------------------------------- */
 .drop{position:relative;background:var(--card);border:1px solid var(--line);
   border-radius:3px;padding:40px 26px;text-align:center;
   box-shadow:0 1px 0 rgba(0,0,0,.03),0 10px 30px -22px rgba(0,0,0,.45);
   transition:border-color .18s ease,background .18s ease}
 .drop::after{content:"";position:absolute;inset:7px;border:1px dashed var(--hair);
   border-radius:2px;pointer-events:none;transition:border-color .18s ease}
 .drop:focus-visible,.drop.over{border-color:var(--accent);background:var(--accent-soft);outline:none}
 .drop.over::after,.drop:focus-visible::after{border-color:var(--accent)}
 .drop .big{font:400 21px/1.3 var(--serif);margin:0 0 6px}
 .drop .kinds{font-size:13.5px;color:var(--mut);margin:14px auto 0;max-width:30em}
 .btn{font:500 14px/1 var(--sans);cursor:pointer;border-radius:3px;
   border:1px solid var(--hair);background:transparent;color:var(--ink);
   padding:10px 16px;text-decoration:none;display:inline-block;
   transition:border-color .15s ease,color .15s ease,background .15s ease}
 .btn:hover{border-color:var(--accent);color:var(--accent)}
 .primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600;
   padding:12px 26px}
 .primary:hover{color:#fff;filter:brightness(1.08)}
 .primary:disabled{opacity:.35;cursor:not-allowed;filter:none}

 /* the recognised-file card: appears once a file is chosen */
 .recog{margin:16px 0 0;border:1px solid var(--line);border-left:3px solid var(--accent);
   background:var(--card);border-radius:2px;padding:12px 15px;text-align:left;display:none}
 .recog.show{display:block;animation:rise .3s ease both}
 .recog .name{font:500 14px/1.4 var(--mono);word-break:break-all}
 .recog .meta{font-size:13px;color:var(--mut);margin-top:4px}
 .recog .meta b{color:var(--ink);font-weight:600}

 .controls{margin:24px 0 0}
 .field{display:flex;gap:12px;flex-wrap:wrap;align-items:baseline;margin:0 0 16px}
 .field > label{font:500 14px/1.9 var(--sans);color:var(--ink);min-width:112px}
 select,input[type=email]{font:14px/1 var(--sans);padding:9px 11px;border-radius:3px;
   border:1px solid var(--hair);background:var(--card);color:var(--ink);min-width:190px}
 select:focus,input:focus{outline:2px solid var(--accent);outline-offset:1px}
 .hint{font-size:13px;line-height:1.5;color:var(--mut);flex:1 1 15em;min-width:0}
 .check{display:flex;gap:8px;align-items:flex-start;font:14px/1.5 var(--sans);min-width:112px}
 .actions{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin:26px 0 0}
 .status{font-size:13.5px;color:var(--mut);min-height:20px}

 /* --- failure panel ---------------------------------------------------
    An upload that cannot proceed gets a real panel, not a line of grey text.
    It states what stopped, what to do next, and (for a format problem) what we
    do read — the same content the server's refusal page carries. */
 .fail{display:none;margin:20px 0 0;background:var(--card);border:1px solid var(--line);
   border-left:3px solid var(--warn);border-radius:3px;padding:16px 18px}
 .fail.show{display:block;animation:rise .3s ease both}
 .fail .code{font:11px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;
   color:var(--faint);margin:0 0 8px}
 .fail .t{font:500 15px/1.45 var(--sans);margin:0 0 6px}
 .fail .m{font-size:14px;line-height:1.6;color:var(--mut);margin:0}
 .fail .h{margin:12px 0 0;padding:10px 12px;background:var(--warn-soft);border-radius:2px;
   font-size:13.5px;line-height:1.6;color:var(--ink)}
 .fail ul{margin:12px 0 0;padding-left:18px;font-size:13.5px;line-height:1.65;color:var(--mut)}
 .fail li{margin:0 0 4px}

 /* --- sidebar -------------------------------------------------------- */
 aside .tier{display:flex;gap:10px;align-items:flex-start;padding:11px 0;
   border-bottom:1px solid var(--line);font-size:13.5px}
 aside .tier:last-child{border-bottom:0}
 .dot{flex:0 0 auto;width:9px;height:9px;border-radius:50%;margin-top:6px}
 .tier b{display:block;font:600 13.5px/1.5 var(--sans)}
 .tier span{color:var(--mut);font-size:13px}
 .privacy{margin:30px 0 0;font-size:13.5px;line-height:1.6;color:var(--mut)}
 .privacy li{margin:0 0 7px}
 .privacy ul{margin:0;padding-left:18px}

 /* --- demos ---------------------------------------------------------- */
 .demos{margin:52px 0 0}
 .specimens{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:14px}
 .specimen{display:block;text-decoration:none;color:inherit;background:var(--card);
   border:1px solid var(--line);border-radius:3px;padding:18px 18px 16px;
   transition:border-color .16s ease,transform .16s ease}
 .specimen:hover{border-color:var(--accent);transform:translateY(-2px)}
 .specimen .no{font:600 10.5px/1 var(--sans);letter-spacing:.16em;color:var(--faint)}
 .specimen .t{font:400 17px/1.3 var(--serif);margin:9px 0 6px}
 .specimen .d{font-size:12.5px;line-height:1.5;color:var(--mut)}
 .demos .hint{margin:16px 0 0;max-width:52em}

 /* --- colophon ------------------------------------------------------- */
 footer{margin:56px 0 0;padding:22px 0 0;border-top:1px solid var(--hair);
   font-size:13px;line-height:1.6;color:var(--mut)}
 footer strong{color:var(--ink)}
 footer a{color:var(--accent)}
 .colophon{margin:14px 0 0;font:11.5px/1.7 var(--mono);color:var(--faint);
   display:flex;gap:18px;flex-wrap:wrap}

 /* --- load choreography ---------------------------------------------- */
 @keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
 header,.band,.demos,footer{animation:rise .55s cubic-bezier(.2,.7,.3,1) both}
 .band{animation-delay:.09s}.demos{animation-delay:.17s}footer{animation-delay:.24s}
 @media(prefers-reduced-motion:reduce){
   *{animation:none !important;transition:none !important}
 }
</style></head><body>
<main class="sheet">

 <header>
   <p class="eyebrow">Good&nbsp;Ancestor &middot; open genomics</p>
   <h1>DNA&#8202;<span class="dash">&mdash;</span>&#8202;Report</h1>
   <p class="lede">Read a methylation or genome file and get an interpretation grouped by
     how much scientific support each finding actually has. Everything runs on the
     server &mdash; your file is not shared with any third party.</p>
   <div class="track" aria-hidden="true"></div>
 </header>

 <div class="band">
  <section>
   <p class="rule">Your file</p>

   <div class="drop" id="drop" tabindex="0" role="button"
        aria-label="Drop a file here, or press Enter to choose a file">
     <p class="big">Drop a file here</p>
     <p><label class="btn" for="file">Choose a file</label>
       <input id="file" type="file" hidden></p>
     <p class="kinds">A methylation beta-value export (e.g. a TruDiagnostic CSV),
       an EPIC array file, a 23andMe or VCF genome, or an ONT modBAM.</p>
   </div>

   <div class="recog" id="recog" aria-live="polite">
     <div class="name" id="recog-name"></div>
     <div class="meta" id="recog-meta"></div>
   </div>

   <div class="controls">
     <div class="field">
       <label for="tissue">Sample type</label>
       <select id="tissue">
         <option value="">Auto-detect</option>
         <option value="blood">Blood</option>
         <option value="saliva">Saliva</option>
         <option value="buccal">Buccal / cheek swab</option>
         <option value="other">Other</option>
       </select>
       <span class="hint" id="tissuehint">We infer this from your file and use it to decide
         which epigenetic clocks are valid &mdash; you can override it.</span>
     </div>

     <div class="field">
       <label for="notify_email">Email me</label>
       <input id="notify_email" type="email" placeholder="you@example.com" autocomplete="email">
       <span class="hint">Optional. Large genomes can take a few minutes; give an email only
         if you want a link when it's done &mdash; otherwise just bookmark the result page.</span>
     </div>

     <div class="field">
       <label class="check" for="newsletter"><input id="newsletter" type="checkbox">
         Newsletter</label>
       <span class="hint">Separate, optional, and off by default &mdash; a delivery email is
         never added to any list unless you tick this.</span>
     </div>
   </div>

   <div class="actions">
     <button class="primary btn" id="go" disabled>Analyze</button>
     <span class="status" id="status"></span>
   </div>

   <div class="fail" id="fail" role="alert" aria-live="assertive">
     <p class="code" id="fail-code"></p>
     <p class="t" id="fail-title"></p>
     <p class="m" id="fail-msg"></p>
     <div class="h" id="fail-hint" hidden></div>
     <ul id="fail-list" hidden></ul>
   </div>
  </section>

  <aside>
   <p class="rule">How findings are labelled</p>
   <div class="tier"><span class="dot" style="background:var(--robust)"></span>
     <div><b>Robust</b><span>Replicated across independent cohorts.</span></div></div>
   <div class="tier"><span class="dot" style="background:var(--spec)"></span>
     <div><b>Speculative</b><span>Reported, but thin or unreplicated evidence.</span></div></div>
   <div class="tier"><span class="dot" style="background:var(--unknown)"></span>
     <div><b>Unknown / limited</b><span>Marker seen; no dependable interpretation.</span></div></div>

   <div class="privacy">
     <p class="rule" style="margin-top:30px">What happens to your file</p>
     <ul>
       <li>Processed on the server, shared with no one.</li>
       <li>Deleted after the report is produced.</li>
       <li>No third-party fonts, scripts, or trackers on this page.</li>
     </ul>
   </div>
  </aside>
 </div>

 <section class="demos">
   <p class="rule">No file? Open a real sample</p>
   <div class="specimens">
     <a class="specimen demo-link" href="/demo/blood">
       <div class="no">SPECIMEN 01</div>
       <div class="t">Whole-blood methylome</div>
       <div class="d">Healthy adult, public cohort. Every clock is valid for blood.</div></a>
     <a class="specimen demo-link" href="/demo/buccal">
       <div class="no">SPECIMEN 02</div>
       <div class="t">Buccal methylome</div>
       <div class="d">Paediatric research sample. Shows blood-trained clocks being flagged
         rather than reported.</div></a>
     <a class="specimen demo-link" href="/demo/combined">
       <div class="no">SPECIMEN 03</div>
       <div class="t">Methylome + genome</div>
       <div class="d">Two file types merged into a single report, each finding tagged
         with its source.</div></a>
   </div>
   <p class="hint">All three are real public profiles. The buccal one is included on
     purpose: it shows the report marking clocks that are not valid for a given sample
     type, instead of printing a confident but misleading number.</p>
 </section>

 <footer>
   <p><strong>Not medical advice.</strong> DNA-Report shows research associations for
     your markers, labelled by how much evidence supports them. It is not a diagnosis.
     Every report ends with the full note on how to read it &mdash;
     <a href="/disclaimer">read it here first</a> if you like.</p>
   <p class="colophon">
     <span>DNA-Report __VERSION__</span>
     <span>AGPL-3.0-or-later</span>
     <span>Good Ancestor Foundation</span>
   </p>
 </footer>
</main>

<div id="overlay" style="display:none;position:fixed;inset:0;background:var(--paper);
  z-index:50;flex-direction:column;align-items:center;justify-content:center;gap:16px;padding:24px">
  <div class="spinner" style="width:34px;height:34px;border:3px solid var(--line);
    border-top-color:var(--accent);border-radius:50%;animation:spin .9s linear infinite"></div>
  <div id="overlay-msg" style="font:400 20px/1.3 var(--serif);color:var(--ink);text-align:center">
    Generating your report&hellip;</div>
  <div style="font-size:13px;color:var(--mut);text-align:center;max-width:30em">
    Matching your markers against published research.
    <span id="elapsed" style="font-family:var(--mono);color:var(--faint)"></span>
  </div>
</div>
<style>@keyframes spin{to{transform:rotate(360deg)}}</style>

<script>
 // ---- recognise the chosen file client-side ---------------------------
 // Cosmetic only: it mirrors the labels dnareport.detect produces so the user
 // gets an instant "yes, I know what this is" before uploading. The server
 // re-detects from content and is the only authority — a wrong guess here
 // changes nothing about how the file is actually routed.
 const KIND_HINTS = [
   [/\\.idat$/i,                 'Illumina IDAT (raw array)',        'heavy'],
   [/\\.(mod)?bam$/i,            'ONT modBAM (genome + methylome)',  'heavy'],
   [/\\.vcf(\\.gz)?$/i,           'VCF genome',                       'light'],
   [/\\.bed(methyl)?(\\.gz)?$/i,  'bedMethyl methylation calls',      'light'],
   [/23andme|genome_.*\\.txt$/i,  '23andMe raw export',               'light'],
   [/ancestry|ftdna|myheritage|livingdna/i, 'Consumer genotype export', 'light'],
   [/\\.(csv|tsv|txt)(\\.gz)?$/i, 'Methylation beta-value table',     'light'],
 ];
 function describe(f){
   for(const [re,label,weight] of KIND_HINTS){ if(re.test(f.name)) return {label,weight}; }
   return {label:'Unrecognised — the server will sniff the contents', weight:'light'};
 }
 function humanSize(b){
   if(b < 1024) return b + ' B';
   const u=['KB','MB','GB','TB']; let i=-1;
   do{ b/=1024; i++; }while(b>=1024 && i<u.length-1);
   return b.toFixed(b<10?1:0) + ' ' + u[i];
 }

 const drop=document.getElementById('drop'), fileIn=document.getElementById('file'),
   go=document.getElementById('go'), statusEl=document.getElementById('status'),
   tissue=document.getElementById('tissue'), recog=document.getElementById('recog'),
   recogName=document.getElementById('recog-name'), recogMeta=document.getElementById('recog-meta');
 let chosen=null;

 function pick(f){
   chosen=f; go.disabled=!f; statusEl.textContent='';
   if(!f){ recog.classList.remove('show'); return; }
   const d=describe(f);
   recogName.textContent=f.name;
   const route = d.weight==='heavy'
     ? 'goes through the large-file upload flow'
     : 'analysed here, straight away';
   // built as nodes, not markup — f.name is user-controlled and must never be
   // parsed as HTML
   recogMeta.replaceChildren(
     Object.assign(document.createElement('b'),{textContent:d.label}),
     document.createTextNode(' \\u00b7 '+humanSize(f.size)+' \\u00b7 '+route));
   recog.classList.add('show');
 }
 fileIn.onchange=e=>pick(e.target.files[0]);

 // keyboard parity with the click target
 drop.addEventListener('keydown',e=>{
   if(e.key==='Enter'||e.key===' '){ e.preventDefault(); fileIn.click(); }
 });
 ['dragover','dragenter'].forEach(ev=>drop.addEventListener(ev,e=>{
   e.preventDefault(); drop.classList.add('over');}));
 ['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{
   e.preventDefault(); drop.classList.remove('over');}));
 drop.addEventListener('drop',e=>{ if(e.dataTransfer.files[0]) pick(e.dataTransfer.files[0]); });

 // ---- overlay + elapsed clock ----------------------------------------
 const overlay=document.getElementById('overlay'), elapsed=document.getElementById('elapsed');
 let tick=null;
 function showOverlay(){
   overlay.style.display='flex';
   const t0=Date.now();
   clearInterval(tick);
   tick=setInterval(()=>{ elapsed.textContent=' ('+Math.round((Date.now()-t0)/1000)+'s)'; },1000);
 }
 function hideOverlay(){ overlay.style.display='none'; clearInterval(tick); elapsed.textContent=''; }

 document.querySelectorAll('.demo-link').forEach(a=>a.addEventListener('click',showOverlay));
 // ALWAYS clear the overlay when the page is shown — including back/forward
 // (bfcache) restores, which replay the page with the overlay still visible and
 // would otherwise leave it stuck forever with no reload to reset it.
 window.addEventListener('pageshow',hideOverlay);

 // Swap this page for the returned report document. Parsed into a detached
 // document and grafted in as a node — the report is static HTML+CSS, so
 // nothing needs to execute, and this never treats the response as script.
 function showReport(htmlText){
   const doc = new DOMParser().parseFromString(htmlText,'text/html');
   document.replaceChild(document.importNode(doc.documentElement,true),
                         document.documentElement);
 }

 // ---- failure panel ---------------------------------------------------
 // Every stop shows the SAME shape the server's refusal page uses: a code, a
 // title, what happened, what to do, and (for a format problem) what we accept.
 const failEl=document.getElementById('fail'), failCode=document.getElementById('fail-code'),
   failTitle=document.getElementById('fail-title'), failMsg=document.getElementById('fail-msg'),
   failHint=document.getElementById('fail-hint'), failList=document.getElementById('fail-list');

 function clearFail(){ failEl.classList.remove('show'); }
 function showFail(e){
   // textContent throughout: every field here can carry a filename or a server
   // string and must never be parsed as markup
   failCode.textContent='Upload stopped \\u00b7 '+(e.code||'error');
   failTitle.textContent=e.title||'That upload did not go through';
   failMsg.textContent=e.message||'';
   failHint.hidden=!e.hint; failHint.textContent=e.hint||'';
   failList.replaceChildren();
   failList.hidden=!(e.accepted&&e.accepted.length);
   (e.accepted||[]).forEach(a=>failList.appendChild(
     Object.assign(document.createElement('li'),{textContent:a})));
   failEl.classList.add('show');
   failEl.scrollIntoView({behavior:'smooth',block:'nearest'});
 }

 // ---- unwrap archives in the browser ----------------------------------
 // 23andMe, AncestryDNA, MyHeritage and FTDNA all hand you a ZIP, so a ZIP is
 // what people actually upload. Unpacking it here rather than on the server
 // means the genotype text is all that crosses the network: a smaller upload,
 // and one less copy of an archive to handle. Uses the platform's own
 // DecompressionStream — no library, consistent with this page fetching nothing
 // from anywhere. If the browser lacks it we upload as-is; the server unwraps
 // ZIPs too, so this is an optimisation, never the only line of defence.
 const NOISE=/(^|\\/)(__MACOSX\\/|\\.|readme|license|checksum)|\\.md5$/i;

 async function inflate(blob,fmt){
   return await new Response(blob.stream().pipeThrough(
     new DecompressionStream(fmt))).blob();
 }
 async function readLE(blob,start,len){
   return new DataView(await blob.slice(start,start+len).arrayBuffer());
 }

 async function unzip(file){
   // locate the end-of-central-directory record in the tail
   const tailLen=Math.min(file.size,66000);
   const tail=new DataView(await file.slice(file.size-tailLen).arrayBuffer());
   let eocd=-1;
   for(let i=tail.byteLength-22;i>=0;i--){
     if(tail.getUint32(i,true)===0x06054b50){ eocd=i; break; }
   }
   if(eocd<0) throw new Error('not a readable zip');
   const count=tail.getUint16(eocd+10,true),
         cdSize=tail.getUint32(eocd+12,true),
         cdOff=tail.getUint32(eocd+16,true);
   if(cdOff===0xffffffff) throw new Error('zip64 unsupported');

   const cd=new DataView(await file.slice(cdOff,cdOff+cdSize).arrayBuffer());
   const dec=new TextDecoder();
   let p=0,best=null;
   for(let i=0;i<count&&p+46<=cd.byteLength;i++){
     if(cd.getUint32(p,true)!==0x02014b50) break;
     const method=cd.getUint16(p+10,true),
           cSize=cd.getUint32(p+20,true),
           uSize=cd.getUint32(p+24,true),
           nLen=cd.getUint16(p+28,true),
           xLen=cd.getUint16(p+30,true),
           kLen=cd.getUint16(p+32,true),
           lOff=cd.getUint32(p+42,true),
           name=dec.decode(new Uint8Array(cd.buffer,cd.byteOffset+p+46,nLen));
     p+=46+nLen+xLen+kLen;
     if(name.endsWith('/')||!uSize||NOISE.test(name)) continue;
     if(method!==0&&method!==8) continue;
     if(!best||uSize>best.uSize) best={method,cSize,uSize,lOff,name};
   }
   if(!best) throw new Error('nothing readable inside the zip');

   // local header tells us where this member's bytes actually start
   const lh=await readLE(file,best.lOff,30);
   if(lh.getUint32(0,true)!==0x04034b50) throw new Error('bad local header');
   const start=best.lOff+30+lh.getUint16(26,true)+lh.getUint16(28,true);
   const raw=file.slice(start,start+best.cSize);
   const out=best.method===8?await inflate(raw,'deflate-raw'):raw;
   return new File([out],best.name.split('/').pop(),{type:'text/plain'});
 }

 // Returns the file to actually upload. Never throws: if unpacking fails we
 // hand back the original and let the server have its own go at it.
 async function unwrap(file){
   if(typeof DecompressionStream==='undefined') return file;
   const n=file.name.toLowerCase();
   try{
     if(n.endsWith('.zip')){
       statusEl.textContent='Unpacking your archive\\u2026';
       return await unzip(file);
     }
     if(n.endsWith('.gz')&&!n.endsWith('.vcf.gz')){
       statusEl.textContent='Decompressing\\u2026';
       const out=await inflate(file,'gzip');
       return new File([out],file.name.replace(/\\.gz$/i,''),{type:'text/plain'});
     }
   }catch(err){ /* fall through and upload the original */ }
   return file;
 }

 // Map a transport-level failure onto the same structured shape, so a proxy
 // error page or a dropped connection reads like every other stop.
 function transportError(r){
   if(r.status===413) return {code:'too_large',
     title:'That file was too large to send',
     message:'The upload was rejected before it reached the analyser because of its size.',
     hint:'Whole-genome files belong on the large-file upload, which sends them in parts.'};
   if(r.status===429) return {code:'rate_limited',title:'Too many requests just now',
     message:'This instance is rate-limiting requests.',hint:'Wait a minute and try again.'};
   if(r.status===403) return {code:'blocked_in_transit',
     title:'Something between us and the analyser blocked this upload',
     message:'The request never reached the analyser \\u2014 an edge security rule rejected it first. '+
       'Compressed archives are the usual trigger.',
     hint:'Unzip the file and upload the data file inside it directly.'};
   if(r.status>=500) return {code:'server_error',title:'The analyser hit an error',
     message:'Something failed on our side while handling this file (HTTP '+r.status+').',
     hint:'This is our bug, not your file. Trying again shortly is worth a shot.'};
   return {code:'http_'+r.status,title:'That upload did not go through',
     message:'The server answered with HTTP '+r.status+'.'};
 }

 go.onclick=async()=>{
   if(!chosen)return;
   go.disabled=true; clearFail();
   showOverlay();
   let r;
   try{
     const payload=await unwrap(chosen);
     statusEl.textContent='Analyzing\\u2026 this runs on the server and may take a moment.';
     const fd=new FormData(); fd.append('file',payload);
     if(tissue.value)fd.append('tissue',tissue.value);
     // opt-ins (only used if the file is large enough to be queued; ignored for
     // inline analysis, which returns the report immediately)
     const em=document.getElementById('notify_email');
     const nl=document.getElementById('newsletter');
     if(em&&em.value)fd.append('notify_email',em.value);
     if(nl&&nl.checked)fd.append('newsletter','1');
     r=await fetch('/analyze',{method:'POST',body:fd,headers:{'Accept':'text/html'}});
   }catch(err){
     hideOverlay(); statusEl.textContent='';
     showFail({code:'network',title:'We could not reach the analyser',
       message:String(err&&err.message||err),
       hint:'Check your connection and try again \\u2014 nothing was uploaded.'});
     go.disabled=false; return;
   }

   const ct=r.headers.get('content-type')||'';
   // Only a SUCCESSFUL response may replace this page. A 403/502 from an edge
   // proxy is also text/html, and grafting one in would swap the app for a
   // stranger's error page — which is exactly how a failed upload used to look
   // like the site breaking.
   if(r.ok&&ct.includes('text/html')){ showReport(await r.text()); return; }

   hideOverlay(); statusEl.textContent=''; go.disabled=false;
   let err=null;
   if(ct.includes('application/json')){
     const j=await r.json().catch(()=>null);
     if(j) err=j.error||{code:'error',title:'That upload did not go through',
                          message:j.detail||JSON.stringify(j)};
   }
   showFail(err||transportError(r));
 };
</script>
</body></html>"""

LANDING_HTML = _LANDING_TEMPLATE.replace("__VERSION__", __version__)

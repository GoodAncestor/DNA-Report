"""The landing page HTML for the DNA-Report front door.

Deliberately restrained: a moderately-technical lay reader should understand it,
and it makes NO definitive health claims. The tone is the intentional opposite of
salesy "quantify your exact pace of cellular degradation" marketing copy — it
states what the tool does, names the evidence tiers, and keeps the single
consolidated disclaimer in one place (not scattered).
"""
from __future__ import annotations

TISSUES = ["blood", "saliva", "buccal", "other"]

LANDING_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>DNA-Report — methylation & variant interpretation</title>
<style>
 :root{--ink:#1a1a1a;--mut:#666;--line:#e4e4e2;--bg:#fbfbfa;--card:#fff;
   --accent:#2b6a5b;--robust:#0c7a54;--spec:#b8860b;--unknown:#8a8a8a;}
 @media(prefers-color-scheme:dark){:root{--ink:#eee;--mut:#a9a9a5;--line:#333;
   --bg:#141414;--card:#1d1d1c;--accent:#4bbf9f;}}
 *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
   font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
 main{max-width:760px;margin:0 auto;padding:40px 22px 70px}
 h1{font-size:24px;margin:0 0 6px}
 .sub{color:var(--mut);margin:0 0 28px;font-size:15px}
 .drop{border:2px dashed var(--line);border-radius:12px;padding:34px 22px;text-align:center;
   background:var(--card);transition:border-color .15s}
 .drop.over{border-color:var(--accent)}
 .drop p{margin:8px 0;color:var(--mut);font-size:14px}
 button,.btn{font:inherit;cursor:pointer;border-radius:8px;border:1px solid var(--line);
   background:var(--card);color:var(--ink);padding:9px 15px;text-decoration:none;display:inline-block}
 .primary{background:var(--accent);color:#fff;border-color:var(--accent);font-weight:600}
 .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:16px 0}
 label{font-size:14px;color:var(--mut)}
 select{font:inherit;padding:7px 9px;border-radius:8px;border:1px solid var(--line);
   background:var(--card);color:var(--ink)}
 .demos{margin-top:26px;padding-top:22px;border-top:1px solid var(--line)}
 .demos h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:0 0 10px}
 .hint{font-size:13px;color:var(--mut);margin-top:5px}
 .tiers{display:flex;gap:16px;margin:18px 0 0;font-size:13px;color:var(--mut);flex-wrap:wrap}
 .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:middle}
 .status{margin-top:18px;font-size:14px;color:var(--mut);min-height:20px}
 footer{margin-top:34px;padding-top:18px;border-top:1px solid var(--line);font-size:13px;color:var(--mut)}
 code{font-family:ui-monospace,Menlo,monospace;font-size:13px}
</style></head><body><main>
 <h1>DNA-Report</h1>
 <p class="sub">Read a methylation or genome file and get an interpretation grouped by
   how much scientific support each finding has. Everything runs on the server —
   your file is not shared with any third party.</p>

 <div class="drop" id="drop">
   <p><strong>Drop a file here</strong> or <label class="btn" for="file">choose a file</label>
     <input id="file" type="file" hidden></p>
   <p>Methylation beta-value export (e.g. a TruDiagnostic CSV), an EPIC array file,
     a 23andMe/VCF genome, or an ONT modBAM.</p>
 </div>

 <div class="row">
   <label for="tissue">Sample type:</label>
   <select id="tissue">
     <option value="">(auto-detect)</option>
     <option value="blood">Blood</option>
     <option value="saliva">Saliva</option>
     <option value="buccal">Buccal / cheek swab</option>
     <option value="other">Other</option>
   </select>
   <span class="hint" id="tissuehint">We infer this from your file and use it to decide
     which epigenetic clocks are valid &mdash; you can override it.</span>
 </div>

 <button class="primary" id="go" disabled>Analyze</button>
 <div class="status" id="status"></div>

 <div class="tiers">
   <span><span class="dot" style="background:var(--robust)"></span>Robust</span>
   <span><span class="dot" style="background:var(--spec)"></span>Speculative</span>
   <span><span class="dot" style="background:var(--unknown)"></span>Unknown / limited</span>
 </div>

 <div class="demos">
   <h2>No file? Try a real sample</h2>
   <div class="row">
     <a class="btn demo-link" href="/demo/blood">Whole-blood methylome (adult)</a>
     <a class="btn demo-link" href="/demo/buccal">Buccal methylome (pediatric research sample)</a>
     <a class="btn demo-link" href="/demo/combined">Methylome + genome (combined)</a>
   </div>
   <p class="hint">Both are real public methylation profiles. The buccal one is a
     children's research sample &mdash; it shows how the report flags clocks that are not
     valid for a given sample type, rather than showing a misleading number.</p>
 </div>

 <footer><strong>Not medical advice.</strong> DNA-Report shows research associations
   for your markers, labelled by how much evidence supports them. Your file is
   processed on the server and shared with no one. Every report ends with the full
   note on how to read it.</footer>
</main>
<div id="overlay" style="display:none;position:fixed;inset:0;background:rgba(251,251,250,.92);
  z-index:50;flex-direction:column;align-items:center;justify-content:center;gap:14px">
  <div class="spinner" style="width:38px;height:38px;border:4px solid #d8d8d4;
    border-top-color:#2b6a5b;border-radius:50%;animation:spin 0.9s linear infinite"></div>
  <div id="overlay-msg" style="font-size:15px;color:#444">Generating your report&hellip;</div>
  <div style="font-size:12.5px;color:#888">Matching your markers against published research &mdash; this can take a few seconds.</div>
</div>
<style>@keyframes spin{to{transform:rotate(360deg)}}
@media(prefers-color-scheme:dark){#overlay{background:rgba(20,20,20,.92)}#overlay-msg{color:#ddd}}</style>
<script>
 // show a loading overlay when a demo link is clicked (server render takes several
 // seconds while it matches markers live, so give immediate feedback)
 document.querySelectorAll('.demo-link').forEach(function(a){
   a.addEventListener('click',function(){
     document.getElementById('overlay').style.display='flex';
   });
 });
 const drop=document.getElementById('drop'),fileIn=document.getElementById('file'),
   go=document.getElementById('go'),statusEl=document.getElementById('status'),
   tissue=document.getElementById('tissue');
 let chosen=null;
 function pick(f){chosen=f;go.disabled=!f;statusEl.textContent=f?('Selected: '+f.name):'';}
 fileIn.onchange=e=>pick(e.target.files[0]);
 ['dragover','dragenter'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add('over');}));
 ['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove('over');}));
 drop.addEventListener('drop',e=>{if(e.dataTransfer.files[0])pick(e.dataTransfer.files[0]);});
 go.onclick=async()=>{
   if(!chosen)return;
   go.disabled=true;statusEl.textContent='Analyzing\\u2026 this runs on the server and may take a moment.';
   const fd=new FormData();fd.append('file',chosen);
   if(tissue.value)fd.append('tissue',tissue.value);
   try{
     const r=await fetch('/analyze',{method:'POST',body:fd});
     const ct=r.headers.get('content-type')||'';
     if(ct.includes('text/html')){
       document.open();document.write(await r.text());document.close();
     }else{const j=await r.json();statusEl.textContent=(j.detail||JSON.stringify(j));go.disabled=false;}
   }catch(err){statusEl.textContent='Error: '+err;go.disabled=false;}
 };
</script>
</body></html>"""

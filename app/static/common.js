/* Shared across the chat, evaluation and corpus pages. */
const $ = s => document.querySelector(s);
const esc = s => (s==null?'':String(s)).replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
const pct = n => (n==null?'\u2014':(n*100).toFixed(0)+'%');
let CFG={}, METRICS=null;

/* ------------------------------- theme ------------------------------- */
function initTheme(){
  const b = $('#theme'); if(!b) return;
  b.onclick = () => {
    const cur = document.documentElement.getAttribute('data-theme');
    const next = cur === 'dark' ? 'light' : (cur === 'light' ? '' : 'dark');
    if(next) document.documentElement.setAttribute('data-theme', next);
    else document.documentElement.removeAttribute('data-theme');
  };
}

/* ---------------------------- shared boot ---------------------------- */
async function loadConfig(){
  CFG = await (await fetch('/api/config')).json();
  const c = $('#b-corpus'), e = $('#b-embed'), g = $('#b-gen');
  if(c) c.innerHTML = `<b>${(CFG.stats.total||0).toLocaleString()}</b> chunks`;
  if(e) e.innerHTML = `embed <b>${esc(CFG.providers.embedding)}</b>`;
  if(g) g.innerHTML = CFG.providers.generation_available
    ? `gen <b>${esc(CFG.providers.generation)}</b>`
    : `gen <b>not configured</b> \u2014 retrieval only`;

  const a = $('#attrib');
  if(a) a.innerHTML = CFG.attribution.map(x=>`<div>${esc(x)}</div>`).join('') +
    `<div style="margin-top:7px">This assistant provides information based on legislation
     and AUSTRAC guidance. It does not provide legal advice and does not guarantee
     compliance. Your obligations remain with you as the reporting entity.
     Not endorsed by or affiliated with AUSTRAC. Meridian Accounting Partners and
     Corvid Advisory are fictional firms; their documents were written for this demo.</div>`;
  return CFG;
}

async function loadMetrics(){
  if(!METRICS) METRICS = await (await fetch('/api/metrics')).json();
  return METRICS;
}

/* Tenant-scoped: the server 404s a chunk owned by a different client, so a
   citation key alone is never enough to read another firm's document. */
async function showSource(key, tenantId){
  const t = tenantId !== undefined ? tenantId
          : ($('#tenant') ? $('#tenant').value : '');
  const r = await fetch('/api/source/' + encodeURIComponent(key) +
                        '?tenant_id=' + encodeURIComponent(t||''));
  if(!r.ok){
    $('#dlg-title').textContent = 'Not available';
    $('#dlg-body').textContent =
      'That source is not in the current client\u2019s corpus.';
    $('#dlg').showModal();
    return;
  }
  const d = await r.json();
  $('#dlg-title').textContent = d.heading_path.join('  \u203a  ');
  $('#dlg-body').textContent = d.content + `\n\n\u2014 ${d.citation_key}` +
    (d.compilation_no ? `  \u00b7  compilation ${d.compilation_no}` : '') +
    (d.effective_from ? `  \u00b7  in force ${d.effective_from}` : '') +
    `\n${d.source_url}`;
  $('#dlg').showModal();
}

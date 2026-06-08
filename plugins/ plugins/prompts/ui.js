// ════════════════════════════════════════════════════════
//  Plugin DuckDuckGo — recherche web agentique
// ════════════════════════════════════════════════════════
var DDG = {query:'', results:[], synthesis:'', fname:''};

function openDuckDuckGo(){
  if(!ACTIVE){ toast('Ouvrez d\'abord un fichier .md'); return; }
  DDG = {query:'', results:[], synthesis:'', question:'', sysPrompt:'',
    fname: ACTIVE.split(/[/\\]/).pop().replace(/\.md$/i,'')};
  $('ddg-hint').value = '';
  $('ddg-question').value = '';
  $('ddg-sys-prompt').value = '';
  if($('ddg-fetch-full')) $('ddg-fetch-full').checked = true;
  $('ddg-form').style.display = 'block';
  $('ddg-progress').style.display = 'none';
  $('ddg-results').style.display = 'none';
  $('ddg-error').style.display = 'none';
  $('ddg-edit-query').style.display = 'none';
  $('ddg-retry-search').style.display = 'none';
  $('ddg-syn-block').style.display = 'none';
  $('ddg-save-btn').style.display = 'none';
  $('ddg-syn-btn').disabled = false;
  $('ddg-syn-btn').textContent = '✨ Synthétiser';
  $('mddg').classList.add('on');
}

function closeDuckDuckGo(){
  $('mddg').classList.remove('on');
}

function setDDGStep(n, state){
  var el = $('ddg-step'+n);
  el.classList.remove('muted','done','fail');
  var labels = {
    1: {a:'⏳ Étape 1/3 — L\'IA génère la requête…',  d:'✓ Requête générée',         f:'✗ Échec génération requête'},
    2: {a:'⏳ Étape 2/3 — Interrogation de DuckDuckGo…', d:'✓ Résultats récupérés',     f:'✗ Échec DuckDuckGo'},
    3: {a:'⏳ Étape 3/3 — Synthèse en cours…',           d:'✓ Synthèse produite',       f:'✗ Échec synthèse'}
  };
  if(state==='active'){ el.textContent = labels[n].a; }
  else if(state==='done'){ el.textContent = labels[n].d; el.classList.add('done'); }
  else if(state==='fail'){ el.textContent = labels[n].f; el.classList.add('fail'); }
  else { el.textContent = '○ Étape '+n+'/3'; el.classList.add('muted'); }
}

async function runDDG(){
  if(!ACTIVE){ toast('Aucun fichier ouvert'); return; }
  var content = $('md-editor').value;
  var name    = ACTIVE.split(/[/\\]/).pop();
  var hint    = $('ddg-hint').value.trim();
  var n       = parseInt($('ddg-n').value);
  var fetchFull = $('ddg-fetch-full') ? $('ddg-fetch-full').checked : true;

  $('ddg-form').style.display = 'none';
  $('ddg-progress').style.display = 'block';
  $('ddg-error').style.display = 'none';
  setDDGStep(1,'active'); setDDGStep(2,'pending'); setDDGStep(3,'pending');
  if(fetchFull){
    setTimeout(function(){
      if($('ddg-step2').classList.contains('muted')) return;
      $('ddg-step2').textContent = '⏳ Étape 2/3 — DDG + lecture des pages (peut prendre 10-20 s)…';
    }, 1500);
  }

  var r = await post('/api/ddg/agentic', {content:content, name:name, hint:hint, n:n,
    fetch_full: fetchFull, n_fetch: 5});

  if(r.error){
    $('ddg-error-msg').textContent = r.error;
    $('ddg-error').style.display = 'block';
    if(r.query){
      setDDGStep(1,'done'); setDDGStep(2,'fail');
      DDG.query = r.query;
      $('ddg-query-edit').value = r.query;
      $('ddg-edit-query').style.display = 'block';
      $('ddg-retry-search').style.display = 'inline-flex';
    } else {
      setDDGStep(1,'fail');
      $('ddg-edit-query').style.display = 'none';
      $('ddg-retry-search').style.display = 'none';
    }
    toast('⚠ Échec — voir détail dans le panneau', 4000);
    return;
  }

  setDDGStep(1,'done'); setDDGStep(2,'done');
  DDG.query = r.query;
  DDG.results = r.results || [];

  $('ddg-results').style.display = 'block';
  $('ddg-query').textContent = r.query;
  $('ddg-count').textContent = DDG.results.length;
  // Indicateur méta sur le fetch
  if(r.fetch_full){
    var info = ' · 📖 ' + (r.fetched_count||0) + '/' + Math.min(5, DDG.results.length) + ' pages lues';
    $('ddg-fetch-info').textContent = info;
  } else {
    $('ddg-fetch-info').textContent = ' · snippets uniquement';
  }
  renderDDGResults(DDG.results);
  if(!DDG.results.length) toast('Aucun résultat trouvé', 4000);
  // Focus sur la question pour inciter l'utilisateur à la remplir
  setTimeout(function(){ if($('ddg-question')) $('ddg-question').focus(); }, 100);
}

async function retryDDGSearch(){
  var q = $('ddg-query-edit').value.trim();
  if(!q){ toast('Requête vide'); return; }
  var n = parseInt($('ddg-n').value);
  DDG.query = q;

  $('ddg-error').style.display = 'none';
  $('ddg-progress').style.display = 'block';
  setDDGStep(1,'done'); setDDGStep(2,'active'); setDDGStep(3,'pending');

  var r = await fetch('/api/ddg/search?q='+encodeURIComponent(q)+'&n='+n).then(function(r){return r.json();});
  if(r.error){
    setDDGStep(2,'fail');
    $('ddg-error-msg').textContent = r.error;
    $('ddg-error').style.display = 'block';
    $('ddg-edit-query').style.display = 'block';
    $('ddg-retry-search').style.display = 'inline-flex';
    toast('⚠ DDG encore en échec', 4000);
    return;
  }
  DDG.results = r.results || [];
  setDDGStep(2,'done');
  $('ddg-results').style.display = 'block';
  $('ddg-query').textContent = q;
  $('ddg-count').textContent = DDG.results.length;
  renderDDGResults(DDG.results);
  if(!DDG.results.length) toast('Aucun résultat', 4000);
}

function renderDDGResults(results){
  if(!results.length){
    $('ddg-list').innerHTML = '<div style="padding:14px;color:var(--tx2);font-size:12px;text-align:center">Aucun résultat pour cette requête. Affinez l\'indication et relancez.</div>';
    return;
  }
  $('ddg-list').innerHTML = results.map(function(r,i){
    var safeUrl = esc(r.url||'');
    var domain = '';
    try { domain = new URL(r.url).hostname.replace(/^www\./,''); } catch(e){ domain = r.url; }
    // Badge fetch
    var badge = '';
    if(r.fetch_status === 'ok'){
      badge = '<span style="color:var(--grn);font-size:10px;margin-left:6px">📖 lu (' + (r.fetch_chars||0) + ' chars)</span>';
    } else if(r.fetch_status && r.fetch_status !== 'non récupéré'){
      badge = '<span style="color:var(--red);font-size:10px;margin-left:6px;opacity:.7" title="'+esc(r.fetch_status)+'">⚠ '+esc(r.fetch_status.substring(0,25))+'</span>';
    } else if(r.fetch_status === 'non récupéré'){
      badge = '<span style="color:var(--tx2);font-size:10px;margin-left:6px">○ snippet seul</span>';
    }
    return '<div class="ddg-item">'
      + '<div class="ddg-item-title"><span class="ddg-item-num">['+(i+1)+']</span><a href="'+safeUrl+'" target="_blank" rel="noopener">'+esc(r.title||'(sans titre)')+'</a>'+badge+'</div>'
      + '<div class="ddg-item-url"><a href="'+safeUrl+'" target="_blank" rel="noopener">'+esc(domain)+'</a></div>'
      + '<div class="ddg-item-snippet">'+esc(r.snippet||'')+'</div>'
      + '</div>';
  }).join('');
}

async function synthesizeDDG(){
  if(!DDG.results.length){ toast('Pas de résultats à synthétiser'); return; }
  var question  = $('ddg-question').value.trim();
  var sysPrompt = $('ddg-sys-prompt').value.trim();
  DDG.question  = question;
  DDG.sysPrompt = sysPrompt;

  setDDGStep(3,'active');
  $('ddg-step3').textContent = '⏳ Étape 3/3 — Synthèse… (peut prendre 1-3 min)';
  $('ddg-syn-btn').disabled = true;
  $('ddg-syn-btn').textContent = '⏳ Synthèse en cours…';
  $('ddg-error').style.display = 'none';

  var r = await post('/api/ddg/synthesize', {
    results: DDG.results, content: $('md-editor').value,
    name: ACTIVE.split(/[/\\]/).pop(), query: DDG.query,
    question: question, system_prompt: sysPrompt
  });
  if(r.error){
    setDDGStep(3,'fail');
    $('ddg-error-msg').textContent = r.error;
    $('ddg-error').style.display = 'block';
    $('ddg-syn-btn').disabled = false;
    $('ddg-syn-btn').textContent = '🔄 Réessayer la synthèse';
    toast('⚠ '+r.error.substring(0,80), 5000);
    return;
  }
  DDG.synthesis = r.synthesis;
  setDDGStep(3,'done');
  $('ddg-syn-block').style.display = 'block';
  // Méta sur la qualité de la matière utilisée
  var meta = '';
  if(r.used_full_content !== undefined){
    meta = ' · ' + r.used_full_content + ' page(s) lue(s), ' + r.used_snippet_only + ' snippet(s) seul(s)';
    if(question) meta += ' · question ciblée';
  }
  $('ddg-syn-meta').textContent = meta;
  $('ddg-synthesis').textContent = r.synthesis;
  $('ddg-save-btn').style.display = 'inline-flex';
  $('ddg-syn-btn').style.display = 'none';
}

async function saveDDGAsFile(){
  if(!DDG.synthesis){ toast('Pas de synthèse à sauver'); return; }
  // Nom basé sur la question si présente, sinon sur le fichier source
  var slugBase = DDG.question
    ? DDG.question.substring(0,40).replace(/[^\w\sàâäéèêëïîôöùûüç-]/gi,'').replace(/\s+/g,'-').toLowerCase()
    : DDG.fname;
  var base = safeFileName('ddg-' + slugBase);
  var newPath = null;
  for(var i=1; i<30; i++){
    var tryName = i===1 ? base : base+'-'+i;
    var resp = await post('/api/files/new', {dir: CUR_DIR, name: tryName});
    if(resp.ok){ newPath = resp.path; break; }
    if(resp.error !== 'Fichier existant'){ toast('⚠ '+resp.error); return; }
  }
  if(!newPath){ toast('⚠ Création impossible'); return; }

  var head = '# 🦆 Recherche web — ' + DDG.fname + '\n\n';
  head += '*Source :* [['+DDG.fname+']]  \n';
  head += '*Requête utilisée :* `' + DDG.query + '`  \n';
  if(DDG.question) head += '*Question :* ' + DDG.question + '  \n';
  if(DDG.sysPrompt) head += '*Prompt système :* `' + DDG.sysPrompt.substring(0,200) + '`  \n';
  head += '*Résultats analysés :* ' + DDG.results.length;
  var fetched = DDG.results.filter(function(r){return r.fetch_status==='ok';}).length;
  if(fetched > 0) head += ' (dont ' + fetched + ' avec contenu complet)';
  head += '\n\n---\n\n';
  var resList = DDG.results.map(function(r,i){
    return '['+(i+1)+'] **'+r.title+'** — '+r.url;
  }).join('\n');
  var content = head + DDG.synthesis + '\n\n---\n\n## Liste brute des résultats\n\n' + resList + '\n';

  await post('/api/files/save', {path: newPath, content: content});
  openFileTab(newPath, content);
  loadDir(CUR_DIR);
  closeDuckDuckGo();
  toast('✓ Synthèse sauvegardée');
}

// Handlers Escape + clic-extérieur
document.addEventListener('click', function(e){
  if(e.target === document.getElementById('mddg')) closeDuckDuckGo();
});
document.addEventListener('keydown', function(e){
  if(e.key === 'Escape' && document.getElementById('mddg') && document.getElementById('mddg').classList.contains('on')) closeDuckDuckGo();
});

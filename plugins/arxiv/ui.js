
// ══════════════════════════════════════════════════
//  RECHERCHE ARXIV AGENTIQUE
// ══════════════════════════════════════════════════
var ARX={query:'',papers:[],synthesis:'',fname:''};

function openArxiv(){
  if(!ACTIVE){ toast('Ouvrez d\'abord un fichier .md'); return; }
  ARX={query:'',papers:[],synthesis:'',fname:ACTIVE.split(/[/\\]/).pop().replace(/\.md$/i,'')};
  $('arx-hint').value=''; $('arx-form').style.display='block';
  $('arx-progress').style.display='none'; $('arx-results').style.display='none';
  $('arx-error').style.display='none';
  $('arx-edit-query').style.display='none'; $('arx-retry-search').style.display='none';
  $('arx-syn-block').style.display='none'; $('arx-save-btn').style.display='none';
  $('arx-syn-btn').disabled=false; $('arx-syn-btn').textContent='✨ Synthétiser';
  $('marxiv').classList.add('on');
}
function closeArxiv(){ $('marxiv').classList.remove('on'); }

function setStep(n, state){
  // state: 'pending' | 'done' | 'fail' | 'active'
  var el=$('arx-step'+n);
  el.classList.remove('muted','done','fail');
  var labels={
    1:{a:'⏳ Étape 1/3 — L\'IA génère la requête…', d:'✓ Requête générée',          f:'✗ Échec génération requête'},
    2:{a:'⏳ Étape 2/3 — Interrogation d\'ArXiv…',   d:'✓ Papiers récupérés',         f:'✗ Échec ArXiv'},
    3:{a:'⏳ Étape 3/3 — Synthèse en cours…',         d:'✓ Synthèse produite',          f:'✗ Échec synthèse'}
  };
  if(state==='active') el.textContent=labels[n].a;
  else if(state==='done'){ el.textContent=labels[n].d; el.classList.add('done'); }
  else if(state==='fail'){ el.textContent=labels[n].f; el.classList.add('fail'); }
  else { el.textContent='○ Étape '+n+'/3'; el.classList.add('muted'); }
}

async function runArxiv(){
  if(!ACTIVE){ toast('Aucun fichier ouvert'); return; }
  var content=$('md-editor').value;
  var name=ACTIVE.split(/[/\\]/).pop();
  var hint=$('arx-hint').value.trim();
  var n=parseInt($('arx-n').value);

  $('arx-form').style.display='none';
  $('arx-progress').style.display='block';
  $('arx-error').style.display='none';
  setStep(1,'active'); setStep(2,'pending'); setStep(3,'pending');
  setTimeout(function(){
    var el=$('arx-step2');
    if(el && el.classList.contains('muted')){
      el.textContent='⏳ Attente — politique de débit ArXiv (≤ 1 req / 3 s)';
      el.classList.remove('muted');
    }
  },1500);

  var r=await post('/api/arxiv/agentic',{content:content,name:name,hint:hint,n:n});
  if(r.error){
    $('arx-error-msg').textContent=r.error;
    $('arx-error').style.display='block';
    if(r.query){
      // L'IA a généré la requête, mais ArXiv a échoué. Permettre l'édition + retry ciblé.
      setStep(1,'done'); setStep(2,'fail');
      ARX.query=r.query;
      $('arx-query-edit').value=r.query;
      $('arx-edit-query').style.display='block';
      $('arx-retry-search').style.display='inline-flex';
    }else{
      // L'IA elle-même a échoué — pas de requête à éditer.
      setStep(1,'fail');
      $('arx-edit-query').style.display='none';
      $('arx-retry-search').style.display='none';
    }
    toast('⚠ Échec — voir détail dans le panneau',4000);
    return;
  }
  ARX.query=r.query; ARX.papers=r.papers||[];
  setStep(1,'done'); setStep(2,'done');

  // Affichage des résultats
  $('arx-results').style.display='block';
  $('arx-query').textContent=r.query;
  $('arx-count').textContent=ARX.papers.length;
  renderPapers(ARX.papers);
  if(!ARX.papers.length){
    toast('Aucun papier trouvé pour cette requête',4000);
  }
}

async function retryArxivSearch(){
  var q=$('arx-query-edit').value.trim();
  if(!q){ toast('Requête vide'); return; }
  var n=parseInt($('arx-n').value);
  ARX.query=q;

  // Reset visuel
  $('arx-error').style.display='none';
  $('arx-progress').style.display='block';
  setStep(1,'done'); setStep(2,'active'); setStep(3,'pending');

  var r=await fetch('/api/arxiv/search?q='+encodeURIComponent(q)+'&n='+n).then(function(r){return r.json();});
  if(r.error){
    setStep(2,'fail');
    $('arx-error-msg').textContent=r.error;
    $('arx-error').style.display='block';
    $('arx-edit-query').style.display='block';
    $('arx-retry-search').style.display='inline-flex';
    toast('⚠ ArXiv encore en échec — éditez ou attendez',4000);
    return;
  }
  ARX.papers=r.papers||[];
  setStep(2,'done');
  $('arx-results').style.display='block';
  $('arx-query').textContent=q;
  $('arx-count').textContent=ARX.papers.length;
  renderPapers(ARX.papers);
  if(!ARX.papers.length) toast('Aucun papier trouvé pour cette requête',4000);
}

function renderPapers(papers){
  if(!papers.length){
    $('arx-papers').innerHTML='<div style="padding:14px;color:var(--tx2);font-size:12px;text-align:center">Aucun résultat ArXiv pour cette requête. Affinez l\'indication et relancez.</div>';
    return;
  }
  $('arx-papers').innerHTML=papers.map(function(p,i){
    var auths=(p.authors||[]).slice(0,3).join(', ')+((p.authors||[]).length>3?'…':'');
    return '<div class="arx-paper">'
      +'<div class="arx-paper-title"><span class="arx-paper-num">['+(i+1)+']</span>'+esc(p.title||'')+'</div>'
      +'<div class="arx-paper-meta">'
      +'<span>👤 '+esc(auths)+'</span>'
      +'<span>📅 '+esc(p.published||'')+'</span>'
      +'<a href="'+esc(p.url||'')+'" target="_blank">↗ ArXiv</a>'
      +'</div>'
      +'<div class="arx-paper-abs" id="arx-abs-'+i+'">'+esc(p.abstract||'')+'</div>'
      +'<span class="arx-paper-toggle" onclick="togglePaperAbs('+i+')">⇣ Tout afficher</span>'
      +'</div>';
  }).join('');
}
function togglePaperAbs(i){
  var el=$('arx-abs-'+i);
  el.classList.toggle('expanded');
  el.nextElementSibling.textContent=el.classList.contains('expanded')?'⇡ Réduire':'⇣ Tout afficher';
}

async function synthesizeArxiv(){
  if(!ARX.papers.length){ toast('Pas de papiers à synthétiser'); return; }
  setStep(3,'active');
  $('arx-step3').textContent='⏳ Étape 3/3 — Synthèse… (peut prendre 1-3 min)';
  $('arx-syn-btn').disabled=true; $('arx-syn-btn').textContent='⏳ Synthèse en cours…';
  $('arx-error').style.display='none';

  var r=await post('/api/arxiv/synthesize',{
    papers:ARX.papers, content:$('md-editor').value, name:ACTIVE.split(/[/\\]/).pop(), query:ARX.query
  });
  if(r.error){
    setStep(3,'fail');
    $('arx-error-msg').textContent=r.error;
    $('arx-error').style.display='block';
    $('arx-edit-query').style.display='none';
    $('arx-retry-search').style.display='none';
    $('arx-syn-btn').disabled=false; $('arx-syn-btn').textContent='🔄 Réessayer la synthèse';
    toast('⚠ '+r.error.substring(0,80),5000);
    return;
  }
  ARX.synthesis=r.synthesis;
  setStep(3,'done');
  $('arx-syn-block').style.display='block';
  $('arx-synthesis').textContent=r.synthesis;
  $('arx-save-btn').style.display='inline-flex';
  $('arx-syn-btn').style.display='none';
}

async function saveArxivAsFile(){
  if(!ARX.synthesis){ toast('Pas de synthèse à sauvegarder'); return; }
  var base=safeFileName('arxiv-'+ARX.fname);
  var newPath=null;
  for(var i=1;i<30;i++){
    var tryName=i===1?base:base+'-'+i;
    var resp=await post('/api/files/new',{dir:CUR_DIR,name:tryName});
    if(resp.ok){ newPath=resp.path; break; }
    if(resp.error!=='Fichier existant'){ toast('⚠ '+resp.error); return; }
  }
  if(!newPath){ toast('⚠ Création impossible'); return; }

  // Construction du contenu final
  var head='# 🔬 Recherche ArXiv — '+ARX.fname+'\n\n';
  head+='*Source :* [['+ARX.fname+']]  \n';
  head+='*Requête utilisée :* `'+ARX.query+'`  \n';
  head+='*Papiers analysés :* '+ARX.papers.length+'\n\n---\n\n';
  var papersList=ARX.papers.map(function(p,i){
    return '['+(i+1)+'] **'+p.title+'** — '+(p.authors||[]).slice(0,3).join(', ')+' ('+p.published+') — '+p.url;
  }).join('\n');
  var content=head+ARX.synthesis+'\n\n---\n\n## Liste brute des papiers\n\n'+papersList+'\n';

  await post('/api/files/save',{path:newPath,content:content});
  openFileTab(newPath,content); loadDir(CUR_DIR); closeArxiv();
  toast('✓ Synthèse sauvegardée');
}


// Handlers spécifiques au plugin (close clic-extérieur + Escape)
document.addEventListener('click', function(e){
  if(e.target===document.getElementById('marxiv')) closeArxiv();
});
document.addEventListener('keydown', function(e){
  if(e.key==='Escape' && document.getElementById('marxiv').classList.contains('on')) closeArxiv();
});

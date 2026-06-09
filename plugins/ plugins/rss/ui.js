// ══════════════════════════════════════════════════════════════
//  Plugin RSS — gestion + analyse de flux
// ══════════════════════════════════════════════════════════════

var RSS = {
  feeds: [],            // [{name, url, tags, section}]
  fetchedFeeds: null,   // résultat du /api/rss/fetch
  syntheses: null,      // résultat du /api/rss/analyze
  mode: 'together',
  question: '',
  sysPrompt: '',
  // État d'exécution
  abortCtrl: null,      // AbortController pour annuler la requête en cours
  startTime: 0,         // Date.now() au début de l'analyse
  timerId: null,        // setInterval qui rafraîchit le chronomètre
};

function openRSS(){
  $('mrss').classList.add('on');
  rssSwitchTab('manage');
  rssReloadList();
}
function closeRSS(){
  rssAbortRunning();
  $('mrss').classList.remove('on');
}

function rssAbortRunning(){
  if(RSS.abortCtrl){
    try { RSS.abortCtrl.abort(); } catch(e){}
    RSS.abortCtrl = null;
  }
  if(RSS.timerId){
    clearInterval(RSS.timerId);
    RSS.timerId = null;
  }
}

function rssFormatElapsed(ms){
  var s = Math.floor(ms / 1000);
  var m = Math.floor(s / 60);
  s = s % 60;
  return (m > 0 ? m+' min ' : '') + s + ' s';
}

function rssSwitchTab(t){
  $('rss-tab-manage').classList.toggle('on', t==='manage');
  $('rss-tab-analyze').classList.toggle('on', t==='analyze');
  $('rss-pane-manage').style.display  = t==='manage'  ? 'block' : 'none';
  $('rss-pane-analyze').style.display = t==='analyze' ? 'block' : 'none';
  if(t==='analyze'){
    rssRenderAnalyzeList();
    // Reset l'affichage des résultats à chaque ouverture de l'onglet
    $('rss-result-block').style.display = 'none';
    $('rss-save-btn').style.display = 'none';
  }
}

// ─── ONGLET 1 : Gestion ───────────────────────────────────────

async function rssReloadList(){
  $('rss-feeds-list').innerHTML = '<div style="padding:20px;text-align:center;color:var(--tx2);font-size:12px">Chargement…</div>';
  var r = await fetch('/api/rss/list').then(function(r){return r.json();});
  RSS.feeds = r.feeds || [];
  $('rss-feeds-count').textContent = RSS.feeds.length;
  $('rss-vault-file').textContent = r.vault_file || 'flux-rss.md';
  rssRenderFeedsList();
}

function rssRenderFeedsList(){
  if(!RSS.feeds.length){
    $('rss-feeds-list').innerHTML = '<div style="padding:20px;text-align:center;color:var(--tx2);font-size:12px">Aucun flux enregistré. Ajoutez-en un ci-dessus.</div>';
    return;
  }
  // Regrouper par section
  var sections = {};
  RSS.feeds.forEach(function(f){
    var s = f.section || '(Sans section)';
    if(!sections[s]) sections[s] = [];
    sections[s].push(f);
  });
  var html = '';
  Object.keys(sections).sort().forEach(function(s){
    if(s !== '(Sans section)') html += '<div style="font-size:11px;color:var(--acc);font-weight:600;margin:10px 0 6px 4px;text-transform:uppercase;letter-spacing:.5px">'+esc(s)+'</div>';
    sections[s].forEach(function(f){
      var tagsHtml = (f.tags||[]).map(function(t){return '<span class="rss-tag">#'+esc(t)+'</span>';}).join('');
      var safeUrl = esc(f.url);
      var domain = '';
      try { domain = new URL(f.url).hostname.replace(/^www\./,''); } catch(e){ domain = f.url; }
      html += '<div class="rss-feed-row">'
        + '<div style="flex:1;min-width:0">'
        + '<div class="rss-feed-name">'+esc(f.name)+' '+tagsHtml+'</div>'
        + '<div class="rss-feed-meta"><a href="'+safeUrl+'" target="_blank" rel="noopener" style="color:var(--tx2);text-decoration:none">'+esc(domain)+'</a></div>'
        + '</div>'
        + '<button class="btn bs" onclick="rssRemoveFeed(\''+f.url.replace(/'/g,"\\'")+'\')" title="Supprimer" style="width:auto;padding:4px 8px;font-size:11px;color:var(--red)">🗑</button>'
        + '</div>';
    });
  });
  $('rss-feeds-list').innerHTML = html;
}

async function rssAddFeed(){
  var url = $('rss-add-url').value.trim();
  var tagsRaw = $('rss-add-tags').value.trim();
  if(!url){ toast('URL manquante'); return; }
  var tags = (tagsRaw.match(/#?[\w-]+/g) || []).map(function(t){return t.replace(/^#/,'').toLowerCase();});

  $('rss-add-btn').disabled = true;
  $('rss-add-btn').textContent = '⏳ Découverte…';
  var st = $('rss-add-status');
  st.style.display = 'block';
  st.textContent = '🔍 Recherche du flux RSS (auto-discovery + chemins courants)…';

  var r = await post('/api/rss/add', {url: url, tags: tags});

  $('rss-add-btn').disabled = false;
  $('rss-add-btn').textContent = '➕ Ajouter';

  if(r.error){
    st.style.color = 'var(--red)';
    st.textContent = '⚠ '+r.error;
    toast('⚠ '+r.error.substring(0,80), 4000);
    return;
  }

  st.style.color = 'var(--grn)';
  var msg = '✓ Ajouté : '+r.feed.name+' → '+r.feed.url;
  if(r.feed.original_url) msg += ' (depuis '+r.feed.original_url+')';
  st.textContent = msg;
  $('rss-add-url').value = '';
  $('rss-add-tags').value = '';
  rssReloadList();
  toast('✓ Flux ajouté');
}

async function rssRemoveFeed(url){
  if(!confirm('Retirer ce flux de la liste ?')) return;
  var r = await post('/api/rss/remove', {url: url});
  if(r.error){ toast('⚠ '+r.error); return; }
  rssReloadList();
  toast('✓ Flux retiré');
}

// ─── ONGLET 2 : Analyser ───────────────────────────────────────

function rssRenderAnalyzeList(){
  if(!RSS.feeds.length){
    $('rss-analyze-feeds-list').innerHTML = '<div style="padding:16px;text-align:center;color:var(--tx2);font-size:12px">Aucun flux enregistré — ajoutez-en dans l\'onglet « Mes flux ».</div>';
    return;
  }
  var html = '';
  RSS.feeds.forEach(function(f, idx){
    var safeUrl = esc(f.url);
    var tagsHtml = (f.tags||[]).map(function(t){return '<span class="rss-tag">#'+esc(t)+'</span>';}).join('');
    html += '<label style="display:flex;align-items:center;gap:8px;padding:5px 4px;cursor:pointer;font-size:12px;color:var(--tx);border-bottom:1px solid var(--bdr)">'
      + '<input type="checkbox" class="rss-feed-check" data-url="'+safeUrl+'" checked style="accent-color:var(--acc)">'
      + '<span style="flex:1">'+esc(f.name)+' '+tagsHtml+'</span>'
      + '<span style="font-family:var(--mono);font-size:10px;color:var(--tx2)">'+esc(f.url.length>50 ? f.url.slice(0,50)+'…' : f.url)+'</span>'
      + '</label>';
  });
  $('rss-analyze-feeds-list').innerHTML = html;
}

function rssSelectAll(checked){
  document.querySelectorAll('.rss-feed-check').forEach(function(cb){ cb.checked = checked; });
}

function rssGetSelectedFeeds(){
  return Array.from(document.querySelectorAll('.rss-feed-check'))
    .filter(function(cb){return cb.checked;})
    .map(function(cb){return cb.dataset.url;});
}

async function rssRunAnalysis(){
  var feedUrls = rssGetSelectedFeeds();
  if(!feedUrls.length){ toast('Cochez au moins un flux'); return; }

  var hours      = parseInt($('rss-hours').value);
  var maxItems   = parseInt($('rss-max-items').value);
  var fetchFull  = $('rss-fetch-full').checked;
  var modeEl     = document.querySelector('input[name="rss-mode"]:checked');
  var mode       = modeEl ? modeEl.value : 'together';
  var question   = $('rss-question').value.trim();
  var sysPrompt  = $('rss-sys-prompt').value.trim();

  RSS.mode = mode;
  RSS.question = question;
  RSS.sysPrompt = sysPrompt;
  RSS.startTime = Date.now();
  RSS.abortCtrl = new AbortController();

  // UI : passer en mode "en cours"
  $('rss-run-btn').textContent = '⏹ Annuler';
  $('rss-run-btn').onclick = function(){ rssCancelAnalysis(); };
  $('rss-progress').style.display = 'block';
  $('rss-result-block').style.display = 'none';
  $('rss-save-btn').style.display = 'none';

  // Démarrer le chronomètre (mise à jour toutes les 1 s)
  var currentStep = 'fetch';  // 'fetch' | 'analyze'
  RSS.timerId = setInterval(function(){
    var elapsed = rssFormatElapsed(Date.now() - RSS.startTime);
    if(currentStep === 'fetch'){
      $('rss-progress-step').textContent = '⏳ Étape 1/2 — Fetch des flux'
        + (fetchFull ? ' + lecture des articles' : '')
        + ' · ' + elapsed + ' écoulées';
    } else {
      $('rss-progress-step').textContent = '⏳ Étape 2/2 — Synthèse IA en cours · ' + elapsed + ' écoulées'
        + ' (l\'IA peut mettre 30 s à 3 min selon le modèle ; vous pouvez annuler ci-dessous)';
    }
  }, 1000);

  // ── ÉTAPE 1 : FETCH ──
  try {
    var fr = await rssFetchWithAbort('/api/rss/fetch', {
      feed_urls:  feedUrls,
      hours_back: hours,
      fetch_full: fetchFull,
      max_items:  maxItems,
    });

    if(fr.error){
      rssEndAnalysis();
      toast('⚠ Fetch : '+fr.error.substring(0,100), 5000);
      return;
    }
    RSS.fetchedFeeds = fr.feeds;

    if(fr.total_articles === 0){
      rssEndAnalysis();
      toast('⚠ Aucun article dans la fenêtre temporelle choisie', 5000);
      return;
    }

    toast('✓ Étape 1/2 OK — ' + fr.total_articles + ' articles trouvés' + (fr.fetched_full ? ' ('+fr.fetched_full+' avec contenu complet)' : ''), 3000);

    // ── ÉTAPE 2 : ANALYZE ──
    currentStep = 'analyze';
    RSS.startTime = Date.now();  // reset chrono pour l'étape 2

    var ar = await rssFetchWithAbort('/api/rss/analyze', {
      feeds: fr.feeds,
      question: question,
      system_prompt: sysPrompt,
      mode: mode,
    });

    if(ar.error){
      rssEndAnalysis();
      toast('⚠ Synthèse IA : '+ar.error.substring(0,100), 5500);
      return;
    }

    RSS.syntheses = ar;
    rssEndAnalysis();
    rssRenderResult();

  } catch(e){
    rssEndAnalysis();
    if(e.name === 'AbortError'){
      toast('⏹ Analyse annulée par l\'utilisateur', 3000);
    } else {
      toast('⚠ Erreur réseau : '+(e.message || e.name).substring(0,100), 5000);
      console.error('[RSS] Erreur:', e);
    }
  }
}

// Helper : fetch avec signal d'abort + parse JSON
async function rssFetchWithAbort(url, data){
  var resp = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data),
    signal: RSS.abortCtrl.signal,
  });
  // Même si HTTP non-OK, on essaie de lire le JSON (le serveur renvoie {"error":...})
  try { return await resp.json(); }
  catch(e){ return {error: 'Réponse serveur invalide (HTTP '+resp.status+')'}; }
}

function rssCancelAnalysis(){
  if(!RSS.abortCtrl) return;
  rssAbortRunning();
  rssEndAnalysis();
  toast('⏹ Annulation en cours…', 2000);
}

function rssEndAnalysis(){
  if(RSS.timerId){ clearInterval(RSS.timerId); RSS.timerId = null; }
  RSS.abortCtrl = null;
  $('rss-progress').style.display = 'none';
  $('rss-run-btn').textContent = '🚀 Analyser';
  $('rss-run-btn').onclick = function(){ rssRunAnalysis(); };
}

function rssRenderResult(){
  if(!RSS.syntheses) return;
  $('rss-result-block').style.display = 'block';
  var meta = '';
  if(RSS.syntheses.mode === 'together'){
    var s = RSS.syntheses.stats || {};
    meta = ' · '+s.feeds+' flux · '+s.articles+' articles · '+(s.with_full_content||0)+' avec contenu complet';
    if(RSS.question) meta += ' · question ciblée';
    $('rss-result-content').textContent = RSS.syntheses.synthesis || '(pas de synthèse)';
  } else {
    // per_feed : concaténer les synthèses
    var parts = (RSS.syntheses.syntheses || []).map(function(s){
      var head = '## 📡 ' + s.feed_name + '\n*' + s.feed_url + '* · ' + s.article_count + ' articles\n\n';
      if(s.error){
        return head + '⚠ Erreur : ' + s.error;
      }
      return head + (s.synthesis || '(pas de synthèse)');
    });
    meta = ' · '+(RSS.syntheses.syntheses||[]).length+' flux analysés séparément';
    if(RSS.question) meta += ' · question ciblée';
    $('rss-result-content').textContent = parts.join('\n\n---\n\n');
  }
  $('rss-result-meta').textContent = meta;
  $('rss-save-btn').style.display = 'inline-flex';
}

async function rssSaveAsFile(){
  if(!RSS.syntheses){ toast('Pas de synthèse à sauver'); return; }

  // Nom basé sur la question si fournie
  var slugBase = RSS.question
    ? RSS.question.substring(0,40).replace(/[^\w\sàâäéèêëïîôöùûüç-]/gi,'').replace(/\s+/g,'-').toLowerCase()
    : 'veille';
  var dateStr = new Date().toISOString().substring(0,10);
  var base = safeFileName('rss-analyse-' + slugBase + '-' + dateStr);

  var newPath = null;
  for(var i=1; i<30; i++){
    var tryName = i===1 ? base : base+'-'+i;
    var resp = await post('/api/files/new', {dir: CUR_DIR, name: tryName});
    if(resp.ok){ newPath = resp.path; break; }
    if(resp.error !== 'Fichier existant'){ toast('⚠ '+resp.error); return; }
  }
  if(!newPath){ toast('⚠ Création impossible'); return; }

  // Construire le contenu
  var head = '# 🗞 Veille RSS — ' + dateStr + '\n\n';
  if(RSS.question)  head += '*Question :* ' + RSS.question + '  \n';
  if(RSS.sysPrompt) head += '*Prompt système :* `' + RSS.sysPrompt.substring(0,200) + '`  \n';
  head += '*Mode :* ' + (RSS.mode === 'together' ? 'Tous flux ensemble' : 'Flux par flux') + '  \n';
  if(RSS.fetchedFeeds){
    var total = RSS.fetchedFeeds.reduce(function(a,f){return a+(f.items||[]).length;}, 0);
    var fetched = RSS.fetchedFeeds.reduce(function(a,f){return a+(f.items||[]).filter(function(it){return it.fetch_status==='ok';}).length;}, 0);
    head += '*Articles analysés :* ' + total;
    if(fetched > 0) head += ' (dont ' + fetched + ' avec contenu complet)';
    head += '  \n';
  }
  head += '\n---\n\n';

  // Corps : la synthèse
  var body = '';
  if(RSS.syntheses.mode === 'together'){
    body = RSS.syntheses.synthesis || '';
  } else {
    body = (RSS.syntheses.syntheses || []).map(function(s){
      var h = '## 📡 ' + s.feed_name + '\n*[' + s.feed_url + '](' + s.feed_url + ')* · ' + s.article_count + ' articles\n\n';
      if(s.error) return h + '⚠ Erreur : ' + s.error;
      return h + (s.synthesis || '');
    }).join('\n\n---\n\n');
  }

  // Liste brute des articles à la fin
  var rawList = '';
  if(RSS.fetchedFeeds && RSS.fetchedFeeds.length){
    rawList = '\n\n---\n\n## Liste brute des articles\n\n';
    var counter = 1;
    RSS.fetchedFeeds.forEach(function(f){
      if(!f.items || !f.items.length) return;
      rawList += '### ' + f.name + '\n\n';
      f.items.forEach(function(it){
        var dateS = it.date_str || '?';
        rawList += '['+(counter++)+'] **' + (it.title||'(sans titre)') + '** — *' + dateS + '*\n' + (it.link||'') + '\n\n';
      });
    });
  }

  var content = head + body + rawList;

  await post('/api/files/save', {path: newPath, content: content});
  openFileTab(newPath, content);
  loadDir(CUR_DIR);
  closeRSS();
  toast('✓ Analyse RSS sauvegardée');
}

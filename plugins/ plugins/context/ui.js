// ════════════════════════════════════════════════════════
//  Plugin Context Builder — sélection multi + interrogation IA
// ════════════════════════════════════════════════════════
var CTX = {files:[], selected:new Set(), answer:'', question:'', sysPrompt:'', filesUsed:[]};

async function openContextBuilder(){
  // Charger la liste de tous les .md du workspace
  try {
    var r = await fetch('/api/context/list?dir='+eu(CUR_DIR)).then(function(r){return r.json();});
    if(r.error){ toast('⚠ '+r.error); return; }
    CTX.files = r.files || [];
  } catch(e){ toast('⚠ Erreur chargement fichiers: '+e); return; }

  // Pré-sélectionner le fichier ouvert si présent
  CTX.selected = new Set(ACTIVE ? [ACTIVE] : []);
  CTX.answer = '';

  $('ctx-search').value = '';
  $('ctx-question').value = '';
  $('ctx-sys-prompt').value = '';
  $('ctx-response').style.display = 'none';
  $('ctx-ask-btn').disabled = false;
  $('ctx-ask-btn').textContent = '🚀 Envoyer à l\'IA';
  document.querySelector('input[name=ctx-mode][value=manual]').checked = true;
  $('ctx-depth-block').style.display = 'none';

  renderCtxFiles();
  ctxUpdateStats();

  $('mctx').classList.add('on');
}

function closeContextBuilder(){
  $('mctx').classList.remove('on');
}

function ctxModeChange(){
  var mode = document.querySelector('input[name=ctx-mode]:checked').value;
  $('ctx-depth-block').style.display = mode === 'auto' ? 'flex' : 'none';
}

function renderCtxFiles(){
  var filter = ($('ctx-search').value || '').toLowerCase().trim();
  var visible = CTX.files.filter(function(f){
    if(!filter) return true;
    return f.name.toLowerCase().indexOf(filter) >= 0
        || (f.rel||'').toLowerCase().indexOf(filter) >= 0;
  });

  if(!visible.length){
    $('ctx-files').innerHTML = '<div style="padding:14px;color:var(--tx2);font-size:11px;text-align:center">Aucun fichier — vérifiez le workspace</div>';
    return;
  }

  $('ctx-files').innerHTML = visible.map(function(f,i){
    var checked = CTX.selected.has(f.path) ? 'checked' : '';
    var selClass = CTX.selected.has(f.path) ? 'selected' : '';
    var safePath = f.path.replace(/\\/g,'\\\\').replace(/'/g,"\\'");
    var nameClean = f.name.replace(/\.md$/i,'');
    return '<label class="ctx-file '+selClass+'">'
      + '<input type="checkbox" '+checked+' onchange="ctxToggle(\''+safePath+'\',this.checked)">'
      + '<span class="name">'+esc(nameClean)+'</span>'
      + '<span class="path" title="'+esc(f.rel||'')+'">'+esc(f.rel||'')+'</span>'
      + '</label>';
  }).join('');
}

function ctxToggle(path, checked){
  if(checked) CTX.selected.add(path);
  else        CTX.selected.delete(path);
  // Mise à jour visuelle de la ligne uniquement
  var labels = $('ctx-files').querySelectorAll('.ctx-file');
  labels.forEach(function(l){
    var cb = l.querySelector('input');
    if(cb && cb.checked) l.classList.add('selected');
    else l.classList.remove('selected');
  });
  ctxUpdateStats();
}

function ctxSelectAll(state){
  var filter = ($('ctx-search').value || '').toLowerCase().trim();
  var visible = CTX.files.filter(function(f){
    if(!filter) return true;
    return f.name.toLowerCase().indexOf(filter) >= 0
        || (f.rel||'').toLowerCase().indexOf(filter) >= 0;
  });
  visible.forEach(function(f){
    if(state) CTX.selected.add(f.path);
    else      CTX.selected.delete(f.path);
  });
  renderCtxFiles();
  ctxUpdateStats();
}

function ctxSelectCurrent(){
  if(!ACTIVE){ toast('Aucun fichier ouvert'); return; }
  CTX.selected = new Set([ACTIVE]);
  renderCtxFiles();
  ctxUpdateStats();
}

function ctxUpdateStats(){
  var totalChars = 0;
  CTX.files.forEach(function(f){ if(CTX.selected.has(f.path)) totalChars += (f.size||0); });
  var n = CTX.selected.size;
  $('ctx-count').textContent = n + ' sélectionné' + (n > 1 ? 's' : '');
  $('ctx-est-files').textContent = n;
  $('ctx-est-chars').textContent = totalChars.toLocaleString();
  $('ctx-est-tokens').textContent = Math.ceil(totalChars / 4).toLocaleString();
}

async function ctxPreview(){
  if(!CTX.selected.size){ toast('Aucun fichier sélectionné'); return; }
  var mode  = document.querySelector('input[name=ctx-mode]:checked').value;
  var depth = mode === 'auto' ? parseInt($('ctx-depth').value) : 0;

  toast('Assemblage du corpus…', 2000);
  var r = await post('/api/context/build', {paths: Array.from(CTX.selected), depth: depth, dir: CUR_DIR});
  if(r.error){ toast('⚠ '+r.error); return; }

  // Aperçu dans nouvel onglet
  var w = window.open('', '_blank');
  if(!w){ toast('⚠ Le navigateur bloque les pop-ups'); return; }
  var info = '📊 ' + r.files_count + ' fichier(s) · ' + r.total_chars.toLocaleString() + ' chars · ≈ ' + Math.ceil(r.total_chars/4).toLocaleString() + ' tokens';
  if(r.auto_added > 0) info += ' · +' + r.auto_added + ' ajoutés via liens';
  w.document.write('<title>Aperçu corpus</title>'
    + '<style>body{font-family:ui-monospace,monospace;padding:20px;background:#0a0a0a;color:#ddd;line-height:1.55}'
    + 'header{position:sticky;top:0;background:#0a0a0a;border-bottom:1px solid #333;padding:8px 0;margin-bottom:12px;color:#60a5fa}</style>'
    + '<header>'+info+'</header><pre style="white-space:pre-wrap">'
    + esc(r.markdown||'')
    + '</pre>');
  w.document.close();
}

async function ctxAsk(){
  if(!CTX.selected.size){ toast('Aucun fichier sélectionné'); return; }
  var question = $('ctx-question').value.trim();
  if(!question){ toast('Posez d\'abord une question'); return; }

  var mode  = document.querySelector('input[name=ctx-mode]:checked').value;
  var depth = mode === 'auto' ? parseInt($('ctx-depth').value) : 0;
  var sysPrompt = $('ctx-sys-prompt').value.trim();

  $('ctx-ask-btn').disabled = true;
  $('ctx-ask-btn').textContent = '⏳ IA en cours (peut prendre 1-3 min)…';
  $('ctx-response').style.display = 'none';

  var r = await post('/api/context/ask', {
    paths:         Array.from(CTX.selected),
    depth:         depth,
    question:      question,
    system_prompt: sysPrompt,
    dir:           CUR_DIR
  });

  $('ctx-ask-btn').disabled = false;
  $('ctx-ask-btn').textContent = '🚀 Envoyer à l\'IA';

  if(r.error){ toast('⚠ '+r.error, 6000); return; }

  CTX.answer    = r.answer;
  CTX.question  = question;
  CTX.sysPrompt = sysPrompt;
  CTX.filesUsed = r.files_used || [];

  var meta = '📊 ' + (r.files_count||0) + ' fichier(s) analysé(s)';
  if(r.truncated) meta += ' · ⚠ corpus tronqué (trop volumineux)';
  $('ctx-resp-meta').textContent = meta;
  $('ctx-answer').textContent = r.answer;
  $('ctx-response').style.display = 'block';
  $('ctx-response').scrollIntoView({behavior:'smooth', block:'nearest'});
}

async function ctxSaveAsFile(){
  if(!CTX.answer){ toast('Pas de réponse à sauver'); return; }

  // Nom basé sur la question (30 premiers chars cleanups)
  var qSlug = CTX.question.substring(0,40).replace(/[^\w\sàâäéèêëïîôöùûüç-]/gi,'').replace(/\s+/g,'-').toLowerCase();
  var base = safeFileName('contexte-' + (qSlug || 'sans-titre'));

  var newPath = null;
  for(var i=1; i<30; i++){
    var tryName = i===1 ? base : base+'-'+i;
    var resp = await post('/api/files/new', {dir: CUR_DIR, name: tryName});
    if(resp.ok){ newPath = resp.path; break; }
    if(resp.error !== 'Fichier existant'){ toast('⚠ '+resp.error); return; }
  }
  if(!newPath){ toast('⚠ Création impossible'); return; }

  var head = '# 🗂 Contexte multi-fichiers\n\n';
  head += '*Question :* ' + CTX.question + '\n\n';
  if(CTX.sysPrompt) head += '*Prompt système :* `' + CTX.sysPrompt.substring(0,200) + '`\n\n';
  head += '*Fichiers du corpus (' + CTX.filesUsed.length + ') :*\n';
  head += CTX.filesUsed.map(function(rel){
    var stem = rel.split(/[/\\]/).pop().replace(/\.md$/i,'');
    return '- [[' + stem + ']]';
  }).join('\n');
  head += '\n\n---\n\n## ✨ Réponse de l\'IA\n\n';

  var content = head + CTX.answer + '\n';
  await post('/api/files/save', {path: newPath, content: content});
  openFileTab(newPath, content);
  loadDir(CUR_DIR);
  closeContextBuilder();
  toast('✓ Sauvegardé');
}

function ctxResetResponse(){
  CTX.answer = '';
  $('ctx-response').style.display = 'none';
  $('ctx-question').value = '';
  $('ctx-question').focus();
}

// Handlers Escape + clic-extérieur, autonomes au plugin
document.addEventListener('click', function(e){
  if(e.target === document.getElementById('mctx')) closeContextBuilder();
});
document.addEventListener('keydown', function(e){
  if(e.key === 'Escape' && document.getElementById('mctx').classList.contains('on')) closeContextBuilder();
});

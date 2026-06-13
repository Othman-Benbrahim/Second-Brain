// ════════════════════════════════════════════════════════
//  Plugin Prompts Manager
//  - CRUD de presets persistants
//  - Auto-injection de sélecteurs dans les modals des autres plugins
//    (cherche les éléments avec data-sys-prompt-target)
// ════════════════════════════════════════════════════════
var PROMPTS = {list:[], current:null, loaded:false};

// ── Manager modal ───────────────────────────────────────

async function openPromptsManager(){
  await loadPrompts();
  PROMPTS.current = null;
  $('prompts-empty').style.display = 'flex';
  $('prompts-edit').style.display  = 'none';
  renderPromptsList();
  $('mprompts').classList.add('on');
}

function closePromptsManager(){
  $('mprompts').classList.remove('on');
}

async function loadPrompts(){
  try {
    var r = await fetch('/api/prompts/list').then(function(r){return r.json();});
    PROMPTS.list   = r.prompts || [];
    PROMPTS.loaded = true;
  } catch(e){
    PROMPTS.list   = [];
    PROMPTS.loaded = true;
    toast('⚠ Erreur chargement presets : '+e);
  }
}

function renderPromptsList(){
  if(!PROMPTS.list.length){
    $('prompts-list').innerHTML = '<div style="padding:14px;color:var(--tx2);font-size:11px;text-align:center">Aucun preset</div>';
    return;
  }
  $('prompts-list').innerHTML = PROMPTS.list.map(function(p){
    var active = (PROMPTS.current && PROMPTS.current.id === p.id) ? 'active' : '';
    var preview = (p.content||'').substring(0,55).replace(/\n/g,' ');
    var safeId = p.id.replace(/'/g, "\\'");
    return '<div class="prompts-item '+active+'" onclick="editPrompt(\''+safeId+'\')">'
      + '<div class="pname">'+esc(p.name)+'</div>'
      + '<div class="pprev">'+esc(preview)+'…</div>'
      + '</div>';
  }).join('');
}

function newPrompt(){
  PROMPTS.current = null;
  $('prompts-empty').style.display = 'none';
  $('prompts-edit').style.display  = 'flex';
  $('prompt-name').value    = '';
  $('prompt-content').value = '';
  $('prompt-del-btn').style.display = 'none';
  renderPromptsList();
  setTimeout(function(){ $('prompt-name').focus(); }, 40);
}

function editPrompt(id){
  var p = PROMPTS.list.find(function(p){return p.id === id;});
  if(!p) return;
  PROMPTS.current = p;
  $('prompts-empty').style.display = 'none';
  $('prompts-edit').style.display  = 'flex';
  $('prompt-name').value    = p.name;
  $('prompt-content').value = p.content;
  $('prompt-del-btn').style.display = 'inline-flex';
  renderPromptsList();
}

function cancelEditPrompt(){
  PROMPTS.current = null;
  $('prompts-empty').style.display = 'flex';
  $('prompts-edit').style.display  = 'none';
  renderPromptsList();
}

async function savePrompt(){
  var name    = $('prompt-name').value.trim();
  var content = $('prompt-content').value.trim();
  if(!name)    { toast('Nom requis');     return; }
  if(!content) { toast('Contenu requis'); return; }

  var payload = {name: name, content: content};
  if(PROMPTS.current) payload.id = PROMPTS.current.id;

  var r = await post('/api/prompts/save', payload);
  if(r.error){ toast('⚠ '+r.error); return; }

  await loadPrompts();
  PROMPTS.current = PROMPTS.list.find(function(p){return p.id === r.id;});
  renderPromptsList();
  toast('✓ '+(r.created ? 'Preset créé' : 'Preset sauvegardé'));
}

async function deletePromptUI(){
  if(!PROMPTS.current) return;
  if(!confirm('Supprimer le preset "'+PROMPTS.current.name+'" ?\n(action irréversible)')) return;
  var r = await post('/api/prompts/delete', {id: PROMPTS.current.id});
  if(r.error){ toast('⚠ '+r.error); return; }
  cancelEditPrompt();
  await loadPrompts();
  renderPromptsList();
  toast('✓ Supprimé');
}

async function resetPromptsToDefault(){
  if(!confirm('Restaurer les presets d\'usine ?\nVos personnalisations seront écrasées.')) return;
  var r = await post('/api/prompts/reset', {});
  if(r.error){ toast('⚠ '+r.error); return; }
  await loadPrompts();
  cancelEditPrompt();
  renderPromptsList();
  toast('✓ Presets restaurés');
}

// ════════════════════════════════════════════════════════
//  Auto-injection — sélecteurs à côté des sys-prompt-target
// ════════════════════════════════════════════════════════

function injectPromptSelectors(){
  document.querySelectorAll('[data-sys-prompt-target]:not([data-sp-injected])').forEach(function(target){
    target.setAttribute('data-sp-injected', '1');
    var container = document.createElement('div');
    container.className = 'sp-injected';
    var safeId = (target.id || '').replace(/'/g, "\\'");
    container.innerHTML = '<button type="button" class="sp-btn" onclick="showPromptMenu(event,\''+safeId+'\')">📝 Choisir un preset…</button>'
      + '<span class="sp-current" data-for="'+target.id+'"></span>';
    target.parentNode.insertBefore(container, target.nextSibling);
  });
}

async function showPromptMenu(e, targetId){
  e.stopPropagation();
  e.preventDefault();
  // Fermer tout menu existant
  document.querySelectorAll('.sp-menu').forEach(function(m){m.remove();});

  if(!PROMPTS.loaded) await loadPrompts();

  var menu = document.createElement('div');
  menu.className = 'sp-menu';

  if(!PROMPTS.list.length){
    menu.innerHTML = '<div class="sp-menu-empty">Aucun preset défini.<br>Ouvrez 📝 Prompts dans la barre pour en créer.</div>';
  } else {
    var safeTarget = targetId.replace(/'/g, "\\'");
    menu.innerHTML = PROMPTS.list.map(function(p){
      var preview = (p.content||'').substring(0,90).replace(/\n/g,' ');
      var safeId  = p.id.replace(/'/g, "\\'");
      return '<div class="sp-opt" onclick="applyPromptToTarget(\''+safeId+'\',\''+safeTarget+'\')">'
        + '<div>'+esc(p.name)+'</div>'
        + '<div class="pprev">'+esc(preview)+'…</div>'
        + '</div>';
    }).join('');
  }

  // Position : juste en-dessous du bouton (fixed, viewport-relative)
  var rect = e.target.getBoundingClientRect();
  menu.style.left = Math.min(rect.left, window.innerWidth - 400) + 'px';
  menu.style.top  = (rect.bottom + 4) + 'px';
  document.body.appendChild(menu);

  // Fermer sur clic ailleurs (avec délai pour éviter le clic d'ouverture)
  setTimeout(function(){
    document.addEventListener('click', closeSpMenuOnce, {once: true});
    document.addEventListener('keydown', closeSpMenuOnEsc, {once: true});
  }, 50);
}

function closeSpMenuOnce(){
  document.querySelectorAll('.sp-menu').forEach(function(m){m.remove();});
}
function closeSpMenuOnEsc(e){
  if(e.key === 'Escape') closeSpMenuOnce();
  else {
    // Réécouter pour la prochaine touche
    document.addEventListener('keydown', closeSpMenuOnEsc, {once: true});
  }
}

function applyPromptToTarget(promptId, targetId){
  var prompt = PROMPTS.list.find(function(p){return p.id === promptId;});
  var target = document.getElementById(targetId);
  if(!prompt || !target){ closeSpMenuOnce(); return; }
  target.value = prompt.content;
  target.dispatchEvent(new Event('input', {bubbles: true}));
  // Mettre à jour l'indicateur visuel
  var indicator = document.querySelector('.sp-current[data-for="'+targetId+'"]');
  if(indicator) indicator.textContent = '✓ '+prompt.name;
  closeSpMenuOnce();
  toast('✓ Preset appliqué : '+prompt.name);
}

// Effacer l'indicateur quand l'utilisateur modifie manuellement la textarea
document.addEventListener('input', function(e){
  if(e.target && e.target.hasAttribute && e.target.hasAttribute('data-sys-prompt-target')){
    var ind = document.querySelector('.sp-current[data-for="'+e.target.id+'"]');
    if(ind && !e.isTrusted === false){
      // Si la valeur ne correspond plus à un preset connu, effacer l'indicateur
      var matching = PROMPTS.list.find(function(p){return p.content === e.target.value;});
      ind.textContent = matching ? '✓ '+matching.name : (e.target.value ? '(personnalisé)' : '');
    }
  }
});

// Au chargement de la page : précharger les presets + injecter les sélecteurs
window.addEventListener('load', function(){
  loadPrompts().then(function(){
    injectPromptSelectors();
  });
});

// Handlers du modal manager
document.addEventListener('click', function(e){
  if(e.target === document.getElementById('mprompts')) closePromptsManager();
});
document.addEventListener('keydown', function(e){
  if(e.key === 'Escape' && document.getElementById('mprompts') && document.getElementById('mprompts').classList.contains('on')) closePromptsManager();
});

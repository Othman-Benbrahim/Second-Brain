// ════════════════════════════════════════════════════════
//  Plugin OSINT Cross-Reference — frontend compatible
// ════════════════════════════════════════════════════════
var OSCX = { type: 'auto', query: '', data: null, searching: false };

function oscxEsc(s){
  return String(s == null ? '' : s).replace(/[&<>'"]/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c];
  });
}

function openOsintCx(){
  OSCX.searching = false;
  $('oscx-error').style.display = 'none';
  $('oscx-progress').style.display = 'none';
  $('oscx-results').style.display = 'none';
  $('oscx-form').style.display = 'block';
  $('moscx').classList.add('on');
  setTimeout(function(){ if($('oscx-query')) $('oscx-query').focus(); }, 80);
}

function closeOsintCx(){
  $('moscx').classList.remove('on');
}


function toggleBrixHubBox(){
  var cb = $('oscx-brixhub');
  var box = $('oscx-brixhub-box');
  if(box) box.style.display = cb && cb.checked ? 'block' : 'none';
}

function bxVal(id){
  var el = $(id);
  return el ? el.value.trim() : '';
}

function collectBrixHubPayload(){
  var payload = {};
  var raw = bxVal('oscx-bx-json');
  if(raw){
    try{
      payload = JSON.parse(raw);
    }catch(e){
      throw new Error('JSON BrixHub invalide : ' + e.message);
    }
  }
  var map = [
    ['oscx-bx-nom', 'nom_famille'],
    ['oscx-bx-prenom', 'prenom'],
    ['oscx-bx-ville', 'ville'],
    ['oscx-bx-email', 'email'],
    ['oscx-bx-tel', 'telephone'],
    ['oscx-bx-user', 'nom_utilisateur']
  ];
  for(var i=0;i<map.length;i++){
    var v = bxVal(map[i][0]);
    if(v) payload[map[i][1]] = v;
  }
  if($('oscx-bx-flex') && $('oscx-bx-flex').checked) payload.flexible = true;
  return payload;
}

function hasBrixPayload(payload){
  return payload && Object.keys(payload).length > 0;
}

async function fetchBrixHubPayload(payload){
  var resp = await fetch('/api/osintcx/brixhub', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  return await resp.json();
}

function setOsintCxType(type){
  OSCX.type = type || 'auto';
  var btns = document.querySelectorAll('.oscx-type-btn');
  for(var i=0;i<btns.length;i++){
    btns[i].classList.toggle('on', btns[i].dataset.type === OSCX.type);
  }
}

async function runOsintCxSearch(){
  if(OSCX.searching) return;
  var q = $('oscx-query').value.trim();
  var withBrix = $('oscx-brixhub') && $('oscx-brixhub').checked;
  var brixPayload = {};
  try{
    if(withBrix) brixPayload = collectBrixHubPayload();
  }catch(e){
    showOsintCxError(e.message);
    return;
  }
  if(!q && !(withBrix && hasBrixPayload(brixPayload))){ toast('Requête vide'); return; }
  OSCX.searching = true;
  OSCX.query = q || 'BrixHub';
  OSCX.data = null;

  $('oscx-form').style.display = 'none';
  $('oscx-error').style.display = 'none';
  $('oscx-results').style.display = 'none';
  $('oscx-progress').style.display = 'block';

  try{
    var r = null;
    if(q){
      var url = '/api/osintcx/crossref?q=' + encodeURIComponent(q) + '&type=' + encodeURIComponent(OSCX.type) + (withBrix && !hasBrixPayload(brixPayload) ? '&brixhub=1' : '');
      r = await fetch(url).then(function(resp){ return resp.json(); });
      if(r.error || r.ok === false){ $('oscx-progress').style.display = 'none'; showOsintCxError(r.error || 'Recherche impossible'); return; }
    }else{
      r = {ok:true, query:'BrixHub', type:'brixhub', results:{}};
    }
    if(withBrix && hasBrixPayload(brixPayload)){
      r.brixhub = await fetchBrixHubPayload(brixPayload);
    }
    $('oscx-progress').style.display = 'none';
    OSCX.data = r;
    renderOsintCxResults(r);
  }catch(e){
    $('oscx-progress').style.display = 'none';
    showOsintCxError('Erreur réseau : ' + e.message);
  }finally{
    OSCX.searching = false;
  }
}

function showOsintCxError(msg){
  $('oscx-error-msg').textContent = msg;
  $('oscx-error').style.display = 'block';
  $('oscx-form').style.display = 'block';
  $('oscx-results').style.display = 'none';
}

function renderOsintCxResults(data){
  $('oscx-results').style.display = 'block';
  var res = data.results || {};
  $('oscx-summary').innerHTML = '<strong>Requête :</strong> ' + oscxEsc(data.query || OSCX.query)
    + ' · <strong>Type :</strong> ' + oscxEsc(data.type || 'auto')
    + (res.count !== undefined ? ' · <strong>Résultats :</strong> ' + oscxEsc(res.count) : '');

  if(data.type === 'username') renderOsintUsername(res);
  else if(data.type === 'email') renderOsintEmail(res);
  else if(data.type === 'phone') renderOsintPhone(res);
  else if(data.type === 'ip') renderOsintIp(res);
  else if(data.type === 'domain') renderOsintDomain(res);
  else renderOsintGeneric(res);

  if(data.brixhub){
    var container = document.createElement('div');
    container.innerHTML = renderBrixHubBlock(data.brixhub);
    $('oscx-list').appendChild(container.firstChild);
  }
}


function renderOsintUsername(res){
  var found = res.results || [];
  if(!found.length){
    $('oscx-list').innerHTML = '<div class="oscx-empty">👤 Aucun profil détecté. Cela ne prouve pas que le pseudo est absent : certains sites bloquent les requêtes automatiques.</div>';
    return;
  }
  $('oscx-list').innerHTML = '<div class="oscx-section">' + found.map(function(p){
    return '<div class="oscx-card">'
      + '<div class="oscx-card-title"><span>' + oscxEsc(p.icon || '🔗') + '</span>' + oscxEsc(p.platform || '') + '</div>'
      + '<div class="oscx-url"><a href="' + oscxEsc(p.url) + '" target="_blank" rel="noopener">' + oscxEsc(p.url) + '</a></div>'
      + '</div>';
  }).join('') + '</div>' + (res.note ? '<div class="oscx-note">' + oscxEsc(res.note) + '</div>' : '');
}

function renderOsintEmail(res){
  var html = '<div class="oscx-section">';
  html += '<div class="oscx-card"><div class="oscx-card-title">📧 Format</div>'
    + '<div class="oscx-grid"><div class="oscx-k">Email</div><div class="oscx-v">' + oscxEsc(res.query) + '</div>'
    + '<div class="oscx-k">Format valide</div><div class="oscx-v">' + (res.valid_format ? 'oui' : 'non') + '</div></div></div>';
  if(res.gravatar){
    html += '<div class="oscx-card"><div class="oscx-card-title">👤 Gravatar public</div>'
      + '<div style="display:flex;gap:12px;align-items:flex-start"><img class="oscx-avatar" src="' + oscxEsc(res.gravatar.avatar_url) + '" alt="Gravatar">'
      + '<div><div class="oscx-url"><a href="' + oscxEsc(res.gravatar.profile_url) + '" target="_blank" rel="noopener">' + oscxEsc(res.gravatar.profile_url) + '</a></div></div></div></div>';
  }
  if(res.domain) html += domainCardHtml(res.domain, '🌐 Domaine email');
  if(res.notes && res.notes.length) html += '<div class="oscx-note">' + res.notes.map(oscxEsc).join('<br>') + '</div>';
  $('oscx-list').innerHTML = html + '</div>';
}

function renderOsintPhone(res){
  var countries = (res.country_codes || []).map(function(c){ return oscxEsc(c.code + ' — ' + c.country); }).join('<br>') || 'non reconnu';
  $('oscx-list').innerHTML = '<div class="oscx-section"><div class="oscx-card"><div class="oscx-card-title">📞 Téléphone</div>'
    + '<div class="oscx-grid"><div class="oscx-k">Entrée</div><div class="oscx-v">' + oscxEsc(res.query) + '</div>'
    + '<div class="oscx-k">Nettoyé</div><div class="oscx-v">' + oscxEsc(res.cleaned) + '</div>'
    + '<div class="oscx-k">Format valide</div><div class="oscx-v">' + (res.valid_format ? 'oui' : 'non') + '</div>'
    + '<div class="oscx-k">Indicatif</div><div class="oscx-v">' + countries + '</div></div></div>'
    + (res.notes && res.notes.length ? '<div class="oscx-note">' + res.notes.map(oscxEsc).join('<br>') + '</div>' : '') + '</div>';
}

function renderOsintIp(res){
  var geo = res.geo || {};
  var html = '<div class="oscx-section"><div class="oscx-card"><div class="oscx-card-title">🌐 Adresse IP</div>'
    + '<div class="oscx-grid"><div class="oscx-k">IP</div><div class="oscx-v">' + oscxEsc(res.query) + '</div>'
    + '<div class="oscx-k">Version</div><div class="oscx-v">IPv' + oscxEsc(res.version) + '</div>'
    + '<div class="oscx-k">Privée</div><div class="oscx-v">' + (res.is_private ? 'oui' : 'non') + '</div>'
    + '<div class="oscx-k">Globale</div><div class="oscx-v">' + (res.is_global ? 'oui' : 'non') + '</div></div></div>';
  if(res.geo){
    html += '<div class="oscx-card"><div class="oscx-card-title">📍 Géolocalisation approximative</div><div class="oscx-grid">'
      + '<div class="oscx-k">Pays</div><div class="oscx-v">' + oscxEsc(geo.country) + '</div>'
      + '<div class="oscx-k">Ville</div><div class="oscx-v">' + oscxEsc(geo.city) + '</div>'
      + '<div class="oscx-k">ASN</div><div class="oscx-v">' + oscxEsc(geo.asn) + '</div>'
      + '<div class="oscx-k">Organisation</div><div class="oscx-v">' + oscxEsc(geo.org) + '</div>'
      + '<div class="oscx-k">Timezone</div><div class="oscx-v">' + oscxEsc(geo.timezone) + '</div></div></div>';
  }
  if(res.notes && res.notes.length) html += '<div class="oscx-note">' + res.notes.map(oscxEsc).join('<br>') + '</div>';
  $('oscx-list').innerHTML = html + '</div>';
}

function renderOsintDomain(res){ $('oscx-list').innerHTML = '<div class="oscx-section">' + domainCardHtml(res, '🏛 Domaine') + '</div>'; }

function domainCardHtml(d, title){
  var ips = (d.ips || []).join('<br>') || 'non résolu';
  var html = '<div class="oscx-card"><div class="oscx-card-title">' + oscxEsc(title || 'Domaine') + '</div>'
    + '<div class="oscx-grid"><div class="oscx-k">Domaine</div><div class="oscx-v">' + oscxEsc(d.query) + '</div>'
    + '<div class="oscx-k">IP(s)</div><div class="oscx-v">' + ips + '</div></div>';
  if(d.rdap){
    html += '<div class="oscx-note"><strong>RDAP</strong><br>Handle : ' + oscxEsc(d.rdap.handle || '')
      + '<br>Status : ' + oscxEsc((d.rdap.status || []).join ? d.rdap.status.join(', ') : d.rdap.status || '') + '</div>';
  }
  if(d.notes && d.notes.length) html += '<div class="oscx-note">' + d.notes.map(oscxEsc).join('<br>') + '</div>';
  return html + '</div>';
}

function renderOsintGeneric(res){
  $('oscx-list').innerHTML = '<pre class="oscx-card" style="white-space:pre-wrap;font-family:var(--mono);max-height:360px;overflow:auto">' + oscxEsc(JSON.stringify(res, null, 2)) + '</pre>';
}



function brixFieldLabel(key){
  var labels = {
    nom_famille:'Nom', prenom:'Prénom', nom_naissance:'Nom de naissance', nom_affichage:'Nom affiché', nom_utilisateur:'Nom utilisateur',
    date_naissance:'Date de naissance', annee_naissance:'Année de naissance', genre:'Genre', civilite:'Civilité',
    email:'Email', telephone:'Téléphone', mobile:'Mobile', adresse_ip:'Adresse IP',
    adresse:'Adresse', complement_adresse:'Complément', code_postal:'Code postal', ville:'Ville', ville_naissance:'Ville de naissance', lieu_naissance:'Lieu de naissance', pays:'Pays', region:'Région', departement:'Département',
    nir:'NIR', iban:'IBAN', bic:'BIC', siret:'SIRET', siren:'SIREN',
    vin_plaque:'VIN / Plaque', immatriculation:'Immatriculation', numero_serie:'Numéro de série', marque:'Marque', modele:'Modèle',
    societe:'Société', profession:'Profession', fonction:'Fonction',
    steam_id:'Steam ID', fivem_license:'FiveM license', fivem_license2:'FiveM license 2', fivem_id:'FiveM ID', xbox_live_id:'Xbox Live ID', live_id:'Live ID', discord_id:'Discord ID',
    _confidence:'Confiance', _sources:'Sources'
  };
  return labels[key] || key.replace(/_/g, ' ');
}

function brixFormatValue(key, value){
  if(value == null || value === '') return '';
  if(key === '_sources' && Array.isArray(value)){
    return '<div class="oscx-pills">' + value.map(function(src){ return '<span class="oscx-pill">' + oscxEsc(src) + '</span>'; }).join('') + '</div>';
  }
  if(key === '_confidence'){
    var n = Number(value);
    var cls = n >= 80 ? 'good' : (n >= 50 ? 'mid' : 'low');
    return '<span class="oscx-confidence ' + cls + '">' + oscxEsc(value) + '/100</span>';
  }
  if(Array.isArray(value)) return value.map(oscxEsc).join('<br>');
  if(typeof value === 'object') return '<pre class="oscx-mini-json">' + oscxEsc(JSON.stringify(value, null, 2)) + '</pre>';
  return oscxEsc(value);
}

function brixProfileTitle(profile, index){
  var parts = [];
  if(profile.prenom) parts.push(profile.prenom);
  if(profile.nom_famille) parts.push(profile.nom_famille);
  if(profile.nom_affichage && !parts.length) parts.push(profile.nom_affichage);
  if(profile.nom_utilisateur && !parts.length) parts.push('@' + profile.nom_utilisateur);
  return parts.length ? parts.join(' ') : 'Profil #' + (index + 1);
}

function renderBrixProfileCard(profile, index){
  var priority = [
    'email','telephone','mobile','adresse_ip',
    'adresse','complement_adresse','code_postal','ville','departement','region','pays',
    'date_naissance','annee_naissance','ville_naissance','lieu_naissance','genre','civilite',
    'nom_utilisateur','societe','profession','fonction',
    'siret','siren','iban','bic','nir',
    'immatriculation','vin_plaque','numero_serie','marque','modele',
    'steam_id','fivem_license','fivem_license2','fivem_id','xbox_live_id','live_id','discord_id',
    '_sources','_confidence'
  ];
  var used = {};
  var rows = [];
  priority.forEach(function(k){
    if(profile[k] != null && profile[k] !== ''){
      used[k] = true;
      rows.push([k, profile[k]]);
    }
  });
  Object.keys(profile).sort().forEach(function(k){
    if(!used[k] && k !== 'nom_famille' && k !== 'prenom' && k !== 'nom_affichage') rows.push([k, profile[k]]);
  });
  var html = '<div class="oscx-brix-profile">'
    + '<div class="oscx-brix-profile-head">'
    + '<div class="oscx-brix-profile-title">' + oscxEsc(brixProfileTitle(profile, index)) + '</div>';
  if(profile._confidence != null) html += '<div>' + brixFormatValue('_confidence', profile._confidence) + '</div>';
  html += '</div>';
  if(rows.length){
    html += '<div class="oscx-grid oscx-brix-grid">';
    rows.forEach(function(row){
      if(row[0] === '_confidence') return;
      html += '<div class="oscx-k">' + oscxEsc(brixFieldLabel(row[0])) + '</div><div class="oscx-v">' + brixFormatValue(row[0], row[1]) + '</div>';
    });
    html += '</div>';
  }
  return html + '</div>';
}

function renderBrixHubBlock(bx){
  var ok = bx && bx.ok;
  var title = ok ? '🧩 BrixHub API' : '🧩 BrixHub API — non disponible';
  var html = '<div class="oscx-card"><div class="oscx-card-title">' + title + '</div>';
  if(!bx){ return html + '<div class="oscx-note">Aucune réponse BrixHub.</div></div>'; }
  if(!ok){
    html += '<div class="oscx-note">' + oscxEsc(bx.error || 'Erreur BrixHub') + '</div>';
    if(bx.response_preview){ html += '<div class="oscx-brix-json">' + oscxEsc(bx.response_preview) + '</div>'; }
    if(bx.attempts){ html += '<div class="oscx-note"><strong>Tentatives</strong></div><div class="oscx-brix-json">' + oscxEsc(JSON.stringify(bx.attempts, null, 2)) + '</div>'; }
    return html + '</div>';
  }

  var payload = bx.results || {};
  var data = payload.data || {};
  var profiles = Array.isArray(data.results) ? data.results : (Array.isArray(payload.results) ? payload.results : []);
  var meta = payload.meta || bx.meta || {};

  html += '<div class="oscx-grid">'
    + '<div class="oscx-k">Endpoint</div><div class="oscx-v">' + oscxEsc(bx.endpoint || '') + '</div>'
    + '<div class="oscx-k">Méthode</div><div class="oscx-v">' + oscxEsc(bx.method || 'POST') + '</div>'
    + '<div class="oscx-k">Critères envoyés</div><div class="oscx-v"><code>' + oscxEsc(Object.keys(bx.criteria_sent || {}).join(', ') || '—') + '</code></div>'
    + '</div>';

  if(meta && Object.keys(meta).length){
    html += '<div class="oscx-brix-meta">'
      + '<span>Total : <strong>' + oscxEsc(meta.total != null ? meta.total : profiles.length) + '</strong></span>'
      + '<span>Page : <strong>' + oscxEsc(meta.page || 1) + '</strong></span>'
      + '<span>Par page : <strong>' + oscxEsc(meta.per_page || profiles.length || 0) + '</strong></span>'
      + (meta.pages != null ? '<span>Pages : <strong>' + oscxEsc(meta.pages) + '</strong></span>' : '')
      + (meta.took_ms != null ? '<span>Temps : <strong>' + oscxEsc(meta.took_ms) + ' ms</strong></span>' : '')
      + (meta.total_is_capped ? '<span class="oscx-warn">Total plafonné</span>' : '')
      + '</div>';
  }

  if(!profiles.length){
    html += '<div class="oscx-empty">Aucun profil BrixHub trouvé pour ces critères.</div>';
  }else{
    html += '<div class="oscx-brix-results">' + profiles.map(renderBrixProfileCard).join('') + '</div>';
  }

  if(bx.notice){ html += '<div class="oscx-note">' + oscxEsc(bx.notice) + '</div>'; }
  return html + '</div>';
}

async function saveOsintCxAsFile(){
  if(!OSCX.data){ toast('Pas de résultat à sauver'); return; }
  if(typeof safeFileName !== 'function' || typeof post !== 'function' || typeof openFileTab !== 'function'){
    toast('⚠ API fichier Second Brain indisponible'); return;
  }
  var slug = safeFileName('osint-' + OSCX.query.substring(0, 50));
  var newPath = null;
  for(var i=1;i<30;i++){
    var name = i === 1 ? slug : slug + '-' + i;
    var resp = await post('/api/files/new', {dir: CUR_DIR, name: name});
    if(resp.ok){ newPath = resp.path; break; }
    if(resp.error !== 'Fichier existant'){ toast('⚠ ' + resp.error); return; }
  }
  if(!newPath){ toast('⚠ Création impossible'); return; }
  var md = '# 🔍 OSINT — ' + OSCX.query + '\n\n'
    + '*Type :* `' + (OSCX.data.type || OSCX.type) + '`  \n'
    + '*Date :* ' + new Date().toISOString() + '\n\n'
    + '```json\n' + JSON.stringify(OSCX.data, null, 2) + '\n```\n';
  await post('/api/files/save', {path: newPath, content: md});
  openFileTab(newPath, md);
  loadDir(CUR_DIR);
  closeOsintCx();
  toast('✓ Rapport OSINT sauvegardé');
}

document.addEventListener('click', function(e){
  if(e.target === document.getElementById('moscx')) closeOsintCx();
});
document.addEventListener('keydown', function(e){
  if(e.key === 'Escape' && document.getElementById('moscx') && document.getElementById('moscx').classList.contains('on')) closeOsintCx();
});

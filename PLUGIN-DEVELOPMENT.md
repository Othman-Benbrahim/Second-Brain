# 🔌 Créer un plugin Second Brain

> Guide complet pour développer un plugin Second Brain. Si vous savez écrire du Python et un peu de JavaScript, vous écrirez votre premier plugin en 20 minutes.

---

## 📖 Table des matières

1. [Concept](#-concept)
2. [Anatomie d'un plugin](#-anatomie-dun-plugin)
3. [Le manifest.json](#-le-manifestjson)
4. [Le backend (`__init__.py`)](#-le-backend-__init__py)
5. [Le frontend (`ui.html`, `ui.js`, `ui.css`)](#-le-frontend)
6. [Helpers disponibles côté backend](#-helpers-disponibles)
7. [Conventions UI à respecter](#-conventions-ui)
8. [Intégration cross-plugin](#-intégration-cross-plugin)
9. [🎓 Tutoriel : créer un plugin "Hello World"](#-tutoriel--plugin-hello-world)
10. [Patterns courants](#-patterns-courants)
11. [Pièges à éviter](#-pièges-à-éviter)

---

## 🎯 Concept

Un plugin Second Brain est un **dossier dans `plugins/`** qui contient au minimum un `manifest.json`. Au démarrage, `second_brain.py` parcourt tous les sous-dossiers, charge ce qu'il trouve, et :

- Si le plugin a un `__init__.py`, il appelle `register(app, rd_cfg)` qui peut **ajouter des routes Flask**
- Si le plugin a un `ui.html` / `ui.css` / `ui.js`, leur contenu est **injecté** dans la page principale au bon endroit
- Si le manifest déclare un bouton, celui-ci apparaît dans la **toolbar** de l'éditeur

Aucun build, aucun bundler, aucun framework. Juste des fichiers texte.

---

## 🧱 Anatomie d'un plugin

```
plugins/mon-plugin/
├── manifest.json    # Obligatoire — métadonnées + boutons UI
├── __init__.py      # Optionnel — code Python (routes Flask)
├── ui.html          # Optionnel — HTML injecté (modals, panneaux)
├── ui.css           # Optionnel — CSS injecté
└── ui.js            # Optionnel — JS injecté
```

**Seul `manifest.json` est strictement obligatoire.** Un plugin peut être :
- 100 % côté serveur (juste `__init__.py`) — par exemple un endpoint d'API exposé pour scripts externes
- 100 % côté client (juste `ui.html` + `ui.js`) — par exemple un widget purement local sans IA
- Les deux (cas le plus courant : modal qui appelle des routes)

---

## 📋 Le manifest.json

```json
{
  "name": "Mon Plugin",
  "version": "1.0",
  "description": "Ce que fait le plugin en une phrase.",
  "author": "Votre nom",
  "buttons": [
    {
      "panel": "toolbar",
      "label": "🦄 Mon plugin",
      "title": "Tooltip affiché au survol",
      "onclick": "openMonPlugin()"
    }
  ]
}
```

| Champ | Rôle |
|---|---|
| `name` | Nom affiché dans les logs au démarrage |
| `version` | Pour votre propre suivi |
| `description` | Vue par les utilisateurs dans la doc |
| `author` | Crédits |
| `buttons` | Liste des boutons à ajouter dans la toolbar |

Pour chaque bouton :
- `panel` : actuellement seul `toolbar` est supporté
- `label` : texte du bouton (emoji + mots courts recommandés)
- `title` : tooltip
- `onclick` : nom d'une fonction JS définie dans votre `ui.js`

Si votre plugin n'ajoute pas de bouton (par exemple, il est invoqué via le menu contextuel ou un autre plugin), omettez la clé `buttons`.

---

## 🐍 Le backend (`__init__.py`)

La structure minimale :

```python
"""Mon plugin — description courte."""
from flask import request, jsonify
from second_brain import _ai_call  # Import des helpers du cœur

def register(app, rd_cfg):
    """Appelé automatiquement au démarrage par le plugin loader.
    
    Args:
        app:    L'instance Flask. Ajoutez vos routes ici.
        rd_cfg: Fonction sans argument qui retourne le dict de config 
                actuel (api_key, model, workspace, etc.)
    """
    
    @app.route("/api/monplugin/ping", methods=["GET"])
    def ping():
        return jsonify({"ok": True, "version": "1.0"})
    
    @app.route("/api/monplugin/process", methods=["POST"])
    def process():
        cfg = rd_cfg()  # Récupération de la config actuelle
        if not cfg.get("api_key"):
            return jsonify({"error": "Clé API manquante"}), 400
        
        d = request.json or {}
        # ... votre logique ici
        
        return jsonify({"result": "..."})
```

**Règles importantes** :

- **Ne pas modifier `app` globalement** (pas de `app.config[...]` ou autre)
- **Préfixer toutes vos routes** par `/api/<nom-plugin>/` pour éviter les collisions
- **Utiliser `rd_cfg()` à chaque requête** (et pas au chargement) : la config peut changer pendant l'exécution
- **Retourner du JSON** via `jsonify`, avec un code HTTP cohérent (400 = entrée invalide, 500 = bug serveur)

---

## 🎨 Le frontend

### `ui.html`

Ce fichier est injecté **avant `</body>`** dans la page principale. C'est là que vivent vos modals et panneaux. **Important** : pour qu'un modal s'affiche correctement, utilisez la convention CSS du projet :

```html
<div id="m-mon-plugin" class="ov">
  <div class="mb mb-wide" style="width:780px;max-height:90vh">
    <div style="display:flex;align-items:center;gap:10px">
      <div class="mt" style="flex:1">🦄 Mon plugin — titre</div>
      <button class="hbtn" onclick="closeMonPlugin()">✕</button>
    </div>
    <div class="minfo">Brève description de ce que fait ce panneau.</div>
    
    <div class="arx-block">
      <div class="arx-block-title">📦 Section</div>
      <!-- vos champs ici -->
    </div>
    
    <div class="mf">
      <button class="btn bs" onclick="closeMonPlugin()" style="width:auto;padding:7px 16px">Fermer</button>
      <button class="btn bp" onclick="monPluginRun()" style="width:auto;padding:7px 16px">🚀 Lancer</button>
    </div>
  </div>
</div>
```

| Classe | Rôle |
|---|---|
| `.ov` | Overlay (fond semi-transparent). Activé par `.classList.add('on')` |
| `.mb` | Modal box (boîte centrée) |
| `.mb-wide` | Variante large |
| `.mt` | Titre du modal |
| `.minfo` | Bandeau d'information sous le titre |
| `.arx-block` | Bloc de contenu (titre + zone) |
| `.arx-block-title` | Titre d'un bloc |
| `.mf` | Footer (boutons d'action) |
| `.btn .bp` | Bouton primaire |
| `.btn .bs` | Bouton secondaire |
| `.hbtn` | Bouton style header |

### `ui.js`

Code JS injecté dans `<script>` au bas du document. C'est ici que vous définissez les fonctions `onclick` déclarées dans votre `manifest.json` et dans votre `ui.html`.

```javascript
// Préfixez VOS fonctions/variables avec un namespace court pour éviter les collisions
var MON_PLUGIN = { state: null };

function openMonPlugin(){
  $('m-mon-plugin').classList.add('on');
  // ... initialisation
}

function closeMonPlugin(){
  $('m-mon-plugin').classList.remove('on');
}

async function monPluginRun(){
  var r = await fetch('/api/monplugin/process', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({foo: 'bar'})
  }).then(function(r){return r.json();});
  
  if(r.error){ toast('⚠ '+r.error); return; }
  toast('✓ '+r.result);
}
```

### `ui.css`

CSS injecté dans `<style>`. N'écrivez ici que **vos sélecteurs propres** — préfixés par `.mon-plugin-` pour éviter les collisions :

```css
.mon-plugin-card {
  padding: 8px;
  border: 1px solid var(--bdr);
  border-radius: 6px;
  background: var(--bg3);
}
```

Utilisez les **variables CSS du projet** (`--bg`, `--bg2`, `--bg3`, `--bg4`, `--bdr`, `--bdr2`, `--tx`, `--tx2`, `--tx3`, `--acc`, `--acc2`, `--red`, `--grn`, `--mono`, `--font`) plutôt que d'inventer des couleurs — votre plugin restera cohérent visuellement.

---

## 🧰 Helpers disponibles

Importables depuis `second_brain` :

| Fonction | Rôle |
|---|---|
| `_ai_call(cfg, messages, max_tokens, temp, timeout)` | Appel à l'API IA configurée. Retourne `(response_str, error_or_None)` |
| `extract_link_refs(content)` | Extrait les `[[wikilinks]]` d'un texte |
| `resolve_ref(ref, current_dir)` | Résout un wikilink en chemin absolu (3 niveaux : nom exact, glob, recherche workspace) |
| `parse_chat_response(text)` | Parse une réponse IA en pages structurées |

Exemple d'utilisation :

```python
from second_brain import _ai_call

response, err = _ai_call(
    cfg,
    messages=[
        {"role": "system", "content": "Tu es un assistant."},
        {"role": "user",   "content": "Bonjour"}
    ],
    max_tokens=500,
    temp=0.5,
    timeout=60
)
if err:
    return jsonify({"error": err}), 500
return jsonify({"reply": response})
```

---

## 🎨 Conventions UI

### Toasts (notifications brèves)

`toast('message', duration_ms)` est disponible globalement. Utilisez :
- `toast('✓ ...')` pour les succès
- `toast('⚠ ...')` pour les avertissements
- `toast('⏳ ...', 6000)` pour les opérations longues (durée augmentée)

### Fonction `$(id)`

Raccourci global pour `document.getElementById(id)`. Utilisez-la partout.

### Variables globales utiles côté client

| Variable | Contenu |
|---|---|
| `ACTIVE` | Chemin du fichier actuellement ouvert (string ou null) |
| `CUR_DIR` | Dossier courant de l'explorateur |
| `TABS` | Dict `{path: {content, saved, modified}}` des onglets ouverts |
| `HC` | Headers HTTP pour les requêtes JSON (`{'Content-Type': 'application/json'}`) |

### Fonctions du cœur réutilisables

| Fonction | Rôle |
|---|---|
| `openFileTab(path, content)` | Ouvre un fichier dans un nouvel onglet |
| `loadDir(path)` | Recharge l'explorateur sur un dossier |
| `safeFileName(s)` | Nettoie une chaîne pour en faire un nom de fichier valide |
| `post(url, data)` | POST JSON simple (sans timeout — préférez `fetch` direct pour les opérations longues) |
| `pushAIUndo(path, content, action)` | Enregistre l'état actuel pour permettre l'annulation (`Ctrl+Alt+Z`). À appeler **AVANT** toute modification destructive de l'éditeur |

---

## 🔗 Intégration cross-plugin

Le projet a une convention pour qu'un plugin propose ses fonctionnalités à un autre **sans couplage direct** : l'attribut HTML `data-sys-prompt-target`.

**Exemple concret** : le plugin **Prompts Manager** détecte automatiquement tous les `<textarea>` avec cet attribut et injecte un sélecteur de presets au-dessus. Tout plugin qui veut bénéficier de cette fonctionnalité ajoute simplement :

```html
<textarea id="mon-plugin-sys-prompt" data-sys-prompt-target rows="2"
          placeholder="Prompt système optionnel"></textarea>
```

C'est tout. Aucun import, aucune dépendance déclarée. Le sélecteur 📝 apparaît automatiquement si Prompts Manager est chargé, et il est ignoré s'il ne l'est pas.

**Si vous voulez exposer une fonctionnalité similaire** depuis votre plugin, suivez le même pattern : un attribut HTML descriptif (`data-mon-feature`) + un module JS qui scanne le DOM au chargement.

---

## 🎓 Tutoriel : plugin "Hello World"

Construisons un plugin minimal qui affiche un compteur et stocke sa valeur côté serveur.

### Étape 1 : créer le dossier

```bash
mkdir plugins/hello
cd plugins/hello
```

### Étape 2 : `manifest.json`

```json
{
  "name": "Hello World",
  "version": "1.0",
  "description": "Compteur démo du système de plugins.",
  "author": "Vous",
  "buttons": [
    {
      "panel": "toolbar",
      "label": "👋 Hello",
      "title": "Ouvrir le compteur démo",
      "onclick": "openHello()"
    }
  ]
}
```

### Étape 3 : `__init__.py`

```python
"""Plugin Hello — compteur démo."""
from pathlib import Path
import json
from flask import request, jsonify

STATE_FILE = Path.home() / ".secondbrain" / "hello_state.json"

def _load():
    if not STATE_FILE.exists(): return {"count": 0}
    try:    return json.loads(STATE_FILE.read_text())
    except: return {"count": 0}

def _save(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))

def register(app, rd_cfg):
    @app.route("/api/hello/get", methods=["GET"])
    def hello_get():
        return jsonify(_load())
    
    @app.route("/api/hello/increment", methods=["POST"])
    def hello_inc():
        s = _load()
        s["count"] += 1
        _save(s)
        return jsonify(s)
```

### Étape 4 : `ui.html`

```html
<div id="m-hello" class="ov">
  <div class="mb" style="width:380px">
    <div style="display:flex;align-items:center;gap:10px">
      <div class="mt" style="flex:1">👋 Hello World</div>
      <button class="hbtn" onclick="closeHello()">✕</button>
    </div>
    <div class="minfo">Compteur persistant — démo du système de plugins.</div>
    <div class="arx-block">
      <div style="text-align:center;font-size:48px;font-weight:700;color:var(--acc);margin:10px 0">
        <span id="hello-counter">0</span>
      </div>
    </div>
    <div class="mf">
      <button class="btn bs" onclick="closeHello()" style="width:auto;padding:7px 16px">Fermer</button>
      <button class="btn bp" onclick="helloIncrement()" style="width:auto;padding:7px 16px">+1</button>
    </div>
  </div>
</div>
```

### Étape 5 : `ui.js`

```javascript
async function openHello(){
  $('m-hello').classList.add('on');
  await helloRefresh();
}
function closeHello(){ $('m-hello').classList.remove('on'); }

async function helloRefresh(){
  var r = await fetch('/api/hello/get').then(function(r){return r.json();});
  $('hello-counter').textContent = r.count;
}

async function helloIncrement(){
  var r = await post('/api/hello/increment', {});
  $('hello-counter').textContent = r.count;
  toast('✓ Incrémenté');
}
```

### Étape 6 : redémarrer

```bash
python second_brain.py
```

Vous devriez voir dans les logs :
```
  ✓ Plugin chargé : hello (Hello World)
```

Le bouton **👋 Hello** apparaît dans la toolbar. Cliquez, incrémentez, refermez, rouvrez — la valeur persiste.

**Vous venez de comprendre 80 % de ce qu'il faut savoir.** Les 20 % restants sont les patterns ci-dessous.

---

## 🛠 Patterns courants

### Appel IA avec gestion d'erreur

```python
synthesis, err = _ai_call(cfg, [
    {"role": "system", "content": "..."},
    {"role": "user",   "content": user_prompt}
], max_tokens=2000, temp=0.5, timeout=180)
if err:
    return jsonify({"error": f"IA : {err}"}), 500
return jsonify({"synthesis": synthesis})
```

### Fetch parallèle (plusieurs URLs)

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def fetch_one(url):
    try:    return url, requests.get(url, timeout=(10, 20)).text, None
    except Exception as e: return url, None, str(e)

with ThreadPoolExecutor(max_workers=6) as ex:
    futs = [ex.submit(fetch_one, u) for u in urls]
    for fut in as_completed(futs):
        url, content, err = fut.result()
        # ...
```

### Cache TTL en mémoire

```python
import time
from threading import Lock

_CACHE = {}
_CACHE_LOCK = Lock()
_CACHE_TTL = 1800  # 30 min

def get_cached(key, fetcher):
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and (time.time() - cached[0]) < _CACHE_TTL:
            return cached[1]
    value = fetcher()
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)
    return value
```

### Persistance dans le vault (philosophie IRIS∞)

Plutôt que stocker dans `~/.secondbrain/X.json`, stockez dans le vault en `.md` éditable :

```python
def _vault_root():
    return rd_cfg().get('workspace') or str(Path.home())

def _load_data():
    f = Path(_vault_root()) / "mes-données.md"
    if not f.exists(): return []
    # Parser le markdown selon vos conventions
    return [...]

def _save_data(items):
    f = Path(_vault_root()) / "mes-données.md"
    f.write_text("\n".join([...]))
```

L'utilisateur peut alors **éditer le fichier directement** dans Second Brain.

### Long polling avec annulation côté client

Pour les opérations longues, côté client utilisez `AbortController` plutôt que `post()` :

```javascript
var abortCtrl = new AbortController();

async function runLongTask(){
  try {
    var resp = await fetch('/api/monplugin/longtask', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...}),
      signal: abortCtrl.signal,
    });
    return await resp.json();
  } catch(e){
    if(e.name === 'AbortError') toast('⏹ Annulé');
    else toast('⚠ Erreur : ' + e.message);
  }
}

function cancelTask(){ abortCtrl.abort(); }
```

---

## ⚠ Pièges à éviter

### 1. Round-trip JSON et types Python

Si votre `__init__.py` retourne un objet `datetime` ou `Decimal` dans une réponse JSON, Flask le sérialise en chaîne. Si vous renvoyez ces données vers une autre route, **vous recevez des chaînes, pas des objets** :

```python
# ❌ Plante quand date_parsed arrive du frontend
date_s = it['date_parsed'].strftime('%Y-%m-%d')

# ✅ Robuste
dp = it.get('date_parsed')
if isinstance(dp, datetime):
    date_s = dp.strftime('%Y-%m-%d')
elif isinstance(dp, str):
    date_s = dp[:10]
else:
    date_s = '?'
```

### 2. Convention CSS du modal

Si votre modal utilise `class="modal-overlay"` ou `class="modal-content"`, **il ne s'affichera pas**. Le projet utilise `class="ov"` + `class="mb"`. Si vous voyez votre bouton réagir au clic mais rien ne s'ouvrir, c'est ça.

### 3. Variables globales JS

`var foo = ...` au niveau racine de votre `ui.js` devient global. **Préfixez tout** avec un namespace :

```javascript
// ❌ Risque de collision avec un autre plugin
var STATE = {...};

// ✅
var MON_PLUGIN_STATE = {...};
// ou
var MP = { state: {...} };
```

### 4. Boucles infinies côté backend

Si vous faites du fetch agentique (l'IA propose une URL, vous la fetch, vous renvoyez à l'IA), **mettez une limite stricte** :

```python
for step in range(5):  # max 5 itérations
    response = _ai_call(...)
    if "TERMINÉ" in response: break
```

### 5. Timeout AI trop long

Si votre opération met >5 min, le navigateur (Chrome notamment) coupe la connexion HTTP. Préférez :
- Découper en plusieurs requêtes courtes
- Implémenter un AbortController côté client
- Réduire la taille du prompt envoyé à l'IA

### 6. Modification du fichier source par effet de bord

Si votre plugin ouvre un fichier généré (`openFileTab(newPath, content)`), **le fichier source de l'utilisateur ne doit jamais être modifié**. Si malgré tout vous touchez à `$('md-editor').value`, **appelez `pushAIUndo()` avant** pour qu'il puisse annuler.

---

## 🔍 Pour aller plus loin

Lisez le code des 5 plugins inclus, par ordre de complexité croissante :

1. **`prompts/`** — gestion d'un fichier JSON, cross-plugin via attribut HTML (~500 lignes)
2. **`context/`** — multi-sélection, suivi récursif de wikilinks (~470 lignes)
3. **`arxiv/`** — workflow agentique (formulation IA → fetch API → synthèse) (~510 lignes)
4. **`duckduckgo/`** — cascade de stratégies de fallback, fetch parallèle (~590 lignes)
5. **`rss/`** — auto-découverte, parsing XML, persistance dans le vault, mode multi-modal (~790 lignes)

Chacun illustre un pattern différent. **N'hésitez pas à copier-coller** des morceaux : c'est l'objectif.

---

## 📨 Partager votre plugin

Une fois votre plugin testé localement :

1. Créez un repo GitHub `secondbrain-plugin-<nom>` (convention proposée)
2. Mettez votre dossier plugin à la racine, avec un README expliquant l'installation
3. L'installation côté utilisateur : `cp -r votre-repo /chemin/vers/secondbrain/plugins/`, redémarrer

Si vous souhaitez qu'il soit listé dans la documentation officielle, ouvrez une PR sur ce repo en ajoutant une ligne au tableau des plugins du README principal.

---

Bon dev. Si quelque chose dans ce guide est confus ou obsolète, ouvrez une issue avec un cas concret — la doc évoluera avec les retours.

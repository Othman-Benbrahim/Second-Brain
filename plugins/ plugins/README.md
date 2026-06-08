# Plugins de Second Brain

Ce dossier contient les **plugins auto-découverts au démarrage**. Chaque plugin vit dans son propre sous-dossier et étend Second Brain sans toucher au cœur.

> 📖 Pour le guide complet (tutoriel pas-à-pas, helpers, patterns, pièges), voir [**PLUGIN-DEVELOPMENT.md**](../PLUGIN-DEVELOPMENT.md) à la racine du projet.

---

## 📦 Plugins inclus

| Dossier | Bouton | Description |
|---|---|---|
| `arxiv/` | 🔬 ArXiv | Recherche scientifique agentique : l'IA formule la requête, fetch les abstracts, synthétise |
| `context/` | 🗂 Multi-sélection | Construit un contexte multi-fichiers en suivant les wikilinks sur 1-5 niveaux |
| `duckduckgo/` | 🦆 DDG | Recherche web avec lecture du contenu complet des pages + synthèse IA (3 stratégies de fallback : lib `ddgs`, scraping `lite`, SearXNG) |
| `prompts/` | 📝 Prompts | Gestionnaire de presets de prompts système réutilisables. Auto-injection d'un sélecteur dans toute zone `<textarea data-sys-prompt-target>` |
| `rss/` | 🗞 RSS | Veille RSS/Atom avec auto-découverte des flux (`lemonde.fr` → `rss/une.xml`) + analyse IA. Stockage dans `flux-rss.md` du vault |

---

## 🧱 Anatomie d'un plugin

```
plugins/mon-plugin/
├── manifest.json    # Obligatoire — métadonnées + boutons UI
├── __init__.py      # Optionnel — code Python (routes Flask)
├── ui.html          # Optionnel — HTML injecté avant </body>
├── ui.css           # Optionnel — CSS injecté dans <style>
└── ui.js            # Optionnel — JS injecté dans <script>
```

Seul `manifest.json` est strictement obligatoire. Un plugin peut être 100 % serveur, 100 % client, ou les deux.

### `manifest.json` minimal

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
      "title": "Tooltip au survol",
      "onclick": "openMonPlugin()"
    }
  ]
}
```

### `__init__.py` minimal

```python
from flask import request, jsonify
from second_brain import _ai_call

def register(app, rd_cfg):
    @app.route("/api/monplugin/ping", methods=["GET"])
    def ping():
        return jsonify({"ok": True})
```

---

## ➕ Ajouter un plugin

1. Créer `plugins/mon-plugin/` avec au minimum un `manifest.json`
2. Redémarrer le serveur (`python second_brain.py`)
3. Vérifier dans les logs : `✓ Plugin chargé : mon-plugin (Mon Plugin)`
4. Le bouton apparaît dans la toolbar

---

## ⚠ Conventions importantes à respecter

- **Préfixer toutes les routes** par `/api/<nom-plugin>/` pour éviter les collisions
- **Préfixer les fonctions et variables JS** (ex : `MON_PLUGIN_STATE`, `openMonPlugin`) — sinon collisions garanties
- **Pour les modals**, utiliser les classes `class="ov"` + `class="mb"` (PAS `modal-overlay` / `modal-content`). Activation via `.classList.add('on')`. Sinon, le modal ne s'affichera pas
- **Utiliser les variables CSS du projet** (`--bg`, `--bg2`, `--bg3`, `--bdr`, `--tx`, `--acc`, etc.) plutôt que d'inventer des couleurs
- **Avant toute modification destructive de l'éditeur**, appeler `pushAIUndo(path, content, 'libellé action')` pour permettre l'annulation `Ctrl+Alt+Z`
- **Round-trip JSON** : Flask sérialise `datetime` en `str`. Si vos données reviennent du frontend, ce ne sont plus des `datetime` mais des chaînes. Tester avec `isinstance(dp, datetime)` avant d'appeler `.strftime()`

---

## 🔗 Intégration cross-plugin

Le projet a une convention pour qu'un plugin propose ses fonctionnalités à un autre **sans couplage direct** : l'attribut HTML `data-sys-prompt-target`.

Tout `<textarea>` qui porte cet attribut reçoit automatiquement un sélecteur de prompts système (📝) au-dessus, si le plugin **Prompts Manager** est chargé. C'est complètement opt-in et ne nécessite aucun import :

```html
<textarea id="mon-plugin-sys-prompt" data-sys-prompt-target rows="2"
          placeholder="Prompt système optionnel"></textarea>
```

---

## 📚 Documentation complète

Le présent fichier est un **résumé**. Pour le guide détaillé avec tutoriel "Hello World", helpers disponibles, patterns courants, et tous les pièges connus → **[../PLUGIN-DEVELOPMENT.md](../PLUGIN-DEVELOPMENT.md)**.

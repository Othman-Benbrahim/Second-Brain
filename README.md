# 🧠 Second Brain

> Éditeur Markdown local avec IA intégrée, graphe de connaissances, et architecture à plugins.
> Pensé pour un workflow de prise de notes profond, type Obsidian, mais sans cloud et avec l'IA directement dans l'éditeur.

![status](https://img.shields.io/badge/status-stable-green) ![python](https://img.shields.io/badge/python-3.10%2B-blue) ![licence](https://img.shields.io/badge/licence-voir%20LICENSE-lightgrey)

---

## ⚡ Démarrage rapide

```bash
git clone <ce-repo> secondbrain
cd secondbrain
pip install -r requirements.txt
python second_brain.py
```

Le navigateur s'ouvre sur `http://localhost:5000`. Première chose à faire : aller dans **⚙ Paramètres**, renseigner votre **clé API** (OpenRouter, Anthropic, ou tout endpoint OpenAI-compatible) et la **racine de votre vault** (dossier où vivront vos `.md`).

---

## ✨ Ce que vous obtenez

### 📝 Édition Markdown

- Trois modes : **✏️ Éditer** · **👁 Aperçu** · **⧉ Scindé** (côte à côte)
- **Wikilinks** `[[fichier]]` cliquables avec auto-complétion
- **Onglets** multi-fichiers avec indicateur de modification non sauvegardée
- **Carte mentale** automatique (structure du fichier dans le panneau latéral)
- **Recherche full-text** dans tout le vault (`Ctrl+Shift+F`)
- **Graphe de connaissances** (toile d'araignée des liens entre vos fichiers, coloré par sous-dossier)

### 🤖 IA intégrée

- **Sur le fichier entier** : ✨ Améliorer · 🌿 Développer · 📋 Restructurer · 📄 Résumer
- **Sur sélection** (sélectionnez du texte → une mini-toolbar apparaît) : ✨ Améliorer · ✂️ Raccourcir · 💡 Expliquer · 🌐 Traduire — **chaque action crée un nouveau fichier**, le source reste intact
- **↶ Annuler IA** (`Ctrl+Alt+Z`) : annule la dernière modification destructive — un bouton orange pulsant apparaît automatiquement après chaque opération
- **🔗 Liens IA** : l'IA propose des `[[wikilinks]]` cohérents pour votre fichier

### 🔌 Architecture à plugins

Cinq plugins inclus, tous chargés automatiquement au démarrage :

| Plugin | Bouton | Fonction |
|---|---|---|
| **ArXiv** | 🔬 ArXiv | Recherche scientifique agentique (l'IA formule la requête, fetch les abstracts, synthétise) |
| **Context Builder** | 🗂 Multi-sélection | Construit un contexte multi-fichiers en suivant les wikilinks sur 1-5 niveaux |
| **DuckDuckGo** | 🦆 DDG | Recherche web avec lecture du contenu complet des pages + synthèse IA |
| **Prompts Manager** | 📝 Prompts | Gestionnaire de presets de prompts système réutilisables |
| **RSS Feeds** | 🗞 RSS | Veille RSS/Atom avec auto-découverte des flux + analyse IA |

→ Voir **[PLUGIN-DEVELOPMENT.md](./PLUGIN-DEVELOPMENT.md)** pour créer le vôtre.

---

## 🗂 Structure du projet

```
secondbrain/
├── second_brain.py       # Cœur Flask : routes + plugin loader
├── ui.html               # UI principale (HTML/CSS/JS en un seul fichier)
├── requirements.txt      # flask, requests
├── plugins/              # Plugins auto-découverts au démarrage
│   ├── README.md         # Convention de développement (résumé)
│   ├── arxiv/
│   ├── context/
│   ├── duckduckgo/
│   ├── prompts/
│   └── rss/
├── PLUGIN-DEVELOPMENT.md # Guide complet pour créer un plugin
└── README.md             # Ce fichier
```

---

## ⚙ Configuration

Tous les paramètres sont accessibles via **⚙ Paramètres** (icône engrenage en haut à droite), stockés dans `~/.secondbrain/config.json`.

| Champ | Rôle |
|---|---|
| `workspace` | Racine du vault (dossier des `.md`) |
| `api_key` | Clé API du fournisseur IA |
| `api_url` | URL du endpoint (défaut : OpenRouter) |
| `model` | Nom du modèle (ex: `anthropic/claude-3.5-sonnet`) |
| `temperature` | Créativité de l'IA (0.0–1.0) |

**Compatibilité API** : tout endpoint compatible OpenAI (OpenRouter, Anthropic direct, Mistral, Together, OpenAI, vLLM local, etc.).

---

## ⌨ Raccourcis clavier

| Raccourci | Action |
|---|---|
| `Ctrl+S` | Sauvegarder le fichier courant |
| `Ctrl+Shift+F` | Recherche full-text dans le vault |
| `Ctrl+Alt+Z` | Annuler la dernière modification IA |
| `Échap` | Fermer modal / panneau ouvert |

---

## 🔒 Vie privée

- **100 % local** : aucune télémétrie, aucune connexion sortante sauf vers l'API IA que vous avez configurée
- **Pas de base de données** : tout vit dans votre vault `.md` + `~/.secondbrain/` (config + presets de prompts)
- **Vos fichiers ne quittent pas votre machine** sauf quand vous invoquez l'IA (et même alors, seul le contenu nécessaire est envoyé)

---

## 🧱 Stack technique

- **Backend** : Python 3.10+, Flask
- **Frontend** : HTML/CSS/Vanilla JS, D3.js (pour le graphe), aucun framework
- **IA** : Endpoint OpenAI-compatible (REST + JSON)
- **Persistance** : Markdown plain text + JSON pour les presets

Aucune dépendance lourde, aucun build step. Vous éditez `ui.html` → vous rafraîchissez la page → c'est appliqué.

---

## 🤝 Contribuer

Le projet est conçu pour être étendu via plugins. Vous voulez ajouter une fonctionnalité ? **Créez un plugin** plutôt que de modifier le cœur — vos changements restent isolés et upgradables.

→ **[PLUGIN-DEVELOPMENT.md](./PLUGIN-DEVELOPMENT.md)**

Pour les contributions au cœur (bugs, performance, ergonomie), ouvrez une issue ou une pull request avec un cas de reproduction clair.

---

## 📜 Licence

Voir le fichier `LICENSE`.

---

## 🙏 Remerciements

Construit par Othman Benbrahim avec l'aide de Claude (Anthropic) comme pair-programmeur.
Inspiré par Obsidian, Logseq, et la philosophie « tout en `.md` lisible » de l'écosystème IRIS∞.

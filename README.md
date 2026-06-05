# 🧠 Second Brain

> Éditeur Markdown local style Obsidian, avec IA intégrée, carte mentale et graphe de connaissances — le tout dans deux fichiers.

Une alternative locale, simple et hackable pour gérer son second cerveau, augmentée par n'importe quelle API OpenAI-compatible (OpenAI, FantasyAI, DeepSeek, Groq, Ollama…).

---

## ✨ Fonctionnalités

| | |
|---|---|
| **Éditeur Markdown** | 3 modes : édition · aperçu · scindé |
| **Carte mentale** | Générée automatiquement depuis les titres `#` du fichier actif |
| **Wikilinks** | `[[autre_fichier]]` cliquables en aperçu |
| **Backlinks** | Toutes les notes qui pointent vers la note actuelle |
| **Graphe de connaissances** | Vue force-directed de tout un dossier et de ses liens |
| **Recherche full-text** | Récursive sur tous les `.md` du dossier (Ctrl+Shift+F) |
| **Tags** | `#tag` extraits automatiquement, filtrage de l'explorateur |
| **Renommage inline** | Double-clic sur un fichier |
| **IA sur le fichier** | Améliorer, restructurer, résumer, développer, instruction libre |
| **IA sur sélection** | Résultat dans un nouveau fichier auto-nommé |
| **Synthèse de dossier** | Analyse globale de toutes les notes d'un dossier |
| **Suggestions de liens** | L'IA propose où placer des `[[wikilinks]]` |

Aucune base de données, aucun build, aucune dépendance npm. Vos notes restent des fichiers `.md` standard, compatibles Obsidian.

---

## 🚀 Installation

```bash
git clone https://github.com/<votre-nom>/second-brain.git
cd second-brain
pip install -r requirements.txt
python second_brain.py
```

L'interface s'ouvre automatiquement sur `http://localhost:5000`.

> Sous Windows, utiliser `py` au lieu de `python` si nécessaire.

---

## 🔧 Configuration

Au premier lancement, **⚙ Paramètres** :

| Champ | Description |
|-------|-------------|
| **Clé API** | Votre clé pour le fournisseur choisi |
| **Modèle** | `gpt-4o`, `claude-3-5-sonnet`, `deepseek-chat`… |
| **URL API** | `https://api.openai.com/v1`, `https://fantasyai.cloud/api/v1`, etc. |
| **Dossier de notes** | Chemin vers vos `.md` |

Les paramètres sont stockés dans `~/.secondbrain/config.json` (hors du dépôt, jamais committé).

---

## 🔌 APIs supportées

Toute API OpenAI-compatible exposant un endpoint `/chat/completions` :

- OpenAI
- FantasyAI Cloud
- DeepSeek
- Groq
- Ollama (local)
- LM Studio (local)
- OpenRouter
- Anthropic via gateway compatible

Le parser gère à la fois les réponses JSON classiques et les flux SSE (streaming).

---

## ⌨ Raccourcis

| Touche | Action |
|--------|--------|
| `Ctrl+S` | Sauvegarder le fichier actif |
| `Ctrl+Shift+F` | Recherche full-text |
| `Double-clic` | Renommer un fichier |
| `Échap` | Fermer modal / menu |

---

## 🏗 Architecture

```
second-brain/
├── second_brain.py     ← Backend Flask (~370 lignes)
├── ui.html             ← Frontend complet (~1200 lignes)
├── requirements.txt    ← flask, requests
├── README.md
├── LICENSE
└── .gitignore
```

Tout le frontend (HTML + CSS + JS + D3.js + marked.js) tient dans un seul fichier statique chargé par Flask.

**Stack technique** :

| Composant | Rôle |
|-----------|------|
| Flask | Serveur HTTP local |
| requests | Appels API IA |
| D3.js 7 | Carte mentale, graphe force-directed |
| marked.js | Rendu Markdown |

---

## 📁 Structure des données

```
~/.secondbrain/                ← données utilisateur (jamais committées)
  └── config.json              ← Clé API + préférences

<votre dossier de notes>/      ← n'importe où sur votre disque
  ├── note1.md
  ├── projet/
  │   └── note2.md
  └── ...
```

---

## 🛣 Roadmap

- [ ] Mode hors-ligne avec modèle local par défaut
- [ ] Export multi-fichiers en PDF
- [ ] Recherche sémantique vectorielle
- [ ] Plugins / hooks utilisateur
- [ ] Synchronisation Git intégrée

---

## 📄 Licence

MIT — voir [LICENSE](LICENSE).

---

## 🤝 Contribution

Les pull requests sont les bienvenues. Pour un changement structurant, ouvrir d'abord une issue pour en discuter.

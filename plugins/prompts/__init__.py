"""
Plugin Prompts Manager — presets de prompts système persistants.

Stockage : ~/.secondbrain/system_prompts.json

Routes exposées :
  GET  /api/prompts/list   — liste tous les presets
  POST /api/prompts/save   — crée ou met à jour un preset
  POST /api/prompts/delete — supprime un preset par id
  POST /api/prompts/reset  — restaure les presets d'usine
"""
import json, re, uuid
from pathlib import Path
from flask import request, jsonify

PROMPTS_FILE = Path.home() / ".secondbrain" / "system_prompts.json"

# Presets d'usine — adaptés au profil IRIS∞ + usages courants
DEFAULT_PROMPTS = [
    {
        "id": "neutral-fr",
        "name": "Synthèse française neutre",
        "content": ("Tu réponds en français, avec rigueur et concision. "
                    "Markdown structuré, pas de fioritures, pas de meta-commentaires. "
                    "Cite les sources et fichiers pertinents via [[nom_du_fichier]] quand utile.")
    },
    {
        "id": "iris-symbolic",
        "name": "Registre IRIS∞ symbolique",
        "content": ("Tu réponds dans le registre symbolique IRIS∞ : grammaire des arcanes, "
                    "glyphes STÈLE, résonances fractales, motifs archétypaux. "
                    "Tu maintiens explicitement la distinction analytique/symbolique. "
                    "Tu cites les skills par leur nom (TCAI, NEXUS-ARCHÊ, KAIROS, LÉR, etc.) quand pertinent. "
                    "Tu n'inventes pas de glyphes ou de structures non documentées dans le corpus.")
    },
    {
        "id": "zetetic-broch",
        "name": "Analyse zététique (Broch)",
        "content": ("Tu appliques les filtres zététiques d'Henri Broch : charge de la preuve sur celui qui affirme, "
                    "parcimonie, hypothèses alternatives, effet impact, sélection des preuves, effet placebo, "
                    "régression vers la moyenne. Tu identifies les biais cognitifs et rhétoriques. "
                    "Tu réponds avec rigueur épistémique : sans complaisance, sans cynisme, "
                    "en distinguant ce qui est démontré, plausible, spéculatif ou réfuté.")
    },
    {
        "id": "osint-intel",
        "name": "Renseignement OSINT",
        "content": ("Tu analyses comme un analyste OSINT en renseignement stratégique. "
                    "Tu produis : signaux faibles, scénarios concurrents avec probabilités explicites "
                    "(somme = 100%), indicateurs de bascule observables, conditions de falsification. "
                    "Registre factuel et épistémique. Pas de compression symbolique. "
                    "Tu sépares clairement les faits, les hypothèses, et les inférences.")
    },
    {
        "id": "superforecast",
        "name": "Superforecasting (Tetlock)",
        "content": ("Tu formules toute prédiction selon le protocole Good Judgment Project (Tetlock & Mellers) : "
                    "probabilité chiffrée explicite, horizon temporel borné, indicateurs observables, "
                    "conditions de falsification. Tu utilises l'inférence bayésienne quand pertinent "
                    "(prior + vraisemblance → posterior). Tu réponds en français.")
    },
    {
        "id": "code-review",
        "name": "Revue de code rigoureuse",
        "content": ("Tu fais une revue de code rigoureuse : sécurité, performance, lisibilité, edge cases, "
                    "tests, conventions du langage. Tu pointes les vraies fragilités, "
                    "pas les détails cosmétiques. Tu réponds en français mais utilises les termes techniques anglais "
                    "standards. Format Markdown avec sections claires.")
    },
]


def _load_prompts():
    if not PROMPTS_FILE.exists():
        return list(DEFAULT_PROMPTS)
    try:
        data = json.loads(PROMPTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else list(DEFAULT_PROMPTS)
    except Exception:
        return list(DEFAULT_PROMPTS)


def _save_prompts(prompts):
    PROMPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROMPTS_FILE.write_text(json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_id(name, existing_ids):
    base = re.sub(r'[^a-z0-9-]', '-', name.lower()).strip('-')[:32]
    if not base: base = uuid.uuid4().hex[:8]
    pid, i = base, 2
    while pid in existing_ids:
        pid = f"{base}-{i}"; i += 1
    return pid


def register(app, rd_cfg):

    @app.route("/api/prompts/list", methods=["GET"])
    def prompts_list():
        return jsonify({"prompts": _load_prompts()})

    @app.route("/api/prompts/save", methods=["POST"])
    def prompts_save():
        d       = request.json or {}
        name    = (d.get("name", "") or "").strip()
        content = (d.get("content", "") or "").strip()
        pid     = (d.get("id", "") or "").strip()
        if not name:    return jsonify({"error": "Nom vide"}), 400
        if not content: return jsonify({"error": "Contenu vide"}), 400

        prompts = _load_prompts()
        if pid:
            for p in prompts:
                if p["id"] == pid:
                    p["name"]    = name
                    p["content"] = content
                    _save_prompts(prompts)
                    return jsonify({"ok": True, "id": pid, "updated": True})
        # Création nouveau preset
        new_id = _make_id(name, {p["id"] for p in prompts})
        prompts.append({"id": new_id, "name": name, "content": content})
        _save_prompts(prompts)
        return jsonify({"ok": True, "id": new_id, "created": True})

    @app.route("/api/prompts/delete", methods=["POST"])
    def prompts_delete():
        pid = ((request.json or {}).get("id", "") or "").strip()
        if not pid: return jsonify({"error": "ID requis"}), 400
        prompts = _load_prompts()
        before = len(prompts)
        prompts = [p for p in prompts if p["id"] != pid]
        if len(prompts) == before:
            return jsonify({"error": "Preset introuvable"}), 404
        _save_prompts(prompts)
        return jsonify({"ok": True})

    @app.route("/api/prompts/reset", methods=["POST"])
    def prompts_reset():
        _save_prompts(list(DEFAULT_PROMPTS))
        return jsonify({"ok": True, "prompts": _load_prompts()})

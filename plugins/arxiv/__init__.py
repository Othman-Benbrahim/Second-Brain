"""
Plugin ArXiv — recherche agentique de littérature scientifique.

Workflow :
1. IA génère une requête ArXiv depuis le contenu du fichier ouvert
2. Serveur interroge ArXiv (rate-limit 3.5 s, cache 5 min, retry 429)
3. IA synthétise les papiers trouvés en français avec citations

Routes exposées :
  GET  /api/arxiv/ping
  GET  /api/arxiv/search
  POST /api/arxiv/agentic
  POST /api/arxiv/synthesize
"""
import time
import xml.etree.ElementTree as ET
from threading import Lock
from flask import request, jsonify
import requests as http

# Helpers récupérés du core
from second_brain import _ai_call

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_NS  = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# Rate-limiting (politique officielle ArXiv ≈ 1 req / 3 s)
_ARXIV_LAST         = [0.0]
_ARXIV_LOCK         = Lock()
_ARXIV_MIN_INTERVAL = 3.5
_ARXIV_CACHE        = {}
_ARXIV_CACHE_TTL    = 300

def _arxiv_wait():
    with _ARXIV_LOCK:
        elapsed = time.time() - _ARXIV_LAST[0]
        if elapsed < _ARXIV_MIN_INTERVAL:
            time.sleep(_ARXIV_MIN_INTERVAL - elapsed)
        _ARXIV_LAST[0] = time.time()

def _arxiv_query(query, n=8):
    """Interroge ArXiv avec rate-limit + cache 5 min + retry sur 429."""
    cache_key = (query, n)
    cached = _ARXIV_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _ARXIV_CACHE_TTL:
        return cached[1]

    for attempt in range(2):
        _arxiv_wait()
        try:
            r = http.get(ARXIV_API,
                params={"search_query": query, "max_results": min(max(1, n), 20), "sortBy": "relevance"},
                headers={"User-Agent": "SecondBrain/1.0 (https://github.com)"},
                timeout=(15, 45))
        except http.exceptions.ReadTimeout:
            if attempt < 1:
                time.sleep(5); continue
            raise RuntimeError(
                "Timeout ArXiv après 2 tentatives (45 s × 2). "
                "La requête est peut-être trop restrictive ou ArXiv est surchargé. "
                "→ Simplifiez la requête dans le champ ci-dessous, ou patientez 1 min."
            )
        except http.exceptions.ConnectTimeout:
            if attempt < 1:
                time.sleep(3); continue
            raise RuntimeError("ArXiv injoignable (timeout de connexion). Vérifiez votre connexion.")
        except http.exceptions.RequestException as e:
            if attempt < 1:
                time.sleep(3); continue
            raise RuntimeError(f"Connexion ArXiv : {e}")

        if r.status_code == 429:
            wait = 12 * (attempt + 1)
            if attempt < 1:
                time.sleep(wait); continue
            raise RuntimeError(
                "ArXiv : trop de requêtes (HTTP 429). Patientez 30-60 s puis relancez. "
                "Limite officielle ≈ 1 req / 3 s par IP. Cache 5 min pour requêtes identiques."
            )

        try:
            r.raise_for_status()
        except http.exceptions.HTTPError as e:
            raise RuntimeError(f"ArXiv HTTP {r.status_code} : {str(e)[:200]}")

        try:
            root = ET.fromstring(r.text)
        except ET.ParseError as e:
            raise RuntimeError(f"ArXiv réponse XML invalide : {e}")

        out = []
        for entry in root.findall("a:entry", ARXIV_NS):
            authors = [(a.find("a:name", ARXIV_NS).text or "").strip()
                       for a in entry.findall("a:author", ARXIV_NS)]
            out.append({
                "title"     : (entry.find("a:title", ARXIV_NS).text or "").strip().replace("\n  ", " "),
                "abstract"  : (entry.find("a:summary", ARXIV_NS).text or "").strip().replace("\n", " ")[:900],
                "authors"   : authors[:6],
                "url"       : (entry.find("a:id", ARXIV_NS).text or "").strip(),
                "published" : ((entry.find("a:published", ARXIV_NS).text or "")[:10]),
                "categories": [c.attrib.get("term","") for c in entry.findall("a:category", ARXIV_NS)][:4],
            })
        _ARXIV_CACHE[cache_key] = (time.time(), out)
        return out
    return []


def register(app, rd_cfg):
    """Point d'entrée appelé par le core au chargement."""

    @app.route("/api/arxiv/ping", methods=["GET"])
    def arxiv_ping():
        """Test minimal de connexion ArXiv (1 papier, sans IA)."""
        t0 = time.time()
        try:
            r = http.get(ARXIV_API,
                params={"search_query": "cat:cs.AI", "max_results": 1},
                headers={"User-Agent": "SecondBrain/1.0"},
                timeout=(15, 45))
            return jsonify({
                "ok": r.status_code == 200,
                "status": r.status_code,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "body_size": len(r.text),
                "body_preview": r.text[:200],
            })
        except Exception as e:
            return jsonify({
                "ok": False,
                "error": f"{type(e).__name__}: {str(e)[:300]}",
                "elapsed_ms": int((time.time() - t0) * 1000),
            })

    @app.route("/api/arxiv/search", methods=["GET"])
    def arxiv_search():
        """Recherche ArXiv directe (mode manuel)."""
        q = request.args.get("q", "").strip()
        n = int(request.args.get("n", 8))
        if not q: return jsonify({"error": "Requête vide"}), 400
        try:
            return jsonify({"query": q, "papers": _arxiv_query(q, n)})
        except Exception as e:
            return jsonify({"error": f"ArXiv : {str(e)[:300]}"}), 500

    @app.route("/api/arxiv/agentic", methods=["POST"])
    def arxiv_agentic():
        """Étape 1+2 : IA génère la requête, puis interroge ArXiv."""
        cfg = rd_cfg()
        if not cfg.get("api_key"): return jsonify({"error": "Clé API manquante"}), 400
        d = request.json
        content = (d.get("content","") or "")[:4000]
        fname   = d.get("name", "note")
        hint    = (d.get("hint","") or "").strip()
        n_pap   = int(d.get("n", 8))

        prompt = (
            f'À partir du fichier "{fname}" ci-dessous, génère UNE requête ArXiv courte '
            f'et précise (3-8 mots-clés en anglais, opérateurs ArXiv autorisés : '
            f'AND, OR, ti:, abs:, cat:).\nRéponds UNIQUEMENT avec la requête, '
            f'sans guillemets ni explication.\n\n'
            f'=== DÉBUT DU FICHIER ===\n{content}\n=== FIN ==='
        )
        if hint: prompt += f"\n\nIndication de l'utilisateur (à prioriser) : {hint}"

        query, err = _ai_call(cfg, [
            {"role": "system", "content": "Tu génères des requêtes ArXiv optimales en anglais. Pas de fioritures."},
            {"role": "user",   "content": prompt}
        ], max_tokens=200, temp=0.3)
        if err: return jsonify({"error": f"IA (génération requête) : {err}"}), 500
        query = (query or "").strip().strip('"').strip("'").replace("\n", " ")[:250]
        if not query: return jsonify({"error": "L'IA n'a pas généré de requête"}), 500

        try:
            papers = _arxiv_query(query, n_pap)
        except Exception as e:
            return jsonify({"error": f"ArXiv : {type(e).__name__}: {str(e)[:300]}", "query": query}), 500

        return jsonify({"query": query, "papers": papers, "count": len(papers)})

    @app.route("/api/arxiv/synthesize", methods=["POST"])
    def arxiv_synthesize():
        """Étape 3 : synthèse française des papiers, avec citations."""
        cfg = rd_cfg()
        if not cfg.get("api_key"): return jsonify({"error": "Clé API manquante"}), 400
        d = request.json
        papers  = d.get("papers", [])
        content = (d.get("content","") or "")[:3000]
        fname   = d.get("name", "note")
        query   = d.get("query", "")
        if not papers: return jsonify({"error": "Aucun papier à synthétiser"}), 400

        papers_md = "\n\n".join([
            f"### [{i+1}] {p.get('title','(sans titre)')}\n"
            f"**Auteurs** : {', '.join(p.get('authors',[])[:3])}{'…' if len(p.get('authors',[]))>3 else ''}\n"
            f"**Publié** : {p.get('published','')}  ·  **URL** : {p.get('url','')}\n"
            f"**Abstract** : {p.get('abstract','')[:500]}"
            for i, p in enumerate(papers)
        ])
        user_prompt = (
            f"Voici {len(papers)} articles ArXiv trouvés via la requête `{query}`, "
            f"en lien avec mon fichier \"{fname}\".\n\n"
            f"Produis une synthèse en français (Markdown structuré) qui :\n"
            f"1. Identifie les **thèmes principaux** abordés\n"
            f"2. Souligne **convergences et divergences** entre les approches\n"
            f"3. Identifie les **liens conceptuels** avec mon fichier d'origine\n"
            f"4. Propose 2-3 **pistes de recherche** à explorer\n\n"
            f"Cite chaque papier avec son numéro [1], [2], etc. dans le texte.\n"
            f"Termine par une section **## Références** listant tous les papiers cités.\n\n"
            f"=== MON FICHIER \"{fname}\" ===\n{content}\n=== FIN ===\n\n"
            f"=== PAPIERS ARXIV ===\n{papers_md}\n=== FIN ==="
        )
        synthesis, err = _ai_call(cfg, [
            {"role": "system", "content": "Tu es expert en synthèse de littérature scientifique. Tu écris en français en Markdown structuré, avec rigueur et concision."},
            {"role": "user",   "content": user_prompt}
        ], max_tokens=2500, temp=0.5, timeout=240)
        if err: return jsonify({"error": f"IA (synthèse) : {err}"}), 500
        return jsonify({"synthesis": synthesis})

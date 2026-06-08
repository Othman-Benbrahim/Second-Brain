"""
Plugin DuckDuckGo Search — recherche web agentique avec synthèse IA.

STRATÉGIES (essayées dans l'ordre, fallback automatique) :
  1. Bibliothèque `ddgs` ou `duckduckgo-search` (recommandé)
     → `pip install ddgs`  ou  `pip install duckduckgo-search`
  2. Scraping de lite.duckduckgo.com (fallback sans dépendance)
"""
import re, time, html
from threading import Lock
from urllib.parse import unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import request, jsonify
import requests as http

from second_brain import _ai_call

# Stratégie 1 : bibliothèque (essayer les 2 noms, le projet a été renommé)
HAS_DDGS_LIB = False
DDGS_FLAVOR  = None
try:
    from ddgs import DDGS                          # nouveau nom (≥ 2025)
    HAS_DDGS_LIB = True
    DDGS_FLAVOR  = "ddgs"
except ImportError:
    try:
        from duckduckgo_search import DDGS         # ancien nom
        HAS_DDGS_LIB = True
        DDGS_FLAVOR  = "duckduckgo-search"
    except ImportError:
        pass

DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
DDG_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Instances SearXNG publiques (fallback ultime si DDG bloque)
SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://searx.tiekoetter.com",
    "https://priv.au",
    "https://search.brave4u.com",
    "https://searx.work",
]

_DDG_LAST         = [0.0]
_DDG_LOCK         = Lock()
_DDG_MIN_INTERVAL = 1.5
_DDG_CACHE        = {}
_DDG_CACHE_TTL    = 300

# Cache de pages fetchées (les pages bougent moins vite que les recherches)
_PAGE_CACHE       = {}
_PAGE_CACHE_TTL   = 1800     # 30 min
PAGE_FETCH_TIMEOUT = (10, 15)  # (connect, read)
PAGE_MAX_CHARS     = 5000      # texte par page envoyé à l'IA
PAGE_MAX_PARALLEL  = 5         # threads simultanés


def _ddg_wait():
    with _DDG_LOCK:
        elapsed = time.time() - _DDG_LAST[0]
        if elapsed < _DDG_MIN_INTERVAL:
            time.sleep(_DDG_MIN_INTERVAL - elapsed)
        _DDG_LAST[0] = time.time()


def _strip_html(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _decode_ddg_url(href):
    if not href: return ""
    if href.startswith("//"): href = "https:" + href
    if "/l/?" in href or href.startswith("/l/"):
        try:
            qs = href.split("?", 1)[1]
            for part in qs.split("&"):
                if part.startswith("uddg="):
                    return unquote(part[5:])
        except Exception:
            pass
    return href


def _lib_call(query, n, backend=None, region="wt-wt", safesearch="moderate"):
    """Wrap unique de DDGS.text() — gère versions anciennes ET récentes."""
    ddgs = DDGS()
    try:
        # Tentative complète (versions récentes : backend en kwarg)
        kwargs = {"max_results": n, "region": region, "safesearch": safesearch}
        if backend: kwargs["backend"] = backend
        raw = ddgs.text(query, **kwargs)
        return list(raw) if raw is not None else []
    except TypeError as e:
        # Version qui n'accepte pas certains kwargs : retry sans backend
        try:
            raw = ddgs.text(query, max_results=n, region=region, safesearch=safesearch)
            return list(raw) if raw is not None else []
        except TypeError:
            # Version vraiment ancienne : appel minimal
            try:
                raw = ddgs.text(query, max_results=n)
                return list(raw) if raw is not None else []
            except Exception as e3:
                raise RuntimeError(f"ddgs.text(minimal) : {type(e3).__name__}: {e3}")
    except Exception as e:
        raise RuntimeError(f"ddgs.text() : {type(e).__name__}: {str(e)[:200]}")
    finally:
        try:
            if hasattr(ddgs, "close"): ddgs.close()
        except Exception: pass


def _search_via_lib(query, n):
    """Recherche via la bibliothèque. Le backend 'api' (défaut) est souvent
    soft-bloqué et retourne 0 résultats silencieusement. On essaie donc
    explicitement 'html' puis 'lite' avant de retomber sur 'api'."""
    last_err = None
    # Backend en 1er argument : 'html' et 'lite' sont plus fiables que 'api' (défaut)
    backend_configs = [
        ("html", "wt-wt", "moderate"),
        ("lite", "wt-wt", "moderate"),
        ("html", "us-en", "moderate"),
        (None,   "wt-wt", "moderate"),   # défaut, en dernier recours
    ]
    for backend, region, safe in backend_configs:
        try:
            raw = _lib_call(query, n, backend=backend, region=region, safesearch=safe)
            if raw:
                return [{
                    "title":   (r.get("title") or "")[:300],
                    "url":     r.get("href") or r.get("url") or "",
                    "snippet": (r.get("body")  or r.get("snippet") or "")[:700],
                } for r in raw if isinstance(r, dict)]
            last_err = RuntimeError(f"backend={backend!r}, region={region!r} : 0 résultat")
        except Exception as e:
            last_err = e
            time.sleep(0.4)
            continue
    if last_err: raise last_err
    return []


_RE_LITE_LINK = re.compile(
    r"<a[^>]+class=['\"]result-link['\"][^>]+href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>"
    r"|<a[^>]+href=['\"]([^'\"]+)['\"][^>]+class=['\"]result-link['\"][^>]*>(.*?)</a>",
    re.DOTALL | re.IGNORECASE
)
_RE_LITE_SNIPPET = re.compile(
    r"<td[^>]+class=['\"]result-snippet['\"][^>]*>(.*?)</td>",
    re.DOTALL | re.IGNORECASE
)


def _search_via_lite(query, n):
    """Fallback : scraping de l'interface lite, avec retry 30s sur 202."""
    for attempt in range(2):
        r = http.get(DDG_LITE_URL,
            params={"q": query, "kl": "wt-wt"},
            headers={
                "User-Agent": DDG_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
                "Referer": "https://duckduckgo.com/",
                "DNT": "1",
            },
            timeout=(15, 30))

        if r.status_code == 202:
            if attempt < 1:
                time.sleep(30)  # Le blocage 202 est souvent court — on attend
                continue
            raise RuntimeError(
                "DDG HTTP 202 (votre IP est rate-limitée par DuckDuckGo). "
                "Solutions immédiates : (1) patientez 15-30 min, "
                "(2) VPN/changement de réseau, "
                "(3) le fallback SearXNG va prendre le relais."
            )
        if r.status_code == 429:
            raise RuntimeError("DDG HTTP 429 — rate-limit. Patientez 60 s.")
        if r.status_code != 200:
            raise RuntimeError(f"DDG lite HTTP {r.status_code}")
        break  # succès, on sort de la boucle

    text = r.text
    if "anomaly" in text.lower()[:1000]:
        raise RuntimeError("DDG marque la requête comme anomalie — passage à SearXNG.")

    out, items = [], []
    for m in _RE_LITE_LINK.finditer(text):
        href  = m.group(1) or m.group(3)
        title = m.group(2) or m.group(4)
        if href: items.append((href, title))

    snippets = [m.group(1) for m in _RE_LITE_SNIPPET.finditer(text)]

    for i, (href, title) in enumerate(items[:n]):
        snippet = snippets[i] if i < len(snippets) else ""
        url = _decode_ddg_url(href)
        if not url or "duckduckgo.com" in url: continue
        out.append({
            "title":   _strip_html(title)[:300],
            "url":     url,
            "snippet": _strip_html(snippet)[:700],
        })

    if not out and not items:
        raise RuntimeError("0 résultats parsables depuis lite — DDG a peut-être changé son HTML")
    return out


def _search_via_searxng(query, n):
    """Stratégie 3 — méta-recherche via instances SearXNG publiques.
    SearXNG agrège Google/Bing/DDG/etc., donc immunisé aux blocages DDG-spécifiques."""
    last_err = None
    for instance in SEARXNG_INSTANCES:
        try:
            # Essayer l'API JSON d'abord
            r = http.get(
                f"{instance}/search",
                params={
                    "q": query,
                    "format": "json",
                    "categories": "general",
                    "language": "fr",
                    "safesearch": "1",
                },
                headers={
                    "User-Agent": DDG_UA,
                    "Accept": "application/json,text/html;q=0.5",
                    "Accept-Language": "fr-FR,fr;q=0.9",
                },
                timeout=(10, 20))

            if r.status_code != 200:
                last_err = RuntimeError(f"{instance} : HTTP {r.status_code}")
                continue

            # Tenter le parsing JSON
            ct = (r.headers.get("content-type", "") or "").lower()
            if "json" in ct:
                try:
                    data = r.json()
                except Exception as e:
                    last_err = RuntimeError(f"{instance} : JSON invalide")
                    continue
                items = data.get("results", []) or []
                if not items:
                    last_err = RuntimeError(f"{instance} : 0 résultat JSON")
                    continue
                out = []
                for it in items[:n]:
                    url = it.get("url") or ""
                    if not url: continue
                    # Filtrer les liens internes searxng
                    if any(p in url for p in ("/search?", "searx.", "searxng.")): continue
                    out.append({
                        "title":   (it.get("title") or "")[:300],
                        "url":     url,
                        "snippet": (it.get("content") or it.get("snippet") or "")[:700],
                    })
                if out: return out
                last_err = RuntimeError(f"{instance} : 0 résultat utilisable")
            else:
                # JSON désactivé sur cette instance — passer à la suivante
                last_err = RuntimeError(f"{instance} : JSON désactivé (HTML reçu)")
                continue

        except http.exceptions.Timeout:
            last_err = RuntimeError(f"{instance} : timeout")
            continue
        except Exception as e:
            last_err = RuntimeError(f"{instance} : {type(e).__name__}: {str(e)[:80]}")
            continue

    raise last_err if last_err else RuntimeError(
        "Toutes les instances SearXNG sont injoignables. "
        "Patientez ou installez/maj `pip install --upgrade ddgs`"
    )


def _search_ddg(query, n=10):
    cache_key = (query, n)
    cached = _DDG_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _DDG_CACHE_TTL:
        return cached[1]

    _ddg_wait()

    strategies = []
    if HAS_DDGS_LIB:
        strategies.append(("lib", _search_via_lib))
    strategies.append(("lite", _search_via_lite))
    strategies.append(("searxng", _search_via_searxng))   # ← 3e fallback : méta-recherche

    last_err = None
    for name, fn in strategies:
        try:
            results = fn(query, n)
            if results:
                _DDG_CACHE[cache_key] = (time.time(), results)
                return results
            last_err = RuntimeError(f"Stratégie '{name}' : 0 résultat (même après retry)")
        except Exception as e:
            last_err = e
            continue

    raise last_err if last_err else RuntimeError("Toutes les stratégies DDG ont échoué")


# ════════════════════════════════════════════════════════
#  Fetching de pages complètes (option B)
# ════════════════════════════════════════════════════════

def _fetch_page(url):
    """Récupère et extrait le contenu textuel d'une page web.
    Retourne (content, error_or_None). content limité à PAGE_MAX_CHARS."""
    if not url or not url.startswith(("http://", "https://")):
        return None, "URL invalide"

    # Cache check
    cached = _PAGE_CACHE.get(url)
    if cached and (time.time() - cached[0]) < _PAGE_CACHE_TTL:
        return cached[1], None

    try:
        r = http.get(url,
            headers={
                "User-Agent": DDG_UA,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.5",
                "Accept-Language": "fr,en;q=0.7",
            },
            timeout=PAGE_FETCH_TIMEOUT,
            allow_redirects=True)
    except http.exceptions.Timeout:
        return None, "timeout"
    except http.exceptions.RequestException as e:
        return None, f"{type(e).__name__}"
    except Exception as e:
        return None, f"erreur: {type(e).__name__}"

    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"

    ct = (r.headers.get("Content-Type", "") or "").lower()
    if ct and ("html" not in ct and "xml" not in ct and "text" not in ct):
        return None, f"type non textuel"

    text = r.text or ""
    if not text:
        return None, "page vide"

    # 1) Essayer d'extraire le bloc principal : <article> ou <main>
    main_m = re.search(
        r"<(?:article|main)\b[^>]*>(.*?)</(?:article|main)>",
        text, re.DOTALL | re.IGNORECASE)
    if main_m:
        text = main_m.group(1)
    else:
        body_m = re.search(r"<body\b[^>]*>(.*?)</body>", text, re.DOTALL | re.IGNORECASE)
        if body_m: text = body_m.group(1)

    # 2) Retirer les blocs non-contenu
    for tag in ("script", "style", "nav", "footer", "header", "aside",
                "form", "iframe", "noscript", "svg", "button"):
        text = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", " ", text,
                      flags=re.DOTALL | re.IGNORECASE)

    # 3) Strip tous les tags restants
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) < 100:
        return None, "trop court après extraction"

    text = text[:PAGE_MAX_CHARS]
    _PAGE_CACHE[url] = (time.time(), text)
    return text, None


def _fetch_pages_parallel(results, n_fetch):
    """Lance en parallèle le fetch des `n_fetch` premiers résultats.
    Modifie results[i] en place avec full_content et fetch_status."""
    if not results: return results
    to_fetch = results[:n_fetch]
    with ThreadPoolExecutor(max_workers=PAGE_MAX_PARALLEL) as ex:
        futures = {ex.submit(_fetch_page, r["url"]): i for i, r in enumerate(to_fetch)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                content, err = future.result()
                if content:
                    to_fetch[i]["full_content"] = content
                    to_fetch[i]["fetch_status"] = "ok"
                    to_fetch[i]["fetch_chars"]  = len(content)
                else:
                    to_fetch[i]["fetch_status"] = err or "vide"
            except Exception as e:
                to_fetch[i]["fetch_status"] = f"erreur: {type(e).__name__}"
    # Marquer les résultats non récupérés
    for r in results[n_fetch:]:
        r.setdefault("fetch_status", "non récupéré")
    return results


def register(app, rd_cfg):

    @app.route("/api/ddg/ping", methods=["GET"])
    def ddg_ping():
        """Diagnostic détaillé : teste chaque backend de la lib + scraping lite."""
        t0 = time.time()
        test_query = request.args.get("q", "python programming language")
        out = {
            "lib_available": HAS_DDGS_LIB,
            "lib_flavor":    DDGS_FLAVOR,
            "test_query":    test_query,
            "strategies_tried": [],
        }
        if HAS_DDGS_LIB:
            # Tester chaque backend séparément pour identifier celui qui marche
            for backend in ("html", "lite", "api", None):
                try:
                    results = _lib_call(test_query, 3, backend=backend, region="wt-wt", safesearch="moderate")
                    label = f"lib (backend={backend!r})" if backend else "lib (backend défaut)"
                    out["strategies_tried"].append({
                        "name": label, "ok": True, "results": len(results),
                        "sample_title": (results[0].get("title")[:80] if results and results[0].get("title") else None)
                    })
                    time.sleep(0.4)
                except Exception as e:
                    label = f"lib (backend={backend!r})" if backend else "lib (backend défaut)"
                    out["strategies_tried"].append({"name": label, "ok": False, "error": str(e)[:200]})
        try:
            time.sleep(1)
            results = _search_via_lite(test_query, 3)
            out["strategies_tried"].append({
                "name": "lite (scraping)", "ok": True, "results": len(results),
                "sample_title": (results[0]["title"][:80] if results else None)
            })
        except Exception as e:
            out["strategies_tried"].append({"name": "lite (scraping)", "ok": False, "error": str(e)[:200]})
        try:
            time.sleep(1)
            results = _search_via_searxng(test_query, 3)
            out["strategies_tried"].append({
                "name": "searxng (meta)", "ok": True, "results": len(results),
                "sample_title": (results[0]["title"][:80] if results else None)
            })
        except Exception as e:
            out["strategies_tried"].append({"name": "searxng (meta)", "ok": False, "error": str(e)[:200]})
        out["elapsed_ms"] = int((time.time() - t0) * 1000)
        out["ok"] = any(s.get("ok") and s.get("results", 0) > 0 for s in out["strategies_tried"])
        return jsonify(out)

    @app.route("/api/ddg/search", methods=["GET"])
    def ddg_search():
        q = request.args.get("q", "").strip()
        n = int(request.args.get("n", 10))
        if not q: return jsonify({"error": "Requête vide"}), 400
        try:
            return jsonify({"query": q, "results": _search_ddg(q, n)})
        except Exception as e:
            return jsonify({"error": f"DDG : {str(e)[:400]}"}), 500

    @app.route("/api/ddg/agentic", methods=["POST"])
    def ddg_agentic():
        cfg = rd_cfg()
        if not cfg.get("api_key"): return jsonify({"error": "Clé API manquante"}), 400
        d = request.json
        content    = (d.get("content", "") or "")[:4000]
        fname      = d.get("name", "note")
        hint       = (d.get("hint", "") or "").strip()
        n_res      = int(d.get("n", 10))
        fetch_full = bool(d.get("fetch_full", True))  # ← option B activée par défaut
        n_fetch    = int(d.get("n_fetch", 5))         # ← top 5 par défaut

        prompt = (
            f'À partir du fichier "{fname}" ci-dessous, génère UNE requête web optimale '
            f'(5-12 mots-clés, français ou anglais selon ce qui maximisera les résultats). '
            f'Opérateurs autorisés : "site:", "filetype:", guillemets pour expressions exactes. '
            f'Réponds UNIQUEMENT avec la requête, sans guillemets autour ni explication.\n\n'
            f'=== DÉBUT DU FICHIER ===\n{content}\n=== FIN ==='
        )
        if hint: prompt += f"\n\nIndication de l'utilisateur (à prioriser) : {hint}"

        query, err = _ai_call(cfg, [
            {"role": "system", "content": "Tu génères des requêtes de moteur de recherche optimales. Pas d'explications."},
            {"role": "user",   "content": prompt}
        ], max_tokens=150, temp=0.3)
        if err: return jsonify({"error": f"IA (génération requête) : {err}"}), 500
        query = (query or "").strip().strip('"').strip("'").replace("\n", " ")[:200]
        if not query: return jsonify({"error": "L'IA n'a pas généré de requête"}), 500

        try:
            results = _search_ddg(query, n_res)
        except Exception as e:
            return jsonify({"error": f"DDG : {type(e).__name__}: {str(e)[:400]}", "query": query}), 500

        # Option B : fetch parallèle du contenu complet pour les top n_fetch
        fetched_count = 0
        if fetch_full and results:
            results = _fetch_pages_parallel(results, min(n_fetch, len(results)))
            fetched_count = sum(1 for r in results if r.get("fetch_status") == "ok")

        return jsonify({
            "query": query,
            "results": results,
            "count": len(results),
            "fetched_count": fetched_count,
            "fetch_full": fetch_full,
        })

    @app.route("/api/ddg/synthesize", methods=["POST"])
    def ddg_synthesize():
        cfg = rd_cfg()
        if not cfg.get("api_key"): return jsonify({"error": "Clé API manquante"}), 400
        d = request.json
        results        = d.get("results", [])
        content        = (d.get("content", "") or "")[:3000]
        fname          = d.get("name", "note")
        query          = d.get("query", "")
        question       = (d.get("question", "") or "").strip()
        user_sys       = (d.get("system_prompt", "") or "").strip()
        if not results: return jsonify({"error": "Aucun résultat à synthétiser"}), 400

        # Construire le bloc des résultats — full_content si dispo, sinon snippet
        res_parts = []
        for i, r in enumerate(results):
            full = r.get("full_content")
            if full:
                label = f"Contenu de la page ({len(full)} chars)"
                body  = full[:3500]   # limite par source pour respect budget tokens
            else:
                label = f"Snippet uniquement ({r.get('fetch_status','non récupéré')})"
                body  = r.get("snippet", "")[:600]
            res_parts.append(
                f"### [{i+1}] {r.get('title','(sans titre)')}\n"
                f"**URL** : {r.get('url','')}\n"
                f"**{label}** :\n{body}"
            )
        res_md = "\n\n".join(res_parts)

        # Construire le user_prompt selon présence d'une question
        if question:
            user_prompt = (
                f"=== OBJECTIF / QUESTION DE L'UTILISATEUR ===\n{question}\n=== FIN OBJECTIF ===\n\n"
                f"Réponds **précisément** à l'objectif/question ci-dessus en t'appuyant sur les sources "
                f"web fournies. N'imite pas un template — adapte ta structure à ce qui est demandé. "
                f"Cite chaque source qui contribue avec son numéro [1], [2], etc. "
                f"Si une source est incomplète (snippet uniquement), signale-le. "
                f"Si aucune source ne permet de répondre, dis-le explicitement plutôt que de combler par des généralités.\n\n"
                f"=== MON FICHIER DE TRAVAIL \"{fname}\" (contexte) ===\n{content}\n=== FIN ===\n\n"
                f"=== {len(results)} RÉSULTATS WEB (requête : `{query}`) ===\n{res_md}\n=== FIN ===\n\n"
                f"Termine par une section **## Sources** listant tous les liens cités."
            )
        else:
            # Template par défaut (comportement historique mais avec contenu réel)
            user_prompt = (
                f"Voici {len(results)} résultats web trouvés via DuckDuckGo avec la requête `{query}`, "
                f"en lien avec mon fichier \"{fname}\".\n\n"
                f"Produis une synthèse en français (Markdown structuré) qui :\n"
                f"1. Identifie les **points-clés** qui ressortent du corpus web\n"
                f"2. Souligne **convergences et contradictions** entre les sources\n"
                f"3. Évalue brièvement la **qualité épistémique** (sources primaires vs secondaires, opinions vs faits)\n"
                f"4. Identifie les **liens avec mon fichier d'origine**\n"
                f"5. Propose 2-3 **angles à creuser** ensuite\n\n"
                f"Cite chaque source avec son numéro [1], [2], etc.\n"
                f"Termine par une section **## Sources** listant tous les liens cités avec leur titre.\n\n"
                f"=== MON FICHIER \"{fname}\" ===\n{content}\n=== FIN ===\n\n"
                f"=== RÉSULTATS WEB ===\n{res_md}\n=== FIN ==="
            )

        default_sys = ("Tu es analyste expert en synthèse de sources web. "
                       "Tu écris en français, en Markdown structuré, avec rigueur épistémique. "
                       "Tu ne paraphrases pas en généralités quand la matière manque — tu le dis.")
        system = user_sys or default_sys

        synthesis, err = _ai_call(cfg, [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_prompt}
        ], max_tokens=2500, temp=0.5, timeout=240)
        if err: return jsonify({"error": f"IA (synthèse) : {err}"}), 500
        return jsonify({
            "synthesis": synthesis,
            "used_full_content": sum(1 for r in results if r.get("full_content")),
            "used_snippet_only": sum(1 for r in results if not r.get("full_content")),
        })

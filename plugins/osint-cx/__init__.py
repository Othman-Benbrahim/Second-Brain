"""
Plugin OSINT Cross-Reference — compatible Second Brain.

Structure alignée sur le plugin DuckDuckGo :
  - manifest.json déclare le bouton toolbar
  - ui.html / ui.css / ui.js sont injectés par le loader
  - ce fichier expose register(app, rd_cfg) et déclare toutes les routes Flask

Routes :
  GET /api/osintcx/ping
  GET /api/osintcx/username?q=<username>
  GET /api/osintcx/email?q=<email>
  GET /api/osintcx/phone?q=<phone>
  GET /api/osintcx/ip?q=<ip>
  GET /api/osintcx/domain?q=<domain>
  GET /api/osintcx/crossref?q=<term>&type=<auto|username|email|phone|ip|domain>
  GET /api/osintcx/brixhub?q=<term>&type=<auto|username|email|phone|ip|domain>

BrixHub est optionnel et se configure côté serveur avec des variables d'environnement :
  BRIXHUB_API_KEY       clé API BrixHub, obligatoire pour interroger le service
  BRIXHUB_BASE_URL      domaine API, défaut https://brixhub.net
  BRIXHUB_SEARCH_PATH   chemin de recherche, défaut /api/v1/search
  BRIXHUB_DOCS_PATH     chemin OpenAPI, défaut /api/v1/docs
  BRIXHUB_AUTH_HEADER   nom du header d'auth, défaut X-API-Key
  BRIXHUB_AUTH_SCHEME   préfixe éventuel, vide par défaut
  BRIXHUB_USER_AGENT    User-Agent obligatoire envoyé à BrixHub
"""

import hashlib
import os
import ipaddress
import re
import socket
import time
from threading import Lock
from urllib.parse import urlparse, urljoin

from flask import request, jsonify
import requests as http

TIMEOUT = (7, 12)
UA = "SecondBrain-OSINT-CX/1.4 (+local plugin)"

# BrixHub v1 — d'après la documentation fournie :
#   base API : /api/v1
#   auth : header X-API-Key: brix_...
#   User-Agent obligatoire
#   JSON uniquement
BRIXHUB_BASE_URL = os.getenv("BRIXHUB_BASE_URL", "https://brixhub.net").rstrip("/")
BRIXHUB_FALLBACK_BASE_URL = os.getenv("BRIXHUB_FALLBACK_BASE_URL", "https://brixhub.site").rstrip("/")
BRIXHUB_SEARCH_PATH = os.getenv("BRIXHUB_SEARCH_PATH", "/api/v1/search")
BRIXHUB_DOCS_PATH = os.getenv("BRIXHUB_DOCS_PATH", "/api/v1/docs")
BRIXHUB_API_KEY = os.getenv("BRIXHUB_API_KEY", "").strip()
BRIXHUB_AUTH_HEADER = os.getenv("BRIXHUB_AUTH_HEADER", "X-API-Key").strip() or "X-API-Key"
BRIXHUB_AUTH_SCHEME = os.getenv("BRIXHUB_AUTH_SCHEME", "").strip()
BRIXHUB_USER_AGENT = os.getenv("BRIXHUB_USER_AGENT", UA).strip() or UA
BRIXHUB_ENABLED = bool(BRIXHUB_API_KEY)
_CACHE = {}
_CACHE_TTL = 300
_CACHE_LOCK = Lock()

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE_RE = re.compile(r"^\+?[0-9\s\-().]{7,20}$")
_DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?)+$",
    re.I,
)
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{2,64}$")

USERNAME_PLATFORMS = [
    {"platform": "GitHub",        "url": "https://github.com/{username}",              "icon": "📁"},
    {"platform": "GitLab",        "url": "https://gitlab.com/{username}",              "icon": "🦊"},
    {"platform": "Reddit",        "url": "https://www.reddit.com/user/{username}",     "icon": "🤖"},
    {"platform": "X / Twitter",   "url": "https://x.com/{username}",                   "icon": "🐦"},
    {"platform": "Instagram",     "url": "https://www.instagram.com/{username}/",      "icon": "📷"},
    {"platform": "TikTok",        "url": "https://www.tiktok.com/@{username}",         "icon": "🎵"},
    {"platform": "YouTube",       "url": "https://www.youtube.com/@{username}",        "icon": "📺"},
    {"platform": "Telegram",      "url": "https://t.me/{username}",                    "icon": "✉️"},
    {"platform": "Twitch",        "url": "https://www.twitch.tv/{username}",           "icon": "🎮"},
    {"platform": "Medium",        "url": "https://medium.com/@{username}",             "icon": "✍️"},
    {"platform": "DEV",           "url": "https://dev.to/{username}",                  "icon": "💻"},
    {"platform": "Keybase",       "url": "https://keybase.io/{username}",              "icon": "🔑"},
    {"platform": "SoundCloud",    "url": "https://soundcloud.com/{username}",          "icon": "🎧"},
    {"platform": "Pinterest",     "url": "https://www.pinterest.com/{username}/",      "icon": "📌"},
    {"platform": "Steam",         "url": "https://steamcommunity.com/id/{username}",   "icon": "🎮"},
    {"platform": "Pastebin",      "url": "https://pastebin.com/u/{username}",          "icon": "📋"},
]

COUNTRY_CODES = {
    "+1": "États-Unis / Canada", "+7": "Russie / Kazakhstan", "+31": "Pays-Bas",
    "+32": "Belgique", "+33": "France", "+34": "Espagne", "+39": "Italie",
    "+41": "Suisse", "+43": "Autriche", "+44": "Royaume-Uni", "+45": "Danemark",
    "+46": "Suède", "+47": "Norvège", "+48": "Pologne", "+49": "Allemagne",
    "+52": "Mexique", "+55": "Brésil", "+61": "Australie", "+81": "Japon",
    "+82": "Corée du Sud", "+86": "Chine", "+91": "Inde", "+212": "Maroc",
    "+213": "Algérie", "+216": "Tunisie", "+351": "Portugal", "+971": "Émirats arabes unis",
}


def _cached(key):
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry and time.time() - entry[0] < _CACHE_TTL:
            return entry[1]
    return None


def _set_cache(key, value):
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)
    return value


def _safe_get(url, *, params=None, timeout=TIMEOUT):
    try:
        return http.get(url, params=params, headers={"User-Agent": UA}, timeout=timeout, allow_redirects=True)
    except http.exceptions.RequestException as exc:
        return exc


def _normalize_domain(value):
    value = (value or "").strip().lower()
    if "://" in value:
        value = urlparse(value).netloc or value
    value = value.split("/")[0].split(":")[0].strip(".")
    if value.startswith("www."):
        value = value[4:]
    return value


def detect_type(q):
    q = (q or "").strip()
    if _EMAIL_RE.match(q):
        return "email"
    try:
        ipaddress.ip_address(q)
        return "ip"
    except ValueError:
        pass
    dom = _normalize_domain(q)
    if _DOMAIN_RE.match(dom) and "." in dom and not q.startswith("+"):
        return "domain"
    if _PHONE_RE.match(q) and any(ch.isdigit() for ch in q):
        digits = re.sub(r"\D", "", q)
        if len(digits) >= 7:
            return "phone"
    return "username"


def search_username(username):
    username = (username or "").strip().lstrip("@")
    cached = _cached(("username", username))
    if cached is not None:
        return cached
    if not _USERNAME_RE.match(username):
        return {"ok": False, "type": "username", "error": "Pseudonyme invalide ou trop court."}

    found, checked = [], []
    for platform in USERNAME_PLATFORMS:
        url = platform["url"].replace("{username}", username)
        r = _safe_get(url, timeout=(4, 7))
        item = {**platform, "url": url, "status": "unknown"}
        if isinstance(r, Exception):
            item.update({"status": "error", "error": type(r).__name__})
        else:
            item["status_code"] = r.status_code
            if 200 <= r.status_code < 400:
                item["status"] = "found"
                found.append(item.copy())
            elif r.status_code in (401, 403, 429):
                item["status"] = "blocked"
            else:
                item["status"] = "not_found"
        checked.append(item)
        time.sleep(0.05)

    return _set_cache(("username", username), {
        "ok": True,
        "type": "username",
        "query": username,
        "count": len(found),
        "results": found,
        "checked": checked,
        "note": "Certains sites renvoient de faux positifs ou bloquent les requêtes automatisées : vérifiez les liens importants manuellement.",
    })


def search_email(email):
    email = (email or "").strip().lower()
    cached = _cached(("email", email))
    if cached is not None:
        return cached

    out = {"ok": True, "type": "email", "query": email, "valid_format": bool(_EMAIL_RE.match(email)), "gravatar": None, "domain": None, "notes": []}
    if not out["valid_format"]:
        out["ok"] = False
        out["error"] = "Format d'email invalide."
        return out

    domain = email.split("@", 1)[1]
    out["domain"] = search_domain(domain, lightweight=True)

    email_hash = hashlib.md5(email.encode("utf-8")).hexdigest()
    avatar_url = f"https://www.gravatar.com/avatar/{email_hash}?d=404&s=200"
    r = _safe_get(avatar_url, timeout=(4, 7))
    if not isinstance(r, Exception) and r.status_code == 200:
        profile_url = f"https://www.gravatar.com/{email_hash}.json"
        profile = None
        pr = _safe_get(profile_url, timeout=(4, 7))
        if not isinstance(pr, Exception) and pr.status_code == 200:
            try:
                profile = pr.json()
            except Exception:
                profile = None
        out["gravatar"] = {
            "avatar_url": f"https://www.gravatar.com/avatar/{email_hash}?s=200",
            "profile_url": f"https://gravatar.com/{email_hash}",
            "profile": profile,
        }
    else:
        out["notes"].append("Aucun Gravatar public détecté.")

    out["notes"].append("Le plugin ne teste pas les bases de fuites privées et n'envoie pas l'adresse email à un service de breach lookup.")
    return _set_cache(("email", email), out)


def search_phone(phone):
    phone = (phone or "").strip()
    cached = _cached(("phone", phone))
    if cached is not None:
        return cached
    cleaned = re.sub(r"[\s\-().]", "", phone)
    countries = []
    for code in sorted(COUNTRY_CODES, key=len, reverse=True):
        if cleaned.startswith(code):
            countries.append({"code": code, "country": COUNTRY_CODES[code]})
            break
    notes = []
    if cleaned.startswith("+") and 8 <= len(cleaned) <= 16:
        notes.append("Format proche E.164 : indicatif international + numéro.")
    elif cleaned.startswith("0"):
        notes.append("Format probablement national : ajoutez l'indicatif pays pour une meilleure analyse.")
    return _set_cache(("phone", phone), {
        "ok": True,
        "type": "phone",
        "query": phone,
        "cleaned": cleaned,
        "valid_format": bool(_PHONE_RE.match(phone)),
        "country_codes": countries,
        "notes": notes,
    })


def search_ip(ip):
    ip = (ip or "").strip()
    cached = _cached(("ip", ip))
    if cached is not None:
        return cached
    try:
        ip_obj = ipaddress.ip_address(ip)
    except ValueError:
        return {"ok": False, "type": "ip", "error": "Adresse IP invalide."}

    out = {
        "ok": True,
        "type": "ip",
        "query": ip,
        "version": ip_obj.version,
        "is_private": ip_obj.is_private,
        "is_global": ip_obj.is_global,
        "geo": None,
        "notes": [],
    }
    if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved:
        out["notes"].append("IP non publique : pas de géolocalisation externe pertinente.")
        return _set_cache(("ip", ip), out)

    r = _safe_get(f"https://ipapi.co/{ip}/json/", timeout=(5, 9))
    if not isinstance(r, Exception) and r.status_code == 200:
        try:
            data = r.json()
            out["geo"] = {
                "ip": data.get("ip"), "city": data.get("city"), "region": data.get("region"),
                "country": data.get("country_name"), "asn": data.get("asn"), "org": data.get("org"),
                "timezone": data.get("timezone"), "latitude": data.get("latitude"), "longitude": data.get("longitude"),
            }
        except Exception:
            out["notes"].append("Réponse ipapi.co non lisible.")
    else:
        out["notes"].append("Géolocalisation ipapi.co indisponible ou bloquée.")
    return _set_cache(("ip", ip), out)


def search_domain(domain, lightweight=False):
    domain = _normalize_domain(domain)
    cached = _cached(("domain", domain, lightweight))
    if cached is not None:
        return cached
    if not _DOMAIN_RE.match(domain):
        return {"ok": False, "type": "domain", "error": "Domaine invalide."}

    ips = []
    try:
        infos = socket.getaddrinfo(domain, None, type=socket.SOCK_STREAM)
        ips = sorted({info[4][0] for info in infos})[:8]
    except Exception:
        pass

    out = {"ok": True, "type": "domain", "query": domain, "ips": ips, "rdap": None, "notes": []}
    if not ips:
        out["notes"].append("Aucun enregistrement A/AAAA résolu via DNS local.")

    if not lightweight:
        r = _safe_get(f"https://rdap.org/domain/{domain}", timeout=(5, 10))
        if not isinstance(r, Exception) and r.status_code == 200:
            try:
                data = r.json()
                out["rdap"] = {
                    "handle": data.get("handle"),
                    "ldhName": data.get("ldhName"),
                    "status": data.get("status", [])[:6] if isinstance(data.get("status"), list) else data.get("status"),
                    "events": data.get("events", [])[:6] if isinstance(data.get("events"), list) else [],
                    "links": data.get("links", [])[:4] if isinstance(data.get("links"), list) else [],
                }
            except Exception:
                out["notes"].append("Réponse RDAP non lisible.")
        else:
            out["notes"].append("RDAP indisponible pour ce domaine.")

    return _set_cache(("domain", domain, lightweight), out)


def _brixhub_headers(content_type=False):
    headers = {
        "User-Agent": BRIXHUB_USER_AGENT,
        "Accept": "application/json",
    }
    if content_type:
        headers["Content-Type"] = "application/json"
    if BRIXHUB_API_KEY:
        value = f"{BRIXHUB_AUTH_SCHEME} {BRIXHUB_API_KEY}".strip() if BRIXHUB_AUTH_SCHEME else BRIXHUB_API_KEY
        headers[BRIXHUB_AUTH_HEADER] = value
    return headers


def _brixhub_url(base, path):
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def brixhub_openapi_spec():
    """Récupère la spec OpenAPI brute si BrixHub la sert en JSON."""
    cache_key = ("brixhub_spec", BRIXHUB_BASE_URL, BRIXHUB_DOCS_PATH)
    cached = _cached(cache_key)
    if cached is not None:
        return cached
    if not BRIXHUB_API_KEY:
        return {"ok": False, "enabled": False, "error": "BRIXHUB_API_KEY manquante."}

    url = _brixhub_url(BRIXHUB_BASE_URL, BRIXHUB_DOCS_PATH)
    try:
        r = http.get(url, headers=_brixhub_headers(), timeout=TIMEOUT)
        if 200 <= r.status_code < 300:
            try:
                payload = r.json()
            except Exception:
                payload = {"raw": r.text[:4000]}
            return _set_cache(cache_key, {"ok": True, "endpoint": url, "spec": _compact_brixhub_payload(payload)})
        return _set_cache(cache_key, {"ok": False, "error": "Spec OpenAPI BrixHub indisponible.", "attempts": [{"url": url, "status_code": r.status_code}]})
    except http.exceptions.RequestException as exc:
        return _set_cache(cache_key, {"ok": False, "error": "Spec OpenAPI BrixHub indisponible.", "attempts": [{"url": url, "error": type(exc).__name__}]})


def _compact_brixhub_payload(payload):
    """Renvoie un aperçu borné pour éviter d'injecter des réponses énormes dans l'UI."""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            payload = {**payload, "data": {**data, "results": data.get("results", [])[:20]}}
        else:
            for key in ("results", "items", "documents", "hits"):
                val = payload.get(key)
                if isinstance(val, list):
                    payload = {**payload, key: val[:20]}
                    break
        return payload
    if isinstance(payload, list):
        return payload[:20]
    return payload


BRIXHUB_ALLOWED_FIELDS = {
    # Identité
    "nom_famille", "prenom", "nom_naissance", "nom_affichage", "nom_utilisateur",
    "date_naissance", "annee_naissance", "jour_naissance", "mois_naissance", "genre", "civilite",
    # Contact
    "email", "telephone", "mobile", "adresse_ip",
    # Adresse
    "adresse", "complement_adresse", "code_postal", "ville", "ville_naissance",
    "lieu_naissance", "pays", "region", "departement",
    # Identifiants uniques
    "nir", "iban", "bic", "siret", "siren",
    # Véhicule
    "vin_plaque", "immatriculation", "numero_serie", "marque", "modele",
    # Professionnel
    "societe", "profession", "fonction",
    # Gaming / FiveM
    "steam_id", "fivem_license", "fivem_license2", "fivem_id", "xbox_live_id", "live_id", "discord_id",
    # Options
    "page", "per_page", "flexible",
}


def _clean_brixhub_criteria(criteria):
    """Garde uniquement les champs documentés pour POST /api/v1/search."""
    if not isinstance(criteria, dict):
        return {}
    cleaned = {}
    for key, value in criteria.items():
        if key not in BRIXHUB_ALLOWED_FIELDS:
            continue
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                continue
        if key in ("page", "per_page", "jour_naissance", "mois_naissance"):
            try:
                value = int(value)
            except Exception:
                continue
        if key == "flexible":
            if isinstance(value, str):
                value = value.lower() in ("1", "true", "yes", "on", "oui")
            else:
                value = bool(value)
        cleaned[key] = value
    return cleaned


def _criteria_from_query(q, typ="auto"):
    """Compatibilité avec la recherche simple du plugin : mapping uniquement vers des champs documentés."""
    q = (q or "").strip()
    typ = (typ or "auto").strip().lower()
    if typ == "auto":
        typ = detect_type(q)
    if typ == "email":
        return {"email": q}
    if typ == "phone":
        return {"telephone": q}
    if typ == "ip":
        return {"adresse_ip": q}
    if typ == "username":
        return {"nom_utilisateur": q.lstrip("@")}
    return {}


def search_brixhub_payload(criteria):
    """Appelle strictement l'endpoint documenté : POST /api/v1/search en JSON."""
    criteria = _clean_brixhub_criteria(criteria)
    if not criteria:
        return {"ok": False, "type": "brixhub", "error": "Aucun critère BrixHub documenté n'a été fourni."}
    if not BRIXHUB_API_KEY:
        return {
            "ok": False,
            "type": "brixhub",
            "enabled": False,
            "error": "BrixHub non configuré : définissez BRIXHUB_API_KEY côté serveur.",
            "config_help": {
                "BRIXHUB_API_KEY": "clé API fournie par BrixHub",
                "BRIXHUB_BASE_URL": BRIXHUB_BASE_URL,
                "BRIXHUB_SEARCH_PATH": BRIXHUB_SEARCH_PATH,
                "BRIXHUB_AUTH_HEADER": BRIXHUB_AUTH_HEADER,
                "BRIXHUB_USER_AGENT": BRIXHUB_USER_AGENT,
            },
        }

    url = _brixhub_url(BRIXHUB_BASE_URL, BRIXHUB_SEARCH_PATH)
    cache_key = ("brixhub_post", url, tuple(sorted(criteria.items())))
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    try:
        r = http.post(url, json=criteria, headers=_brixhub_headers(content_type=True), timeout=TIMEOUT)
    except http.exceptions.RequestException as exc:
        return _set_cache(cache_key, {
            "ok": False,
            "type": "brixhub",
            "enabled": True,
            "method": "POST",
            "endpoint": url,
            "criteria_sent": criteria,
            "error": f"Erreur réseau BrixHub : {type(exc).__name__}",
        })

    attempt = {"method": "POST", "url": url, "json_keys": list(criteria.keys()), "status_code": r.status_code}
    if r.status_code in (401, 403):
        return _set_cache(cache_key, {
            "ok": False, "type": "brixhub", "enabled": True,
            "method": "POST", "endpoint": url, "criteria_sent": criteria,
            "error": "Accès BrixHub refusé : vérifiez BRIXHUB_API_KEY et le header X-API-Key.",
            "attempts": [attempt],
        })
    if r.status_code == 429:
        return _set_cache(cache_key, {
            "ok": False, "type": "brixhub", "enabled": True,
            "method": "POST", "endpoint": url, "criteria_sent": criteria,
            "error": "Limite de requêtes BrixHub atteinte.", "attempts": [attempt],
        })
    if not (200 <= r.status_code < 300):
        preview = r.text[:1200] if getattr(r, "text", None) else ""
        return _set_cache(cache_key, {
            "ok": False,
            "type": "brixhub",
            "enabled": True,
            "method": "POST",
            "endpoint": url,
            "criteria_sent": criteria,
            "error": f"BrixHub a répondu avec le code HTTP {r.status_code}.",
            "response_preview": preview,
            "attempts": [attempt],
        })

    try:
        payload_out = r.json()
    except Exception:
        payload_out = {"raw": r.text[:4000]}

    meta = payload_out.get("meta") if isinstance(payload_out, dict) else None
    return _set_cache(cache_key, {
        "ok": True,
        "type": "brixhub",
        "enabled": True,
        "method": "POST",
        "endpoint": url,
        "criteria_sent": criteria,
        "results": _compact_brixhub_payload(payload_out),
        "meta": meta,
        "rate_limits": {k: v for k, v in r.headers.items() if k.lower().startswith("x-ratelimit")},
        "notice": "Endpoint utilisé strictement selon la documentation fournie : POST /api/v1/search avec JSON et header X-API-Key.",
    })


def search_brixhub(q, typ="auto"):
    """Wrapper pour la recherche simple depuis la barre OSINT."""
    q = (q or "").strip()
    if not q:
        return {"ok": False, "type": "brixhub", "error": "Requête vide."}
    criteria = _criteria_from_query(q, typ)
    if not criteria:
        return {"ok": False, "type": "brixhub", "query": q, "error": "Ce type n'est pas mappé à un champ BrixHub documenté."}
    return search_brixhub_payload(criteria)

def crossref(q, typ="auto"):
    q = (q or "").strip()
    typ = (typ or "auto").strip().lower()
    if not q:
        return {"ok": False, "error": "Requête vide."}
    if typ == "auto":
        typ = detect_type(q)
    if typ == "username":
        res = search_username(q)
    elif typ == "email":
        res = search_email(q)
    elif typ == "phone":
        res = search_phone(q)
    elif typ == "ip":
        res = search_ip(q)
    elif typ == "domain":
        res = search_domain(q)
    else:
        return {"ok": False, "error": f"Type inconnu : {typ}"}

    data = {"ok": res.get("ok", True), "query": q, "type": typ, "results": res}
    if request.args.get("brixhub", "0").lower() in ("1", "true", "yes", "on"):
        data["brixhub"] = search_brixhub(q, typ)
    return data


def register(app, rd_cfg):
    """Point d'entrée appelé automatiquement par le plugin loader Second Brain."""

    @app.route("/api/osintcx/ping", methods=["GET"])
    def osintcx_ping():
        return jsonify({"ok": True, "plugin": "osint-cx", "version": "1.4", "routes_registered": True})

    @app.route("/api/osintcx/username", methods=["GET"])
    def osintcx_username():
        return jsonify(search_username(request.args.get("q", "")))

    @app.route("/api/osintcx/email", methods=["GET"])
    def osintcx_email():
        return jsonify(search_email(request.args.get("q", "")))

    @app.route("/api/osintcx/phone", methods=["GET"])
    def osintcx_phone():
        return jsonify(search_phone(request.args.get("q", "")))

    @app.route("/api/osintcx/ip", methods=["GET"])
    def osintcx_ip():
        return jsonify(search_ip(request.args.get("q", "")))

    @app.route("/api/osintcx/domain", methods=["GET"])
    def osintcx_domain():
        return jsonify(search_domain(request.args.get("q", "")))

    @app.route("/api/osintcx/brixhub", methods=["GET", "POST"])
    def osintcx_brixhub():
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            data = search_brixhub_payload(payload)
        else:
            data = search_brixhub(request.args.get("q", ""), request.args.get("type", "auto"))
        status = 200 if data.get("ok", True) else 400
        return jsonify(data), status

    @app.route("/api/osintcx/brixhub/spec", methods=["GET"])
    def osintcx_brixhub_spec():
        data = brixhub_openapi_spec()
        status = 200 if data.get("ok", True) else 400
        return jsonify(data), status

    @app.route("/api/osintcx/crossref", methods=["GET"])
    def osintcx_crossref():
        data = crossref(request.args.get("q", ""), request.args.get("type", "auto"))
        status = 200 if data.get("ok", True) else 400
        return jsonify(data), status

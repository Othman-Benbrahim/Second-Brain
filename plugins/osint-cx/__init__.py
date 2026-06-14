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
import json
import shutil
import subprocess
import tempfile
import sys
import sysconfig
import importlib.util
from threading import Lock
from urllib.parse import urlparse, urljoin

from flask import request, jsonify
import requests as http

TIMEOUT = (7, 12)
UA = "SecondBrain-OSINT-CX/1.8 (+local plugin)"
GITHUB_API_URL = "https://api.github.com/users"
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
ENTREPRISE_API_URL = "https://recherche-entreprises.api.gouv.fr/search"

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

# X API v2 — lecture de profil public par username.
# Configurez uniquement côté serveur dans .env :
#   X_BEARER_TOKEN=...
# Optionnel :
#   X_BASE_URL=https://api.x.com
X_BASE_URL = os.getenv("X_BASE_URL", "https://api.x.com").rstrip("/")
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "").strip()
X_ENABLED = bool(X_BEARER_TOKEN)

# LinkedIn via RapidAPI — optionnel, désactivé si RAPIDAPI_KEY ou le host n'est pas configuré.
# Le plugin reste générique car chaque API RapidAPI a ses propres chemins/paramètres.
# Configuration minimale dans .env :
#   RAPIDAPI_KEY=...
#   LINKEDIN_RAPIDAPI_HOST=exemple.p.rapidapi.com
#   LINKEDIN_RAPIDAPI_ENDPOINT=/profile
# Optionnel :
#   LINKEDIN_RAPIDAPI_METHOD=GET ou POST
#   LINKEDIN_RAPIDAPI_PARAM=url
#   LINKEDIN_RAPIDAPI_BASE_URL=https://<host>
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "").strip()
LINKEDIN_RAPIDAPI_HOST = os.getenv("LINKEDIN_RAPIDAPI_HOST", "").strip()
LINKEDIN_RAPIDAPI_ENDPOINT = os.getenv("LINKEDIN_RAPIDAPI_ENDPOINT", "").strip()
LINKEDIN_RAPIDAPI_METHOD = os.getenv("LINKEDIN_RAPIDAPI_METHOD", "GET").strip().upper()
LINKEDIN_RAPIDAPI_PARAM = os.getenv("LINKEDIN_RAPIDAPI_PARAM", "url").strip() or "url"
LINKEDIN_RAPIDAPI_BASE_URL = os.getenv("LINKEDIN_RAPIDAPI_BASE_URL", "").strip().rstrip("/")
LINKEDIN_ENABLED = bool(RAPIDAPI_KEY and LINKEDIN_RAPIDAPI_HOST and LINKEDIN_RAPIDAPI_ENDPOINT)
X_USER_FIELDS = os.getenv(
    "X_USER_FIELDS",
    "created_at,description,location,public_metrics,verified,verified_type,profile_image_url,url"
).strip()
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




def _safe_post(url, *, json=None, params=None, headers=None, timeout=TIMEOUT):
    try:
        merged = {"User-Agent": UA, "Accept": "application/json"}
        if headers:
            merged.update(headers)
        return http.post(url, params=params, json=json, headers=merged, timeout=timeout)
    except http.exceptions.RequestException as exc:
        return exc


def _linkedin_url_from_query(q):
    q = (q or "").strip()
    if not q:
        return ""
    if q.startswith("http://") or q.startswith("https://"):
        return q
    username = q.strip().lstrip("@/")
    username = username.replace("https://www.linkedin.com/in/", "").replace("https://linkedin.com/in/", "")
    username = username.strip("/")
    return f"https://www.linkedin.com/in/{username}/"


def _deep_find_first(obj, keys):
    """Recherche prudente d'un champ dans une réponse JSON variable RapidAPI."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        lower = {str(k).lower(): v for k, v in obj.items()}
        for key in keys:
            if key.lower() in lower and lower[key.lower()] not in (None, ""):
                return lower[key.lower()]
        for v in obj.values():
            found = _deep_find_first(v, keys)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for item in obj[:10]:
            found = _deep_find_first(item, keys)
            if found not in (None, ""):
                return found
    return None


def _compact_linkedin_payload(payload):
    if isinstance(payload, dict):
        profile = {
            "full_name": _deep_find_first(payload, ["full_name", "fullname", "name", "nom", "display_name"]),
            "first_name": _deep_find_first(payload, ["first_name", "firstname", "prenom"]),
            "last_name": _deep_find_first(payload, ["last_name", "lastname", "nom_famille"]),
            "headline": _deep_find_first(payload, ["headline", "title", "occupation", "poste", "fonction"]),
            "location": _deep_find_first(payload, ["location", "geo", "city", "ville", "localisation"]),
            "company": _deep_find_first(payload, ["company", "current_company", "organization", "organisation", "societe"]),
            "profile_url": _deep_find_first(payload, ["profile_url", "linkedin_url", "url", "public_profile_url"]),
            "avatar_url": _deep_find_first(payload, ["profile_pic_url", "profile_picture", "avatar", "avatar_url", "image"]),
            "summary": _compact_text(_deep_find_first(payload, ["summary", "about", "description", "bio"]), 700),
        }
        # Nettoyage des objets/lists mal mappés.
        profile = {k: (_compact_text(v, 260) if not isinstance(v, (dict, list)) else "") for k, v in profile.items() if v not in (None, "", [], {})}
        return profile
    return {}


def search_linkedin_rapidapi(q, profile_url=None):
    """Lecture optionnelle d'un profil LinkedIn via une API RapidAPI configurée.

    Le plugin n'impose aucun fournisseur RapidAPI : il envoie l'URL LinkedIn au host et
    endpoint configurés dans .env, puis extrait seulement les champs utiles au score.
    """
    q = (q or "").strip()
    profile_url = (profile_url or "").strip() or _linkedin_url_from_query(q)
    if not q and not profile_url:
        return {"ok": False, "type": "linkedin", "error": "Pseudo ou URL LinkedIn manquant."}
    if not LINKEDIN_ENABLED:
        missing = []
        if not RAPIDAPI_KEY: missing.append("RAPIDAPI_KEY")
        if not LINKEDIN_RAPIDAPI_HOST: missing.append("LINKEDIN_RAPIDAPI_HOST")
        if not LINKEDIN_RAPIDAPI_ENDPOINT: missing.append("LINKEDIN_RAPIDAPI_ENDPOINT")
        return {"ok": False, "type": "linkedin", "query": q, "profile_url": profile_url, "error": "Configuration LinkedIn/RapidAPI incomplète : " + ", ".join(missing)}

    base = LINKEDIN_RAPIDAPI_BASE_URL or f"https://{LINKEDIN_RAPIDAPI_HOST}"
    endpoint = LINKEDIN_RAPIDAPI_ENDPOINT if LINKEDIN_RAPIDAPI_ENDPOINT.startswith("/") else "/" + LINKEDIN_RAPIDAPI_ENDPOINT
    url = base.rstrip("/") + endpoint
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": LINKEDIN_RAPIDAPI_HOST,
        "User-Agent": UA,
        "Accept": "application/json",
    }
    cache_key = ("linkedin_rapidapi", LINKEDIN_RAPIDAPI_HOST, LINKEDIN_RAPIDAPI_ENDPOINT, LINKEDIN_RAPIDAPI_METHOD, profile_url.lower())
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    payload = {LINKEDIN_RAPIDAPI_PARAM: profile_url, "url": profile_url, "username": q.strip().lstrip("@")}
    if LINKEDIN_RAPIDAPI_METHOD == "POST":
        headers["Content-Type"] = "application/json"
        r = _safe_post(url, json=payload, headers=headers, timeout=(8, 20))
    else:
        try:
            r = http.get(url, params={LINKEDIN_RAPIDAPI_PARAM: profile_url}, headers=headers, timeout=(8, 20))
        except http.exceptions.RequestException as exc:
            r = exc

    if isinstance(r, Exception):
        return _set_cache(cache_key, {"ok": False, "type": "linkedin", "query": q, "profile_url": profile_url, "error": f"LinkedIn RapidAPI indisponible : {type(r).__name__}"})
    status = getattr(r, "status_code", 0)
    if not (200 <= status < 300):
        preview = ""
        try:
            preview = _compact_text(r.text, 700)
        except Exception:
            pass
        return _set_cache(cache_key, {"ok": False, "type": "linkedin", "query": q, "profile_url": profile_url, "endpoint": url, "status_code": status, "error": f"LinkedIn RapidAPI HTTP {status}", "response_preview": preview})
    try:
        raw = r.json()
    except Exception:
        raw = {}
    profile = _compact_linkedin_payload(raw)
    if profile_url and not profile.get("profile_url"):
        profile["profile_url"] = profile_url
    return _set_cache(cache_key, {
        "ok": True,
        "type": "linkedin",
        "query": q,
        "found": bool(profile),
        "endpoint": url,
        "method": LINKEDIN_RAPIDAPI_METHOD,
        "profile_url": profile_url,
        "profile": profile,
        "notice": "Données structurées extraites via RapidAPI. Elles servent uniquement d'indices pour le score de corrélation.",
    })

def search_github_profile(username):
    """Lecture publique d'un profil GitHub. Aucune clé API requise.

    Usage prévu : audit défensif d'un compte/pseudo fourni volontairement.
    """
    username = (username or "").strip().lstrip("@")
    if not _USERNAME_RE.match(username):
        return {"ok": False, "type": "github", "error": "Nom GitHub invalide."}
    cache_key = ("github", username.lower())
    cached = _cached(cache_key)
    if cached is not None:
        return cached
    url = f"{GITHUB_API_URL}/{username}"
    r = _safe_get(url, timeout=(5, 10))
    if isinstance(r, Exception):
        return _set_cache(cache_key, {"ok": False, "type": "github", "query": username, "error": f"GitHub indisponible : {type(r).__name__}"})
    if r.status_code == 404:
        return _set_cache(cache_key, {"ok": True, "type": "github", "query": username, "found": False, "endpoint": url})
    if r.status_code in (403, 429):
        return _set_cache(cache_key, {"ok": False, "type": "github", "query": username, "status_code": r.status_code, "error": "GitHub bloque ou limite temporairement les requêtes."})
    if not (200 <= r.status_code < 300):
        return _set_cache(cache_key, {"ok": False, "type": "github", "query": username, "status_code": r.status_code, "error": f"GitHub HTTP {r.status_code}"})
    try:
        data = r.json()
    except Exception:
        data = {}
    profile = {
        "login": data.get("login"),
        "id": data.get("id"),
        "name": _compact_text(data.get("name"), 180),
        "company": _compact_text(data.get("company"), 180),
        "blog": _compact_text(data.get("blog"), 240),
        "location": _compact_text(data.get("location"), 180),
        "email": data.get("email"),
        "bio": _compact_text(data.get("bio"), 500),
        "twitter_username": data.get("twitter_username"),
        "public_repos": data.get("public_repos"),
        "followers": data.get("followers"),
        "following": data.get("following"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "avatar_url": data.get("avatar_url"),
        "html_url": data.get("html_url"),
    }
    return _set_cache(cache_key, {
        "ok": True,
        "type": "github",
        "query": username,
        "found": True,
        "endpoint": url,
        "profile": profile,
        "notice": "Métadonnées publiques GitHub uniquement. Aucun dépôt privé ni donnée authentifiée n'est consulté.",
    })


def search_wikidata(q):
    """Recherche simple Wikidata/Wikipédia via l'API publique MediaWiki."""
    q = (q or "").strip()
    if len(q) < 2:
        return {"ok": False, "type": "wikidata", "error": "Requête Wikidata trop courte."}
    cache_key = ("wikidata", q.lower())
    cached = _cached(cache_key)
    if cached is not None:
        return cached
    params = {
        "action": "wbsearchentities",
        "format": "json",
        "language": "fr",
        "uselang": "fr",
        "type": "item",
        "limit": 5,
        "search": q,
    }
    r = _safe_get(WIKIDATA_API_URL, params=params, timeout=(5, 10))
    if isinstance(r, Exception):
        return _set_cache(cache_key, {"ok": False, "type": "wikidata", "query": q, "error": f"Wikidata indisponible : {type(r).__name__}"})
    if not (200 <= r.status_code < 300):
        return _set_cache(cache_key, {"ok": False, "type": "wikidata", "query": q, "status_code": r.status_code, "error": f"Wikidata HTTP {r.status_code}"})
    try:
        payload = r.json()
    except Exception:
        payload = {}
    results = []
    for item in (payload.get("search") or [])[:5]:
        if not isinstance(item, dict):
            continue
        qid = item.get("id")
        results.append({
            "id": qid,
            "label": _compact_text(item.get("label"), 180),
            "description": _compact_text(item.get("description"), 240),
            "url": item.get("concepturi") or (f"https://www.wikidata.org/wiki/{qid}" if qid else None),
            "match": item.get("match", {}).get("text") if isinstance(item.get("match"), dict) else None,
        })
    return _set_cache(cache_key, {
        "ok": True,
        "type": "wikidata",
        "query": q,
        "count": len(results),
        "results": results,
        "source": WIKIDATA_API_URL,
        "notice": "Recherche publique Wikidata. À interpréter surtout pour personnes publiques, organisations, projets ou entités notables.",
    })


def _norm_for_score(value):
    value = "" if value is None else str(value).strip().lower()
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"^www\.", "", value)
    value = re.sub(r"[^a-z0-9àâäéèêëîïôöùûüçñ._@ -]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .-/")
    return value


def _add_signal(bucket, kind, value, source, weight=1, url=None):
    value_s = "" if value is None else str(value).strip()
    if not value_s:
        return
    norm = _norm_for_score(value_s)
    if len(norm) < 2:
        return
    bucket.append({"kind": kind, "value": value_s, "norm": norm, "source": source, "weight": weight, "url": url})


def _signals_from_crossref(data):
    signals = []
    base = data.get("results") if isinstance(data, dict) else {}
    if isinstance(base, dict):
        for item in (base.get("results") or [])[:40]:
            _add_signal(signals, "profile_url", item.get("url"), item.get("platform") or "Présence web", 1, item.get("url"))
            _add_signal(signals, "platform", item.get("platform"), item.get("platform") or "Présence web", 1, item.get("url"))

    gh = data.get("github") if isinstance(data, dict) else None
    if isinstance(gh, dict) and gh.get("ok") and gh.get("found"):
        p = gh.get("profile") or {}
        _add_signal(signals, "username", p.get("login"), "GitHub", 2, p.get("html_url"))
        _add_signal(signals, "display_name", p.get("name"), "GitHub", 3, p.get("html_url"))
        _add_signal(signals, "organization", p.get("company"), "GitHub", 2, p.get("html_url"))
        _add_signal(signals, "location", p.get("location"), "GitHub", 2, p.get("html_url"))
        _add_signal(signals, "domain_or_url", p.get("blog"), "GitHub", 2, p.get("html_url"))
        _add_signal(signals, "bio", p.get("bio"), "GitHub", 1, p.get("html_url"))
        _add_signal(signals, "username", p.get("twitter_username"), "GitHub/X", 2, p.get("html_url"))

    rd = data.get("reddit") if isinstance(data, dict) else None
    if isinstance(rd, dict) and rd.get("ok") and rd.get("found"):
        p = rd.get("profile") or {}
        _add_signal(signals, "username", p.get("name"), "Reddit", 2, p.get("url"))
        _add_signal(signals, "bio", p.get("subreddit_title"), "Reddit", 1, p.get("url"))
        _add_signal(signals, "bio", p.get("subreddit_public_description"), "Reddit", 1, p.get("url"))

    li = data.get("linkedin") if isinstance(data, dict) else None
    if isinstance(li, dict) and li.get("ok") and li.get("found"):
        p = li.get("profile") or {}
        _add_signal(signals, "profile_url", p.get("profile_url") or li.get("profile_url"), "LinkedIn", 2, p.get("profile_url") or li.get("profile_url"))
        _add_signal(signals, "display_name", p.get("full_name"), "LinkedIn", 3, p.get("profile_url") or li.get("profile_url"))
        _add_signal(signals, "display_name", " ".join([str(p.get("first_name") or ""), str(p.get("last_name") or "")]).strip(), "LinkedIn", 2, p.get("profile_url") or li.get("profile_url"))
        _add_signal(signals, "location", p.get("location"), "LinkedIn", 2, p.get("profile_url") or li.get("profile_url"))
        _add_signal(signals, "organization", p.get("company"), "LinkedIn", 2, p.get("profile_url") or li.get("profile_url"))
        _add_signal(signals, "bio", p.get("headline"), "LinkedIn", 1, p.get("profile_url") or li.get("profile_url"))
        _add_signal(signals, "bio", p.get("summary"), "LinkedIn", 1, p.get("profile_url") or li.get("profile_url"))

    xp = data.get("x_profile") if isinstance(data, dict) else None
    if isinstance(xp, dict) and xp.get("ok") and xp.get("found"):
        p = xp.get("profile") or {}
        _add_signal(signals, "username", p.get("username"), "X", 2, p.get("url"))
        _add_signal(signals, "display_name", p.get("name"), "X", 3, p.get("url"))
        _add_signal(signals, "location", p.get("location"), "X", 2, p.get("url"))
        _add_signal(signals, "domain_or_url", p.get("external_url"), "X", 2, p.get("url"))
        _add_signal(signals, "bio", p.get("description"), "X", 1, p.get("url"))

    sc = data.get("social_cli") if isinstance(data, dict) else None
    if isinstance(sc, dict) and sc.get("ok"):
        for r in (sc.get("results") or [])[:80]:
            _add_signal(signals, "profile_url", r.get("url"), r.get("site") or sc.get("tool") or "Maigret/Sherlock", 1, r.get("url"))
            for k, v in (r.get("details") or {}).items():
                if k in ("username", "nickname", "uid"):
                    _add_signal(signals, "username", v, r.get("site") or "Maigret", 1, r.get("url"))
                elif k in ("fullname", "name", "tagline"):
                    _add_signal(signals, "display_name", v, r.get("site") or "Maigret", 2, r.get("url"))
                elif k in ("country", "location", "city"):
                    _add_signal(signals, "location", v, r.get("site") or "Maigret", 1, r.get("url"))
                elif k in ("company", "role"):
                    _add_signal(signals, "organization", v, r.get("site") or "Maigret", 1, r.get("url"))

    bx = data.get("brixhub") if isinstance(data, dict) else None
    if isinstance(bx, dict) and bx.get("ok"):
        payload = bx.get("results") or {}
        payload_data = payload.get("data") if isinstance(payload, dict) else {}
        profiles = payload_data.get("results") if isinstance(payload_data, dict) else payload.get("results", []) if isinstance(payload, dict) else []
        for p in (profiles or [])[:5]:
            if not isinstance(p, dict):
                continue
            _add_signal(signals, "display_name", " ".join([str(p.get("prenom") or ""), str(p.get("nom_famille") or "")]).strip(), "BrixHub", 3)
            _add_signal(signals, "username", p.get("nom_utilisateur"), "BrixHub", 2)
            _add_signal(signals, "location", p.get("ville"), "BrixHub", 2)
            _add_signal(signals, "organization", p.get("societe"), "BrixHub", 2)
            _add_signal(signals, "email", p.get("email"), "BrixHub", 3)
            if p.get("_confidence") is not None:
                _add_signal(signals, "confidence", p.get("_confidence"), "BrixHub", 1)

    ent = data.get("entreprise") if isinstance(data, dict) else None
    if isinstance(ent, dict) and ent.get("ok"):
        for e in (ent.get("results") or [])[:5]:
            _add_signal(signals, "organization", e.get("nom_complet"), "API Entreprises", 2)
            _add_signal(signals, "location", e.get("adresse"), "API Entreprises", 1)
            for d in (e.get("dirigeants") or [])[:3]:
                _add_signal(signals, "display_name", " ".join([str(d.get("prenoms") or ""), str(d.get("nom") or "")]).strip(), "API Entreprises", 2)

    wd = data.get("wikidata") if isinstance(data, dict) else None
    if isinstance(wd, dict) and wd.get("ok"):
        for item in (wd.get("results") or [])[:5]:
            _add_signal(signals, "display_name", item.get("label"), "Wikidata", 2, item.get("url"))
            _add_signal(signals, "bio", item.get("description"), "Wikidata", 1, item.get("url"))
            _add_signal(signals, "domain_or_url", item.get("url"), "Wikidata", 1, item.get("url"))
    return signals


def compute_correlation_score(data):
    """Calcule un score de concordance prudent, non conclusif.

    Le score mesure la cohérence d'indices publics entre modules. Il ne prouve pas
    une identité réelle et ne doit pas être présenté comme une identification certaine.
    """
    q = (data.get("query") or "").strip()
    q_norm = _norm_for_score(q.lstrip("@"))
    typ = data.get("type") or "auto"
    signals = _signals_from_crossref(data)
    reasons = []
    warnings = ["Score indicatif : il mesure une concordance d'indices publics, pas une preuve d'identité."]
    score = 0

    profile_urls = [s for s in signals if s["kind"] == "profile_url"]
    unique_domains = set()
    for s in profile_urls:
        try:
            unique_domains.add(urlparse(s["value"]).netloc.lower().replace("www.", ""))
        except Exception:
            pass
    presence_count = len(unique_domains) or len(profile_urls)
    if presence_count >= 1:
        add = min(25, 8 + presence_count * 3)
        score += add
        reasons.append({"label": "Présence multi-plateforme", "points": add, "detail": f"{presence_count} profil(s)/domaine(s) public(s) distinct(s) détecté(s)."})

    username_sources = {s["source"] for s in signals if s["kind"] == "username" and s["norm"] == q_norm}
    if q_norm and username_sources:
        add = min(18, 8 + 4 * len(username_sources))
        score += add
        reasons.append({"label": "Pseudo exact recoupé", "points": add, "detail": "Même pseudo retrouvé via " + ", ".join(sorted(username_sources)) + "."})

    def duplicates_for(kind):
        groups = {}
        for s in signals:
            if s["kind"] != kind:
                continue
            if kind == "display_name" and s["norm"] == q_norm:
                continue
            if len(s["norm"]) < 3:
                continue
            groups.setdefault(s["norm"], set()).add(s["source"])
        return [(k, v) for k, v in groups.items() if len(v) >= 2]

    name_dups = duplicates_for("display_name")
    if name_dups:
        best = max(name_dups, key=lambda kv: len(kv[1]))
        add = min(20, 10 + 5 * len(best[1]))
        score += add
        reasons.append({"label": "Nom affiché cohérent", "points": add, "detail": f"Nom/affichage similaire retrouvé dans {len(best[1])} source(s) : {', '.join(sorted(best[1]))}."})

    loc_dups = duplicates_for("location")
    if loc_dups:
        best = max(loc_dups, key=lambda kv: len(kv[1]))
        add = min(15, 6 + 4 * len(best[1]))
        score += add
        reasons.append({"label": "Localisation cohérente", "points": add, "detail": f"Indice de localisation similaire dans {len(best[1])} source(s)."})

    org_dups = duplicates_for("organization")
    if org_dups:
        best = max(org_dups, key=lambda kv: len(kv[1]))
        add = min(15, 6 + 4 * len(best[1]))
        score += add
        reasons.append({"label": "Organisation cohérente", "points": add, "detail": f"Organisation/société similaire dans {len(best[1])} source(s)."})

    # Domaine / URL externe : utile pour relier GitHub, X, Wikidata ou profil perso.
    domains = {}
    for s in signals:
        if s["kind"] != "domain_or_url":
            continue
        value = s["value"]
        try:
            dom = _normalize_domain(value)
            if dom and "." in dom:
                domains.setdefault(dom, set()).add(s["source"])
        except Exception:
            continue
    domain_dups = [(d, srcs) for d, srcs in domains.items() if len(srcs) >= 2]
    if domain_dups:
        best = max(domain_dups, key=lambda kv: len(kv[1]))
        add = min(12, 5 + 4 * len(best[1]))
        score += add
        reasons.append({"label": "Site/domaine recoupé", "points": add, "detail": f"Même domaine externe repéré via {', '.join(sorted(best[1]))}."})

    # BrixHub expose déjà un score interne ; on l'intègre faiblement pour ne pas dominer.
    conf_values = []
    for s in signals:
        if s["kind"] == "confidence":
            try:
                conf_values.append(float(s["value"]))
            except Exception:
                pass
    if conf_values:
        conf = max(conf_values)
        add = int(min(10, max(0, conf) / 10))
        score += add
        reasons.append({"label": "Confiance source externe", "points": add, "detail": f"Score interne maximal observé : {int(conf)}/100."})

    score = max(0, min(100, int(score)))
    if score < 35:
        level = "faible"
        color = "low"
    elif score < 65:
        level = "moyen"
        color = "mid"
    else:
        level = "fort"
        color = "good"
        warnings.append("Même avec un niveau fort, une validation humaine reste nécessaire avant toute conclusion.")

    return {
        "ok": True,
        "type": "correlation_score",
        "query": q,
        "target_type": typ,
        "score": score,
        "level": level,
        "color": color,
        "reasons": reasons,
        "signals_count": len(signals),
        "sources_count": len({s["source"] for s in signals}),
        "warnings": warnings,
    }




def _compact_text(value, limit=260):
    value = "" if value is None else str(value).strip()
    return value if len(value) <= limit else value[:limit - 1] + "…"


def search_entreprise(q):
    """Recherche défensive dans l'API publique des entreprises françaises.

    Usage prévu : audit d'exposition d'un nom, pseudo ou société fourni volontairement.
    Aucune clé API n'est requise.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return {"ok": False, "type": "entreprise", "error": "Requête entreprise trop courte."}
    cache_key = ("entreprise", q.lower())
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    params = {"q": q, "page": 1, "per_page": 5}
    r = _safe_get(ENTREPRISE_API_URL, params=params, timeout=(6, 12))
    if isinstance(r, Exception):
        return _set_cache(cache_key, {"ok": False, "type": "entreprise", "query": q, "error": f"API Entreprises indisponible : {type(r).__name__}"})
    if not (200 <= r.status_code < 300):
        return _set_cache(cache_key, {"ok": False, "type": "entreprise", "query": q, "status_code": r.status_code, "error": f"API Entreprises HTTP {r.status_code}"})
    try:
        data = r.json()
    except Exception:
        return _set_cache(cache_key, {"ok": False, "type": "entreprise", "query": q, "error": "Réponse API Entreprises non JSON."})

    raw_results = data.get("results") or data.get("data") or []
    out_results = []
    for item in raw_results[:5]:
        if not isinstance(item, dict):
            continue
        dirigeants = []
        for d in (item.get("dirigeants") or [])[:5]:
            if not isinstance(d, dict):
                continue
            dirigeants.append({
                "nom": _compact_text(d.get("nom") or d.get("nom_famille")),
                "prenoms": _compact_text(d.get("prenoms") or d.get("prenom")),
                "qualite": _compact_text(d.get("qualite") or d.get("fonction") or d.get("type_dirigeant")),
                "annee_naissance": d.get("annee_naissance"),
            })
        siege = item.get("siege") if isinstance(item.get("siege"), dict) else {}
        out_results.append({
            "nom_complet": _compact_text(item.get("nom_complet") or item.get("nom_raison_sociale") or item.get("nom") or item.get("raison_sociale")),
            "siren": item.get("siren"),
            "siret_siege": item.get("siret_siege") or siege.get("siret"),
            "etat_administratif": item.get("etat_administratif") or item.get("etat_administratif_entreprise"),
            "nature_juridique": _compact_text(item.get("nature_juridique") or item.get("forme_juridique")),
            "activite_principale": item.get("activite_principale") or item.get("section_activite_principale"),
            "categorie_entreprise": item.get("categorie_entreprise"),
            "date_creation": item.get("date_creation"),
            "adresse": _compact_text(item.get("adresse") or siege.get("adresse") or siege.get("libelle_commune")),
            "dirigeants": dirigeants,
        })
    return _set_cache(cache_key, {
        "ok": True,
        "type": "entreprise",
        "query": q,
        "count": len(out_results),
        "results": out_results,
        "source": ENTREPRISE_API_URL,
        "notice": "Recherche effectuée sur l'API publique recherche-entreprises.api.gouv.fr. Usage recommandé : audit autorisé ou recherche d'une société/personne déclarée publiquement.",
    })


def reddit_user_info(username):
    """Récupère uniquement les métadonnées publiques du profil Reddit, sans scraper les commentaires."""
    username = (username or "").strip().lstrip("@")
    if not _USERNAME_RE.match(username):
        return {"ok": False, "type": "reddit", "error": "Nom Reddit invalide."}
    cache_key = ("reddit", username.lower())
    cached = _cached(cache_key)
    if cached is not None:
        return cached
    url = f"https://www.reddit.com/user/{username}/about.json"
    r = _safe_get(url, timeout=(5, 10))
    if isinstance(r, Exception):
        return _set_cache(cache_key, {"ok": False, "type": "reddit", "query": username, "error": f"Reddit indisponible : {type(r).__name__}"})
    if r.status_code == 404:
        return _set_cache(cache_key, {"ok": True, "type": "reddit", "query": username, "found": False, "results": None})
    if r.status_code in (403, 429):
        return _set_cache(cache_key, {"ok": False, "type": "reddit", "query": username, "status_code": r.status_code, "error": "Reddit bloque ou limite temporairement les requêtes."})
    if not (200 <= r.status_code < 300):
        return _set_cache(cache_key, {"ok": False, "type": "reddit", "query": username, "status_code": r.status_code, "error": f"Reddit HTTP {r.status_code}"})
    try:
        payload = r.json().get("data", {})
    except Exception:
        payload = {}
    created_utc = payload.get("created_utc")
    return _set_cache(cache_key, {
        "ok": True,
        "type": "reddit",
        "query": username,
        "found": True,
        "profile": {
            "name": payload.get("name") or username,
            "url": f"https://www.reddit.com/user/{username}/",
            "created_utc": created_utc,
            "created_iso": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(created_utc)) if created_utc else None,
            "comment_karma": payload.get("comment_karma"),
            "link_karma": payload.get("link_karma"),
            "total_karma": payload.get("total_karma"),
            "is_gold": payload.get("is_gold"),
            "is_mod": payload.get("is_mod"),
            "verified": payload.get("verified"),
            "over_18": payload.get("over_18"),
            "subreddit_title": ((payload.get("subreddit") or {}).get("title") if isinstance(payload.get("subreddit"), dict) else None),
            "subreddit_public_description": _compact_text(((payload.get("subreddit") or {}).get("public_description") if isinstance(payload.get("subreddit"), dict) else None), 500),
        },
        "notice": "Métadonnées publiques Reddit uniquement ; pas d'analyse de commentaires dans cette version.",
    })


def _parse_cli_json(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None
    return None


def _extract_urls_from_text(text, max_items=80):
    urls = []
    seen = set()
    for m in re.finditer(r"https?://[^\s)>'\"]+", text or ""):
        url = m.group(0).rstrip(".,;]")
        if url not in seen:
            seen.add(url)
            urls.append({"url": url})
            if len(urls) >= max_items:
                break
    return urls




def _extract_maigret_text_results(text, max_items=80):
    """Parse la sortie texte standard de Maigret.

    Sur Windows, Maigret fonctionne souvent avec `python -m maigret pseudo`,
    mais ne produit pas toujours un JSON exploitable directement. Cette fonction
    extrait donc les lignes `[+] Site: https://...` et quelques détails affichés
    juste en dessous.
    """
    results = []
    current = None
    seen = set()
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        m = re.match(r"^\[\+\]\s+([^:]+):\s+(https?://\S+)", line)
        if m:
            site = m.group(1).strip()
            url = m.group(2).rstrip(".,;])")
            if url not in seen:
                seen.add(url)
                current = {"site": site, "url": url, "status": "found", "details": {}}
                results.append(current)
                if len(results) >= max_items:
                    break
            else:
                current = None
            continue

        if current and ("├─" in line or "└─" in line or "|-" in line):
            detail = line.replace("├─", "").replace("└─", "").replace("|-", "").strip()
            if ":" in detail:
                k, v = detail.split(":", 1)
                k = re.sub(r"[^a-zA-Z0-9_ -]", "", k).strip().lower().replace(" ", "_")
                v = v.strip()
                if k and v:
                    current.setdefault("details", {})[k] = v

    if results:
        return results[:max_items]
    return _extract_urls_from_text(text, max_items=max_items)


def _compact_maigret_json(data, max_items=80):
    results = []
    if isinstance(data, dict):
        sites = data.get("sites") if isinstance(data.get("sites"), dict) else data
        if isinstance(sites, dict):
            for name, value in sites.items():
                if len(results) >= max_items:
                    break
                if not isinstance(value, dict):
                    continue
                status = str(value.get("status") or value.get("status_code") or "").lower()
                url = value.get("url_user") or value.get("url") or value.get("profile_url")
                if url and ("claim" in status or "found" in status or value.get("exists") is True or value.get("status") in (200, "200")):
                    results.append({
                        "site": name,
                        "url": url,
                        "status": value.get("status") or value.get("status_code") or "found",
                        "tags": value.get("tags") if isinstance(value.get("tags"), list) else [],
                    })
    return results


def _script_command_candidates(command_name):
    """Retourne les commandes possibles, même si le dossier Scripts n'est pas dans PATH."""
    candidates = []

    direct = shutil.which(command_name)
    if direct:
        candidates.append([direct])

    scripts_dir = sysconfig.get_path("scripts")
    if scripts_dir:
        possible_names = [
            command_name,
            f"{command_name}.exe",
            f"{command_name}.cmd",
            f"{command_name}.bat",
            f"{command_name}-script.py",
        ]
        for name in possible_names:
            path = os.path.join(scripts_dir, name)
            if os.path.exists(path):
                if path.endswith(".py"):
                    candidates.append([sys.executable, path])
                else:
                    candidates.append([path])

    # Déduplication sans casser l'ordre de priorité.
    deduped = []
    seen = set()
    for c in candidates:
        key = tuple(c)
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    return deduped


def _social_cli_candidates(tool):
    """Détecte Maigret/Sherlock via PATH, Scripts Python ou python -m."""
    candidates = []

    if tool in ("auto", "maigret"):
        for base in _script_command_candidates("maigret"):
            candidates.append({"tool": "maigret", "base": base, "how": "script"})
        if importlib.util.find_spec("maigret") is not None:
            candidates.append({"tool": "maigret", "base": [sys.executable, "-m", "maigret"], "how": "python -m"})

    if tool in ("auto", "sherlock"):
        for base in _script_command_candidates("sherlock"):
            candidates.append({"tool": "sherlock", "base": base, "how": "script"})
        # Selon les installations, le module peut s'appeler sherlock ou sherlock_project.
        if importlib.util.find_spec("sherlock") is not None:
            candidates.append({"tool": "sherlock", "base": [sys.executable, "-m", "sherlock"], "how": "python -m"})
        elif importlib.util.find_spec("sherlock_project") is not None:
            candidates.append({"tool": "sherlock", "base": [sys.executable, "-m", "sherlock_project"], "how": "python -m"})

    deduped = []
    seen = set()
    for c in candidates:
        key = (c["tool"], tuple(c["base"]))
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    return deduped


def search_social_cli(username, tool="auto"):
    """Lance Maigret ou Sherlock si l'outil est installé localement. Aucune clé API.

    Correction Windows : Maigret peut fonctionner en terminal mais échouer dans le
    plugin si l'ancien appel attend un JSON ou si le timeout total est trop court.
    Ici on appelle d'abord Maigret en sortie texte simple, puis on parse les URLs.
    """
    username = (username or "").strip().lstrip("@")
    tool = (tool or "auto").lower()
    if not _USERNAME_RE.match(username):
        return {"ok": False, "type": "social_cli", "error": "Pseudonyme invalide."}
    cache_key = ("social_cli", tool, username.lower())
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    candidates = _social_cli_candidates(tool)
    if not candidates:
        scripts_dir = sysconfig.get_path("scripts") or ""
        return _set_cache(cache_key, {
            "ok": False,
            "type": "social_cli",
            "query": username,
            "error": "Maigret/Sherlock introuvable par le Python qui lance Second-Brain.",
            "install_help": [
                "python -m pip install maigret",
                "python -m pip install sherlock-project",
                f"Dossier Scripts détecté: {scripts_dir}" if scripts_dir else "Dossier Scripts Python non détecté",
            ],
        })

    try:
        cli_timeout = int(os.getenv("OSINTCX_CLI_TIMEOUT", "90"))
    except Exception:
        cli_timeout = 90

    errors = []
    for candidate in candidates:
        chosen = candidate["tool"]
        base = candidate["base"]
        try:
            if chosen == "maigret":
                # Appel volontairement minimal : c'est exactement le mode qui marche
                # dans ton terminal avec `python -m maigret testuser`.
                cmd = base + [username]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=cli_timeout, encoding="utf-8", errors="ignore")
                output = (proc.stdout or "") + "\n" + (proc.stderr or "")
                results = _extract_maigret_text_results(output)
            else:
                cmd = base + [username, "--print-found", "--timeout", "15"]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=cli_timeout, encoding="utf-8", errors="ignore")
                output = (proc.stdout or "") + "\n" + (proc.stderr or "")
                results = _extract_urls_from_text(output)

            # Maigret peut écrire beaucoup de warnings et retourner un code non nul ;
            # si des profils sont extraits, on considère quand même la recherche OK.
            if results:
                return _set_cache(cache_key, {
                    "ok": True,
                    "type": "social_cli",
                    "query": username,
                    "tool": chosen,
                    "launch_mode": candidate.get("how"),
                    "command": " ".join(cmd[:3]) + (" ..." if len(cmd) > 3 else ""),
                    "count": len(results),
                    "results": results[:80],
                    "returncode": proc.returncode,
                    "notice": "Résultats issus d'un outil local. Vérifiez manuellement les profils importants : les faux positifs sont possibles.",
                })

            errors.append({
                "tool": chosen,
                "mode": candidate.get("how"),
                "returncode": proc.returncode,
                "stdout_tail": (proc.stdout or "")[-700:],
                "stderr_tail": (proc.stderr or "")[-700:],
                "command": " ".join(cmd),
            })
        except subprocess.TimeoutExpired as exc:
            partial = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + "\n" + ((exc.stderr or "") if isinstance(exc.stderr, str) else "")
            partial_results = _extract_maigret_text_results(partial) if chosen == "maigret" else _extract_urls_from_text(partial)
            if partial_results:
                return _set_cache(cache_key, {
                    "ok": True,
                    "type": "social_cli",
                    "query": username,
                    "tool": chosen,
                    "launch_mode": candidate.get("how"),
                    "command": " ".join((base + [username])[:3]) + " ...",
                    "count": len(partial_results),
                    "results": partial_results[:80],
                    "returncode": "timeout",
                    "notice": "Recherche interrompue par timeout, mais des profils partiels ont été extraits. Augmente OSINTCX_CLI_TIMEOUT si besoin.",
                })
            errors.append({"tool": chosen, "mode": candidate.get("how"), "error": f"Timeout après {cli_timeout}s"})
        except Exception as exc:
            errors.append({"tool": chosen, "mode": candidate.get("how"), "error": str(exc)})

    return _set_cache(cache_key, {
        "ok": False,
        "type": "social_cli",
        "query": username,
        "error": "Maigret/Sherlock détecté, mais aucune commande n'a produit de profil exploitable.",
        "attempts": errors[:5],
    })


def _x_headers():
    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
    }
    if X_BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {X_BEARER_TOKEN}"
    return headers


def search_x_profile(username):
    """Lit un profil public X via l'API officielle v2, si X_BEARER_TOKEN est configuré.

    Usage prévu : audit défensif d'un compte fourni volontairement.
    N'analyse pas les posts et ne suit pas les abonnés.
    """
    username = (username or "").strip().lstrip("@")
    if not _USERNAME_RE.match(username):
        return {"ok": False, "type": "x", "error": "Nom d'utilisateur X invalide."}
    if not X_BEARER_TOKEN:
        return {
            "ok": False,
            "type": "x",
            "enabled": False,
            "query": username,
            "error": "X non configuré : définissez X_BEARER_TOKEN côté serveur dans le fichier .env.",
        }

    cache_key = ("x_profile", username.lower(), X_USER_FIELDS)
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    url = f"{X_BASE_URL}/2/users/by/username/{username}"
    params = {"user.fields": X_USER_FIELDS}
    try:
        r = http.get(url, params=params, headers=_x_headers(), timeout=TIMEOUT)
    except http.exceptions.RequestException as exc:
        return _set_cache(cache_key, {
            "ok": False,
            "type": "x",
            "enabled": True,
            "query": username,
            "endpoint": url,
            "error": f"Erreur réseau X API : {type(exc).__name__}",
        })

    if r.status_code == 404:
        return _set_cache(cache_key, {"ok": True, "type": "x", "enabled": True, "query": username, "found": False, "endpoint": url})
    if r.status_code in (401, 403):
        return _set_cache(cache_key, {
            "ok": False,
            "type": "x",
            "enabled": True,
            "query": username,
            "endpoint": url,
            "status_code": r.status_code,
            "error": "Accès X API refusé : vérifiez X_BEARER_TOKEN et les droits de votre application X.",
            "response_preview": getattr(r, "text", "")[:700],
        })
    if r.status_code == 429:
        return _set_cache(cache_key, {
            "ok": False,
            "type": "x",
            "enabled": True,
            "query": username,
            "endpoint": url,
            "status_code": 429,
            "error": "Limite de requêtes X API atteinte.",
            "rate_limits": {k: v for k, v in r.headers.items() if k.lower().startswith("x-rate-limit")},
        })
    if not (200 <= r.status_code < 300):
        return _set_cache(cache_key, {
            "ok": False,
            "type": "x",
            "enabled": True,
            "query": username,
            "endpoint": url,
            "status_code": r.status_code,
            "error": f"X API HTTP {r.status_code}",
            "response_preview": getattr(r, "text", "")[:700],
        })

    try:
        payload = r.json()
    except Exception:
        return _set_cache(cache_key, {
            "ok": False,
            "type": "x",
            "enabled": True,
            "query": username,
            "endpoint": url,
            "error": "Réponse X API non JSON.",
            "response_preview": getattr(r, "text", "")[:700],
        })

    user = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(user, dict):
        return _set_cache(cache_key, {
            "ok": True,
            "type": "x",
            "enabled": True,
            "query": username,
            "found": False,
            "endpoint": url,
            "raw": payload,
        })

    profile = {
        "id": user.get("id"),
        "name": user.get("name"),
        "username": user.get("username"),
        "url": f"https://x.com/{user.get('username') or username}",
        "description": _compact_text(user.get("description"), 700),
        "location": _compact_text(user.get("location"), 160),
        "created_at": user.get("created_at"),
        "verified": user.get("verified"),
        "verified_type": user.get("verified_type"),
        "profile_image_url": user.get("profile_image_url"),
        "external_url": user.get("url"),
        "public_metrics": user.get("public_metrics") if isinstance(user.get("public_metrics"), dict) else {},
    }
    return _set_cache(cache_key, {
        "ok": True,
        "type": "x",
        "enabled": True,
        "query": username,
        "found": True,
        "endpoint": url,
        "profile": profile,
        "rate_limits": {k: v for k, v in r.headers.items() if k.lower().startswith("x-rate-limit")},
        "notice": "Lecture seule du profil public via X API v2. Aucun post, follower ou donnée privée n'est récupéré.",
    })

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
    truthy = ("1", "true", "yes", "on")
    if request.args.get("brixhub", "0").lower() in truthy:
        data["brixhub"] = search_brixhub(q, typ)
    if request.args.get("entreprise", "0").lower() in truthy:
        data["entreprise"] = search_entreprise(q)
    if request.args.get("wikidata", "0").lower() in truthy:
        data["wikidata"] = search_wikidata(q)
    if typ == "username" and request.args.get("github", "0").lower() in truthy:
        data["github"] = search_github_profile(q)
    if typ == "username" and request.args.get("reddit", "0").lower() in truthy:
        data["reddit"] = reddit_user_info(q)
    if typ == "username" and request.args.get("x", "0").lower() in truthy:
        data["x_profile"] = search_x_profile(q)
    if typ == "username" and request.args.get("linkedin", "0").lower() in truthy:
        data["linkedin"] = search_linkedin_rapidapi(q, request.args.get("linkedin_url", ""))
    if typ == "username" and request.args.get("socialcli", "0").lower() in truthy:
        data["social_cli"] = search_social_cli(q, request.args.get("social_tool", "auto"))
    if request.args.get("score", "1").lower() in truthy:
        data["correlation"] = compute_correlation_score(data)
    return data


def register(app, rd_cfg):
    """Point d'entrée appelé automatiquement par le plugin loader Second Brain."""

    @app.route("/api/osintcx/ping", methods=["GET"])
    def osintcx_ping():
        return jsonify({"ok": True, "plugin": "osint-cx", "version": "1.8", "routes_registered": True})

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

    @app.route("/api/osintcx/entreprise", methods=["GET"])
    def osintcx_entreprise():
        data = search_entreprise(request.args.get("q", ""))
        status = 200 if data.get("ok", True) else 400
        return jsonify(data), status

    @app.route("/api/osintcx/github", methods=["GET"])
    def osintcx_github():
        data = search_github_profile(request.args.get("q", ""))
        status = 200 if data.get("ok", True) else 400
        return jsonify(data), status

    @app.route("/api/osintcx/wikidata", methods=["GET"])
    def osintcx_wikidata():
        data = search_wikidata(request.args.get("q", ""))
        status = 200 if data.get("ok", True) else 400
        return jsonify(data), status

    @app.route("/api/osintcx/score", methods=["POST"])
    def osintcx_score():
        payload = request.get_json(silent=True) or {}
        data = compute_correlation_score(payload)
        return jsonify(data), 200

    @app.route("/api/osintcx/reddit", methods=["GET"])
    def osintcx_reddit():
        data = reddit_user_info(request.args.get("q", ""))
        status = 200 if data.get("ok", True) else 400
        return jsonify(data), status

    @app.route("/api/osintcx/x", methods=["GET"])
    def osintcx_x():
        data = search_x_profile(request.args.get("q", ""))
        status = 200 if data.get("ok", True) else 400
        return jsonify(data), status

    @app.route("/api/osintcx/linkedin", methods=["GET", "POST"])
    def osintcx_linkedin():
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            data = search_linkedin_rapidapi(payload.get("q", ""), payload.get("url", ""))
        else:
            data = search_linkedin_rapidapi(request.args.get("q", ""), request.args.get("url", ""))
        status = 200 if data.get("ok", True) else 400
        return jsonify(data), status

    @app.route("/api/osintcx/social-cli", methods=["GET"])
    def osintcx_social_cli():
        data = search_social_cli(request.args.get("q", ""), request.args.get("tool", "auto"))
        status = 200 if data.get("ok", True) else 400
        return jsonify(data), status

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

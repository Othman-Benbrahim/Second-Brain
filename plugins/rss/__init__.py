"""Plugin RSS — gestion + analyse IA des flux RSS/Atom.

Caractéristiques :
- Stockage des flux dans `flux-rss.md` à la racine du vault (philosophie IRIS∞)
- Auto-découverte des flux RSS depuis n'importe quelle URL (ex: lemonde.fr → /rss/une.xml)
- Parser natif RSS 2.0 + Atom 1.0 (pas de dépendance feedparser)
- Fetch parallèle des articles complets (ThreadPoolExecutor)
- Cache 30 min par flux et par article
- Deux modes d'analyse : tous flux ensemble OU flux par flux
"""
import re, time, html, os
from datetime import datetime, timedelta, timezone
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET
from flask import request, jsonify
import requests as http

from second_brain import _ai_call

# Chargement dynamique de signal_engine (le loader principal ne gère pas les imports relatifs)
import importlib.util as _ilu
_se_spec = _ilu.spec_from_file_location("rss_signal_engine", Path(__file__).parent / "signal_engine.py")
signal_engine = _ilu.module_from_spec(_se_spec)
_se_spec.loader.exec_module(signal_engine)

# ── Configuration ─────────────────────────────────────────────
VAULT_RSS_FILE   = "flux-rss.md"
FETCH_TIMEOUT    = (10, 20)
PARALLEL_WORKERS = 6
ARTICLE_MAX_CHARS = 5000
FEED_CACHE_TTL   = 1800    # 30 min
PAGE_CACHE_TTL   = 1800
USER_AGENT       = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_FEED_CACHE = {}    # url -> (timestamp, items)
_PAGE_CACHE = {}    # url -> (timestamp, content)
_CACHE_LOCK = Lock()

# Parsing flux-rss.md
RE_FEED_LINE = re.compile(r'^\s*-\s+\[([^\]]+)\]\(([^)]+)\)(.*)$')
RE_TAG       = re.compile(r'#([\w-]+)')
RE_SECTION   = re.compile(r'^##\s+(.+)$')

# Chemins de feed courants pour découverte par convention
COMMON_FEED_PATHS = [
    "/rss", "/feed", "/feed/", "/rss.xml", "/feed.xml", "/atom.xml",
    "/feeds/posts/default", "/rss/une.xml", "/feed/rss",
    "/index.rss", "/rss/all.xml", "/feed/atom",
]


# ═══════════════════════════════════════════════════════════════
#  STORAGE — flux-rss.md
# ═══════════════════════════════════════════════════════════════

def _load_feeds_from_vault(vault_root):
    """Lit flux-rss.md depuis la racine du vault.
    Format de ligne : `- [nom](url) #tag1 #tag2`
    Sections : `## NomSection` regroupe les flux suivants."""
    feeds_file = Path(vault_root) / VAULT_RSS_FILE
    if not feeds_file.exists():
        return []

    feeds = []
    current_section = None
    try:
        with open(feeds_file, 'r', encoding='utf-8') as f:
            for line in f:
                m_section = RE_SECTION.match(line)
                if m_section:
                    current_section = m_section.group(1).strip()
                    continue
                m_feed = RE_FEED_LINE.match(line)
                if m_feed:
                    name = m_feed.group(1).strip()
                    url = m_feed.group(2).strip()
                    extra = m_feed.group(3) or ''
                    tags = [t.lower() for t in RE_TAG.findall(extra)]
                    feeds.append({
                        'name': name,
                        'url': url,
                        'tags': tags,
                        'section': current_section,
                    })
    except Exception as e:
        print(f"[RSS] Erreur lecture {feeds_file}: {e}")
    return feeds


def _save_feeds_to_vault(vault_root, feeds):
    """Écrit la liste des flux dans flux-rss.md.
    Préserve les sections (lignes ## ...)."""
    feeds_file = Path(vault_root) / VAULT_RSS_FILE

    # Regrouper par section
    by_section = {}
    for f in feeds:
        sec = f.get('section') or ''
        by_section.setdefault(sec, []).append(f)

    lines = [
        '# Flux RSS',
        '',
        '*Fichier géré par le plugin RSS de Second Brain (mais éditable manuellement).*',
        '*Format : `- [nom](url-du-flux) #tag1 #tag2` — sections `## NomSection` regroupent les flux.*',
        '',
    ]

    # Flux sans section d'abord
    if '' in by_section:
        for f in by_section['']:
            lines.append(_format_feed_line(f))
        lines.append('')
        del by_section['']

    # Sections triées
    for section in sorted(by_section.keys()):
        lines.append(f'## {section}')
        for f in by_section[section]:
            lines.append(_format_feed_line(f))
        lines.append('')

    feeds_file.write_text('\n'.join(lines), encoding='utf-8')


def _format_feed_line(f):
    line = f'- [{f["name"]}]({f["url"]})'
    if f.get('tags'):
        line += ' ' + ' '.join(f'#{t}' for t in f['tags'])
    return line


# ═══════════════════════════════════════════════════════════════
#  DÉCOUVERTE de flux RSS depuis URL arbitraire
# ═══════════════════════════════════════════════════════════════

def _looks_like_feed(text, content_type=''):
    """Vérifie si un contenu ressemble à un flux RSS/Atom."""
    if 'xml' in (content_type or '').lower():
        return True
    head = text[:1500].lower()
    return ('<rss' in head or '<feed' in head or '<rdf' in head
            or '<?xml' in head and ('<channel' in head or '<entry' in head))


def _parse_feed_title(xml_text):
    """Extrait le titre <title> du flux pour le proposer comme nom."""
    # Regex tolérante (évite les soucis de namespace ET XML)
    m = re.search(r'<channel[^>]*>.*?<title[^>]*>([^<]+)</title>',
                  xml_text[:3000], re.DOTALL | re.IGNORECASE)
    if m: return html.unescape(m.group(1).strip())
    m = re.search(r'<feed[^>]*>.*?<title[^>]*>([^<]+)</title>',
                  xml_text[:3000], re.DOTALL | re.IGNORECASE)
    if m: return html.unescape(m.group(1).strip())
    m = re.search(r'<title[^>]*>([^<]+)</title>', xml_text[:2000], re.IGNORECASE)
    if m: return html.unescape(m.group(1).strip())
    return None


def _discover_feed_url(url):
    """Trouve l'URL RSS effective à partir d'une URL quelconque.
    Retourne (feed_url, name) ou (None, error_msg)."""
    if not url:
        return None, "URL vide"
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    # Étape 1 : si l'URL ressemble déjà à un flux, on tente directement
    lower = url.lower()
    looks_feed = any(lower.rstrip('/').endswith(s) for s in ('.xml', '.rss', '.atom')) \
              or any(s in lower for s in ('/rss', '/feed', '/atom'))
    if looks_feed:
        try:
            r = http.get(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/rss+xml,application/xml,text/xml,*/*;q=0.5'},
                         timeout=FETCH_TIMEOUT, allow_redirects=True)
            if r.status_code == 200 and _looks_like_feed(r.text, r.headers.get('content-type', '')):
                name = _parse_feed_title(r.text) or _domain_name(url)
                return r.url, name
        except Exception:
            pass  # On poursuit avec la découverte

    # Étape 2 : récupérer la page HTML et chercher <link rel="alternate">
    try:
        r = http.get(url, headers={'User-Agent': USER_AGENT},
                     timeout=FETCH_TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return _try_common_feed_paths(url)

        text = r.text

        # Si l'URL était une homepage mais renvoie déjà du XML (rare)
        if _looks_like_feed(text, r.headers.get('content-type', '')):
            name = _parse_feed_title(text) or _domain_name(url)
            return r.url, name

        # Cherche <link rel="alternate" type="application/(rss|atom|rdf)+xml" href="...">
        feed_links = re.findall(
            r'<link[^>]*rel=["\']alternate["\'][^>]*type=["\']application/(?:rss|atom|rdf)\+xml["\'][^>]*?href=["\']([^"\']+)["\']',
            text, re.IGNORECASE)
        # Variante où href apparaît avant type
        feed_links += re.findall(
            r'<link[^>]*type=["\']application/(?:rss|atom|rdf)\+xml["\'][^>]*?href=["\']([^"\']+)["\']',
            text, re.IGNORECASE)
        feed_links += re.findall(
            r'<link[^>]*href=["\']([^"\']+)["\'][^>]*type=["\']application/(?:rss|atom|rdf)\+xml["\']',
            text, re.IGNORECASE)

        if feed_links:
            feed_url = urljoin(r.url, feed_links[0])
            # Récupérer le titre depuis le flux directement
            try:
                rf = http.get(feed_url, headers={'User-Agent': USER_AGENT},
                              timeout=FETCH_TIMEOUT, allow_redirects=True)
                if rf.status_code == 200:
                    name = _parse_feed_title(rf.text)
                    if name: return rf.url, name
            except Exception:
                pass
            # Fallback : titre de la page d'origine
            title_m = re.search(r'<title[^>]*>([^<]+)</title>', text, re.IGNORECASE)
            name = html.unescape(title_m.group(1).strip()) if title_m else _domain_name(url)
            return feed_url, name

        # Pas de link rel=alternate, essayer les chemins courants
        return _try_common_feed_paths(url)

    except Exception as e:
        return _try_common_feed_paths(url)


def _try_common_feed_paths(base_url):
    """Essaie les chemins de feed courants sur le domaine."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"

    for path in COMMON_FEED_PATHS:
        try_url = root + path
        try:
            r = http.get(try_url, headers={'User-Agent': USER_AGENT},
                         timeout=(5, 12), allow_redirects=True)
            if r.status_code == 200 and _looks_like_feed(r.text, r.headers.get('content-type', '')):
                name = _parse_feed_title(r.text) or _domain_name(try_url)
                return r.url, name
        except Exception:
            continue

    return None, f"Aucun flux RSS détecté à {base_url} (auto-discovery + {len(COMMON_FEED_PATHS)} chemins essayés)"


def _domain_name(url):
    try:
        return urlparse(url).netloc.replace('www.', '')
    except Exception:
        return url


# ═══════════════════════════════════════════════════════════════
#  PARSER RSS 2.0 + Atom 1.0
# ═══════════════════════════════════════════════════════════════

def _parse_feed(xml_text, max_items=50):
    """Parse un flux RSS 2.0 ou Atom 1.0.
    Retourne une liste d'items : {title, link, date_str, date_parsed, description}"""
    items = []

    # Nettoyer les namespaces pour faciliter le parsing
    # Retire xmlns="..." et xmlns:prefix="..."
    cleaned = re.sub(r'\sxmlns(?::\w+)?="[^"]*"', '', xml_text)
    # Remplace les préfixes dans les balises : <content:encoded> → <content_encoded>
    cleaned = re.sub(r'<(/?)(\w+):(\w+)', r'<\1\2_\3', cleaned)

    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError:
        # Tentative de récupération : tronquer si XML corrompu en fin
        # Essayer juste de chercher les <item> ou <entry> en regex
        return _parse_feed_regex(xml_text, max_items)

    # RSS 2.0 : <rss><channel><item>...
    # Atom 1.0 : <feed><entry>...
    item_elements = root.findall('.//item') or root.findall('.//entry')

    for el in item_elements[:max_items]:
        items.append(_extract_item(el))

    return items


def _extract_item(el):
    """Extrait un item RSS ou une entry Atom."""
    title = (el.findtext('title') or '').strip()

    # Link : <link>URL</link> en RSS, <link href="URL"/> en Atom
    link = ''
    link_el = el.find('link')
    if link_el is not None:
        link = (link_el.text or link_el.get('href') or '').strip()
    if not link:
        # Atom peut avoir plusieurs <link>, prendre rel="alternate"
        for le in el.findall('link'):
            if le.get('rel', 'alternate') == 'alternate':
                link = (le.get('href') or '').strip()
                if link: break

    # Date
    date_str = (el.findtext('pubDate')
                or el.findtext('published')
                or el.findtext('updated')
                or el.findtext('dc_date')
                or el.findtext('date')
                or '').strip()

    # Description
    description = (el.findtext('description')
                   or el.findtext('summary')
                   or el.findtext('content_encoded')
                   or el.findtext('content')
                   or '').strip()

    # Strip HTML de la description
    description = re.sub(r'<[^>]+>', ' ', description)
    description = html.unescape(description)
    description = re.sub(r'\s+', ' ', description).strip()

    return {
        'title': html.unescape(title)[:300],
        'link': link,
        'date_str': date_str,
        'date_parsed': _parse_date(date_str),
        'description': description[:1500],
    }


def _parse_feed_regex(xml_text, max_items):
    """Fallback regex si l'XML est mal formé."""
    items = []
    # Chercher <item>...</item> ou <entry>...</entry>
    pattern = re.compile(r'<(?:item|entry)\b[^>]*>(.*?)</(?:item|entry)>',
                         re.DOTALL | re.IGNORECASE)
    for m in pattern.finditer(xml_text):
        blob = m.group(1)
        def first(*tags):
            for t in tags:
                mm = re.search(rf'<{t}\b[^>]*>(.*?)</{t}>', blob, re.DOTALL | re.IGNORECASE)
                if mm: return mm.group(1).strip()
            return ''
        link_m = re.search(r'<link[^>]*href=["\']([^"\']+)["\']', blob, re.IGNORECASE)
        link = link_m.group(1) if link_m else first('link')
        description = first('description', 'summary', 'content:encoded', 'content')
        description = re.sub(r'<[^>]+>', ' ', description)
        description = html.unescape(description)
        description = re.sub(r'\s+', ' ', description).strip()
        items.append({
            'title': html.unescape(first('title'))[:300],
            'link': link.strip(),
            'date_str': first('pubDate', 'published', 'updated'),
            'date_parsed': _parse_date(first('pubDate', 'published', 'updated')),
            'description': description[:1500],
        })
        if len(items) >= max_items: break
    return items


def _parse_date(date_str):
    """Parse une date RSS/Atom. Retourne datetime UTC-naive ou None."""
    if not date_str: return None
    s = date_str.strip()
    # Normaliser GMT/UTC
    s = s.replace(' GMT', ' +0000').replace(' UTC', ' +0000')

    formats = [
        '%a, %d %b %Y %H:%M:%S %z',     # RFC 822 avec TZ
        '%a, %d %b %Y %H:%M:%S',         # Sans TZ
        '%Y-%m-%dT%H:%M:%S%z',           # ISO 8601 avec TZ
        '%Y-%m-%dT%H:%M:%S.%f%z',        # ISO 8601 microseconds
        '%Y-%m-%dT%H:%M:%SZ',            # ISO 8601 Z
        '%Y-%m-%dT%H:%M:%S',             # ISO 8601 sans TZ
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            # Normaliser en UTC-naive
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except ValueError:
            continue
    return None


# ═══════════════════════════════════════════════════════════════
#  FETCH (flux + articles)
# ═══════════════════════════════════════════════════════════════

def _fetch_feed(url, max_items=50):
    """Récupère un flux RSS et parse les items. Cache 30 min."""
    with _CACHE_LOCK:
        cached = _FEED_CACHE.get(url)
        if cached and (time.time() - cached[0]) < FEED_CACHE_TTL:
            return cached[1]

    try:
        r = http.get(url,
            headers={'User-Agent': USER_AGENT,
                     'Accept': 'application/rss+xml,application/xml,text/xml,*/*;q=0.5'},
            timeout=FETCH_TIMEOUT, allow_redirects=True)
    except http.exceptions.Timeout:
        raise RuntimeError("timeout")
    except Exception as e:
        raise RuntimeError(f"{type(e).__name__}: {str(e)[:100]}")

    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    if not _looks_like_feed(r.text, r.headers.get('content-type', '')):
        raise RuntimeError("contenu non RSS/Atom (le flux n'est peut-être plus valide)")

    items = _parse_feed(r.text, max_items)
    if not items:
        raise RuntimeError("0 item parsé")

    with _CACHE_LOCK:
        _FEED_CACHE[url] = (time.time(), items)
    return items


def _fetch_article(url):
    """Récupère et nettoie le contenu textuel d'un article.
    Retourne (content, error_or_None)."""
    if not url or not url.startswith(('http://', 'https://')):
        return None, "URL invalide"

    with _CACHE_LOCK:
        cached = _PAGE_CACHE.get(url)
        if cached and (time.time() - cached[0]) < PAGE_CACHE_TTL:
            return cached[1], None

    try:
        r = http.get(url,
            headers={
                'User-Agent': USER_AGENT,
                'Accept': 'text/html,application/xhtml+xml,*/*;q=0.5',
                'Accept-Language': 'fr,en;q=0.7',
            },
            timeout=FETCH_TIMEOUT, allow_redirects=True)
    except http.exceptions.Timeout:
        return None, "timeout"
    except Exception as e:
        return None, f"{type(e).__name__}"

    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"

    ct = (r.headers.get('Content-Type', '') or '').lower()
    if ct and ('html' not in ct and 'xml' not in ct and 'text' not in ct):
        return None, "type non textuel"

    text = r.text or ''
    if not text:
        return None, "page vide"

    # Extraire <article> ou <main>
    main_m = re.search(r'<(?:article|main)\b[^>]*>(.*?)</(?:article|main)>',
                       text, re.DOTALL | re.IGNORECASE)
    if main_m:
        text = main_m.group(1)
    else:
        body_m = re.search(r'<body\b[^>]*>(.*?)</body>', text, re.DOTALL | re.IGNORECASE)
        if body_m: text = body_m.group(1)

    # Retirer les blocs non-contenu
    for tag in ('script', 'style', 'nav', 'footer', 'header', 'aside',
                'form', 'iframe', 'noscript', 'svg', 'button'):
        text = re.sub(rf'<{tag}\b[^>]*>.*?</{tag}>', ' ', text,
                      flags=re.DOTALL | re.IGNORECASE)

    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()

    if len(text) < 100:
        return None, "trop court"

    text = text[:ARTICLE_MAX_CHARS]
    with _CACHE_LOCK:
        _PAGE_CACHE[url] = (time.time(), text)
    return text, None


def _fetch_articles_parallel(items):
    """Fetch en parallèle le contenu complet des articles.
    Modifie items en place : ajoute full_content + fetch_status."""
    if not items: return items
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as ex:
        futures = {ex.submit(_fetch_article, it['link']): i
                   for i, it in enumerate(items) if it.get('link')}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                content, err = fut.result()
                if content:
                    items[i]['full_content'] = content
                    items[i]['fetch_status'] = 'ok'
                    items[i]['fetch_chars'] = len(content)
                else:
                    items[i]['fetch_status'] = err or 'vide'
            except Exception as e:
                items[i]['fetch_status'] = f"erreur: {type(e).__name__}"
    return items


# ═══════════════════════════════════════════════════════════════
#  REGISTER — routes Flask
# ═══════════════════════════════════════════════════════════════

def register(app, rd_cfg):

    def _vault_root():
        return rd_cfg().get('workspace') or str(Path.home())

    @app.route("/api/rss/list", methods=["GET"])
    def rss_list():
        feeds = _load_feeds_from_vault(_vault_root())
        return jsonify({"feeds": feeds, "count": len(feeds),
                        "vault_file": str(Path(_vault_root()) / VAULT_RSS_FILE)})

    @app.route("/api/rss/discover", methods=["POST"])
    def rss_discover():
        d = request.json or {}
        url = (d.get('url') or '').strip()
        if not url:
            return jsonify({"error": "URL manquante"}), 400
        feed_url, name = _discover_feed_url(url)
        if feed_url is None:
            return jsonify({"error": name, "original_url": url}), 404
        return jsonify({"feed_url": feed_url, "name": name, "original_url": url})

    @app.route("/api/rss/add", methods=["POST"])
    def rss_add():
        d = request.json or {}
        url = (d.get('url') or '').strip()
        if not url:
            return jsonify({"error": "URL manquante"}), 400

        # Résoudre l'URL en flux RSS effectif
        feed_url, discovered_name = _discover_feed_url(url)
        if feed_url is None:
            return jsonify({"error": discovered_name}), 404

        name    = (d.get('name') or '').strip() or discovered_name
        tags    = d.get('tags', []) or []
        section = d.get('section')

        vault = _vault_root()
        feeds = _load_feeds_from_vault(vault)
        if any(f['url'] == feed_url for f in feeds):
            return jsonify({"error": "Flux déjà présent", "feed_url": feed_url}), 409

        feeds.append({'name': name, 'url': feed_url, 'tags': tags, 'section': section})
        try:
            _save_feeds_to_vault(vault, feeds)
        except Exception as e:
            return jsonify({"error": f"Écriture impossible : {e}"}), 500

        return jsonify({"ok": True, "feed": {
            "name": name, "url": feed_url, "tags": tags,
            "original_url": url if url != feed_url else None
        }})

    @app.route("/api/rss/remove", methods=["POST"])
    def rss_remove():
        d = request.json or {}
        url = (d.get('url') or '').strip()
        if not url:
            return jsonify({"error": "URL manquante"}), 400
        vault = _vault_root()
        feeds = _load_feeds_from_vault(vault)
        new_feeds = [f for f in feeds if f['url'] != url]
        if len(new_feeds) == len(feeds):
            return jsonify({"error": "Flux non trouvé"}), 404
        _save_feeds_to_vault(vault, new_feeds)
        return jsonify({"ok": True})

    @app.route("/api/rss/fetch", methods=["POST"])
    def rss_fetch():
        d = request.json or {}
        feed_urls   = d.get('feed_urls') or []
        hours_back  = int(d.get('hours_back', 24))     # 0 = pas de filtre
        fetch_full  = bool(d.get('fetch_full', True))
        max_items   = int(d.get('max_items', 10))

        if not feed_urls:
            return jsonify({"error": "Aucun flux sélectionné"}), 400

        all_feeds_meta = {f['url']: f for f in _load_feeds_from_vault(_vault_root())}
        cutoff = datetime.utcnow() - timedelta(hours=hours_back) if hours_back > 0 else None

        # Fetch parallèle des flux
        def _fetch_one(url):
            try:
                return url, _fetch_feed(url, max_items=50), None
            except Exception as e:
                return url, None, str(e)[:200]

        results = []
        all_articles = []
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as ex:
            futs = [ex.submit(_fetch_one, u) for u in feed_urls]
            for fut in as_completed(futs):
                feed_url, items, err = fut.result()
                meta = all_feeds_meta.get(feed_url,
                                          {'name': _domain_name(feed_url), 'url': feed_url, 'tags': []})
                if err:
                    results.append({**meta, 'error': err, 'items': [], 'count': 0})
                    continue
                # Filtrer par date
                if cutoff:
                    items = [it for it in items
                             if it.get('date_parsed') is None or it['date_parsed'] >= cutoff]
                # Limiter
                items = items[:max_items]
                # Marquer la provenance
                for it in items:
                    it['feed_name'] = meta['name']
                    it['feed_url']  = feed_url
                    it['feed_tags'] = meta.get('tags', [])
                results.append({**meta, 'items': items, 'count': len(items)})
                all_articles.extend(items)

        # Fetch parallèle du contenu complet
        fetched_count = 0
        if fetch_full and all_articles:
            all_articles = _fetch_articles_parallel(all_articles)
            fetched_count = sum(1 for it in all_articles if it.get('fetch_status') == 'ok')

        total = sum(len(r.get('items', [])) for r in results)
        return jsonify({
            "feeds": results,
            "total_articles": total,
            "fetched_full": fetched_count,
            "filter_hours": hours_back,
            "fetch_full": fetch_full,
        })

    @app.route("/api/rss/analyze", methods=["POST"])
    def rss_analyze():
        cfg = rd_cfg()
        if not cfg.get("api_key"):
            return jsonify({"error": "Clé API manquante"}), 400

        d = request.json or {}
        feeds_data        = d.get('feeds') or []
        question          = (d.get('question') or '').strip()
        user_sys          = (d.get('system_prompt') or '').strip()
        mode              = (d.get('mode') or 'together').strip()  # 'together' | 'per_feed'
        enable_signals    = bool(d.get('enable_signals', True))    # nouveau toggle

        if not feeds_data:
            return jsonify({"error": "Aucun article à analyser"}), 400

        default_sys = (
            "Tu es analyste expert en veille informationnelle et synthèse de presse. "
            "Tu écris en français, en Markdown structuré, avec rigueur épistémique. "
            "Tu ne paraphrases pas en généralités quand la matière manque — tu le dis."
        )
        system = user_sys or default_sys

        def _articles_md(items, max_per=1500):
            parts = []
            for i, it in enumerate(items):
                full = it.get('full_content')
                if full:
                    body = full[:max_per]
                    label = f"Contenu ({len(full)} chars, tronqué à {max_per})"
                else:
                    body = (it.get('description') or '')[:600]
                    label = f"Description (snippet) — fetch_status: {it.get('fetch_status', 'non récupéré')}"
                # date_parsed peut être un datetime (appel interne) OU une str (round-trip JSON)
                dp = it.get('date_parsed')
                if isinstance(dp, datetime):
                    date_s = dp.strftime('%Y-%m-%d %H:%M')
                elif isinstance(dp, str) and dp:
                    # JSON-sérialisé par Flask : on prend les 16 premiers caractères
                    date_s = dp[:16].replace('T', ' ')
                else:
                    date_s = it.get('date_str') or '?'
                src = it.get('feed_name', '?')
                parts.append(
                    f"### [{i+1}] {it.get('title', '(sans titre)')}\n"
                    f"**Source** : {src} · {date_s}\n"
                    f"**URL** : {it.get('link', '')}\n"
                    f"**{label}** :\n{body}"
                )
            return "\n\n".join(parts)

        # ── MODE 1 : tous flux ensemble ──
        if mode == 'together':
            all_items = []
            for f in feeds_data:
                all_items.extend(f.get('items', []))
            if not all_items:
                return jsonify({"error": "Aucun article dans les flux fournis"}), 400

            # ── ANALYSE LOCALE DES SIGNAUX FAIBLES (avant l'IA) ──
            signals = None
            signals_block_prompt = ""
            if enable_signals:
                feed_urls_set = [f.get('url') for f in feeds_data if f.get('url')]
                prev_run = signal_engine.load_previous_run(feed_urls_set)
                signals = signal_engine.analyze_weak_signals(all_items, previous_run=prev_run)
                signal_engine.save_run(signals, feed_urls_set)
                signals_block_prompt = signal_engine.format_for_prompt(signals)
                print(f"[RSS-signals] {len(signals.get('top_terms', []))} top termes, "
                      f"{len(signals.get('top_bigrams', []))} bigrammes, "
                      f"{len(signals.get('emerging_terms', []))} émergents")

            articles_md = _articles_md(all_items)
            prompt_chars = len(articles_md)
            print(f"[RSS] Analyse 'together' : {len(all_items)} articles, ~{prompt_chars} chars dans le prompt")

            # Construction du prompt avec les signaux en préambule
            signals_intro = ""
            signals_guidance = ""
            if signals and signals.get('top_terms'):
                signals_intro = (
                    f"=== INDICES OBJECTIFS (analyse lexicale locale du corpus) ===\n"
                    f"{signals_block_prompt}\n"
                    f"=== FIN INDICES ===\n\n"
                )
                signals_guidance = (
                    "\n\nLes INDICES OBJECTIFS ci-dessus sont calculés par analyse lexicale locale "
                    "(fréquences, bigrammes, comparaison avec le run précédent). Utilise-les pour :\n"
                    "- ancrer ta synthèse sur des données quantifiées plutôt que ton intuition\n"
                    "- repérer les signaux faibles candidats (termes émergents, associations récurrentes)\n"
                    "- contredire ces indices si l'analyse qualitative montre qu'ils sont trompeurs "
                    "(un mot fréquent peut être du bruit, un émergent peut être un artefact d'un seul article).\n"
                )

            if question:
                user_prompt = (
                    f"{signals_intro}"
                    f"=== OBJECTIF / QUESTION DE L'UTILISATEUR ===\n{question}\n=== FIN ===\n\n"
                    f"Tu vas analyser {len(all_items)} articles issus de {len(feeds_data)} flux RSS. "
                    f"Réponds **précisément** à l'objectif/question ci-dessus. Adapte ta structure à ce qui est demandé. "
                    f"Cite chaque article qui contribue par son numéro [1], [2], etc. "
                    f"Si une source est incomplète, signale-le. Si aucune ne permet de répondre, dis-le explicitement."
                    f"{signals_guidance}\n"
                    f"=== ARTICLES ===\n{articles_md}\n=== FIN ===\n\n"
                    f"Termine par une section **## Sources** listant tous les liens cités."
                )
            else:
                user_prompt = (
                    f"{signals_intro}"
                    f"Voici {len(all_items)} articles issus de {len(feeds_data)} flux RSS.\n\n"
                    f"Produis une synthèse en français (Markdown structuré) qui :\n"
                    f"1. Identifie les **thèmes majeurs** qui ressortent du corpus (appuyés sur les indices objectifs)\n"
                    f"2. Souligne **convergences et contradictions** entre les sources\n"
                    f"3. Interprète les **termes émergents** s'il y en a — donnent-ils un signal réel ou un artefact ?\n"
                    f"4. Évalue la **qualité épistémique** (sources primaires/secondaires, opinions/faits)\n"
                    f"5. Propose 2-3 **angles à creuser** ensuite\n\n"
                    f"Cite chaque source avec son numéro [1], [2], etc.\n"
                    f"Termine par une section **## Sources** listant tous les liens cités."
                    f"{signals_guidance}\n"
                    f"=== ARTICLES ===\n{articles_md}\n=== FIN ==="
                )

            t_ai = time.time()
            synthesis, err = _ai_call(cfg, [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt}
            ], max_tokens=2200, temp=0.5, timeout=180)
            ai_duration = time.time() - t_ai
            print(f"[RSS] Appel IA terminé en {ai_duration:.1f}s — err={err!r}")
            if err: return jsonify({"error": f"IA ({ai_duration:.0f}s) : {err}"}), 500

            return jsonify({
                "mode": "together",
                "synthesis": synthesis,
                "signals": signal_engine.signals_to_client(signals) if signals else None,
                "stats": {
                    "feeds": len(feeds_data),
                    "articles": len(all_items),
                    "with_full_content": sum(1 for it in all_items if it.get('full_content')),
                }
            })

        # ── MODE 2 : flux par flux ──
        # On calcule des signaux GLOBAUX (tous flux confondus) une fois,
        # qu'on injecte en contexte dans chaque appel IA par flux.
        global_signals = None
        global_signals_block = ""
        if enable_signals:
            all_items_global = []
            for f in feeds_data:
                all_items_global.extend(f.get('items', []))
            if all_items_global:
                feed_urls_set = [f.get('url') for f in feeds_data if f.get('url')]
                prev_run = signal_engine.load_previous_run(feed_urls_set)
                global_signals = signal_engine.analyze_weak_signals(all_items_global, previous_run=prev_run)
                signal_engine.save_run(global_signals, feed_urls_set)
                global_signals_block = signal_engine.format_for_prompt(global_signals)
                print(f"[RSS-signals/per_feed] indices globaux calculés sur {len(all_items_global)} articles")

        syntheses = []
        for f in feeds_data:
            items = f.get('items', [])
            if not items:
                syntheses.append({
                    'feed_name': f.get('name'),
                    'feed_url':  f.get('url'),
                    'synthesis': None,
                    'error': 'Aucun article',
                    'article_count': 0,
                })
                continue

            articles_md = _articles_md(items, max_per=1200)

            # Préambule signaux globaux (en contexte)
            signals_intro_pf = ""
            if global_signals_block:
                signals_intro_pf = (
                    f"=== INDICES OBJECTIFS GLOBAUX (tous flux confondus, contexte) ===\n"
                    f"{global_signals_block}\n"
                    f"=== FIN INDICES ===\n\n"
                )

            if question:
                up = (
                    f"{signals_intro_pf}"
                    f"=== OBJECTIF / QUESTION ===\n{question}\n=== FIN ===\n\n"
                    f"Analyse spécifiquement les articles du flux **{f.get('name')}** ({len(items)} articles). "
                    f"Réponds précisément à l'objectif. Cite chaque article par son numéro [1], [2]. "
                    f"Les indices globaux ci-dessus te donnent le contexte de l'actualité — note "
                    f"si ce flux suit le mouvement général ou s'en écarte.\n\n"
                    f"=== ARTICLES ===\n{articles_md}\n=== FIN ==="
                )
            else:
                up = (
                    f"{signals_intro_pf}"
                    f"Synthèse du flux **{f.get('name')}** ({len(items)} articles).\n"
                    f"Markdown structuré, français.\n"
                    f"Identifie : thèmes majeurs, points-clés, signaux faibles, biais éventuels. "
                    f"Compare avec les indices globaux : ce flux est-il aligné ou en décalage avec l'actualité générale ?\n"
                    f"Cite par [N].\n\n"
                    f"=== ARTICLES ===\n{articles_md}\n=== FIN ==="
                )

            t_ai = time.time()
            print(f"[RSS] Per-feed analyse '{f.get('name')}' : {len(items)} articles, ~{len(articles_md)} chars")
            synth, err = _ai_call(cfg, [
                {"role": "system", "content": system},
                {"role": "user", "content": up}
            ], max_tokens=1500, temp=0.5, timeout=150)
            print(f"[RSS] Per-feed '{f.get('name')}' terminé en {time.time()-t_ai:.1f}s")

            syntheses.append({
                'feed_name': f.get('name'),
                'feed_url':  f.get('url'),
                'synthesis': synth if not err else None,
                'error': err,
                'article_count': len(items),
                'with_full_content': sum(1 for it in items if it.get('full_content')),
            })

        return jsonify({
            "mode": "per_feed",
            "syntheses": syntheses,
            "signals": signal_engine.signals_to_client(global_signals) if global_signals else None,
        })

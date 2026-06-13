#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════╗
║  🧠  SECOND BRAIN  v3  —  Éditeur Markdown + IA         ║
╠══════════════════════════════════════════════════════════╣
║  Installation : pip install flask requests               ║
║  Lancement    : python second_brain.py                   ║
║  Navigateur   : http://localhost:5000                    ║
╚══════════════════════════════════════════════════════════╝
Nouveautés v3 :
  - Explorateur corrigé (FILE_ITEMS, onclick inline)
  - Renommage inline (double-clic)
  - 3 onglets mindmap : Structure / Backlinks / Graphe
  - Graphe force-directed des [[wikilinks]]
  - Recherche full-text (Ctrl+Shift+F)
  - Tags #hashtag dans la sidebar
  - IA sur sélection (barre flottante)
  - IA synthèse de dossier
  - Suggestions de [[liens]]
  - Wikilinks cliquables en aperçu
"""

import json, uuid, re, threading, webbrowser, shutil, time, sys, os
from pathlib import Path
from flask import Flask, request, jsonify, Response
import requests as http

app = Flask(__name__)

DATA  = Path.home() / ".secondbrain"
DATA.mkdir(exist_ok=True)
CFG_F = DATA / "config.json"

DEF_CFG = {
    "api_key"  : "",
    "model"    : "gpt-4o",
    "base_url" : "https://fantasyai.cloud/api/v1",
    "workspace": str(Path.home()),
}

# ── Config ─────────────────────────────────────────────────────────────────────

def rd_cfg():
    if CFG_F.exists():
        try: return {**DEF_CFG, **json.loads(CFG_F.read_text())}
        except: pass
    return {**DEF_CFG}

def wr_cfg(data):
    c = rd_cfg(); c.update(data)
    CFG_F.write_text(json.dumps(c, indent=2, ensure_ascii=False))

# ── Variables d'environnement (.env) ────────────────────────────────────────────

def load_env_file(path, override=False):
    """Charge un .env (lignes KEY=VALUE) dans os.environ. Stdlib uniquement.
    Ignore lignes vides et commentaires (#), tolere 'export KEY=val',
    retire les guillemets entourants, n'ecrase pas l'environnement reel par defaut.
    Retourne le nombre de cles chargees."""
    try:
        path = Path(path)
        if not path.exists():
            return 0
        n = 0
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            if override or key not in os.environ:
                os.environ[key] = val
                n += 1
        return n
    except Exception as e:
        print(f"  ⚠ .env ({path}) non charge : {e}")
        return 0

# .env racine du projet, charge au demarrage (avant tout plugin)
load_env_file(Path(__file__).parent / ".env")

# ── Markdown helpers ────────────────────────────────────────────────────────────

def _id(): return str(uuid.uuid4())[:8]
def _node(t, c="", ch=None):
    return {"id": _id(), "title": t, "content": c, "children": ch or [], "collapsed": False}

def extract_wikilinks(content):
    """Compatibilité — délègue à extract_link_refs."""
    return list(extract_link_refs(content))

# Patterns de liens reconnus dans un .md
_RE_WIKI    = re.compile(r"\[\[([^\]|#\n]+?)(?:[|#][^\]\n]*?)?\]\]")
_RE_MDLINK  = re.compile(r"\[[^\]\n]*\]\(\s*([^)\s#?]+?\.md)(?:[#?][^)]*)?\s*\)")
_RE_QUOTED  = re.compile(r"""['"`]([^'"`\n]+?\.md)['"`]""")
_RE_BARE    = re.compile(r"(?:^|[\s,;>(])((?:\.{1,2}/)?[A-Za-z0-9_][A-Za-z0-9_\-./]*\.md)(?=[\s,;:.)]|$)", re.MULTILINE)

def extract_link_refs(content):
    """
    Renvoie l'ensemble des références à d'autres .md trouvées dans le contenu.
    4 patterns reconnus :
      1. [[wikilink]]               (avec |alias et #ancre facultatifs)
      2. [label](path/to/file.md)   (lien Markdown standard)
      3. 'foo.md' "foo.md" `foo.md` (chemins entre guillemets/backticks)
      4. references/file.md         (chemin nu en texte courant)
    """
    refs = set()
    for m in _RE_WIKI.finditer(content):   refs.add(m.group(1).strip())
    for m in _RE_MDLINK.finditer(content):
        v = m.group(1).strip()
        if not v.lower().startswith(("http://","https://")): refs.add(v)
    for m in _RE_QUOTED.finditer(content):
        v = m.group(1).strip()
        if not v.lower().startswith(("http://","https://")): refs.add(v)
    # La regex bare exclut nativement les URLs (contexte préc. interdit / et :)
    for m in _RE_BARE.finditer(content): refs.add(m.group(1).strip())
    return refs

def resolve_ref(raw_ref, source_path, vault_paths):
    """
    Résout une référence brute en chemin réel du vault, ou None.
    Cascade :
      1. Chemin relatif au dossier du fichier source (ex: ../parent/foo.md)
      2. Chemin partiel matchant un suffixe d'un fichier du vault (ex: references/foo.md)
      3. Fuzzy match par nom de fichier (ex: foo → foo.md n'importe où)
    """
    if not raw_ref: return None
    ref = raw_ref.strip().lstrip("/")
    if not ref.lower().endswith(".md"): ref = ref + ".md"
    ref_norm = ref.replace("\\", "/")

    # 1. Relatif au dossier source
    if source_path:
        try:
            cand = (Path(source_path).parent / ref).resolve()
            cand_s = str(cand)
            if cand_s in vault_paths: return cand_s
        except Exception: pass

    # 2. Suffixe (ex: 'references/foo.md' matche '...\skills\X\references\foo.md')
    for vp in vault_paths:
        if vp.replace("\\", "/").lower().endswith("/" + ref_norm.lower()):
            return vp
        if vp.replace("\\", "/").lower().endswith(ref_norm.lower()):
            return vp

    # 3. Fuzzy par nom de fichier
    stem = Path(ref).stem.lower()
    for vp in vault_paths:
        if Path(vp).stem.lower() == stem: return vp
    return None

def extract_tags(content):
    return list({t for t in re.findall(r"(?<!\w)#([a-zA-Z0-9_\-]+)", content)})

def parse_chat_response(text):
    """Parse une réponse chat completion — gère le JSON simple ET le streaming SSE."""
    # 1) JSON direct (non-streaming)
    try:
        d = json.loads(text)
        if isinstance(d, dict) and "choices" in d:
            c = d["choices"][0]
            if "message" in c and "content" in c["message"]: return c["message"]["content"], None
            if "delta"   in c and "content" in c["delta"]:   return c["delta"]["content"], None
        if isinstance(d, dict) and "error" in d:
            return None, f"API: {json.dumps(d['error'])[:300]}"
    except ValueError: pass

    # 2) Streaming SSE : concatène tous les chunks
    parts = []
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("data:"): continue
        body = line[5:].strip()
        if body in ("", "[DONE]"): continue
        try:
            chunk = json.loads(body)
            if "choices" in chunk and chunk["choices"]:
                ch = chunk["choices"][0]
                if "delta" in ch and ch["delta"].get("content"):
                    parts.append(ch["delta"]["content"])
                elif "message" in ch and ch["message"].get("content"):
                    parts.append(ch["message"]["content"])
        except ValueError: continue
    if parts: return "".join(parts), None
    return None, f"Aucun contenu extractible. Début reçu: {text[:300]}"

def _ai_call(cfg, msgs, max_tokens=2000, temp=0.5, timeout=120):
    """
    Appel IA générique — exposé aux plugins.
    Retourne (content, error) où l'un des deux est None.
    """
    key = cfg.get("api_key")
    if not key: return None, "Clé API manquante"
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    try:
        r = http.post(url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": cfg["model"], "messages": msgs,
                  "temperature": temp, "max_tokens": max_tokens, "stream": False}, timeout=timeout)
    except http.exceptions.ReadTimeout:
        return None, (f"Timeout après {timeout}s. Le modèle prend trop de temps à répondre. "
                      f"→ Essayez un modèle plus rapide (gpt-4o-mini) ou réduisez la sortie.")
    except Exception as e:
        return None, f"Réseau : {type(e).__name__}: {str(e)[:200]}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:300]}"
    if not r.text.strip():
        return None, "Réponse vide"
    return parse_chat_response(r.text)

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index(): return Response(PAGE, mimetype="text/html")

@app.route("/api/plugins", methods=["GET"])
def list_plugins():
    """Liste les plugins chargés (pour debug et future UI de gestion)."""
    return jsonify({
        "count": len(LOADED_PLUGINS),
        "plugins": [{
            "name": p["name"],
            "dir": p["dir"],
            "description": p["manifest"].get("description", ""),
            "version": p["manifest"].get("version", "?"),
            "buttons": p["manifest"].get("buttons", []),
        } for p in LOADED_PLUGINS]
    })

@app.route("/api/config", methods=["GET"])
def get_cfg():
    c = rd_cfg()
    return jsonify({**c, "api_key": "●●●" if c.get("api_key") else "", "has_key": bool(c.get("api_key"))})

@app.route("/api/config", methods=["POST"])
def set_cfg(): wr_cfg(request.json); return jsonify({"ok": True})

@app.route("/api/ai", methods=["POST"])
def call_ai():
    cfg = rd_cfg(); key = cfg.get("api_key")
    if not key: return jsonify({"error": "Clé API manquante — configurez-la dans Paramètres."}), 400
    d = request.json
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    try:
        r = http.post(url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": d.get("model", cfg["model"]), "messages": d["messages"],
                  "temperature": 0.72, "max_tokens": 3000, "stream": False}, timeout=90)
    except http.exceptions.Timeout:
        return jsonify({"error": "Timeout — le serveur n'a pas répondu en 90s"}), 504
    except http.exceptions.ConnectionError as e:
        return jsonify({"error": f"Connexion impossible à {url} — {str(e)[:200]}"}), 503
    except Exception as e:
        return jsonify({"error": f"Erreur réseau : {type(e).__name__}: {str(e)[:200]}"}), 500

    if r.status_code != 200:
        return jsonify({"error": f"HTTP {r.status_code}\n{r.text[:400] or '(vide)'}"}), 500
    if not r.text.strip():
        return jsonify({"error": "Réponse vide du serveur"}), 500

    content, err = parse_chat_response(r.text)
    if err: return jsonify({"error": err}), 500
    return jsonify({"response": content})

@app.route("/api/files", methods=["GET"])
def list_files():
    raw = request.args.get("path", "").strip() or rd_cfg().get("workspace", str(Path.home()))
    p = Path(raw)
    try:
        items = sorted(
            [{"name": i.name, "path": str(i), "is_dir": i.is_dir(),
              "is_md": i.suffix.lower() in (".md", ".txt", ".markdown")}
             for i in p.iterdir() if not i.name.startswith(".")],
            key=lambda x: (not x["is_dir"], x["name"].lower())
        )
        return jsonify({"path": str(p), "parent": str(p.parent), "items": items})
    except PermissionError: return jsonify({"error": "Accès refusé"}), 403
    except FileNotFoundError: return jsonify({"error": "Dossier introuvable"}), 404

@app.route("/api/files/read", methods=["GET"])
def read_file():
    try: return jsonify({"content": Path(request.args.get("path", "")).read_text(encoding="utf-8")})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/files/save", methods=["POST"])
def save_file():
    d = request.json
    try:
        Path(d["path"]).write_text(d.get("content", ""), encoding="utf-8")
        return jsonify({"ok": True})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/files/new", methods=["POST"])
def new_file():
    d = request.json
    dir_p = d.get("dir", rd_cfg().get("workspace", str(Path.home())))
    name  = d.get("name", "nouveau.md")
    if not name.endswith(".md"): name += ".md"
    path = Path(dir_p) / name
    if path.exists(): return jsonify({"error": "Fichier existant"}), 409
    try:
        path.write_text(f"# {name.replace('.md','')}\n\n", encoding="utf-8")
        return jsonify({"ok": True, "path": str(path)})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/files/rename", methods=["POST"])
def rename_file():
    d = request.json
    old = Path(d.get("old", "")); new_name = d.get("new_name", "")
    if not old.exists() or not new_name: return jsonify({"error": "Paramètres invalides"}), 400
    new_path = old.parent / new_name
    try: old.rename(new_path); return jsonify({"ok": True, "new_path": str(new_path)})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/files/delete", methods=["POST"])
def delete_file():
    path = request.json.get("path", "")
    try:
        p = Path(path)
        if p.is_dir(): shutil.rmtree(p)
        else: p.unlink()
        return jsonify({"ok": True})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/search", methods=["GET"])
def search_files():
    query = request.args.get("q", "").strip()
    dir_p = request.args.get("dir", "").strip() or rd_cfg().get("workspace", str(Path.home()))
    if len(query) < 2: return jsonify({"results": []})
    results, total = [], 0
    for f in sorted(Path(dir_p).rglob("*.md")):
        try:
            content = f.read_text(encoding="utf-8")
            if query.lower() not in content.lower(): continue
            matches = []
            for i, line in enumerate(content.split("\n")):
                if query.lower() in line.lower():
                    matches.append({"line": i+1, "text": line[:140].strip()})
                    if len(matches) >= 4: break
            results.append({"path": str(f), "name": f.name,
                             "rel": str(f.relative_to(Path(dir_p))), "matches": matches})
            total += 1
            if total >= 40: break
        except: pass
    return jsonify({"results": results})

@app.route("/api/tags", methods=["GET"])
def get_tags():
    dir_p = request.args.get("dir", "").strip() or rd_cfg().get("workspace", str(Path.home()))
    tags = {}
    for f in sorted(Path(dir_p).rglob("*.md")):
        try:
            for tag in extract_tags(f.read_text(encoding="utf-8")):
                tags.setdefault(tag, []).append(str(f))
        except: pass
    return jsonify({"tags": [{"tag": k, "count": len(v), "files": v}
                              for k, v in sorted(tags.items(), key=lambda x: -len(x[1]))]})

@app.route("/api/files/graph", methods=["GET"])
def files_graph():
    dir_p = request.args.get("dir", "").strip() or rd_cfg().get("workspace", str(Path.home()))
    files = sorted(Path(dir_p).rglob("*.md"))
    vault_paths = {str(f) for f in files}
    degree = {str(f): 0 for f in files}
    edges, seen = [], set()
    for f in files:
        try:
            content = f.read_text(encoding="utf-8")
            for ref in extract_link_refs(content):
                target = resolve_ref(ref, str(f), vault_paths)
                if target and target != str(f):
                    key = tuple(sorted([str(f), target]))
                    if key not in seen:
                        seen.add(key); edges.append({"source": str(f), "target": target})
                    degree[str(f)]   = degree.get(str(f),   0) + 1
                    degree[target]   = degree.get(target,   0) + 1
        except: pass
    nodes = [{"id": str(f), "name": f.stem, "path": str(f),
              "degree": degree.get(str(f), 0),
              "rel": str(f.relative_to(dir_p)) if str(f).startswith(dir_p) else f.name}
             for f in files]
    return jsonify({"nodes": nodes, "links": edges})

@app.route("/api/files/backlinks", methods=["GET"])
def backlinks():
    file_path = request.args.get("path", "")
    dir_p     = request.args.get("dir", "").strip() or rd_cfg().get("workspace", str(Path.home()))
    if not file_path: return jsonify({"backlinks": []})
    try:
        target_resolved = str(Path(file_path).resolve())
    except Exception:
        target_resolved = file_path

    vault_paths = {str(f) for f in Path(dir_p).rglob("*.md")}
    results = []
    for f in sorted(Path(dir_p).rglob("*.md")):
        if str(f) == file_path: continue
        try:
            content = f.read_text(encoding="utf-8")
            refs = extract_link_refs(content)
            matched = None
            for ref in refs:
                resolved = resolve_ref(ref, str(f), vault_paths)
                if not resolved: continue
                try: resolved_norm = str(Path(resolved).resolve())
                except: resolved_norm = resolved
                if resolved_norm == target_resolved:
                    matched = ref; break
            if matched:
                # Cherche une ligne contenant la référence pour le contexte
                ctx = ""
                ml = matched.lower()
                for l in content.split("\n"):
                    if ml in l.lower():
                        ctx = l.strip()[:150]; break
                results.append({"path": str(f), "name": f.name, "ctx": ctx, "ref": matched})
        except: pass
    return jsonify({"backlinks": results})

@app.route("/api/ai/folder", methods=["POST"])
def ai_folder():
    cfg = rd_cfg(); key = cfg.get("api_key")
    if not key: return jsonify({"error": "Clé API manquante"}), 400
    d = request.json; dir_p = d.get("dir", "")
    parts, chars = [], 0
    for f in sorted(Path(dir_p).glob("*.md")):
        try:
            txt = f.read_text(encoding="utf-8")
            if chars + len(txt) > 50000: break
            parts.append(f"### {f.name}\n{txt}"); chars += len(txt)
        except: pass
    if not parts: return jsonify({"error": "Aucun .md trouvé"}), 400
    name = Path(dir_p).name
    msgs = [
        {"role": "system", "content": "Tu es un expert en synthèse de connaissances. Analyse ces notes et produis une synthèse structurée en Markdown."},
        {"role": "user",   "content": f"Synthétise le dossier '{name}':\n1. Thèmes principaux\n2. Connexions entre notes\n3. Points clés\n4. Lacunes\n\n---\n" + "\n\n---\n\n".join(parts)}
    ]
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    try:
        r = http.post(url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": d.get("model", cfg["model"]), "messages": msgs,
                  "temperature": 0.7, "max_tokens": 3000, "stream": False}, timeout=120)
    except http.exceptions.Timeout:
        return jsonify({"error": "Timeout (120s)"}), 504
    except Exception as e:
        return jsonify({"error": f"Erreur réseau : {type(e).__name__}: {str(e)[:200]}"}), 500
    if r.status_code != 200:
        return jsonify({"error": f"HTTP {r.status_code}: {r.text[:400] or '(vide)'}"}), 500
    if not r.text.strip():
        return jsonify({"error": "Réponse vide du serveur"}), 500
    content, err = parse_chat_response(r.text)
    if err: return jsonify({"error": err}), 500
    return jsonify({"response": content})

@app.route("/api/files/find", methods=["GET"])
def find_file():
    """Trouve un .md à partir d'une référence brute, avec résolution intelligente."""
    ref      = request.args.get("name", "").strip()
    src      = request.args.get("from", "").strip()
    dir_p    = request.args.get("dir", "").strip() or rd_cfg().get("workspace", str(Path.home()))
    if not ref: return jsonify({"error": "Référence vide"}), 400
    try:
        vault_paths = {str(f) for f in Path(dir_p).rglob("*.md")}
        # 1. Si source connue, résoudre depuis là
        if src:
            resolved = resolve_ref(ref, src, vault_paths)
            if resolved:
                return jsonify({"path": resolved, "content": Path(resolved).read_text(encoding="utf-8")})
        # 2. Sinon, tenter une résolution sans contexte source
        resolved = resolve_ref(ref, "", vault_paths)
        if resolved:
            return jsonify({"path": resolved, "content": Path(resolved).read_text(encoding="utf-8")})
        return jsonify({"error": "Fichier introuvable : " + ref}), 404
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/test", methods=["GET"])
def test_api():
    """Diagnostic — appelle l'API avec un prompt minimal et renvoie tout."""
    cfg = rd_cfg(); key = cfg.get("api_key")
    if not key: return jsonify({"ok": False, "step": "config", "error": "Pas de clé API"})
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    info = {"url": url, "model": cfg.get("model"), "key_prefix": key[:8]+"…"}
    try:
        r = http.post(url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": cfg["model"], "messages": [{"role":"user","content":"Dis bonjour"}],
                  "max_tokens": 30}, timeout=30)
        info["status"]     = r.status_code
        info["headers"]    = dict(r.headers)
        info["body_chars"] = len(r.text)
        info["body_start"] = r.text[:500]
        try:
            j = r.json()
            info["json_keys"] = list(j.keys()) if isinstance(j, dict) else None
            info["json"] = j if len(json.dumps(j)) < 1000 else "(tronqué)"
        except: info["json_error"] = "réponse non-JSON"
        return jsonify({"ok": r.status_code == 200, **info})
    except Exception as e:
        return jsonify({"ok": False, "step": "request", "error": f"{type(e).__name__}: {e}", **info})

@app.route("/api/models", methods=["GET"])
def get_models():
    cfg = rd_cfg()
    if not cfg.get("api_key"): return jsonify({"models": []})
    try:
        r = http.get(cfg["base_url"]+"/models",
            headers={"Authorization": f"Bearer {cfg['api_key']}"}, timeout=10)
        return jsonify({"models": [m["id"] for m in r.json().get("data", [])]})
    except: return jsonify({"models": []})

# ── HTML (embarqué) ────────────────────────────────────────────────────────────

with open(Path(__file__).parent / "ui.html", encoding="utf-8") as _f:
    PAGE_TEMPLATE = _f.read()

# ══════════════════════════════════════════════════
#  PLUGIN LOADER
# ══════════════════════════════════════════════════

PLUGINS_DIR = Path(__file__).parent / "plugins"
LOADED_PLUGINS = []   # liste de dicts avec name, manifest, ui_css, ui_js, ui_html

def load_plugins(flask_app, cfg_reader):
    """
    Découvre et charge tous les plugins du dossier plugins/.
    Chaque sous-dossier avec manifest.json est un plugin.
    """
    import importlib.util
    if not PLUGINS_DIR.exists(): return
    for pdir in sorted(PLUGINS_DIR.iterdir()):
        if not pdir.is_dir() or pdir.name.startswith(("_", ".")): continue
        manifest_p = pdir / "manifest.json"
        if not manifest_p.exists():
            print(f"  ⚠ Plugin '{pdir.name}' : pas de manifest.json — ignoré")
            continue
        try:
            manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ✗ Plugin '{pdir.name}' : manifest invalide ({e})")
            continue

        # .env propre au plugin (cles API, etc.) -> os.environ
        load_env_file(pdir / ".env")
        for _k in manifest.get("env", []):
            if not os.getenv(_k):
                print(f"  ⚠ Plugin '{pdir.name}' : variable manquante : {_k}")

        # Charger le module Python (s'il existe) et appeler register()
        init_p = pdir / "__init__.py"
        if init_p.exists():
            try:
                spec = importlib.util.spec_from_file_location(f"plugins.{pdir.name}", init_p)
                mod  = importlib.util.module_from_spec(spec)
                # Le plugin va importer 'from second_brain import ...' — s'assurer que ce nom est résolvable
                sys.modules.setdefault("second_brain", sys.modules[__name__])
                spec.loader.exec_module(mod)
                if hasattr(mod, "register"):
                    mod.register(flask_app, cfg_reader)
            except Exception as e:
                import traceback; traceback.print_exc()
                print(f"  ✗ Plugin '{pdir.name}' : erreur Python ({e}) — UI désactivée")
                continue

        # Lire les fichiers UI
        def _read(rel):
            p = pdir / rel
            return p.read_text(encoding="utf-8") if p.exists() else ""

        LOADED_PLUGINS.append({
            "name"    : manifest.get("name", pdir.name),
            "dir"     : pdir.name,
            "manifest": manifest,
            "ui_css"  : _read("ui.css"),
            "ui_js"   : _read("ui.js"),
            "ui_html" : _read("ui.html"),
        })
        print(f"  ✓ Plugin chargé : {pdir.name} ({manifest.get('name', '?')})")

def assemble_page():
    """Injecte le contenu des plugins dans le template PAGE."""
    css   = "\n".join(f"/* === plugin: {p['name']} === */\n{p['ui_css']}" for p in LOADED_PLUGINS if p['ui_css'])
    html  = "\n".join(f"<!-- === plugin: {p['name']} === -->\n{p['ui_html']}" for p in LOADED_PLUGINS if p['ui_html'])
    js    = "\n".join(f"// === plugin: {p['name']} ===\n{p['ui_js']}" for p in LOADED_PLUGINS if p['ui_js'])
    btns = []
    for p in LOADED_PLUGINS:
        for b in p["manifest"].get("buttons", []):
            if b.get("panel") == "toolbar":
                btns.append(
                    f'<button class="hbtn" onclick="{b.get("onclick","")}" '
                    f'title="{b.get("title","")}">{b.get("label","")}</button>'
                )
    toolbar_btns = "\n      ".join(btns)
    page = PAGE_TEMPLATE
    page = page.replace("/* PLUGIN_CSS */",                css)
    page = page.replace("<!-- PLUGIN_HTML -->",            html)
    page = page.replace("/* PLUGIN_JS */",                 js)
    page = page.replace("<!-- PLUGIN_TOOLBAR_BUTTONS -->", toolbar_btns)
    return page

# PAGE sera assemblée après le chargement des plugins (dans le main)
PAGE = PAGE_TEMPLATE

# ── Lancement ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("""
  ╔══════════════════════════════════════════╗
  ║   🧠  Second Brain  v3  —  démarrage    ║
  ╠══════════════════════════════════════════╣
  ║   Navigateur : http://localhost:5000     ║
  ║   Ctrl+C pour arrêter                   ║
  ╚══════════════════════════════════════════╝
""")
    print("→ Chargement des plugins…")
    load_plugins(app, rd_cfg)
    PAGE = assemble_page()
    print(f"→ {len(LOADED_PLUGINS)} plugin(s) actif(s)\n")
    threading.Timer(1.6, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(port=5000, debug=False)

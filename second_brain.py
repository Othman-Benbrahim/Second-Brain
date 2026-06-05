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

import json, uuid, re, threading, webbrowser, shutil
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

# ── Markdown helpers ────────────────────────────────────────────────────────────

def _id(): return str(uuid.uuid4())[:8]
def _node(t, c="", ch=None):
    return {"id": _id(), "title": t, "content": c, "children": ch or [], "collapsed": False}

def extract_wikilinks(content):
    return list({m.group(1).split("|")[0].strip().lower()
                 for m in re.finditer(r"\[\[([^\]]+)\]\]", content)})

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

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index(): return Response(PAGE, mimetype="text/html")

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
    for f in sorted(Path(dir_p).glob("*.md")):
        try:
            for tag in extract_tags(f.read_text(encoding="utf-8")):
                tags.setdefault(tag, []).append(str(f))
        except: pass
    return jsonify({"tags": [{"tag": k, "count": len(v), "files": v}
                              for k, v in sorted(tags.items(), key=lambda x: -len(x[1]))]})

@app.route("/api/files/graph", methods=["GET"])
def files_graph():
    dir_p = request.args.get("dir", "").strip() or rd_cfg().get("workspace", str(Path.home()))
    p = Path(dir_p); files = sorted(p.glob("*.md"))
    file_map = {f.stem.lower(): str(f) for f in files}
    degree = {str(f): 0 for f in files}
    edges, seen = [], set()
    for f in files:
        try:
            for link in extract_wikilinks(f.read_text(encoding="utf-8")):
                target = file_map.get(link)
                if target and target != str(f):
                    key = tuple(sorted([str(f), target]))
                    if key not in seen:
                        seen.add(key); edges.append({"source": str(f), "target": target})
                    degree[str(f)] = degree.get(str(f), 0) + 1
                    degree[target] = degree.get(target, 0) + 1
        except: pass
    nodes = [{"id": str(f), "name": f.stem, "path": str(f), "degree": degree.get(str(f), 0)}
             for f in files]
    return jsonify({"nodes": nodes, "links": edges})

@app.route("/api/files/backlinks", methods=["GET"])
def backlinks():
    file_path = request.args.get("path", "")
    dir_p     = request.args.get("dir", "").strip() or rd_cfg().get("workspace", str(Path.home()))
    if not file_path: return jsonify({"backlinks": []})
    target = Path(file_path).stem.lower(); results = []
    for f in sorted(Path(dir_p).glob("*.md")):
        if str(f) == file_path: continue
        try:
            content = f.read_text(encoding="utf-8")
            if target in extract_wikilinks(content):
                ctx = next((l.strip()[:120] for l in content.split("\n")
                            if f"[[{target}" in l.lower()), "")
                results.append({"path": str(f), "name": f.name, "ctx": ctx})
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
    name = request.args.get("name", "").lower().strip()
    dir_p = request.args.get("dir", "").strip() or rd_cfg().get("workspace", str(Path.home()))
    try:
        for f in sorted(Path(dir_p).rglob("*.md")):
            if f.stem.lower() == name:
                return jsonify({"path": str(f), "content": f.read_text(encoding="utf-8")})
        return jsonify({"error": "not found"}), 404
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
    PAGE = _f.read()

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
    threading.Timer(1.6, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(port=5000, debug=False)

"""
Plugin Context Builder — sélection multi-fichiers, résolution optionnelle des liens,
envoi du corpus assemblé à l'IA avec une question libre.

Routes exposées :
  GET  /api/context/list   — liste tous les .md du workspace
  POST /api/context/build  — assemble le markdown du corpus (avec liens si depth>0)
  POST /api/context/ask    — pose une question à l'IA sur le corpus
"""
from pathlib import Path
from flask import request, jsonify

# Helpers du core
from second_brain import _ai_call, extract_link_refs, resolve_ref


def _gather_files(selected_paths, depth, vault_paths):
    """
    À partir d'une sélection initiale, suit les liens jusqu'à `depth` niveaux.
    Retourne une liste ordonnée (sélection initiale d'abord, puis BFS des liens).
    """
    result = list(selected_paths)
    seen   = set(result)
    if depth <= 0: return result
    current = list(selected_paths)
    for _ in range(depth):
        next_level = []
        for p in current:
            try:
                content = Path(p).read_text(encoding="utf-8")
            except Exception:
                continue
            for ref in extract_link_refs(content):
                resolved = resolve_ref(ref, p, vault_paths)
                if resolved and resolved not in seen:
                    seen.add(resolved)
                    result.append(resolved)
                    next_level.append(resolved)
        if not next_level: break
        current = next_level
    return result


def _assemble_context(paths, vault_dir):
    """Concatène le contenu de plusieurs fichiers en un Markdown unifié."""
    parts, files_info = [], []
    for p in paths:
        try:
            content = Path(p).read_text(encoding="utf-8")
            try:    rel = str(Path(p).relative_to(vault_dir))
            except: rel = Path(p).name
            parts.append(f"\n\n=== {rel} ===\n\n{content}")
            files_info.append({"path": p, "rel": rel, "size": len(content)})
        except Exception as e:
            parts.append(f"\n\n=== {p} (ERREUR) ===\n\n[Lecture impossible : {e}]")
    return "".join(parts), files_info


def register(app, rd_cfg):

    @app.route("/api/context/list", methods=["GET"])
    def context_list():
        """Liste tous les .md du workspace avec taille."""
        dir_p = request.args.get("dir", "").strip() or rd_cfg().get("workspace", str(Path.home()))
        files = []
        for f in sorted(Path(dir_p).rglob("*.md")):
            try:    size = f.stat().st_size
            except: size = 0
            try:    rel  = str(f.relative_to(dir_p))
            except: rel  = f.name
            files.append({"path": str(f), "name": f.name, "rel": rel, "size": size})
        return jsonify({"files": files, "count": len(files)})

    @app.route("/api/context/build", methods=["POST"])
    def context_build():
        """Assemble le contexte markdown (avec résolution des liens si depth>0)."""
        d     = request.json
        paths = d.get("paths", [])
        depth = int(d.get("depth", 0))
        dir_p = d.get("dir") or rd_cfg().get("workspace", str(Path.home()))
        if not paths: return jsonify({"error": "Aucun fichier sélectionné"}), 400

        vault_paths = {str(f) for f in Path(dir_p).rglob("*.md")}
        all_paths = _gather_files(paths, depth, vault_paths)
        markdown, files = _assemble_context(all_paths, dir_p)
        return jsonify({
            "markdown":     markdown,
            "files":        files,
            "total_chars":  len(markdown),
            "files_count":  len(files),
            "auto_added":   len(files) - len(paths),
        })

    @app.route("/api/context/ask", methods=["POST"])
    def context_ask():
        """Pose une question à l'IA en utilisant le corpus assemblé comme contexte."""
        cfg = rd_cfg()
        if not cfg.get("api_key"): return jsonify({"error": "Clé API manquante"}), 400

        d           = request.json
        paths       = d.get("paths", [])
        depth       = int(d.get("depth", 0))
        question    = (d.get("question", "") or "").strip()
        sys_prompt  = (d.get("system_prompt", "") or "").strip()
        dir_p       = d.get("dir") or rd_cfg().get("workspace", str(Path.home()))

        if not paths:    return jsonify({"error": "Aucun fichier sélectionné"}), 400
        if not question: return jsonify({"error": "Question vide"}), 400

        vault_paths = {str(f) for f in Path(dir_p).rglob("*.md")}
        all_paths   = _gather_files(paths, depth, vault_paths)
        markdown, files = _assemble_context(all_paths, dir_p)

        # Garde-fou taille (≈ 15k tokens en entrée, modèles standards)
        MAX_CHARS = 60000
        truncated = False
        if len(markdown) > MAX_CHARS:
            markdown = markdown[:MAX_CHARS] + f"\n\n[… contexte tronqué — {len(files)} fichiers totaux, {len(markdown)-MAX_CHARS} chars omis]"
            truncated = True

        default_sys = ("Tu es un assistant qui analyse un corpus de notes Markdown personnelles. "
                       "Tu réponds en français, avec rigueur et concision, en citant les fichiers pertinents "
                       "via [[nom_du_fichier]] quand c'est utile.")
        system = sys_prompt or default_sys

        user_prompt = (
            f"Voici un corpus de {len(files)} fichier(s) .md de mon vault :\n"
            f"{markdown}\n\n"
            f"=== QUESTION ===\n\n{question}"
        )

        answer, err = _ai_call(cfg, [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_prompt}
        ], max_tokens=2500, temp=0.5, timeout=240)

        if err: return jsonify({"error": err}), 500
        return jsonify({
            "answer": answer,
            "files_count": len(files),
            "truncated": truncated,
            "files_used": [f["rel"] for f in files],
        })

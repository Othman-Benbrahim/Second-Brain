"""Moteur léger de détection de signaux faibles.

Inspiré de WeakSignalFinder (https://github.com/LittleViewer/WeakSignalFinder) mais
allégé pour rester dans la philosophie "zéro dépendance lourde" du projet :
- Pas de spaCy, pas de NLTK, pas de scikit-learn
- Regex + Counter + stopwords français/anglais embarqués
- Historique sur disque (~/.secondbrain/rss_signals_history.json)

Pipeline :
  1. Tokenisation (mots de 4+ caractères, lettres + accents FR)
  2. Suppression stopwords + bruit web
  3. Comptage des fréquences unitaires
  4. Comptage des bigrammes adjacents
  5. Comparaison avec le run précédent → termes émergents
  6. Persistance pour la comparaison suivante

L'objectif est de produire des indices OBJECTIFS (chiffrés) qui nourrissent ensuite
l'analyse IA, plutôt que de laisser l'IA "deviner" les signaux faibles.
"""
import re, json
from collections import Counter
from datetime import datetime
from pathlib import Path

# ── Stopwords FR + EN (embarqués pour zéro dépendance) ────────────
STOPWORDS_FR = {
    'alors','ainsi','apres','aucun','aucune','aujourd','aupres','aussi','autant',
    'autre','autres','avant','avec','avoir','beaucoup','bien','cela','celle',
    'celles','celui','cependant','certain','certaine','certaines','certains',
    'certes','cette','ceux','chaque','chez','comme','comment','dans','depuis',
    'derriere','dessous','dessus','deux','devant','devra','doit','donc','dont',
    'duquel','elle','elles','encore','entre','etaient','etais','etait','etant',
    'etant','etant','etat','etre','faire','fait','faut','grand','grande','grands',
    'haut','hors','jamais','jusqu','jusque','lequel','leur','leurs','lorsque',
    'mais','meme','memes','mieux','moins','mois','mon','notre','nous','nouveau',
    'nouvelle','nouvelles','nouveaux','oui','par','parce','parmi','partout','pas',
    'pendant','peut','peuvent','plus','plutot','pour','pourquoi','pourrait',
    'pres','presque','puis','puisque','quand','quel','quelle','quelles','quels',
    'quoi','sans','selon','sera','seront','serait','ses','seulement','soit',
    'son','sont','sous','souvent','suis','sur','tandis','tant','tellement','tels',
    'temps','toujours','tous','tout','toute','toutes','trois','tres','trop','une',
    'uns','vers','voici','voila','vont','votre','vous','etre','avoir','faire',
    'aux','des','les','par','que','qui','des','est','sont','ete','etait',
    'celui-ci','celui-la','celle-ci','celle-la','aujourdhui','peut-etre',
}
STOPWORDS_EN = {
    'about','above','after','again','against','all','any','are','because','been',
    'before','being','below','between','both','but','can','could','did','does',
    'doing','done','down','during','each','few','for','from','further','had',
    'has','have','having','her','here','hers','herself','him','himself','his',
    'how','into','its','itself','just','more','most','myself','nor','not','now',
    'off','once','only','other','our','ours','ourselves','out','over','own',
    'same','she','should','some','such','than','that','the','their','theirs',
    'them','themselves','then','there','these','they','this','those','through',
    'too','under','until','very','was','were','what','when','where','which',
    'while','who','whom','why','will','with','would','you','your','yours',
    'yourself','yourselves','also','etc','com','www','http','https',
}
# Bruit web typique
NOISE = {
    'article','articles','lire','voir','cliquez','abonnez','newsletter','cookies',
    'consentement','publicite','partager','partagez','commentaire','commentaires',
    'lecture','accueil','menu','recherche','contact','accept','accepter','navigation',
    'reuters','afp','source','sources','agence','agences','presse',
    'video','videos','photo','photos','photographie','photographies',
    'apres','avant','plus','depuis','vers','selon','suite','grace','permet',
    'permettre','permettant','meme','memes','autre','autres','annee','annees',
    'jour','jours','semaine','semaines','mois','heure','heures',
    'matin','soir','midi','aujourd',
    'monde','france','francais','francaise','francaises',
    'million','millions','milliard','milliards','euro','euros','dollar','dollars',
    'pour','dans','sur','par','aux','avec','sans','sous','vers','chez',
}
STOPWORDS = STOPWORDS_FR | STOPWORDS_EN | NOISE

# ── Tokenizer ─────────────────────────────────────────────────────
# Lettres latines + accents français, mots de 4+ caractères
_RE_TOKEN = re.compile(r'[a-zà-ÿœæ]{4,}', re.UNICODE)

def _tokenize(text):
    """Tokenise un texte en mots significatifs."""
    if not text: return []
    text = text.lower()
    words = _RE_TOKEN.findall(text)
    return [w for w in words if w not in STOPWORDS]


def _extract_text(article):
    """Concatène titre + description + contenu complet d'un article."""
    return ' '.join([
        article.get('title') or '',
        article.get('description') or '',
        article.get('full_content') or '',
    ])


def _compute_bigrams(tokens):
    """Bigrammes adjacents (deux mots qui se suivent)."""
    return [f"{tokens[i]} {tokens[i+1]}" for i in range(len(tokens)-1)]


# ══════════════════════════════════════════════════════════════
#  ANALYSE PRINCIPALE
# ══════════════════════════════════════════════════════════════

def analyze_weak_signals(articles, previous_run=None, top_n=15):
    """Analyse lexicale légère pour repérer les signaux faibles.

    Args:
        articles: liste d'articles {title, description, full_content, ...}
        previous_run: dict d'un run précédent (issu de load_previous_run),
                      utilisé pour détecter les termes émergents.
        top_n: nombre maximum d'éléments dans chaque catégorie de sortie.

    Returns:
        dict avec:
        - top_terms: liste de (terme, count) les plus fréquents
        - top_bigrams: liste de (bigramme, count), uniquement ceux >= 2 occurrences
        - emerging_terms: liste de {term, current, previous, status}
                          status ∈ {'nouveau', 'en hausse'}
        - article_count: nombre d'articles analysés
        - total_words: nombre total de mots significatifs
        - _term_freqs_full: dict des 200 termes les plus fréquents,
                            pour la prochaine comparaison (NE PAS afficher)
    """
    if not articles:
        return _empty_result()

    all_tokens = []
    all_bigrams = []

    for art in articles:
        text = _extract_text(art)
        tokens = _tokenize(text)
        all_tokens.extend(tokens)
        all_bigrams.extend(_compute_bigrams(tokens))

    if not all_tokens:
        return _empty_result(article_count=len(articles))

    term_counts = Counter(all_tokens)
    bigram_counts = Counter(all_bigrams)

    # Top termes (uniquement ceux >= 2 occurrences pour éviter le bruit)
    top_terms = [(t, c) for t, c in term_counts.most_common(top_n * 2) if c >= 2][:top_n]

    # Top bigrammes (uniquement ceux >= 2 occurrences)
    top_bigrams = [(b, c) for b, c in bigram_counts.most_common(top_n) if c >= 2]

    # Termes émergents (comparaison avec run précédent)
    emerging_terms = []
    if previous_run and isinstance(previous_run, dict):
        prev_freqs = previous_run.get('term_freqs', {}) or {}
        if prev_freqs:
            # Examiner les top 3*top_n termes actuels
            for term, current_count in term_counts.most_common(top_n * 3):
                if current_count < 3:
                    continue  # Trop peu pour parler de signal
                prev_count = prev_freqs.get(term, 0)
                if prev_count == 0:
                    emerging_terms.append({
                        'term': term, 'current': current_count,
                        'previous': 0, 'status': 'nouveau'
                    })
                elif current_count >= prev_count * 2.5:
                    emerging_terms.append({
                        'term': term, 'current': current_count,
                        'previous': prev_count, 'status': 'en hausse'
                    })
            emerging_terms = emerging_terms[:top_n]

    return {
        'top_terms':       top_terms,
        'top_bigrams':     top_bigrams,
        'emerging_terms':  emerging_terms,
        'article_count':   len(articles),
        'total_words':     len(all_tokens),
        '_term_freqs_full': dict(term_counts.most_common(200)),
    }


def _empty_result(article_count=0):
    return {
        'top_terms': [], 'top_bigrams': [], 'emerging_terms': [],
        'article_count': article_count, 'total_words': 0,
        '_term_freqs_full': {},
    }


# ══════════════════════════════════════════════════════════════
#  PERSISTANCE — pour la détection d'émergence inter-runs
# ══════════════════════════════════════════════════════════════

def _history_file():
    return Path.home() / ".secondbrain" / "rss_signals_history.json"


def save_run(signals, feed_urls):
    """Sauvegarde le run actuel pour permettre la comparaison future."""
    f = _history_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    runs = []
    if f.exists():
        try: runs = json.loads(f.read_text(encoding='utf-8')) or []
        except Exception: runs = []
    runs.append({
        'timestamp': datetime.utcnow().isoformat(timespec='seconds'),
        'feed_urls': sorted(feed_urls or []),
        'term_freqs': signals.get('_term_freqs_full', {}),
        'article_count': signals.get('article_count', 0),
    })
    runs = runs[-30:]  # garder les 30 derniers runs max
    try:
        f.write_text(json.dumps(runs, ensure_ascii=False), encoding='utf-8')
    except Exception as e:
        print(f"[RSS-signals] Impossible de sauvegarder l'historique : {e}")


def load_previous_run(feed_urls=None):
    """Charge le run précédent le plus pertinent.
    Si feed_urls est fourni, cherche un run avec un overlap >= 50% des flux.
    Sinon, retourne le run le plus récent."""
    f = _history_file()
    if not f.exists(): return None
    try:
        runs = json.loads(f.read_text(encoding='utf-8'))
    except Exception:
        return None
    if not runs: return None

    if feed_urls:
        target = set(feed_urls)
        # Parcourir du plus récent au plus ancien
        for run in reversed(runs):
            run_feeds = set(run.get('feed_urls', []))
            if not run_feeds: continue
            overlap = len(target & run_feeds) / max(len(target), len(run_feeds))
            if overlap >= 0.5:
                return run
    # Fallback : le plus récent tout court
    return runs[-1] if runs else None


# ══════════════════════════════════════════════════════════════
#  FORMATAGE pour le prompt IA et l'UI
# ══════════════════════════════════════════════════════════════

def format_for_prompt(signals):
    """Génère un bloc texte compact à injecter dans le prompt IA."""
    if not signals or not signals.get('top_terms'):
        return "(analyse lexicale locale indisponible — corpus trop court)"

    lines = []
    lines.append(f"Statistiques : {signals['article_count']} articles, "
                 f"{signals['total_words']} mots significatifs après filtrage.")
    lines.append("")

    if signals['top_terms']:
        lines.append("**Termes les plus fréquents** :")
        lines.append(", ".join(f"{t} ({c})" for t, c in signals['top_terms']))
        lines.append("")

    if signals['top_bigrams']:
        lines.append("**Associations récurrentes (bigrammes ≥ 2 occurrences)** :")
        lines.append(", ".join(f"`{b}` ({c})" for b, c in signals['top_bigrams']))
        lines.append("")

    if signals['emerging_terms']:
        lines.append("**Termes émergents (vs. run précédent)** :")
        for e in signals['emerging_terms']:
            if e['status'] == 'nouveau':
                lines.append(f"- `{e['term']}` : NOUVEAU ({e['current']} occurrences)")
            else:
                lines.append(f"- `{e['term']}` : {e['previous']} → {e['current']} "
                             f"(×{e['current']/max(e['previous'],1):.1f})")
        lines.append("")
    else:
        lines.append("*Aucun terme émergent détecté (premier run ou pas d'émergence significative).*")

    return "\n".join(lines)


def format_for_markdown(signals):
    """Génère un bloc Markdown propre pour la note de sortie."""
    if not signals or not signals.get('top_terms'):
        return "## 🔬 Signaux faibles détectés localement\n\n*Analyse indisponible — corpus trop court.*\n"

    lines = ["## 🔬 Signaux faibles détectés localement", ""]
    lines.append(f"*Analyse lexicale sur {signals['article_count']} articles, "
                 f"{signals['total_words']} mots significatifs.*")
    lines.append("")

    if signals['emerging_terms']:
        lines.append("### ⚡ Termes émergents")
        lines.append("*Comparaison avec le run précédent.*")
        lines.append("")
        for e in signals['emerging_terms']:
            if e['status'] == 'nouveau':
                lines.append(f"- **`{e['term']}`** : NOUVEAU ({e['current']} occurrences)")
            else:
                ratio = e['current'] / max(e['previous'], 1)
                lines.append(f"- **`{e['term']}`** : {e['previous']} → {e['current']} occurrences (×{ratio:.1f})")
        lines.append("")
    else:
        lines.append("### ⚡ Termes émergents")
        lines.append("*Aucune émergence significative (ou premier run sur ces flux).*")
        lines.append("")

    if signals['top_terms']:
        lines.append("### 📊 Termes les plus fréquents")
        lines.append(", ".join(f"`{t}` ({c})" for t, c in signals['top_terms']))
        lines.append("")

    if signals['top_bigrams']:
        lines.append("### 🔗 Associations récurrentes")
        lines.append(", ".join(f"`{b}` ({c})" for b, c in signals['top_bigrams']))
        lines.append("")

    return "\n".join(lines)


def signals_to_client(signals):
    """Prépare la version envoyée au frontend (sans _term_freqs_full)."""
    if not signals: return None
    return {
        'top_terms':      signals.get('top_terms', []),
        'top_bigrams':    signals.get('top_bigrams', []),
        'emerging_terms': signals.get('emerging_terms', []),
        'article_count':  signals.get('article_count', 0),
        'total_words':    signals.get('total_words', 0),
    }

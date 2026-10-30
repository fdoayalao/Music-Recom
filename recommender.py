import google.generativeai as genai
import json
import os
import requests
import random
from dotenv import load_dotenv

load_dotenv()

def normalize_name(name):
    """Normaliza un nombre de artista para comparaciones estrictas e infalibles."""
    n = str(name).lower().strip()
    if n.startswith("the "):
        n = n[4:]
    return "".join(c for c in n if c.isalnum() or c.isspace()).strip()

BAND_SUFFIXES = [
    'experiment', 'trio', 'quartet', 'quintet', 'sextet', 'octet',
    'band', 'orchestra', 'arkestra', 'collective', 'project', 'ensemble',
    'group', 'sound system', 'big band', 'syndicate'
]

def get_artist_variations(name):
    """
    Genera un conjunto de variantes canónicas de un artista para comparaciones robustas:
    - Normalización básica.
    - Remoción de sufijos de bandas/ensambles ('experiment', 'trio', etc.).
    - Descomposición de colaboraciones ('&', 'feat.', 'with', etc.).
    """
    if not name:
        return set()
    norm = normalize_name(name)
    if not norm:
        return set()
    variants = {norm}
    
    # Remoción de sufijos
    for suff in BAND_SUFFIXES:
        if norm.endswith(' ' + suff):
            base = norm[:-len(suff)-1].strip()
            if len(base) >= 3:
                variants.add(base)
                
    # Descomposición de colaboraciones
    import re
    parts = re.split(r'\s+(?:&|and|feat\.?|ft\.?|featuring|with|x)\s+|,', str(name), flags=re.IGNORECASE)
    for p in parts:
        p_norm = normalize_name(p)
        if len(p_norm) >= 3:
            variants.add(p_norm)
            for suff in BAND_SUFFIXES:
                if p_norm.endswith(' ' + suff):
                    base = p_norm[:-len(suff)-1].strip()
                    if len(base) >= 3:
                        variants.add(base)
                        
    return variants

def is_seed_conflict(cand_name, seed_name):
    """
    Detecta de forma infalible si cand_name corresponde al artista semilla,
    sea en solitario, proyecto paralelo, featuring o colaboración.
    """
    if not seed_name or not cand_name:
        return False
    import re
    s_norm = normalize_name(seed_name)
    c_norm = normalize_name(cand_name)
    if not s_norm or not c_norm:
        return False
    if s_norm == c_norm:
        return True
        
    pattern_s = r'\b' + re.escape(s_norm) + r'\b'
    pattern_c = r'\b' + re.escape(c_norm) + r'\b'
    if re.search(pattern_s, c_norm) or re.search(pattern_c, s_norm):
        return True
        
    c_vars = get_artist_variations(cand_name)
    if s_norm in c_vars:
        return True
        
    return False

def is_artist_blocked(cand_name, blocked_pool):
    """
    Verifica si el artista o cualquiera de sus variantes (incluyendo proyectos
    derivados como 'Robert Glasper Experiment') figura en el conjunto de bloqueo.
    """
    if not cand_name or not blocked_pool:
        return False
    vars = get_artist_variations(cand_name)
    return any(v in blocked_pool for v in vars)

def get_lastfm_multi_seed_candidates(seed_artists, api_key, blocked_set, limit=35):
    """
    Algoritmo de Intersección Multi-Semilla (Puentes Sonoros):
    1. Para cada artista semilla reciente, consulta 100 similares en Last.fm con su match score.
    2. Descarta inmediatamente en Python cualquier artista que exista en blocked_set (historia + diario).
    3. Agrupa por candidato. Si aparece en múltiples semillas, calcula:
       affinity_score = sum(matches) * (1 + 0.35 * (count_seeds - 1))
    4. Retorna el Top candidatos ordenados por affinity_score descendente con sus artistas puente.
    """
    candidates = {} # norm_name: {'name': original_name, 'matches': [], 'seeds': []}
    
    for seed in seed_artists[:8]:
        try:
            url = f"http://ws.audioscrobbler.com/2.0/?method=artist.getsimilar&artist={requests.utils.quote(seed)}&api_key={api_key}&format=json&limit=100"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                similars = data.get('similarartists', {}).get('artist', [])
                for sim in similars:
                    name = sim.get('name')
                    try:
                        match_val = float(sim.get('match', 0.0))
                    except (ValueError, TypeError):
                        match_val = 0.0
                        
                    if not name or match_val <= 0.0:
                        continue
                        
                    norm = normalize_name(name)
                    # FILTRO DURO EN PYTHON: 0 plays en historia y nunca antes recomendado
                    if is_artist_blocked(name, blocked_set):
                        continue
                        
                    if norm not in candidates:
                        candidates[norm] = {
                            'name': name,
                            'matches': [],
                            'seeds': []
                        }
                    candidates[norm]['matches'].append(match_val)
                    candidates[norm]['seeds'].append(seed)
        except Exception:
            continue

    scored_candidates = []
    for norm, info in candidates.items():
        count_seeds = len(info['seeds'])
        affinity_score = sum(info['matches']) * (1.0 + 0.35 * (count_seeds - 1))
        scored_candidates.append({
            'name': info['name'],
            'affinity_score': affinity_score,
            'seeds': info['seeds']
        })

    scored_candidates.sort(key=lambda x: x['affinity_score'], reverse=True)
    return scored_candidates[:limit]

def fetch_artist_top_tracks(artist_name, api_key, limit=2):
    """
    Obtiene los tracks oficiales más reproducidos de un artista en Last.fm con retry
    y backoff para protegerse de micro-cortes y rate limits (límite de ~5 req/s).
    """
    import time
    tracks = []
    if not artist_name or not api_key:
        return tracks
        
    url = f"http://ws.audioscrobbler.com/2.0/?method=artist.gettoptracks&artist={requests.utils.quote(artist_name)}&api_key={api_key}&format=json&limit={limit}"
    for attempt in range(2):
        try:
            resp = requests.get(url, timeout=3.5)
            if resp.status_code == 200:
                raw_tracks = resp.json().get('toptracks', {}).get('track', [])
                if isinstance(raw_tracks, dict):
                    raw_tracks = [raw_tracks]
                for t in raw_tracks:
                    t_name = t.get('name')
                    if t_name and t_name not in tracks:
                        tracks.append(t_name)
                if tracks:
                    break
            elif resp.status_code == 429:
                time.sleep(0.2)
        except Exception:
            time.sleep(0.15)
    return tracks[:limit]

def enrich_candidates_with_top_tracks(candidates, api_key, max_candidates=25, tracks_per_artist=2):
    """
    Toma los mejores candidatos y consulta en paralelo sus canciones más representativas
    en Last.fm utilizando max_workers=4 para respetar estrictamente el umbral de ~5 req/s.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    top_cands = candidates[:max_candidates]
    if not api_key or not top_cands:
        return top_cands
        
    enriched = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_cand = {
            executor.submit(fetch_artist_top_tracks, c['name'], api_key, tracks_per_artist): c
            for c in top_cands
        }
        for future in as_completed(future_to_cand):
            c = future_to_cand[future]
            try:
                tracks = future.result()
            except Exception:
                tracks = []
            c_copy = dict(c)
            c_copy['top_tracks'] = tracks
            enriched.append(c_copy)
            
    # Restaurar orden original de afinidad
    name_order = {c['name']: idx for idx, c in enumerate(top_cands)}
    enriched.sort(key=lambda x: name_order.get(x['name'], 999))
    return enriched

def clean_html(text):
    """Limpia etiquetas HTML y enlaces de textos de APIs."""
    import re
    if not text:
        return ""
    clean = re.sub(r'<a\b[^>]*>(.*?)</a>', '', text, flags=re.IGNORECASE)
    clean = re.sub(r'<[^>]+>', '', clean)
    return " ".join(clean.split()).strip()

def profile_seed_artist(seed_artist, lastfm_api_key):
    """
    Realiza una radiografía completa del artista semilla en Last.fm:
    - Biografía y contexto (integrantes, origen, proyectos paralelos).
    - Etiquetas / Microgéneros.
    - Canciones canónicas más escuchadas.
    - Artistas similares de la escena.
    """
    profile = {
        'bio': '',
        'tags': [],
        'top_tracks': [],
        'similar_peers': []
    }
    if not lastfm_api_key or not seed_artist:
        return profile
        
    try:
        url_info = f"http://ws.audioscrobbler.com/2.0/?method=artist.getinfo&artist={requests.utils.quote(seed_artist)}&api_key={lastfm_api_key}&format=json"
        resp_info = requests.get(url_info, timeout=5)
        if resp_info.status_code == 200:
            d = resp_info.json().get('artist', {})
            raw_bio = d.get('bio', {}).get('summary', '')
            profile['bio'] = clean_html(raw_bio)
            
            tags_list = d.get('tags', {}).get('tag', [])
            profile['tags'] = [t['name'] for t in tags_list if t.get('name')][:6]
            
            sim_list = d.get('similar', {}).get('artist', [])
            profile['similar_peers'] = [
                s['name'] for s in sim_list 
                if s.get('name') and not is_seed_conflict(s['name'], seed_artist)
            ][:8]
    except Exception as e:
        print(f"Aviso: Error obteniendo info de Last.fm para '{seed_artist}': {e}")
        
    try:
        url_tracks = f"http://ws.audioscrobbler.com/2.0/?method=artist.gettoptracks&artist={requests.utils.quote(seed_artist)}&api_key={lastfm_api_key}&format=json&limit=5"
        resp_tracks = requests.get(url_tracks, timeout=5)
        if resp_tracks.status_code == 200:
            tracks_list = resp_tracks.json().get('toptracks', {}).get('track', [])
            profile['top_tracks'] = [t['name'] for t in tracks_list if t.get('name')][:5]
    except Exception as e:
        print(f"Aviso: Error obteniendo top tracks de Last.fm para '{seed_artist}': {e}")
        
    return profile

def get_lastfm_single_seed_candidates(seed_artist, api_key, blocked_set, limit=50):
    """
    Obtiene los artistas similares a un único artista semilla desde Last.fm (solicitando hasta 250),
    filtrando estrictamente contra el historial del usuario (0 reproducciones previas) y eliminando
    cualquier conflicto o proyecto colaborativo con la semilla.
    """
    candidates = []
    try:
        url = f"http://ws.audioscrobbler.com/2.0/?method=artist.getsimilar&artist={requests.utils.quote(seed_artist)}&api_key={api_key}&format=json&limit=250"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            similars = data.get('similarartists', {}).get('artist', [])
            for sim in similars:
                name = sim.get('name')
                try:
                    match_val = float(sim.get('match', 0.0))
                except (ValueError, TypeError):
                    match_val = 0.0
                if not name or match_val <= 0.0:
                    continue
                # Filtro 1: Conflicto con la semilla (solitario, feat, collab)
                if is_seed_conflict(name, seed_artist):
                    continue
                # Filtro 2: Bloqueo estricto por historial y diario (incluyendo variantes como Trío o Experiment)
                if is_artist_blocked(name, blocked_set):
                    continue
                candidates.append({
                    'name': name,
                    'affinity_score': match_val,
                    'seeds': [seed_artist]
                })
    except Exception as e:
        print(f"Error fetching Last.fm single seed for '{seed_artist}': {e}")
        
    candidates.sort(key=lambda x: x['affinity_score'], reverse=True)
    return candidates[:limit]

def get_recommendations(top_df, engine, base_type, num_recommendations=10, artistas_historicos=None, artistas_2026=None, trend_df=None, artistas_guardados=None, confirmed_hits=None, seed_artist=None, exclude_artists=None, **kwargs):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("No Gemini API key found. Please add GEMINI_API_KEY to your .env file.")
        
    genai.configure(api_key=api_key)
    models_to_try = [
        'gemini-3.5-flash-lite',
        'gemini-flash-latest',
        'gemini-3.8-flash',
        'gemini-flash-lite-latest',
        'gemini-3.1-flash-lite'
    ]

    def format_df_to_string(df, type_of_base):
        if df is None or df.empty:
            return "Ninguno"
        n = len(df)
        if 'obsession_score' in df.columns:
            if type_of_base == "Artistas":
                return f"Top {n} Artistas por Obsesión Reciente (Últimos 30 días): " + ", ".join([
                    f"{r['artist_name']} (Obsession Score: {r['obsession_score']:.1f}, {int(r['play_count'])} streams en {int(r['days_active'])} días)"
                    for _, r in df.iterrows()
                ])
            elif type_of_base == "Canciones":
                return f"Top {n} Canciones por Obsesión Reciente: " + ", ".join([
                    f"\"{r['track_name']}\" de {r['artist_name']} (Score: {r['obsession_score']:.1f})"
                    for _, r in df.iterrows()
                ])
            else:
                return f"Top {n} Álbumes por Obsesión Reciente: " + ", ".join([
                    f"\"{r['album_name']}\" de {r['artist_name']} (Score: {r['obsession_score']:.1f})"
                    for _, r in df.iterrows()
                ])
        else:
            if type_of_base == "Artistas":
                return f"Top {n} Artists: " + ", ".join(df['artist_name'].tolist())
            elif type_of_base == "Canciones":
                return f"Top {n} Songs: " + ", ".join(df.apply(lambda row: f"\"{row['track_name']}\" by {row['artist_name']}", axis=1).tolist())
            else:
                return f"Top {n} Albums: " + ", ".join(df.apply(lambda row: f"\"{row['album_name']}\" by {row['artist_name']}", axis=1).tolist())

    base_profile_str = format_df_to_string(top_df, base_type)
    trend_profile_str = format_df_to_string(trend_df, base_type)
    
    # Preparación de conjuntos para exclusión en Python
    hist_lower = set([normalize_name(a) for a in (artistas_historicos or [])])
    saved_rec_lower = set([normalize_name(a) for a in (artistas_guardados or [])])
    blocked_pool = hist_lower | saved_rec_lower

    if exclude_artists:
        for a in exclude_artists:
            if a:
                blocked_pool.add(normalize_name(a))

    seed_artist = (seed_artist or kwargs.get('seed_artist') or "").strip()
    if seed_artist:
        blocked_pool.add(normalize_name(seed_artist))

    # Lista corta de referencia para evitar repeticiones recientes en el prompt
    recent_blocked_str = ", ".join(artistas_guardados[-35:]) if artistas_guardados else "Ninguno"

    if seed_artist:
        lastfm_api_key = os.getenv("LASTFM_API_KEY")
        seed_profile = profile_seed_artist(seed_artist, lastfm_api_key) if lastfm_api_key else {}
        
        bio_txt = seed_profile.get('bio', '')
        tags_txt = ", ".join(seed_profile.get('tags', [])) if seed_profile.get('tags') else "Indie, Alternativo"
        top_tracks_txt = ", ".join(f'"{t}"' for t in seed_profile.get('top_tracks', [])) if seed_profile.get('top_tracks') else "Singles oficiales del artista"
        peers_txt = ", ".join(seed_profile.get('similar_peers', [])) if seed_profile.get('similar_peers') else "Artistas contemporáneos de la escena"
        
        grounding_block = f"""
[RADIOGRAFÍA Y CONTEXTO SONORO DE LA SEMILLA]:
- Artista Semilla: {seed_artist}
- Contexto / Integrantes / Origen: {bio_txt if bio_txt else f'Proyecto musical de {seed_artist}'}
- Microgéneros y Etiquetas Estéticas: {tags_txt}
- Canciones Canónicas de Referencia de {seed_artist}: {top_tracks_txt}
- Escena y Pares Musicales Inmediatos: {peers_txt}
"""
        user_profile = f"""
{grounding_block}
MISIÓN DEL CURADOR: El usuario desea descubrir artistas y canciones afines al ADN musical, texturas, groove, atmósfera tímbrica y estética de {seed_artist}.
REGLA INFALIBLE: El usuario NUNCA debe haber escuchado a estos artistas recomendados (0 reproducciones en su historia).
"""
        # Obtención y enriquecimiento canónico de candidatos Last.fm para la semilla
        seed_candidates = []
        if lastfm_api_key:
            seed_candidates = get_lastfm_single_seed_candidates(seed_artist, lastfm_api_key, blocked_pool, limit=45)
            if len(seed_candidates) < 15 and seed_profile.get('similar_peers'):
                for peer in seed_profile.get('similar_peers', []):
                    if not is_seed_conflict(peer, seed_artist) and not is_artist_blocked(peer, blocked_pool):
                        p_norm = normalize_name(peer)
                        if not any(normalize_name(c['name']) == p_norm for c in seed_candidates):
                            seed_candidates.append({
                                'name': peer,
                                'affinity_score': 0.85,
                                'seeds': [seed_artist]
                            })
            seed_candidates = enrich_candidates_with_top_tracks(seed_candidates, lastfm_api_key, max_candidates=25, tracks_per_artist=2)

        candidate_lines = []
        for c in seed_candidates:
            tracks_str = ", ".join(f'"{t}"' for t in c.get('top_tracks', []))
            if tracks_str:
                candidate_lines.append(f"- {c['name']} (Afinidad: {c['affinity_score']:.2f} | Canciones oficiales sugeridas: {tracks_str})")
            else:
                candidate_lines.append(f"- {c['name']} (Afinidad: {c['affinity_score']:.2f})")
        rag_str = "\n".join(candidate_lines) if candidate_lines else "Ninguno"

        grace_rule = """
        REGLA DE SELECCIÓN DE CANCIÓN Y GRACIA CURATORIAL (90/10):
        Para cada artista seleccionado, elige preferentemente una de sus 'Canciones oficiales sugeridas' indicadas en el catálogo (esto garantiza existencia inmediata y 0 fallos de búsqueda en Spotify). Si y solo si tienes certeza absoluta e inequívoca de otro track de estudio oficial del artista que conecte aún mejor con el sonido solicitado, puedes proponerlo, garantizando su título exacto de estudio en Spotify. PROHIBIDO inventar títulos inexistentes.
        """

        if engine == "Last.fm":
            if not lastfm_api_key:
                raise ValueError("No se encontró LASTFM_API_KEY en .env. ¡Por favor añade tu clave de Last.fm!")
                
            persona = f"""
            Rol: Eres un recomendador basado en la API oficial de afinidad de Last.fm enfocado en {seed_artist}.
            Enfoque: "PUNTOS CIEGOS DE ALTA AFINIDAD". Artistas con correlación matemática altísima con {seed_artist} que el usuario NUNCA ha escuchado (0 reproducciones en su historia).
            
            USA ESTA RADIOGRAFÍA SONORA COMO GUÍA ESTRICTA:
            {grounding_block}
            
            CANDIDATOS DETECTADOS POR LAST.FM (Pre-filtrados con 0 reproducciones y enriquecidos con pistas canónicas):
            {rag_str}
            
            TU TAREA:
            1. Selecciona a los artistas de esta lista de [CANDIDATOS DETECTADOS POR LAST.FM] para formular tus recomendaciones.
            2. {grace_rule}
            3. REGLA DE REASON: En la explicación ("reason"), justifica explícitamente cómo conecta el sonido, vibra o instrumentación con {seed_artist}.
            4. PROHIBICIÓN TOTAL DE LA SEMILLA: Prohibido sugerir al mismo {seed_artist}, proyectos paralelos o colaboraciones/feats de {seed_artist}, o artistas ya recomendados recientemente: {recent_blocked_str}.
            5. REGLA DE SPOTIFY: Asegúrate 100% que la canción exista en el catálogo oficial de streaming de Spotify con el artista exacto.
            """
            reason_example = f"Afinidad Last.fm: Conecta con el groove y la atmósfera de {seed_artist} a través de [característica sonora]."
            default_badge = "[AFINIDAD LAST.FM]"

        elif engine == "RYM":
            cands_section = f"\nCATÁLOGO DE CANDIDATOS PRE-FILTRADOS CON 0 REPRODUCCIONES:\n{rag_str}\n(Puedes seleccionar proyectos afines de este catálogo o sugerir otros de culto de RYM con 0 reproducciones comprobadas).\n" if rag_str != "Ninguno" else ""
            persona = f"""
            Rol: Eres un curador experto en el canon de RateYourMusic.
            Enfoque: Curaduría sonora y microgéneros aclamados (rating > 3.65 en RYM) que dialoguen estéticamente con el universo de {seed_artist}.
            
            USA ESTA RADIOGRAFÍA SONORA COMO GUÍA ESTRICTA:
            {grounding_block}
            {cands_section}
            ANÁLISIS DE 3 DIMENSIONES TÍMBRICAS:
            1. Timbres e instrumentación: dialoga con las etiquetas {tags_txt} y texturas analógicas/vintage.
            2. Ritmo y cadencia: síncopas, groove o tempo relajado acorde al linaje de {seed_artist}.
            3. Atmósfera vocal e intimidad de grabación.
            
            REGLAS INFALIBLES:
            - REGLA DE AFINIDAD ESTÉTICA Y 0 REPRODUCCIONES: Prioriza proyectos con aclamación crítica en RYM y alta afinidad sonora con {seed_artist}. No estás restringido a la ultra-obscuridad si el usuario nunca lo ha escuchado. Lo fundamental es que el artista tenga 0 reproducciones previas en la historia del usuario y que complemente a la perfección el sonido de {seed_artist}.
            - {grace_rule}
            - PROHIBICIÓN TOTAL DE LA SEMILLA: Prohibido sugerir al mismo {seed_artist}, proyectos paralelos o colaboraciones/feats de {seed_artist}, o artistas de sesiones recientes: {recent_blocked_str}.
            - REGLA DE REASON: Menciona el microgénero exacto de RYM, el álbum y por qué conecta estilísticamente con {seed_artist}.
            - REGLA DE SPOTIFY: Asegúrate 100% que la canción recomendada exista en el catálogo oficial de streaming de Spotify con el artista exacto.
            """
            reason_example = f"Microgénero: [microgénero exacto]. Álbum: [Álbum]. Conexión con {seed_artist}: [explicación sonora]."
            default_badge = "[MICROGÉNERO RYM]"

        elif engine == "Discogs":
            cands_section = f"\nCATÁLOGO DE CANDIDATOS PRE-FILTRADOS CON 0 REPRODUCCIONES:\n{rag_str}\n(Puedes seleccionar proyectos afines de este catálogo o sugerir otros con conexiones técnicas de créditos con 0 reproducciones comprobadas).\n" if rag_str != "Ninguno" else ""
            persona = f"""
            Rol: Eres un archivista e ingeniero de audio enfocado en los créditos técnicos de Discogs.
            Enfoque: Letra chica técnica. Conexiones por productores, ingenieros de sonido/mezcla, sellos independientes o músicos de sesión compartidos o estilísticamente afines al sonido de {seed_artist}.
            
            USA ESTA RADIOGRAFÍA SONORA COMO GUÍA ESTRICTA:
            {grounding_block}
            {cands_section}
            REGLAS INFALIBLES:
            - REGLA DE CONEXIÓN TÉCNICA Y 0 REPRODUCCIONES: Conexiones técnicas de producción, instrumentación o sellos afines al sonido de {seed_artist}. Prohibido sugerir a {seed_artist} o colaboraciones suyas. No hay límite de popularidad siempre y cuando el usuario tenga 0 reproducciones en su historia.
            - {grace_rule}
            - PROHIBICIÓN TOTAL DE LA SEMILLA: Prohibido sugerir al mismo {seed_artist}, colaboraciones, feats o proyectos de {seed_artist}, o artistas de sesiones recientes: {recent_blocked_str}.
            - REGLA DE REASON: Detalla la conexión técnica de producción, instrumentación o sello con el sonido de {seed_artist}.
            - REGLA DE SPOTIFY: Asegúrate 100% que la canción exista en el catálogo de streaming de Spotify.
            """
            reason_example = f"Créditos / Estudio: Conexión técnica con {seed_artist} mediante [productor/sello/arreglo sonoro]."
            default_badge = "[CRÉDITOS / ESTUDIO]"

        else: # Universal / Curador Inteligente
            cands_block = ""
            if rag_str != "Ninguno":
                cands_block = f"\nCANDIDATOS DETECTADOS CON ALTA AFINIDAD Y 0 REPRODUCCIONES COMPROBADAS:\n{rag_str}\n(Selecciona prioritariamente de esta lista o sugiere otros afines que cumplan las 0 reproducciones y no sean la semilla).\n"

            persona = f"""
            Rol: Eres un Curador Musical Maestro y Productor Ejecutivo de Estudio.
            Enfoque: HÍBRIDO UNIVERSAL Y SÍNTESIS SONORA alrededor de {seed_artist}.
            
            USA ESTA RADIOGRAFÍA SONORA COMO GUÍA ESTRICTA:
            {grounding_block}
            {cands_block}
            ANÁLISIS OBLIGATORIO EN 3 DIMENSIONES TÍMBRICAS:
            1. INSTRUMENTACIÓN Y TIMBRES: Deconstruye el sonido de {seed_artist} (ej: teclados vintage Casio/Rhodes/Wurlitzer, sintetizadores analógicos, guitarras chorus/acústicas lo-fi, compresión de bajo funk/groove, baterías analógicas secas).
            2. RITMO, TEMPO Y GROOVE: Captura la cadencia exacta (ej: downtempo arrastrado, funk relajado, síncopas neo-soul, cadencia bedroom disco).
            3. TRATAMIENTO VOCAL Y ATMÓSFERA: Identifica la intimidad de grabación, reverberación sutil, falsetes soul o melodías vocales suaves y etéreas.
            
            REGLAS INFALIBLES:
            - REGLA DE 0 REPRODUCCIONES Y LIBERTAD DE ESCENA (SIN BARRERA DE NIVEL 3): El usuario busca descubrir música complementaria que NUNCA ha escuchado antes (0 reproducciones en su historial). No estás obligado a restringirte al nicho ultra-obscuro: puedes recomendar artistas consolidados de la escena internacional, bandas contemporáneas de festivales o joyas ocultas, siempre que sean 100% inéditos para el usuario y complementen la instrumentación, groove y atmósfera de {seed_artist}.
            - {grace_rule}
            - PROHIBICIÓN TOTAL DE LA SEMILLA: Tienes ESTRICTAMENTE PROHIBIDO sugerir al mismo {seed_artist}, solo o en cualquier colaboración, featuring o proyecto paralelo (ej: bandas conjuntas con otros artistas), así como artistas de sesiones recientes: {recent_blocked_str}.
            - REGLA DEL CAMPO REASON: Explica la conexión sonora concreta con {seed_artist} aludiendo a las dimensiones tímbricas (ej: 'Comparte con {seed_artist} el uso de teclados vintage y el bajo funk sincopado mediante...').
            - REGLA DE SPOTIFY: Asegúrate 100% que la canción recomendada exista en Spotify con el artista exacto.
            """
            reason_example = f"Afinidad Sonora: Comparte con {seed_artist} [groove/textura/linaje musical]."
            default_badge = "[CURADURÍA UNIVERSAL]"

    else:
        # Modo Basado en Perfil Histórico y Tendencias Recientes
        feedback_str = ""
        if confirmed_hits:
            hits_lines = [
                f"- {h['artist']} - \"{h['track']}\" (escuchada {h['plays']} veces tras ser recomendada)"
                for h in confirmed_hits[:10]
            ]
            feedback_str = (
                "\n[CALIBRACIÓN DE GUSTO REAL (HITS ADOPTADOS POR EL USUARIO TRAS RECOMENDACIONES PREVIAS)]:\n"
                + "\n".join(hits_lines)
                + "\nIMPORTANTE: Usa estas canciones adoptadas como señal positiva de alta precisión para entender las texturas sonoras y timbres que realmente cautivan al usuario.\n"
            )

        user_profile = f"[PERFIL FUNDAMENTAL (Gusto Histórico)]:\n{base_profile_str}\n\n[OBSESIÓN ACTUAL (Tendencia ponderada de los últimos 30 días)]:\n{trend_profile_str}{feedback_str}"

        # Obtención y enriquecimiento de Puentes Sonoros Multi-Semilla para TODOS los motores
        lastfm_api_key = os.getenv("LASTFM_API_KEY")
        seed_artists = []
        if trend_df is not None and not trend_df.empty:
            seed_artists = trend_df['artist_name'].unique().tolist()
        elif top_df is not None and not top_df.empty:
            seed_artists = top_df['artist_name'].unique().tolist()

        multi_seed_candidates = []
        if lastfm_api_key and seed_artists:
            raw_multi = get_lastfm_multi_seed_candidates(seed_artists, lastfm_api_key, blocked_pool, limit=35)
            multi_seed_candidates = enrich_candidates_with_top_tracks(raw_multi, lastfm_api_key, max_candidates=25, tracks_per_artist=2)

        multi_candidate_lines = []
        for c in multi_seed_candidates:
            tracks_str = ", ".join(f'"{t}"' for t in c.get('top_tracks', []))
            seeds_str = ", ".join(c.get('seeds', []))
            if tracks_str:
                multi_candidate_lines.append(f"- {c['name']} (Afinidad: {c['affinity_score']:.2f} | Puente sonoro con [{seeds_str}] | Canciones oficiales sugeridas: {tracks_str})")
            else:
                multi_candidate_lines.append(f"- {c['name']} (Afinidad: {c['affinity_score']:.2f} | Puente sonoro con [{seeds_str}])")
        rag_multi_str = "\n".join(multi_candidate_lines) if multi_candidate_lines else "Ninguno"

        grace_rule_profile = """
        REGLA DE CANCIÓN Y GRACIA CURATORIAL (90/10):
        Para cada artista seleccionado, elige preferentemente una de las 'Canciones oficiales sugeridas' indicadas en el catálogo (esto garantiza existencia inmediata en Spotify y 0 fallos de búsqueda). Si y solo si tienes certeza absoluta e inequívoca de otro track de estudio oficial del artista que conecte aún mejor con el gusto del usuario, puedes proponerlo, garantizando su título exacto de estudio en Spotify. PROHIBIDO inventar títulos inexistentes.
        """

        if engine == "Last.fm":
            persona = f"""
            Rol: Eres un recomendador basado en la API oficial de afinidad e intersección de Last.fm.
            Enfoque: "PUNTOS CIEGOS DE ALTA AFINIDAD". Son artistas que tienen una correlación matemática altísima con la obsesión reciente del usuario, pero que él NUNCA ha escuchado (0 reproducciones en su historia).
            
            CANDIDATOS DETECTADOS POR INTERSECCIÓN (Pre-filtrados con 0 reproducciones y pistas canónicas):
            {rag_multi_str}
            
            TU TAREA:
            1. Selecciona a los artistas de esta lista de [CANDIDATOS DETECTADOS POR INTERSECCIÓN] para formular tus recomendaciones.
            2. {grace_rule_profile}
            3. REGLA DEL CAMPO REASON (PUENTE SONORO OBLIGATORIO): En la explicación ("reason"), debes justificar explícitamente el PUENTE SONORO. Menciona por qué conecta con los artistas puente de la lista (ej. "Conecta la tensión rítmica de [Artista A] con la melancolía vocal de [Artista B] mediante...").
            4. REGLA DE NO REPETICIÓN: Prohibido sugerir bandas ya recomendadas en sesiones recientes: {recent_blocked_str}
            5. REGLA DE SPOTIFY: Asegúrate 100% de que la canción exista en el catálogo oficial de streaming de Spotify.
            """
            reason_example = "Puente sonoro: Conecta la energía de [Artista A] y [Artista B] a través de [característica sonora]."
            default_badge = "[PUNTO CIEGO: Alta Afinidad]"
            
        elif engine == "RYM":
            cands_section = f"\nCATÁLOGO DE PUENTES SONOROS CON 0 REPRODUCCIONES:\n{rag_multi_str}\n(Prioriza de este catálogo las opciones de mayor aclamación crítica o sugiere microgéneros de culto afines con 0 reproducciones comprobadas).\n" if rag_multi_str != "Ninguno" else ""
            persona = f"""
            Rol: Eres un curador experto en el canon de RateYourMusic.
            Enfoque: Curaduría de culto, underground y alta aclamación crítica (rating > 3.65 en RYM con pocos votos globales).
            {cands_section}
            REGLA DE OBSCUREZA EXTREMA: El usuario es un melómano con miles de artistas escuchados. Tienes ESTRICTAMENTE PROHIBIDO recomendar bandas ultra-conocidas de RYM (como Radiohead, Slint, MBV, Joy Division, etc.). Debes bucear en microgéneros profundos y joyas escondidas de culto.
            {grace_rule_profile}
            REGLA DE NO REPETICIÓN HISTÓRICA: El usuario ya conoce y ha escuchado a los artistas de su perfil. Tienes ESTRICTAMENTE PROHIBIDO recomendar a cualquiera de los artistas presentes en su perfil. Cada recomendación DEBE ser un artista o banda completamente DISTINTO y NUEVO para el usuario.
            REGLA DE DIVERSIDAD: Asegura que los artistas recomendados vengan de distintos microgéneros.
            REGLA DE NO REPETICIÓN RECIENTE: Prohibido sugerir artistas de sesiones recientes: {recent_blocked_str}
            REGLA DE SPOTIFY: Asegúrate 100% que la canción recomendada exista en el catálogo de streaming de Spotify con el artista exacto.
            """
            reason_example = "Microgénero: [microgénero exacto de RYM]. Álbum de culto: [Álbum]."
            default_badge = "[NUEVO DESCUBRIMIENTO]"
            
        elif engine == "Discogs":
            cands_section = f"\nCATÁLOGO DE PUENTES SONOROS CON 0 REPRODUCCIONES:\n{rag_multi_str}\n(Prioriza de este catálogo las opciones con mayores conexiones técnicas o sugiere proyectos afines con 0 reproducciones comprobadas).\n" if rag_multi_str != "Ninguno" else ""
            persona = f"""
            Rol: Eres un archivista e ingeniero de audio enfocado en los créditos de Discogs.
            Enfoque: Letra chica técnica. Conexiones por productores, ingenieros de sonido, sellos independientes o músicos de sesión compartidos.
            {cands_section}
            REGLA DE OBSCUREZA EXTREMA: Prohibido sugerir álbumes mainstream. Deben ser álbumes que requirieron bucear en los créditos técnicos de Discogs.
            {grace_rule_profile}
            REGLA DE NO REPETICIÓN HISTÓRICA: El usuario ya conoce y ha escuchado a los artistas de su perfil. Tienes ESTRICTAMENTE PROHIBIDO recomendar a cualquiera de los artistas presentes en su perfil. Cada recomendación DEBE ser un artista o banda completamente DISTINTO y NUEVO para el usuario.
            REGLA DE DIVERSIDAD: Asegura máxima variedad de estilos.
            REGLA DE NO REPETICIÓN RECIENTE: Prohibido sugerir artistas de sesiones recientes: {recent_blocked_str}
            REGLA DE SPOTIFY: Asegúrate 100% que la canción recomendada exista en el catálogo oficial de streaming de Spotify con el artista exacto.
            """
            reason_example = "Año: [año]. Conexión: [Crédito exacto, ej: Producido por X / Mismo baterista de sesión que grabó en Y]."
            default_badge = "[LADO B / CRÉDITOS]"
            
        else: # Universal / Curador Inteligente Histórico
            cands_section = f"\nCATÁLOGO DE PUENTES SONOROS DETECTADOS CON 0 REPRODUCCIONES:\n{rag_multi_str}\n(Selecciona prioritariamente de esta lista para garantizar novedad absoluta y existencia de catálogo).\n" if rag_multi_str != "Ninguno" else ""
            persona = f"""
            Rol: Eres un Curador Musical Maestro y Productor Ejecutivo.
            Enfoque: CURADURÍA UNIVERSAL Y SÍNTESIS HOLÍSTICA.
            Analizas tanto el gusto fundamental histórico como la obsesión reciente del usuario para encontrar joyas musicales que expandan sus horizontes estéticos.
            {cands_section}
            REGLA DE PUREZA: El usuario es un melómano con miles de artistas escuchados. Tienes ESTRICTAMENTE PROHIBIDO recomendar artistas que aparezcan en su perfil o que sean evidentes del mainstream.
            {grace_rule_profile}
            REGLA DE REASON: Justifica con precisión analítica por qué conecta con su perfil y obsesión reciente.
            REGLA DE SPOTIFY: Asegúrate 100% que la canción exista en el catálogo de Spotify.
            """
            reason_example = "Afinidad Sonora: Conecta tus gustos históricos con tu obsesión reciente mediante [motivo analítico]."
            default_badge = "[CURADURÍA UNIVERSAL]"

    prompt = f"""
    {persona}
    
    INSTRUCCIÓN DE CONTRASTE TEMPORAL:
    Analiza el PERFIL FUNDAMENTAL para entender el ADN musical del usuario, pero usa la OBSESIÓN ACTUAL para capturar su estado de ánimo reciente. Tus recomendaciones y tus explicaciones ("reason") deben tender un puente entre sus gustos históricos y su vibra reciente.
    
    Mis preferencias y obsesiones actuales son:
    {user_profile}
    
    Con base en estas reglas, genera una lista de 30 recomendaciones musicales candidatas altamente relevantes. Genera un pool amplio para que el sistema valide la existencia en streaming.
    
    IMPORTANTE: Siempre recomienda una canción específica en el campo 'item'.
    
    Devuelve la salida ESTRICTAMENTE como un JSON array de objetos con las siguientes claves:
    - "artist": El nombre del artista.
    - "item": El nombre de la canción.
    - "reason": Una explicación detallada en español basada en tu rol. Ejemplo: "{reason_example}"
    - "badge": Una etiqueta visual. Elige la más pertinente: "{default_badge}", "[NUEVO DESCUBRIMIENTO]", "[RECONEXIÓN]" o "[LADO B / CRÉDITOS]".
    
    Do not output any markdown formatting like ```json or anything else. Just the raw JSON array.
    """
    
    response = None
    last_error = None
    
    for model_name in models_to_try:
        try:
            model = genai.GenerativeModel(
                model_name,
                generation_config=genai.GenerationConfig(response_mime_type="application/json")
            )
            response = model.generate_content(prompt)
            if response and response.text:
                break
        except Exception as e:
            last_error = e
            print(f"Aviso: Modelo '{model_name}' falló ({e}). Probando siguiente alternativa...")
            continue
            
    if not response or not response.text:
        raise RuntimeError(f"Error comunicando con Gemini. Todos los modelos fallaron. Último error: {last_error}")
        
    try:
        text = response.text.strip()
        start_idx = text.find('[')
        end_idx = text.rfind(']')
        
        if start_idx != -1 and end_idx != -1:
            json_str = text[start_idx:end_idx+1]
            data = json.loads(json_str)
            
            # FILTRO DURO EN PYTHON: Excluye todo lo escuchado en la historia y en el diario
            filtered_data = []
            seen_artists_in_batch = set()
            
            for rec in data:
                raw_artist = str(rec.get('artist', ''))
                if not raw_artist:
                    continue
                # Filtro Semilla estricto: Descartar si es el artista semilla o colaboración
                if seed_artist and is_seed_conflict(raw_artist, seed_artist):
                    continue
                # Filtro Historial estricto: Descartar si el artista o variantes (ej: Trío, Experiment) están bloqueados
                if is_artist_blocked(raw_artist, blocked_pool):
                    continue
                artist_norm = normalize_name(raw_artist)
                # Descartar duplicados en la misma tanda
                if artist_norm in seen_artists_in_batch:
                    continue
                    
                filtered_data.append(rec)
                seen_artists_in_batch.add(artist_norm)
                if len(filtered_data) >= 30:
                    break
                    
            # Si el filtrado duro dejó pocos candidatos (el usuario ha escuchado miles de artistas),
            # hacemos un top-up rápido pidiendo joyas aún más subterráneas
            if len(filtered_data) < 15 and engine in ["RYM", "Discogs", "Universal"]:
                try:
                    top_up_prompt = f"""
                    {persona}
                    
                    REGLA DE PROFUNDIZACIÓN:
                    El usuario es un melómano con más de 15.000 artistas y ya conoce a los primeros sugeridos.
                    Genera 25 recomendaciones adicionales de artistas aún MÁS SUBTERRÁNEOS, oscuros o de microgéneros emergentes que conecten con este gusto pero que un melómano veterano difícilmente haya escuchado.
                    
                    Gusto del usuario:
                    {user_profile}
                    
                    Devuelve la salida ESTRICTAMENTE como un JSON array de objetos con las mismas claves:
                    - "artist": El nombre del artista.
                    - "item": El nombre de la canción más emblemática o conocida.
                    - "reason": Explicación detallada.
                    - "badge": "{default_badge}"
                    """
                    for m_name in models_to_try:
                        try:
                            m_obj = genai.GenerativeModel(
                                m_name,
                                generation_config=genai.GenerationConfig(response_mime_type="application/json")
                            )
                            r2 = m_obj.generate_content(top_up_prompt)
                            if r2 and r2.text:
                                t2 = r2.text.strip()
                                s2 = t2.find('[')
                                e2 = t2.rfind(']')
                                if s2 != -1 and e2 != -1:
                                    d2 = json.loads(t2[s2:e2+1])
                                    for rec2 in d2:
                                        raw_a2 = str(rec2.get('artist', ''))
                                        if not raw_a2:
                                            continue
                                        if seed_artist and is_seed_conflict(raw_a2, seed_artist):
                                            continue
                                        if is_artist_blocked(raw_a2, blocked_pool):
                                            continue
                                        a_norm = normalize_name(raw_a2)
                                        if a_norm in seen_artists_in_batch:
                                            continue
                                        filtered_data.append(rec2)
                                        seen_artists_in_batch.add(a_norm)
                                        if len(filtered_data) >= 30:
                                            break
                                break
                        except Exception:
                            continue
                except Exception as ex:
                    print(f"Top-up aviso: {ex}")
                    
            return filtered_data
        else:
            raise ValueError(f"No se encontró un array JSON en la respuesta de Gemini. Respuesta: {text}")
            
    except Exception as e:
        print(f"Error procesando recomendaciones: {e}")
        raise e

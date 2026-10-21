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
                    if norm in blocked_set:
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

def get_lastfm_single_seed_candidates(seed_artist, api_key, blocked_set, limit=35):
    """
    Obtiene los artistas similares a un único artista semilla desde Last.fm,
    filtrando estrictamente contra el historial del usuario (0 reproducciones previas).
    """
    candidates = []
    try:
        url = f"http://ws.audioscrobbler.com/2.0/?method=artist.getsimilar&artist={requests.utils.quote(seed_artist)}&api_key={api_key}&format=json&limit=100"
        resp = requests.get(url, timeout=6)
        if resp.status_code == 200:
            data = resp.json()
            similars = data.get('similarartists', {}).get('artist', [])
            seed_norm = normalize_name(seed_artist)
            for sim in similars:
                name = sim.get('name')
                try:
                    match_val = float(sim.get('match', 0.0))
                except (ValueError, TypeError):
                    match_val = 0.0
                if not name or match_val <= 0.0:
                    continue
                norm = normalize_name(name)
                if norm in blocked_set or norm == seed_norm:
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

def get_recommendations(top_df, engine, base_type, num_recommendations=10, artistas_historicos=None, artistas_2026=None, trend_df=None, artistas_guardados=None, confirmed_hits=None, seed_artist=None, **kwargs):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("No Gemini API key found. Please add GEMINI_API_KEY to your .env file.")
        
    genai.configure(api_key=api_key)
    models_to_try = ['gemini-3.5-flash', 'gemini-3.5-flash-lite', 'gemini-3.8-flash']

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

    seed_artist = (seed_artist or kwargs.get('seed_artist') or "").strip()
    if seed_artist:
        blocked_pool.add(normalize_name(seed_artist))

    # Lista corta de referencia para evitar repeticiones recientes en el prompt
    recent_blocked_str = ", ".join(artistas_guardados[-35:]) if artistas_guardados else "Ninguno"

    if seed_artist:
        user_profile = f"""
[ARTISTA SEMILLA EXPLORADO]: {seed_artist}
El usuario desea descubrir artistas y canciones afines al ADN sonoro, texturas, groove, estética y arreglos de {seed_artist}.
REGLA INFALIBLE: El usuario NUNCA debe haber escuchado a estos artistas recomendados (0 reproducciones en su historia).
"""
        if engine == "Last.fm":
            lastfm_api_key = os.getenv("LASTFM_API_KEY")
            if not lastfm_api_key:
                raise ValueError("No se encontró LASTFM_API_KEY en .env. ¡Por favor añade tu clave de Last.fm!")
                
            top_candidates = get_lastfm_single_seed_candidates(seed_artist, lastfm_api_key, blocked_pool, limit=35)
            candidate_lines = [
                f"- {c['name']} (Afinidad Last.fm: {c['affinity_score']:.2f})"
                for c in top_candidates
            ]
            rag_str = "\n".join(candidate_lines) if candidate_lines else "Ninguno"
            
            persona = f"""
            Rol: Eres un recomendador basado en la API oficial de afinidad de Last.fm enfocado en {seed_artist}.
            Enfoque: "PUNTOS CIEGOS DE ALTA AFINIDAD". Artistas con correlación matemática altísima con {seed_artist} que el usuario NUNCA ha escuchado (0 reproducciones en su historia).
            
            CANDIDATOS DETECTADOS POR LAST.FM (Pre-filtrados con 0 reproducciones y ordenados por afinidad):
            {rag_str}
            
            TU TAREA:
            1. Selecciona a los artistas de esta lista de [CANDIDATOS DETECTADOS POR LAST.FM] para formular tus recomendaciones.
            2. REGLA DE PUERTA DE ENTRADA: Recomienda la CANCIÓN MÁS REPRESENTATIVA, ICÓNICA O CANÓNICA de cada artista que mejor conecte con el sonido de {seed_artist}.
            3. REGLA DE REASON: En la explicación ("reason"), justifica explícitamente cómo conecta el sonido, vibra o instrumentación con {seed_artist}.
            4. REGLA DE NO REPETICIÓN: Prohibido sugerir al mismo {seed_artist} o artistas ya recomendados recientemente: {recent_blocked_str}.
            5. REGLA DE SPOTIFY: Asegúrate 100% que la canción exista en el catálogo oficial de streaming de Spotify con el artista exacto.
            """
            reason_example = f"Afinidad Last.fm: Conecta con el groove y la atmósfera de {seed_artist} a través de [característica sonora]."
            default_badge = "[AFINIDAD LAST.FM]"

        elif engine == "RYM":
            persona = f"""
            Rol: Eres un curador experto en el canon de RateYourMusic.
            Enfoque: Curaduría de culto, underground y microgéneros aclamados (rating > 3.65 en RYM) que dialoguen estéticamente con el universo de {seed_artist}.
            REGLA DE OBSCUREZA EXTREMA: El usuario es un melómano veterano. Tienes ESTRICTAMENTE PROHIBIDO recomendar artistas mainstream o súper conocidos. Debes ir a microgéneros profundos y joyas escondidas de culto.
            REGLA DE CANCIÓN: Recomienda su CANCIÓN MÁS REPRESENTATIVA, ACLAMADA O CANÓNICA (la que mejor defina su sonido y exista 100% en el catálogo oficial de Spotify). Prohibido inventar títulos.
            REGLA DE NO REPETICIÓN: Prohibido sugerir al mismo {seed_artist} o artistas de sesiones recientes: {recent_blocked_str}.
            REGLA DE REASON: Menciona el microgénero exacto de RYM, el álbum y por qué conecta estilísticamente con {seed_artist}.
            REGLA DE SPOTIFY: Asegúrate 100% que la canción recomendada exista en el catálogo oficial de streaming de Spotify con el artista exacto.
            """
            reason_example = f"Microgénero: [microgénero exacto]. Álbum: [Álbum]. Conexión con {seed_artist}: [explicación sonora]."
            default_badge = "[MICROGÉNERO RYM]"

        elif engine == "Discogs":
            persona = f"""
            Rol: Eres un archivista e ingeniero de audio enfocado en los créditos técnicos de Discogs.
            Enfoque: Letra chica técnica. Conexiones por productores, ingenieros de sonido/mezcla, sellos independientes o músicos de sesión compartidos o estilísticamente afines al sonido de {seed_artist}.
            REGLA DE OBSCUREZA: Prohibido sugerir artistas mainstream. Deben ser proyectos con conexiones técnicas de estudio.
            REGLA DE CANCIÓN: Recomienda una de las canciones más representativas o aclamadas del proyecto que exista 100% en Spotify con el artista exacto. Prohibido inventar títulos inexistentes.
            REGLA DE NO REPETICIÓN: Prohibido sugerir al mismo {seed_artist} o artistas de sesiones recientes: {recent_blocked_str}.
            REGLA DE REASON: Detalla la conexión técnica de producción, instrumentación o sello con el sonido de {seed_artist}.
            REGLA DE SPOTIFY: Asegúrate 100% que la canción exista en el catálogo de streaming de Spotify.
            """
            reason_example = f"Créditos / Estudio: Conexión técnica con {seed_artist} mediante [productor/sello/arreglo sonoro]."
            default_badge = "[CRÉDITOS / ESTUDIO]"

        else: # Universal / Curador Inteligente
            persona = f"""
            Rol: Eres un Curador Musical Maestro y Productor Ejecutivo.
            Enfoque: HÍBRIDO UNIVERSAL Y SÍNTESIS SONORA alrededor de {seed_artist}.
            Analizas minuciosamente el ADN musical de {seed_artist}: su tratamiento de guitarras/teclados, groove de bajo/batería, tempo, dinámicas vocales, atmósfera sonora y linaje estético contemporáneo o retro.
            REGLA DE PUREZA Y 0 REPRODUCCIONES: El usuario es un melómano con miles de artistas escuchados. Tienes ESTRICTAMENTE PROHIBIDO sugerir al mismo {seed_artist} o artistas ultra-obvios del mainstream.
            REGLA DE CANCIÓN: Recomienda la CANCIÓN MÁS EMBLEMÁTICA O ICÓNICA de cada artista que sirva de puerta de entrada directa y exista 100% en el catálogo de Spotify con el artista exacto. Prohibido inventar títulos.
            REGLA DE NO REPETICIÓN: Prohibido sugerir al mismo {seed_artist} o artistas de sesiones recientes: {recent_blocked_str}.
            REGLA DEL CAMPO REASON: Explica la conexión sonora concreta con {seed_artist} (ej: 'Comparte el groove sincopado y las guitarras limpias de {seed_artist} mediante...').
            REGLA DE SPOTIFY: Asegúrate 100% que la canción recomendada exista en Spotify con el artista exacto.
            """
            reason_example = f"Afinidad Sonora: Comparte con {seed_artist} [groove/textura/linaje musical]."
            default_badge = "[CURADURÍA UNIVERSAL]"

    else:
        # Modo Basado en Perfil Histórico y Tendencias Recientes
        # Circuito de Feedback Real (Hits Confirmados)
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

        if engine == "Last.fm":
            lastfm_api_key = os.getenv("LASTFM_API_KEY")
            if not lastfm_api_key:
                raise ValueError("No se encontró LASTFM_API_KEY en .env. ¡Por favor añade tu clave de Last.fm!")
                
            seed_artists = []
            if trend_df is not None and not trend_df.empty:
                seed_artists = trend_df['artist_name'].unique().tolist()
            elif top_df is not None and not top_df.empty:
                seed_artists = top_df['artist_name'].unique().tolist()
                
            # Algoritmo de Intersección Multi-Semilla (Puntos Ciegos)
            top_candidates = get_lastfm_multi_seed_candidates(seed_artists, lastfm_api_key, blocked_pool, limit=35)
            
            candidate_lines = [
                f"- {c['name']} (Afinidad: {c['affinity_score']:.2f} | Puente sonoro con tus artistas: [{', '.join(c['seeds'])}])"
                for c in top_candidates
            ]
            rag_str = "\n".join(candidate_lines) if candidate_lines else "Ninguno"
            
            persona = f"""
            Rol: Eres un recomendador basado en la API oficial de afinidad e intersección de Last.fm.
            Enfoque: "PUNTOS CIEGOS DE ALTA AFINIDAD". Son artistas que tienen una correlación matemática altísima con la obsesión reciente del usuario, pero que él NUNCA ha escuchado (0 reproducciones en su historia).
            
            CANDIDATOS DETECTADOS POR INTERSECCIÓN (Pre-filtrados con 0 reproducciones y ordenados por afinidad):
            {rag_str}
            
            TU TAREA:
            1. Selecciona a los artistas de esta lista de [CANDIDATOS DETECTADOS POR INTERSECCIÓN] para formular tus recomendaciones.
            2. REGLA DE PUERTA DE ENTRADA (GATEWAY TRACK): Como son puntos ciegos totales del usuario (incluso si son bandas consagradas, de culto o famosas que inexplicablemente nunca escuchó), NO recomiendes un lado B inaccesible. Recomienda su CANCIÓN MÁS REPRESENTATIVA, ICÓNICA o MEJOR PUERTA DE ENTRADA que mejor conecte con el sonido de los "artistas puente" señalados.
            3. REGLA DEL CAMPO REASON (PUENTE SONORO OBLIGATORIO): En la explicación ("reason"), debes justificar explícitamente el PUENTE SONORO. Menciona por qué conecta con los artistas puente de la lista (ej. "Conecta la tensión rítmica de [Artista A] con la melancolía vocal de [Artista B] mediante...").
            4. REGLA DE NO REPETICIÓN: Prohibido sugerir bandas ya recomendadas en sesiones recientes: {recent_blocked_str}
            5. REGLA DE SPOTIFY: Asegúrate 100% de que la canción exista en el catálogo oficial de streaming de Spotify.
            """
            reason_example = "Puente sonoro: Conecta la energía de [Artista A] y [Artista B] a través de [característica sonora]."
            default_badge = "[PUNTO CIEGO: Alta Afinidad]"
            
        elif engine == "RYM":
            persona = f"""
            Rol: Eres un curador experto en el canon de RateYourMusic.
            Enfoque: Curaduría de culto, underground y alta aclamación crítica (rating > 3.65 en RYM con pocos votos globales).
            REGLA DE OBSCUREZA EXTREMA: El usuario es un melómano con miles de artistas escuchados. Tienes ESTRICTAMENTE PROHIBIDO recomendar bandas ultra-conocidas de RYM (como Radiohead, Slint, MBV, Joy Division, etc.). Debes bucear en microgéneros profundos y joyas escondidas de culto.
            REGLA DE CANCIÓN (CANCIÓN CANÓNICA): Como el artista o proyecto musical ya es de nicho o culto y el usuario NUNCA lo ha escuchado, recomienda su CANCIÓN MÁS REPRESENTATIVA, ACLAMADA O CANÓNICA (la que mejor defina su sonido y exista 100% en el catálogo oficial de streaming de Spotify). Prohibido inventar títulos de canciones inexistentes.
            REGLA DE NO REPETICIÓN HISTÓRICA: El usuario ya conoce y ha escuchado a los artistas de su perfil. Tienes ESTRICTAMENTE PROHIBIDO recomendar a cualquiera de los artistas presentes en su perfil. Cada recomendación DEBE ser un artista o banda completamente DISTINTO y NUEVO para el usuario.
            REGLA DE DIVERSIDAD: Asegura que los artistas recomendados vengan de distintos microgéneros.
            REGLA DE NO REPETICIÓN RECIENTE: Prohibido sugerir artistas de sesiones recientes: {recent_blocked_str}
            REGLA DE SPOTIFY: Asegúrate 100% que la canción recomendada exista en el catálogo de streaming de Spotify con el artista exacto.
            """
            reason_example = "Microgénero: [microgénero exacto de RYM]. Álbum de culto: [Álbum]."
            default_badge = "[NUEVO DESCUBRIMIENTO]"
            
        elif engine == "Discogs":
            persona = f"""
            Rol: Eres un archivista e ingeniero de audio enfocado en los créditos de Discogs.
            Enfoque: Letra chica técnica. Conexiones por productores, ingenieros de sonido, sellos independientes o músicos de sesión compartidos.
            REGLA DE OBSCUREZA EXTREMA: Prohibido sugerir álbumes mainstream. Deben ser álbumes que requirieron bucear en los créditos técnicos de Discogs.
            REGLA DE CANCIÓN: Recomienda una de las canciones más representativas o aclamadas del álbum que exista 100% en el catálogo oficial de streaming de Spotify con el artista exacto. Prohibido inventar títulos inexistentes.
            REGLA DE NO REPETICIÓN HISTÓRICA: El usuario ya conoce y ha escuchado a los artistas de su perfil. Tienes ESTRICTAMENTE PROHIBIDO recomendar a cualquiera de los artistas presentes en su perfil. Cada recomendación DEBE ser un artista o banda completamente DISTINTO y NUEVO para el usuario.
            REGLA DE DIVERSIDAD: Asegura máxima variedad de estilos.
            REGLA DE NO REPETICIÓN RECIENTE: Prohibido sugerir artistas de sesiones recientes: {recent_blocked_str}
            REGLA DE SPOTIFY: Asegúrate 100% que la canción recomendada exista en el catálogo oficial de streaming de Spotify con el artista exacto.
            """
            reason_example = "Año: [año]. Conexión: [Crédito exacto, ej: Producido por X / Mismo baterista de sesión que grabó en Y]."
            default_badge = "[LADO B / CRÉDITOS]"
            
        else: # Universal / Curador Inteligente Histórico
            persona = f"""
            Rol: Eres un Curador Musical Maestro y Productor Ejecutivo.
            Enfoque: CURADURÍA UNIVERSAL Y SÍNTESIS HOLÍSTICA.
            Analizas tanto el gusto fundamental histórico como la obsesión reciente del usuario para encontrar joyas musicales que expandan sus horizontes estéticos.
            REGLA DE PUREZA: El usuario es un melómano con miles de artistas escuchados. Tienes ESTRICTAMENTE PROHIBIDO recomendar artistas que aparezcan en su perfil o que sean evidentes del mainstream.
            REGLA DE CANCIÓN: Recomienda la CANCIÓN MÁS REPRESENTATIVA Y CANÓNICA de cada artista que exista 100% en el catálogo de Spotify con el artista exacto.
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
    
    Con base en estas reglas, genera una lista de 35 recomendaciones musicales candidatas altamente relevantes. Genera un pool amplio para que el sistema valide la existencia en streaming.
    
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
                artist_norm = normalize_name(raw_artist)
                
                # Descartar si ya fue escuchado alguna vez o recomendado previamente
                if artist_norm in blocked_pool:
                    continue
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
                                        a_norm = normalize_name(str(rec2.get('artist', '')))
                                        if a_norm in blocked_pool or a_norm in seen_artists_in_batch:
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

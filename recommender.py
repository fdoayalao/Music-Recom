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

def get_recommendations(top_df, engine, base_type, num_recommendations=10, artistas_historicos=None, artistas_2026=None, trend_df=None, artistas_guardados=None, confirmed_hits=None, **kwargs):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("No Gemini API key found. Please add GEMINI_API_KEY to your .env file.")
        
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-3.5-flash', generation_config=genai.GenerationConfig(response_mime_type="application/json"))

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

    # Lista corta de referencia para evitar repeticiones recientes en el prompt
    recent_blocked_str = ", ".join(artistas_guardados[-35:]) if artistas_guardados else "Ninguno"

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
        REGLA DE OBSCUREZA EXTREMA: El usuario es un melómano que ya conoce el culto evidente. Tienes ESTRICTAMENTE PROHIBIDO recomendar bandas ultra-conocidas de RYM (como Radiohead, Slint, MBV, etc.). Debes ir a microgéneros profundos y joyas escondidas.
        REGLA DE CANCIÓN: Bajo NINGÚN MOTIVO recomiendes la canción principal de un disco. Recomienda un Lado B u obra oculta.
        REGLA DE DIVERSIDAD: Asegura que los artistas recomendados vengan de distintos microgéneros.
        REGLA DE NO REPETICIÓN: Prohibido sugerir artistas de sesiones recientes: {recent_blocked_str}
        REGLA DE SPOTIFY: Asegúrate 100% que la canción recomendada exista en el catálogo de streaming de Spotify.
        """
        reason_example = "Microgénero: [microgénero exacto de RYM]. Álbum de culto: [Álbum]."
        default_badge = "[NUEVO DESCUBRIMIENTO]"
        
    elif engine == "Discogs":
        persona = f"""
        Rol: Eres un archivista e ingeniero de audio enfocado en los créditos de Discogs.
        Enfoque: Letra chica técnica. Conexiones por productores, ingenieros de sonido, sellos independientes o músicos de sesión compartidos.
        REGLA DE OBSCUREZA EXTREMA: Prohibido sugerir álbumes mainstream. Deben ser álbumes que requirieron bucear en los créditos técnicos de Discogs.
        REGLA DE CANCIÓN: Recomienda Lados B, rarezas o tomas alternativas disponibles en streaming.
        REGLA DE DIVERSIDAD: Asegura máxima variedad de estilos.
        REGLA DE NO REPETICIÓN: Prohibido sugerir artistas de sesiones recientes: {recent_blocked_str}
        REGLA DE SPOTIFY: Asegúrate 100% que la canción recomendada exista en el catálogo oficial de streaming de Spotify.
        """
        reason_example = "Año: [año]. Conexión: [Crédito exacto, ej: Producido por X / Mismo baterista de sesión que grabó en Y]."
        default_badge = "[LADO B / CRÉDITOS]"
        
    else:
        persona = "Act as a music recommender."
        reason_example = "Recomendado por afinidad."
        default_badge = "[NUEVO DESCUBRIMIENTO]"

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
    
    try:
        response = model.generate_content(prompt)
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
                    
            return filtered_data
        else:
            print("No se encontró un array JSON en la respuesta de Gemini.")
            print(f"Respuesta bruta: {text}")
            return []
            
    except Exception as e:
        print(f"Error generating recommendations: {e}")
        return []

import google.generativeai as genai
import json
import os
import requests
import os
from dotenv import load_dotenv

load_dotenv()

def get_lastfm_similar_artists(artists_list, api_key, hist_lower, limit=50):
    similar_artists = {} # artist_name: score
    for artist in artists_list[:5]: # Top 5 para no saturar la API
        try:
            url = f"http://ws.audioscrobbler.com/2.0/?method=artist.getsimilar&artist={requests.utils.quote(artist)}&api_key={api_key}&format=json&limit=20"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                similars = data.get('similarartists', {}).get('artist', [])
                for i, sim in enumerate(similars):
                    name = sim.get('name')
                    if name:
                        score = 20 - i
                        similar_artists[name] = similar_artists.get(name, 0) + score
        except Exception as e:
            continue
            
    sorted_similars = sorted(similar_artists.items(), key=lambda x: x[1], reverse=True)
    
    def normalize_name(name):
        n = str(name).lower().strip()
        if n.startswith("the "): n = n[4:]
        return n
        
    final_list = []
    for name, score in sorted_similars:
        norm_name = normalize_name(name)
        if norm_name not in hist_lower:
            final_list.append(name)
        if len(final_list) >= limit:
            break
    return final_list

def get_recommendations(top_df, engine, base_type, num_recommendations=10, artistas_historicos=None, artistas_2026=None, trend_df=None):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("No Gemini API key found. Please add GEMINI_API_KEY to your .env file.")
        
    genai.configure(api_key=api_key)
    
    # Usamos la versión Flash porque la cuenta es gratuita y Pro es muy restrictiva
    model = genai.GenerativeModel('gemini-3.5-flash')

    # Formatear el perfil de usuario basándose en los dos DataFrames (Fundamental vs Tendencia)
    def format_df_to_string(df, type_of_base):
        if df is None or df.empty:
            return "Ninguno"
        n = len(df)
        if type_of_base == "Artistas":
            return f"Top {n} Artists: " + ", ".join(df['artist_name'].tolist())
        elif type_of_base == "Canciones":
            return f"Top {n} Songs: " + ", ".join(df.apply(lambda row: f"{row['track_name']} by {row['artist_name']}", axis=1).tolist())
        else: # Álbumes
            return f"Top {n} Albums: " + ", ".join(df.apply(lambda row: f"{row['album_name']} by {row['artist_name']}", axis=1).tolist())

    base_profile_str = format_df_to_string(top_df, base_type)
    trend_profile_str = format_df_to_string(trend_df, base_type)
    
    user_profile = f"[PERFIL FUNDAMENTAL (Gusto Histórico o General)]:\n{base_profile_str}\n\n[OBSESIÓN ACTUAL (Tendencia de los últimos 30 días)]:\n{trend_profile_str}"
    
    # Prepare history lists
    hist_str = ", ".join(artistas_historicos) if artistas_historicos else "Ninguno"
    hist_2026_str = ", ".join(artistas_2026) if artistas_2026 else "Ninguno"

    # Define the persona based on the engine
    if engine == "RYM":
        persona = f"""
        Rol: Eres un curador experto en el canon de RateYourMusic.
        Enfoque: Curaduría de culto, underground y alta aclamación crítica (rating > 3.65 en RYM con pocos votos globales).
        REGLA DE NOVEDAD ABSOLUTA: Está TERMINANTEMENTE PROHIBIDO recomendar a cualquier artista que figure en la siguiente lista de mi historial:
        [HISTORIAL PROHIBIDO]: {hist_str}
        Deben ser 100% descubrimientos inéditos para mí.
        """
        reason_example = "Microgénero: [microgénero exacto de RYM]. Álbum de culto: [Álbum]."
    elif engine == "Last.fm":
        lastfm_api_key = os.getenv("LASTFM_API_KEY")
        if not lastfm_api_key:
            raise ValueError("No se encontró LASTFM_API_KEY en .env. ¡Por favor añade tu clave de Last.fm!")
            
        base_artists = []
        if trend_df is not None and not trend_df.empty:
            base_artists = trend_df['artist_name'].unique().tolist()
        elif top_df is not None and not top_df.empty:
            base_artists = top_df['artist_name'].unique().tolist()
            
        def normalize_name(name):
            n = str(name).lower().strip()
            if n.startswith("the "): n = n[4:]
            return n
            
        hist_lower = set([normalize_name(a) for a in artistas_historicos]) if artistas_historicos else set()
        
        # RAG CALL (Extraemos hasta 80 artistas para tener un pool profundo)
        rag_similar = get_lastfm_similar_artists(base_artists, lastfm_api_key, hist_lower, limit=80)
        rag_str = ", ".join(rag_similar) if rag_similar else "Ninguno"
        
        persona = f"""
        Rol: Eres un recomendador basado en la API oficial de similitud profunda de Last.fm.
        Enfoque: Puntos ciegos y consenso de audiencia extraídos directamente de la base de datos de Last.fm.
        DATOS DUROS INYECTADOS (RAG):
        He consultado la API de Last.fm usando el Top 5 de la obsesión actual del usuario. La API devolvió esta lista OFICIAL curada de artistas altamente similares (el historial del usuario ya fue filtrado para garantizar novedad pura):
        [ARTISTAS SIMILARES DE LAST.FM]: {rag_str}
        
        TU TAREA:
        Selecciona estrictamente a los artistas de esta lista [ARTISTAS SIMILARES DE LAST.FM] para crear tus recomendaciones.
        REGLA DE OBSCUREZA: Para garantizar que el usuario descubra música nueva (ya que suele conocer a los artistas muy famosos), el 80% de tus selecciones DEBEN ser artistas de nicho, independientes, emergentes o menos conocidos dentro de la lista. Evita a los gigantes obvios.
        Extrae una canción representativa para cada artista elegido.
        """
        reason_example = "Conexión de Last.fm: [Explicación basada en la similitud matemática de audiencia de Last.fm]."
    elif engine == "Discogs":
        persona = f"""
        Rol: Eres un archivista e ingeniero de audio enfocado en los créditos de Discogs.
        Enfoque: Letra chica técnica. Conexiones por productores, ingenieros de sonido, sellos independientes o músicos de sesión.
        REGLA DE PROFUNDIDAD: NO puedes sugerir NADA de lo escuchado en 2026: [HISTORIAL 2026]: {hist_2026_str}
        Si recomiendas a alguien que está en mi historial general ([HISTORIAL GENERAL]: {hist_str}), debe ser OBLIGATORIAMENTE un lado B, rareza, colaboración o proyecto paralelo conectado por créditos. NUNCA sus discos obvios.
        """
        reason_example = "Año: [año]. Conexión: [Crédito exacto, ej: Producido por X / Mismo bajista de sesión que grabó en Y]."
    else:
        persona = "Act as a music recommender."
        reason_example = "Recomendado por similitud."

    prompt = f"""
    {persona}
    
    INSTRUCCIÓN DE CONTRASTE TEMPORAL:
    Analiza el PERFIL FUNDAMENTAL para entender el ADN musical del usuario, pero usa la OBSESIÓN ACTUAL para capturar su estado de ánimo reciente. Tus recomendaciones y tus explicaciones ("reason") deben tratar de tender un puente entre sus gustos históricos y su vibra actual, o profundizar fuertemente en su obsesión reciente de una manera narrativa.
    
    Mis preferencias actuales son:
    {user_profile}
    
    Con base en estas reglas, genera una lista de al menos {num_recommendations * 4} recomendaciones musicales altamente relevantes. Genera bastantes opciones para asegurar que cumples los filtros.
    
    IMPORTANTE: Siempre recomienda una canción específica en el campo 'item'.
    
    Devuelve la salida ESTRICTAMENTE como un JSON array de objetos con las siguientes claves:
    - "artist": El nombre del artista.
    - "item": El nombre de la canción.
    - "reason": Una explicación corta en español basada en tu rol. Ejemplo: "{reason_example}"
    - "badge": Una etiqueta visual. Elige EXACTAMENTE UNA de estas 3 opciones dependiendo del caso:
        1. "[NUEVO DESCUBRIMIENTO]" (si el artista no estaba en mi [HISTORIAL GENERAL])
        2. "[RECONEXIÓN: No escuchado en 2026]" (si es un artista de mi pasado que no está en [HISTORIAL 2026])
        3. "[LADO B / CRÉDITOS]" (si es una rareza de un artista conocido vía Discogs).
    
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
            
            # FILTRO ESTRICTO EN PYTHON (Para mitigar las alucinaciones del LLM)
            def normalize_name(name):
                n = str(name).lower().strip()
                if n.startswith("the "):
                    n = n[4:]
                return n
                
            hist_lower = set([normalize_name(a) for a in artistas_historicos]) if artistas_historicos else set()
            hist_2026_lower = set([normalize_name(a) for a in artistas_2026]) if artistas_2026 else set()
            
            filtered_data = []
            for rec in data:
                raw_artist = str(rec.get('artist', ''))
                artist_norm = normalize_name(raw_artist)
                
                if engine == "RYM":
                    if artist_norm in hist_lower:
                        continue # Regla estricta: NO puede estar en el historial
                elif engine == "Last.fm":
                    if artist_norm in hist_2026_lower:
                        continue # Regla estricta: NO puede estar en 2026
                elif engine == "Discogs":
                    if artist_norm in hist_2026_lower:
                        continue # Regla estricta: NO puede estar en 2026
                        
                filtered_data.append(rec)
                if len(filtered_data) == num_recommendations:
                    break
                    
            return filtered_data
        else:
            print("No se encontró un array JSON en la respuesta de Gemini.")
            print(f"Respuesta bruta: {text}")
            return []
            
    except Exception as e:
        print(f"Error generating recommendations: {e}")
        return []

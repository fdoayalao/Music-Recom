import google.generativeai as genai
import json
import os
from dotenv import load_dotenv

load_dotenv()

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
        persona = f"""
        Rol: Eres el algoritmo de similitud de audiencia profunda de Last.fm.
        Enfoque: Puntos ciegos y consenso de audiencia. Puede incluir artistas populares, clásicos o mainstream que sean referentes directos.
        CONDICIÓN DE AUDIENCIA:
        - Prioridad 1: Referentes que NUNCA he escuchado. NO deben estar en esta lista: [HISTORIAL GENERAL]: {hist_str}
        - Prioridad 2: Artistas que he escuchado antes, pero NO en 2026. NO deben estar en esta lista: [HISTORIAL 2026]: {hist_2026_str}
        - PROHIBIDO: Recomendar artistas que ya escuché en 2026 (los que están en [HISTORIAL 2026]).
        """
        reason_example = "Conexión de audiencia: [Un referente clave para los fans de tu top que aún no exploras, o un clásico que no escuchas desde hace años]."
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

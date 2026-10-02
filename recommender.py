import google.generativeai as genai
import json
import os
from dotenv import load_dotenv

load_dotenv()

def get_recommendations(top_df, engine, base_type, num_recommendations=10):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("No Gemini API key found. Please add GEMINI_API_KEY to your .env file.")
        
    genai.configure(api_key=api_key)
    
    # Usamos la versión Flash porque la cuenta es gratuita y Pro es muy restrictiva
    model = genai.GenerativeModel('gemini-3.5-flash')

    # Prepare user profile
    n_count = len(top_df)
    if base_type == "Artistas":
        items_list = top_df['artist_name'].tolist()
        user_profile = f"Top {n_count} Artists: " + ", ".join(items_list)
    elif base_type == "Canciones":
        items_list = top_df.apply(lambda row: f"{row['track_name']} by {row['artist_name']}", axis=1).tolist()
        user_profile = f"Top {n_count} Songs: " + ", ".join(items_list)
    else: # Álbumes
        items_list = top_df.apply(lambda row: f"{row['album_name']} by {row['artist_name']}", axis=1).tolist()
        user_profile = f"Top {n_count} Albums: " + ", ".join(items_list)
    
    # Define the persona based on the engine
    if engine == "RYM":
        persona = """
        Rol: Eres un curador experto en el canon de RateYourMusic.
        Criterio: Debes recomendar discos de culto con rating alto (> 3.65/5.0) pero con menos de 8.000 votos (para evitar discos universalmente obvios).
        Instrucción: Extrae microgéneros y descriptores de sonido exactos de RYM (ej: sophisti-pop, lush, warm bassline, groovy, city pop) a partir de la música más escuchada del usuario.
        """
        reason_example = "Microgénero: [microgénero]. Álbum de culto: [Álbum]. Recomendado porque te gusta [tu artista]."
    elif engine == "Last.fm":
        persona = """
        Rol: Eres el algoritmo de similitud de audiencia profunda de Last.fm.
        Criterio: Debes recomendar artistas de nicho de la misma escena, circuito independiente o época que comparten la misma base de oyentes que los artistas del usuario. Debes evitar rotundamente los "top similar" comerciales y directos.
        """
        reason_example = "Escena/Época: [escena]. Conecta profundamente a nivel de audiencia con [tu artista]."
    elif engine == "Discogs":
        persona = """
        Rol: Eres un archivista e ingeniero de audio enfocado en los créditos de Discogs.
        Criterio: NO recomiendes por género. Recomienda estrictamente por conexiones de producción: mismo productor, ingeniero de mezcla, músicos de sesión destacados (bajo, batería, teclados) o sellos discográficos independientes afines.
        """
        reason_example = "Año: [año]. Conexión: [Crédito exacto, ej: Producido por X / Mismo bajista de sesión que grabó en Y]."
    else:
        persona = "Act as a music recommender."
        reason_example = "Recomendado por similitud."

    prompt = f"""
    {persona}
    
    The user's taste is represented by:
    {user_profile}
    
    Based on these, generate a list of exactly {num_recommendations} highly relevant music recommendations that the user has likely NOT discovered yet.
    
    IMPORTANT: You must always recommend a specific song in the 'item' field, even if your main recommendation is an artist or an album, because we will add this to a Spotify playlist.
    
    Return the output STRICTLY as a JSON array of objects with the following keys:
    - "artist": The name of the recommended artist.
    - "item": The name of the recommended song.
    - "reason": A short 1-sentence explanation of "Por qué te lo recomendamos" (in Spanish), strictly following the focus of your persona. Example format: "{reason_example}"
    
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
            return data
        else:
            print("No se encontró un array JSON en la respuesta de Gemini.")
            print(f"Respuesta bruta: {text}")
            return []
            
    except Exception as e:
        print(f"Error generating recommendations: {e}")
        return []

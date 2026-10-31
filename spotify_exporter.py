import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
from dotenv import load_dotenv

load_dotenv()

try:
    from recommender import normalize_name, is_seed_conflict, is_artist_blocked
except ImportError:
    def normalize_name(n):
        return "".join(c for c in str(n).lower() if c.isalnum() or c.isspace()).strip()
    def is_seed_conflict(c, s):
        return normalize_name(c) == normalize_name(s) if s else False
    def is_artist_blocked(c, b):
        return normalize_name(c) in b if b else False

def get_spotify_oauth():
    client_id = os.getenv("SPOTIPY_CLIENT_ID")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI")
    
    # Soporte para Streamlit Secrets en la nube
    try:
        import streamlit as st
        if hasattr(st, 'secrets'):
            client_id = client_id or st.secrets.get("SPOTIPY_CLIENT_ID")
            client_secret = client_secret or st.secrets.get("SPOTIPY_CLIENT_SECRET")
            redirect_uri = redirect_uri or st.secrets.get("SPOTIPY_REDIRECT_URI")
    except Exception:
        pass
        
    redirect_uri = redirect_uri or "http://localhost:8501"
    
    if not client_id or not client_secret:
        raise ValueError("Faltan las credenciales de Spotify (SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET)")

    scope = "playlist-modify-public playlist-modify-private playlist-read-private user-read-recently-played user-read-currently-playing user-library-read"
    
    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=scope,
        open_browser=False,
        cache_handler=spotipy.cache_handler.CacheFileHandler(cache_path=".spotify_cache")
    )

def get_auth_url():
    try:
        return get_spotify_oauth().get_authorize_url()
    except Exception as e:
        print(f"Error generando auth URL: {e}")
        return None

def save_token_to_db(token_dict):
    """
    Persiste el token de Spotify en la tabla app_settings de Supabase
    para que esté disponible permanentemente en Streamlit Cloud y entre sesiones.
    """
    if not token_dict or not isinstance(token_dict, dict):
        return
    try:
        import json
        from sqlalchemy import create_engine, text
        supabase_url = os.getenv("SUPABASE_URL")
        try:
            import streamlit as st
            if not supabase_url and hasattr(st, 'secrets'):
                supabase_url = st.secrets.get("SUPABASE_URL")
        except Exception:
            pass
            
        if not supabase_url:
            return
            
        if supabase_url.startswith("postgresql://"):
            supabase_url = supabase_url.replace("postgresql://", "postgresql+psycopg2://")
            
        engine = create_engine(supabase_url)
        val = json.dumps(token_dict)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS app_settings (key VARCHAR PRIMARY KEY, value TEXT);"))
            conn.execute(text("""
                INSERT INTO app_settings (key, value) 
                VALUES ('spotify_token', :v) 
                ON CONFLICT (key) DO UPDATE SET value = :v
            """), {'v': val})
    except Exception as e:
        print(f"Aviso guardando token en DB: {e}")

def get_token(code):
    tok = get_spotify_oauth().get_access_token(code, as_dict=True)
    if tok:
        save_token_to_db(tok)
    return tok

def get_cached_token():
    token = None
    try:
        auth = get_spotify_oauth()
        token = auth.get_cached_token()
    except Exception:
        pass
        
    # 1. Intentar desde archivo local .spotify_cache
    if not token:
        try:
            import json
            cache_path = ".spotify_cache"
            if os.path.exists(cache_path):
                with open(cache_path) as f:
                    data = json.load(f)
                    if data and isinstance(data, dict) and data.get('access_token'):
                        token = data
        except Exception:
            pass

    # 2. Intentar desde Streamlit Secrets
    if not token:
        try:
            import streamlit as st
            if hasattr(st, 'secrets'):
                ref_tok = st.secrets.get("SPOTIFY_REFRESH_TOKEN") or st.secrets.get("SPOTIPY_REFRESH_TOKEN")
                if ref_tok:
                    token = get_spotify_oauth().refresh_access_token(ref_tok)
        except Exception:
            pass

    # 3. Intentar desde Supabase (persistencia universal para Streamlit Cloud)
    if not token:
        try:
            import json
            from sqlalchemy import create_engine, text
            supabase_url = os.getenv("SUPABASE_URL")
            try:
                import streamlit as st
                if not supabase_url and hasattr(st, 'secrets'):
                    supabase_url = st.secrets.get("SUPABASE_URL")
            except Exception:
                pass
            if supabase_url:
                if supabase_url.startswith("postgresql://"):
                    supabase_url = supabase_url.replace("postgresql://", "postgresql+psycopg2://")
                engine = create_engine(supabase_url)
                with engine.connect() as conn:
                    row = conn.execute(text("SELECT value FROM app_settings WHERE key = 'spotify_token'")).fetchone()
                    if row and row[0]:
                        token = json.loads(row[0])
        except Exception as e:
            print(f"Aviso recuperando token de DB: {e}")
            
    return token

def ensure_valid_token(token_info=None):
    """
    Verifica si el token de Spotify expiró y lo refresca automáticamente
    usando el refresh_token, persistiendo la nueva versión en disco, en DB y en st.session_state.
    """
    if not token_info:
        token_info = get_cached_token()
    if not token_info:
        return None
        
    try:
        auth = get_spotify_oauth()
        if auth.is_token_expired(token_info):
            refresh_tok = token_info.get('refresh_token')
            if refresh_tok:
                new_token = auth.refresh_access_token(refresh_tok)
                if new_token:
                    token_info = new_token
                    save_token_to_db(new_token)
                    try:
                        import streamlit as st
                        if hasattr(st, 'session_state'):
                            st.session_state['spotify_token'] = new_token
                    except Exception:
                        pass
    except Exception as e:
        print(f"Aviso refrescando token de Spotify: {e}")
        
    return token_info

def get_currently_playing(token_info=None):
    """
    Returns the currently playing track if any, else None.
    Supports active playing, paused playback, and fallback to the last played track.
    """
    if not token_info:
        token_info = get_cached_token()
    valid_token = ensure_valid_token(token_info) or token_info
    if not valid_token or not isinstance(valid_token, dict) or 'access_token' not in valid_token:
        return None
    try:
        sp = spotipy.Spotify(auth=valid_token['access_token'])
        current = sp.current_user_playing_track()
        if current and current.get('item'):
            track = current['item']
            artists = ", ".join([a['name'] for a in track.get('artists', [])]) if track.get('artists') else 'Artista Desconocido'
            images = track.get('album', {}).get('images', [])
            cover_url = images[0]['url'] if images else None
            return {
                'is_playing': bool(current.get('is_playing')),
                'is_recent': False,
                'track_name': track.get('name', 'Desconocido'),
                'artist_name': artists,
                'album_name': track.get('album', {}).get('name', ''),
                'cover_url': cover_url,
                'url': track.get('external_urls', {}).get('spotify', '#')
            }
        
        # Si no hay canción activa en el reproductor (ej. cerrado o inactivo), traer la última reproducida
        recent = sp.current_user_recently_played(limit=1)
        if recent and recent.get('items'):
            last_item = recent['items'][0]
            track = last_item.get('track', {})
            artists = ", ".join([a['name'] for a in track.get('artists', [])]) if track.get('artists') else 'Artista Desconocido'
            images = track.get('album', {}).get('images', [])
            cover_url = images[0]['url'] if images else None
            return {
                'is_playing': False,
                'is_recent': True,
                'track_name': track.get('name', 'Desconocido'),
                'artist_name': artists,
                'album_name': track.get('album', {}).get('name', ''),
                'cover_url': cover_url,
                'url': track.get('external_urls', {}).get('spotify', '#')
            }
    except Exception as e:
        print(f"Aviso al obtener reproducción actual: {e}")
    return None

def sync_recently_played_to_db(token_info):
    """
    Sincroniza las 50 reproducciones más recientes en Supabase de forma atómica y optimizada.
    Utiliza una única consulta batch para evitar 50 roundtrips de red y normaliza timestamps.
    Returns the number of new tracks added.
    """
    import pandas as pd
    import os
    from sqlalchemy import create_engine, text
    
    valid_token = ensure_valid_token(token_info)
    if not valid_token:
        return 0
        
    sp = spotipy.Spotify(auth=valid_token['access_token'])
    try:
        recent = sp.current_user_recently_played(limit=50)
    except Exception as e:
        print("Error fetching recent tracks:", e)
        return 0
        
    items = recent.get('items', [])
    if not items:
        return 0
        
    supabase_url = os.getenv("SUPABASE_URL")
    if not supabase_url:
        print("No SUPABASE_URL configured")
        return 0
    if supabase_url.startswith("postgresql://"):
        supabase_url = supabase_url.replace("postgresql://", "postgresql+psycopg2://")
    engine = create_engine(supabase_url)
    
    parsed_items = []
    min_ts = None
    for item in items:
        played_at_iso = item['played_at']
        track = item['track']
        ts = pd.to_datetime(played_at_iso).strftime('%Y-%m-%d %H:%M:%S+00:00')
        if min_ts is None or ts < min_ts:
            min_ts = ts
            
        parsed_items.append({
            'ts': ts,
            'year': pd.to_datetime(played_at_iso).year,
            'ms_played': track['duration_ms'],
            'minutes_played': track['duration_ms'] / 60000.0,
            'hours_played': track['duration_ms'] / 3600000.0,
            'track_name': track['name'],
            'artist_name': track['artists'][0]['name'],
            'album_name': track['album']['name'],
            'track_uri': track['uri']
        })
        
    new_tracks_count = 0
    
    with engine.begin() as conn:
        # Consulta BATCH única para la ventana de tiempo reciente
        existing_rows = conn.execute(
            text("SELECT track_uri, ts FROM spotify_history WHERE ts >= :min_ts"),
            {"min_ts": min_ts}
        ).fetchall()
        
        # Ajuste 1: Normalizar r[1] con pd.to_datetime(r[1]).strftime('%Y-%m-%d %H:%M:%S+00:00')
        existing_set = set(
            (r[0], pd.to_datetime(r[1]).strftime('%Y-%m-%d %H:%M:%S+00:00'))
            for r in existing_rows if r[0] and r[1]
        )
        
        for item in parsed_items:
            key = (item['track_uri'], item['ts'])
            if key not in existing_set:
                conn.execute(text('''
                    INSERT INTO spotify_history 
                    (ts, year, ms_played, minutes_played, hours_played, track_name, artist_name, album_name, track_uri)
                    VALUES (:ts, :year, :ms_played, :minutes_played, :hours_played, :track_name, :artist_name, :album_name, :track_uri)
                '''), item)
                existing_set.add(key)
                new_tracks_count += 1
                
    return new_tracks_count

def create_spotify_playlist(playlist_name, description, tracks_info, token_info=None, motor=None):
    """
    tracks_info should be a list of dicts with 'artist' and 'track' (or 'item' for albums).
    Agrupa en una única playlist acumulativa por artista semilla o motor y evita duplicar canciones.
    """
    if token_info and isinstance(token_info, dict) and 'access_token' in token_info:
        sp = spotipy.Spotify(auth=token_info['access_token'])
    else:
        cached = get_cached_token()
        if cached and 'access_token' in cached:
            sp = spotipy.Spotify(auth=cached['access_token'])
        else:
            raise ValueError("No se encontró token de Spotify activo.")
    
    user_id = sp.current_user()['id']
    
    # Check if an existing playlist exists for this motor or name
    playlists = sp.current_user_playlists(limit=50)
    playlist_id = None
    playlist_url = None
    
    target_motor = (motor or "").strip().lower()
    p_name_clean = playlist_name.strip().lower()
    is_seed_playlist = p_name_clean.startswith("descubrimientos:")
    
    # Normalización infalible para playlists de artista semilla
    import re
    def normalize_seed_title(name_str):
        s = str(name_str).lower().strip()
        if s.startswith("descubrimientos:"):
            s = s[len("descubrimientos:"):].strip()
        elif s.startswith("descubrimientos"):
            s = s[len("descubrimientos"):].strip()
        s = re.sub(r'\(.*?\)', '', s)
        return "".join(c for c in s if c.isalnum()).strip()

    target_seed_norm = normalize_seed_title(playlist_name) if is_seed_playlist else ""

    exact_matches = []
    seed_matches = []
    motor_matches = []
    
    while playlists:
        for pl in playlists.get('items', []):
            if pl.get('owner', {}).get('id') == user_id:
                p_name_raw = pl.get('name', '').strip()
                p_name_lower = p_name_raw.lower()
                
                # 1. Coincidencia normalizada de artista semilla (ignora mayúsculas, comas y sufijos como (Universal))
                if is_seed_playlist and target_seed_norm:
                    pl_seed_norm = normalize_seed_title(p_name_raw)
                    if pl_seed_norm == target_seed_norm:
                        seed_matches.append(pl)
                # 2. Coincidencia exacta de texto
                elif p_name_lower == p_name_clean:
                    exact_matches.append(pl)
                # 3. Coincidencia por motor sólo si no es una playlist de artista semilla
                elif target_motor and target_motor in p_name_lower and not is_seed_playlist:
                    if "recomendaciones" in p_name_lower:
                        motor_matches.insert(0, pl)
                    else:
                        motor_matches.append(pl)
                        
        if seed_matches or exact_matches or motor_matches:
            if seed_matches:
                chosen = seed_matches[0]
            elif exact_matches:
                chosen = exact_matches[0]
            else:
                chosen = motor_matches[0]
                
            playlist_id = chosen['id']
            playlist_url = chosen['external_urls']['spotify']
            break
            
        if playlists.get('next'):
            playlists = sp.next(playlists)
        else:
            break
            
    if not playlist_id:
        # Create the playlist if it doesn't exist
        try:
            playlist = sp.user_playlist_create(user_id, playlist_name, public=False, description=description)
        except Exception:
            # Algunas cuentas devuelven 403 en users/{user_id}/playlists, pero me/playlists sí funciona
            playlist = sp._post('me/playlists', payload={
                'name': playlist_name,
                'public': False,
                'description': description
            })
        playlist_id = playlist['id']
        playlist_url = playlist['external_urls']['spotify']
    
    track_uris = []
    
    for info in tracks_info:
        # Si ya viene con su URI oficial validada, la usamos directamente (rápido y 100% exacto)
        uri = info.get('track_uri')
        if uri:
            track_uris.append(uri)
        else:
            artist = info.get('artist', info.get('artist_name', ''))
            track = info.get('track', info.get('item', info.get('track_name', '')))
            found = find_verified_spotify_track(sp, artist, track)
            if found:
                track_uris.append(found['uri'])
                
    if not track_uris:
        return playlist_url
        
    # Obtener los URIs ya presentes en la playlist para evitar agregar canciones duplicadas
    existing_uris = set()
    try:
        results = sp.playlist_items(playlist_id, limit=100)
        while results:
            for item in results.get('items', []):
                t = item.get('item') or item.get('track')
                if t and t.get('uri'):
                    existing_uris.add(t['uri'])
            if results.get('next'):
                results = sp.next(results)
            else:
                break
    except Exception as e:
        print("Aviso: No se pudieron leer tracks existentes de la playlist:", e)
        
    # Filtrar sólo aquellas canciones que aún no estén en la lista
    uris_to_add = [uri for uri in track_uris if uri not in existing_uris]
    
    if uris_to_add:
        # Add items in batches of 100 max
        for i in range(0, len(uris_to_add), 100):
            sp.playlist_add_items(playlist_id, uris_to_add[i:i+100])
            
    return playlist_url

def clean_text(s):
    import re
    s = str(s).lower().strip()
    s = re.sub(r'\(.*?\)', '', s)
    s = re.sub(r'\[.*?\]', '', s)
    s = re.sub(r'-\s*(remaster(ed)?|single\s*version|live|radio\s*edit|edit|mono|stereo|bonus\s*track).*$', '', s, flags=re.IGNORECASE)
    if s.startswith("the "): 
        s = s[4:]
    return "".join(c for c in s if c.isalnum() or c.isspace()).strip()

def find_verified_spotify_track(sp, artist, track):
    """
    Busca una canción en Spotify y verifica que el artista y el título
    coincidan de verdad con la recomendación (evitando temas erróneos de otros artistas).
    Si la canción puntual tiene ligeras discrepancias de catálogo pero el artista
    SÍ existe en Spotify con coincidencia exacta, rescata su tema más representativo
    para preservar el descubrimiento estético.
    """
    norm_artist = clean_text(artist)
    norm_track = clean_text(track)
    if not norm_artist:
        return None
        
    queries = [
        f'{artist} {track}',
        f'artist:"{artist}" track:"{track}"',
        f'"{artist}" "{track}"'
    ]
    
    # 1. Búsqueda directa del tema sugerido
    for q in queries:
        try:
            res = sp.search(q=q, type='track', limit=5)
            items = res.get('tracks', {}).get('items', [])
            for item in items:
                # 1. Verificar coincidencia del artista
                artist_matched = any(
                    clean_text(art.get('name', '')) == norm_artist or
                    clean_text(art.get('name', '')) in norm_artist or
                    norm_artist in clean_text(art.get('name', ''))
                    for art in item.get('artists', [])
                )
                if not artist_matched:
                    continue
                    
                # 2. Verificar coincidencia del título de la canción
                sp_track = clean_text(item.get('name', ''))
                if norm_track and (norm_track in sp_track or sp_track in norm_track):
                    return item
        except Exception:
            continue
            
    # 2. Smart Rescue: Rescate del tema representativo oficial en Spotify del artista confirmado
    try:
        res = sp.search(q=f'artist:"{artist}"', type='track', limit=3)
        items = res.get('tracks', {}).get('items', [])
        for item in items:
            for art in item.get('artists', []):
                sp_art_norm = clean_text(art.get('name', ''))
                if sp_art_norm == norm_artist or sp_art_norm in norm_artist or norm_artist in sp_art_norm:
                    return item
    except Exception:
        pass
        
    return None

def filter_and_verify_tracks(token_info, tracks_info, target_count=10, blocked_pool=None, seed_artist=None, existing_artists=None, *args, **kwargs):
    """
    Toma candidatos de canciones y:
    1. Verifica concurrentemente que existan 100% en el catálogo de Spotify (max_workers=5).
    2. Evita repetir el mismo artista dentro de la misma tanda y a través de pasadas previas (existing_artists).
    3. Descarta de forma absoluta cualquier track donde participe el artista semilla o un artista bloqueado (historial / guardados).
    4. Descarta canciones que el usuario ya tenga guardadas en sus 'Me Gusta' de Spotify usando una llamada única por lote (batch).
    Devuelve hasta target_count canciones totalmente validadas y enriquecidas con track_uri, cover_url y spotify_url.
    """
    from concurrent.futures import ThreadPoolExecutor
    from spotipy.oauth2 import SpotifyClientCredentials
    
    # Validación y resolución de token antes de iniciar hilos concurrentes
    if token_info and isinstance(token_info, dict) and 'access_token' in token_info:
        sp = spotipy.Spotify(auth=token_info['access_token'])
        has_user_auth = True
    else:
        cached = get_cached_token()
        if cached and 'access_token' in cached:
            sp = spotipy.Spotify(auth=cached['access_token'])
            has_user_auth = True
        else:
            sp = spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials())
            has_user_auth = False
            
    # 1. Pre-filtrado en memoria ultrarrápido (sin llamadas I/O)
    seen_raw_artists = set(normalize_name(a) for a in (existing_artists or []) if a)
    candidates_to_search = []
    
    for info in (tracks_info or []):
        artist = info.get('artist', info.get('artist_name', ''))
        track = info.get('item', info.get('track', info.get('track_name', '')))
        
        if not artist or not track:
            continue
            
        norm_art = normalize_name(artist)
        if norm_art in seen_raw_artists:
            continue
            
        if seed_artist and is_seed_conflict(artist, seed_artist):
            continue
            
        if blocked_pool and is_artist_blocked(artist, blocked_pool):
            continue
            
        seen_raw_artists.add(norm_art)
        candidates_to_search.append(info)
        
    if not candidates_to_search:
        return []
        
    # 2. Búsqueda paralela en Spotify (max_workers=5 para velocidad y thread-safety)
    def search_worker(cand):
        c_art = cand.get('artist', cand.get('artist_name', ''))
        c_trk = cand.get('item', cand.get('track', cand.get('track_name', '')))
        found_item = find_verified_spotify_track(sp, c_art, c_trk)
        return cand, found_item

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(search_worker, c) for c in candidates_to_search]
        results = [f.result() for f in futures]
        
    # 3. Post-filtrado de artistas acreditados (Escudo Spotify)
    pre_verified = []
    seen_artists = set(normalize_name(a) for a in (existing_artists or []) if a)
    seen_tracks = set()
    
    for cand_info, found in results:
        if not found:
            continue
            
        track_artists = found.get('artists', [])
        
        # Escudo Semilla: ¿Participa el artista semilla en solitario, collab o feat?
        if seed_artist and any(is_seed_conflict(art.get('name', ''), seed_artist) for art in track_artists):
            continue
            
        # Escudo Historial: ¿Participa algún artista bloqueado en historial o guardados?
        if blocked_pool and any(is_artist_blocked(art.get('name', ''), blocked_pool) for art in track_artists):
            continue
            
        primary_sp_artist = track_artists[0]['name'] if track_artists else cand_info.get('artist', '')
        primary_sp_norm = normalize_name(primary_sp_artist)
        
        if primary_sp_norm in seen_artists:
            continue
            
        track_name_norm = normalize_name(found.get('name', ''))
        if track_name_norm in seen_tracks:
            continue
            
        enriched = dict(cand_info)
        enriched['track_uri'] = found['uri']
        enriched['spotify_url'] = found['external_urls']['spotify']
        enriched['cover_url'] = found['album']['images'][0]['url'] if found['album']['images'] else None
        enriched['album_name'] = found['album']['name']
        enriched['artist'] = primary_sp_artist
        enriched['item'] = found['name']
        
        seen_artists.add(primary_sp_norm)
        seen_tracks.add(track_name_norm)
        pre_verified.append(enriched)
        
    if not pre_verified:
        return []
        
    # 4. Verificación de biblioteca guardada en LOTE (Batch call única a Spotify)
    saved_mask = []
    if has_user_auth and pre_verified:
        batch_uris = [t['track_uri'] for t in pre_verified[:50]]
        try:
            saved_mask = sp.current_user_saved_tracks_contains(tracks=batch_uris)
        except Exception as e:
            print("Aviso: Error en batch check de canciones guardadas:", e)
            saved_mask = [False] * len(batch_uris)
    else:
        saved_mask = [False] * len(pre_verified)
        
    final_verified = []
    for idx, t in enumerate(pre_verified):
        is_saved = saved_mask[idx] if idx < len(saved_mask) else False
        if not is_saved:
            final_verified.append(t)
            if len(final_verified) >= target_count:
                break
                
    return final_verified

def filter_unsaved_tracks(token_info, tracks_info, *args, **kwargs):
    target_count = kwargs.pop('target_count', 10)
    if args:
        target_count = args[0]
    return filter_and_verify_tracks(token_info, tracks_info, target_count=target_count, **kwargs)

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
    # Usa la URL definida en los secrets (que será la de Streamlit Cloud) o localhost por defecto
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI", "http://localhost:8501")
    
    if not client_id or not client_secret:
        raise ValueError("Faltan las credenciales de Spotify")

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
    return get_spotify_oauth().get_authorize_url()

def get_token(code):
    return get_spotify_oauth().get_access_token(code, as_dict=True)

def get_cached_token():
    return get_spotify_oauth().get_cached_token()

def get_currently_playing(token_info):
    """
    Returns the currently playing track if any, else None.
    """
    sp = spotipy.Spotify(auth=token_info['access_token'])
    try:
        current = sp.current_user_playing_track()
        if current and current.get('is_playing') and current.get('item'):
            track = current['item']
            return {
                'track_name': track['name'],
                'artist_name': track['artists'][0]['name'],
                'album_name': track['album']['name'],
                'cover_url': track['album']['images'][0]['url'] if track['album']['images'] else None,
                'url': track['external_urls'].get('spotify', '#')
            }
    except Exception:
        pass
    return None

def sync_recently_played_to_db(token_info):
    """
    Fetches the 50 most recently played tracks and inserts them into SQLite.
    Returns the number of new tracks added.
    """
    import pandas as pd
    import os
    from sqlalchemy import create_engine, text
    
    sp = spotipy.Spotify(auth=token_info['access_token'])
    try:
        recent = sp.current_user_recently_played(limit=50)
    except Exception as e:
        print("Error fetching recent tracks:", e)
        return 0
        
    if not recent or 'items' not in recent:
        return 0
        
    supabase_url = os.getenv("SUPABASE_URL")
    if not supabase_url:
        print("No SUPABASE_URL configured")
        return 0
    if supabase_url.startswith("postgresql://"):
        supabase_url = supabase_url.replace("postgresql://", "postgresql+psycopg2://")
    engine = create_engine(supabase_url)
    
    new_tracks_count = 0
    
    with engine.begin() as conn:
        for item in recent['items']:
            played_at_iso = item['played_at']
            track = item['track']
            
            # Convierte el ISO 8601 al formato de base de datos usando pandas para asegurar homogeneidad
            ts = pd.to_datetime(played_at_iso).strftime('%Y-%m-%d %H:%M:%S+00:00')
            year = pd.to_datetime(played_at_iso).year
            
            track_uri = track['uri']
            track_name = track['name']
            artist_name = track['artists'][0]['name']
            album_name = track['album']['name']
            
            # Spotify recently played doesn't return exactly ms_played by the user,
            # but we use the track's duration since it's fully registered as played.
            ms_played = track['duration_ms']
            minutes_played = ms_played / 60000.0
            hours_played = ms_played / 3600000.0
            
            # Check if this exact reproduction already exists
            exists = conn.execute(text("SELECT COUNT(*) FROM spotify_history WHERE track_uri = :uri AND ts = :ts"), {"uri": track_uri, "ts": ts}).scalar()
            
            if not exists:
                # We must match the schema exactly:
                # ts, year, ms_played, minutes_played, hours_played, track_name, artist_name, album_name, track_uri
                conn.execute(text('''
                    INSERT INTO spotify_history 
                    (ts, year, ms_played, minutes_played, hours_played, track_name, artist_name, album_name, track_uri)
                    VALUES (:ts, :year, :ms_played, :minutes_played, :hours_played, :track_name, :artist_name, :album_name, :track_uri)
                '''), {
                    "ts": ts, "year": year, "ms_played": ms_played, "minutes_played": minutes_played, 
                    "hours_played": hours_played, "track_name": track_name, "artist_name": artist_name, 
                    "album_name": album_name, "track_uri": track_uri
                })
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
    seed_name = playlist_name.split(":", 1)[1].strip().lower() if is_seed_playlist else ""
    
    exact_matches = []
    seed_matches = []
    motor_matches = []
    
    while playlists:
        for pl in playlists.get('items', []):
            if pl.get('owner', {}).get('id') == user_id:
                p_name_lower = pl.get('name', '').strip().lower()
                
                # 1. Coincidencia exacta con el nombre buscado
                if p_name_lower == p_name_clean:
                    exact_matches.append(pl)
                # 2. Si es playlist de artista semilla, coincide si empieza con "descubrimientos: {seed}"
                # (reutiliza playlists previas incluso si tenían sufijos como " (Universal)")
                elif is_seed_playlist and seed_name and p_name_lower.startswith(f"descubrimientos: {seed_name}"):
                    seed_matches.append(pl)
                # 3. Coincidencia por motor sólo si no es una playlist específica de artista semilla
                elif target_motor and target_motor in p_name_lower and not is_seed_playlist:
                    if "recomendaciones" in p_name_lower:
                        motor_matches.insert(0, pl)
                    else:
                        motor_matches.append(pl)
                        
        if exact_matches or seed_matches or motor_matches:
            if exact_matches:
                chosen = exact_matches[0]
            elif seed_matches:
                chosen = seed_matches[0]
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
        results = sp.playlist_items(playlist_id, fields='items.track.uri,next', additional_types=['track'], limit=100)
        while results:
            for item in results.get('items', []):
                t = item.get('track')
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

import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
from dotenv import load_dotenv

load_dotenv()

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

def create_spotify_playlist(playlist_name, description, tracks_info, token_info, motor=None):
    """
    tracks_info should be a list of dicts with 'artist' and 'track' (or 'item' for albums).
    """
    sp = spotipy.Spotify(auth=token_info['access_token'])
    
    user_id = sp.current_user()['id']
    
    # Check if an existing playlist exists for this motor or name
    playlists = sp.current_user_playlists(limit=50)
    playlist_id = None
    playlist_url = None
    
    target_motor = (motor or "").strip().lower()
    
    exact_matches = []
    motor_matches = []
    
    while playlists:
        for pl in playlists.get('items', []):
            if pl.get('owner', {}).get('id') == user_id:
                p_name_lower = pl.get('name', '').strip().lower()
                
                # Coincidencia exacta con el nombre buscado
                if p_name_lower == playlist_name.strip().lower():
                    exact_matches.append(pl)
                # Coincidencia por motor (ej: "Recomendaciones Last.fm - Octubre 2026", "Recomendaciones Last.fm", etc.)
                elif target_motor and target_motor in p_name_lower:
                    # Dar prioridad a las que dicen "recomendaciones"
                    if "recomendaciones" in p_name_lower:
                        motor_matches.insert(0, pl)
                    else:
                        motor_matches.append(pl)
                        
        if exact_matches or motor_matches:
            chosen = exact_matches[0] if exact_matches else motor_matches[0]
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
                
    if track_uris:
        # Add items in batches of 100 max
        for i in range(0, len(track_uris), 100):
            sp.playlist_add_items(playlist_id, track_uris[i:i+100])
        return playlist_url
    else:
        return None

def clean_text(s):
    import re
    s = str(s).lower().strip()
    s = re.sub(r'\(.*?\)', '', s)
    s = re.sub(r'\[.*?\]', '', s)
    if s.startswith("the "): 
        s = s[4:]
    return "".join(c for c in s if c.isalnum() or c.isspace()).strip()

def find_verified_spotify_track(sp, artist, track):
    """
    Busca una canción en Spotify y verifica que el artista y el título
    coincidan de verdad con la recomendación (evitando temas erróneos de otros artistas).
    """
    norm_artist = clean_text(artist)
    norm_track = clean_text(track)
    if not norm_artist or not norm_track:
        return None
        
    queries = [
        f'artist:"{artist}" track:"{track}"',
        f'"{artist}" "{track}"',
        f'{artist} {track}'
    ]
    
    for q in queries:
        try:
            res = sp.search(q=q, type='track', limit=5)
            items = res.get('tracks', {}).get('items', [])
            for item in items:
                # 1. Verificar coincidencia del artista
                artist_matched = False
                for art in item.get('artists', []):
                    sp_artist = clean_text(art.get('name', ''))
                    if sp_artist == norm_artist or sp_artist in norm_artist or norm_artist in sp_artist:
                        artist_matched = True
                        break
                if not artist_matched:
                    continue
                    
                # 2. Verificar coincidencia del título de la canción (no solo del disco)
                sp_track = clean_text(item.get('name', ''))
                if norm_track in sp_track or sp_track in norm_track:
                    return item
        except Exception:
            continue
    return None

def filter_and_verify_tracks(token_info, tracks_info, target_count=10, *args, **kwargs):
    """
    Toma candidatos de canciones y:
    1. Verifica que existan 100% en el catálogo de Spotify con coincidencia real de artista y canción.
    2. Evita repetir el mismo artista dentro de la misma tanda.
    3. Descarta canciones que el usuario ya tenga guardadas en sus 'Me Gusta' de Spotify.
    Devuelve hasta target_count canciones totalmente validadas y enriquecidas con track_uri, cover_url y spotify_url.
    """
    from spotipy.oauth2 import SpotifyClientCredentials
    
    if token_info and isinstance(token_info, dict) and 'access_token' in token_info:
        sp = spotipy.Spotify(auth=token_info['access_token'])
        has_user_auth = True
    else:
        sp = spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials())
        has_user_auth = False
        
    verified_tracks = []
    seen_artists = set()
    
    for info in tracks_info:
        artist = info.get('artist', info.get('artist_name', ''))
        track = info.get('item', info.get('track', info.get('track_name', '')))
        
        norm_art = clean_text(artist)
        if norm_art in seen_artists:
            continue # Prohibido repetir artista en la misma tanda
            
        found = find_verified_spotify_track(sp, artist, track)
        if not found:
            continue # Si no está en Spotify o el artista no coincide, se descarta
            
        track_uri = found['uri']
        
        # Verificar si ya está en la biblioteca guardada del usuario
        if has_user_auth:
            try:
                is_saved = sp.current_user_saved_tracks_contains(tracks=[track_uri])[0]
                if is_saved:
                    continue # Descartar: ya la tiene guardada en su Spotify
            except Exception as e:
                print("Error checking saved tracks:", e)
                
        enriched = dict(info)
        enriched['track_uri'] = track_uri
        enriched['spotify_url'] = found['external_urls']['spotify']
        enriched['cover_url'] = found['album']['images'][0]['url'] if found['album']['images'] else None
        enriched['album_name'] = found['album']['name']
        enriched['artist'] = found['artists'][0]['name']
        enriched['item'] = found['name']
        
        verified_tracks.append(enriched)
        seen_artists.add(norm_art)
        
        if len(verified_tracks) == target_count:
            break
            
    return verified_tracks

def filter_unsaved_tracks(token_info, tracks_info, *args, **kwargs):
    target_count = kwargs.get('target_count', 10)
    if args:
        target_count = args[0]
    return filter_and_verify_tracks(token_info, tracks_info, target_count=target_count)

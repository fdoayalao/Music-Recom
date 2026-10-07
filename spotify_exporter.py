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

def sync_recently_played_to_db(token_info, db_path):
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

def create_spotify_playlist(playlist_name, description, tracks_info, token_info):
    """
    tracks_info should be a list of dicts with 'artist' and 'track' (or 'item' for albums).
    """
    sp = spotipy.Spotify(auth=token_info['access_token'])
    
    user_id = sp.current_user()['id']
    
    # Check if a playlist with this exact name already exists
    playlists = sp.current_user_playlists(limit=50)
    playlist_id = None
    playlist_url = None
    
    while playlists:
        for pl in playlists['items']:
            if pl['name'] == playlist_name and pl['owner']['id'] == user_id:
                playlist_id = pl['id']
                playlist_url = pl['external_urls']['spotify']
                break
        if playlist_id:
            break
        if playlists['next']:
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
        artist = info.get('artist', info.get('artist_name', ''))
        track = info.get('track', info.get('item', info.get('track_name', '')))
        
        # Search query
        query = f"artist:{artist} {track}"
        result = sp.search(q=query, type='track', limit=1)
        
        tracks = result['tracks']['items']
        if tracks:
            track_uris.append(tracks[0]['uri'])
        else:
            # Fallback: just search the artist
            fallback_query = f"artist:{artist}"
            fallback_result = sp.search(q=fallback_query, type='track', limit=1)
            if fallback_result['tracks']['items']:
                track_uris.append(fallback_result['tracks']['items'][0]['uri'])
                
    if track_uris:
        # Add items in batches of 100 max
        for i in range(0, len(track_uris), 100):
            sp.playlist_add_items(playlist_id, track_uris[i:i+100])
        return playlist_url
    else:
        return None

def filter_unsaved_tracks(token_info, tracks_info):
    """
    Toma una lista de diccionarios [{'artist': 'A', 'track': 'B', 'reason': 'C'}, ...]
    Busca sus URIs, revisa si el usuario ya las tiene guardadas,
    y devuelve solo la lista de canciones que NO están guardadas en sus 'Me Gusta'.
    """
    sp = spotipy.Spotify(auth=token_info['access_token'])
    unsaved_tracks = []
    
    for info in tracks_info:
        artist = info.get('artist_name', info.get('artist', ''))
        track = info.get('track_name', info.get('track', ''))
        
        query = f"artist:{artist} track:{track}"
        result = sp.search(q=query, type='track', limit=1)
        
        tracks = result['tracks']['items']
        if tracks:
            track_uri = tracks[0]['uri']
            try:
                is_saved = sp.current_user_saved_tracks_contains(tracks=[track_uri])[0]
            except Exception as e:
                # Si falla por permisos viejos, asumimos falso para no romper todo
                print("Error verificando saved tracks:", e)
                is_saved = False
                
            if not is_saved:
                info['spotify_url'] = tracks[0]['external_urls']['spotify']
                info['cover_url'] = tracks[0]['album']['images'][0]['url'] if tracks[0]['album']['images'] else None
                info['album_name'] = tracks[0]['album']['name']
                unsaved_tracks.append(info)
        else:
            # Si no se encuentra en Spotify, la pasamos igual porque es un hallazgo raro
            unsaved_tracks.append(info)
            
    return unsaved_tracks

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

    scope = "playlist-modify-public playlist-modify-private playlist-read-private"
    
    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=scope,
        open_browser=False,
        cache_handler=spotipy.cache_handler.MemoryCacheHandler()
    )

def get_auth_url():
    return get_spotify_oauth().get_authorize_url()

def get_token(code):
    return get_spotify_oauth().get_access_token(code, as_dict=True)

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

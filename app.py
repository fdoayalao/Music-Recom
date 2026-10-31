import streamlit as st
import sqlite3
import pandas as pd
import os
import re
from datetime import datetime
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

load_dotenv()

# Import new modules safely
try:
    from recommender import get_recommendations
except ImportError:
    get_recommendations = None

try:
    from spotify_exporter import (
        create_spotify_playlist, get_auth_url, get_token, get_currently_playing,
        sync_recently_played_to_db, get_cached_token, filter_unsaved_tracks, ensure_valid_token,
        get_spotify_oauth, get_env_or_secret
    )
except Exception as e:
    print(f"Error importing spotify_exporter: {e}")
    create_spotify_playlist = None
    get_auth_url = None
    get_token = None
    get_currently_playing = None
    sync_recently_played_to_db = None
    get_cached_token = None
    filter_unsaved_tracks = None
    ensure_valid_token = None
    get_spotify_oauth = None
    get_env_or_secret = None
    
import zipfile
import time

# Set up page configuration (needs to be the first Streamlit command)
st.set_page_config(page_title="Music Recommender", layout="wide")

# Extraer base de datos si está comprimida (para la nube)
if not os.path.exists('spotify_data.db') and os.path.exists('spotify_data.zip'):
    with zipfile.ZipFile('spotify_data.zip', 'r') as zip_ref:
        zip_ref.extractall('.')

# Manejo del Callback de Spotify (OAuth Web Flow)
if "code" in st.query_params:
    code = st.query_params["code"]
    try:
        if create_spotify_playlist:
            token_info = get_token(code)
            st.session_state['spotify_token'] = token_info
            # Limpiar la URL para no re-procesar el código al refrescar
            st.query_params.clear()
            st.success("¡Autenticado con Spotify exitosamente!")
    except Exception as e:
        st.error(f"Error al iniciar sesión en Spotify: {e}")

# Intento de cargar token persistente desde la caché si no hay uno en sesión
if 'spotify_token' not in st.session_state and get_cached_token:
    cached = get_cached_token()
    if cached:
        st.session_state['spotify_token'] = cached

# Auto-Sync Inteligente con Cooldown (15 minutos)
SYNC_COOLDOWN_SECONDS = 15 * 60  # 15 minutos

if 'spotify_token' in st.session_state and sync_recently_played_to_db:
    # Ajuste 3: Thread/Session safety - asegurar token fresco en sesión
    valid_token = ensure_valid_token(st.session_state['spotify_token']) if ensure_valid_token else st.session_state['spotify_token']
    if valid_token:
        st.session_state['spotify_token'] = valid_token
        current_time = time.time()
        last_sync = st.session_state.get('last_sync_timestamp', 0)
        
        if (current_time - last_sync) > SYNC_COOLDOWN_SECONDS:
            try:
                added = sync_recently_played_to_db(valid_token)
                st.session_state['last_sync_timestamp'] = current_time
                st.session_state['last_sync_added'] = added
                # Ajuste 2: Invalidador de caché y rerun inmediato si se agregaron nuevas reproducciones
                if added > 0:
                    st.cache_data.clear()
                    st.rerun()
            except Exception as e:
                print(f"Aviso en auto-sync desatendido de Spotify: {e}")

# Custom CSS for aesthetics
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

    /* Variables de color Glassmorphism */
    :root {
        --bg-main: #0B0F19; /* Un azul/gris medianoche ultra oscuro, no 100% negro */
        --bg-card: rgba(255, 255, 255, 0.03);
        --border-color: rgba(255, 255, 255, 0.08);
        --text-main: #ffffff;
        --text-sec: #a1a1aa;
        --accent: #38bdf8;
        --accent-alt: #c084fc;
    }

    /* Fondo principal y tipografía general */
    .stApp {
        background: radial-gradient(circle at 15% 50%, rgba(192, 132, 252, 0.12), transparent 40%),
                    radial-gradient(circle at 85% 30%, rgba(56, 189, 248, 0.12), transparent 40%),
                    var(--bg-main) !important;
        color: var(--text-main) !important;
        font-family: 'Inter', sans-serif !important;
    }

    /* Encabezados */
    h1, h2, h3, .st-emotion-cache-10trblm {
        font-family: 'Inter', sans-serif !important;
        color: var(--text-main) !important;
        font-weight: 800 !important;
        letter-spacing: -0.5px !important;
    }

    /* Tarjetas de Métricas */
    .metric-card {
        background: var(--bg-card);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border-radius: 16px;
        padding: 24px;
        text-align: center;
        border: 1px solid var(--border-color);
        box-shadow: 0 8px 32px rgba(0,0,0,0.2);
    }
    .metric-value {
        font-family: 'Inter', sans-serif;
        font-size: 2.5rem;
        font-weight: 800;
        background: linear-gradient(90deg, var(--accent-alt), var(--accent));
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .metric-label {
        font-family: 'Inter', sans-serif;
        color: var(--text-sec);
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 2px;
        margin-top: 8px;
        font-weight: 600;
    }

    /* Diseño de Tarjetas de Recomendación (Glassmorphism Suave) */
    .rec-card {
        background: var(--bg-card);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.05);
        padding: 20px 24px;
        margin-bottom: 20px;
        border-radius: 14px;
        position: relative;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);
        transition: transform 0.25s ease, border-color 0.25s ease, box-shadow 0.25s ease;
    }
    .rec-card:hover {
        transform: translateY(-3px);
        border-color: rgba(56, 189, 248, 0.25);
        box-shadow: 0 6px 24px rgba(0, 0, 0, 0.12);
    }
    .rec-number {
        position: absolute;
        top: 20px;
        right: 24px;
        font-family: 'Inter', sans-serif;
        font-weight: 800;
        color: rgba(255, 255, 255, 0.08);
        font-size: 1.8rem;
        line-height: 1;
    }
    .rec-title {
        font-family: 'Inter', sans-serif;
        font-size: 1.4rem;
        font-weight: 700;
        margin-bottom: 4px;
        color: var(--text-main);
        letter-spacing: -0.5px;
    }
    .rec-artist {
        font-family: 'Inter', sans-serif;
        font-size: 1.05rem;
        font-weight: 500;
        color: var(--text-sec);
        margin-bottom: 14px;
    }
    .rec-pills {
        font-family: 'Inter', sans-serif;
        font-size: 0.7rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 1.5px;
        color: var(--accent);
        margin-bottom: 12px;
        display: inline-block;
        background: rgba(56, 189, 248, 0.08);
        padding: 3px 10px;
        border-radius: 100px;
    }
    .rec-reason {
        background: rgba(255, 255, 255, 0.02);
        border-left: 2px solid var(--accent-alt);
        padding: 10px 14px;
        border-radius: 0 6px 6px 0;
        font-family: 'Inter', sans-serif;
        color: #e4e4e7;
        font-size: 0.9rem;
        line-height: 1.55;
    }

    /* Contenedores con borde (st.container(border=True)) estilo Glassmorphism */
    div[data-testid="stVerticalBlockBorderWrapper"] > div {
        border-color: var(--border-color) !important;
        background: var(--bg-card) !important;
        backdrop-filter: blur(12px) !important;
        -webkit-backdrop-filter: blur(12px) !important;
        border-radius: 14px !important;
    }

    /* Botones generales (incluye Spotify) */
    .stButton > button {
        background: linear-gradient(90deg, var(--accent-alt), var(--accent)) !important;
        color: #ffffff !important;
        font-family: 'Inter', sans-serif !important;
        font-weight: 700 !important;
        border-radius: 100px !important;
        border: none !important;
        padding: 12px 28px !important;
        letter-spacing: 0.5px !important;
        transition: all 0.3s ease !important;
        box-shadow: 0 4px 15px rgba(192, 132, 252, 0.3) !important;
    }
    .stButton > button:hover {
        transform: scale(1.02) !important;
        box-shadow: 0 6px 20px rgba(56, 189, 248, 0.4) !important;
    }

    /* Estilos para las Pestañas (Tabs) de Streamlit */
    button[data-baseweb="tab"] > div[data-testid="stMarkdownContainer"] p {
        font-family: 'Inter', sans-serif !important;
        font-weight: 700 !important;
        font-size: 1.05rem !important;
    }
    div[data-baseweb="tab-highlight"] {
        background: linear-gradient(90deg, var(--accent-alt), var(--accent)) !important;
        border-radius: 4px !important;
        height: 3px !important;
    }
    button[data-baseweb="tab"][aria-selected="true"] > div[data-testid="stMarkdownContainer"] p {
        background: linear-gradient(90deg, var(--accent-alt), var(--accent));
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }

    /* Diseño de Tablas Modernas (Glassmorphism) */
    .glass-table-container {
        overflow-x: auto;
        border-radius: 16px;
        background: var(--bg-card);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid var(--border-color);
        margin-bottom: 24px;
        box-shadow: 0 4px 30px rgba(0, 0, 0, 0.1);
    }
    .glass-table {
        width: 100%;
        border-collapse: collapse;
        font-family: 'Inter', sans-serif;
        text-align: left;
    }
    .glass-table th {
        background: rgba(255, 255, 255, 0.05);
        color: var(--text-sec);
        font-weight: 700;
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 1px;
        padding: 16px 20px;
        border-bottom: 1px solid var(--border-color);
    }
    .glass-table td {
        padding: 16px 20px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.02);
        color: var(--text-main);
        font-size: 0.95rem;
        font-weight: 500;
    }
    .glass-table tr:last-child td {
        border-bottom: none;
    }
    .glass-table tr:hover td {
        background: rgba(255, 255, 255, 0.04);
    }

    /* Responsividad para Celulares */
    @media (max-width: 768px) {
        h1, .st-emotion-cache-10trblm {
            font-size: 1.8rem !important;
        }
        h2, h3 {
            font-size: 1.4rem !important;
        }
        .rec-card {
            padding: 16px;
        }
        .rec-title {
            font-size: 1.25rem;
            margin-right: 45px; /* Evita que el título choque con el número [01] */
        }
        .rec-number {
            font-size: 1.3rem;
            top: 16px;
            right: 16px;
        }
        .rec-artist {
            font-size: 0.95rem;
        }
        .rec-reason {
            font-size: 0.85rem;
            padding: 12px;
        }
        .glass-table th, .glass-table td {
            padding: 10px;
            font-size: 0.8rem;
        }
        .metric-card {
            padding: 12px;
        }
        .metric-value {
            font-size: 1.8rem;
        }
    }
</style>
""", unsafe_allow_html=True)

# DB path
DB_PATH = "spotify_data.db"

def render_glass_table(df, show_index=True):
    html = '<div class="glass-table-container"><table class="glass-table"><thead><tr>'
    if show_index:
        html += '<th>#</th>'
    for col in df.columns:
        html += f'<th>{col}</th>'
    html += '</tr></thead><tbody>'
    for idx, row in df.iterrows():
        html += '<tr>'
        if show_index:
            html += f'<td style="color: var(--accent); font-weight: bold;">{idx}</td>'
        for val in row:
            if isinstance(val, (int, float)):
                if val == int(val):
                    val_str = f"{int(val):,}".replace(",", ".")
                else:
                    val_str = f"{val:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
                html += f'<td>{val_str}</td>'
            else:
                html += f'<td>{val}</td>'
        html += '</tr>'
    html += '</tbody></table></div>'
    st.markdown(html, unsafe_allow_html=True)

from sqlalchemy import create_engine, text

def get_engine():
    supabase_url = os.getenv("SUPABASE_URL")
    if not supabase_url:
        return None
    if supabase_url.startswith("postgresql://"):
        supabase_url = supabase_url.replace("postgresql://", "postgresql+psycopg2://")
    return create_engine(supabase_url)

@st.cache_data(max_entries=1)
def get_years():
    engine = get_engine()
    if not engine:
        return []
    try:
        years_df = pd.read_sql_query("SELECT DISTINCT year FROM spotify_history ORDER BY year DESC", engine)
        years = years_df['year'].tolist()
    except Exception as e:
        print(f"Error fetching years: {e}")
        years = []
    return years

@st.cache_data(max_entries=2)
def load_data(year_filter="All Time"):
    engine = get_engine()
    if not engine:
        return pd.DataFrame()
        
    if year_filter == "All Time":
        df = pd.read_sql_query("SELECT * FROM spotify_history", engine)
    else:
        df = pd.read_sql_query("SELECT * FROM spotify_history WHERE year = %(year)s", engine, params={"year": int(year_filter)})
        
    if not df.empty:
        df['ts'] = pd.to_datetime(df['ts'])
    return df

@st.cache_data(ttl=600)
def get_feedback_signals():
    """
    Cruza saved_recommendations con spotify_history donde la reproducción
    ocurrió con fecha posterior a la recomendación generada.
    Retorna hits confirmados (escuchados 2 o más veces).
    """
    db_engine = get_engine()
    if not db_engine:
        return []
    try:
        query = text("""
            SELECT 
                sr.artist_name, 
                sr.track_name, 
                sr.motor,
                sr.created_at,
                COUNT(sh.ts) AS plays_after
            FROM saved_recommendations sr
            LEFT JOIN spotify_history sh
              ON LOWER(TRIM(sr.artist_name)) = LOWER(TRIM(sh.artist_name))
              AND LOWER(TRIM(sr.track_name)) = LOWER(TRIM(sh.track_name))
              AND sh.ts > sr.created_at
            GROUP BY sr.id, sr.artist_name, sr.track_name, sr.motor, sr.created_at
            HAVING COUNT(sh.ts) >= 2
            ORDER BY plays_after DESC
            LIMIT 10;
        """)
        with db_engine.connect() as conn:
            hits = conn.execute(query).fetchall()
            return [{'artist': r[0], 'track': r[1], 'plays': r[4]} for r in hits]
    except Exception as e:
        print(f"Error fetching feedback signals: {e}")
        return []

# Main Header
st.title("Music Recommender")

# Check if DB exists
engine = get_engine()
if not engine:
    st.error("No se encontró la variable SUPABASE_URL en el entorno (.env o Secrets).")
    st.stop()


available_years = get_years()

# Reproduciendo Ahora (Visible en la parte superior si está vinculado a Spotify)
token_for_player = st.session_state.get('spotify_token') or (get_cached_token() if get_cached_token else None)
if token_for_player and get_currently_playing:
    try:
        now_playing = get_currently_playing(token_for_player)
        if now_playing:
            cover_img = now_playing.get('cover_url') or "https://storage.googleapis.com/pr-newsroom-wp/1/2018/11/Spotify_Logo_RGB_Green.png"
            track_name = now_playing.get('track_name', 'Desconocido')
            artist_name = now_playing.get('artist_name', 'Artista')
            album_name = now_playing.get('album_name', '')
            album_info = f" • {album_name}" if album_name else ""
            track_url = now_playing.get('url', '#')
            is_playing = now_playing.get('is_playing', True)
            
            badge_text = "Escuchando Ahora" if is_playing else "Última Reproducción"
            badge_color = "#1DB954" if is_playing else "#38bdf8"
            spin_style = "animation: spin 5s linear infinite;" if is_playing else ""
            dot_shadow = f"box-shadow: 0 0 8px {badge_color};"
            border_color = "rgba(29, 185, 84, 0.4)" if is_playing else "rgba(56, 189, 248, 0.3)"
            bg_gradient = "linear-gradient(90deg, rgba(29, 185, 84, 0.15) 0%, rgba(56, 189, 248, 0.08) 100%)" if is_playing else "linear-gradient(90deg, rgba(56, 189, 248, 0.12) 0%, rgba(192, 132, 252, 0.08) 100%)"

            st.markdown(f"""
            <a href="{track_url}" target="_blank" style="text-decoration: none; color: inherit; display: block; margin-bottom: 24px;">
                <div style="display: flex; align-items: center; justify-content: space-between; background: {bg_gradient}; border: 1px solid {border_color}; border-radius: 12px; padding: 12px 18px; box-shadow: 0 4px 15px rgba(0,0,0,0.2); transition: border-color 0.2s ease;">
                    <div style="display: flex; align-items: center; gap: 14px;">
                        <img src="{cover_img}" style="width: 52px; height: 52px; border-radius: 50%; object-fit: cover; border: 2px solid {badge_color}; {spin_style} flex-shrink: 0;">
                        <div>
                            <div style="display: flex; align-items: center; gap: 6px;">
                                <span style="display: inline-block; width: 8px; height: 8px; background-color: {badge_color}; border-radius: 50%; {dot_shadow}"></span>
                                <span style="font-size: 0.72rem; color: {badge_color}; font-weight: 800; text-transform: uppercase; letter-spacing: 1px;">{badge_text}</span>
                            </div>
                            <div style="font-weight: 700; font-size: 1.1rem; color: #ffffff; margin-top: 2px;">{track_name}</div>
                            <div style="font-size: 0.88rem; color: #a1a1aa;">{artist_name}{album_info}</div>
                        </div>
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="font-size: 0.8rem; color: {badge_color}; font-weight: 600;">Abrir en Spotify</span>
                        <span style="color: {badge_color}; font-size: 1.1rem;">↗</span>
                    </div>
                </div>
            </a>
            <style>
                @keyframes spin {{ 100% {{ transform: rotate(360deg); }} }}
            </style>
            """, unsafe_allow_html=True)
    except Exception as e:
        print(f"Aviso reproduciendo ahora: {e}")

# Sidebar Navigation
st.sidebar.image("https://storage.googleapis.com/pr-newsroom-wp/1/2018/11/Spotify_Logo_RGB_Green.png", width=150)

# ---------------------------------------------------------------------
# BARRA LATERAL: CONTROL VISIBLE DE SPOTIFY
# ---------------------------------------------------------------------
with st.sidebar.container(border=True):
    st.markdown("<div style='font-size: 0.95rem; font-weight: 700; margin-bottom: 6px;'>Conexión Spotify</div>", unsafe_allow_html=True)

    auth_manager = get_spotify_oauth() if callable(get_spotify_oauth) else None
    if not auth_manager:
        missing = []
        has_cid = get_env_or_secret(["SPOTIPY_CLIENT_ID", "SPOTIFY_CLIENT_ID"]) if callable(get_env_or_secret) else os.getenv("SPOTIPY_CLIENT_ID")
        has_sec = get_env_or_secret(["SPOTIPY_CLIENT_SECRET", "SPOTIFY_CLIENT_SECRET"]) if callable(get_env_or_secret) else os.getenv("SPOTIPY_CLIENT_SECRET")
        if not has_cid:
            missing.append("SPOTIPY_CLIENT_ID")
        if not has_sec:
            missing.append("SPOTIPY_CLIENT_SECRET")
        missing_str = ", ".join(missing) if missing else "credenciales"
        st.error(f"Faltan configurar en Secrets: {missing_str}")
    else:
        token = st.session_state.get('spotify_token') or (get_cached_token() if callable(get_cached_token) else None)
        if token:
            st.session_state['spotify_token'] = token

        if not token:
            auth_url = auth_manager.get_authorize_url()
            st.caption("Desconectado")
            st.link_button("Conectar Spotify", auth_url, use_container_width=True)
        else:
            last_sync = st.session_state.get('last_sync_timestamp')
            if last_sync:
                mins = int((time.time() - last_sync) // 60)
                time_text = "hace un instante" if mins < 1 else f"hace {mins} min"
                st.caption(f"Sincronizado: {time_text}")
            else:
                st.caption("Conectado")

            if st.button("Live Sync", use_container_width=True):
                with st.spinner("Sincronizando con Spotify..."):
                    try:
                        valid_tok = ensure_valid_token(token) if ensure_valid_token else token
                        if valid_tok:
                            st.session_state['spotify_token'] = valid_tok
                            token = valid_tok
                        added = sync_recently_played_to_db(token)
                        st.session_state['last_sync_timestamp'] = time.time()
                        st.session_state['last_sync_added'] = added
                        st.cache_data.clear()
                        st.success(f"{added} reproducciones nuevas.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al sincronizar: {e}")

# Global Filters
st.sidebar.header("Filtros Globales")
period_options = ["All Time"] + [str(y) for y in available_years]
selected_period = st.sidebar.selectbox("Selecciona un período", period_options)

page = st.sidebar.radio("Navegación", ["Recomendador", "Estadísticas", "Diario"])

if page == "Estadísticas":
    st.sidebar.markdown("### About")
    st.sidebar.info("Dashboard of your extended Spotify streaming history (>30 seconds).")

    with st.spinner("Loading data..."):
        df = load_data(selected_period)

    if df.empty:
        st.warning("No data found for the selected period.")
        st.stop()

    # --- Overview Metrics ---
    st.markdown(f"### Overview: {selected_period}")
    col1, col2, col3, col4 = st.columns(4)

    total_hours = df['hours_played'].sum()
    total_streams = len(df)
    unique_artists = df['artist_name'].nunique()
    unique_tracks = df['track_name'].nunique()

    col1.markdown(f'<div class="metric-card"><div class="metric-value">{total_hours:,.0f}</div><div class="metric-label">Total Hours</div></div>', unsafe_allow_html=True)
    col2.markdown(f'<div class="metric-card"><div class="metric-value">{total_streams:,}</div><div class="metric-label">Total Streams</div></div>', unsafe_allow_html=True)
    col3.markdown(f'<div class="metric-card"><div class="metric-value">{unique_artists:,}</div><div class="metric-label">Artists</div></div>', unsafe_allow_html=True)
    col4.markdown(f'<div class="metric-card"><div class="metric-value">{unique_tracks:,}</div><div class="metric-label">Songs</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # --- Top 50 Rankings ---
    st.markdown("### Top 50 Rankings")

    # Exclude "Mocasines" from the rankings (ghost artist)
    ranking_df = df[df['artist_name'].str.lower() != 'mocasines']

    tab1, tab2, tab3 = st.tabs(["Top Artistas", "Top Canciones", "Top Álbumes"])

    with tab1:
        top_artists = ranking_df.groupby('artist_name').agg(
            Play_Count=('ts', 'count'),
            Hours_Listened=('hours_played', 'sum')
        ).reset_index().sort_values(by='Play_Count', ascending=False).head(50)
        top_artists['Hours_Listened'] = top_artists['Hours_Listened'].round(1)
        top_artists.rename(columns={'artist_name': 'Artist', 'Play_Count': 'Streams', 'Hours_Listened': 'Hours Listened'}, inplace=True)
        top_artists.index = range(1, 51)
        render_glass_table(top_artists)

    with tab2:
        top_songs = ranking_df.groupby(['track_name', 'artist_name']).agg(
            Play_Count=('ts', 'count'),
            Hours_Listened=('hours_played', 'sum')
        ).reset_index().sort_values(by='Play_Count', ascending=False).head(50)
        top_songs['Hours_Listened'] = top_songs['Hours_Listened'].round(1)
        top_songs.rename(columns={'track_name': 'Song', 'artist_name': 'Artist', 'Play_Count': 'Streams', 'Hours_Listened': 'Hours Listened'}, inplace=True)
        top_songs.index = range(1, 51)
        render_glass_table(top_songs)

    with tab3:
        top_albums = ranking_df.groupby(['album_name', 'artist_name']).agg(
            Play_Count=('ts', 'count'),
            Hours_Listened=('hours_played', 'sum')
        ).reset_index().sort_values(by='Play_Count', ascending=False).head(50)
        top_albums['Hours_Listened'] = top_albums['Hours_Listened'].round(1)
        top_albums.rename(columns={'album_name': 'Album', 'artist_name': 'Artist', 'Play_Count': 'Streams', 'Hours_Listened': 'Hours Listened'}, inplace=True)
        top_albums.index = range(1, 51)
        top_albums.index = range(1, 51)
        render_glass_table(top_albums)

elif page == "Diario":
    st.markdown('<h2 style="margin-bottom: 24px;">Diario de Descubrimientos</h2>', unsafe_allow_html=True)
    st.markdown("Aquí se guardan para la posteridad todas las recomendaciones exitosas que el Recomendador Inteligente ha generado para ti.")
    
    if engine:
        try:
            history_df = pd.read_sql_query("SELECT * FROM saved_recommendations ORDER BY created_at DESC", engine)
            if history_df.empty:
                st.info("Aún no tienes recomendaciones guardadas. ¡Genera algunas en la pestaña de Recomendador!")
            else:
                motores_disponibles = ["Todos"] + sorted(history_df['motor'].dropna().unique().tolist())
                motor_filtro = st.selectbox("Filtrar por motor de búsqueda", motores_disponibles)
                
                if motor_filtro != "Todos":
                    history_df = history_df[history_df['motor'] == motor_filtro]
                    
                st.markdown(f"**Mostrando {len(history_df)} recomendaciones guardadas:**")
                
                for idx, row in history_df.iterrows():
                    badge = row.get('badge', '')
                    badge_html = ""
                    if badge:
                        clean_badge = re.sub(r'[^\w\s-]', '', str(badge)).strip().upper()
                        if clean_badge:
                            badge_color = "#38bdf8"
                            if "RECONEXIÓN" in clean_badge: badge_color = "#c084fc"
                            elif "LADO B" in clean_badge: badge_color = "#f59e0b"
                            elif "PUNTO CIEGO" in clean_badge: badge_color = "#10b981"
                            elif "CURADURÍA" in clean_badge or "UNIVERSAL" in clean_badge: badge_color = "#ec4899"
                            
                            badge_html = f'<div style="display:inline-block; background-color:{badge_color}12; color:{badge_color}; border: 1px solid {badge_color}40; padding: 2px 8px; border-radius: 4px; font-size: 0.65rem; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 8px;">{clean_badge}</div>'
                    
                    # Convert to local timezone strings or format directly
                    date_str = str(row['created_at']).split('.')[0]
                    
                    cover_img = f'<img src="{row["cover_url"]}" style="width: 56px; height: 56px; border-radius: 8px; margin-right: 14px; float: left; object-fit: cover; box-shadow: 0 4px 12px rgba(0,0,0,0.5);">' if row.get('cover_url') else ''
                    title_display = f'<a href="{row["spotify_url"]}" target="_blank" style="color:white; text-decoration:none; hover:underline;">{row["track_name"]} ↗</a>' if row.get('spotify_url') else row['track_name']
                    
                    card_html = f"""
                    <div class="rec-card" style="margin-bottom: 24px; overflow: hidden;">
                        <div class="rec-number" style="font-size: 0.8rem; top: 12px; right: 12px; opacity: 0.5;">{date_str}</div>
                        {badge_html}
                        <div style="display: flex; align-items: center; margin-top: 6px;">
                            {cover_img}
                            <div>
                                <div class="rec-title">{title_display}</div>
                                <div class="rec-artist">por {row['artist_name']}</div>
                            </div>
                        </div>
                        <div style="clear: both; margin-top: 10px;"></div>
                        <div class="rec-pills">/ MOTOR: {row['motor']} /</div>
                        <div class="rec-reason">"{row['reason']}"</div>
                    </div>
                    """
                    st.markdown(card_html, unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Error cargando el diario: {e}")

elif page == "Recomendador":
    has_gemini = os.getenv("GEMINI_API_KEY") or (hasattr(st, 'secrets') and st.secrets.get("GEMINI_API_KEY"))
    has_spotify = os.getenv("SPOTIPY_CLIENT_ID") or (hasattr(st, 'secrets') and st.secrets.get("SPOTIPY_CLIENT_ID"))
    if not has_gemini or not has_spotify:
        with st.expander("Configuración de Credenciales Requerida", expanded=True):
            st.warning("Faltan claves de API en el archivo `.env`.")
            st.markdown("""
            Para que estas funciones operen, debes crear un archivo `.env` en la misma carpeta del script con lo siguiente:
            
            ```env
            GEMINI_API_KEY="tu_clave_gemini"
            SPOTIPY_CLIENT_ID="tu_client_id_de_spotify"
            SPOTIPY_CLIENT_SECRET="tu_client_secret_de_spotify"
            SPOTIPY_REDIRECT_URI="http://127.0.0.1:8501"
            ```
            **Pasos para Spotify:**
            1. Ve a [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
            2. Inicia sesión y haz click en "Create app".
            3. En "Redirect URIs" de las configuraciones de tu app, pon exactamente `http://127.0.0.1:8501` y guárdalo.
            4. Copia el Client ID y Client Secret al archivo `.env`.
            """)

    df = load_data(selected_period)
    
    if df.empty:
        st.warning("Base de datos vacía.")
        st.stop()
        
    # Excluir a "Mocasines" de los cálculos de la IA y Gemas
    df = df[df['artist_name'].str.lower() != 'mocasines']
        
    tabA, tabB = st.tabs(["Gemas de Catálogo", "Curador IA"])
    
    with tabA:
        st.subheader("Gemas de Catálogo")
        st.write("Canciones que amabas (más de 15 reproducciones) pero que llevas al menos 18 meses sin escuchar.")
        
        # Calculate Gemas Olvidadas
        max_date = df['ts'].max()
        cutoff_date = max_date - pd.DateOffset(months=18)
        
        # Total plays per track
        track_stats = df.groupby(['track_name', 'artist_name']).agg(
            Total_Plays=('ts', 'count'),
            Last_Played=('ts', 'max')
        ).reset_index()
        
        gemas = track_stats[(track_stats['Total_Plays'] > 15) & (track_stats['Last_Played'] < cutoff_date)]
        gemas = gemas.sort_values(by='Total_Plays', ascending=False)
        
        if gemas.empty:
            st.info("No tienes gemas olvidadas que cumplan este criterio.")
        else:
            gemas.index = range(1, len(gemas) + 1)
            # Formatear la fecha para que no salga +00:00
            gemas['Last_Played'] = gemas['Last_Played'].dt.strftime('%Y-%m-%d')
            render_glass_table(gemas[['artist_name', 'track_name', 'Total_Plays', 'Last_Played']])
            
            if st.button("Crear Playlist de Gemas en Spotify"):
                if create_spotify_playlist:
                    with st.spinner("Creando playlist... revisa la consola o navegador para autorizar."):
                        tracks_to_send = [{'artist': r['artist_name'], 'track': r['track_name']} for i, r in gemas.iterrows()]
                        try:
                            url = create_spotify_playlist("Gemas Olvidadas", "Reviviendo canciones olvidadas desde mi historial de Spotify.", tracks_to_send)
                            if url:
                                st.success(f"¡Playlist creada con éxito! [Abrir en Spotify]({url})")
                        except Exception as e:
                            st.error(f"Error al conectar con Spotify: {e}")
                else:
                    st.error("El módulo de exportación de Spotify no está cargado correctamente. Revisa que instalaste spotipy y reinicia la app.")

    with tabB:
        st.subheader("Curador Algorítmico")
        
        rec_mode = st.radio(
            "Modo de Exploración:",
            ["Mi Perfil y Tendencias", "Por Artista Específico (Semilla)"],
            horizontal=True
        )
        
        top_n = None
        trend_df = None
        base_type = "Artistas"
        seed_artist = ""
        cols_to_show = []
        n_selection = 15
        
        with st.container(border=True):
            col_in1, col_in2 = st.columns(2)
            
            with col_in1:
                if rec_mode == "Mi Perfil y Tendencias":
                    sub_c1, sub_c2 = st.columns(2)
                    with sub_c1:
                        base_type = st.selectbox("Basar recomendaciones en:", ["Artistas", "Canciones", "Álbumes"])
                    with sub_c2:
                        n_selection = st.selectbox("Cantidad a analizar (N):", [15, 30, 50, 100])
                    
                    if base_type == "Artistas":
                        grouped = df.groupby('artist_name').agg(
                            Play_Count=('ts', 'count')
                        ).reset_index()
                        top_n = grouped.sort_values(by='Play_Count', ascending=False).head(n_selection)
                        cols_to_show = ['artist_name', 'Play_Count']
                    elif base_type == "Canciones":
                        grouped = df.groupby(['track_name', 'artist_name']).agg(
                            Play_Count=('ts', 'count')
                        ).reset_index()
                        top_n = grouped.sort_values(by='Play_Count', ascending=False).head(n_selection)
                        cols_to_show = ['track_name', 'artist_name', 'Play_Count']
                    else: # Álbumes
                        grouped = df.groupby(['album_name', 'artist_name']).agg(
                            Play_Count=('ts', 'count')
                        ).reset_index()
                        top_n = grouped.sort_values(by='Play_Count', ascending=False).head(n_selection)
                        cols_to_show = ['album_name', 'artist_name', 'Play_Count']
                        
                    top_n.index = range(1, len(top_n) + 1)
                else:
                    seed_artist = st.text_input(
                        "Artista Semilla / Referencia:",
                        placeholder="Ej: Tom Misch, Khruangbin, Men I Trust, BADBADNOTGOOD, Jordan Rakei...",
                        help="Ingresa el nombre de cualquier artista para descubrir 10 canciones afines a su sonido que nunca hayas escuchado."
                    )
            
            with col_in2:
                motor_options = {
                    "Curador Universal": "Universal",
                    "Red Last.fm": "Last.fm",
                    "Nicho Crítico (RYM)": "RYM",
                    "Créditos y Estudio (Discogs)": "Discogs"
                }
                motor_help = (
                    "Curador Universal: Análisis sonoro integral (groove, producción, texturas, dinámicas vocales e influencias) sintetizando lo mejor de todos los enfoques.\n\n"
                    "Red Last.fm: Conexiones de audiencia y escena musical mediante su API oficial.\n\n"
                    "Nicho Crítico (RYM): Enfoque en joyas de nicho y aclamación crítica de microgéneros underground.\n\n"
                    "Créditos y Estudio (Discogs): Recomendaciones por producción de estudio, ingenieros de sonido, músicos de sesión o sellos discográficos."
                )
                motor_selection = st.selectbox(
                    "Elige el Enfoque (Motor de Recomendación)",
                    list(motor_options.keys()),
                    help=motor_help
                )
                motor = motor_options[motor_selection]
            
            if rec_mode == "Mi Perfil y Tendencias" and top_n is not None:
                with st.expander(f"Ver tu Top {n_selection} de {base_type} ({selected_period})"):
                    render_glass_table(top_n[cols_to_show])
                    
            btn_generate = st.button("Generar recomendaciones", use_container_width=True)
            
        if btn_generate:
            if rec_mode == "Por Artista Específico (Semilla)" and not seed_artist.strip():
                st.warning("Por favor ingresa el nombre de un artista semilla antes de generar recomendaciones.")
            elif get_recommendations:
                if rec_mode == "Por Artista Específico (Semilla)":
                    if motor == "Last.fm":
                        spinner_text = f"Consultando la API de Last.fm para '{seed_artist.strip()}' y cruzando con tu historial..."
                    else:
                        spinner_text = f"Gemini está explorando el universo sonoro de '{seed_artist.strip()}' y verificando exclusividad..."
                else:
                    if motor == "Last.fm":
                        spinner_text = "Consultando en vivo la API de Last.fm y conectando con Gemini..."
                    else:
                        spinner_text = "Gemini está analizando tu perfil y cruzando con tu historial..."
                    
                with st.spinner(spinner_text):
                    try:
                        # Cargar artistas de toda la historia y artistas ya recomendados recientemente
                        if engine:
                            try:
                                with engine.connect() as conn:
                                    all_hist_res = conn.execute(text("SELECT DISTINCT artist_name FROM spotify_history")).fetchall()
                                    lista_historica = [r[0] for r in all_hist_res if r[0]]
                                    saved_res = conn.execute(text("SELECT DISTINCT artist_name FROM saved_recommendations")).fetchall()
                                    lista_guardados = [r[0] for r in saved_res if r[0]]
                            except Exception:
                                lista_historica = df['artist_name'].dropna().unique().tolist()
                                lista_guardados = []
                        else:
                            lista_historica = df['artist_name'].dropna().unique().tolist()
                            lista_guardados = []
                            
                        lista_2026 = df[df['year'] == 2026]['artist_name'].dropna().unique().tolist()
                        
                        # Calcular el Vector de Tendencia (Últimos 30 días) solo en modo Perfil
                        if rec_mode == "Mi Perfil y Tendencias":
                            max_date = df['ts'].max()
                            cutoff_30_days = max_date - pd.DateOffset(days=30)
                            recent_data = df[df['ts'] >= cutoff_30_days]
                            
                            if base_type == "Artistas":
                                grouped_recent = recent_data.groupby('artist_name').agg(
                                    play_count=('ts', 'count'),
                                    days_active=('ts', lambda s: s.dt.date.nunique()),
                                    hours_played=('hours_played', 'sum')
                                ).reset_index()
                                filtered_recent = grouped_recent[grouped_recent['play_count'] > 1].copy()
                                if filtered_recent.empty:
                                    filtered_recent = grouped_recent.copy()
                                filtered_recent['obsession_score'] = (
                                    filtered_recent['play_count'] * (1.0 + 0.5 * (filtered_recent['days_active'] - 1)) +
                                    (filtered_recent['hours_played'] * 2.0)
                                ).round(1)
                                trend_df = filtered_recent.sort_values(by='obsession_score', ascending=False).head(n_selection)
                            elif base_type == "Canciones":
                                grouped_recent = recent_data.groupby(['track_name', 'artist_name']).agg(
                                    play_count=('ts', 'count'),
                                    days_active=('ts', lambda s: s.dt.date.nunique()),
                                    hours_played=('hours_played', 'sum')
                                ).reset_index()
                                filtered_recent = grouped_recent[grouped_recent['play_count'] > 1].copy()
                                if filtered_recent.empty:
                                    filtered_recent = grouped_recent.copy()
                                filtered_recent['obsession_score'] = (
                                    filtered_recent['play_count'] * (1.0 + 0.5 * (filtered_recent['days_active'] - 1)) +
                                    (filtered_recent['hours_played'] * 2.0)
                                ).round(1)
                                trend_df = filtered_recent.sort_values(by='obsession_score', ascending=False).head(n_selection)
                            else: # Álbumes
                                grouped_recent = recent_data.groupby(['album_name', 'artist_name']).agg(
                                    play_count=('ts', 'count'),
                                    days_active=('ts', lambda s: s.dt.date.nunique()),
                                    hours_played=('hours_played', 'sum')
                                ).reset_index()
                                filtered_recent = grouped_recent[grouped_recent['play_count'] > 1].copy()
                                if filtered_recent.empty:
                                    filtered_recent = grouped_recent.copy()
                                filtered_recent['obsession_score'] = (
                                    filtered_recent['play_count'] * (1.0 + 0.5 * (filtered_recent['days_active'] - 1)) +
                                    (filtered_recent['hours_played'] * 2.0)
                                ).round(1)
                                trend_df = filtered_recent.sort_values(by='obsession_score', ascending=False).head(n_selection)
                        else:
                            trend_df = None
                        
                        # Obtener feedback histórico real cruzando saved_recommendations con spotify_history
                        confirmed_hits = get_feedback_signals()
                        
                        # Pedir candidatos a Gemini filtrando historial, recomendaciones previas y con feedback real
                        import inspect
                        sig = inspect.signature(get_recommendations)
                        rec_kwargs = {
                            'top_df': top_n,
                            'engine': motor,
                            'base_type': base_type,
                            'num_recommendations': 30,
                            'artistas_historicos': lista_historica,
                            'artistas_2026': lista_2026,
                            'trend_df': trend_df,
                            'seed_artist': seed_artist.strip() if rec_mode == "Por Artista Específico (Semilla)" else None
                        }
                        has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                        if 'artistas_guardados' in sig.parameters or has_kwargs:
                            rec_kwargs['artistas_guardados'] = lista_guardados
                        if 'confirmed_hits' in sig.parameters or has_kwargs:
                            rec_kwargs['confirmed_hits'] = confirmed_hits
                            
                        candidates = get_recommendations(**rec_kwargs)
                        
                        recs = []
                        token_info = st.session_state.get('spotify_token')
                        clean_seed = seed_artist.strip() if rec_mode == "Por Artista Específico (Semilla)" else None
                        blocked_pool_set = set(lista_historica or []) | set(lista_guardados or [])

                        if candidates and filter_unsaved_tracks:
                            try:
                                recs = filter_unsaved_tracks(
                                    token_info, 
                                    candidates, 
                                    target_count=10,
                                    blocked_pool=blocked_pool_set,
                                    seed_artist=clean_seed,
                                    existing_artists=[]
                                )
                            except Exception as ex_filter:
                                print(f"Error filtering tracks: {ex_filter}")
                                recs = candidates[:10]
                        elif candidates:
                            recs = candidates[:10]
                            
                        # BUCLE DE AUTO-RECUPERACIÓN: Garantía Innegociable de 10 Canciones
                        if len(recs) < 10 and get_recommendations:
                            seen_artists_so_far = set(
                                [r.get('artist', '') for r in recs if r.get('artist')] + 
                                [c.get('artist', '') for c in (candidates or []) if c.get('artist')]
                            )
                            needed = 10 - len(recs)
                            try:
                                recovery_kwargs = dict(rec_kwargs)
                                recovery_kwargs['exclude_artists'] = seen_artists_so_far
                                recovery_kwargs['num_recommendations'] = 30
                                recovery_candidates = get_recommendations(**recovery_kwargs)
                                if recovery_candidates and filter_unsaved_tracks:
                                    existing_artists_list = [r.get('artist') for r in recs if r.get('artist')]
                                    more_recs = filter_unsaved_tracks(
                                        token_info, 
                                        recovery_candidates, 
                                        target_count=needed,
                                        blocked_pool=blocked_pool_set,
                                        seed_artist=clean_seed,
                                        existing_artists=existing_artists_list
                                    )
                                    for mr in more_recs:
                                        if len(recs) < 10:
                                            mr_art = mr.get('artist', '').strip().lower()
                                            if not any(r.get('artist', '').strip().lower() == mr_art for r in recs):
                                                recs.append(mr)
                            except Exception as ex_recovery:
                                print(f"Aviso en pasada de auto-recuperación: {ex_recovery}")
                                
                        if recs:
                            st.session_state['current_recs'] = recs
                            st.session_state['current_motor'] = motor
                            st.session_state['current_seed_artist'] = seed_artist.strip() if rec_mode == "Por Artista Específico (Semilla)" else None
                            
                            # Guardar en Supabase para el Diario de Descubrimientos
                            if engine and recs:
                                try:
                                    with engine.begin() as conn:
                                        for rec in recs:
                                            conn.execute(text("""
                                                INSERT INTO saved_recommendations 
                                                (motor, artist_name, track_name, reason, badge, spotify_url, cover_url)
                                                VALUES (:motor, :artist_name, :track_name, :reason, :badge, :spotify_url, :cover_url)
                                            """), {
                                                "motor": motor,
                                                "artist_name": rec.get('artist', 'Desconocido'),
                                                "track_name": rec.get('item', 'Desconocido'),
                                                "reason": rec.get('reason', ''),
                                                "badge": rec.get('badge', ''),
                                                "spotify_url": rec.get('spotify_url', ''),
                                                "cover_url": rec.get('cover_url', '')
                                            })
                                except Exception as e:
                                    print(f"Error saving to history: {e}")
                                    
                            st.success(f"¡{len(recs)} recomendaciones 100% verificadas en Spotify, libres de repetición y guardadas!")
                        else:
                            st.warning("No se encontraron candidatos nuevos que no estuvieran ya en tu historial. Prueba seleccionando otro motor o período.")
                    except Exception as e:
                        st.error(f"Error generando recomendaciones: {e}")
            else:
                st.error("Módulo de recomendaciones no disponible. Faltan librerías.")
                
        if 'current_recs' in st.session_state and st.session_state['current_recs']:
            motor = st.session_state.get('current_motor', '')
            cur_seed = st.session_state.get('current_seed_artist')
            recs = st.session_state.get('current_recs', [])
            
            st.markdown("<div style='margin-top: 24px;'></div>", unsafe_allow_html=True)
            col_hdr_left, col_hdr_right = st.columns([3, 2], vertical_alignment="center")
            
            with col_hdr_left:
                if cur_seed:
                    st.markdown(f"### Selección Curada ({len(recs)})\n<div style='color: var(--text-sec); font-size: 0.88rem; margin-top: -6px;'>Para: <b>{cur_seed}</b> · Motor: <b>{motor}</b></div>", unsafe_allow_html=True)
                else:
                    st.markdown(f"### Selección Curada ({len(recs)})\n<div style='color: var(--text-sec); font-size: 0.88rem; margin-top: -6px;'>Motor: <b>{motor}</b></div>", unsafe_allow_html=True)
                    
            with col_hdr_right:
                if create_spotify_playlist:
                    if 'spotify_token' not in st.session_state:
                        auth_url = get_auth_url() if callable(get_auth_url) else "#"
                        st.link_button("Conectar Spotify para Exportar", auth_url, use_container_width=True)
                    else:
                        if st.button("Exportar Recomendaciones a Spotify", key="quick_export_spotify", use_container_width=True):
                            with st.spinner("Creando playlist..."):
                                try:
                                    m = st.session_state.get('current_motor', '')
                                    cur_seed = st.session_state.get('current_seed_artist')
                                    if cur_seed:
                                        p_name = f"Descubrimientos: {cur_seed.strip()}"
                                        p_desc = f"Descubrimientos musicales afines a {cur_seed.strip()} generados por IA."
                                    else:
                                        p_name = f"Recomendaciones {m}"
                                        p_desc = f"Descubrimientos generados por IA usando el motor de {m}."
                                    url = create_spotify_playlist(p_name, p_desc, recs, st.session_state['spotify_token'], motor=m)
                                    if url:
                                        st.success(f"¡Playlist actualizada con éxito en Spotify! [Abrir '{p_name}']({url})")
                                except Exception as e:
                                    st.error(f"Error exportando a Spotify: {e}")
                                    
            st.markdown("<div style='margin-bottom: 16px;'></div>", unsafe_allow_html=True)
                
            for idx, rec in enumerate(recs):
                artist = rec.get('artist', 'Desconocido')
                track = rec.get('item', 'Desconocido')
                reason = rec.get('reason', '')
                badge = rec.get('badge', '')
                cover_url = rec.get('cover_url', '')
                spotify_url = rec.get('spotify_url', '')
                
                badge_html = ""
                if badge:
                    clean_badge = re.sub(r'[^\w\s-]', '', str(badge)).strip().upper()
                    if clean_badge:
                        badge_color = "#38bdf8"
                        if "RECONEXIÓN" in clean_badge: badge_color = "#c084fc"
                        elif "LADO B" in clean_badge: badge_color = "#f59e0b"
                        elif "PUNTO CIEGO" in clean_badge: badge_color = "#10b981"
                        elif "CURADURÍA" in clean_badge or "UNIVERSAL" in clean_badge: badge_color = "#ec4899"
                        
                        badge_html = f'<div style="display:inline-block; background-color:{badge_color}12; color:{badge_color}; border: 1px solid {badge_color}40; padding: 2px 8px; border-radius: 4px; font-size: 0.65rem; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 8px;">{clean_badge}</div>'
                
                cover_img = f'<img src="{cover_url}" style="width: 56px; height: 56px; border-radius: 8px; margin-right: 14px; float: left; object-fit: cover; box-shadow: 0 4px 12px rgba(0,0,0,0.5);">' if cover_url else ''
                title_display = f'<a href="{spotify_url}" target="_blank" style="color:white; text-decoration:none;">{track} ↗</a>' if spotify_url else track
                
                pill_info = f"/ MOTOR: {motor} · SEMILLA: {cur_seed} /" if cur_seed else f"/ MOTOR: {motor} /"
                
                # HTML template para la tarjeta de recomendación
                card_html = f"""
                <div class="rec-card" style="overflow: hidden;">
                    <div class="rec-number">[{str(idx+1).zfill(2)}]</div>
                    {badge_html}
                    <div style="display: flex; align-items: center; margin-top: 6px;">
                        {cover_img}
                        <div>
                            <div class="rec-title">{title_display}</div>
                            <div class="rec-artist">por {artist}</div>
                        </div>
                    </div>
                    <div style="clear: both; margin-top: 10px;"></div>
                    <div class="rec-pills">{pill_info}</div>
                    <div class="rec-reason">"{reason}"</div>
                </div>
                """
                st.markdown(card_html, unsafe_allow_html=True)

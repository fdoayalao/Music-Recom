import streamlit as st
import sqlite3
import pandas as pd
import os
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
    from spotify_exporter import create_spotify_playlist, get_auth_url, get_token, get_currently_playing, sync_recently_played_to_db, get_cached_token
except ImportError:
    create_spotify_playlist = None
    get_auth_url = None
    get_token = None
    get_currently_playing = None
    sync_recently_played_to_db = None
    get_cached_token = None
    
import zipfile

# Set up page configuration (needs to be the first Streamlit command)
st.set_page_config(page_title="My Spotify Stats", page_icon="🎵", layout="wide")

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

    /* Diseño de Tarjetas de Recomendación (Glassmorphism) */
    .rec-card {
        background: var(--bg-card);
        backdrop-filter: blur(16px);
        -webkit-backdrop-filter: blur(16px);
        border: 1px solid var(--border-color);
        padding: 24px;
        margin-bottom: 24px;
        border-radius: 16px;
        position: relative;
        box-shadow: 0 4px 30px rgba(0, 0, 0, 0.1);
        transition: transform 0.3s ease, border-color 0.3s ease;
    }
    .rec-card:hover {
        transform: translateY(-4px);
        border-color: rgba(56, 189, 248, 0.3);
    }
    .rec-number {
        position: absolute;
        top: 24px;
        right: 24px;
        font-family: 'Inter', sans-serif;
        font-weight: 800;
        color: rgba(255, 255, 255, 0.1);
        font-size: 2rem;
        line-height: 1;
    }
    .rec-title {
        font-family: 'Inter', sans-serif;
        font-size: 1.5rem;
        font-weight: 700;
        margin-bottom: 4px;
        color: var(--text-main);
        letter-spacing: -0.5px;
    }
    .rec-artist {
        font-family: 'Inter', sans-serif;
        font-size: 1.1rem;
        font-weight: 500;
        color: var(--text-sec);
        margin-bottom: 16px;
    }
    .rec-pills {
        font-family: 'Inter', sans-serif;
        font-size: 0.7rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 1.5px;
        color: var(--accent);
        margin-bottom: 16px;
        display: inline-block;
        background: rgba(56, 189, 248, 0.1);
        padding: 4px 10px;
        border-radius: 100px;
    }
    .rec-reason {
        background: rgba(255, 255, 255, 0.02);
        border-left: 2px solid var(--accent-alt);
        padding: 16px;
        border-radius: 0 8px 8px 0;
        font-family: 'Inter', sans-serif;
        color: #e4e4e7;
        font-size: 0.95rem;
        line-height: 1.6;
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

@st.cache_data
def get_years():
    if not os.path.exists(DB_PATH):
        return []
    conn = sqlite3.connect(DB_PATH)
    try:
        years_df = pd.read_sql_query("SELECT DISTINCT year FROM spotify_history ORDER BY year DESC", conn)
        years = years_df['year'].tolist()
    except Exception:
        years = []
    conn.close()
    return years

@st.cache_data
def load_data(year_filter="All Time"):
    conn = sqlite3.connect(DB_PATH)
    if year_filter == "All Time":
        df = pd.read_sql_query("SELECT * FROM spotify_history", conn)
    else:
        df = pd.read_sql_query("SELECT * FROM spotify_history WHERE year = ?", conn, params=(int(year_filter),))
    conn.close()
    if not df.empty:
        df['ts'] = pd.to_datetime(df['ts'])
    return df

# Main Header
st.title("Music Recommender")

# Check if DB exists
if not os.path.exists(DB_PATH):
    st.error("Database not found! Please run `python process_data.py` first to generate `spotify_data.db`.")
    st.stop()

available_years = get_years()

# Sidebar Navigation
st.sidebar.image("https://storage.googleapis.com/pr-newsroom-wp/1/2018/11/Spotify_Logo_RGB_Green.png", width=150)

if 'spotify_token' in st.session_state:
    if st.sidebar.button("Live Sync"):
        if sync_recently_played_to_db:
            with st.spinner("Sincronizando con Spotify..."):
                added = sync_recently_played_to_db(st.session_state['spotify_token'], DB_PATH)
                st.cache_data.clear()
                st.sidebar.success(f"¡Sincronización completa! {added} nuevas reproducciones agregadas.")
                st.rerun()
else:
    if get_auth_url:
        st.sidebar.markdown(f'<a href="{get_auth_url()}" target="_blank" style="display:block; background-color:#1DB954; color:white; padding:10px 15px; border-radius:100px; text-decoration:none; font-weight:bold; text-align:center; font-size:0.9rem;">🔌 Vincular Spotify</a>', unsafe_allow_html=True)

st.sidebar.markdown("---")

# Global Filters
st.sidebar.header("Filtros Globales")
period_options = ["All Time"] + [str(y) for y in available_years]
selected_period = st.sidebar.selectbox("Selecciona un período", period_options)
st.sidebar.markdown("---")

page = st.sidebar.radio("Navegación", ["Recomendador y Playlists", "Estadísticas"])
st.sidebar.markdown("---")

if page == "Estadísticas":
    if 'spotify_token' in st.session_state and get_currently_playing:
        now_playing = get_currently_playing(st.session_state['spotify_token'])
        if now_playing:
            st.markdown(f"""
            <div style="display: flex; align-items: center; background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 12px; padding: 12px; margin-bottom: 24px; animation: pulse 2s infinite;">
                <img src="{now_playing['cover_url']}" style="width: 50px; height: 50px; border-radius: 50%; border: 2px solid #38bdf8; margin-right: 16px; animation: spin 4s linear infinite;">
                <div>
                    <div style="font-size: 0.75rem; color: #38bdf8; font-weight: bold; text-transform: uppercase; letter-spacing: 1px;">Escuchando Ahora</div>
                    <div style="font-weight: bold; font-size: 1.1rem; color: white;">{now_playing['track_name']}</div>
                    <div style="font-size: 0.9rem; color: #a1a1aa;">{now_playing['artist_name']}</div>
                </div>
            </div>
            <style>
                @keyframes spin {{ 100% {{ transform: rotate(360deg); }} }}
                @keyframes pulse {{ 0% {{ box-shadow: 0 0 0 0 rgba(56, 189, 248, 0.4); }} 70% {{ box-shadow: 0 0 0 10px rgba(56, 189, 248, 0); }} 100% {{ box-shadow: 0 0 0 0 rgba(56, 189, 248, 0); }} }}
            </style>
            """, unsafe_allow_html=True)

    st.sidebar.markdown("### About")
    st.sidebar.info("Dashboard of your extended Spotify streaming history (>30 seconds).")

    with st.spinner("Loading data..."):
        df = load_data(selected_period)

    if df.empty:
        st.warning("No data found for the selected period.")
        st.stop()

    # --- Overview Metrics ---
    st.markdown(f"### 📊 Overview: {selected_period}")
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
    st.markdown("### 🏆 Top 50 Rankings")

    # Exclude "Mocasines" from the rankings (ghost artist)
    ranking_df = df[df['artist_name'].str.lower() != 'mocasines']

    tab1, tab2, tab3 = st.tabs(["🎤 Top 50 Artists", "🎧 Top 50 Songs", "💿 Top 50 Albums"])

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
        render_glass_table(top_albums)

elif page == "Recomendador y Playlists":
    # Credenciales Warning
    if not os.getenv("GEMINI_API_KEY") or not os.getenv("SPOTIPY_CLIENT_ID"):
        with st.expander("⚠️ Configuración de Credenciales Requerida (Haz click aquí)", expanded=True):
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
        
    tabA, tabB = st.tabs(["💎 Gemas Olvidadas", "🤖 Recomendador Orgánico"])
    
    with tabA:
        st.subheader("Tus Gemas Olvidadas")
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
            
            if st.button("Crear Playlist de Gemas Olvidadas en Spotify"):
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
        st.subheader("Recomendaciones Orgánicas Multi-Motor")
        
        col1, col2 = st.columns(2)
        with col1:
            base_type = st.selectbox("Basar recomendaciones en:", ["Artistas", "Canciones", "Álbumes"])
        with col2:
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
            
        # Hacer que el índice sea un ranking (1, 2, 3...)
        top_n.index = range(1, len(top_n) + 1)
            
        with st.expander(f"Ver tu Top {n_selection} de {base_type} ({selected_period})"):
            render_glass_table(top_n[cols_to_show])
            
        motor_options = {
            "RYM (Joyas de nicho y aclamación crítica)": "RYM",
            "Last.fm (Conexiones de audiencia y escena)": "Last.fm",
            "Discogs (Créditos, producción y sonido de estudio)": "Discogs"
        }
        motor_selection = st.selectbox("Elige el Enfoque (Motor de Recomendación)", list(motor_options.keys()))
        motor = motor_options[motor_selection]
        
        st.info({
            "RYM": "🧠 **RYM:** Enfoque en Joyas de nicho y aclamación crítica de microgéneros.",
            "Last.fm": "🌐 **Last.fm:** Conexiones de audiencia y 'similar artists' de la escena.",
            "Discogs": "🎛️ **Discogs:** Recomendaciones por producción, sello o sonido de estudio."
        }[motor])
        
        if st.button("Generar Recomendaciones con Gemini"):
            if get_recommendations:
                with st.spinner("Gemini está analizando tu perfil..."):
                    recs = get_recommendations(top_n, motor, base_type)
                    if recs:
                        st.session_state['current_recs'] = recs
                        st.session_state['current_motor'] = motor
                        st.success("¡Recomendaciones generadas!")
                    else:
                        st.error("No se pudieron generar recomendaciones. Revisa tu API key en .env y que tengas conexión.")
            else:
                st.error("Módulo de recomendaciones no disponible. Faltan librerías.")
                
        if 'current_recs' in st.session_state:
            st.markdown(f"### Resultados (Motor: {st.session_state.get('current_motor', '')})")
            motor = st.session_state.get('current_motor', '')
            for idx, rec in enumerate(st.session_state['current_recs']):
                artist = rec.get('artist', 'Desconocido')
                track = rec.get('item', 'Desconocido')
                reason = rec.get('reason', '')
                
                # HTML template para la tarjeta de recomendación
                card_html = f"""
                <div class="rec-card">
                    <div class="rec-number">[{str(idx+1).zfill(2)}]</div>
                    <div class="rec-title">{track}</div>
                    <div class="rec-artist">por {artist}</div>
                    <div class="rec-pills">/ MOTOR: {motor} /</div>
                    <div class="rec-reason">"{reason}"</div>
                </div>
                """
                st.markdown(card_html, unsafe_allow_html=True)
                
            if create_spotify_playlist:
                if 'spotify_token' not in st.session_state:
                    auth_url = get_auth_url()
                    st.warning("Debes conectar tu cuenta de Spotify para poder crear y exportar playlists.")
                    st.link_button("Conectar con Spotify", auth_url)
                else:
                    if st.button("Exportar Recomendaciones a Spotify"):
                        with st.spinner("Creando playlist..."):
                            try:
                                m = st.session_state.get('current_motor', '')
                                meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
                                mes_actual = meses[datetime.now().month - 1]
                                ano_actual = datetime.now().year
                                
                                p_name = f"Recomendaciones {m} - {mes_actual} {ano_actual}"
                                p_desc = f"Descubrimientos mensuales generados por IA usando el motor de {m}."
                                url = create_spotify_playlist(p_name, p_desc, st.session_state['current_recs'], st.session_state['spotify_token'])
                                if url:
                                    st.success(f"¡Playlist creada con éxito! [Abrir en Spotify]({url})")
                            except Exception as e:
                                st.error(f"Error exportando a Spotify: {e}")

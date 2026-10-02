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
    from spotify_exporter import create_spotify_playlist, get_auth_url, get_token
except ImportError:
    create_spotify_playlist = None
    
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

# Custom CSS for aesthetics
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;1,400&family=Space+Grotesk:wght@400;600;700&display=swap');

    /* Variables de color según lineamientos */
    :root {
        --bg-main: #171615;
        --bg-card: #21201D;
        --border-color: #33302B;
        --text-main: #F2EFE9;
        --text-sec: #A8A398;
        --accent: #E0A946;
        --accent-alt: #C46242;
    }

    /* Fondo principal y tipografía general */
    .stApp {
        background-color: var(--bg-main) !important;
        color: var(--text-main) !important;
        font-family: 'Space Grotesk', sans-serif !important;
    }

    /* Encabezados editoriales */
    h1, h2, h3, .st-emotion-cache-10trblm {
        font-family: 'Lora', serif !important;
        color: var(--text-main) !important;
        font-weight: 600 !important;
    }

    /* Tarjetas de Métricas */
    .metric-card {
        background-color: var(--bg-card);
        border-radius: 4px;
        padding: 20px;
        text-align: center;
        border: 1px solid var(--border-color);
        box-shadow: 4px 4px 0px rgba(0,0,0,0.3);
    }
    .metric-value {
        font-family: 'Space Grotesk', sans-serif;
        font-size: 2.2rem;
        font-weight: 700;
        color: var(--text-main);
    }
    .metric-label {
        font-family: 'Space Grotesk', sans-serif;
        color: var(--text-sec);
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 2px;
        margin-top: 8px;
    }

    /* Diseño de Tarjetas de Recomendación (Liner Notes Style) */
    .rec-card {
        background-color: var(--bg-card);
        border: 1px solid var(--border-color);
        padding: 24px;
        margin-bottom: 20px;
        border-radius: 2px;
        position: relative;
    }
    .rec-number {
        position: absolute;
        top: 24px;
        right: 24px;
        font-family: 'Space Grotesk', sans-serif;
        font-weight: 700;
        color: var(--accent);
        font-size: 1.1rem;
    }
    .rec-title {
        font-family: 'Lora', serif;
        font-size: 1.6rem;
        font-weight: 600;
        margin-bottom: 4px;
        color: var(--text-main);
    }
    .rec-artist {
        font-family: 'Space Grotesk', sans-serif;
        font-size: 1.1rem;
        color: var(--text-sec);
        margin-bottom: 16px;
    }
    .rec-pills {
        font-family: 'Space Grotesk', sans-serif;
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 1.5px;
        color: var(--accent);
        margin-bottom: 16px;
    }
    .rec-reason {
        background-color: #1C1B19;
        border-left: 3px solid var(--accent-alt);
        padding: 16px;
        font-family: 'Lora', serif;
        font-style: italic;
        color: var(--text-main);
        font-size: 1rem;
        line-height: 1.6;
    }

    /* Botones generales (incluye Spotify) */
    .stButton > button {
        background-color: var(--accent) !important;
        color: #171615 !important;
        font-family: 'Space Grotesk', sans-serif !important;
        font-weight: 600 !important;
        border-radius: 2px !important;
        border: none !important;
        padding: 10px 24px !important;
        text-transform: uppercase !important;
        letter-spacing: 1px !important;
        transition: all 0.2s ease !important;
    }
    .stButton > button:hover {
        background-color: var(--text-main) !important;
        color: #171615 !important;
    }
</style>
""", unsafe_allow_html=True)

# DB path
DB_PATH = "spotify_data.db"

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
st.title("🎵 Spotify Analyzer & Recommender")

# Check if DB exists
if not os.path.exists(DB_PATH):
    st.error("Database not found! Please run `python process_data.py` first to generate `spotify_data.db`.")
    st.stop()

available_years = get_years()

# Sidebar Navigation
st.sidebar.image("https://storage.googleapis.com/pr-newsroom-wp/1/2018/11/Spotify_Logo_RGB_Green.png", width=150)

# Global Filters
st.sidebar.header("Filtros Globales")
period_options = ["All Time"] + [str(y) for y in available_years]
selected_period = st.sidebar.selectbox("Selecciona un período", period_options)
st.sidebar.markdown("---")

page = st.sidebar.radio("Navegación", ["Recomendador y Playlists", "Estadísticas"])
st.sidebar.markdown("---")

if page == "Estadísticas":
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
        st.dataframe(top_artists, use_container_width=True)

    with tab2:
        top_songs = ranking_df.groupby(['track_name', 'artist_name']).agg(
            Play_Count=('ts', 'count'),
            Hours_Listened=('hours_played', 'sum')
        ).reset_index().sort_values(by='Play_Count', ascending=False).head(50)
        top_songs['Hours_Listened'] = top_songs['Hours_Listened'].round(1)
        top_songs.rename(columns={'track_name': 'Song', 'artist_name': 'Artist', 'Play_Count': 'Streams', 'Hours_Listened': 'Hours Listened'}, inplace=True)
        top_songs.index = range(1, 51)
        st.dataframe(top_songs, use_container_width=True)

    with tab3:
        top_albums = ranking_df.groupby(['album_name', 'artist_name']).agg(
            Play_Count=('ts', 'count'),
            Hours_Listened=('hours_played', 'sum')
        ).reset_index().sort_values(by='Play_Count', ascending=False).head(50)
        top_albums['Hours_Listened'] = top_albums['Hours_Listened'].round(1)
        top_albums.rename(columns={'album_name': 'Album', 'artist_name': 'Artist', 'Play_Count': 'Streams', 'Hours_Listened': 'Hours Listened'}, inplace=True)
        top_albums.index = range(1, 51)
        st.dataframe(top_albums, use_container_width=True)

elif page == "Recomendador y Playlists":
    st.header("✨ Recomendador Inteligente y Gemas Olvidadas")
    
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
            st.dataframe(gemas[['artist_name', 'track_name', 'Total_Plays', 'Last_Played']], use_container_width=True)
            
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
            # Calculate Top 15 Deep Listening Artists
            grouped = df.groupby('artist_name').agg(
                Minutes_Listened=('minutes_played', 'sum'),
                Unique_Tracks=('track_name', 'nunique')
            ).reset_index()
            grouped['Deep_Score'] = grouped['Minutes_Listened'] * grouped['Unique_Tracks']
            top_n = grouped.sort_values(by='Deep_Score', ascending=False).head(n_selection)
            cols_to_show = ['artist_name', 'Minutes_Listened', 'Unique_Tracks']
        elif base_type == "Canciones":
            grouped = df.groupby(['track_name', 'artist_name']).agg(
                Minutes_Listened=('minutes_played', 'sum')
            ).reset_index()
            top_n = grouped.sort_values(by='Minutes_Listened', ascending=False).head(n_selection)
            cols_to_show = ['track_name', 'artist_name', 'Minutes_Listened']
        else: # Álbumes
            grouped = df.groupby(['album_name', 'artist_name']).agg(
                Minutes_Listened=('minutes_played', 'sum')
            ).reset_index()
            top_n = grouped.sort_values(by='Minutes_Listened', ascending=False).head(n_selection)
            cols_to_show = ['album_name', 'artist_name', 'Minutes_Listened']
            
        # Hacer que el índice sea un ranking (1, 2, 3...)
        top_n.index = range(1, len(top_n) + 1)
            
        with st.expander(f"Ver tu Top {n_selection} de {base_type} ({selected_period})"):
            st.dataframe(top_n[cols_to_show])
            
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

import os
import pandas as pd
import streamlit as st
from datetime import datetime
import folium
from streamlit_folium import st_folium

from geocoding.processor import GeocodingProcessor
from geocoding.config import LOGS_DIR, RESULTADOS_DIR

# ---------------------------------------------------------------------------
# Configuración de página
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Validación de Direcciones",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

TEMP_DIR = os.path.join("data", "input")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    st.title("Validación de Direcciones — Google Geocoding")
    st.markdown("---")

    # Inicializar session_state aquí — independiente de la sección activa
    if "resumen"  not in st.session_state: st.session_state.resumen  = None
    if "log_text" not in st.session_state: st.session_state.log_text = ""
    if "res_file" not in st.session_state: st.session_state.res_file = None
    if "visor_res_file" not in st.session_state: st.session_state.visor_res_file = None

    st.sidebar.title("Navegación")
    seccion = st.sidebar.radio("", ["Ejecución", "Historial", "Visor"])

    if seccion == "Ejecución":
        render_ejecucion()
    elif seccion == "Historial":
        render_historial()
    else:
        render_visor()


# ---------------------------------------------------------------------------
# Sección: Ejecución
# ---------------------------------------------------------------------------

def render_ejecucion():
    st.header("Procesamiento de Direcciones")

    uploaded_file = st.file_uploader(
        "Cargar archivo CSV",
        type=["csv"],
    )

    if uploaded_file is not None:
        temp_path = _guardar_archivo_temporal(uploaded_file)
        st.success(f"Archivo cargado: **{uploaded_file.name}**")

        start_button = st.button("Procesar Archivo", type="primary")

        # Contenedores dinámicos — se actualizan durante el procesamiento
        progress_placeholder = st.empty()
        status_placeholder   = st.empty()

        with st.expander("Logs de ejecución", expanded=True):
            log_container = st.empty()

        # Mostrar logs de una ejecución anterior si los hay
        if st.session_state.log_text:
            log_container.text(st.session_state.log_text)

        if start_button:
            # Limpiar estado previo
            st.session_state.resumen  = None
            st.session_state.log_text = ""
            st.session_state.res_file = None

            progress_bar = progress_placeholder.progress(0)

            def update_progress(progreso, texto):
                progress_bar.progress(progreso)
                status_placeholder.text(texto)

            def append_log(mensaje):
                st.session_state.log_text += f"{mensaje}\n"
                log_container.text(st.session_state.log_text)

            processor = GeocodingProcessor(
                update_callback=update_progress,
                log_callback=append_log,
            )

            with st.spinner("Procesando..."):
                resumen = processor.process(temp_path)

            progress_placeholder.empty()
            status_placeholder.empty()

            if resumen:
                st.session_state.resumen  = resumen
                st.session_state.res_file = resumen.get("res_file")
            else:
                st.error(
                    "El procesamiento no pudo iniciarse. "
                    "Verificá que el archivo tenga las columnas requeridas: "
                    "ID_Cliente, Dirección, Localidad, Provincia."
                )

    else:
        st.info("Subí un archivo CSV para comenzar.")
        # Mostrar logs fuera del bloque de archivo (si persisten en sesión)
        if st.session_state.log_text:
            with st.expander("Logs de la última ejecución", expanded=False):
                st.text(st.session_state.log_text)

    # Resultados — se muestran siempre que haya un resumen en sesión,
    # incluso si el usuario navegó a otra sección y volvió.
    if st.session_state.resumen:
        _render_resultados(st.session_state.resumen)


def _guardar_archivo_temporal(uploaded_file):
    """Persiste el archivo subido en disco para que processor.py pueda leerlo."""
    os.makedirs(TEMP_DIR, exist_ok=True)
    path = os.path.join(TEMP_DIR, uploaded_file.name)
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return path


def _render_resultados(resumen):
    """
    Muestra métricas, tabla de resultados y botón de descarga.
    No contiene lógica de negocio — solo presenta datos ya resueltos.
    """
    st.markdown("---")
    st.success("Procesamiento finalizado.")

    # --- Métricas ---
    # El resumen de logging_utils solo trae exitosos/fallidos (SUCCESS vs no-SUCCESS).
    # Los conteos de FAILED y ERROR se calculan leyendo el CSV de resultados,
    # que es la única fuente de verdad de los tres estados.
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Recibidos",  resumen["recibidos"])
    col2.metric("Procesados", resumen["procesados"])

    # Leer CSV una sola vez — se reutiliza para métricas y tabla
    res_file = resumen.get("res_file")
    df_res   = pd.read_csv(res_file) if res_file and os.path.exists(res_file) else None

    if df_res is not None:
        n_success = int((df_res["validation_status"] == "SUCCESS").sum())
        n_failed  = int((df_res["validation_status"] == "FAILED").sum())
        n_error   = int((df_res["validation_status"] == "ERROR").sum())
    else:
        n_success = resumen["exitosos"]
        n_failed  = resumen["errores"]
        n_error   = 0

    col3.metric("✅ SUCCESS", n_success)
    col4.metric("⚠️ FAILED",  n_failed)
    col5.metric("❌ ERROR",   n_error)

    st.caption(f"Tiempo total: {resumen['tiempo']}s  |  Log: `{resumen['log_file']}`")

    # --- Tabla de resultados ---
    if df_res is not None:
        st.markdown("### Resultados")

        # Filtro por estado — único filtro relevante en el nuevo modelo
        estados_disponibles = sorted(df_res["validation_status"].dropna().unique().tolist())
        estados_seleccionados = st.multiselect(
            "Filtrar por estado",
            options=estados_disponibles,
            default=estados_disponibles,
            key="ejecucion_estado_filter",
        )

        df_filtrado = df_res[df_res["validation_status"].isin(estados_seleccionados)]
        st.dataframe(df_filtrado, use_container_width=True)

        # Descarga del CSV filtrado y acceso al Visor
        col_dl, col_visor = st.columns([3, 1])

        with col_dl:
            csv_bytes = df_filtrado.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="Descargar resultados (CSV)",
                data=csv_bytes,
                file_name=os.path.basename(res_file),
                mime="text/csv",
            )

        with col_visor:
            if st.button("📍 Ver en Visor", key="ejecucion_abrir_visor"):
                st.session_state.visor_res_file = res_file
                st.rerun()


# ---------------------------------------------------------------------------
# Sección: Historial
# ---------------------------------------------------------------------------

def render_historial():
    st.header("Historial de Ejecuciones")

    hist_file = os.path.join(LOGS_DIR, "logs_historicos.csv")

    if not os.path.exists(hist_file):
        st.info("Todavía no hay ejecuciones registradas.")
        return

    df_hist = pd.read_csv(hist_file)
    df_hist = df_hist.sort_values(by="fecha_ejecucion", ascending=False)

    st.dataframe(df_hist, use_container_width=True)

    # --- Detalle de una ejecución ---
    st.markdown("### Detalle de ejecución")

    opciones_log = df_hist["archivo_log"].dropna().tolist()
    selected_log = st.selectbox("Seleccionar ejecución", opciones_log, key="historial_log_selector")

    if selected_log:
        # Derivar el path del resultado a partir del path del log de debug.
        # Ambos usan el mismo timestamp: log_ejecucion_TS.csv → resultado_geocodificacion_TS.csv
        res_file = selected_log.replace(
            os.path.join(LOGS_DIR, "log_ejecucion_"),
            os.path.join(RESULTADOS_DIR, "resultado_geocodificacion_"),
        )

        archivo_a_mostrar = res_file if os.path.exists(res_file) else selected_log

        if os.path.exists(archivo_a_mostrar):
            df_log = pd.read_csv(archivo_a_mostrar)

            # Filtro por estado — alineado con los tres estados del nuevo modelo
            estados_disponibles = sorted(df_log["validation_status"].dropna().unique().tolist())
            estados_seleccionados = st.multiselect(
                "Filtrar por estado",
                options=estados_disponibles,
                default=estados_disponibles,
                key="historial_estado_filter",
            )

            df_filtrado = df_log[df_log["validation_status"].isin(estados_seleccionados)]
            st.dataframe(df_filtrado, use_container_width=True)

            col_dl, col_visor = st.columns([3, 1])

            with col_dl:
                csv_bytes = df_filtrado.to_csv(index=False).encode("utf-8")
                st.download_button(
                    label="Descargar resultados (CSV)",
                    data=csv_bytes,
                    file_name=f"resultado_filtrado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                )

            with col_visor:
                if st.button("📍 Abrir en Visor", key="historial_abrir_visor"):
                    st.session_state.visor_res_file = archivo_a_mostrar
                    st.rerun()

        else:
            st.warning(f"No se encontró el archivo de resultados en disco: `{archivo_a_mostrar}`")


# ---------------------------------------------------------------------------
# Sección: Visor
# ---------------------------------------------------------------------------

# Configuración de rubros: ícono Font Awesome y color del pin.
# Cualquier rubro no listado aquí cae en el fallback.
_RUBRO_CONFIG = {
    "Supermercado": {"icon": "shopping-cart", "color": "blue"},
    "Almacén":      {"icon": "home",          "color": "green"},
    "Gran Cadena":  {"icon": "building",      "color": "red"},
}
_RUBRO_FALLBACK = {"icon": "map-marker", "color": "gray"}


def render_visor():
    st.header("Visor de Direcciones")

    # Si se llegó desde el Historial, usar el path ya conocido.
    # El botón "Limpiar" permite volver al modo upload manual.
    res_file_from_hist = st.session_state.get("visor_res_file")

    if res_file_from_hist and os.path.exists(res_file_from_hist):
        st.info(f"Mostrando resultados de: `{os.path.basename(res_file_from_hist)}`")
        if st.button("✖ Cargar otro archivo", key="visor_limpiar"):
            st.session_state.visor_res_file = None
            st.rerun()
        try:
            df = pd.read_csv(res_file_from_hist)
        except Exception as e:
            st.error(f"No se pudo leer el archivo: {e}")
            return
    else:
        uploaded_file = st.file_uploader(
            "Cargar CSV de resultados",
            type=["csv"],
            key="visor_uploader",
        )
        if uploaded_file is None:
            st.info("Subí un CSV de resultados, o abrilo directo desde el Historial.")
            return
        try:
            df = pd.read_csv(uploaded_file)
        except Exception as e:
            st.error(f"No se pudo leer el archivo: {e}")
            return

    # Verificar columnas mínimas necesarias
    cols_necesarias = {"latitude", "longitude", "validation_status"}
    faltantes = cols_necesarias - set(df.columns)
    if faltantes:
        st.error(f"El CSV no tiene las columnas requeridas: {', '.join(sorted(faltantes))}")
        return

    # Descartar filas sin coordenadas válidas
    df_geo = df.dropna(subset=["latitude", "longitude"]).copy()
    df_sin_coords = df[df["latitude"].isna() | df["longitude"].isna()]

    if df_geo.empty:
        st.warning("Ninguna fila tiene coordenadas válidas para mostrar en el mapa.")
        return

    # ------------------------------------------------------------------
    # Sidebar — filtros
    # ------------------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.markdown("### Filtros del visor")

    # Filtro por provincia
    if "provincia" in df_geo.columns:
        provincias_disponibles = sorted(df_geo["provincia"].dropna().unique().tolist())
        provincias_sel = st.sidebar.multiselect(
            "Provincia",
            options=provincias_disponibles,
            default=provincias_disponibles,
            key="visor_provincia_filter",
        )
    else:
        provincias_sel = None

    # Filtro por validation_status
    estados_disponibles = sorted(df_geo["validation_status"].dropna().unique().tolist())
    estados_sel = st.sidebar.multiselect(
        "Estado",
        options=estados_disponibles,
        default=estados_disponibles,
        key="visor_estado_filter",
    )

    # Filtro por rubro
    if "rubro" in df_geo.columns:
        rubros_disponibles = sorted(df_geo["rubro"].dropna().unique().tolist())
        rubros_sel = st.sidebar.multiselect(
            "Rubro",
            options=rubros_disponibles,
            default=rubros_disponibles,
            key="visor_rubro_filter",
        )
    else:
        rubros_sel = None

    # ------------------------------------------------------------------
    # Aplicar filtros
    # ------------------------------------------------------------------
    df_filtrado = df_geo.copy()

    if provincias_sel is not None:
        df_filtrado = df_filtrado[df_filtrado["provincia"].isin(provincias_sel)]

    df_filtrado = df_filtrado[df_filtrado["validation_status"].isin(estados_sel)]

    if rubros_sel is not None:
        df_filtrado = df_filtrado[df_filtrado["rubro"].isin(rubros_sel)]

    # ------------------------------------------------------------------
    # Métricas
    # ------------------------------------------------------------------
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total en CSV",      len(df))
    col2.metric("Con coordenadas",   len(df_geo))
    col3.metric("✅ SUCCESS",        int((df_filtrado["validation_status"] == "SUCCESS").sum()))
    col4.metric("⚠️ FAILED",         int((df_filtrado["validation_status"] == "FAILED").sum()))
    col5.metric("❌ ERROR",          int((df_filtrado["validation_status"] == "ERROR").sum()))

    if len(df_sin_coords) > 0:
        st.caption(f"ℹ️ {len(df_sin_coords)} fila(s) sin coordenadas no se muestran en el mapa.")

    if df_filtrado.empty:
        st.warning("No hay puntos para mostrar con los filtros actuales.")
        return

    # ------------------------------------------------------------------
    # Mapa Folium
    # ------------------------------------------------------------------
    centro_lat = df_filtrado["latitude"].mean()
    centro_lon = df_filtrado["longitude"].mean()

    mapa = folium.Map(
        location=[centro_lat, centro_lon],
        zoom_start=10,
        tiles="CartoDB positron",
    )

    for _, row in df_filtrado.iterrows():
        rubro = str(row.get("rubro", "")) if "rubro" in df_filtrado.columns else ""
        cfg   = _RUBRO_CONFIG.get(rubro, _RUBRO_FALLBACK)

        id_cli    = row.get("id_cliente",       "—")
        nombre    = row.get("nombre",           "—") if "nombre"           in df_filtrado.columns else "—"
        sucursal  = row.get("sucursal_asignada","—") if "sucursal_asignada" in df_filtrado.columns else "—"
        direccion = row.get("original_address", "—") if "original_address"  in df_filtrado.columns else "—"

        popup_html = f"""
        <div style="font-family:sans-serif; font-size:13px; min-width:200px;">
            <b>{nombre}</b><br>
            <span style="color:#666">ID: {id_cli}</span><br>
            <hr style="margin:4px 0">
            🏷️ {rubro or '—'}<br>
            🏢 {sucursal}<br>
            📍 {direccion}
        </div>
        """

        folium.Marker(
            location=[row["latitude"], row["longitude"]],
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=nombre,
            icon=folium.Icon(color=cfg["color"], icon=cfg["icon"], prefix="fa"),
        ).add_to(mapa)

    st_folium(mapa, use_container_width=True, height=550, returned_objects=[])

    # ------------------------------------------------------------------
    # Tabla resumen (colapsable)
    # ------------------------------------------------------------------
    with st.expander("Ver tabla de resultados filtrados", expanded=False):
        st.dataframe(df_filtrado, use_container_width=True)

        csv_bytes = df_filtrado.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Descargar filtrado (CSV)",
            data=csv_bytes,
            file_name=f"visor_filtrado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            key="visor_download",
        )


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
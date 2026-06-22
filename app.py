import os
import time
import threading
import pandas as pd
import streamlit as st
from datetime import datetime
import folium
from streamlit_folium import st_folium

from geocoding.processor import GeocodingProcessor
from geocoding.config import LOGS_DIR, RESULTADOS_DIR

# Módulos del visor desacoplados (Sprint 4)
from visor import (
    load_main,
    load_complement,
    merge_complements,
    ColorEngine,
    available_filters,
    apply as filter_apply,
    unique_values,
    build_map,
)
from visor.color_engine import colorable_columns, default_color_column, COLOR_HEX

st.set_page_config(page_title="Validación de Direcciones", layout="wide")

TEMP_DIR = os.path.join("data", "input")


# ---------------------------------------------------------------------------
# Buffer de comunicación hilo → UI
# ---------------------------------------------------------------------------

class ProcessBuffer:
    """Buffer thread-safe para comunicación hilo → UI."""
    def __init__(self):
        self._lock        = threading.Lock()
        self.log_lines    = []
        self.progreso     = 0.0
        self.texto        = ""
        self.resumen      = None
        self.res_file     = None
        self.done         = False

    def append_log(self, mensaje):
        with self._lock:
            self.log_lines.append(mensaje)

    def update_progress(self, progreso, texto):
        with self._lock:
            self.progreso = progreso
            self.texto    = texto

    def finish(self, resumen):
        with self._lock:
            self.resumen  = resumen
            self.res_file = resumen.get("res_file") if resumen else None
            self.done     = True

    def snapshot(self):
        with self._lock:
            return {
                "log_text":  "\n".join(self.log_lines),
                "progreso":  self.progreso,
                "texto":     self.texto,
                "resumen":   self.resumen,
                "res_file":  self.res_file,
                "done":      self.done,
            }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    st.title("Validación de Direcciones — Google Geocoding")
    st.markdown("---")

    _init_state()

    st.sidebar.title("Navegación")
    seccion = st.sidebar.radio("", ["Ejecución", "Historial", "Visor"])

    if seccion == "Ejecución":
        render_ejecucion()
    elif seccion == "Historial":
        render_historial()
    else:
        render_visor()


def _init_state():
    defaults = {
        "running":        False,
        "stopped":        False,
        "processor":      None,
        "thread":         None,
        "buffer":         None,
        "resumen":        None,
        "res_file":       None,
        "log_text":       "",
        "visor_res_file": None,
        # Sprint 4 — estado del visor
        "visor_color_engine":   None,   # ColorEngine persistente entre reruns
        "visor_df_merged":      None,   # DataFrame consolidado (post-merge)
        "visor_active_filters": None,   # Filtros habilitados por el usuario
        "visor_color_col":      None,   # Columna activa de coloreado
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ---------------------------------------------------------------------------
# Sección: Ejecución  (sin cambios)
# ---------------------------------------------------------------------------

def render_ejecucion():
    st.header("Procesamiento de Direcciones")

    if st.session_state.running:
        buf  = st.session_state.buffer
        snap = buf.snapshot()

        if snap["done"]:
            st.session_state.resumen  = snap["resumen"]
            st.session_state.res_file = snap["res_file"]
            st.session_state.log_text = snap["log_text"]
            st.session_state.running  = False
            st.rerun()
            return

        st.info("⏳ Procesamiento en curso...")

        if st.button("⏹ Detener procesamiento", type="secondary"):
            if st.session_state.processor:
                st.session_state.processor.stop()
                st.session_state.stopped = True
            st.warning("Señal de detención enviada. Finalizando la fila actual...")

        st.progress(snap["progreso"])
        st.caption(snap["texto"])

        with st.expander("Logs de ejecución", expanded=True):
            st.text(snap["log_text"])

        time.sleep(0.5)
        st.rerun()
        return

    uploaded_file = st.file_uploader("Cargar archivo CSV", type=["csv"])

    if uploaded_file is not None:
        temp_path = _guardar_archivo_temporal(uploaded_file)
        st.success(f"Archivo cargado: **{uploaded_file.name}**")

        if st.session_state.log_text:
            with st.expander("Logs de la última ejecución", expanded=False):
                st.text(st.session_state.log_text)

        if st.button("Procesar Archivo", type="primary"):
            st.session_state.resumen  = None
            st.session_state.res_file = None
            st.session_state.log_text = ""
            st.session_state.stopped  = False

            buf = ProcessBuffer()
            st.session_state.buffer = buf

            processor = GeocodingProcessor(
                update_callback=buf.update_progress,
                log_callback=buf.append_log,
            )
            st.session_state.processor = processor

            def run_processing():
                resumen = processor.process(temp_path)
                buf.finish(resumen)

            thread = threading.Thread(target=run_processing, daemon=True)
            st.session_state.thread  = thread
            st.session_state.running = True
            thread.start()

    else:
        st.info("Subí un archivo CSV para comenzar.")
        if st.session_state.log_text:
            with st.expander("Logs de la última ejecución", expanded=False):
                st.text(st.session_state.log_text)

    if st.session_state.resumen:
        if st.session_state.stopped:
            st.warning(
                "⚠️ El procesamiento fue interrumpido manualmente. "
                "El CSV contiene los resultados parciales hasta la última fila completada."
            )
        _render_resultados(st.session_state.resumen)


def _guardar_archivo_temporal(uploaded_file):
    os.makedirs(TEMP_DIR, exist_ok=True)
    path = os.path.join(TEMP_DIR, uploaded_file.name)
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return path


def _render_resultados(resumen):
    st.markdown("---")
    st.success("Procesamiento finalizado.")

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Recibidos",  resumen["recibidos"])
    col2.metric("Procesados", resumen["procesados"])

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

    if df_res is not None:
        st.markdown("### Resultados")

        estados_disponibles = sorted(df_res["validation_status"].dropna().unique().tolist())
        estados_seleccionados = st.multiselect(
            "Filtrar por estado",
            options=estados_disponibles,
            default=estados_disponibles,
            key="ejecucion_estado_filter",
        )

        df_filtrado = df_res[df_res["validation_status"].isin(estados_seleccionados)]
        st.dataframe(df_filtrado, use_container_width=True)

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
# Sección: Historial  (sin cambios)
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

    st.markdown("### Detalle de ejecución")
    opciones_log = df_hist["archivo_log"].dropna().tolist()
    selected_log = st.selectbox("Seleccionar ejecución", opciones_log, key="historial_log_selector")

    if selected_log:
        res_file = selected_log.replace(
            os.path.join(LOGS_DIR, "log_ejecucion_"),
            os.path.join(RESULTADOS_DIR, "resultado_geocodificacion_"),
        )
        archivo_a_mostrar = res_file if os.path.exists(res_file) else selected_log

        if os.path.exists(archivo_a_mostrar):
            df_log = pd.read_csv(archivo_a_mostrar)

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
# Sección: Visor  (Sprint 4 — refactorizado)
# ---------------------------------------------------------------------------

def render_visor():
    st.header("Visor de Direcciones")

    # ------------------------------------------------------------------
    # 1. Carga del CSV principal
    # ------------------------------------------------------------------
    res_file_from_hist = st.session_state.get("visor_res_file")

    if res_file_from_hist and os.path.exists(res_file_from_hist):
        st.info(f"Mostrando resultados de: `{os.path.basename(res_file_from_hist)}`")
        if st.button("✖ Cargar otro archivo", key="visor_limpiar"):
            st.session_state.visor_res_file    = None
            st.session_state.visor_df_merged   = None
            st.session_state.visor_color_engine= None
            st.session_state.visor_color_col   = None
            st.rerun()
        try:
            df_main = load_main(res_file_from_hist)
        except Exception as e:
            st.error(f"No se pudo leer el archivo: {e}")
            return
    else:
        uploaded_main = st.file_uploader(
            "Cargar CSV principal de resultados",
            type=["csv"],
            key="visor_uploader_main",
        )
        if uploaded_main is None:
            st.info("Subí un CSV de resultados, o abrilo directo desde el Historial.")
            return
        try:
            df_main = load_main(uploaded_main)
        except Exception as e:
            st.error(f"No se pudo leer el archivo: {e}")
            return

    # ------------------------------------------------------------------
    # 2. Validación de columnas mínimas
    # ------------------------------------------------------------------
    cols_necesarias = {"latitude", "longitude", "validation_status"}
    faltantes = cols_necesarias - set(df_main.columns)
    if faltantes:
        st.error(f"El CSV no tiene las columnas requeridas: {', '.join(sorted(faltantes))}")
        return

    # ------------------------------------------------------------------
    # 3. CSV complementarios (Reqs. 3, 5)
    # ------------------------------------------------------------------
    st.markdown("#### CSV complementarios (opcional)")
    uploaded_complements = st.file_uploader(
        "Cargar uno o más CSV complementarios (vinculados por id_cliente)",
        type=["csv"],
        accept_multiple_files=True,
        key="visor_uploader_complements",
    )

    complements = []
    for uf in (uploaded_complements or []):
        try:
            complements.append(load_complement(uf))
        except Exception as e:
            st.warning(f"No se pudo leer complementario `{uf.name}`: {e}")

    # ------------------------------------------------------------------
    # 4. Merge
    # ------------------------------------------------------------------
    df_merged = merge_complements(df_main, complements)
    df_geo    = df_merged.dropna(subset=["latitude", "longitude"]).copy()
    df_sin_coords = df_merged[df_merged["latitude"].isna() | df_merged["longitude"].isna()]

    if df_geo.empty:
        st.warning("Ninguna fila tiene coordenadas válidas para mostrar en el mapa.")
        return

    # ------------------------------------------------------------------
    # 5. ColorEngine — persistir entre reruns para consistencia de colores
    # ------------------------------------------------------------------
    if st.session_state.visor_color_engine is None:
        engine = ColorEngine()
        st.session_state.visor_color_engine = engine
    else:
        engine = st.session_state.visor_color_engine

    # ------------------------------------------------------------------
    # 6. Selector de coloreado — sidebar
    # ------------------------------------------------------------------
    color_cols = colorable_columns(df_geo)

    if color_cols:
        # Determinar valor por defecto (persistir selección entre reruns)
        if st.session_state.visor_color_col not in color_cols:
            st.session_state.visor_color_col = default_color_column(df_geo)

        st.sidebar.markdown("---")
        st.sidebar.markdown("### Coloreado del mapa")
        color_col = st.sidebar.selectbox(
            "Colorear por:",
            options=color_cols,
            index=color_cols.index(st.session_state.visor_color_col),
            format_func=lambda c: c.replace("_", " ").title(),
            key="visor_color_col_selector",
        )
        st.session_state.visor_color_col = color_col

        # Pre-cargar todos los valores únicos de la columna activa
        engine.build(df_geo, color_col)
    else:
        color_col = None

    # ------------------------------------------------------------------
    # 7. Filtros dinámicos — sidebar
    # ------------------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.markdown("### Filtros del visor")

    all_filters = available_filters(df_geo)   # { col: etiqueta }

    # Req. 6 — selector de filtros visibles
    if len(all_filters) > 3:
        with st.sidebar.expander("⚙️ Filtros visibles", expanded=False):
            active_keys = st.multiselect(
                "Mostrar filtros:",
                options=list(all_filters.keys()),
                default=list(all_filters.keys()),
                format_func=lambda c: all_filters[c],
                key="visor_active_filters_selector",
            )
    else:
        active_keys = list(all_filters.keys())

    # Renderizar cada filtro activo y recolectar selecciones
    selections: dict[str, list] = {}
    for col in active_keys:
        label  = all_filters[col]
        values = unique_values(df_geo, col)
        sel    = st.sidebar.multiselect(
            label,
            options=values,
            default=values,
            key=f"visor_filter_{col}",
        )
        selections[col] = sel

    # ------------------------------------------------------------------
    # 8. Aplicar filtros
    # ------------------------------------------------------------------
    df_filtrado = filter_apply(df_geo, selections)

    # ------------------------------------------------------------------
    # 9. Métricas
    # ------------------------------------------------------------------
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total en CSV",    len(df_merged))
    col2.metric("Con coordenadas", len(df_geo))
    col3.metric("✅ SUCCESS",      int((df_filtrado["validation_status"] == "SUCCESS").sum()))
    col4.metric("⚠️ FAILED",       int((df_filtrado["validation_status"] == "FAILED").sum()))
    col5.metric("❌ ERROR",        int((df_filtrado["validation_status"] == "ERROR").sum()))

    if len(df_sin_coords) > 0:
        st.caption(f"ℹ️ {len(df_sin_coords)} fila(s) sin coordenadas no se muestran en el mapa.")

    if df_filtrado.empty:
        st.warning("No hay puntos para mostrar con los filtros actuales.")
        return

    # ------------------------------------------------------------------
    # 10. Mapa
    # ------------------------------------------------------------------
    mapa = build_map(df_filtrado, engine, color_col=color_col or "rubro")
    st_folium(mapa, use_container_width=True, height=550, returned_objects=[])

    # ------------------------------------------------------------------
    # 11. Leyenda de colores
    # ------------------------------------------------------------------
    _render_legend(engine, color_col or "rubro")

    # ------------------------------------------------------------------
    # 12. Tabla + descarga
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


def _render_legend(engine: ColorEngine, col: str) -> None:
    """Renderiza la leyenda de colores dentro de un expander, dinámica según la columna activa."""
    legend_items = engine.legend(col)
    if not legend_items:
        return

    col_label = col.replace("_", " ").title()
    with st.expander(f"🎨 Leyenda de colores — {col_label}", expanded=True):
        grid_cols = st.columns(min(len(legend_items), 4))
        for i, (category, color) in enumerate(legend_items):
            hex_color = COLOR_HEX.get(color, "#888888")
            dot = (
                f'<span style="display:inline-block;width:14px;height:14px;'
                f'border-radius:50%;background:{hex_color};margin-right:6px;'
                f'vertical-align:middle;"></span>'
            )
            grid_cols[i % 4].markdown(f"{dot}{category}", unsafe_allow_html=True)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
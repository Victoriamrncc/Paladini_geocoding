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
    seccion = st.sidebar.radio("", ["Ejecución", "Historial"])

    if seccion == "Ejecución":
        render_ejecucion()
    else:
        render_historial()


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
        else:
            st.warning(f"No se encontró el archivo de resultados en disco: `{archivo_a_mostrar}`")


if __name__ == "__main__":
    main()
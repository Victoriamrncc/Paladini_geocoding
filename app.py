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
#
# El hilo secundario NO puede escribir en st.session_state directamente:
# SafeSessionState.__setitem__ llama _yield_callback(), que propaga
# RerunException al hilo y lo mata silenciosamente durante el loop de rerun.
#
# Solución: el hilo escribe en este objeto Python simple (sin Streamlit).
# El hilo principal lee de aquí en cada rerun y copia al session_state.
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
        """Lectura atómica para el hilo principal."""
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

    if "running"        not in st.session_state: st.session_state.running        = False
    if "stopped"        not in st.session_state: st.session_state.stopped        = False
    if "processor"      not in st.session_state: st.session_state.processor      = None
    if "thread"         not in st.session_state: st.session_state.thread         = None
    if "buffer"         not in st.session_state: st.session_state.buffer         = None
    if "resumen"        not in st.session_state: st.session_state.resumen        = None
    if "res_file"       not in st.session_state: st.session_state.res_file       = None
    if "log_text"       not in st.session_state: st.session_state.log_text       = ""
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

    # ------------------------------------------------------------------
    # ESTADO A: procesamiento en curso
    # ------------------------------------------------------------------
    if st.session_state.running:

        # Leer buffer — sin tocar session_state desde el hilo
        buf  = st.session_state.buffer
        snap = buf.snapshot()

        # ¿Terminó el hilo?
        if snap["done"]:
            # Copiar resultados al session_state ahora que estamos en el hilo principal
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

    # ------------------------------------------------------------------
    # ESTADO B: en reposo
    # ------------------------------------------------------------------
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
            # Sin st.rerun() — Streamlit ya rerenderiza por el click del botón

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
# Sección: Visor
# ---------------------------------------------------------------------------

_RUBRO_CONFIG = {
    "Supermercado": {"icon": "shopping-cart", "color": "blue"},
    "Almacén":      {"icon": "home",          "color": "green"},
    "Gran Cadena":  {"icon": "building",      "color": "red"},
}
_RUBRO_FALLBACK = {"icon": "map-marker", "color": "gray"}


def render_visor():
    st.header("Visor de Direcciones")

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
        uploaded_file = st.file_uploader("Cargar CSV de resultados", type=["csv"], key="visor_uploader")
        if uploaded_file is None:
            st.info("Subí un CSV de resultados, o abrilo directo desde el Historial.")
            return
        try:
            df = pd.read_csv(uploaded_file)
        except Exception as e:
            st.error(f"No se pudo leer el archivo: {e}")
            return

    cols_necesarias = {"latitude", "longitude", "validation_status"}
    faltantes = cols_necesarias - set(df.columns)
    if faltantes:
        st.error(f"El CSV no tiene las columnas requeridas: {', '.join(sorted(faltantes))}")
        return

    df_geo        = df.dropna(subset=["latitude", "longitude"]).copy()
    df_sin_coords = df[df["latitude"].isna() | df["longitude"].isna()]

    if df_geo.empty:
        st.warning("Ninguna fila tiene coordenadas válidas para mostrar en el mapa.")
        return

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Filtros del visor")

    provincias_sel = None
    if "provincia" in df_geo.columns:
        provincias_disponibles = sorted(df_geo["provincia"].dropna().unique().tolist())
        provincias_sel = st.sidebar.multiselect("Provincia", options=provincias_disponibles, default=provincias_disponibles, key="visor_provincia_filter")

    estados_disponibles = sorted(df_geo["validation_status"].dropna().unique().tolist())
    estados_sel = st.sidebar.multiselect("Estado", options=estados_disponibles, default=estados_disponibles, key="visor_estado_filter")

    rubros_sel = None
    if "rubro" in df_geo.columns:
        rubros_disponibles = sorted(df_geo["rubro"].dropna().unique().tolist())
        rubros_sel = st.sidebar.multiselect("Rubro", options=rubros_disponibles, default=rubros_disponibles, key="visor_rubro_filter")

    df_filtrado = df_geo.copy()
    if provincias_sel is not None:
        df_filtrado = df_filtrado[df_filtrado["provincia"].isin(provincias_sel)]
    df_filtrado = df_filtrado[df_filtrado["validation_status"].isin(estados_sel)]
    if rubros_sel is not None:
        df_filtrado = df_filtrado[df_filtrado["rubro"].isin(rubros_sel)]

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total en CSV",    len(df))
    col2.metric("Con coordenadas", len(df_geo))
    col3.metric("✅ SUCCESS",      int((df_filtrado["validation_status"] == "SUCCESS").sum()))
    col4.metric("⚠️ FAILED",       int((df_filtrado["validation_status"] == "FAILED").sum()))
    col5.metric("❌ ERROR",        int((df_filtrado["validation_status"] == "ERROR").sum()))

    if len(df_sin_coords) > 0:
        st.caption(f"ℹ️ {len(df_sin_coords)} fila(s) sin coordenadas no se muestran en el mapa.")

    if df_filtrado.empty:
        st.warning("No hay puntos para mostrar con los filtros actuales.")
        return

    centro_lat = df_filtrado["latitude"].mean()
    centro_lon = df_filtrado["longitude"].mean()
    mapa = folium.Map(location=[centro_lat, centro_lon], zoom_start=10, tiles="CartoDB positron")

    for _, row in df_filtrado.iterrows():
        rubro    = str(row.get("rubro", "")) if "rubro" in df_filtrado.columns else ""
        cfg      = _RUBRO_CONFIG.get(rubro, _RUBRO_FALLBACK)
        id_cli   = row.get("id_cliente",        "—")
        nombre   = row.get("nombre",            "—") if "nombre"            in df_filtrado.columns else "—"
        sucursal = row.get("sucursal_asignada", "—") if "sucursal_asignada" in df_filtrado.columns else "—"
        direccion= row.get("original_address",  "—") if "original_address"  in df_filtrado.columns else "—"

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
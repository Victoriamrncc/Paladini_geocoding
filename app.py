import os
import threading
import time as _time
import math
import pandas as pd
import streamlit as st
from datetime import datetime

from geocoding.processor import GeocodingProcessor
from geocoding.config import LOGS_DIR, RESULTADOS_DIR, CHUNK_SIZE

# ---------------------------------------------------------------------------
# Configuración de página
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Validación de Direcciones",
    layout="wide",
)

TEMP_DIR = os.path.join("data", "input")

# ---------------------------------------------------------------------------
# Estado compartido entre hilo y hilo principal
#
# Streamlit prohíbe acceder a st.session_state desde hilos secundarios
# (no tienen ScriptRunContext). El hilo escribe SOLO en estas estructuras
# Python puras; el hilo principal las lee en cada rerun del polling y
# las vuelca a session_state.
# ---------------------------------------------------------------------------

_shared = {
    "log_buffer":   [],     # list de strings — append es thread-safe en CPython
    "progress":     0.0,    # float 0.0–1.0
    "status_text":  "",     # string
    "result":       None,   # dict resumen o None
    "done":         False,  # True cuando el hilo terminó
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    st.title("Validación de Direcciones — Google Geocoding")
    st.markdown("---")

    if "resumen"    not in st.session_state: st.session_state.resumen    = None
    if "log_text"   not in st.session_state: st.session_state.log_text   = ""
    if "res_file"   not in st.session_state: st.session_state.res_file   = None
    if "procesando" not in st.session_state: st.session_state.procesando = False
    if "processor"  not in st.session_state: st.session_state.processor  = None
    if "hilo"       not in st.session_state: st.session_state.hilo       = None

    st.sidebar.title("Navegación")
    seccion = st.sidebar.radio("Sección", ["Ejecución", "Historial"],
                               label_visibility="collapsed")

    if seccion == "Ejecución":
        render_ejecucion()
    else:
        render_historial()


# ---------------------------------------------------------------------------
# Sección: Ejecución
# ---------------------------------------------------------------------------

def render_ejecucion():
    st.header("Procesamiento de Direcciones")

    uploaded_file = st.file_uploader(
        "Cargar archivo CSV",
        type=["csv"],
        disabled=st.session_state.procesando,
    )

    if uploaded_file is not None:
        temp_path = _guardar_archivo_temporal(uploaded_file)
        st.success(f"Archivo cargado: **{uploaded_file.name}**")

        # Aviso de chunks
        try:
            df_preview = pd.read_csv(temp_path, dtype=str)
            n_filas = len(df_preview)
            if n_filas > CHUNK_SIZE:
                n_chunks = math.ceil(n_filas / CHUNK_SIZE)
                st.info(
                    f"El archivo tiene **{n_filas} filas** — se procesará en "
                    f"**{n_chunks} lotes de hasta {CHUNK_SIZE} filas** cada uno. "
                    f"Al finalizar se genera un CSV consolidado con todos los resultados."
                )
        except Exception:
            pass

        col_btn1, col_btn2, _ = st.columns([1, 1, 5])
        with col_btn1:
            start_button = st.button(
                "▶ Procesar",
                type="primary",
                disabled=st.session_state.procesando,
            )
        with col_btn2:
            stop_button = st.button(
                "⏹ Detener",
                disabled=not st.session_state.procesando,
            )
    else:
        start_button = False
        stop_button  = False
        temp_path    = None
        st.info("Subí un archivo CSV para comenzar.")
        if st.session_state.log_text:
            with st.expander("Logs de la última ejecución", expanded=False):
                st.text(st.session_state.log_text)

    # Contenedores que se actualizan en cada rerun
    progress_placeholder = st.empty()
    status_placeholder   = st.empty()

    with st.expander("Logs de ejecución", expanded=True):
        log_container = st.empty()

    # ------------------------------------------------------------------
    # Arrancar hilo
    # ------------------------------------------------------------------
    if start_button and temp_path:
        # Limpiar estado previo
        st.session_state.resumen    = None
        st.session_state.log_text   = ""
        st.session_state.res_file   = None
        st.session_state.procesando = True

        # Resetear buffer compartido
        _shared["log_buffer"]  = []
        _shared["progress"]    = 0.0
        _shared["status_text"] = ""
        _shared["result"]      = None
        _shared["done"]        = False

        # Callbacks que solo tocan estructuras Python puras
        def update_progress(progreso, texto):
            _shared["progress"]    = min(float(progreso), 1.0)
            _shared["status_text"] = texto

        def append_log(mensaje):
            _shared["log_buffer"].append(mensaje)

        processor = GeocodingProcessor(
            update_callback=update_progress,
            log_callback=append_log,
        )
        st.session_state.processor = processor

        def _run():
            result = processor.process_chunked(temp_path)
            _shared["result"] = result
            _shared["done"]   = True

        hilo = threading.Thread(target=_run, daemon=True)
        st.session_state.hilo = hilo
        hilo.start()
        st.rerun()

    # ------------------------------------------------------------------
    # Botón Stop
    # ------------------------------------------------------------------
    if stop_button and st.session_state.processor:
        st.session_state.processor.stop()
        status_placeholder.text("⏹ Deteniendo al finalizar la fila actual...")

    # ------------------------------------------------------------------
    # Polling: mientras corre, sincronizar buffer → session_state y refrescar
    # ------------------------------------------------------------------
    if st.session_state.procesando:
        # Volcar buffer de log al session_state (solo lectura del hilo principal)
        nuevos = _shared["log_buffer"]
        if nuevos:
            st.session_state.log_text = "\n".join(nuevos)

        # Mostrar progreso y log actualizados
        progress_placeholder.progress(_shared["progress"])
        status_placeholder.text(_shared["status_text"])
        log_container.text(st.session_state.log_text)

        if _shared["done"]:
            # Hilo terminó — pasar resultado a session_state y salir del polling
            st.session_state.procesando = False
            resumen = _shared["result"]
            if resumen:
                st.session_state.resumen  = resumen
                st.session_state.res_file = resumen.get("res_file")
            st.rerun()
        else:
            _time.sleep(2)
            st.rerun()

    # ------------------------------------------------------------------
    # Mostrar resultado una vez terminado
    # ------------------------------------------------------------------
    else:
        if st.session_state.log_text:
            log_container.text(st.session_state.log_text)

        resumen = st.session_state.resumen
        if resumen:
            progress_placeholder.empty()
            status_placeholder.empty()

            if resumen.get("crash_error"):
                st.warning(
                    f"⚠️ El procesamiento se interrumpió inesperadamente.\n\n"
                    f"**Error:** `{resumen['crash_error']}`\n\n"
                    f"Se guardaron {resumen['procesados']} de {resumen['recibidos']} registros."
                )
            elif resumen.get("chunks_total", 1) > 1:
                st.success(
                    f"✅ Procesamiento en lotes completado — "
                    f"{resumen['chunks_completados']}/{resumen['chunks_total']} lotes OK."
                )

            _render_resultados(resumen)


def _guardar_archivo_temporal(uploaded_file):
    os.makedirs(TEMP_DIR, exist_ok=True)
    path = os.path.join(TEMP_DIR, uploaded_file.name)
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return path


# ---------------------------------------------------------------------------
# Diagnóstico y resultados
# ---------------------------------------------------------------------------

def _avisos_diagnostico(resumen, df_res):
    if df_res is None or df_res.empty:
        return

    total      = len(df_res)
    n_error    = int((df_res["validation_status"] == "ERROR").sum())
    tasa_error = n_error / total if total > 0 else 0

    errores_msgs = df_res.loc[
        df_res["validation_status"] == "ERROR", "error_message"
    ].dropna().astype(str)

    tiene_api_key_invalida = errores_msgs.str.contains("invalid_api_key", na=False).any()
    tiene_quota_excedida   = errores_msgs.str.contains("quota_exceeded",  na=False).any()
    tiene_request_failed   = errores_msgs.str.contains("request_failed",  na=False).any()

    if tiene_api_key_invalida:
        st.error(
            "🔑 **API Key inválida** — Google rechazó las solicitudes con `REQUEST_DENIED`.\n\n"
            "Verificá que `GOOGLE_API_KEY` en el archivo `.env` sea correcta y tenga "
            "habilitada la API de Geocoding."
        )
    elif tiene_quota_excedida:
        st.error(
            "📊 **Cuota de API excedida** — Google devolvió `OVER_QUERY_LIMIT`.\n\n"
            "Esperá unos minutos antes de volver a procesar, o revisá los límites "
            "de tu plan en Google Cloud Console."
        )
    elif tiene_request_failed and tasa_error > 0.5:
        st.warning(
            f"🌐 **Problemas de conectividad** — {n_error}/{total} filas fallaron por "
            f"errores de red o timeout.\n\n"
            f"Verificá la conexión a internet y volvé a procesar las filas con ERROR."
        )
    elif tasa_error > 0.5:
        error_frecuente = errores_msgs.value_counts().idxmax() if not errores_msgs.empty else "desconocido"
        st.warning(
            f"⚠️ **Tasa de error alta** — {n_error} de {total} filas terminaron en ERROR "
            f"({tasa_error:.0%}).\n\n"
            f"Error más frecuente: `{error_frecuente}`"
        )

    if resumen.get("procesados", 0) < resumen.get("recibidos", 0) and not resumen.get("crash_error"):
        faltantes = resumen["recibidos"] - resumen["procesados"]
        st.info(
            f"⏹ Procesamiento detenido por el usuario — "
            f"quedaron **{faltantes} filas sin procesar** "
            f"({resumen['procesados']} de {resumen['recibidos']} completadas)."
        )


def _render_resultados(resumen):
    st.markdown("---")

    res_file = resumen.get("res_file")
    df_res   = None
    if res_file and os.path.exists(res_file):
        df_res    = pd.read_csv(res_file)
        n_success = int((df_res["validation_status"] == "SUCCESS").sum())
        n_failed  = int((df_res["validation_status"] == "FAILED").sum())
        n_error   = int((df_res["validation_status"] == "ERROR").sum())
    else:
        n_success = resumen.get("exitosos", 0)
        n_failed  = resumen.get("errores", 0)
        n_error   = 0

    if not resumen.get("crash_error"):
        if n_error == 0 and n_failed == 0:
            st.success("✅ Procesamiento finalizado sin errores.")
        else:
            st.success("Procesamiento finalizado.")

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Recibidos",  resumen["recibidos"])
    col2.metric("Procesados", resumen["procesados"])
    col3.metric("✅ SUCCESS", n_success)
    col4.metric("⚠️ FAILED",  n_failed)
    col5.metric("❌ ERROR",   n_error)

    caption_parts = [f"Tiempo total: {resumen['tiempo']}s"]
    if resumen.get("chunks_total", 1) > 1:
        caption_parts.append(
            f"Lotes: {resumen.get('chunks_completados', '?')}/{resumen['chunks_total']}"
        )
    if resumen.get("log_file"):
        caption_parts.append(f"Log: `{resumen['log_file']}`")
    st.caption("  |  ".join(caption_parts))

    _avisos_diagnostico(resumen, df_res)

    if df_res is not None:
        st.markdown("### Resultados")
        estados_disponibles   = sorted(df_res["validation_status"].dropna().unique().tolist())
        estados_seleccionados = st.multiselect(
            "Filtrar por estado",
            options=estados_disponibles,
            default=estados_disponibles,
            key="ejecucion_estado_filter",
        )
        df_filtrado = df_res[df_res["validation_status"].isin(estados_seleccionados)]
        st.dataframe(df_filtrado, use_container_width=True)
        csv_bytes = df_filtrado.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Descargar resultados (CSV)",
            data=csv_bytes,
            file_name=os.path.basename(res_file),
            mime="text/csv",
        )


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
    df_hist = df_hist.sort_values(by="fecha_ejecucion", ascending=False).reset_index(drop=True)

    st.caption("Hacé click en una fila para ver el detalle de esa ejecución.")

    seleccion = st.dataframe(
        df_hist,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        key="historial_tabla",
    )

    filas_seleccionadas = seleccion.selection.get("rows", [])

    if not filas_seleccionadas:
        st.info("Seleccioná una fila para ver el detalle.")
        return

    idx          = filas_seleccionadas[0]
    fila         = df_hist.iloc[idx]
    selected_log = fila.get("archivo_log")

    if not selected_log or pd.isna(selected_log):
        st.warning("Esta ejecución no tiene log registrado.")
        return

    st.markdown("---")
    st.markdown(
        f"### Detalle — {fila.get('fecha_ejecucion', '')}  |  `{fila.get('archivo', '')}`"
    )
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Recibidos",  int(fila.get("registros_recibidos",  0)))
    col2.metric("Procesados", int(fila.get("registros_procesados", 0)))
    col3.metric("Exitosos",   int(fila.get("exitosos",  0)))
    col4.metric("Fallidos",   int(fila.get("fallidos",  0)))
    st.caption(f"Tiempo: {fila.get('tiempo_segundos', '?')}s  |  Log: `{selected_log}`")

    res_file = selected_log.replace(
        os.path.join(LOGS_DIR, "log_ejecucion_"),
        os.path.join(RESULTADOS_DIR, "resultado_geocodificacion_"),
    )
    archivo_a_mostrar = res_file if os.path.exists(res_file) else selected_log

    if not os.path.exists(archivo_a_mostrar):
        st.warning(f"No se encontró el archivo en disco: `{archivo_a_mostrar}`")
        return

    df_detalle = pd.read_csv(archivo_a_mostrar)

    tab_res, tab_log = st.tabs(["📋 Resultados", "🔍 Log de debug"])

    with tab_res:
        estados_disponibles   = sorted(df_detalle["validation_status"].dropna().unique().tolist())
        estados_seleccionados = st.multiselect(
            "Filtrar por estado",
            options=estados_disponibles,
            default=estados_disponibles,
            key="historial_estado_filter",
        )
        df_filtrado = df_detalle[df_detalle["validation_status"].isin(estados_seleccionados)]
        st.dataframe(df_filtrado, use_container_width=True)
        csv_bytes = df_filtrado.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Descargar resultados (CSV)",
            data=csv_bytes,
            file_name=f"resultado_filtrado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            key="historial_download_res",
        )

    with tab_log:
        if os.path.exists(selected_log):
            df_log_debug = pd.read_csv(selected_log)
            st.dataframe(df_log_debug, use_container_width=True)
            csv_log = df_log_debug.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="Descargar log de debug (CSV)",
                data=csv_log,
                file_name=os.path.basename(selected_log),
                mime="text/csv",
                key="historial_download_log",
            )
        else:
            st.info("El log de debug no está disponible para esta ejecución.")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
import os
import pandas as pd
import streamlit as st
from datetime import datetime

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

    st.sidebar.title("Navegación")
    seccion = st.sidebar.radio("", ["Ejecución", "Historial"])

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
        "Cargar archivo CSV o Excel",
        type=["csv", "xlsx", "xls"],
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
        st.info("Subí un archivo CSV o Excel para comenzar.")
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

    # Leer CSV para desglosar SUCCESS / FAILED / ERROR
    res_file = resumen.get("res_file")
    if res_file and os.path.exists(res_file):
        df_res = pd.read_csv(res_file)
        n_success = int((df_res["validation_status"] == "SUCCESS").sum())
        n_failed  = int((df_res["validation_status"] == "FAILED").sum())
        n_error   = int((df_res["validation_status"] == "ERROR").sum())
    else:
        n_success = resumen["exitosos"]
        n_failed  = resumen["errores"]
        n_error   = 0

    col3.metric("✅ SUCCESS", n_success)
    col4.metric("⚠️ FAILED",  n_failed)

    st.caption(f"Tiempo total: {resumen['tiempo']}s  |  Log: `{resumen['log_file']}`")

    # --- Tabla de resultados ---
    if res_file and os.path.exists(res_file):
        st.markdown("### Resultados")

        df_res = pd.read_csv(res_file)

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

        # Descarga del CSV filtrado
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

            csv_bytes = df_filtrado.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="Descargar resultados (CSV)",
                data=csv_bytes,
                file_name=f"resultado_filtrado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
            )
        else:
            st.warning(f"No se encontró el archivo de resultados en disco: `{archivo_a_mostrar}`")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
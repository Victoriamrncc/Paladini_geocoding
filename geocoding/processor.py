import os
import time
import pandas as pd
from datetime import datetime

from .config import COLUMNAS_REQUERIDAS, SLEEP_ENTRE_REQUESTS, CHUNK_SIZE
from .api_clients import geocodificar_google, reverse_geocode_google
from .validation import validar_coordenadas
from .logging_utils import ExecutionLogger


def _leer_csv_robusto(filepath):
    """
    Lee un CSV tolerando distintos delimitadores (`,` `;` `\t`) y encodings.
    Prueba encodings en orden de probabilidad para archivos argentinos/latinoamericanos.
    """
    encodings = ["utf-8-sig", "utf-8", "latin1", "cp1252"]
    for encoding in encodings:
        try:
            df = pd.read_csv(filepath, sep=None, engine="python", encoding=encoding, dtype=str)
            df.columns = df.columns.str.strip()
            return df
        except UnicodeDecodeError:
            continue
    raise ValueError(f"No se pudo leer el CSV con ninguno de los encodings probados: {encodings}")


class GeocodingProcessor:
    """
    Coordina el flujo completo de validación geográfica para cada fila del CSV.

    Flujo por fila:
        1. Geocoding (dirección original → lat1, lon1)
        2. Reverse geocoding (lat1, lon1 → direccion_reverse)
        3. Segundo geocoding (direccion_reverse → lat2, lon2)
        4. Validación Haversine (distancia entre (lat1, lon1) y (lat2, lon2))
        5. Registro del resultado

    Tres estados posibles por fila:
        SUCCESS  — flujo completo OK, distancia <= MAX_DISTANCE_METERS
        FAILED   — flujo completo OK, distancia >  MAX_DISTANCE_METERS
        ERROR    — fallo técnico en algún paso (timeout, API key, HTTP, etc.)

    La distinción entre FAILED y ERROR es intencional:
        - FAILED conserva todas las coordenadas y datos del flujo (útiles para análisis).
        - ERROR puede no tener coordenadas válidas según en qué paso falló.

    Responsabilidades:
        - Leer y validar el archivo de entrada
        - Construir la query para la API
        - Coordinar llamadas a api_clients
        - Delegar validación a validation.py
        - Delegar persistencia a logging_utils.py
        - Reportar progreso y logs a la UI vía callbacks
    """

    def __init__(self, update_callback=None, log_callback=None):
        self.update_callback = update_callback
        self.log_callback    = log_callback
        self.should_stop     = False

    # ------------------------------------------------------------------
    # Interfaz pública
    # ------------------------------------------------------------------

    def stop(self):
        """Señal para detener el procesamiento al finalizar la fila actual."""
        self.should_stop = True

    def process(self, filepath):
        """
        Procesa el archivo CSV o Excel en `filepath`.
        Retorna el dict de resumen de ExecutionLogger.end_and_save()
        o None si el archivo no pasa la validación inicial.

        Garantiza guardado parcial: si el proceso se interrumpe por cualquier
        causa (excepción, crash, Stop del usuario), end_and_save() se llama
        igual con todo lo procesado hasta ese momento.
        """
        self.should_stop = False

        valid, result = self._validate_file(filepath)
        if not valid:
            self._log(f"[Error] Archivo inválido: {result}")
            return None

        df         = self._preparar_dataframe(result)
        total_rows = len(df)
        logger     = ExecutionLogger(os.path.basename(filepath))
        logger.start(total_rows)

        self._log(f"Iniciando procesamiento: {total_rows} registro(s) en '{os.path.basename(filepath)}'.")
        self._log("-" * 60)

        crash_error = None

        try:
            for numero_fila, (_, row) in enumerate(df.iterrows(), start=1):

                if self.should_stop:
                    self._log("Procesamiento detenido por el usuario.")
                    break

                self._actualizar_progreso(numero_fila, total_rows, row["id_cliente"])
                record = self._procesar_fila(numero_fila, total_rows, row)
                logger.log_record(record)
                self._log("-" * 60)

        except Exception as e:
            crash_error = e
            self._log(f"[CRASH] Error inesperado en fila {numero_fila}: {type(e).__name__}: {e}")
            self._log("Guardando resultados parciales...")

        finally:
            self._actualizar_progreso_final()
            resumen = logger.end_and_save()

            if crash_error:
                resumen["crash_error"] = f"{type(crash_error).__name__}: {crash_error}"
                self._log(
                    f"Guardado parcial — "
                    f"Procesados: {resumen['procesados']}/{total_rows} | "
                    f"Exitosos: {resumen['exitosos']} | "
                    f"Fallidos: {resumen['errores']} | "
                    f"Tiempo: {resumen['tiempo']}s"
                )
            else:
                resumen["crash_error"] = None
                self._log(
                    f"Finalizado — "
                    f"Procesados: {resumen['procesados']} | "
                    f"Exitosos: {resumen['exitosos']} | "
                    f"Fallidos: {resumen['errores']} | "
                    f"Tiempo: {resumen['tiempo']}s"
                )

        return resumen

    def process_chunked(self, filepath):
        """
        Punto de entrada principal. Si el archivo supera CHUNK_SIZE filas,
        lo divide en lotes y procesa cada uno con process().

        Para cada chunk genera archivos de log y resultado independientes
        (con sufijo _chunk_N en el timestamp). Al finalizar todos los chunks
        consolida los resultados en un único CSV descargable.

        Retorna dict con:
            chunks_total        int   — cantidad de lotes procesados
            chunks_completados  int   — lotes que terminaron sin crash
            recibidos           int   — total de filas en el archivo original
            procesados          int   — total de filas procesadas en todos los lotes
            exitosos            int
            errores             int
            tiempo              float — tiempo total en segundos
            res_files           list  — paths de cada archivo de resultado por chunk
            res_consolidado     str   — path del CSV consolidado (todos los chunks)
            crash_error         str | None — error del último crash, si hubo
        """
        self.should_stop = False

        # Validar y preparar el DataFrame completo una sola vez
        valid, result = self._validate_file(filepath)
        if not valid:
            self._log(f"[Error] Archivo inválido: {result}")
            return None

        df         = self._preparar_dataframe(result)
        total_rows = len(df)

        # Sin chunks necesarios: delegar directo a process()
        if total_rows <= CHUNK_SIZE:
            resumen = self.process(filepath)
            if resumen:
                resumen["chunks_total"]       = 1
                resumen["chunks_completados"] = 0 if resumen.get("crash_error") else 1
                resumen["res_files"]          = [resumen["res_file"]]
                resumen["res_consolidado"]    = resumen["res_file"]
            return resumen

        # Dividir en chunks
        chunks       = [df.iloc[i:i + CHUNK_SIZE] for i in range(0, total_rows, CHUNK_SIZE)]
        n_chunks     = len(chunks)
        inicio_total = datetime.now()

        self._log(
            f"Archivo con {total_rows} filas — "
            f"dividido en {n_chunks} lote(s) de hasta {CHUNK_SIZE} filas."
        )
        self._log("=" * 60)

        resumen_global = {
            "chunks_total":       n_chunks,
            "chunks_completados": 0,
            "recibidos":          total_rows,
            "procesados":         0,
            "exitosos":           0,
            "errores":            0,
            "tiempo":             0.0,
            "res_files":          [],
            "res_consolidado":    None,
            "crash_error":        None,
            "log_file":           None,
        }

        for idx, chunk_df in enumerate(chunks, start=1):

            if self.should_stop:
                self._log(f"Procesamiento detenido por el usuario antes del lote {idx}/{n_chunks}.")
                break

            self._log(f"--- Lote {idx}/{n_chunks} ({len(chunk_df)} filas) ---")

            # Escribir el chunk a un CSV temporal para reutilizar process()
            temp_chunk_path = os.path.join(
                os.path.dirname(filepath),
                f"_chunk_{idx}_{os.path.basename(filepath)}"
            )
            chunk_df[["id_cliente", "Dirección", "Localidad", "Provincia"]] \
                .rename(columns={"id_cliente": "ID_Cliente"}) \
                .to_csv(temp_chunk_path, index=False, encoding="utf-8")

            # Actualizar progreso global antes de iniciar el chunk
            if self.update_callback:
                progreso_base = (idx - 1) / n_chunks
                self.update_callback(
                    progreso_base,
                    f"Lote {idx}/{n_chunks} — procesando {len(chunk_df)} filas..."
                )

            resumen_chunk = self.process(temp_chunk_path)

            # Limpiar el CSV temporal
            try:
                os.remove(temp_chunk_path)
            except OSError:
                pass

            if resumen_chunk is None:
                self._log(f"  [Lote {idx}] No pudo iniciarse. Saltando.")
                continue

            # Acumular totales
            resumen_global["procesados"] += resumen_chunk["procesados"]
            resumen_global["exitosos"]   += resumen_chunk["exitosos"]
            resumen_global["errores"]    += resumen_chunk["errores"]

            if resumen_chunk.get("res_file"):
                resumen_global["res_files"].append(resumen_chunk["res_file"])

            if resumen_chunk.get("crash_error"):
                resumen_global["crash_error"] = (
                    f"Lote {idx}: {resumen_chunk['crash_error']}"
                )
            else:
                resumen_global["chunks_completados"] += 1

            self._log(f"  [Lote {idx}] OK — {resumen_chunk['procesados']} filas procesadas.")
            self._log("=" * 60)

        # Consolidar todos los CSVs de resultado en uno solo
        resumen_global["tiempo"] = round(
            (datetime.now() - inicio_total).total_seconds(), 2
        )

        if resumen_global["res_files"]:
            res_consolidado = self._consolidar_resultados(resumen_global["res_files"])
            resumen_global["res_consolidado"] = res_consolidado
            resumen_global["res_file"]        = res_consolidado  # compatibilidad con app.py
        else:
            resumen_global["res_file"] = None

        # log_file apunta al último chunk (para compatibilidad con historial)
        resumen_global["log_file"] = resumen_chunk.get("log_file") if resumen_chunk else None

        if self.update_callback:
            self.update_callback(1.0, "Finalizado")

        self._log(
            f"Consolidado — "
            f"Lotes: {resumen_global['chunks_completados']}/{n_chunks} | "
            f"Procesados: {resumen_global['procesados']}/{total_rows} | "
            f"Exitosos: {resumen_global['exitosos']} | "
            f"Fallidos: {resumen_global['errores']} | "
            f"Tiempo: {resumen_global['tiempo']}s"
        )

        return resumen_global

    def _consolidar_resultados(self, res_files):
        """
        Une todos los CSVs de resultado de cada chunk en un único archivo.
        El archivo consolidado se guarda en RESULTADOS_DIR con sufijo _consolidado.
        Retorna el path del archivo consolidado.
        """
        from .config import RESULTADOS_DIR
        timestamp      = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path_consol    = os.path.join(
            RESULTADOS_DIR,
            f"resultado_consolidado_{timestamp}.csv"
        )
        dfs = []
        for f in res_files:
            if os.path.exists(f):
                try:
                    dfs.append(pd.read_csv(f))
                except Exception as e:
                    self._log(f"[Consolidar] No se pudo leer {f}: {e}")

        if dfs:
            pd.concat(dfs, ignore_index=True).to_csv(path_consol, index=False, encoding="utf-8")
            self._log(f"Resultado consolidado guardado en: {path_consol}")
        else:
            self._log("[Consolidar] No hay archivos de resultado para consolidar.")
            return None

        return path_consol

    # ------------------------------------------------------------------
    # Validación y preparación del archivo
    # ------------------------------------------------------------------

    def _validate_file(self, filepath):
        """
        Lee el archivo y verifica estructura mínima.
        Retorna (True, DataFrame) o (False, mensaje_error).
        """
        try:
            if filepath.endswith(".csv"):
                df = _leer_csv_robusto(filepath)
            elif filepath.endswith((".xls", ".xlsx")):
                df = pd.read_excel(filepath)
            else:
                return False, "Formato no soportado. El archivo debe ser CSV o Excel."

            if df.empty:
                return False, "El archivo está vacío."

            faltantes = COLUMNAS_REQUERIDAS - set(df.columns)
            if faltantes:
                return False, f"Faltan columnas requeridas: {', '.join(sorted(faltantes))}"

            return True, df

        except Exception as e:
            return False, f"No se pudo leer el archivo: {e}"

    def _preparar_dataframe(self, df):
        """
        Normaliza el DataFrame de entrada:
        - Renombra columnas a nombres internos
        - Construye la query que se enviará a la API
        """
        df = df.copy()
        df = df.rename(columns={"ID_Cliente": "id_cliente"})

        df["dir_original"]       = df["Dirección"].astype(str).str.strip()
        df["localidad_original"] = df["Localidad"].astype(str).str.strip()
        df["provincia_original"] = df["Provincia"].astype(str).str.strip()

        df["input_query"] = df.apply(self._construir_query, axis=1)
        return df

    @staticmethod
    def _construir_query(row):
        """
        Construye la query de geocoding uniendo los campos de dirección.
        Omite partes vacías o con valor 'nan'.
        """
        partes = [
            row["dir_original"],
            row["localidad_original"],
            row["provincia_original"],
            "Argentina",
        ]
        return ", ".join(
            p for p in partes
            if p and str(p).lower() != "nan"
        )

    @staticmethod
    def _campos_vacios(row):
        """
        Detecta qué campos de geocoding están vacíos o tienen valor 'nan'.
        Retorna lista de nombres de columna originales vacíos, o [] si todo está completo.
        """
        campos = {
            "Dirección": row["dir_original"],
            "Localidad":  row["localidad_original"],
            "Provincia":  row["provincia_original"],
        }
        return [
            nombre
            for nombre, valor in campos.items()
            if not valor or str(valor).lower() == "nan"
        ]

    # ------------------------------------------------------------------
    # Flujo por fila
    # ------------------------------------------------------------------

    def _procesar_fila(self, numero_fila, total_rows, row):
        """
        Ejecuta el flujo completo para una fila.

        El estado se construye incrementalmente: cada paso agrega sus datos
        antes de continuar. Si un paso falla técnicamente, el estado ya
        contiene todo lo recopilado hasta ese momento.

        Estados posibles en el record resultante:
            SUCCESS  — distancia <= MAX_DISTANCE_METERS
            FAILED   — distancia >  MAX_DISTANCE_METERS  (datos completos conservados)
            ERROR    — fallo técnico en API              (datos parciales según paso fallido)
        """
        id_cliente       = row["id_cliente"]
        direccion        = row["dir_original"]
        localidad        = row["localidad_original"]
        provincia        = row["provincia_original"]
        original_address = row["input_query"]
        timestamp        = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self._log(f"[{numero_fila}/{total_rows}] {original_address[:70]}...")

        # ----------------------------------------------------------
        # PASO 0 — Validación de campos vacíos
        # ----------------------------------------------------------
        campos_faltantes = self._campos_vacios(row)
        if campos_faltantes:
            faltantes_str = ", ".join(campos_faltantes)
            mensaje = f"Campos vacíos: {faltantes_str}"
            self._log(f"  => ERROR: {mensaje}")
            return {
                "id_cliente":               id_cliente,
                "direccion":                direccion,
                "localidad":                localidad,
                "provincia":                provincia,
                "original_address":         original_address,
                "latitude":                 None,
                "longitude":                None,
                "reverse_geocoded_address": None,
                "distance_meters":          None,
                "validation_status":        "ERROR",
                "error_message":            mensaje,
                "timestamp":                timestamp,
            }

        # Estado acumulado — se completa paso a paso.
        # Los valores None indican que ese paso aún no se alcanzó.
        estado = {
            "id_cliente":               id_cliente,
            "direccion":                direccion,
            "localidad":                localidad,
            "provincia":                provincia,
            "original_address":         original_address,
            "latitude":                 None,
            "longitude":                None,
            "reverse_geocoded_address": None,
            "distance_meters":          None,
            "validation_status":        "ERROR",
            "error_message":            None,
            "timestamp":                timestamp,
        }

        # ----------------------------------------------------------
        # PASO 1 — Geocoding: dirección original → lat1, lon1
        # ----------------------------------------------------------
        self._log(f"  [1/3] Geocoding: '{original_address[:60]}'")
        geo1 = geocodificar_google(original_address, logger=self._log)

        if geo1["error"]:
            # Fallo técnico antes de obtener cualquier coordenada.
            # latitude y longitude permanecen None.
            estado["error_message"] = f"Geocoding fallido: {geo1['error']}"
            self._log(f"  => ERROR: {estado['error_message']}")
            return estado

        lat1 = geo1["lat"]
        lon1 = geo1["lon"]

        # A partir de aquí hay coordenadas válidas.
        # Se persisten en el estado inmediatamente — si un paso posterior
        # falla, estas coordenadas quedan disponibles para análisis.
        estado["latitude"]  = lat1
        estado["longitude"] = lon1

        self._log(f"  => lat1={lat1:.6f}, lon1={lon1:.6f}")
        time.sleep(SLEEP_ENTRE_REQUESTS)

        # ----------------------------------------------------------
        # PASO 2 — Reverse geocoding: (lat1, lon1) → direccion_reverse
        # ----------------------------------------------------------
        self._log(f"  [2/3] Reverse geocoding: ({lat1:.6f}, {lon1:.6f})")
        rev = reverse_geocode_google(lat1, lon1, logger=self._log)

        if rev["error"]:
            # lat1/lon1 ya están persistidos en el estado.
            estado["error_message"] = f"Reverse geocoding fallido: {rev['error']}"
            self._log(f"  => ERROR: {estado['error_message']}")
            return estado

        direccion_reverse = rev["direccion"]
        estado["reverse_geocoded_address"] = direccion_reverse

        self._log(f"  => '{direccion_reverse}'")
        time.sleep(SLEEP_ENTRE_REQUESTS)

        # ----------------------------------------------------------
        # PASO 3 — Segundo geocoding: direccion_reverse → lat2, lon2
        # ----------------------------------------------------------
        self._log(f"  [3/3] Geocoding: '{direccion_reverse[:60]}'")
        geo2 = geocodificar_google(direccion_reverse, logger=self._log)

        if geo2["error"]:
            # lat1/lon1 y direccion_reverse ya están persistidos.
            estado["error_message"] = f"Segundo geocoding fallido: {geo2['error']}"
            self._log(f"  => ERROR: {estado['error_message']}")
            return estado

        lat2 = geo2["lat"]
        lon2 = geo2["lon"]

        self._log(f"  => lat2={lat2:.6f}, lon2={lon2:.6f}")
        time.sleep(SLEEP_ENTRE_REQUESTS)

        # ----------------------------------------------------------
        # PASO 4 — Validación Haversine: única fuente de verdad
        #
        # FAILED y SUCCESS no son errores técnicos.
        # El flujo funcionó correctamente en ambos casos.
        # Todas las coordenadas y datos intermedios se conservan siempre.
        # ----------------------------------------------------------
        validacion = validar_coordenadas(lat1, lon1, lat2, lon2)

        estado["distance_meters"]   = validacion["distance_meters"]
        estado["validation_status"] = validacion["validation_status"]
        estado["error_message"]     = validacion["error_message"]

        self._log(
            f"  => {validacion['validation_status']} | "
            f"distancia={validacion['distance_meters']}m"
            + (f" | {validacion['error_message']}" if validacion["error_message"] else "")
        )

        return estado

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------

    def _log(self, message):
        if self.log_callback:
            self.log_callback(message)
        else:
            print(message)

    def _actualizar_progreso(self, numero_fila, total_rows, id_cliente):
        if self.update_callback:
            progreso = (numero_fila - 1) / total_rows
            self.update_callback(
                progreso,
                f"Procesando {numero_fila}/{total_rows}: {id_cliente}"
            )

    def _actualizar_progreso_final(self):
        if self.update_callback:
            self.update_callback(1.0, "Finalizado")
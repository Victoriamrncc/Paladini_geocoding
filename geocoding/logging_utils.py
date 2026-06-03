import os
import pandas as pd
from datetime import datetime
from .config import LOGS_DIR, RESULTADOS_DIR


class ExecutionLogger:
    """
    Registra cada fila procesada y persiste dos artefactos al finalizar:

        logs/log_ejecucion_<timestamp>.csv
            — log detallado fila a fila, útil para debugging.

        resultados/resultado_geocodificacion_<timestamp>.csv
            — output final del procesamiento, columnas según spec.

        logs/logs_historicos.csv
            — registro acumulativo de ejecuciones (una fila por run).
    """

    def __init__(self, filename):
        self.filename  = filename
        self.timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

        self.log_file        = os.path.join(LOGS_DIR,       f"log_ejecucion_{self.timestamp}.csv")
        self.resultados_file = os.path.join(RESULTADOS_DIR, f"resultado_geocodificacion_{self.timestamp}.csv")
        self.historico_file  = os.path.join(LOGS_DIR,       "logs_historicos.csv")

        self._logs      = []
        self._resultados = []

        self.total_registros = 0
        self.procesados      = 0
        self.exitosos        = 0
        self.fallidos        = 0
        self._start_time     = datetime.now()

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def start(self, total_registros):
        """Marca el inicio del procesamiento y registra el total esperado."""
        self.total_registros = total_registros
        self._start_time     = datetime.now()

    def log_record(self, record):
        """
        Registra el resultado de una fila procesada.

        Parámetro `record` — dict con las siguientes claves:
            id_cliente              str
            original_address        str   — dirección tal como viene del CSV
            input_query             str   — query construida y enviada a la API
            latitude                float | None
            longitude               float | None
            reverse_geocoded_address str  | None
            distance_meters         float | None
            validation_status       "SUCCESS" | "FAILED" | "ERROR"
            error_message           str   | None
            timestamp               str   — ISO timestamp de la fila
        """
        self.procesados += 1

        if record.get("validation_status") == "SUCCESS":
            self.exitosos += 1
        else:
            self.fallidos += 1

        # --- Log detallado (debugging) ---
        self._logs.append({
            "timestamp":                record.get("timestamp"),
            "archivo":                  self.filename,
            "id_cliente":               record.get("id_cliente"),
            "nombre":                   record.get("nombre"),
            "rubro":                    record.get("rubro"),
            "sucursal_asignada":        record.get("sucursal_asignada"),
            "provincia":                record.get("provincia"),
            "original_address":         record.get("original_address"),
            "input_query":              record.get("input_query"),
            "latitude":                 record.get("latitude"),
            "longitude":                record.get("longitude"),
            "reverse_geocoded_address": record.get("reverse_geocoded_address"),
            "distance_meters":          record.get("distance_meters"),
            "validation_status":        record.get("validation_status"),
            "error_message":            record.get("error_message"),
        })

        # --- Resultado final (output del spec) ---
        self._resultados.append({
            "id_cliente":              record.get("id_cliente"),
            "nombre":                  record.get("nombre"),
            "rubro":                   record.get("rubro"),
            "sucursal_asignada":       record.get("sucursal_asignada"),
            "provincia":               record.get("provincia"),
            "original_address":        record.get("original_address"),
            "latitude":                record.get("latitude"),
            "longitude":               record.get("longitude"),
            "reverse_geocoded_address": record.get("reverse_geocoded_address"),
            "distance_meters":         record.get("distance_meters"),
            "validation_status":       record.get("validation_status"),
            "timestamp":               record.get("timestamp"),
            "error_message":           record.get("error_message"),
        })

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def end_and_save(self):
        """
        Persiste logs, resultados e histórico.
        Cada escritura es independiente: el fallo de un archivo no impide
        que los demás se escriban ni que el resumen se retorne.
        Retorna dict de resumen para consumo de processor.py y app.py.
        La clave 'errores_escritura' contiene una lista vacía si todo fue OK,
        o una lista de strings descriptivos por cada escritura fallida.
        """
        tiempo_total = round(
            (datetime.now() - self._start_time).total_seconds(), 2
        )
        errores_escritura = []

        # --- Log detallado (debugging) ---
        try:
            pd.DataFrame(self._logs).to_csv(self.log_file, index=False, encoding="utf-8")
        except Exception as e:
            errores_escritura.append(f"log_file: {type(e).__name__}: {e}")

        # --- Resultados finales ---
        try:
            pd.DataFrame(self._resultados).to_csv(
                self.resultados_file, index=False, encoding="utf-8"
            )
        except Exception as e:
            errores_escritura.append(f"resultados_file: {type(e).__name__}: {e}")

        # --- Histórico acumulativo ---
        resumen_hist = {
            "fecha_ejecucion":      self._start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "archivo":              self.filename,
            "registros_recibidos":  self.total_registros,
            "registros_procesados": self.procesados,
            "exitosos":             self.exitosos,
            "fallidos":             self.fallidos,
            "tiempo_segundos":      tiempo_total,
            "archivo_log":          self.log_file,
        }

        try:
            df_nuevo = pd.DataFrame([resumen_hist])
            if os.path.exists(self.historico_file):
                df_hist = pd.read_csv(self.historico_file)
                df_hist = pd.concat([df_hist, df_nuevo], ignore_index=True)
            else:
                df_hist = df_nuevo
            df_hist.to_csv(self.historico_file, index=False, encoding="utf-8")
        except Exception as e:
            errores_escritura.append(f"historico_file: {type(e).__name__}: {e}")

        # Resumen para processor.py y app.py
        return {
            "recibidos":         self.total_registros,
            "procesados":        self.procesados,
            "exitosos":          self.exitosos,
            "errores":           self.fallidos,
            "tiempo":            tiempo_total,
            "log_file":          self.log_file,
            "res_file":          self.resultados_file,
            "errores_escritura": errores_escritura,
        }
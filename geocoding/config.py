import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# ---------------------------------------------------------------------------
# Validación geográfica
# ---------------------------------------------------------------------------

# Distancia máxima aceptable entre (lat1, lon1) y (lat2, lon2) para considerar
# una dirección como válida. Fuente de verdad del sistema.
MAX_DISTANCE_METERS = 30
FAILED_MINIMO_MAX = 50
FAILED_MEDIO_MAX  = 100

# ---------------------------------------------------------------------------
# Red
# ---------------------------------------------------------------------------

MAX_INTENTOS         = 3   # Reintentos por request
SLEEP_ENTRE_REQUESTS = 1.0 # Segundos entre llamadas a la API
CHUNK_SIZE           = 300 # Máximo de filas por lote de procesamiento

# ---------------------------------------------------------------------------
# Rutas de salida
# ---------------------------------------------------------------------------

BASE_DIR       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR       = os.path.join(BASE_DIR, "logs")
RESULTADOS_DIR = os.path.join(BASE_DIR, "resultados")

os.makedirs(LOGS_DIR,       exist_ok=True)
os.makedirs(RESULTADOS_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

# Columnas que debe tener el CSV de entrada.
COLUMNAS_REQUERIDAS = {"ID_Cliente", "Dirección", "Localidad", "Provincia"}
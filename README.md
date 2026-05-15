# Validación de Direcciones con Google Geocoding

Herramienta web para verificar la precisión de direcciones de forma masiva, usando un proceso de geocoding cruzado con la API de Google Maps.

---

## Objetivo

Verificar la validez de un conjunto de direcciones cargadas desde un archivo CSV o Excel. Para garantizar que una dirección sea correcta, el sistema realiza una validación cruzada utilizando coordenadas geográficas.

---

## Flujo de validación

Cada dirección del archivo pasa por cuatro pasos antes de recibir su estado final.

**Paso 1 — Geocoding directo**
La dirección original (`Dirección`, `Localidad`, `Provincia`) se envía a la API para obtener coordenadas iniciales (`lat1`, `lon1`).

**Paso 2 — Reverse geocoding**
Con las coordenadas obtenidas, se consulta qué dirección registra Google en ese punto exacto (`dirección_reverse`).

**Paso 3 — Segundo geocoding directo**
La dirección inversa se geocodifica nuevamente para obtener un segundo par de coordenadas (`lat2`, `lon2`).

**Paso 4 — Validación Haversine**
Se calcula la distancia en metros entre (`lat1`, `lon1`) y (`lat2`, `lon2`). Si supera el umbral configurado, la dirección se marca como fallida.

---

## Estados de resultado

| Estado | Descripción |
|--------|-------------|
| `SUCCESS` | Flujo completo y distancia dentro del umbral permitido. |
| `FAILED` | Flujo completo, pero la distancia supera la tolerancia. La dirección puede ser ambigua o inexacta. |
| `ERROR` | Fallo técnico durante el proceso (timeout, API key inválida, campo vacío, límite de cuota, etc.). |

Los resultados `FAILED` conservan todas las coordenadas e información del flujo para facilitar el análisis posterior. Los resultados `ERROR` pueden tener coordenadas parciales o nulas según en qué paso ocurrió el fallo.

---

## Estructura del proyecto

```
.
├── app.py                  # Punto de entrada — interfaz Streamlit
└── geocoding/
    ├── processor.py        # Orquestador del flujo de validación
    ├── api_clients.py      # Llamadas a Google Geocoding (directo y reverse)
    ├── validation.py       # Fórmula de Haversine y clasificación de estados
    ├── config.py           # Configuración global (umbrales, rutas, timeouts)
    └── logging_utils.py    # Persistencia de resultados e historial
```

### `app.py`

Punto de entrada de la aplicación Streamlit. Define la interfaz gráfica con dos secciones:

- **Ejecución**: carga de archivos, logs en tiempo real, métricas y descarga de resultados.
- **Historial**: consulta de ejecuciones anteriores con filtros por estado.

### `geocoding/processor.py`

Orquesta el flujo completo de validación por cada fila del archivo. Maneja los tres estados posibles (`SUCCESS`, `FAILED`, `ERROR`), construye el estado de forma incremental por paso, y reporta el progreso a la UI mediante callbacks.

### `geocoding/api_clients.py`

Contiene las funciones de comunicación con Google Geocoding API (geocoding directo y reverse). Implementa reintentos automáticos con backoff exponencial y manejo de errores HTTP (429, timeout, errores de red).

### `geocoding/validation.py`

Implementa la fórmula de Haversine para calcular la distancia entre dos pares de coordenadas y determina el estado `SUCCESS` o `FAILED`. Para los resultados `FAILED`, asigna un nivel de severidad según la distancia: `error mínimo`, `error medio` o `error grave`.

### `geocoding/config.py`

Almacena la configuración global del sistema: umbral de distancia (`MAX_DISTANCE_METERS`), columnas requeridas del CSV, tiempos de espera entre requests, y rutas de los directorios de salida.

### `geocoding/logging_utils.py`

Persiste los resultados fila a fila durante el procesamiento y genera tres artefactos al finalizar:

- `logs/log_ejecucion_<timestamp>.csv` — log detallado para debugging.
- `resultados/resultado_geocodificacion_<timestamp>.csv` — output final del procesamiento.
- `logs/logs_historicos.csv` — registro acumulativo de todas las ejecuciones.

---

## Columnas del archivo de entrada

| Columna | Descripción | Obligatoria |
|---------|-------------|-------------|
| `ID_Cliente` | Identificador único del registro. Se usa para trazar resultados. | ✓ |
| `Dirección` | Calle y número del domicilio. | ✓ |
| `Localidad` | Ciudad o localidad correspondiente. | ✓ |
| `Provincia` | Provincia argentina. Se combina con las anteriores para construir la query de geocoding. | ✓ |

> Si alguno de estos campos está vacío en una fila, esa fila se registra automáticamente como `ERROR` indicando cuál o cuáles columnas faltan, sin realizar ninguna llamada a la API.

---

## Configuración

Crear un archivo `.env` en la raíz del proyecto con la siguiente variable:

```env
GOOGLE_API_KEY=tu_clave_de_api
```

Los parámetros ajustables en `config.py`:

| Parámetro | Descripción | Valor por defecto |
|-----------|-------------|-------------------|
| `MAX_DISTANCE_METERS` | Umbral de distancia para clasificar `SUCCESS` vs `FAILED`. | `30` m |
| `FAILED_MINIMO_MAX` | Límite superior de "error mínimo". | `50` m |
| `FAILED_MEDIO_MAX` | Límite superior de "error medio". Por encima es "error grave". | `100` m |
| `SLEEP_ENTRE_REQUESTS` | Pausa entre llamadas a la API (segundos). | `1.0` s |
| `MAX_INTENTOS` | Reintentos por request HTTP. | `3` |

---

## Instalación y uso

```bash
# Instalar dependencias
pip install -r requirements.txt

# Iniciar la aplicación
streamlit run app.py
```

1. Cargá un archivo CSV o Excel con las cuatro columnas obligatorias. ID_Cliente, Dirección, Localidad y Provincia.
2. Presioná **Procesar Archivo** y monitoreá el progreso en tiempo real.
3. Filtrá los resultados por estado (`SUCCESS` / `FAILED` / `ERROR`) y descargalos en CSV.
4. Consultá ejecuciones anteriores en la sección **Historial** del panel lateral.

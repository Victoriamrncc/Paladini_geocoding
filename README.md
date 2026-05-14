# Validación de Direcciones con Google Geocoding

Este proyecto es una aplicación web interactiva desarrollada con **Streamlit** que permite realizar la validación masiva de direcciones utilizando la **API de Google Maps (Geocoding)**.

##  Objetivo

El objetivo principal de la herramienta es verificar la precisión y validez de un conjunto de direcciones (cargadas a través de un archivo CSV o Excel). Para garantizar que una dirección sea correcta, el sistema realiza un proceso de validación cruzada utilizando las coordenadas geográficas de la misma.

##  Conceptos Clave y Flujo de Validación

El proceso de validación por cada fila de datos (dirección) sigue estos pasos:

1. **Geocoding Directo:** La dirección original ingresada (Dirección, Localidad, Provincia) se envía a Google Geocoding API para obtener sus coordenadas iniciales (`lat1`, `lon1`).
2. **Reverse Geocoding:** Utilizando las coordenadas obtenidas (`lat1`, `lon1`), se consulta nuevamente a la API para obtener la dirección que Google tiene registrada exactamente en ese punto (`direccion_reverse`).
3. **Segundo Geocoding Directo:** La dirección obtenida en el paso anterior (`direccion_reverse`) se envía de nuevo a la API para obtener un segundo par de coordenadas (`lat2`, `lon2`).
4. **Validación (Fórmula de Haversine):** Se calcula la distancia en metros entre el primer par de coordenadas (`lat1`, `lon1`) y el segundo par (`lat2`, `lon2`).

###  Estados de Resultado

Dependiendo del flujo de validación, cada dirección puede clasificarse en uno de tres estados:

- **SUCCESS:** El proceso se completó correctamente y la distancia entre ambas coordenadas es menor o igual al límite permitido (indicando alta precisión).
- **FAILED:** El proceso se completó correctamente, pero la distancia entre ambas coordenadas supera el límite de tolerancia (la dirección puede ser ambigua o inexacta).
- **ERROR:** Ocurrió un fallo técnico durante el proceso (ej. Timeout, error de API Key, límite de peticiones).

##  Contenido del Programa y Estructura

El proyecto está estructurado modularmente para separar la interfaz gráfica, la lógica de validación y la interacción con la API.

### `app.py`
Es el punto de entrada de la aplicación web y define la interfaz gráfica con Streamlit.
- **Sección de Ejecución:** Permite cargar archivos CSV/Excel, muestra logs de ejecución en tiempo real y visualiza los resultados (métricas, tabla y opción de descarga).
- **Sección de Historial:** Permite consultar ejecuciones anteriores, cargar los resultados y filtrar por el estado de validación.

### Directorio `geocoding/`
Contiene toda la lógica de negocio, validación y comunicación con APIs externas.

- **`processor.py`**: Es el cerebro de la validación. Orquesta el flujo de los 4 pasos para cada fila del archivo cargado. Maneja los estados y coordina los reportes de progreso hacia la interfaz (UI).
- **`api_clients.py`**: Contiene las funciones específicas para hacer las llamadas (requests) a la API de Google Geocoding (tanto directo como reverse).
- **`validation.py`**: Contiene la lógica matemática (como la Fórmula de Haversine) para calcular la distancia entre coordenadas y determinar si el estado es SUCCESS o FAILED.
- **`config.py`**: Almacena las configuraciones globales como el tiempo de espera entre requests (`SLEEP_ENTRE_REQUESTS`), las columnas requeridas en los archivos y las rutas de los directorios.
- **`logging_utils.py`**: Se encarga de persistir el progreso, registrar (loguear) errores y guardar el resumen de cada ejecución en archivos para poder acceder al Historial más tarde.

##  Uso del Programa

1. Iniciar la aplicación ejecutando `streamlit run app.py` en la terminal.
2. Cargar un archivo con las columnas obligatorias: `ID_Cliente`, `Dirección`, `Localidad` y `Provincia`.
3. Presionar "Procesar Archivo".
4. Ver el progreso y descargar los resultados filtrados una vez finalizado.

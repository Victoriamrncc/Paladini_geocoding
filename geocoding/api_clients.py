import requests
import time
from .config import MAX_INTENTOS, GOOGLE_API_KEY

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def get_with_retry(url, params=None, max_intentos=MAX_INTENTOS, timeout=10, logger=None):
    """
    GET con reintentos y backoff exponencial.
    Maneja: 429 (rate limit), timeouts y errores de red genéricos.
    Devuelve el objeto Response o None si todos los intentos fallan.
    """
    for intento in range(max_intentos):
        try:
            r = requests.get(url, params=params, timeout=timeout)

            if r.status_code == 429:
                espera = 5 * (intento + 1)
                if logger:
                    logger(f"[HTTP] Rate limit (429). Esperando {espera}s antes de reintentar...")
                time.sleep(espera)
                continue

            r.raise_for_status()
            return r

        except requests.exceptions.Timeout:
            if logger:
                logger(f"[HTTP] Timeout en intento {intento + 1}/{max_intentos}.")
            if intento < max_intentos - 1:
                time.sleep(2 ** intento)

        except requests.exceptions.RequestException as e:
            if logger:
                logger(f"[HTTP] Error de red en intento {intento + 1}/{max_intentos}: {e}")
            if intento < max_intentos - 1:
                time.sleep(2 ** intento)

    if logger:
        logger(f"[HTTP] Todos los intentos fallaron para: {url}")
    return None


# ---------------------------------------------------------------------------
# Google Geocoding
# ---------------------------------------------------------------------------

def geocodificar_google(query, logger=None):
    """
    Geocoding hacia adelante: dirección → (lat, lon).

    Retorna dict con:
        lat          float | None
        lon          float | None
        direccion    str   | None   — formatted_address de Google
        status       str          — status devuelto por la API
        error        str | None   — mensaje de error si corresponde
    """
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address":    query,
        "key":        GOOGLE_API_KEY,
        "region":     "ar",
        "language":   "es",
        "components": "country:AR",
    }

    vacio = {"lat": None, "lon": None, "direccion": None, "status": None, "error": None}

    r = get_with_retry(url, params=params, logger=logger)

    if r is None:
        vacio["error"] = "request_failed"
        return vacio

    try:
        data   = r.json()
        status = data.get("status")
        vacio["status"] = status

        if status == "OK":
            result = data["results"][0]
            return {
                "lat":       result["geometry"]["location"]["lat"],
                "lon":       result["geometry"]["location"]["lng"],
                "direccion": result["formatted_address"],
                "status":    status,
                "error":     None,
            }

        if status == "ZERO_RESULTS":
            vacio["error"] = "zero_results"
            return vacio

        if status == "REQUEST_DENIED":
            vacio["error"] = "invalid_api_key"
            if logger:
                logger("[Google Geocoding] REQUEST_DENIED — verificar API key.")
            return vacio

        if status == "OVER_QUERY_LIMIT":
            vacio["error"] = "quota_exceeded"
            if logger:
                logger("[Google Geocoding] OVER_QUERY_LIMIT — cuota de API excedida.")
            return vacio

        # Cualquier otro status inesperado
        vacio["error"] = f"unexpected_status:{status}"
        if logger:
            logger(f"[Google Geocoding] Status inesperado: {status}")
        return vacio

    except Exception as e:
        vacio["error"] = f"parse_error:{e}"
        if logger:
            logger(f"[Google Geocoding] Error al parsear respuesta: {e}")
        return vacio


# ---------------------------------------------------------------------------
# Google Reverse Geocoding
# ---------------------------------------------------------------------------

def reverse_geocode_google(lat, lon, logger=None):
    """
    Reverse geocoding: (lat, lon) → dirección formateada.

    Retorna dict con:
        direccion    str  | None   — formatted_address de Google
        status       str         — status devuelto por la API
        error        str | None  — mensaje de error si corresponde
    """
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "latlng":      f"{lat},{lon}",
        "key":         GOOGLE_API_KEY,
        "language":    "es",
        "result_type": "street_address|route",
    }

    vacio = {"direccion": None, "status": None, "error": None}

    r = get_with_retry(url, params=params, logger=logger)

    if r is None:
        vacio["error"] = "request_failed"
        return vacio

    try:
        data   = r.json()
        status = data.get("status")
        vacio["status"] = status

        if status == "OK" and data["results"]:
            return {
                "direccion": data["results"][0]["formatted_address"],
                "status":    status,
                "error":     None,
            }

        if status == "ZERO_RESULTS":
            vacio["error"] = "zero_results"
            return vacio

        if status == "REQUEST_DENIED":
            vacio["error"] = "invalid_api_key"
            if logger:
                logger("[Google Reverse] REQUEST_DENIED — verificar API key.")
            return vacio

        if status == "OVER_QUERY_LIMIT":
            vacio["error"] = "quota_exceeded"
            if logger:
                logger("[Google Reverse] OVER_QUERY_LIMIT — cuota de API excedida.")
            return vacio

        vacio["error"] = f"unexpected_status:{status}"
        if logger:
            logger(f"[Google Reverse] Status inesperado: {status}")
        return vacio

    except Exception as e:
        vacio["error"] = f"parse_error:{e}"
        if logger:
            logger(f"[Google Reverse] Error al parsear respuesta: {e}")
        return vacio
import math
from .config import MAX_DISTANCE_METERS


# ---------------------------------------------------------------------------
# Cálculo de distancia geográfica
# ---------------------------------------------------------------------------

def haversine(lat1, lon1, lat2, lon2):
    """
    Calcula la distancia en metros entre dos puntos geográficos
    usando la fórmula de Haversine.
    """
    R  = 6_371_000  # Radio de la Tierra en metros
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a  = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# Validación geográfica
# ---------------------------------------------------------------------------

def validar_coordenadas(lat1, lon1, lat2, lon2):
    """
    Fuente de verdad del sistema.

    Calcula la distancia Haversine entre (lat1, lon1) y (lat2, lon2)
    y la compara contra MAX_DISTANCE_METERS.

    Retorna dict con:
        validation_status   "SUCCESS" | "FAILED"
        distance_meters     float — distancia calculada, redondeada a 2 decimales
        error_message       str | None — solo presente si FAILED
    """
    distancia = round(haversine(lat1, lon1, lat2, lon2), 2)

    if distancia <= MAX_DISTANCE_METERS:
        return {
            "validation_status": "SUCCESS",
            "distance_meters":   distancia,
            "error_message":     None,
        }
    else:
        return {
            "validation_status": "FAILED",
            "distance_meters":   distancia,
            "error_message": (
                f"Distancia geográfica ({distancia:.1f}m) "
                f"supera el umbral permitido ({MAX_DISTANCE_METERS}m)."
            ),
        }
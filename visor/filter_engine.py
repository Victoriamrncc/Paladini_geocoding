"""
visor/filter_engine.py
----------------------
Generación y aplicación de filtros, desacoplada de Streamlit.

Responsabilidades:
- Detectar qué columnas del DataFrame son filtrables.
- Separar columnas "núcleo" (siempre presentes) de columnas "dinámicas"
  (provenientes de CSVs complementarios).
- Aplicar un diccionario de selecciones a un DataFrame y devolver el filtrado.

La capa de presentación (Streamlit) solo necesita llamar a:
  1. filter_engine.available_filters(df)   → qué columnas mostrar
  2. filter_engine.apply(df, selections)   → DataFrame filtrado
"""

from __future__ import annotations

import pandas as pd


# ---------------------------------------------------------------------------
# Columnas "núcleo" con nombre de etiqueta amigable
# ---------------------------------------------------------------------------

CORE_FILTER_COLUMNS: dict[str, str] = {
    "provincia":         "Provincia",
    "validation_status": "Estado",
    "rubro":             "Rubro",
}

# Columnas que nunca deben ofrecerse como filtros (técnicas / coordenadas)
NEVER_FILTER: set[str] = {
    # Coordenadas y geocodificación
    "latitude",
    "longitude",
    "original_address",
    "geocoded_address",
    "reverse_geocoded_address",
    "input_query",
    "lat_original",
    "lon_original",
    # Métricas numéricas continuas (no útiles como multiselect)
    "distance_meters",
    # Columnas técnicas / de diagnóstico
    "timestamp",
    "archivo",
    "error_message",
    # Identificadores de alta cardinalidad
    "id_cliente",
    "nombre",
}


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def available_filters(df: pd.DataFrame) -> dict[str, str]:
    """
    Devuelve un dict ordenado { columna: etiqueta } con todos los filtros
    disponibles para el DataFrame dado.

    - Primero aparecen las columnas núcleo (en orden fijo).
    - Luego las columnas dinámicas (en orden alfabético).
    - Se excluyen columnas sin variabilidad (un único valor único).
    """
    result: dict[str, str] = {}

    # 1. Columnas núcleo
    for col, label in CORE_FILTER_COLUMNS.items():
        if col in df.columns and _is_filterable(df, col):
            result[col] = label

    # 2. Columnas dinámicas
    dynamic_cols = sorted(
        c for c in df.columns
        if c not in CORE_FILTER_COLUMNS
        and c not in NEVER_FILTER
        and _is_filterable(df, c)
    )
    for col in dynamic_cols:
        result[col] = _humanize(col)

    return result


def apply(df: pd.DataFrame, selections: dict[str, list]) -> pd.DataFrame:
    """
    Aplica las selecciones al DataFrame.

    `selections` es un dict { columna: [valores_seleccionados] }.
    Si una columna no está en selections (o su lista está vacía) se ignora.
    """
    result = df.copy()
    for col, values in selections.items():
        if col in result.columns and values:
            result = result[result[col].isin(values)]
    return result


def unique_values(df: pd.DataFrame, col: str) -> list:
    """Devuelve los valores únicos ordenados de una columna."""
    if col not in df.columns:
        return []
    return sorted(df[col].dropna().unique().tolist())


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _is_filterable(df: pd.DataFrame, col: str) -> bool:
    """Una columna es filtrable si tiene al menos 1 valor no nulo."""
    return df[col].notna().any()


def _humanize(col: str) -> str:
    """Convierte snake_case / nombres técnicos a etiqueta legible."""
    return col.replace("_", " ").title()
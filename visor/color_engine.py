"""
visor/color_engine.py
---------------------
Asignación de colores por categoría, completamente desacoplada de Streamlit.

Responsabilidades:
- Mantener una paleta de colores compatibles con Folium.
- Mapear rubros conocidos a colores fijos.
- Asignar automáticamente colores a categorías nuevas sin repetir los ya usados.
- Gestionar mappings independientes por columna de coloreado.
- Garantizar que, dentro de una sesión, la misma categoría siempre recibe el mismo color.
- Exponer una leyenda lista para renderizar.
- Determinar qué columnas de un DataFrame son candidatas a colorear.

Los colores son strings válidos para folium.Icon(color=...).
Paleta completa soportada por Folium/Leaflet.awesome-markers:
  red, blue, green, purple, orange, darkred, lightred, beige,
  darkblue, darkgreen, cadetblue, darkpurple, white, pink,
  lightblue, lightgreen, gray, black, lightgray
"""

from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------------------
# Mapa de nombres Folium → hex CSS
# Usado por map_builder para CircleMarker (sin dependencia de CDN externo)
# ---------------------------------------------------------------------------

COLOR_HEX: dict[str, str] = {
    "red":        "#d63b3b",
    "blue":       "#4a90d9",
    "green":      "#3aad5b",
    "purple":     "#8b5db8",
    "orange":     "#e6820e",
    "darkred":    "#8b0000",
    "lightred":   "#f07070",
    "beige":      "#d9c89b",
    "darkblue":   "#00008b",
    "darkgreen":  "#006400",
    "cadetblue":  "#5f9ea0",
    "darkpurple": "#580058",
    "white":      "#f0f0f0",
    "pink":       "#f0a0c0",
    "lightblue":  "#add8e6",
    "lightgreen": "#90ee90",
    "gray":       "#888888",
    "black":      "#333333",
    "lightgray":  "#cccccc",
}

# ---------------------------------------------------------------------------
# Configuración base — rubros conocidos con colores fijos
# ---------------------------------------------------------------------------

BASE_RUBRO_COLORS: dict[str, str] = {
    "Socio Estratégico": "red",
    "Supermercado":      "blue",
    "Distribuidor":      "green",
    "Mayorista":         "orange",
    "Gran Cadena":       "darkred",
    "Almacén":           "cadetblue",
}

# Paleta de reserva para categorías desconocidas (en orden de preferencia)
_FALLBACK_PALETTE: list[str] = [
    "red",
    "blue",
    "green",
    "purple",
    "orange",
    "darkred",
    "cadetblue",
    "darkblue",
    "darkgreen",
    "darkpurple",
    "pink",
    "lightred",
    "lightblue",
    "lightgreen",
    "beige",
    "black",
    "lightgray",
]

# Icono único para todos los marcadores
MARKER_ICON = "circle"
ICON_PREFIX = "fa"

# ---------------------------------------------------------------------------
# Columnas que nunca deben ofrecerse como opción de coloreado
# ---------------------------------------------------------------------------

NEVER_COLOR_COLUMNS: set[str] = {
    "id_cliente",
    "nombre",
    "latitude",
    "longitude",
    "original_address",
    "formatted_address",
    "geocoded_address",
    "reverse_geocoded_address",
    "place_id",
    "input_query",
    "lat_original",
    "lon_original",
    "distance_meters",
    "timestamp",
    "archivo",
    "error_message",
    "validation_status",
}


# ---------------------------------------------------------------------------
# Función pública para obtener columnas coloreables
# ---------------------------------------------------------------------------

def colorable_columns(df: pd.DataFrame) -> list[str]:
    """
    Devuelve las columnas del DataFrame aptas para colorear marcadores.

    Criterios de inclusión:
    - No está en NEVER_COLOR_COLUMNS.
    - Es de tipo object/string o categórica (no puramente numérica).
    - Tiene al menos 1 valor no nulo y más de 1 valor único (variabilidad mínima).
    - Tiene a lo sumo 50 valores únicos (cardinalidad razonable para una leyenda).

    El orden devuelto coloca 'rubro' primero si existe, luego el resto
    en orden alfabético.
    """
    candidates: list[str] = []
    for col in df.columns:
        if col in NEVER_COLOR_COLUMNS:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        n_unique = df[col].nunique(dropna=True)
        if n_unique < 1 or n_unique > 50:
            continue
        candidates.append(col)

    # Ordenar: rubro primero, luego alfabético
    candidates.sort(key=lambda c: (0 if c == "rubro" else 1, c))
    return candidates


def default_color_column(df: pd.DataFrame) -> str | None:
    """
    Devuelve la columna de coloreado por defecto:
    'rubro' si existe en las coloreables, si no la primera disponible.
    """
    cols = colorable_columns(df)
    if not cols:
        return None
    if "rubro" in cols:
        return "rubro"
    return cols[0]


# ---------------------------------------------------------------------------
# _ColumnPalette — motor de colores para UNA columna
# ---------------------------------------------------------------------------

class _ColumnPalette:
    """
    Gestiona la asignación categoría → color para una sola columna.
    Usa colores base fijos cuando la columna es 'rubro'; de lo contrario
    asigna desde la paleta de fallback.
    """

    def __init__(self, col_name: str) -> None:
        self._col = col_name
        # Para la columna 'rubro' arrancamos con los colores base conocidos
        if col_name == "rubro":
            self._mapping: dict[str, str] = dict(BASE_RUBRO_COLORS)
            self._used: set[str] = set(BASE_RUBRO_COLORS.values())
        else:
            self._mapping = {}
            self._used = set()
        self._fallback_idx = 0

    def build(self, values: list[str]) -> None:
        """Pre-asigna colores a una lista de valores."""
        for v in values:
            self.color_for(v)

    def color_for(self, value: str) -> str:
        value = value.strip() if value else ""
        if not value:
            return "gray"
        if value not in self._mapping:
            self._mapping[value] = self._next_color()
        return self._mapping[value]

    def legend(self) -> list[tuple[str, str]]:
        """Devuelve lista de (categoría, color) para los valores asignados."""
        return list(self._mapping.items())

    def _next_color(self) -> str:
        while self._fallback_idx < len(_FALLBACK_PALETTE):
            candidate = _FALLBACK_PALETTE[self._fallback_idx]
            self._fallback_idx += 1
            if candidate not in self._used:
                self._used.add(candidate)
                return candidate
        return "gray"


# ---------------------------------------------------------------------------
# ColorEngine — API pública
# ---------------------------------------------------------------------------

class ColorEngine:
    """
    Motor de colores multi-columna.

    Mantiene una paleta independiente por columna de coloreado, lo que
    garantiza consistencia dentro de la sesión independientemente de los filtros.

    Uso típico:
        engine = ColorEngine()
        engine.build(df, "rubro")
        engine.build(df, "vendedor")

        color = engine.color_for("rubro", "Supermercado")
        legend = engine.legend("vendedor")
    """

    def __init__(self) -> None:
        self._palettes: dict[str, _ColumnPalette] = {}

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def build(self, df: pd.DataFrame, col: str) -> None:
        """
        Pre-asigna colores a todos los valores únicos de `col` en `df`.
        Si la paleta de esa columna ya existe, solo agrega valores nuevos.
        """
        if col not in df.columns:
            return
        palette = self._get_palette(col)
        values = df[col].dropna().unique().tolist()
        palette.build([str(v) for v in values])

    def color_for(self, col: str, value: str) -> str:
        """Devuelve el color para `value` dentro de la columna `col`."""
        return self._get_palette(col).color_for(value)

    def legend(self, col: str) -> list[tuple[str, str]]:
        """
        Devuelve lista de (categoría, color) para la columna dada.
        Solo incluye los valores que fueron consultados al menos una vez.
        """
        if col not in self._palettes:
            return []
        return self._palettes[col].legend()

    def icon_kwargs(self, col: str, value: str) -> dict:
        """Devuelve los kwargs listos para folium.Icon."""
        return {
            "color":  self.color_for(col, value),
            "icon":   MARKER_ICON,
            "prefix": ICON_PREFIX,
        }

    # ------------------------------------------------------------------
    # Compatibilidad hacia atrás (API anterior usaba rubro directamente)
    # ------------------------------------------------------------------

    def color_for_rubro(self, rubro: str) -> str:
        """Alias de compatibilidad: colorea por la columna 'rubro'."""
        return self.color_for("rubro", rubro)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _get_palette(self, col: str) -> _ColumnPalette:
        if col not in self._palettes:
            self._palettes[col] = _ColumnPalette(col)
        return self._palettes[col]
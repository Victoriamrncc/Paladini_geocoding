"""
visor/map_builder.py
--------------------
Construcción del mapa Folium, desacoplada de Streamlit.

Responsabilidades:
- Recibir un DataFrame ya filtrado, un ColorEngine y la columna de coloreado.
- Construir y devolver el objeto folium.Map.
- Agregar marcadores con popup enriquecido.

No importa nada de Streamlit. La capa UI solo llama a build_map() y
luego pasa el objeto a st_folium().
"""

from __future__ import annotations

import folium
import pandas as pd

from .color_engine import ColorEngine


def build_map(
    df: pd.DataFrame,
    color_engine: ColorEngine,
    color_col: str = "rubro",
) -> folium.Map:
    """
    Construye un folium.Map con un marcador por fila de `df`.

    Parámetros
    ----------
    df : DataFrame filtrado con columnas latitude, longitude y opcionalmente
         rubro, nombre, id_cliente, sucursal_asignada, original_address.
    color_engine : instancia ya inicializada de ColorEngine.
    color_col : columna del DataFrame que determina el color del marcador.
                Por defecto 'rubro' (comportamiento original).

    Retorna
    -------
    folium.Map listo para pasar a st_folium().
    """
    centro_lat = df["latitude"].mean()
    centro_lon = df["longitude"].mean()

    mapa = folium.Map(
        location=[centro_lat, centro_lon],
        zoom_start=10,
        tiles="CartoDB positron",
    )

    for _, row in df.iterrows():
        # Valor de la columna de coloreado
        color_value = _get(row, color_col, "")

        # Campos del popup (siempre desde las columnas estándar)
        rubro    = _get(row, "rubro", "")
        nombre   = _get(row, "nombre", "—")
        id_cli   = _get(row, "id_cliente", "—")
        sucursal = _get(row, "sucursal_asignada", "—")
        direccion= _get(row, "original_address", "—")

        # Si la columna de coloreado no es rubro, mostramos ambos en el popup
        extra_line = ""
        if color_col != "rubro" and color_value:
            col_label = color_col.replace("_", " ").title()
            extra_line = f"🎨 <b>{col_label}:</b> {color_value}<br>"

        popup_html = (
            f'<div style="font-family:sans-serif;font-size:13px;min-width:200px;">'
            f"<b>{nombre}</b><br>"
            f'<span style="color:#666">ID: {id_cli}</span><br>'
            f'<hr style="margin:4px 0">'
            f"{extra_line}"
            f"🏷️ {rubro or '—'}<br>"
            f"🏢 {sucursal}<br>"
            f"📍 {direccion}"
            f"</div>"
        )

        folium.Marker(
            location=[row["latitude"], row["longitude"]],
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=nombre,
            icon=folium.Icon(**color_engine.icon_kwargs(color_col, color_value)),
        ).add_to(mapa)

    return mapa


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get(row: pd.Series, col: str, default: str) -> str:
    val = row.get(col)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return str(val)
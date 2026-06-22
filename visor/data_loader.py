"""
visor/data_loader.py
--------------------
Lógica de carga y merge de CSVs, completamente desacoplada de Streamlit.

Responsabilidades:
- Leer el CSV principal.
- Leer N CSV complementarios.
- Realizar left-joins sobre `id_cliente`.
- Exponer el DataFrame consolidado y la lista de columnas extra.
"""

from __future__ import annotations

import io
import pandas as pd
from typing import Sequence


# Columna de join — podría parametrizarse en el futuro
JOIN_KEY = "id_cliente"

# Columnas que siempre existen en el CSV principal (no se tratan como "extras")
CORE_COLUMNS = {
    JOIN_KEY,
    "latitude",
    "longitude",
    "validation_status",
    "provincia",
    "rubro",
    "nombre",
    "sucursal_asignada",
    "original_address",
}


def load_main(source: str | io.IOBase) -> pd.DataFrame:
    """Carga el CSV principal y normaliza nombres de columna a minúsculas."""
    df = pd.read_csv(source, sep=None, engine="python")
    df.columns = [c.strip().lower() for c in df.columns]
    return df


def load_complement(source: str | io.IOBase) -> pd.DataFrame:
    """Carga un CSV complementario y normaliza nombres."""
    df = pd.read_csv(source, sep=None, engine="python")
    df.columns = [c.strip().lower() for c in df.columns]
    return df


def merge_complements(
    df_main: pd.DataFrame,
    complements: Sequence[pd.DataFrame],
) -> pd.DataFrame:
    """
    Realiza left-joins sucesivos entre el CSV principal y cada complementario.

    - La clave de join es JOIN_KEY.
    - Si un complementario no tiene JOIN_KEY se ignora (con advertencia).
    - Las columnas duplicadas (excepto la clave) se sufijan con `_dup_N`
      para evitar colisiones silenciosas.
    """
    result = df_main.copy()

    for i, comp in enumerate(complements):
        if JOIN_KEY not in comp.columns:
            # No se puede hacer join; registrar y continuar
            print(
                f"[data_loader] Complementario #{i+1} no tiene columna "
                f"'{JOIN_KEY}' — ignorado."
            )
            continue

        # Detectar colisiones de columnas (distintas a la clave)
        cols_comp = [c for c in comp.columns if c != JOIN_KEY]
        cols_result = set(result.columns)
        renames = {
            c: f"{c}_dup_{i+1}" for c in cols_comp if c in cols_result
        }
        comp_renamed = comp.rename(columns=renames)

        result = result.merge(comp_renamed, on=JOIN_KEY, how="left")

    return result


def extra_columns(df: pd.DataFrame) -> list[str]:
    """
    Devuelve la lista de columnas que NO forman parte del núcleo,
    candidatas a convertirse en filtros dinámicos.
    """
    return [c for c in df.columns if c not in CORE_COLUMNS]

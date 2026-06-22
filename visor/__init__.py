"""Paquete visor — lógica desacoplada de Streamlit."""
from .data_loader import load_main, load_complement, merge_complements, extra_columns
from .color_engine import ColorEngine, BASE_RUBRO_COLORS, colorable_columns, default_color_column
from .filter_engine import available_filters, apply, unique_values
from .map_builder import build_map

__all__ = [
    "load_main",
    "load_complement",
    "merge_complements",
    "extra_columns",
    "ColorEngine",
    "BASE_RUBRO_COLORS",
    "colorable_columns",
    "default_color_column",
    "available_filters",
    "apply",
    "unique_values",
    "build_map",
]
"""Consulta de la tabla de planteles avícolas SIMULADOS (corpus/datos/planteles_mock.csv)."""

import unicodedata
from pathlib import Path

import pandas as pd

# vet_agent/datos/planteles.py -> raíz del repo -> corpus/datos/planteles_mock.csv
RUTA_CSV = Path(__file__).resolve().parents[2] / "corpus" / "datos" / "planteles_mock.csv"


def _normalizar(texto):
    """Minúsculas, sin tildes y sin espacios sobrantes: 'Valparaíso ' -> 'valparaiso'."""
    texto = unicodedata.normalize("NFD", str(texto).strip().lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def buscar_planteles(region=None, comuna=None):
    """Devuelve un DataFrame con los planteles que coinciden con la región y/o comuna.

    Si no se entrega ningún filtro, devuelve todos los planteles.
    La comparación ignora mayúsculas y tildes.
    """
    df = pd.read_csv(RUTA_CSV, encoding="utf-8")

    if region is not None:
        df = df[df["region"].map(_normalizar) == _normalizar(region)]
    if comuna is not None:
        df = df[df["comuna"].map(_normalizar) == _normalizar(comuna)]

    return df.reset_index(drop=True)

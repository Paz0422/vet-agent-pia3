"""Cabecera de metadatos al inicio de cada .md (formato YAML simple).

Ejemplo:
    ---
    id: "D23"
    titulo: "Comparison of whole blood on filter strips..."
    anio: "2025"
    ---
    # Texto del documento...

Cada valor se escribe entre comillas (json), asi los dos puntos o comillas
dentro de un titulo no rompen nada. La leen descargar_md, validar e indexar_md.
"""
import json
import pathlib

SEPARADOR = "---"


def escribir(meta: dict, cuerpo: str) -> str:
    lineas = [SEPARADOR] + [f"{k}: {json.dumps(str(v), ensure_ascii=False)}" for k, v in meta.items()] + [SEPARADOR]
    return "\n".join(lineas) + "\n\n" + cuerpo.strip() + "\n"


def separar(texto: str) -> tuple[dict, str]:
    """(metadatos, cuerpo). Si el archivo no tiene cabecera, metadatos queda vacio."""
    if not texto.startswith(SEPARADOR + "\n"):
        return {}, texto
    fin = texto.find("\n" + SEPARADOR + "\n", len(SEPARADOR))
    if fin < 0:
        return {}, texto
    meta = {}
    for linea in texto[len(SEPARADOR) + 1:fin].splitlines():
        clave, _, valor = linea.partition(": ")
        try:
            meta[clave.strip()] = json.loads(valor)
        except ValueError:
            meta[clave.strip()] = valor.strip()
    return meta, texto[fin + len(SEPARADOR) + 2:].lstrip("\n")


def leer(ruta: pathlib.Path) -> tuple[dict, str]:
    return separar(ruta.read_text(encoding="utf-8"))

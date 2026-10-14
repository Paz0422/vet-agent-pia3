# Tarea 3: cortar(texto) -> lista de pedazos de ~1200 caracteres
#
# 

import re

MAX_CHARS = 1200   # tamano aproximado de cada pedazo
SOLAPE = 200       # cuanto se repite del pedazo anterior


def _por_palabras(texto: str, max_chars: int) -> list[str]:
    """Parte un texto largo por palabras para no cortar ninguna a la mitad.

    Si aparece una palabra sola mas larga que max_chars (una URL, una cadena
    sin espacios), se corta a la fuerza para respetar el limite.
    """
    partes, actual = [], ""
    for palabra in texto.split():
        if len(palabra) > max_chars:
            if actual:
                partes.append(actual)
                actual = ""
            for i in range(0, len(palabra), max_chars):
                partes.append(palabra[i:i + max_chars])
            continue
        if actual and len(actual) + 1 + len(palabra) > max_chars:
            partes.append(actual)
            actual = palabra
        else:
            actual = (actual + " " + palabra).strip()
    if actual:
        partes.append(actual)
    return partes


def _oraciones(parrafo: str) -> list[str]:
    """Divide un parrafo tras . ! ? seguido de espacio, conservando el signo."""
    return [o.strip() for o in re.split(r"(?<=[.!?])\s+", parrafo) if o.strip()]


def _unidades(texto: str, max_chars: int):
    """Genera bloques que por si solos caben en max_chars.

    Primero por parrafos, luego por oraciones y, si una oracion sigue siendo
    muy larga (tablas, listas sin puntos), por palabras. Asi nunca se emite
    una unidad mayor que max_chars.
    """
    for parrafo in texto.split("\n\n"):
        parrafo = parrafo.strip()
        if not parrafo:
            continue
        if len(parrafo) <= max_chars:
            yield parrafo
            continue
        for oracion in _oraciones(parrafo):
            if len(oracion) <= max_chars:
                yield oracion
            else:
                yield from _por_palabras(oracion, max_chars)


def cortar(texto: str, max_chars: int = MAX_CHARS, solape: int = SOLAPE) -> list[str]:
    """Recibe un texto largo y devuelve una lista de pedazos.

    Cada pedazo mide como maximo max_chars + solape (1200 + 200 por defecto).
    Ningun pedazo queda vacio y el inicio de cada uno repite el final del
    anterior para no perder el contexto que cae justo en el borde.
    """
    pedazos, actual = [], ""
    for unidad in _unidades(texto, max_chars):
        if actual and len(actual) + len(unidad) + 2 > max_chars:
            pedazos.append(actual.strip())
            actual = actual[-solape:] if solape > 0 else ""
        actual = (actual + "\n\n" + unidad).strip()
    if actual.strip():
        pedazos.append(actual.strip())
    return pedazos


EJEMPLO = (
    "La influenza aviar es una enfermedad viral que afecta a las aves. "
    "El diagnostico se confirma mediante RT-PCR en tiempo real. "
    "Las muestras se toman de la traquea y la cloaca.\n\n"
    "El laboratorio debe validar cada ensayo antes de usarlo en rutina. "
    "Un resultado positivo se confirma con una segunda tecnica.\n\n"
) * 40


if __name__ == "__main__":
    import pathlib

    # prueba.txt en la carpeta actual o en la raiz del repo; si no existe,
    # se usa un texto de ejemplo para que el script siempre corra
    raiz = pathlib.Path(__file__).resolve().parents[2]
    archivo = pathlib.Path("prueba.txt")
    if not archivo.exists():
        archivo = raiz / "prueba.txt"
    if archivo.exists():
        texto = archivo.read_text(encoding="utf-8")
        print("Leyendo", archivo)
    else:
        texto = EJEMPLO
        print("No hay prueba.txt; se usa un texto de ejemplo.")
    pedazos = cortar(texto)
    print(len(pedazos), "pedazos")
    for i, c in enumerate(pedazos[:3]):
        print(f"\n--- pedazo {i} ({len(c)} caracteres) ---\n{c}")

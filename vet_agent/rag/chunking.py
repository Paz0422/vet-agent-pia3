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


if __name__ == "__main__":
    import pathlib
    import sys

    raiz = pathlib.Path(__file__).resolve().parents[2]
    # Preferir primero un archivo prueba.txt en la raiz o carpeta actual
    candidatos = []
    candidatos.append(pathlib.Path("prueba.txt"))
    candidatos.append(raiz / "prueba.txt")
    # Si no hay prueba.txt, buscar el primer .txt de corpus/txt
    for c in candidatos:
        if c.exists():
            archivo = c
            break
    else:
        # buscar corpus/txt/D*.txt
        corpus = raiz / "corpus" / "txt"
        if corpus.exists():
            archivos = sorted(corpus.glob("D*.txt"))
            if archivos:
                archivo = archivos[0]
            else:
                print("No hay prueba.txt ni archivos en corpus/txt. Generalos con: python -m vet_agent.rag.extraer")
                sys.exit(1)
        else:
            print("No hay prueba.txt ni corpus/txt. Genera los .txt con: python -m vet_agent.rag.extraer")
            sys.exit(1)

    texto = archivo.read_text(encoding="utf-8")
    print("Leyendo", archivo)
    pedazos = cortar(texto)
    print(len(pedazos), "pedazos")
    for i, c in enumerate(pedazos[:3]):
        print(f"\n--- pedazo {i} ({len(c)} caracteres) ---\n{c}")

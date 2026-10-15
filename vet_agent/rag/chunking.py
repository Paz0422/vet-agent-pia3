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


def _ajustar_solape(tail: str) -> str:
    """Recorta el solape para que empiece con una palabra completa.

    Elimina cualquier token parcial al inicio del trozo de solape
    (retrocede hasta el primer espacio/salto de linea).
    """
    if not tail:
        return ""
    # Quitar espacios al inicio
    inicio = 0
    for i, c in enumerate(tail):
        if c.isspace():
            inicio = i + 1
        else:
            break
    cola = tail[inicio:]
    if not cola:
        return ""
    # Si empieza con caracter no espacio, puede ser palabra parcial
    # Buscar primer espacio desde la izquierda
    idx_esp = cola.find(" ")
    idx_nl = cola.find("\n")
    idx = min(idx_esp if idx_esp != -1 else len(cola),
              idx_nl if idx_nl != -1 else len(cola))
    if idx_esp == -1 and idx_nl == -1:
        # No hay espacios: token completo o muy largo; mejor no repetir fragmento partido
        return ""
    # Corta antes del primer espacio/salto para empezar en palabra completa
    return cola[idx + 1 :] if idx < len(cola) else ""


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
            if solape > 0 and len(actual) > solape:
                cola = actual[-solape:]
                cola = _ajustar_solape(cola)
                actual = cola
            else:
                actual = ""
        actual = (actual + "\n\n" + unidad).strip()
    if actual.strip():
        pedazos.append(actual.strip())
    return pedazos


RE_TITULO = re.compile(r"^#{1,6} ", re.MULTILINE)   # linea que empieza con # a ######


def _secciones_md(texto: str) -> list[str]:
    """Divide el Markdown en secciones: cada una empieza en un titulo (#)."""
    inicios = [m.start() for m in RE_TITULO.finditer(texto)]
    if not inicios or inicios[0] != 0:
        inicios = [0] + inicios                      # texto antes del primer titulo
    fines = inicios[1:] + [len(texto)]
    return [texto[a:b].strip() for a, b in zip(inicios, fines) if texto[a:b].strip()]


def cortar_md(texto: str, max_chars: int = MAX_CHARS, solape: int = SOLAPE) -> list[str]:
    """Como cortar(), pero corta preferentemente en los titulos del Markdown.

    - Las secciones chicas se juntan hasta llegar a ~max_chars.
    - Una seccion mas grande que max_chars se corta con cortar(), y cada
      pedazo (menos el primero) repite el titulo para no perder el contexto.
    - Entre secciones no hay solape: el titulo ya es un corte natural.
    """
    pedazos, actual = [], ""
    for seccion in _secciones_md(texto):
        if len(seccion) > max_chars:                 # seccion grande: se corta por dentro
            if actual:
                pedazos.append(actual)
                actual = ""
            titulo = seccion.split("\n", 1)[0] if seccion.startswith("#") else ""
            partes = cortar(seccion, max_chars, solape)
            pedazos.append(partes[0])
            pedazos += [f"{titulo}\n\n{p}" if titulo else p for p in partes[1:]]
            continue
        if actual and len(actual) + 2 + len(seccion) > max_chars:
            pedazos.append(actual)                   # la seccion no cabe: pedazo nuevo
            actual = ""
        actual = (actual + "\n\n" + seccion).strip()  # secciones chicas se juntan
    if actual:
        pedazos.append(actual)
    return pedazos


if __name__ == "__main__":
    import pathlib
    import sys

    raiz = pathlib.Path(__file__).resolve().parents[2]
    # Prueba rapida con el primer .md de corpus/md/diagnostico (sin su cabecera)
    archivos = sorted((raiz / "corpus" / "md" / "diagnostico").glob("D*.md"))
    if not archivos:
        print("No hay .md en corpus/md/diagnostico. Generalos con: python -m vet_agent.rag.pdf_local")
        sys.exit(1)
    archivo = archivos[0]

    from vet_agent.rag import cabecera
    _, texto = cabecera.leer(archivo)
    print("Leyendo", archivo)
    pedazos = cortar_md(texto)
    print(len(pedazos), "pedazos")
    for i, c in enumerate(pedazos[:3]):
        print(f"\n--- pedazo {i} ({len(c)} caracteres) ---\n{c}")

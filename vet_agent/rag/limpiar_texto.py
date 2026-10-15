"""Etapa 2 - Limpieza del contenido de los .md (antes de validar e indexar).

Uso (raiz del repo, despues de descargar_md):
    python -m vet_agent.rag.limpiar_texto

Limpia el cuerpo de cada corpus/md/<coleccion>/<id>.md (la cabecera de
metadatos no se toca) y lo marca en la cabecera, asi no se limpia dos veces:
- bibliografia final (References, Referencias, Bibliography, Literature cited),
- imagenes y sus marcadores (![...](...), "==> picture ... <=="),
- encabezados y pies de pagina repetidos y numeros de pagina sueltos,
- lineas "Downloaded from...", "(c) 20XX...", "This article is licensed under...",
- avisos de cookies, menus y textos de navegacion de las paginas web,
- espacios y saltos de linea repetidos.
Se mantienen los titulos de seccion y las tablas.

Deja en corpus/md/limpieza.csv las palabras antes y despues de cada documento
y avisa si alguno perdio mas del 50 % (puede ser un error de limpieza).
"""
import csv
import pathlib
import re
import time
from collections import Counter

from vet_agent.rag import cabecera

RAIZ = pathlib.Path(__file__).resolve().parents[2]
CARPETA = RAIZ / "corpus" / "md"
COLECCIONES = ("climatico", "diagnostico", "bioseguridad")
REGISTRO = CARPETA / "limpieza.csv"
AVISO_PERDIDA = 0.5          # avisar si un documento pierde mas que esto

RE_IMAGEN = re.compile(r"!\[[^\]]*\]\([^)]*\)")
RE_MARCA_IMAGEN = re.compile(r"^\W*(==>\s*picture\b.*<==|-+\s*(start|end) of picture text\s*-+)\W*$", re.IGNORECASE)
RE_BIBLIOGRAFIA = re.compile(
    r"^(#{1,6}\s*|\*\*\s*)?(\d+\.?\s*)?(references?|referencias( bibliogr[áa]ficas)?|bibliograf[íi]a|bibliography|"
    r"literature cited|works cited|reference list|cited literature)\s*(\*\*)?\s*:?\s*$", re.IGNORECASE)
RE_NUMERO_PAGINA = re.compile(r"^\W*((page|p[áa]g(ina)?\.?)\s*)?\d{1,4}(\s*(of|de|/)\s*\d{1,4})?\W*$", re.IGNORECASE)
RE_LEGAL = re.compile(
    r"downloaded from|©\s*(19|20)\d\d|\bcopyright\b.{0,10}(19|20)\d\d|this article is licensed under|"
    r"all rights reserved|todos los derechos reservados", re.IGNORECASE)
RE_NAVEGACION = re.compile(
    r"\b(cookies?|accept all|reject all|aceptar (todas|todo)|rechazar todas|cookie (settings|policy|preferences)|"
    r"privacy policy|pol[íi]tica de privacidad|skip to (main )?content|ir al contenido|sign in|log ?in|"
    r"iniciar sesi[óo]n|subscribe|suscr[íi]bete|newsletter|follow us|s[íi]guenos|share (this|on)|compartir en)\b",
    re.IGNORECASE)

MAX_PALABRAS_LEGAL = 30      # solo se borran lineas cortas: una oracion larga con "copyright" se queda
MAX_PALABRAS_NAVEGACION = 12
MAX_PALABRAS_REPETIDA = 20   # encabezados/pies: lineas cortas que se repiten en muchas paginas


def _es_estructura(linea: str) -> bool:
    """Titulos y filas de tabla nunca se borran por las reglas de lineas sueltas."""
    s = linea.lstrip()
    return s.startswith("#") or s.startswith("|")


def quitar_bibliografia(lineas: list[str]) -> list[str]:
    """Borra desde el ultimo titulo "References" (en la segunda mitad) hasta el siguiente titulo."""
    candidatos = [i for i, l in enumerate(lineas) if RE_BIBLIOGRAFIA.match(l.strip()) and i >= len(lineas) // 2]
    if not candidatos:
        return lineas
    i = candidatos[-1]
    fin = next((j for j in range(i + 1, len(lineas)) if lineas[j].lstrip().startswith("#")), len(lineas))
    return lineas[:i] + lineas[fin:]


def quitar_repetidas(lineas: list[str], paginas: int) -> list[str]:
    """Encabezados y pies de pagina: lineas cortas identicas que se repiten en muchas paginas."""
    conteo = Counter(l.strip() for l in lineas if l.strip() and not _es_estructura(l)
                     and len(l.split()) <= MAX_PALABRAS_REPETIDA)
    minimo = max(3, int(paginas * 0.3)) if paginas else 4
    repetidas = {l for l, n in conteo.items() if n >= minimo}
    return [l for l in lineas if l.strip() not in repetidas]


def limpiar(cuerpo: str, paginas: int = 0) -> str:
    """El cuerpo de un .md, limpio. paginas > 0 si viene de un PDF."""
    cuerpo = RE_IMAGEN.sub("", cuerpo)
    lineas = cuerpo.splitlines()
    lineas = quitar_bibliografia(lineas)
    lineas = quitar_repetidas(lineas, paginas)
    limpias = []
    for l in lineas:
        s, palabras = l.strip(), len(l.split())
        if not _es_estructura(l) and s:
            if RE_MARCA_IMAGEN.match(s) or RE_NUMERO_PAGINA.match(s):
                continue
            if palabras <= MAX_PALABRAS_LEGAL and RE_LEGAL.search(s):
                continue
            if palabras <= MAX_PALABRAS_NAVEGACION and RE_NAVEGACION.search(s):
                continue
        if not s.startswith("|"):  # en las tablas se respetan los espacios de alineacion
            l = re.sub(r"[ \t]+", " ", l).rstrip()
        limpias.append(l)
    texto = "\n".join(limpias)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def main() -> None:
    filas = []
    for col in COLECCIONES:
        for ruta in sorted((CARPETA / col).glob("*.md")):
            meta, cuerpo = cabecera.leer(ruta)
            if "limpieza" not in meta:
                antes = len(cuerpo.split())
                cuerpo = limpiar(cuerpo, int(meta.get("paginas") or 0))
                meta.update({"palabras_antes": antes, "palabras_despues": len(cuerpo.split()),
                             "limpieza": time.strftime("%Y-%m-%d")})
                ruta.write_text(cabecera.escribir(meta, cuerpo), encoding="utf-8")
            antes, despues = int(meta["palabras_antes"]), int(meta["palabras_despues"])
            perdida = 1 - despues / antes if antes else 0
            filas.append({"id": ruta.stem, "coleccion": col, "ruta": meta.get("ruta", ""), "palabras_antes": antes,
                          "palabras_despues": despues, "perdida": f"{perdida:.0%}"})

    with open(REGISTRO, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "coleccion", "ruta", "palabras_antes", "palabras_despues", "perdida"])
        w.writeheader()
        w.writerows(filas)
    total_antes = sum(f["palabras_antes"] for f in filas)
    total_despues = sum(f["palabras_despues"] for f in filas)
    print(f"{len(filas)} documentos | palabras: {total_antes:,} -> {total_despues:,} "
          f"({1 - total_despues / max(total_antes, 1):.0%} menos) | registro en {REGISTRO}")
    grandes = [f for f in filas if f["palabras_antes"] and 1 - f["palabras_despues"] / f["palabras_antes"] > AVISO_PERDIDA]
    if grandes:
        print(f"AVISO: {len(grandes)} perdieron más del {AVISO_PERDIDA:.0%} (revisar si es un error de limpieza):")
        for f in grandes:
            print(f"   {f['id']:9} {f['coleccion']:12} {f['ruta']:20} {f['palabras_antes']:>6} -> {f['palabras_despues']:>6} ({f['perdida']})")


if __name__ == "__main__":
    main()

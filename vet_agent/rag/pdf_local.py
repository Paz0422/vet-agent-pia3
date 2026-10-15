"""PDF que ya estan en el repo (corpus/D/) -> Markdown, sin internet.

Uso (raiz del repo):
    python -m vet_agent.rag.pdf_local                          # corpus/D -> corpus/md/diagnostico
    python -m vet_agent.rag.pdf_local --eje D --coleccion diagnostico

Reemplaza al antiguo pipeline .txt (extraer.py + indexar.py): convierte cada
PDF con pymupdf4llm (conserva titulos y tablas), aplica los arreglos de
corpus/arreglos.json (palabras rotas, menus, etc.) y lo guarda como
corpus/md/<coleccion>/<id>.md con cabecera de metadatos (de metadatos.csv).

No pisa un documento que ya este "OK" en la validacion (por ejemplo, porque
llego mas limpio desde Europe PMC); los demas se convierten o se reemplazan.
Despues hay que correr limpiar_texto, validar e indexar_md, como con la descarga.
"""
import argparse
import json
import pathlib
import time

import pandas as pd
import pymupdf
import pymupdf4llm

from vet_agent.rag import cabecera

RAIZ = pathlib.Path(__file__).resolve().parents[2]
CORPUS = RAIZ / "corpus"
REPORTE = CORPUS / "md" / "reporte_validacion.xlsx"
RUTA_LOCAL = "e) PDF local"


def _posicion(lineas: list[str], frase: str, desde: int = 0) -> int:
    """Numero de la primera linea (desde `desde`) que contiene `frase`. -1 si no esta."""
    for i in range(desde, len(lineas)):
        if frase in lineas[i]:
            return i
    return -1


def aplicar_arreglos(texto: str, reglas: dict) -> str:
    """Aplica las reglas de UN documento de corpus/arreglos.json. Cada regla es opcional.

    (Es la misma logica que tenia extraer.py en la Tarea 2.)
    """
    for roto, bien in sorted(reglas.get("reemplazar", {}).items(), key=lambda par: -len(par[0])):
        texto = texto.replace(roto, bien)
    lineas = texto.split("\n")
    if reglas.get("quitar_repetidas"):
        sin_repetir = []
        for linea in lineas:
            if sin_repetir and linea.strip() and linea == sin_repetir[-1]:
                continue
            sin_repetir.append(linea)
        lineas = sin_repetir
    for desde, hasta in reglas.get("borrar_entre", []):
        i = _posicion(lineas, desde)
        j = _posicion(lineas, hasta, i + 1) if i >= 0 else -1
        if i >= 0 and j >= 0:
            lineas = lineas[:i] + lineas[j + 1:]
    basura = set(reglas.get("borrar_lineas", []))
    lineas = [l for l in lineas if l.strip() not in basura]
    if "empieza_en" in reglas:
        i = _posicion(lineas, reglas["empieza_en"])
        if i >= 0:
            lineas = lineas[i:]
    if "termina_en" in reglas:
        i = _posicion(lineas, reglas["termina_en"])
        if i >= 0:
            lineas = lineas[:i + 1]
    return "\n".join(lineas).strip()


def ya_ok(coleccion: str) -> set[str]:
    if not REPORTE.exists():
        return set()
    r = pd.read_excel(REPORTE, sheet_name="Archivos", dtype=str).fillna("")
    return set(r[(r.estado == "OK") & (r.coleccion == coleccion)].id)


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF locales -> Markdown")
    parser.add_argument("--eje", default="D", help="carpeta dentro de corpus/ con los PDF")
    parser.add_argument("--coleccion", default="diagnostico", help="carpeta de salida en corpus/md/")
    args = parser.parse_args()

    meta = pd.read_csv(CORPUS / "metadatos.csv", dtype=str, encoding="utf-8-sig").fillna("").set_index("id")
    arreglos = json.loads((CORPUS / "arreglos.json").read_text(encoding="utf-8"))
    salida = CORPUS / "md" / args.coleccion
    salida.mkdir(parents=True, exist_ok=True)
    saltar = ya_ok(args.coleccion)

    hechos, saltados = [], []
    for pdf in sorted((CORPUS / args.eje).glob("*.pdf")):
        doc_id = pdf.stem
        if doc_id in saltar:
            saltados.append(doc_id)
            continue
        if doc_id not in meta.index:
            print("Falta en metadatos.csv:", doc_id)
            continue
        with pymupdf.open(pdf) as d:
            paginas = d.page_count
        md = pymupdf4llm.to_markdown(str(pdf), show_progress=False)
        for clave in ("*", doc_id):  # primero las reglas para todos, luego las del documento
            if clave in arreglos:
                md = aplicar_arreglos(md, arreglos[clave])
        m = meta.loc[doc_id]
        cab = {
            "id": doc_id, "coleccion": args.coleccion, "titulo": m.titulo, "titulo_excel": m.titulo,
            "institucion": m.institucion, "anio": m.anio, "enfermedad": m.enfermedad, "doi": "",
            "url_origen": f"corpus/{args.eje}/{pdf.name}", "url_plan": f"corpus/{args.eje}/{pdf.name}",
            "url_descarga": f"corpus/{args.eje}/{pdf.name}", "ruta": RUTA_LOCAL, "paginas": paginas,
            "fuente_metadatos": "excel", "fecha_descarga": time.strftime("%Y-%m-%d"),
        }
        (salida / f"{doc_id}.md").write_text(cabecera.escribir(cab, md), encoding="utf-8")
        hechos.append(doc_id)
        print(f"OK {doc_id:4} {paginas:3} páginas {len(md.split()):7} palabras ({m.enfermedad})")
    print(f"\n{len(hechos)} convertidos | {len(saltados)} ya estaban OK en {args.coleccion} y no se tocaron: {saltados}")


if __name__ == "__main__":
    main()

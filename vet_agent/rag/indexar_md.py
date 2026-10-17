"""Tarea 8: guardar los pedazos de los .md en una coleccion de Chroma aparte.

Uso (raiz del repo):
    python -m vet_agent.rag.indexar_md                                   # las 3 colecciones *_md
    python -m vet_agent.rag.indexar_md --carpeta corpus/md/diagnostico --coleccion diagnostico_md

Lee los .md, los corta con cortar_md() (respeta los titulos) y solo escribe
en colecciones *_md.

- Metadatos: si el id esta en metadatos.csv se usan esos; si no, los de la
  cabecera del .md (titulo, institucion, anio, enfermedad...).
- La cabecera nunca se indexa como texto.
- Si existe corpus/md/reporte_validacion.xlsx, solo se indexan los "OK".
"""
import argparse
import pathlib

import chromadb
import pandas as pd
from chromadb.utils import embedding_functions

from vet_agent.rag import cabecera
from vet_agent.rag.chunking import cortar_md

RAIZ = pathlib.Path(__file__).resolve().parents[2]
MODELO = "paraphrase-multilingual-MiniLM-L12-v2"  # el mismo que usa buscar.py para las preguntas
REPORTE = RAIZ / "corpus" / "md" / "reporte_validacion.xlsx"
COLECCIONES = {"climatico": "climatico_md", "diagnostico": "diagnostico_md", "bioseguridad": "bioseguridad_md"}
CAMPOS = ("titulo", "institucion", "anio", "enfermedad", "doi", "ruta", "url_origen", "fuente_metadatos")


def aprobados(carpeta: pathlib.Path) -> set[str] | None:
    """Los id "OK" de la validacion para esta carpeta (None = no hay validacion: se indexa todo)."""
    if not REPORTE.exists():
        return None
    r = pd.read_excel(REPORTE, sheet_name="Archivos", dtype=str).fillna("")
    return set(r[(r.estado == "OK") & (r.coleccion == carpeta.name)].id)


def indexar(carpeta: pathlib.Path, coleccion: str) -> None:
    # Solo colecciones *_md: como cada corrida borra la coleccion, asi no se pisa otra por error
    if not coleccion.endswith("_md"):
        raise SystemExit(f"'{coleccion}' no termina en _md; indexar_md solo escribe en colecciones *_md")

    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=MODELO)
    cliente = chromadb.PersistentClient(path=str(RAIZ / "chroma_db"))
    # se borra y se crea de nuevo, asi cada corrida parte limpia
    try:
        cliente.delete_collection(coleccion)
    except Exception:
        pass
    col = cliente.create_collection(coleccion, embedding_function=ef)

    meta_csv = pd.read_csv(RAIZ / "corpus" / "metadatos.csv", dtype=str, encoding="utf-8-sig").fillna("").set_index("id")
    ok = aprobados(carpeta)

    ids, textos, metadatos, saltados = [], [], [], 0
    for md in sorted(carpeta.glob("*.md")):
        doc_id = md.stem
        if ok is not None and doc_id not in ok:
            saltados += 1
            continue
        cab, cuerpo = cabecera.leer(md)
        if doc_id in meta_csv.index:
            base = meta_csv.loc[doc_id].to_dict()
            base["enfermedad"] = base.get("enfermedad") or cab.get("enfermedad", "")
        else:
            base = {k: str(cab.get(k, "")) for k in CAMPOS}
        for i, pedazo in enumerate(cortar_md(cuerpo)):
            m = dict(base, doc_id=doc_id, chunk=i)
            ids.append(f"{doc_id}_{i}")
            textos.append(pedazo)
            metadatos.append(m)

    # se agrega en grupos de 100 para no saturar la memoria
    for j in range(0, len(ids), 100):
        col.add(ids=ids[j:j+100], documents=textos[j:j+100], metadatas=metadatos[j:j+100])

    print(f"{col.count()} pedazos de {len({m['doc_id'] for m in metadatos})} documentos indexados en {coleccion}"
          + (f" ({saltados} archivos sin 'OK' en la validación quedaron fuera)" if saltados else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description="Indexa .md en Chroma")
    parser.add_argument("--carpeta", help="carpeta con los .md (sin esto: las 3 colecciones)")
    parser.add_argument("--coleccion", help="coleccion de Chroma (terminada en _md)")
    args = parser.parse_args()
    if args.carpeta:
        indexar(pathlib.Path(args.carpeta), args.coleccion or pathlib.Path(args.carpeta).name + "_md")
        return
    for carpeta, coleccion in COLECCIONES.items():
        indexar(RAIZ / "corpus" / "md" / carpeta, coleccion)


if __name__ == "__main__":
    main()

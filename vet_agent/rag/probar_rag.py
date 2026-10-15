# Tarea 5: preguntas de prueba y Recall@5
"""Mide si el buscador encuentra el documento correcto.

Uso (raiz del repo, despues de correr extraer e indexar):
    python -m vet_agent.rag.probar_rag                            # coleccion "diagnostico" (.txt)
    python -m vet_agent.rag.probar_rag --coleccion diagnostico_md # coleccion de los .md

Lee corpus/preguntas_prueba.csv (pregunta, doc_esperado, nota). Para cada
pregunta pide 5 pedazos a buscar_crudo y revisa si alguno viene del
doc_esperado. Recall@5 = preguntas acertadas / total de preguntas.

Se muestra de dos formas:
- total: todas las preguntas.
- solo docs en la coleccion: deja fuera las preguntas cuyo doc_esperado no
  esta indexado (por ejemplo, porque no se pudo descargar). Esa mide la
  calidad del buscador; la total mide ademas cuanto falta del corpus.

OJO: las preguntas son un BORRADOR. Investigacion tiene que validarlas.
"""
import argparse
import pathlib

import pandas as pd

from vet_agent.rag.buscar import _cliente, buscar_crudo

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ARCHIVO_PREGUNTAS = RAIZ / "corpus" / "preguntas_prueba.csv"
COLECCION = "diagnostico"
K = 5


def posicion(coleccion: str, pregunta: str, doc_esperado: str, k: int = K) -> int | None:
    """Lugar (1 a k) del primer pedazo que viene de doc_esperado. None si no aparece.

    Varios pedazos pueden ser del mismo documento; cuenta el mejor ubicado.
    """
    docs = [p["doc_id"] for p in buscar_crudo(coleccion, pregunta, k)]
    return docs.index(doc_esperado) + 1 if doc_esperado in docs else None


def docs_indexados(coleccion: str) -> set[str]:
    """Los doc_id que tienen al menos un pedazo en la coleccion."""
    metas = _cliente.get_collection(coleccion).get(include=["metadatas"])["metadatas"]
    return {m["doc_id"] for m in metas}


def _recall(posiciones: list) -> str:
    aciertos = sum(1 for p in posiciones if p)
    return f"{aciertos}/{len(posiciones)} = {aciertos / len(posiciones):.0%}" if posiciones else "sin preguntas"


def main() -> None:
    parser = argparse.ArgumentParser(description="Recall@5 de las preguntas de prueba")
    parser.add_argument("--coleccion", default=COLECCION, help="coleccion de Chroma a probar")
    coleccion = parser.parse_args().coleccion

    preguntas = pd.read_csv(ARCHIVO_PREGUNTAS, dtype=str, encoding="utf-8-sig")
    esperados = preguntas.doc_esperado.str.strip()
    try:
        indexados = docs_indexados(coleccion)
        posiciones = [posicion(coleccion, q, d) for q, d in zip(preguntas.pregunta, esperados)]
    except Exception as e:  # la coleccion no existe todavia
        print(f"No pude buscar en '{coleccion}' ({e}).")
        print("Primero corre extraer + indexar (o descargar + indexar_md)")
        return

    tabla = pd.DataFrame({
        "pregunta": preguntas.pregunta.str.slice(0, 70),
        "esperado": esperados,
        "indexado": ["sí" if d in indexados else "no" for d in esperados],
        "acierto": ["sí" if p else "no" for p in posiciones],
        "posicion": [p if p else "-" for p in posiciones],
    })
    print(f"Coleccion: {coleccion} ({len(indexados)} documentos)")
    print("Preguntas en BORRADOR (falta que Investigacion las valide)\n")
    print(tabla.to_string(index=False))

    con_doc = [p for p, d in zip(posiciones, esperados) if d in indexados]
    print(f"\nRecall@{K} total:                     {_recall(posiciones)}")
    print(f"Recall@{K} solo docs en la coleccion: {_recall(con_doc)}")


if __name__ == "__main__":
    main()

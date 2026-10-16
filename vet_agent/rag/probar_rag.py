# Tarea 5: preguntas de prueba y Recall@5
"""Mide si el buscador encuentra el documento correcto.

Uso (raiz del repo, despues de indexar_md):
    python -m vet_agent.rag.probar_rag                               # cada pregunta en su coleccion
    python -m vet_agent.rag.probar_rag --coleccion diagnostico_md_v1 # todas contra una coleccion

Lee corpus/preguntas_prueba.csv (pregunta, doc_esperado, nota, coleccion). doc_esperado
puede tener varios documentos validos separados por ";" (por ejemplo
"D22;D8"): la pregunta acierta si CUALQUIERA aparece entre los 5 pedazos.
Recall@5 = preguntas acertadas / total de preguntas.

Se muestra de dos formas:
- total: todas las preguntas.
- solo docs en la coleccion: deja fuera las preguntas cuyos documentos
  esperados no estan indexados (por ejemplo, porque no se pudieron bajar).
  Esa mide la calidad del buscador; la total mide ademas cuanto falta del corpus.

OJO: las preguntas son un BORRADOR. Investigacion tiene que validarlas.
"""
import argparse
import pathlib

import pandas as pd

from vet_agent.rag.buscar import _cliente, buscar_crudo

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ARCHIVO_PREGUNTAS = RAIZ / "corpus" / "preguntas_prueba.csv"
COLECCION = "diagnostico_md"
K = 5


def esperados(texto: str) -> list[str]:
    """'D22; D8' -> ['D22', 'D8']"""
    return [d.strip() for d in str(texto).split(";") if d.strip()]


def posicion(coleccion: str, pregunta: str, doc_esperado: str, k: int = K) -> int | None:
    """Lugar (1 a k) del primer pedazo que viene de alguno de los docs esperados. None si no aparece.

    Varios pedazos pueden ser del mismo documento; cuenta el mejor ubicado.
    """
    validos = set(esperados(doc_esperado))
    docs = [p["doc_id"] for p in buscar_crudo(coleccion, pregunta, k)]
    return next((i + 1 for i, d in enumerate(docs) if d in validos), None)


def docs_indexados(coleccion: str) -> set[str]:
    """Los doc_id que tienen al menos un pedazo en la coleccion."""
    metas = _cliente.get_collection(coleccion).get(include=["metadatas"])["metadatas"]
    return {m["doc_id"] for m in metas}


def _recall(posiciones: list) -> str:
    aciertos = sum(1 for p in posiciones if p)
    return f"{aciertos}/{len(posiciones)} = {aciertos / len(posiciones):.0%}" if posiciones else "sin preguntas"


def probar_todas(preguntas: pd.DataFrame) -> None:
    """Cada pregunta contra la coleccion de su columna "coleccion"; resumen por coleccion."""
    resumen, fallidas = [], []
    for coleccion, grupo in preguntas.groupby("coleccion", sort=False):
        try:
            indexados = docs_indexados(coleccion)
        except Exception as e:
            print(f"No pude buscar en '{coleccion}' ({e}).")
            continue
        posiciones, presentes = [], []
        for f in grupo.itertuples():
            p = posicion(coleccion, f.pregunta, f.doc_esperado)
            presente = any(d in indexados for d in esperados(f.doc_esperado))
            posiciones.append(p)
            presentes.append(presente)
            if not p:
                fallidas.append({"coleccion": coleccion, "esperado": f.doc_esperado,
                                 "indexado": "sí" if presente else "no", "pregunta": f.pregunta[:90]})
        resumen.append({"coleccion": coleccion, "preguntas": len(grupo), f"Recall@{K} total": _recall(posiciones),
                        f"Recall@{K} solo docs indexados": _recall([p for p, ok in zip(posiciones, presentes) if ok])})
    print("Preguntas en BORRADOR (falta que Investigacion las valide)\n")
    print(pd.DataFrame(resumen).to_string(index=False))
    print(f"\nPreguntas que fallaron ({len(fallidas)}):")
    print(pd.DataFrame(fallidas).to_string(index=False) if fallidas else "   ninguna")


def main() -> None:
    parser = argparse.ArgumentParser(description="Recall@5 de las preguntas de prueba")
    parser.add_argument("--coleccion", help="probar TODAS las preguntas contra esta coleccion "
                                            "(sin esto: cada pregunta en la de su columna 'coleccion')")
    coleccion = parser.parse_args().coleccion

    preguntas = pd.read_csv(ARCHIVO_PREGUNTAS, dtype=str, encoding="utf-8-sig").fillna("")
    if not coleccion:
        if "coleccion" not in preguntas:
            preguntas["coleccion"] = COLECCION
        probar_todas(preguntas)
        return
    esperado = preguntas.doc_esperado.str.strip()
    try:
        indexados = docs_indexados(coleccion)
        posiciones = [posicion(coleccion, q, d) for q, d in zip(preguntas.pregunta, esperado)]
    except Exception as e:  # la coleccion no existe todavia
        print(f"No pude buscar en '{coleccion}' ({e}).")
        print("Primero corre descargar_md (o pdf_local), limpiar_texto, validar e indexar_md")
        return

    presente = [any(d in indexados for d in esperados(e)) for e in esperado]
    tabla = pd.DataFrame({
        "pregunta": preguntas.pregunta.str.slice(0, 70),
        "esperado": esperado,
        "indexado": ["sí" if p else "no" for p in presente],
        "acierto": ["sí" if p else "no" for p in posiciones],
        "posicion": [p if p else "-" for p in posiciones],
    })
    print(f"Coleccion: {coleccion} ({len(indexados)} documentos)")
    print("Preguntas en BORRADOR (falta que Investigacion las valide)\n")
    print(tabla.to_string(index=False))

    con_doc = [p for p, ok in zip(posiciones, presente) if ok]
    print(f"\nRecall@{K} total:                     {_recall(posiciones)}")
    print(f"Recall@{K} solo docs en la coleccion: {_recall(con_doc)}")


if __name__ == "__main__":
    main()

# Tarea 5: preguntas de prueba y Recall@5
"""Mide si el buscador encuentra el documento correcto.

Uso (raiz del repo, despues de correr extraer e indexar):
    python -m vet_agent.rag.probar_rag

Lee corpus/preguntas_prueba.csv (pregunta, doc_esperado, nota). Para cada
pregunta pide 5 pedazos a buscar_crudo y revisa si alguno viene del
doc_esperado. Recall@5 = preguntas acertadas / total de preguntas.

OJO: las preguntas son un BORRADOR. Investigacion tiene que validarlas.
"""
import pathlib

import pandas as pd

from vet_agent.rag.buscar import buscar_crudo

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ARCHIVO_PREGUNTAS = RAIZ / "corpus" / "preguntas_prueba.csv"
COLECCION = "diagnostico"
K = 5


def posicion(pregunta: str, doc_esperado: str, k: int = K) -> int | None:
    """Lugar (1 a k) del primer pedazo que viene de doc_esperado. None si no aparece.

    Varios pedazos pueden ser del mismo documento; cuenta el mejor ubicado.
    """
    docs = [p["doc_id"] for p in buscar_crudo(COLECCION, pregunta, k)]
    return docs.index(doc_esperado) + 1 if doc_esperado in docs else None


def main() -> None:
    preguntas = pd.read_csv(ARCHIVO_PREGUNTAS, dtype=str, encoding="utf-8-sig")
    try:
        posiciones = [posicion(f.pregunta, f.doc_esperado.strip()) for f in preguntas.itertuples()]
    except Exception as e:  # la coleccion no existe todavia
        print(f"No pude buscar en '{COLECCION}' ({e}).")
        print("Primero corre: python -m vet_agent.rag.extraer y python -m vet_agent.rag.indexar")
        return

    tabla = pd.DataFrame({
        "pregunta": preguntas.pregunta.str.slice(0, 70),
        "esperado": preguntas.doc_esperado,
        "acierto": ["sí" if p else "no" for p in posiciones],
        "posicion": [p if p else "-" for p in posiciones],
    })
    print("Preguntas en BORRADOR (falta que Investigacion las valide)\n")
    print(tabla.to_string(index=False))

    aciertos = sum(1 for p in posiciones if p)
    print(f"\nRecall@{K}: {aciertos}/{len(posiciones)} = {aciertos / len(posiciones):.0%}")


if __name__ == "__main__":
    main()

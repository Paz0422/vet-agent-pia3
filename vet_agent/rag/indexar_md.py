"""Tarea 8: guardar los pedazos de los .md en una coleccion de Chroma aparte.

Uso (raiz del repo, despues de correr descargar):
    python -m vet_agent.rag.indexar_md                                   # corpus/md -> diagnostico_md
    python -m vet_agent.rag.indexar_md --carpeta corpus/md --coleccion diagnostico_md

Es igual que indexar.py, pero lee .md, corta con cortar_md() (respeta los
titulos) y NUNCA toca la coleccion "diagnostico" del pipeline .txt.
"""
import argparse
import pathlib

import chromadb
import pandas as pd
from chromadb.utils import embedding_functions

from vet_agent.rag.chunking import cortar_md

RAIZ = pathlib.Path(__file__).resolve().parents[2]
MODELO = "paraphrase-multilingual-MiniLM-L12-v2"  # el mismo de indexar.py, para comparar parejo


def indexar(carpeta: pathlib.Path, coleccion: str) -> None:
    # Solo colecciones *_md: asi nunca se pisa "diagnostico" ni otra del pipeline .txt
    if not coleccion.endswith("_md"):
        raise SystemExit(f"'{coleccion}' no termina en _md; esas son del pipeline .txt")

    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=MODELO)
    cliente = chromadb.PersistentClient(path=str(RAIZ / "chroma_db"))
    # se borra y se crea de nuevo, asi cada corrida parte limpia
    try:
        cliente.delete_collection(coleccion)
    except Exception:
        pass
    col = cliente.create_collection(coleccion, embedding_function=ef)

    meta = pd.read_csv(RAIZ / "corpus" / "metadatos.csv", dtype=str, encoding="utf-8-sig").fillna("").set_index("id")

    ids, textos, metadatos = [], [], []
    for md in sorted(carpeta.glob("*.md")):
        doc_id = md.stem
        if doc_id not in meta.index:
            print("Falta en metadatos.csv:", doc_id)
            continue
        for i, pedazo in enumerate(cortar_md(md.read_text(encoding="utf-8"))):
            m = meta.loc[doc_id].to_dict()
            m.update({"doc_id": doc_id, "chunk": i})
            ids.append(f"{doc_id}_{i}")
            textos.append(pedazo)
            metadatos.append(m)

    # se agrega en grupos de 100 para no saturar la memoria
    for j in range(0, len(ids), 100):
        col.add(ids=ids[j:j+100], documents=textos[j:j+100], metadatas=metadatos[j:j+100])

    print(col.count(), "pedazos de", len({m["doc_id"] for m in metadatos}), "documentos indexados en", coleccion)


def main() -> None:
    parser = argparse.ArgumentParser(description="Indexa .md en Chroma")
    parser.add_argument("--carpeta", default=str(RAIZ / "corpus" / "md"), help="carpeta con los .md")
    parser.add_argument("--coleccion", default="diagnostico_md", help="coleccion de Chroma (terminada en _md)")
    args = parser.parse_args()
    indexar(pathlib.Path(args.carpeta), args.coleccion)


if __name__ == "__main__":
    main()

# Tarea 4: buscar(coleccion, pregunta, k=5) y buscar_crudo(coleccion, pregunta, k=5)
import pathlib
import chromadb
from chromadb.utils import embedding_functions

# Raíz del repo: vet_agent/rag/buscar.py -> sube 2 niveles
RAIZ = pathlib.Path(__file__).resolve().parents[2]
RUTA_DB = str(RAIZ / "chroma_db")

_ef = None  # el modelo de embeddings se carga la primera vez que se busca (no al importar)
_cliente = chromadb.PersistentClient(path=RUTA_DB)


def _embedding():
    global _ef
    if _ef is None:
        _ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="paraphrase-multilingual-MiniLM-L12-v2")
    return _ef


# El proyecto es de influenza aviar: por defecto se dejan fuera los documentos de cerdos
SIN_PORCINO = {"enfermedad": {"$ne": "porcino"}}


def buscar_crudo(coleccion: str, pregunta: str, k: int = 5, excluir_porcino: bool = True) -> list[dict]:
    """Devuelve los k pedazos como lista de dicts (lo usa la Tarea 5).

    Con excluir_porcino=True (por defecto) no devuelve pedazos con enfermedad "porcino".
    """
    col = _cliente.get_collection(coleccion, embedding_function=_embedding())
    r = col.query(query_texts=[pregunta], n_results=k, where=SIN_PORCINO if excluir_porcino else None)
    return [{"texto": t, **m} for t, m in zip(r["documents"][0], r["metadatas"][0])]


def buscar(coleccion: str, pregunta: str, k: int = 5, excluir_porcino: bool = True) -> str:
    """Devuelve los k pedazos como un solo texto listo para el prompt del agente."""
    partes = []
    for p in buscar_crudo(coleccion, pregunta, k, excluir_porcino):
        cabecera = f"[{p['doc_id']}] {p.get('institucion', '')} {p.get('anio', '')} — {p.get('titulo', '')}"
        partes.append(cabecera + "\n" + p["texto"])
    return "\n\n---\n\n".join(partes)

# Tarea 4: buscar(coleccion, pregunta, k=5) y buscar_crudo(coleccion, pregunta, k=5)
import chromadb
from chromadb.utils import embedding_functions

_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="paraphrase-multilingual-MiniLM-L12-v2")
_cliente = chromadb.PersistentClient(path="chroma_db")

def buscar_crudo(coleccion: str, pregunta: str, k: int = 5) -> list[dict]:
    """Devuelve los k pedazos como lista de dicts (lo usa la Tarea 5)."""
    col = _cliente.get_collection(coleccion, embedding_function=_ef)
    r = col.query(query_texts=[pregunta], n_results=k)
    return [{"texto": t, **m} for t, m in zip(r["documents"][0],
r["metadatas"][0])]

def buscar(coleccion: str, pregunta: str, k: int = 5) -> str:
    """Devuelve los k pedazos como un solo texto listo para el prompt del agente."""
    partes = []
    for p in buscar_crudo(coleccion, pregunta, k):
        cabecera = f"[{p['doc_id']}] {p.get('institucion', '')}{p.get('anio', '')} — {p.get('titulo', '')}"
        partes.append(cabecera + "\n" + p["texto"])
    return "\n\n---\n\n".join(partes)

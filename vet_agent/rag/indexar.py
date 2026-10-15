# Tarea 4: guardar pedazos en Chroma, colección "diagnostico"
import pathlib
import chromadb
import pandas as pd
from chromadb.utils import embedding_functions
import sys
RAIZ = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
from vet_agent.rag.chunking import cortar


MODELO = "paraphrase-multilingual-MiniLM-L12-v2" # entiende español e inglés, gratis, corre en el PC
COLECCION = "diagnostico"

ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=MODELO)
cliente = chromadb.PersistentClient(path=str(RAIZ / "chroma_db"))

# se borra y se crea de nuevo, así cada corrida parte limpia
try:
    cliente.delete_collection(COLECCION)
except Exception:
    pass
col = cliente.create_collection(COLECCION, embedding_function=ef)

meta = pd.read_csv(RAIZ / "corpus" / "metadatos.csv", dtype=str, encoding="utf-8-sig").fillna("").set_index("id")

ids, textos, metadatos = [], [], []

for txt in sorted((RAIZ / "corpus" / "txt").glob("D*.txt")):
    doc_id = txt.stem
    if doc_id not in meta.index:
        print("Falta en metadatos.csv:", doc_id)
        continue
    for i, pedazo in enumerate(cortar(txt.read_text(encoding="utf-8"))):
        m = meta.loc[doc_id].to_dict()
        m.update({"doc_id": doc_id, "chunk": i})
        ids.append(f"{doc_id}_{i}")
        textos.append(pedazo)
        metadatos.append(m)

# se agrega en grupos de 100 para no saturar la memoria
for j in range(0, len(ids), 100):
    col.add(ids=ids[j:j+100], documents=textos[j:j+100], metadatas=metadatos[j:j+100])

print(col.count(), "pedazos indexados en", COLECCION)

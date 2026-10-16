import copy

import chromadb
from chromadb.api.types import EmbeddingFunction
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END
from langgraph.types import Command

from vet_agent import llm
from vet_agent.config import MAX_RONDAS
from vet_agent.grafo import construir_grafo, despues_del_moderador, despues_de_revision_humana
from vet_agent.rag import buscar

APROBAR = {"aprobado": True, "comentarios": ""}
RECHAZAR = {"aprobado": False, "comentarios": "falta informacion de humedad"}

CASO = {
    "especie": "porcino",
    "ubicacion": "Región de O'Higgins",
    "resultados_laboratorio": ["PCR positivo a PRRSV"],
}


def estado_inicial():
    return {"caso": CASO, "resultados": {}, "solicitudes": {}, "revisiones": 0, "decision_humana": {}}


def pregunta(destino, texto="aclarar"):
    # arma una pregunta del moderador con el formato del prompt
    return {"agente_destino": destino, "pregunta": texto, "motivo": "prueba"}


def ejecutar(decisiones):
    # corre el grafo entero y va respondiendo cada pausa humana en orden
    app = construir_grafo(InMemorySaver())
    config = {"configurable": {"thread_id": "prueba"}}
    resultado = app.invoke(estado_inicial(), config)

    for decision in decisiones:
        assert "__interrupt__" in resultado, "se esperaba una pausa para revision humana"
        resultado = app.invoke(Command(resume=decision), config)

    assert "__interrupt__" not in resultado, "el grafo no termino"
    return resultado


# ---------- pruebas de las funciones que deciden el camino ----------

def prueba_moderador_informe_final():
    estado = {"evaluacion": {"estado": "informe_final", "informe": {}}, "revisiones": 1}
    assert despues_del_moderador(estado) == "revision_humana"


def prueba_moderador_pide_debate():
    # dos preguntas al climatico y una al diagnostico: cada agente debe correr una sola vez
    evaluacion = {"estado": "requiere_debate", "preguntas": [
        pregunta("climatico"), pregunta("diagnostico"), pregunta("climatico"),
    ]}
    estado = {"evaluacion": evaluacion, "revisiones": 1}
    assert despues_del_moderador(estado) == ["climatico", "diagnostico"]


def prueba_moderador_llega_al_maximo():
    evaluacion = {"estado": "requiere_debate", "preguntas": [pregunta("climatico")]}
    estado = {"evaluacion": evaluacion, "revisiones": MAX_RONDAS}
    assert despues_del_moderador(estado) == "revision_humana"


def prueba_moderador_agente_invalido():
    # si el modelo inventa un nombre de agente, no debe romper el grafo
    evaluacion = {"estado": "requiere_debate", "preguntas": [pregunta("agente climático")]}
    estado = {"evaluacion": evaluacion, "revisiones": 1}
    assert despues_del_moderador(estado) == "revision_humana"


def prueba_humano_aprueba():
    assert despues_de_revision_humana({"decision_humana": APROBAR}) == END


def prueba_humano_rechaza():
    assert despues_de_revision_humana({"decision_humana": RECHAZAR}) == "moderador"


# ---------- pruebas del grafo completo ----------

def prueba_flujo_normal():
    resultado = ejecutar([APROBAR])
    assert set(resultado["resultados"]) == {"climatico", "diagnostico", "bioseguridad"}
    assert resultado["revisiones"] == 1
    assert resultado["evaluacion"]["estado"] == "informe_final"


def prueba_rechazo_y_aprobacion():
    resultado = ejecutar([RECHAZAR, APROBAR])
    assert resultado["revisiones"] == 2
    assert resultado["decision_humana"]["aprobado"] is True


def prueba_debate_termina():
    # forzamos que el moderador simulado siempre pida debatir,
    # para comprobar que el grafo corta en MAX_RONDAS y no queda pegado
    original = copy.deepcopy(llm.RESPUESTAS_SIMULADAS["moderador"])
    llm.RESPUESTAS_SIMULADAS["moderador"] = {
        "estado": "requiere_debate",
        "preguntas": [pregunta("climatico", "precisar humedad")],
    }
    try:
        resultado = ejecutar([APROBAR])
        assert resultado["revisiones"] == MAX_RONDAS
        assert resultado["solicitudes"] == {"climatico": "precisar humedad"}
    finally:
        # dejamos la simulacion como estaba
        llm.RESPUESTAS_SIMULADAS["moderador"] = original


# ---------- pruebas del buscador (RAG) ----------
# No usan chroma_db/ ni el modelo de embeddings: arman una coleccion en memoria
# con pocos pedazos y un embedding falso, asi corren en cualquier PC.

class EmbeddingFalso(EmbeddingFunction):
    """Vectores fijos y baratos: alcanza para probar filtros y cantidades."""

    def __init__(self):
        pass

    def __call__(self, input):
        return [[float(len(t) % 7), 1.0, float(t.count("a"))] for t in input]

    @staticmethod
    def name():
        return "embedding_falso"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return EmbeddingFalso()


PEDAZOS = [
    ("PRRS en cerdos: ELISA en fluidos orales", "porcino"),
    ("Síndrome reproductivo y respiratorio porcino", "porcino"),
    ("Vigilancia de PRRS en granjas de cerdos", "porcino"),
    ("Influenza aviar H5N1 en aves de corral", "aviar"),
    ("RT-PCR para influenza aviar", "aviar"),
    ("Toma y envío de muestras al laboratorio", "general"),
]


def con_buscador_de_prueba(prueba):
    """Corre `prueba` con buscar.py apuntando a una coleccion en memoria y la deja como estaba."""
    cliente_original, ef_original = buscar._cliente, buscar._ef
    buscar._cliente, buscar._ef = chromadb.EphemeralClient(), EmbeddingFalso()
    try:
        col = buscar._cliente.get_or_create_collection("prueba_md", embedding_function=buscar._ef)
        col.add(ids=[f"P{i}" for i in range(len(PEDAZOS))], documents=[t for t, _ in PEDAZOS],
                metadatas=[{"doc_id": f"P{i}", "enfermedad": e, "titulo": t} for i, (t, e) in enumerate(PEDAZOS)])
        prueba()
    finally:
        buscar._cliente.delete_collection("prueba_md")
        buscar._cliente, buscar._ef = cliente_original, ef_original


def prueba_filtro_excluir_porcino():
    def revisar():
        sin = buscar.buscar_crudo("prueba_md", "PRRS en cerdos", k=6)
        assert sin and all(p["enfermedad"] != "porcino" for p in sin), "devolvió pedazos porcinos"
        con = buscar.buscar_crudo("prueba_md", "PRRS en cerdos", k=6, excluir_porcino=False)
        assert any(p["enfermedad"] == "porcino" for p in con), "sin el filtro deberían aparecer porcinos"
    con_buscador_de_prueba(revisar)


def prueba_buscar_maximo_k():
    def revisar():
        for k in (1, 2, 3, 50):  # 50 es mas que los pedazos que hay
            texto = buscar.buscar("prueba_md", "influenza aviar", k=k, excluir_porcino=False)
            assert len(texto.split("\n\n---\n\n")) <= k, f"buscar() devolvió más de {k}"
            assert len(buscar.buscar_crudo("prueba_md", "influenza aviar", k=k)) <= k
    con_buscador_de_prueba(revisar)


if __name__ == "__main__":
    pruebas = [
        prueba_moderador_informe_final,
        prueba_moderador_pide_debate,
        prueba_moderador_llega_al_maximo,
        prueba_moderador_agente_invalido,
        prueba_humano_aprueba,
        prueba_humano_rechaza,
        prueba_flujo_normal,
        prueba_rechazo_y_aprobacion,
        prueba_debate_termina,
        prueba_filtro_excluir_porcino,
        prueba_buscar_maximo_k,
    ]
    for prueba in pruebas:
        prueba()
        print(f"OK  {prueba.__name__}")

    print(f"\n{len(pruebas)} pruebas pasaron")
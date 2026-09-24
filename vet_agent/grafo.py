from langgraph.graph import StateGraph, START, END

from vet_agent.estado import Estado
from vet_agent.agentes.climatico import climatico
from vet_agent.agentes.diagnostico import diagnostico
from vet_agent.agentes.bioseguridad import bioseguridad
from vet_agent.agentes.moderador import moderador
from vet_agent.revision_humana import revision_humana

# el moderador evalua como maximo 3 veces, despues pasa si o si a revision humana
MAX_REVISIONES = 3

ESPECIALISTAS = ["climatico", "diagnostico", "bioseguridad"]


def despues_del_moderador(estado: Estado):
    evaluacion = estado["evaluacion"]
    pide_revision = evaluacion.get("requiere_nueva_revision", False)
    agentes = evaluacion.get("agente_a_consultar", [])

    if pide_revision and agentes and estado["revisiones"] < MAX_REVISIONES:
        # devolvemos la lista de agentes, langgraph los corre en paralelo
        return agentes

    return "revision_humana"


def despues_de_revision_humana(estado: Estado):
    if estado["decision_humana"].get("aprobado"):
        return END
    # si lo rechazo, vuelve al moderador con los comentarios
    return "moderador"


def construir_grafo(checkpointer=None):
    g = StateGraph(Estado)

    g.add_node("climatico", climatico)
    g.add_node("diagnostico", diagnostico)
    g.add_node("bioseguridad", bioseguridad)
    g.add_node("moderador", moderador)
    g.add_node("revision_humana", revision_humana)

    # los especialistas parten juntos y le entregan al moderador
    for nombre in ESPECIALISTAS:
        g.add_edge(START, nombre)
        g.add_edge(nombre, "moderador")

    # flechas condicionales: la funcion decide a donde se va
    g.add_conditional_edges(
        "moderador",
        despues_del_moderador,
        ESPECIALISTAS + ["revision_humana"],
    )
    g.add_conditional_edges(
        "revision_humana",
        despues_de_revision_humana,
        ["moderador", END],
    )

    # el checkpointer guarda el estado para poder pausar y seguir
    return g.compile(checkpointer=checkpointer)
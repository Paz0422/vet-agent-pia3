from langgraph.graph import StateGraph, START, END

from vet_agent.estado import Estado
from vet_agent.agentes.climatico import climatico
from vet_agent.agentes.diagnostico import diagnostico
from vet_agent.agentes.bioseguridad import bioseguridad
from vet_agent.agentes.moderador import moderador
from vet_agent.revision_humana import revision_humana
from vet_agent.config import MAX_RONDAS

# el moderador evalua como maximo 3 veces, despues pasa si o si a revision humana
MAX_REVISIONES = 3

# orden fijo de la cadena: con un solo modelo local (LM Studio) corren de a uno
ESPECIALISTAS = ["climatico", "diagnostico", "bioseguridad"]


def solo_si_le_toca(nombre, agente):
    # en la ronda 1 todos llaman al LLM. En las rondas de debate se recorre la
    # cadena entera, pero solo llama al LLM el agente al que el moderador le pregunto;
    # los demas devuelven {} y conservan su resultado anterior
    def nodo(estado: Estado) -> dict:
        if estado.get("revisiones", 0) == 0 or nombre in estado.get("solicitudes", {}):
            return agente(estado)
        return {}
    return nodo


def despues_del_moderador(estado: Estado):
    evaluacion = estado["evaluacion"]

    if evaluacion.get("estado") == "requiere_debate" and estado["revisiones"] < MAX_RONDAS:
        destinos = [p.get("agente_destino") for p in evaluacion.get("preguntas", [])]
        # si al menos uno es un agente real, se vuelve a recorrer la cadena desde el inicio
        if any(d in ESPECIALISTAS for d in destinos):
            return ESPECIALISTAS[0]

    return "revision_humana"


def despues_de_revision_humana(estado: Estado):
    if estado["decision_humana"].get("aprobado"):
        return END
    # si lo rechazo, vuelve al moderador con los comentarios
    return "moderador"


def construir_grafo(checkpointer=None):
    g = StateGraph(Estado)

    g.add_node("climatico", solo_si_le_toca("climatico", climatico))
    g.add_node("diagnostico", solo_si_le_toca("diagnostico", diagnostico))
    g.add_node("bioseguridad", solo_si_le_toca("bioseguridad", bioseguridad))
    g.add_node("moderador", moderador)
    g.add_node("revision_humana", revision_humana)

    # cadena fija: START -> climatico -> diagnostico -> bioseguridad -> moderador
    g.add_edge(START, "climatico")
    g.add_edge("climatico", "diagnostico")
    g.add_edge("diagnostico", "bioseguridad")
    g.add_edge("bioseguridad", "moderador")

    # flechas condicionales: la funcion decide a donde se va
    g.add_conditional_edges(
        "moderador",
        despues_del_moderador,
        ["climatico", "revision_humana"],
    )
    g.add_conditional_edges(
        "revision_humana",
        despues_de_revision_humana,
        ["moderador", END],
    )

    # el checkpointer guarda el estado para poder pausar y seguir
    return g.compile(checkpointer=checkpointer)
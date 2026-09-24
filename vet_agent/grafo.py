from langgraph.graph import StateGraph, START, END

from vet_agent.estado import Estado
from vet_agent.agentes.climatico import climatico
from vet_agent.agentes.diagnostico import diagnostico
from vet_agent.agentes.bioseguridad import bioseguridad
from vet_agent.agentes.moderador import moderador


def construir_grafo():
    g = StateGraph(Estado)

    # el moderador va primero para poder conectarle flechas despues
    g.add_node("moderador", moderador)

    # agentes que analizan el caso en paralelo
    especialistas = {
        "climatico": climatico,
        "diagnostico": diagnostico,
        "bioseguridad": bioseguridad,
    }

    for nombre, funcion in especialistas.items():
        g.add_node(nombre, funcion)
        g.add_edge(START, nombre)        # todos parten al inicio
        g.add_edge(nombre, "moderador")  # y le pasan su resultado al moderador

    g.add_edge("moderador", END)

    # compile() deja el grafo listo para ejecutarse
    return g.compile()
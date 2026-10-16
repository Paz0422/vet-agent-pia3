import copy
import json

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END
from langgraph.types import Command

from vet_agent import llm
from vet_agent.agentes import climatico, diagnostico, bioseguridad, moderador
from vet_agent.config import MAX_RONDAS
from vet_agent.grafo import construir_grafo, despues_del_moderador, despues_de_revision_humana

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
    # aunque pregunte a varios agentes, la cadena siempre se vuelve a recorrer desde climatico
    evaluacion = {"estado": "requiere_debate", "preguntas": [
        pregunta("climatico"), pregunta("diagnostico"), pregunta("climatico"),
    ]}
    estado = {"evaluacion": evaluacion, "revisiones": 1}
    assert despues_del_moderador(estado) == "climatico"


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


def prueba_debate_solo_llama_al_consultado():
    # espia sobre consultar_llm: anota que agente llama al LLM y en que orden.
    # Cada agente importa consultar_llm por su cuenta, asi que se reemplaza en cada modulo.
    llamadas = []
    original = llm.consultar_llm
    debate = {"estado": "requiere_debate", "preguntas": [pregunta("diagnostico", "precisar muestras")]}

    def espia(instrucciones, pregunta_llm, agente):
        llamadas.append(agente)
        # el moderador pide debatir solo la primera vez, despues entrega el informe
        if agente == "moderador" and llamadas.count("moderador") == 1:
            return json.dumps(debate, ensure_ascii=False)
        return original(instrucciones, pregunta_llm, agente)

    modulos = [climatico, diagnostico, bioseguridad, moderador]
    for modulo in modulos:
        modulo.consultar_llm = espia
    try:
        resultado = ejecutar([APROBAR])
    finally:
        for modulo in modulos:
            modulo.consultar_llm = original

    assert llamadas == [
        "climatico", "diagnostico", "bioseguridad", "moderador",  # ronda 1: todos
        "diagnostico", "moderador",                                 # debate: solo el consultado
    ], llamadas
    # los que no fueron consultados conservan su resultado de la ronda 1
    assert set(resultado["resultados"]) == {"climatico", "diagnostico", "bioseguridad"}
    assert resultado["revisiones"] == 2


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
        prueba_debate_solo_llama_al_consultado,
    ]
    for prueba in pruebas:
        prueba()
        print(f"OK  {prueba.__name__}")

    print(f"\n{len(pruebas)} pruebas pasaron")
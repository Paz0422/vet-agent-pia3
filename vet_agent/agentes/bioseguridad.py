from vet_agent.estado import Estado
from vet_agent.llm import consultar_llm, leer_json
from vet_agent.prompts import PROMPT_BIOSEGURIDAD, rellenar
from vet_agent.agentes.base import preparar_pregunta, a_texto, SIN_CONTEXTO


def bioseguridad(estado: Estado) -> dict:
    # en la primera ronda los otros agentes corren al mismo tiempo,
    # asi que sus hallazgos recien estan disponibles si hay una segunda ronda
    previos = {
        agente: salida
        for agente, salida in estado.get("resultados", {}).items()
        if agente != "bioseguridad"
    }
    hallazgos = a_texto(previos) if previos else "(los demás agentes aún no entregan hallazgos)"

    instrucciones = rellenar(
        PROMPT_BIOSEGURIDAD,
        resumen_escenario=a_texto(estado["caso"]),
        hallazgos_previos=hallazgos,
        contexto_rag=SIN_CONTEXTO,  # aca van los manuales SAG/OMSA cuando este el RAG
    )
    pregunta = preparar_pregunta(estado, "bioseguridad")
    texto = consultar_llm(instrucciones, pregunta, "bioseguridad")
    return {"resultados": {"bioseguridad": leer_json(texto)}}
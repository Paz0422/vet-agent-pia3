from vet_agent.estado import Estado
from vet_agent.llm import consultar_llm, leer_json
from vet_agent.prompts import PROMPT_BIOSEGURIDAD, rellenar
from vet_agent.agentes.base import preparar_pregunta, a_texto, contexto_rag, con_evidencia


def bioseguridad(estado: Estado) -> dict:
    # corre al final de la cadena (climatico -> diagnostico -> bioseguridad),
    # asi que desde la primera ronda ya ve los hallazgos de los otros dos agentes
    # sin "evidencia": los fragmentos de los otros agentes no son hallazgos y alargan el prompt
    previos = {
        agente: {k: v for k, v in salida.items() if k != "evidencia"}
        for agente, salida in estado.get("resultados", {}).items()
        if agente != "bioseguridad"
    }
    hallazgos = a_texto(previos) if previos else "(los demás agentes aún no entregan hallazgos)"

    contexto, fragmentos = contexto_rag("bioseguridad", estado["caso"])
    instrucciones = rellenar(
        PROMPT_BIOSEGURIDAD,
        resumen_escenario=a_texto(estado["caso"]),
        hallazgos_previos=hallazgos,
        contexto_rag=contexto,
    )
    pregunta = preparar_pregunta(estado, "bioseguridad")
    texto = consultar_llm(instrucciones, pregunta, "bioseguridad")
    return {"resultados": {"bioseguridad": con_evidencia(leer_json(texto), fragmentos)}}
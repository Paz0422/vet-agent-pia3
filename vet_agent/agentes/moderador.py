import json

from vet_agent.estado import Estado
from vet_agent.llm import consultar_llm, leer_json
from vet_agent.prompts import PROMPT_MODERADOR


def moderador(estado: Estado) -> dict:
    # el moderador recibe los analisis de los 3 agentes juntos
    pregunta = "Análisis de los agentes:\n" + json.dumps(
        estado["resultados"], ensure_ascii=False, indent=2
    )

    texto = consultar_llm(PROMPT_MODERADOR, pregunta, "moderador")
    evaluacion = leer_json(texto)

    # si pide revision, anotamos que aclaracion le toca a cada agente
    solicitudes = {
        agente: evaluacion["solicitud_de_aclaracion"]
        for agente in evaluacion.get("agente_a_consultar", [])
    }

    return {"evaluacion": evaluacion, "solicitudes": solicitudes}
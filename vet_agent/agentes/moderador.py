import json

from vet_agent.estado import Estado
from vet_agent.llm import consultar_llm, leer_json
from vet_agent.prompts import PROMPT_MODERADOR


def moderador(estado: Estado) -> dict:
    pregunta = "Análisis de los agentes:\n" + json.dumps(
        estado["resultados"], ensure_ascii=False, indent=2
    )

    # si el revisor humano rechazo el borrador anterior, le pasamos sus comentarios
    decision = estado.get("decision_humana") or {}
    if decision and not decision.get("aprobado") and decision.get("comentarios"):
        pregunta += (
            "\n\nEl revisor humano rechazó el borrador anterior con estos comentarios: "
            + decision["comentarios"]
        )

    texto = consultar_llm(PROMPT_MODERADOR, pregunta, "moderador")
    evaluacion = leer_json(texto)

    solicitudes = {
        agente: evaluacion["solicitud_de_aclaracion"]
        for agente in evaluacion.get("agente_a_consultar", [])
    }

    return {
        "evaluacion": evaluacion,
        "solicitudes": solicitudes,
        "revisiones": estado.get("revisiones", 0) + 1,  # sumamos una vuelta
    }
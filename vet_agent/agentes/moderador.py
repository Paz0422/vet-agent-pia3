import json

from vet_agent.config import MAX_RONDAS
from vet_agent.estado import Estado
from vet_agent.llm import consultar_llm, leer_json
from vet_agent.prompts import PROMPT_MODERADOR, rellenar
from vet_agent.agentes.base import SIN_CONTEXTO


def moderador(estado: Estado) -> dict:
    ronda = estado.get("revisiones", 0) + 1

    instrucciones = rellenar(
        PROMPT_MODERADOR,
        salidas_agentes=json.dumps(estado["resultados"], ensure_ascii=False, indent=2),
        contexto_rag=SIN_CONTEXTO,
        ronda=ronda,
        max_rondas=MAX_RONDAS,
    )

    pregunta = "Evalúa las salidas de los agentes y responde con el JSON indicado."

    # si el revisor humano rechazo el informe anterior, le pasamos sus comentarios
    decision = estado.get("decision_humana") or {}
    if decision and not decision.get("aprobado") and decision.get("comentarios"):
        pregunta += (
            "\n\nEl revisor humano rechazó el informe anterior con estos comentarios: "
            + decision["comentarios"]
        )

    texto = consultar_llm(instrucciones, pregunta, "moderador")
    evaluacion = leer_json(texto)

    # juntamos las preguntas por agente (puede haber mas de una para el mismo)
    solicitudes = {}
    if evaluacion.get("estado") == "requiere_debate":
        for p in evaluacion.get("preguntas", []):
            destino = p.get("agente_destino")
            if destino in solicitudes:
                solicitudes[destino] += " / " + p["pregunta"]
            else:
                solicitudes[destino] = p["pregunta"]

    return {"evaluacion": evaluacion, "solicitudes": solicitudes, "revisiones": ronda}
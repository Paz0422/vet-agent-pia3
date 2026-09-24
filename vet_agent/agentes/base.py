import json

from vet_agent.estado import Estado


def preparar_pregunta(estado: Estado, agente: str) -> str:
    # le pasamos el caso completo como JSON ordenado
    pregunta = "Caso a analizar:\n" + json.dumps(estado["caso"], ensure_ascii=False, indent=2)

    # si el moderador le pidio algo a este agente, se lo agregamos
    aclaracion = estado.get("solicitudes", {}).get(agente)
    if aclaracion:
        pregunta += f"\n\nEl moderador solicita aclarar lo siguiente: {aclaracion}"

    return pregunta
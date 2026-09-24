import json

from vet_agent.estado import Estado

# mientras no exista el RAG, los agentes reciben este texto como contexto
SIN_CONTEXTO = "(Aún no hay documentos recuperados. Indícalo como vacío de respaldo documental.)"


def a_texto(datos) -> str:
    # pasa listas o diccionarios del caso a texto legible para el prompt
    if datos in (None, "", [], {}):
        return "no informado"
    if isinstance(datos, str):
        return datos
    return json.dumps(datos, ensure_ascii=False, indent=2)


def preparar_pregunta(estado: Estado, agente: str) -> str:
    # el caso completo va siempre, asi el agente sabe especie, enfermedad, etc
    pregunta = "Caso a analizar:\n" + a_texto(estado["caso"])

    # si el moderador le pidio algo a este agente, se lo agregamos
    aclaracion = estado.get("solicitudes", {}).get(agente)
    if aclaracion:
        pregunta += f"\n\nEl moderador solicita aclarar lo siguiente: {aclaracion}"

    return pregunta
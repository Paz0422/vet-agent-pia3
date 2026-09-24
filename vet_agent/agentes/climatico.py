from vet_agent.estado import Estado
from vet_agent.llm import consultar_llm


def climatico(estado: Estado) -> dict:
    # revisa temperatura, humedad, lluvias, etc. del caso
    respuesta = consultar_llm(
        "Eres un experto en factores climaticos de riesgo sanitario.",
        f"Analiza este caso: {estado['caso']}",
    )
    # solo devolvemos lo que cambia, langgraph lo junta con el resto
    return {"hallazgos": [f"[Climatico] {respuesta}"]}
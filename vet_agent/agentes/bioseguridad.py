from vet_agent.estado import Estado
from vet_agent.llm import consultar_llm


def bioseguridad(estado: Estado) -> dict:
    # busca protocolos y medidas de control aplicables
    # (despues esto va a sacar info del RAG con documentos SAG/OMSA)
    respuesta = consultar_llm(
        "Eres un experto en protocolos de bioseguridad animal.",
        f"Analiza este caso: {estado['caso']}",
    )
    return {"hallazgos": [f"[Bioseguridad] {respuesta}"]}
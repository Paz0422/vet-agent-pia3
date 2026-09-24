from vet_agent.estado import Estado
from vet_agent.llm import consultar_llm


def diagnostico(estado: Estado) -> dict:
    # interpreta examenes (PCR, ELISA), mortalidad y sintomas
    respuesta = consultar_llm(
        "Eres un experto en diagnostico veterinario.",
        f"Analiza este caso: {estado['caso']}",
    )
    return {"hallazgos": [f"[Diagnostico] {respuesta}"]}
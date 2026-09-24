from vet_agent.estado import Estado
from vet_agent.llm import consultar_llm, leer_json
from vet_agent.prompts import PROMPT_CLIMATICO, rellenar
from vet_agent.agentes.base import preparar_pregunta, a_texto, SIN_CONTEXTO


def climatico(estado: Estado) -> dict:
    caso = estado["caso"]
    instrucciones = rellenar(
        PROMPT_CLIMATICO,
        datos_climaticos=a_texto(caso.get("clima")),
        contexto_rag=SIN_CONTEXTO,  # despues esto viene del RAG
    )
    pregunta = preparar_pregunta(estado, "climatico")
    texto = consultar_llm(instrucciones, pregunta, "climatico")
    return {"resultados": {"climatico": leer_json(texto)}}